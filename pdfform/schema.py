"""Config schema for the form engine.

Plain dataclasses -- no Pydantic. Validation errors are raised as
``ConfigError`` with a precise location in the YAML so the user can
fix the file.

The shape is intentionally close to the YAML so that
``Config.from_yaml(d)`` is mostly a type-coercion pass, not a translation.

YAML example:

    id: form956
    title: "Form 956"
    pdf: pdfs/956.pdf
    pages: 6
    fields:
      - { app: agent_title, pdf: "mg.title", type: radio, label: "Title",
          options: { mr: "Mr", mrs: "Mrs" } }
      - { app: agent_family_name, pdf: "mg.name fam", type: text,
          label: "Family name", required: true }
      - app: additional_people
        type: group
        pdf_base: "cc.name"
        max_items: 5
        item:
          - { app: family, pdf: "cc.name fam", type: text, label: "Family" }
          - { app: given,  pdf: "cc.name giv", type: text, label: "Given"  }
      - { app: agent_declarations, pdf: "mg.dec", type: check_all,
          label: "Agent declarations" }
    sections:
      - { title: "Migration agent", fields: [agent_title, agent_family_name] }
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ConfigError(ValueError):
    """A user-visible error in a form config file."""


# --------------------------------------------------------------------- leaf fields
@dataclass
class TextField:
    app: str
    pdf: str
    label: str = ""
    required: bool = False
    format: str | None = None          # "date" | "email" | "tel" | "number" | None
    type: str = "text"

    @classmethod
    def from_dict(cls, d: dict) -> "TextField":
        if d.get("type") not in (None, "text"):
            raise ConfigError(f"text field {d.get('app')!r}: type must be 'text'")
        fmt = d.get("format")
        if fmt is not None and fmt not in ("date", "email", "tel", "number"):
            raise ConfigError(
                f"text field {d.get('app')!r}: unknown format {fmt!r} "
                f"(allowed: date, email, tel, number)"
            )
        return cls(
            app=d["app"],
            pdf=d["pdf"],
            label=d.get("label", ""),
            required=bool(d.get("required", False)),
            format=fmt,
            type="text",
        )


@dataclass
class RadioField:
    app: str
    pdf: str
    label: str = ""
    required: bool = False
    options: dict[str, str] = field(default_factory=dict)
    type: str = "radio"

    @classmethod
    def from_dict(cls, d: dict) -> "RadioField":
        if d.get("type") not in (None, "radio"):
            raise ConfigError(f"radio field {d.get('app')!r}: type must be 'radio'")
        opts = d.get("options")
        if opts is None:
            # Single on-state checkbox. The on-state will be filled in by
            # the engine from the PDF's actual button states.
            opts = {}
        if not isinstance(opts, dict):
            raise ConfigError(
                f"radio field {d.get('app')!r}: 'options' must be a map"
            )
        if not opts:
            # Defer population to the engine once the PDF is scanned.
            pass
        else:
            # values may be strings or nulls; coerce to str.
            opts = {str(k): ("" if v is None else str(v)) for k, v in opts.items()}
        return cls(
            app=d["app"],
            pdf=d["pdf"],
            label=d.get("label", ""),
            required=bool(d.get("required", False)),
            options=opts,
            type="radio",
        )


# --------------------------------------------------------------------- composite fields
@dataclass
class GroupField:
    """A list-valued app field that fans out to suffixed PDF widgets.

    The PDF has widgets named ``pdf_base`` and ``pdf_base " " 1``,
    ``pdf_base " " 2``, ... up to ``max_items``. The app provides a
    list of dicts; each dict's keys come from the inner ``item`` field
    defs.
    """
    app: str
    label: str
    pdf_base: str
    item: list[TextField]
    max_items: int
    type: str = "group"

    @classmethod
    def from_dict(cls, d: dict) -> "GroupField":
        if d.get("type") not in (None, "group"):
            raise ConfigError(f"group field {d.get('app')!r}: type must be 'group'")
        if "pdf_base" not in d:
            raise ConfigError(f"group field {d.get('app')!r}: 'pdf_base' is required")
        item_defs = d.get("item")
        if not isinstance(item_defs, list) or not item_defs:
            raise ConfigError(
                f"group field {d.get('app')!r}: 'item' must be a non-empty list"
            )
        items = [TextField.from_dict(it) for it in item_defs]
        max_items = int(d.get("max_items", 1))
        if max_items < 1:
            raise ConfigError(
                f"group field {d.get('app')!r}: max_items must be >= 1"
            )
        return cls(
            app=d["app"],
            label=d.get("label", ""),
            pdf_base=d["pdf_base"],
            item=items,
            max_items=max_items,
            type="group",
        )


@dataclass
class CheckAllField:
    """A single app bool that ticks every suffixed widget in a sequence.

    Used for the 4 declarations on page 6: ``mg.dec`` -> ``mg.dec 1``
    .. ``mg.dec 4``. The engine finds all widgets whose name starts
    with ``pdf`` followed by a digit and ticks them all when the app
    value is truthy.
    """
    app: str
    pdf: str
    label: str = ""
    type: str = "check_all"

    @classmethod
    def from_dict(cls, d: dict) -> "CheckAllField":
        if d.get("type") not in (None, "check_all"):
            raise ConfigError(
                f"check_all field {d.get('app')!r}: type must be 'check_all'"
            )
        return cls(
            app=d["app"],
            pdf=d["pdf"],
            label=d.get("label", ""),
            type="check_all",
        )


# --------------------------------------------------------------------- top level
@dataclass
class Section:
    title: str
    fields: list[str]                  # app field names


@dataclass
class FormConfig:
    id: str
    title: str
    pdf: str
    fields: list                        # TextField | RadioField | GroupField | CheckAllField
    sections: list[Section] = field(default_factory=list)
    pages: int | None = None

    @property
    def field_by_app(self) -> dict[str, Any]:
        return {f.app: f for f in self.fields}

    @classmethod
    def from_dict(cls, d: dict) -> "FormConfig":
        for k in ("id", "title", "pdf", "fields"):
            if k not in d:
                raise ConfigError(f"form config: missing required key {k!r}")
        raw_fields = d["fields"]
        if not isinstance(raw_fields, list) or not raw_fields:
            raise ConfigError("form config: 'fields' must be a non-empty list")

        parsed: list = []
        seen: set[str] = set()
        for f in raw_fields:
            if not isinstance(f, dict) or "app" not in f:
                raise ConfigError(f"form config: field missing 'app': {f!r}")
            if "type" not in f:
                raise ConfigError(
                    f"form config: field {f.get('app')!r} missing 'type'"
                )
            app = f["app"]
            if app in seen:
                raise ConfigError(f"form config: duplicate app field {app!r}")
            seen.add(app)
            t = f["type"]
            if t == "text":
                parsed.append(TextField.from_dict(f))
            elif t == "radio":
                parsed.append(RadioField.from_dict(f))
            elif t == "group":
                parsed.append(GroupField.from_dict(f))
            elif t == "check_all":
                parsed.append(CheckAllField.from_dict(f))
            else:
                raise ConfigError(
                    f"form config: field {app!r} has unknown type {t!r}"
                )

        sections: list[Section] = []
        for s in d.get("sections") or []:
            sections.append(Section(title=s.get("title", ""),
                                    fields=list(s.get("fields") or [])))
        # Validate that every section's field name exists.
        for s in sections:
            for name in s.fields:
                if name not in seen:
                    raise ConfigError(
                        f"form config: section {s.title!r} references "
                        f"unknown field {name!r}"
                    )

        return cls(
            id=str(d["id"]),
            title=str(d["title"]),
            pdf=str(d["pdf"]),
            fields=parsed,
            sections=sections,
            pages=d.get("pages"),
        )
