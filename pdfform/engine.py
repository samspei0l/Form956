"""FormEngine: load a YAML config + fill a PDF from app data.

The engine is generic. Each PDF form lives in its own YAML file
(see ``forms/form956.yaml`` for the canonical example). The engine:

  1. Parses the YAML into a ``FormConfig`` (see schema.py).
  2. Scans the PDF once to build a ``widget_name -> page_index`` map
     and a per-name on-state list for radios.
  3. Exposes the config as a JSON-serialisable dict for the UI.
  4. ``validate(data)`` returns a list of human-readable errors.
  5. ``fill(data, out_path)`` writes the filled PDF, fanning out
     groups and check_all sequences as declared in the config.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import pymupdf
import yaml

from . import widgets
from .schema import (
    CheckAllField,
    ConfigError,
    FormConfig,
    GroupField,
    RadioField,
    TextField,
)


class FormEngine:
    def __init__(self, config_path: str, project_root: str | None = None):
        self.config_path = config_path
        self.project_root = project_root or os.path.dirname(
            os.path.dirname(os.path.abspath(config_path))
        )
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if not isinstance(raw, dict):
            raise ConfigError(f"{config_path}: top level must be a mapping")
        self.config: FormConfig = FormConfig.from_dict(raw)
        # Resolve pdf path relative to project root.
        self.pdf_path = self.config.pdf
        if not os.path.isabs(self.pdf_path):
            self.pdf_path = os.path.join(self.project_root, self.pdf_path)
        if not os.path.exists(self.pdf_path):
            raise ConfigError(
                f"{config_path}: pdf {self.config.pdf!r} not found "
                f"(resolved to {self.pdf_path})"
            )
        self._scan_widgets()
        # Backfill radio `options` from the PDF for fields declared
        # without explicit options (single-on-state checkboxes).
        for f in self.config.fields:
            if isinstance(f, RadioField) and not f.options:
                states = self._on_states.get(f.pdf, [])
                f.options = {s: s for s in states}

    # ----------------------------------------------------------------- scan
    def _scan_widgets(self) -> None:
        """Walk the PDF and build:
            self._page_for[widget_name]   -> 0-based page index
            self._on_states[widget_name]  -> list[str]  (for radios / check_all)
            self._widgets[widget_name]    -> list[widget ref] (for group fan-out)
        """
        self._page_for: dict[str, int] = {}
        self._on_states: dict[str, list[str]] = {}
        self._widgets: dict[str, list] = {}
        self._total_pages = 0
        doc = pymupdf.open(self.pdf_path)
        try:
            self._total_pages = len(doc)
            for pi, page in enumerate(doc):
                for w in page.widgets() or []:
                    n = w.field_name
                    if not n:
                        continue
                    self._page_for.setdefault(n, pi)
                    self._widgets.setdefault(n, []).append(w)
                    if w.field_type_string == "CheckBox":
                        states = w.button_states() or {}
                        s_list: list[str] = []
                        for s in (states.get("down") or []) + (states.get("normal") or []):
                            if s and s != "Off" and s not in s_list:
                                s_list.append(s)
                        if s_list:
                            # Merge: any widget in the same field name has the
                            # same on-state set; union anyway.
                            existing = self._on_states.setdefault(n, [])
                            for s in s_list:
                                if s not in existing:
                                    existing.append(s)
        finally:
            doc.close()

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def title(self) -> str:
        return self.config.title

    @property
    def total_pages(self) -> int:
        return self._total_pages

    # ----------------------------------------------------------------- schema for the UI
    def schema_dict(self) -> dict:
        """JSON-serialisable view of the config. The dynamic form
        renderer in templates/form.html consumes this."""
        fields: list[dict] = []
        for f in self.config.fields:
            if isinstance(f, TextField):
                fields.append({
                    "app": f.app, "type": "text", "label": f.label,
                    "required": f.required, "format": f.format,
                })
            elif isinstance(f, RadioField):
                fields.append({
                    "app": f.app, "type": "radio", "label": f.label,
                    "required": f.required,
                    "options": [{"value": k, "label": v} for k, v in f.options.items()],
                })
            elif isinstance(f, GroupField):
                fields.append({
                    "app": f.app, "type": "group", "label": f.label,
                    "max_items": f.max_items,
                    "item": [
                        {"app": sub.app, "type": "text", "label": sub.label,
                         "required": sub.required, "format": sub.format}
                        for sub in f.item
                    ],
                })
            elif isinstance(f, CheckAllField):
                fields.append({
                    "app": f.app, "type": "check_all", "label": f.label,
                    "count": self._count_check_all(f),
                })
        sections = [{"title": s.title, "fields": s.fields}
                    for s in self.config.sections]
        return {
            "id": self.config.id,
            "title": self.config.title,
            "pages": self._total_pages,
            "fields": fields,
            "sections": sections,
        }

    def _count_check_all(self, f: CheckAllField) -> int:
        """How many suffixed widgets exist for a check_all base name."""
        n = 0
        for name in self._widgets:
            if name == f.pdf:
                n += 1
            elif re.match(rf"^{re.escape(f.pdf)}\s+\d+$", name):
                n += 1
        return n

    # ----------------------------------------------------------------- validation
    def validate(self, data: dict) -> list[str]:
        errors: list[str] = []
        known = {f.app for f in self.config.fields}
        for k in data:
            if k not in known:
                errors.append(f"Unknown field {k!r}")
        for f in self.config.fields:
            v = data.get(f.app)
            if isinstance(f, TextField):
                if f.required and not (isinstance(v, str) and v.strip()):
                    errors.append(f"{f.app!r} is required")
            elif isinstance(f, RadioField):
                if v is None or v == "" or v is False or v == 0 or v == "0":
                    if f.required:
                        errors.append(f"{f.app!r} is required")
                elif isinstance(v, bool) and len(f.options) == 1:
                    # Booleans are valid for single-on-state radios.
                    pass
                elif str(v) not in f.options:
                    errors.append(
                        f"{f.app!r}: value {v!r} is not in allowed options "
                        f"{list(f.options)}"
                    )
            elif isinstance(f, GroupField):
                items = v or []
                if not isinstance(items, list):
                    errors.append(f"{f.app!r}: must be a list")
                    continue
                if len(items) > f.max_items:
                    errors.append(
                        f"{f.app!r}: too many items ({len(items)} > {f.max_items})"
                    )
                for i, item in enumerate(items):
                    if not isinstance(item, dict):
                        errors.append(f"{f.app!r}[{i}]: must be an object")
                        continue
                    for sub in f.item:
                        sub_v = item.get(sub.app)
                        if sub.required and not (isinstance(sub_v, str) and sub_v.strip()):
                            errors.append(f"{f.app!r}[{i}].{sub.app!r}: required")
            elif isinstance(f, CheckAllField):
                pass   # bool coercion handled in fill
        return errors

    # ----------------------------------------------------------------- fill
    def fill(self, data: dict, out_path: str) -> None:
        """Open the source PDF, write all mapped widgets, save to
        ``out_path``. Raises ``ConfigError`` if a referenced widget
        doesn't exist in the PDF.
        """
        doc = pymupdf.open(self.pdf_path)
        try:
            # Pre-extract the on-state per (page_index, widget_name) from
            # the live document. We can't keep widget refs across fills
            # because pymupdf's Widget.parent is a weakref to Page, and
            # the Page can be GC'd once we leave the scan loop.
            live_states: dict[str, list[tuple[int, str | None]]] = {}
            for pi, page in enumerate(doc):
                for w in page.widgets() or []:
                    n = w.field_name
                    if not n:
                        continue
                    states = w.button_states() or {}
                    on_state = None
                    down = states.get("down") or []
                    if down and down[0] not in (None, "Off"):
                        on_state = down[0]
                    else:
                        for s in states.get("normal") or []:
                            if s and s != "Off":
                                on_state = s
                                break
                    live_states.setdefault(n, []).append((pi, on_state))
            for f in self.config.fields:
                v = data.get(f.app)
                if isinstance(f, TextField):
                    self._fill_text(doc, f, v)
                elif isinstance(f, RadioField):
                    self._fill_radio(doc, f, v, live_states)
                elif isinstance(f, GroupField):
                    self._fill_group(doc, f, v)
                elif isinstance(f, CheckAllField):
                    self._fill_check_all(doc, f, v, live_states)
            doc.save(out_path)
            # On Windows, pymupdf can leave the file in a deferred-write
            # state where a subsequent pymupdf.open() in the same
            # process sees truncated bytes. Force a flush + fsync so
            # the next open() reads the real bytes.
            with open(out_path, "rb") as _f:
                _f.flush()
                try:
                    os.fsync(_f.fileno())
                except OSError:
                    pass
        finally:
            doc.close()

    # ---- per-type fill helpers
    def _page(self, name: str) -> int:
        if name not in self._page_for:
            raise ConfigError(
                f"widget {name!r} not found in {self.pdf_path}. "
                f"Did the PDF change?"
            )
        return self._page_for[name]

    def _fill_text(self, doc, f: TextField, value: Any) -> None:
        if value is None or value == "":
            return
        page = doc[self._page(f.pdf)]
        ok = widgets.set_text(page, f.pdf, str(value))
        if not ok:
            raise ConfigError(
                f"text field {f.app!r} (pdf {f.pdf!r}) not found in PDF"
            )

    def _fill_radio(self, doc, f: RadioField, value: Any,
                    live_states: dict[str, list[tuple[int, str | None]]]) -> None:
        if value is None or value == "" or value is False or value == 0 or value == "0":
            return
        # Booleans for single-on-state radios ("IAAAS" tick-box): truthy
        # means "tick"; falsy means "untick". The on-state comes from
        # the backfilled options list, which is just the on-state name.
        if isinstance(value, bool) and len(f.options) == 1:
            target = next(iter(f.options))
        else:
            target = str(value)
        if target not in f.options:
            raise ConfigError(
                f"radio {f.app!r}: value {target!r} not in options "
                f"{list(f.options)}"
            )
        # Tick the matching on-state; untick the siblings.
        page_idx = self._page(f.pdf)
        page = doc[page_idx]
        for pi, on_state in live_states.get(f.pdf, []):
            if pi != page_idx or on_state is None:
                continue
            widgets.set_checkbox(page, f.pdf, on_state, checked=(on_state == target))

    def _fill_group(self, doc, f: GroupField, value: Any) -> None:
        items = value or []
        if not isinstance(items, list):
            raise ConfigError(f"group {f.app!r}: app data is not a list")
        # The PDF widgets are: bare (no suffix) + suffixed (e.g. 2, 3, 4
        # ...). The base widget name is `sub.pdf`; the suffixed widgets
        # are `sub.pdf " " N`. Map list index 0 to the bare widget, index
        # k>=1 to the widget with suffix k+1 (so 1 -> 2, 2 -> 3, etc.).
        for i, item in enumerate(items[: f.max_items]):
            if not isinstance(item, dict):
                continue
            for sub in f.item:
                sub_v = item.get(sub.app)
                if sub_v is None or sub_v == "":
                    continue
                if i == 0:
                    pdf_name = sub.pdf
                else:
                    pdf_name = f"{sub.pdf} {i + 1}"
                page = doc[self._page(pdf_name)]
                ok = widgets.set_text(page, pdf_name, str(sub_v))
                if not ok:
                    raise ConfigError(
                        f"group {f.app!r}[{i}].{sub.app!r}: "
                        f"pdf widget {pdf_name!r} not found in PDF"
                    )

    def _fill_check_all(self, doc, f: CheckAllField, value: Any,
                        live_states: dict[str, list[tuple[int, str | None]]]) -> None:
        # Find all widget names that match `pdf` or `pdf " " <digit>`.
        pattern = re.compile(rf"^{re.escape(f.pdf)}(\s+\d+)?$")
        names = [n for n in live_states if pattern.match(n)]
        if not names:
            raise ConfigError(
                f"check_all {f.app!r}: no widgets matching {f.pdf!r} found"
            )
        # Sort: the bare name first, then the suffixed ones in order.
        def _key(n: str) -> tuple[int, int]:
            m = re.match(rf"^{re.escape(f.pdf)}\s+(\d+)$", n)
            if m:
                return (0, int(m.group(1)))
            return (0, 0)
        names.sort(key=_key)
        ticked = bool(value) and value != "false" and value != "0"
        for n in names:
            page_idx = self._page(n)
            page = doc[page_idx]
            for pi, on_state in live_states.get(n, []):
                if pi != page_idx or on_state is None:
                    continue
                widgets.set_checkbox(page, n, on_state, checked=ticked)
                break   # one on-state per widget is enough

    # ----------------------------------------------------------------- read back
    def extract(self, pdf_path: str) -> dict:
        """Read the filled PDF and return a flat dict of widget_name -> value.

        Mirrors the shape of the app data (best-effort), but uses the
        PDF widget names as keys. Useful for the "Extract" button.
        """
        # Pre-extract everything in a single pass while pages are alive.
        # Reading w.field_value after the page has been GC'd returns ''.
        rows: list[tuple[str, str]] = []
        doc = pymupdf.open(pdf_path)
        try:
            for page in doc:
                for w in page.widgets() or []:
                    n = w.field_name
                    if not n:
                        continue
                    if w.field_type_string == "Text":
                        v = w.field_value or ""
                    elif w.field_type_string == "CheckBox":
                        v = w.field_value
                    else:
                        continue
                    if v and v != "Off":
                        rows.append((n, v))
        finally:
            doc.close()
        return dict(rows)


# --------------------------------------------------------------------- registry
_engines: dict[str, FormEngine] = {}


def load_all(forms_dir: str, project_root: str | None = None) -> dict[str, FormEngine]:
    """Load every ``*.yaml`` in ``forms_dir`` and return a dict of
    ``form_id -> FormEngine``. Skips files starting with ``_`` (used
    for the example file)."""
    engines: dict[str, FormEngine] = {}
    for fn in sorted(os.listdir(forms_dir)):
        if not fn.endswith((".yaml", ".yml")) or fn.startswith("_"):
            continue
        path = os.path.join(forms_dir, fn)
        try:
            eng = FormEngine(path, project_root=project_root)
        except ConfigError as e:
            # Bad config: don't take the whole app down. Log and skip.
            print(f"[pdfform] skipping {fn}: {e}")
            continue
        engines[eng.id] = eng
    _engines.update(engines)
    return engines


def get_engine(form_id: str) -> FormEngine | None:
    return _engines.get(form_id)
