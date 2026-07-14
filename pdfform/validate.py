"""Pydantic validation for Form 956 payloads.

Returns a list of ``{field, code, message}`` errors. Empty list = valid.

Why a separate validator (instead of just the engine's permissive
``FormEngine.validate``):
  - The engine's validator only checks app-schema shape (unknown fields,
    required flags, radio values). It doesn't enforce the domain rules
    that the user-facing brief calls out (MARN format, DOB format,
    email regex, postcode format).
  - In production, those domain rules are how you stop a typo in the
    agent's MARN from getting stamped into a legal document.

Adding a new form = add a sibling module (e.g. ``validate_form1000.py``)
with the same ``validate(payload) -> list[ValidationError]`` signature
and wire it into the route.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

DATE_DDMMYYYY = re.compile(r"^\d{2}/\d{2}/\d{4}$")
DATE_YYYYMMDD = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MARN_RE = re.compile(r"^\d{7}$")
POSTCODE_RE = re.compile(r"^\d{4}$")
# RFC-5322 is too permissive; this is "good enough" for client form input.
EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"
)


@dataclass(frozen=True)
class ValidationError:
    field: str
    code: str          # "required" | "format" | "unknown" | "value"
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------- form-956 rules
# Field names match the YAML in forms/form956.yaml.

#: Required by the form (printed in the official DHA document).
REQUIRED_FIELDS = frozenset({
    "agent_family_name",
    "agent_given_names",
    "agent_dob",
    "agent_marn",
    "agent_email",
    "client_role",
    "application_type",
})

#: Date fields — accept DD/MM/YYYY or YYYY-MM-DD; normalise to DD/MM/YYYY.
DATE_FIELDS = frozenset({
    "agent_dob",
    "client_dob",
    "date_lodged",
    "agent_declaration_date",
    "client_declaration_date",
    "end_client_dob",
})

#: Email fields (must exist in forms/form956.yaml).
EMAIL_FIELDS = frozenset({
    "agent_email",
    "end_client_email",
})


def normalise_date(s: str) -> str:
    """Convert 'YYYY-MM-DD' or 'DD/MM/YYYY' to 'DD/MM/YYYY'. Pass-through
    for anything else (caller will produce a validation error)."""
    if DATE_YYYYMMDD.match(s):
        y, m, d = s.split("-")
        return f"{d}/{m}/{y}"
    return s  # assume already DD/MM/YYYY; format check is the caller's job


def validate_form956(payload: dict, known_apps: set[str]) -> list[ValidationError]:
    """Validate a Form 956 fill payload.

    Args:
        payload: the JSON body from the POST.
        known_apps: app field names from the engine's schema. Anything
            in ``payload`` not in this set is an unknown field.

    Returns a list of errors. Empty list = payload is valid.

    Side effect: ``payload`` is *not* mutated. Use ``apply_normalisations``
    to get back a copy with dates normalised to DD/MM/YYYY.
    """
    errs: list[ValidationError] = []

    # 1. Unknown fields.
    for k in payload:
        if k not in known_apps:
            errs.append(ValidationError(
                field=k, code="unknown",
                message=f"Unknown field {k!r} for this form",
            ))

    # 2. Required fields.
    for f in REQUIRED_FIELDS:
        v = payload.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            errs.append(ValidationError(
                field=f, code="required",
                message=f"{f!r} is required",
            ))

    # 3. MARN format.
    marn = payload.get("agent_marn")
    if isinstance(marn, str) and marn and not MARN_RE.match(marn):
        errs.append(ValidationError(
            field="agent_marn", code="format",
            message="MARN must be exactly 7 digits",
        ))

    # 4. Date fields.
    for f in DATE_FIELDS:
        v = payload.get(f)
        if not v:
            continue
        if not isinstance(v, str):
            errs.append(ValidationError(
                field=f, code="format",
                message=f"{f!r} must be a string in DD/MM/YYYY or YYYY-MM-DD",
            ))
            continue
        if not (DATE_DDMMYYYY.match(v) or DATE_YYYYMMDD.match(v)):
            errs.append(ValidationError(
                field=f, code="format",
                message=f"{f!r} must be DD/MM/YYYY or YYYY-MM-DD, got {v!r}",
            ))
            continue
        # Range check (parses both shapes via normalise_date).
        try:
            d, m, y = normalise_date(v).split("/")
            date(int(y), int(m), int(d))
        except ValueError:
            errs.append(ValidationError(
                field=f, code="value",
                message=f"{f!r} is not a real date: {v!r}",
            ))

    # 5. Email fields.
    for f in EMAIL_FIELDS:
        v = payload.get(f)
        if not v:
            continue
        if not isinstance(v, str) or not EMAIL_RE.match(v):
            errs.append(ValidationError(
                field=f, code="format",
                message=f"{f!r} must be a valid email address",
            ))

    # 6. Postcode fields (*_pc).
    for f in payload:
        if not (f.endswith("_pc") or f == "agent_postal_pc"
                or f == "client_postcode"):
            continue
        v = payload[f]
        if v is None or v == "":
            continue
        if not isinstance(v, str) or not POSTCODE_RE.match(v):
            errs.append(ValidationError(
                field=f, code="format",
                message=f"{f!r} must be a 4-digit postcode",
            ))

    return errs


def apply_normalisations(payload: dict) -> dict:
    """Return a shallow copy of ``payload`` with date strings normalised
    to ``DD/MM/YYYY``. Anything not in DATE_FIELDS is passed through."""
    out = dict(payload)
    for f in DATE_FIELDS:
        v = out.get(f)
        if isinstance(v, str) and DATE_YYYYMMDD.match(v):
            out[f] = normalise_date(v)
    return out
