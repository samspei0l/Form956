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


def set_checkbox(page: pymupdf.Page, name: str, on_value: str,
                 *, checked: bool) -> bool:
    """Tick or untick the CheckBox widget ``name`` whose on-state is
    ``on_value`` on ``page``.

    Implementation: set ``/AS`` and ``/V`` on the underlying annotation
    xref rather than going through ``Widget.field_value``. That preserves
    the form's original export value (``mr``/``mrs``/``Application``/...)
    instead of normalising to ``Yes``/``Off``.

    Returns True if the widget was found and updated, False otherwise.
    """
    w = _find_widget(page, name, field_type="CheckBox", on_value=on_value)
    if w is None:
        return False
    doc = page.parent
    xref = w.xref
    if checked:
        doc.xref_set_key(xref, "AS", f"/{on_value}")
        doc.xref_set_key(xref, "V", f"/{on_value}")
    else:
        doc.xref_set_key(xref, "AS", "/Off")
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
