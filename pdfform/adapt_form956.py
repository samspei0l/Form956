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
    "end_client_resadd_pc",
})

# `mg.app` (Q1 "Is this a new application?") has its two checkbox widgets'
# AcroForm on-state names swapped relative to their printed labels in the
# source PDF: the "New appointment" widget's real on-state is "No", and the
# "Appointment has ended" widget's real on-state is "Yes" (verified via
# `page.widgets()` button_states() against the label text at those
# coordinates). The engine matches values directly against raw on-state
# names with no translation, so invert here rather than in the shared
# engine — every other radio field's on-state names match their app values.
_IS_NEW_APPLICATION_TO_RAW_STATE = {"Yes": "No", "No": "Yes"}


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
    """Strip a leading AU state abbreviation from a combined "STATE 1234"
    shape (e.g. "NSW 2000" -> "2000"); pass everything else through as-is.

    International postal codes (a 5-digit US ZIP, an alphanumeric UK/CA
    code, ...) must NOT be touched here. This used to fall back to
    truncating any digit-only string to its first 4 characters, which
    silently corrupted non-AU postcodes (a US ZIP "90210" became the wrong
    "9021") instead of leaving them alone.
    """
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        value = str(int(value))
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if re.fullmatch(r"\d{4}", value):
        return value
    # Only unwrap a "STATE 1234" shape (a state-abbreviation prefix in
    # front of the real AU postcode) when there's a letter in the value.
    # An all-digit-and-punctuation value (US ZIP, ZIP+4, ...) is left
    # untouched even if some 4-digit run inside it happens to be bounded
    # by non-digit characters (e.g. "90210-1234" must not become "1234").
    if re.search(r"[A-Za-z]", value):
        match = re.search(r"\b(\d{4})\b", value)
        if match:
            return match.group(1)
    return value


def _merge_people(
    payload: dict,
    family: str | None,
    given: str | None,
) -> None:
    """Client 1 is always PDF row 0 ("cc.name fam"/"cc.name giv"); any
    dependants already in ``people`` are additional people and belong in
    rows 1+ ("cc.name fam 2" etc). Insert Client 1 ahead of them instead of
    overwriting people[0] — overwriting silently dropped the first
    dependant whenever one was present."""
    if not family and not given:
        return
    people = payload.get("people")
    if not isinstance(people, list):
        people = []
    row: dict[str, str] = {}
    if family:
        row["family"] = family
    if given:
        row["given"] = given
    payload["people"] = [row, *people]


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

    if out.get("is_new_application") in _IS_NEW_APPLICATION_TO_RAW_STATE:
        out["is_new_application"] = _IS_NEW_APPLICATION_TO_RAW_STATE[out["is_new_application"]]

    return out
