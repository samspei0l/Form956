"""Tests for the Statement of Services builder (the third finance document).

Same family rules as tests/test_finance_documents.py -- never signed, so no
SIGMETA and no stamp band; "Issued:" is the statement's own date; the Doc ID
is stable -- plus the statement's own: every total is recomputed from the
rows, GST comes out of professional-fee rows only, and the closing wording
is fixed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from costagreements import layout as L  # noqa: E402
from costagreements.builders.statement_of_services import (  # noqa: E402
    CLOSING_LINES,
    StatementOfServicesData,
    apply_statement_of_services_normalisations,
    build_statement_of_services,
    validate_statement_of_services,
)

PAYLOAD = {
    "statement_number": "SOS-2026-0003",
    "issue_date": "12/11/2026",
    "client_name": "John Citizen",
    "matter": "Partner visa (subclass 820/801)",
    "case_reference": "WZL-1234",
    "agreement_date": "01/08/2026",
    "period_from": "01/08/2026",
    "period_to": "12/11/2026",
    "gst_rate_percent": 10,
    "rows": [
        {"date": "05/08/2026", "particulars": "Initial consultation and eligibility advice",
         "kind": "fee", "amount": 1100},
        {"date": "20/09/2026", "particulars": "Preparation and lodgement of application",
         "kind": "fee", "amount": 3300},
        {"date": "20/09/2026", "particulars": "Department of Home Affairs — visa application charge",
         "kind": "disbursement", "amount": 9365},
    ],
    "disbursement_notes": "VAC paid by the firm on the client's behalf.",
    "payments_received": 12000,
    "refunds": 0,
    "credits": 0,
    "invoice_refs": [{"number": "INV-2026-0042", "date": "01/08/2026"}],
    "receipt_refs": [{"number": "RCT-2026-0007", "date": "02/08/2026"}],
}


def _build(payload: dict) -> bytes:
    return build_statement_of_services(
        StatementOfServicesData.from_payload(apply_statement_of_services_normalisations(payload)))


def _texts(pdf: bytes) -> list[str]:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    out = [p.get_text() for p in doc]
    doc.close()
    return out


def _flat(text: str) -> str:
    return " ".join(text.split())


# --------------------------------------------------------------------- validate
def test_minimal_payload_is_valid():
    assert validate_statement_of_services(PAYLOAD) == []


def test_missing_required_fields_reported():
    fields = {e.field for e in validate_statement_of_services({})}
    assert {"issue_date", "client_name", "rows"} <= fields


def test_row_without_particulars_rejected():
    errs = validate_statement_of_services(dict(PAYLOAD, rows=[{"kind": "fee", "amount": 10}]))
    assert [e.field for e in errs] == ["rows[0].particulars"]


def test_unknown_row_kind_and_negative_amount_rejected():
    errs = validate_statement_of_services(dict(PAYLOAD, rows=[
        {"particulars": "x", "kind": "refund", "amount": -5}]))
    assert {e.field for e in errs} == {"rows[0].kind", "rows[0].amount"}


def test_period_ending_before_it_starts_rejected():
    errs = validate_statement_of_services(dict(PAYLOAD, period_from="12/11/2026", period_to="01/08/2026"))
    assert [e.field for e in errs] == ["period_to"]


def test_impossible_date_rejected():
    errs = validate_statement_of_services(dict(PAYLOAD, agreement_date="31/02/2026"))
    assert [e.field for e in errs] == ["agreement_date"]


def test_iso_dates_normalise_everywhere():
    out = apply_statement_of_services_normalisations(dict(
        PAYLOAD, issue_date="2026-11-12",
        rows=[{"date": "2026-08-05", "particulars": "x", "amount": 1}],
        invoice_refs=[{"number": "INV-1", "date": "2026-08-01"}],
    ))
    assert out["issue_date"] == "12/11/2026"
    assert out["rows"][0]["date"] == "05/08/2026"
    assert out["invoice_refs"][0]["date"] == "01/08/2026"


# --------------------------------------------------------------------- totals
def test_totals_recomputed_from_rows_and_gst_only_on_fees():
    d = StatementOfServicesData.from_payload(PAYLOAD)
    assert d.fees_incl_gst == pytest.approx(4400)
    assert d.gst_amount == pytest.approx(400)
    assert d.fees_excl_gst == pytest.approx(4000)
    assert d.disbursements == pytest.approx(9365)  # no GST taken out of the VAC
    assert d.total_charges == pytest.approx(13765)
    assert d.balance == pytest.approx(1765)


def test_caller_totals_are_ignored():
    d = StatementOfServicesData.from_payload(apply_statement_of_services_normalisations(
        dict(PAYLOAD, total_charges=1, balance=0, gst_amount=999)))
    assert d.total_charges == pytest.approx(13765)
    assert d.gst_amount == pytest.approx(400)


def test_refunds_add_back_and_credits_reduce():
    d = StatementOfServicesData.from_payload(dict(PAYLOAD, payments_received=14000, refunds=235, credits=0))
    assert d.balance == pytest.approx(0)
    d = StatementOfServicesData.from_payload(dict(PAYLOAD, credits=1765))
    assert d.balance == pytest.approx(0)


def test_no_gst_rate_means_no_gst():
    d = StatementOfServicesData.from_payload(dict(PAYLOAD, gst_rate_percent=0))
    assert d.gst_amount == 0
    assert d.fees_excl_gst == pytest.approx(4400)


# --------------------------------------------------------------------- build
def test_builds_with_every_template_block():
    pages = _texts(_build(PAYLOAD))
    text = _flat("\n".join(pages))
    for expected in (
        "STATEMENT OF SERVICES",
        f"Registered Migration Agent: {L.RMA_NAME} | MARN {L.RMA_MARN}",
        "John Citizen", "Partner visa (subclass 820/801)", "SOS-2026-0003",
        "12/11/2026", "01/08/2026", "01/08/2026 – 12/11/2026",
        "Services performed and charges", "Particulars of each service actually performed",
        "Initial consultation and eligibility advice", "$1,100.00", "$3,300.00",
        "Account summary", "Professional fees (excl. GST)", "$4,000.00",
        "GST on professional fees", "$400.00", "Professional fees (incl. GST)", "$4,400.00",
        "Disbursements paid / incurred", "$9,365.00", "Total charges", "$13,765.00 AUD",
        "Payments received", "$12,000.00", "Refunds made", "Credits applied",
        "Balance owing", "$1,765.00 AUD",
        "DISBURSEMENTS", "visa application charge", "VAC paid by the firm",
        "INV-2026-0042 (01/08/2026)", "RCT-2026-0007 (02/08/2026)",
        *(_flat(line) for line in CLOSING_LINES),
    ):
        assert expected in text, f"{expected!r} missing"
    assert "TAX INVOICE" not in text
    assert "HOW TO PAY" not in text


def test_disbursement_rows_are_not_listed_as_services():
    page = _texts(_build(PAYLOAD))[0]
    services = page.split("Account summary")[0]
    assert "visa application charge" not in services


@pytest.mark.parametrize("payments, label, value", [
    (13765, "Balance", "Nil"),
    (14000, "Credit in your favour", "$235.00 AUD"),
])
def test_balance_wording(payments, label, value):
    text = _flat(_texts(_build(dict(PAYLOAD, payments_received=payments)))[0])
    assert f"{label} {value}" in text


def test_no_disbursements_prints_nil():
    rows = [r for r in PAYLOAD["rows"] if r["kind"] == "fee"]
    text = _flat(_texts(_build(dict(PAYLOAD, rows=rows, disbursement_notes="")))[0])
    assert "DISBURSEMENTS Nil" in text


def test_header_issued_date_and_stable_doc_id():
    first = _texts(_build(PAYLOAD))
    second = _texts(_build(PAYLOAD))
    for text in first:
        assert "Issued: 12/11/2026" in text

    def doc_id(t: str) -> str:
        return next(ln for ln in t.splitlines() if ln.startswith("Doc ID:")).split(":", 1)[1].strip()

    assert doc_id(first[0]) == doc_id(second[0])
    assert doc_id(first[0]).startswith("WZL-SOS-")


def test_long_statement_paginates_inside_the_frame():
    rows = [{"date": f"{(i % 28) + 1:02d}/09/2026", "particulars": f"Service {i} — drafting and correspondence",
             "kind": "fee", "amount": 110} for i in range(45)]
    pdf = _build(dict(PAYLOAD, rows=rows))
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    assert doc.page_count > 1
    bottom = L.PAGE_H - L.DOC_FRAME_Y
    chrome = (L.FIRM_NAME, L.FIRM_ACN, "Doc ID:", "Issued:", "Verify:", "Page ", "WINZOY LEGAL",
              "Solicitors", "PO Box", "M: 0424")
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span["text"].strip() and not any(c in span["text"] for c in chrome):
                        assert span["bbox"][3] <= bottom + 0.5, span["text"]
    for page in doc:
        if "Service 44" in page.get_text() or "Service 1 " in page.get_text():
            assert "Particulars of each service" in page.get_text()
    doc.close()


def test_markup_in_client_data_is_escaped():
    text = _flat(_texts(_build(dict(PAYLOAD, client_name="Smith <&> Co", rows=[
        {"particulars": "Advice re s501 <cancellation>", "kind": "fee", "amount": 1}])))[0])
    assert "Smith <&> Co" in text
    assert "Advice re s501 <cancellation>" in text


# ---------------------------------------------------------------- HTTP route
@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import importlib
    import os

    os.environ["UPLOADS_DIR"] = str(tmp_path_factory.mktemp("sos_cache"))
    try:
        if "app" in sys.modules:
            importlib.reload(sys.modules["app"])
        else:
            import app  # type: ignore  # noqa: F401
        yield sys.modules["app"].app.test_client()
    finally:
        del os.environ["UPLOADS_DIR"]


def test_route_returns_pdf_and_health_lists_type(client):
    res = client.post("/cost-agreements/statement_of_services/fill", json=PAYLOAD)
    assert res.status_code == 200
    assert res.data[:4] == b"%PDF"
    assert "statement_of_services" in client.get("/health").get_json()["cost_agreement_types"]


def test_route_validation_error(client):
    res = client.post("/cost-agreements/statement_of_services/fill", json={})
    assert res.status_code == 400
    assert res.get_json()["errors"]
