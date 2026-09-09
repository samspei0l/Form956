"""Domain validation shared by the finance documents (invoice, receipt).

The two payloads differ in which fields they carry but not in the *rules*:
required-and-non-blank, dates that are real dates in DD/MM/YYYY or
YYYY-MM-DD, and amounts that aren't negative. Reuses pdfform.validate's
date regexes / ValidationError / normalise_date, same as
costagreements/validate.py does for the agreements.

What is deliberately *not* required: ``case_reference``. It comes from the
case record's optional "Ref No", falling back to the CRM lead number, and
the finance UI passes an empty string when neither is set. Rejecting the
whole document over a blank cross-reference would block staff from issuing
an invoice for a real, payable matter.
"""
from __future__ import annotations

from datetime import date as _date

from pdfform.validate import DATE_DDMMYYYY, DATE_YYYYMMDD, ValidationError, normalise_date

from .money import parse_amt


def _check_date(field: str, value, errs: list[ValidationError]) -> None:
    if not isinstance(value, str) or not value:
        return
    if not (DATE_DDMMYYYY.match(value) or DATE_YYYYMMDD.match(value)):
        errs.append(ValidationError(
            field=field, code="format",
            message=f"{field!r} must be DD/MM/YYYY or YYYY-MM-DD, got {value!r}",
        ))
        return
    try:
        d, m, y = normalise_date(value).split("/")
        _date(int(y), int(m), int(d))
    except ValueError:
        errs.append(ValidationError(
            field=field, code="value", message=f"{field!r} is not a real date: {value!r}",
        ))


def validate_finance_document(
    payload: dict,
    required: tuple[str, ...],
    date_fields: tuple[str, ...],
    money_fields: tuple[str, ...],
    require_line_items: bool = False,
    positive_fields: tuple[str, ...] = (),
) -> list[ValidationError]:
    errs: list[ValidationError] = []

    for f in required:
        v = payload.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            errs.append(ValidationError(field=f, code="required", message=f"{f!r} is required"))

    if require_line_items:
        items = payload.get("line_items")
        if not isinstance(items, list) or not items:
            errs.append(ValidationError(
                field="line_items", code="required",
                message="'line_items' must be a non-empty list",
            ))
        else:
            for i, item in enumerate(items):
                if not isinstance(item, dict):
                    errs.append(ValidationError(
                        field=f"line_items[{i}]", code="format",
                        message=f"'line_items[{i}]' must be an object",
                    ))
                elif not str(item.get("description") or "").strip():
                    errs.append(ValidationError(
                        field=f"line_items[{i}].description", code="required",
                        message=f"'line_items[{i}].description' is required",
                    ))

    for f in date_fields:
        _check_date(f, payload.get(f), errs)

    for f in money_fields:
        v = payload.get(f)
        if v in (None, ""):
            continue
        if parse_amt(v) < 0:
            errs.append(ValidationError(field=f, code="value", message=f"{f!r} must not be negative"))

    for f in positive_fields:
        v = payload.get(f)
        if v in (None, ""):
            continue
        if parse_amt(v) <= 0:
            errs.append(ValidationError(
                field=f, code="value", message=f"{f!r} must be greater than zero",
            ))

    return errs


def apply_finance_normalisations(payload: dict, date_fields: tuple[str, ...]) -> dict:
    """Return a shallow copy with every named date normalised to DD/MM/YYYY,
    so an ISO date and its AU-formatted twin hash to the same cache key."""
    out = dict(payload)
    for f in date_fields:
        v = out.get(f)
        if isinstance(v, str) and DATE_YYYYMMDD.match(v):
            out[f] = normalise_date(v)
    return out
