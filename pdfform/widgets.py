"""Low-level AcroForm widget writers.

Two operations:
  - set_text(page, name, value)   -- write a string into a Text field
  - set_checkbox(page, name, on_value, checked=True) -- tick or untick

Why we don't go through ``Widget.field_value = bool`` for checkboxes:
this form's checkboxes have *non-boolean* on-states (``mr``,
``mrs``, ``Application``, ``sponsor``, ...). The high-level setter
would normalise every kid to ``Yes``/``Off`` and destroy the original
export value. We edit the underlying annotation's ``/AS`` (appearance
state) and ``/V`` (field value) directly via ``Document.xref_set_key``,
preserving the on-state name. On extract, the same on-state name comes
back out verbatim.
"""
from __future__ import annotations

from typing import Iterable

import pymupdf


def _find_widget(page: pymupdf.Page, name: str, field_type: str | None = None,
                 on_value: str | None = None):
    for w in page.widgets() or []:
        if w.field_name != name:
            continue
        if field_type and w.field_type_string != field_type:
            continue
        if on_value is not None:
            states = w.button_states() or {}
            if on_value in (states.get("down") or []) \
                    or on_value in (states.get("normal") or []):
                return w
            continue
        return w
    return None


def set_text(page: pymupdf.Page, name: str, value: str) -> bool:
    """Write ``value`` into the Text field ``name`` on ``page``.

    Returns True if the field was found and updated, False otherwise.
    """
    w = _find_widget(page, name, field_type="Text")
    if w is None:
        return False
    w.field_value = "" if value is None else str(value)
    w.update()
    return True


def _field_value_xref(doc: pymupdf.Document, widget_xref: int) -> int:
    """Find the xref that owns the field's authoritative ``/V``.

    Radio-style groups (the four ``mg.title`` option boxes, ``mg.app``
    Yes/No, ...) are stored as a Kids array: each on-screen box is a
    bare Widget annotation with no ``/FT`` of its own, and the actual
    field dict (``/FT /Btn``, the shared ``/V``) is the ``/Parent``.
    Standalone checkboxes (e.g. the ``mg.dec N`` declaration ticks) are
    merged widget+field objects that carry ``/FT`` directly, so this
    just returns the widget itself for those. Walking to the wrong
    object (or never walking at all) leaves the Parent's ``/V`` stale,
    which is invisible to lenient renderers that paint straight from
    each Kid's own ``/AS`` (browser preview, MuPDF) but causes
    spec-strict readers (Acrobat, Preview.app) to treat the field's
    value as unset/inconsistent and fall back to a different mark.
    """
    xref = widget_xref
    seen: set[int] = set()
    while xref not in seen:
        seen.add(xref)
        if doc.xref_get_key(xref, "FT")[0] != "null":
            return xref
        parent = doc.xref_get_key(xref, "Parent")
        if parent[0] != "xref":
            return xref
        xref = int(parent[1].split()[0])
    return xref


def set_checkbox(page: pymupdf.Page, name: str, on_value: str,
                 *, checked: bool) -> bool:
    """Tick or untick the CheckBox widget ``name`` whose on-state is
    ``on_value`` on ``page``.

    Implementation: set ``/AS`` on the underlying annotation xref rather
    than going through ``Widget.field_value``. That preserves the form's
    original export value (``mr``/``mrs``/``Application``/...) instead of
    normalising to ``Yes``/``Off``. ``/V`` is written to whichever xref
    actually owns the field (see ``_field_value_xref``) so Acrobat-class
    readers agree with lenient ones on which option is selected.

    Returns True if the widget was found and updated, False otherwise.
    """
    w = _find_widget(page, name, field_type="CheckBox", on_value=on_value)
    if w is None:
        return False
    doc = page.parent
    xref = w.xref
    if checked:
        doc.xref_set_key(xref, "AS", f"/{on_value}")
        doc.xref_set_key(_field_value_xref(doc, xref), "V", f"/{on_value}")
    else:
        doc.xref_set_key(xref, "AS", "/Off")
        # Only clear a field-level V when this widget IS the field (a
        # standalone checkbox). For radio-group Kids, the sibling that
        # gets checked=True owns writing the shared Parent's V; clearing
        # it here too would race against that call depending on
        # iteration order and could stomp the real selection.
        if doc.xref_get_key(xref, "FT")[0] != "null":
            doc.xref_set_key(xref, "V", "/Off")
    return True


def collect_radio_on_states(page: pymupdf.Page, name: str) -> list[str]:
    """Return the list of on-state tokens for a radio group ``name``
    on ``page``. There may be one widget per state, all sharing the
    same field name. Returns [] if the group doesn't exist.
    """
    states: list[str] = []
    for w in page.widgets() or []:
        if w.field_name != name or w.field_type_string != "CheckBox":
            continue
        ws = w.button_states() or {}
        # The "down" state is the one the widget takes when checked;
        # "normal" lists the alternative states. For a radio group we
        # want the union, minus "Off".
        for s in (ws.get("down") or []) + (ws.get("normal") or []):
            if s and s != "Off" and s not in states:
                states.append(s)
    return states
