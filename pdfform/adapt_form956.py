"""Adapt legacy React overlay-builder payloads to the Form 956 engine schema.

The old ``buildForm956Pdf`` path used flat, underscore-prefixed keys
(``_client_family``, ``_client_given``) and fields that never existed
on the official PDF (``client_email``). The AcroForm engine expects
the canonical names from ``forms/form956.yaml`` — notably the ``people``
group for client names.
"""
from __future__ import annotations

import re
from typing import Any

# Legacy-only keys from the overlay builder. No matching PDF widget.
_DROP_FIELDS = frozenset({
    "client_email",
})

# Flat client-name keys → ``people[0].{family,given}``.
_FAMILY_ALIASES = ("_client_family", "client_family_name", "client_family")
_GIVEN_ALIASES = ("_client_given", "client_given_names", "client_given")

# Alternate DOB keys seen in case/practitioner records.
_AGENT_DOB_ALIASES = (
    "agent_date_of_birth",
    "agentDateOfBirth",
    "migration_agent_dob",
)

_POSTCODE_FIELDS = frozenset({
    "agent_resadd_pc",
    "agent_postal_pc",
    "client_resadd_pc",
    "client_postcode",
})


def _first_str(payload: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = payload.get(k)
        if v is not None and v != "":
            return str(v).strip()
    return None


def _pop_first(payload: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        if k in payload:
            v = payload.pop(k)
            if v is not None and v != "":
                return str(v).strip()
    return None


def _normalise_postcode(value: Any) -> str:
    """Coerce common postcode shapes to a 4-digit AU string."""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        value = str(int(value))
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if re.fullmatch(r"\d{4}", value):
        return value
    match = re.search(r"\b(\d{4})\b", value)
    if match:
        return match.group(1)
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 4:
        return digits[:4]
    return value


def _merge_people(
    payload: dict,
    family: str | None,
    given: str | None,
) -> None:
    if not family and not given:
        return
    people = payload.get("people")
    if not isinstance(people, list):
        people = []
    row: dict[str, str] = {}
    if people and isinstance(people[0], dict):
        row = dict(people[0])
    if family:
        row["family"] = family
    if given:
        row["given"] = given
    if people:
        people[0] = row
    else:
        people = [row]
    payload["people"] = people


def adapt_form956_payload(payload: dict) -> dict:
    """Return a shallow copy of ``payload`` with legacy keys normalised."""
    out = dict(payload)

    for k in _DROP_FIELDS:
        out.pop(k, None)

    family = _pop_first(out, _FAMILY_ALIASES)
    given = _pop_first(out, _GIVEN_ALIASES)
    _merge_people(out, family, given)

    if not out.get("agent_dob"):
        alt = _first_str(out, _AGENT_DOB_ALIASES)
        if alt:
            out["agent_dob"] = alt
            for k in _AGENT_DOB_ALIASES:
                out.pop(k, None)

    for f in _POSTCODE_FIELDS:
        if f in out and out[f] not in (None, ""):
            out[f] = _normalise_postcode(out[f])

    return out
