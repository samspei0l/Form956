"""Divorce cost agreement: the any-number-of-rows payment schedule ("Add row"
in the CRM form, as on the Partner Visa agreement).

The fixed three-instalment table is kept for payloads that don't send
``payment_schedule_items`` (drafts saved before it existed); those tests
pin that it is unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from costagreements.builders.divorce import (  # noqa: E402
    DivorceCostAgreementData,
    _schedule_rows,
    apply_divorce_normalisations,
    build_divorce_cost_agreement,
    validate_divorce_cost_agreement,
)

BASE = {
    "date": "26/11/2026",
    "our_ref": "FAM - 0042",
    "client_name": "John Citizen",
    "client_address": "12 Sample St, Sampleville NSW 2000",
    "capacity": "solicitor",
    "professional_fee": "2000",
    "estimated_days": "02",
    "service_fee": "0",
    "rep_name": "Chi Nguyen",
}

FIVE_ROWS = [
    {"description": "1st Instalment – On the day of Signing Cost Agreement", "amount": "400"},
    {"description": "2nd Instalment – On the day of lodgement", "amount": "400"},
    {"description": "3rd Instalment – On the day your application is finalised", "amount": "400"},
    {"description": "4th Instalment – On the day of the court hearing", "amount": "400"},
    {"description": "5th Instalment – On the day the divorce order is issued", "amount": "400"},
]


def _text(payload: dict) -> str:
    data = DivorceCostAgreementData.from_payload(apply_divorce_normalisations(payload))
    doc = pymupdf.open(stream=build_divorce_cost_agreement(data), filetype="pdf")
    text = " ".join(" ".join(p.get_text() for p in doc).split())
    doc.close()
    return text


# --------------------------------------------------------------------- rows
def test_every_row_is_printed_with_its_amount():
    text = _text(dict(BASE, payment_schedule_items=FIVE_ROWS))
    assert "PAYMENT SCHEDULE FOR COST AND DISBURSEMENT" in text
    for row in FIVE_ROWS:
        assert row["description"] in text
    assert text.count("$400.00") >= 5


def test_descriptions_print_verbatim_without_extra_numbering():
    text = _text(dict(BASE, payment_schedule_items=FIVE_ROWS))
    assert "1. 1st Instalment" not in text


def test_amounts_that_do_not_sum_to_the_fee_are_split_evenly():
    rows = _schedule_rows([{"description": "A", "amount": "100"}, {"description": "B", "amount": "100"},
                           {"description": "C", "amount": "100"}, {"description": "D", "amount": "100"}], 1000.0)
    assert rows == [("A", "$250.00"), ("B", "$250.00"), ("C", "$250.00"), ("D", "$250.00")]


def test_even_split_remainder_goes_to_the_last_row():
    rows = _schedule_rows([{"description": d, "amount": ""} for d in "ABC"], 1000.0)
    assert [a for _, a in rows] == ["$333.33", "$333.33", "$333.34"]


def test_amounts_with_thousand_separators_are_honoured():
    rows = _schedule_rows([{"description": "A", "amount": "1,500"}, {"description": "B", "amount": "500"}], 2000.0)
    assert rows == [("A", "$1,500.00"), ("B", "$500.00")]


def test_blank_rows_are_dropped_and_unlabelled_rows_get_a_name():
    rows = _schedule_rows([{"description": "", "amount": ""}, {"description": "", "amount": "2000"}], 2000.0)
    assert rows == [("Instalment 1", "$2,000.00")]


def test_no_rows_prints_the_full_payment_box():
    text = _text(dict(BASE, payment_schedule_items=[{"description": " ", "amount": ""}]))
    assert "Full payment of $2,000.00" in text
    assert "PAYMENT SCHEDULE FOR COST AND DISBURSEMENT" not in text


def test_client_pays_in_full_still_wins_over_rows():
    text = _text(dict(BASE, hide_payment_schedule=True, payment_schedule_items=FIVE_ROWS))
    assert "Full payment of $2,000.00" in text
    assert "4th Instalment" not in text


def test_many_rows_paginate_without_error():
    rows = [{"description": f"Instalment {i} – on milestone {i}", "amount": "50"} for i in range(1, 41)]
    text = _text(dict(BASE, payment_schedule_items=rows))
    assert "milestone 40" in text


# --------------------------------------------------------------------- legacy
def test_payload_without_rows_keeps_the_fixed_three_instalments():
    text = _text(dict(BASE, instalment1_amount="1000", instalment2_amount="500", instalment3_amount="500"))
    assert "1st Instalment – On the day of Signing Cost Agreement" in text
    assert "3rd Instalment – On the day your application is finalised" in text
    assert "$1,000.00" in text


# --------------------------------------------------------------------- validation
def test_rows_validate():
    assert validate_divorce_cost_agreement(dict(BASE, payment_schedule_items=FIVE_ROWS)) == []


@pytest.mark.parametrize("bad, field_code", [
    ("not a list", "shape"),
    ([{"description": "x", "amount": "-5"}], "value"),
    (["just a string"], "shape"),
    ([{"description": "x", "amount": "1"}] * 51, "value"),
])
def test_bad_rows_are_rejected(bad, field_code):
    errs = validate_divorce_cost_agreement(dict(BASE, payment_schedule_items=bad))
    assert [(e.field, e.code) for e in errs] == [("payment_schedule_items", field_code)]
