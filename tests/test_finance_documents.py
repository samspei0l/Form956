"""Tests for the finance-document builders (invoice + receipt).

Kept separate from tests/test_cost_agreements.py because the two document
families differ in the things those tests assert: finance documents are
never signed, so they carry no SIGMETA metadata and reserve no post-signing
stamp band -- their content frame runs down to ``layout.DOC_FRAME_Y``, not
``layout.FRAME_Y``.

The regression this file exists for is the one the finance UI reported: the
header's "Issued:" line printed the render timestamp, so regenerating an
invoice months later put today's date at the top of a page whose "Issue
Date" field still showed the real one. ``test_*_header_issued_date_matches_
the_issue_date`` asserts they agree, on every page.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from costagreements import layout as L  # noqa: E402
from costagreements.builders.invoice import (  # noqa: E402
    InvoiceData,
    apply_invoice_normalisations,
    build_invoice,
    validate_invoice,
)
from costagreements.builders.receipt import (  # noqa: E402
    ReceiptData,
    apply_receipt_normalisations,
    build_receipt,
    validate_receipt,
)

INVOICE_MINIMAL_PAYLOAD = {
    "invoice_number": "INV-0042",
    "issue_date": "09/09/2026",
    "due_date": "23/09/2026",
    "case_reference": "WZL-1234",
    "client_name": "John Citizen",
    "bill_to_address": "12 Sample St, Sampleville NSW 2000",
    "bill_to_phone": "0400 000 000",
    "bill_to_email": "john@example.com",
    "line_items": [
        {"description": "Professional Fees", "quantity": 1, "unit_price": 4400, "amount": 4400},
        {"description": "Skills assessment lodgement", "quantity": 2, "unit_price": 550, "amount": 1100},
    ],
    "subtotal": 5000.0,
    "gst_rate_percent": 10,
    "gst_amount": 500.0,
    "total": 5500.0,
    "notes": "Payment due within 14 days.",
}

RECEIPT_MINIMAL_PAYLOAD = {
    "receipt_number": "RCT-0007",
    "issue_date": "10/09/2026",
    "invoice_number": "INV-0042",
    "case_reference": "WZL-1234",
    "client_name": "John Citizen",
    "bill_to_address": "12 Sample St, Sampleville NSW 2000",
    "amount_paid": 2000,
    "payment_method": "bank_transfer",
    "reference": "TX-99881",
    "invoice_total": 5500,
    "previously_paid": 1000,
    "total_paid_to_date": 3000,
    "balance_remaining": 2500,
}


# --------------------------------------------------------------------- helpers
def _chrome_snippets() -> tuple[str, ...]:
    """Header/footer strings, excluded from body-overflow checks -- the
    chrome is *meant* to sit outside the content frame."""
    return (
        L.FIRM_NAME, L.FIRM_ACN, "Doc ID:", "Issued:", "Verify:", "Page ",
        "WINZOY LEGAL", "Solicitors", "PO Box", "M: 0424",
    )


def _assert_no_body_overflow(pdf_bytes: bytes) -> dict[int, str]:
    """Fails if any non-chrome text sits below the finance frame's bottom
    boundary on any page. Returns {page_index: full_text}."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    frame_bottom_topdown = L.PAGE_H - L.DOC_FRAME_Y
    chrome = _chrome_snippets()
    page_text: dict[int, str] = {}
    for i, page in enumerate(doc):
        page_text[i] = page.get_text()
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"]
                    if not text.strip() or any(s in text for s in chrome):
                        continue
                    y1 = span["bbox"][3]
                    assert y1 <= frame_bottom_topdown + 0.5, (
                        f"page {i + 1}: body text {text!r} extends to y={y1:.1f}, "
                        f"past the frame boundary at {frame_bottom_topdown:.1f}"
                    )
    doc.close()
    return page_text


def _page_texts(pdf_bytes: bytes) -> list[str]:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    texts = [page.get_text() for page in doc]
    doc.close()
    return texts


def _build_invoice(payload: dict) -> bytes:
    return build_invoice(InvoiceData.from_payload(apply_invoice_normalisations(payload)))


def _build_receipt(payload: dict) -> bytes:
    return build_receipt(ReceiptData.from_payload(apply_receipt_normalisations(payload)))


# --------------------------------------------------------------------- validate
@pytest.mark.parametrize(
    "validate_fn, payload",
    [
        (validate_invoice, INVOICE_MINIMAL_PAYLOAD),
        (validate_receipt, RECEIPT_MINIMAL_PAYLOAD),
    ],
)
def test_minimal_payload_is_valid(validate_fn, payload):
    assert validate_fn(payload) == []


@pytest.mark.parametrize("validate_fn", [validate_invoice, validate_receipt])
def test_missing_required_fields_reported(validate_fn):
    errs = validate_fn({})
    assert errs
    assert all(e.code == "required" for e in errs)


def test_invoice_rejects_an_impossible_date():
    errs = validate_invoice(dict(INVOICE_MINIMAL_PAYLOAD, issue_date="31/02/2026"))
    assert [e.field for e in errs] == ["issue_date"]
    assert errs[0].code == "value"


def test_invoice_rejects_empty_line_items():
    errs = validate_invoice(dict(INVOICE_MINIMAL_PAYLOAD, line_items=[]))
    assert [e.field for e in errs] == ["line_items"]


def test_invoice_rejects_a_line_item_with_no_description():
    errs = validate_invoice(dict(
        INVOICE_MINIMAL_PAYLOAD,
        line_items=[{"description": "  ", "quantity": 1, "unit_price": 10, "amount": 10}],
    ))
    assert [e.field for e in errs] == ["line_items[0].description"]


def test_receipt_rejects_a_zero_payment():
    """A receipt evidences money actually received -- a $0.00 one is a
    data-entry slip, not a document anyone should be handed."""
    errs = validate_receipt(dict(RECEIPT_MINIMAL_PAYLOAD, amount_paid=0))
    assert [e.field for e in errs] == ["amount_paid"]


def test_negative_amounts_rejected():
    errs = validate_invoice(dict(INVOICE_MINIMAL_PAYLOAD, total=-1))
    assert [e.field for e in errs] == ["total"]


def test_iso_dates_normalise_to_au_format():
    out = apply_invoice_normalisations(dict(
        INVOICE_MINIMAL_PAYLOAD, issue_date="2026-09-09", due_date="2026-09-23",
    ))
    assert out["issue_date"] == "09/09/2026"
    assert out["due_date"] == "23/09/2026"


# --------------------------------------------------------------------- build
def test_invoice_builds_a_one_page_pdf_with_every_block():
    pdf_bytes = _build_invoice(INVOICE_MINIMAL_PAYLOAD)
    pages = _page_texts(pdf_bytes)
    assert len(pages) == 1
    text = pages[0]
    for expected in (
        "TAX INVOICE", "BILL TO", "INVOICE DETAILS", "INV-0042", "09/09/2026",
        "23/09/2026", "WZL-1234", "John Citizen", "Professional Fees",
        "$4,400.00", "$1,100.00", "Subtotal", "$5,000.00", "GST (10%)",
        "$500.00", "Total Due", "$5,500.00 AUD", "NOTES", "HOW TO PAY",
        L.BANK_BSB, L.BANK_ACCOUNT_NO,
    ):
        assert expected in text, f"{expected!r} missing from the invoice"
    _assert_no_body_overflow(pdf_bytes)


def test_receipt_builds_a_one_page_pdf_with_every_block():
    pdf_bytes = _build_receipt(RECEIPT_MINIMAL_PAYLOAD)
    pages = _page_texts(pdf_bytes)
    assert len(pages) == 1
    text = pages[0]
    for expected in (
        "PAYMENT RECEIPT", "BILL TO", "RECEIPT DETAILS", "RCT-0007", "10/09/2026",
        "Paid Toward Invoice", "INV-0042", "Invoice Total", "$5,500.00",
        "Previously Paid", "$1,000.00", "This Payment", "$2,000.00",
        "Paid to Date", "$3,000.00 AUD", "Balance Remaining", "$2,500.00 AUD",
        "PAYMENT DETAILS", "Bank Transfer", "TX-99881",
        "not a tax invoice",
    ):
        assert expected in text, f"{expected!r} missing from the receipt"
    _assert_no_body_overflow(pdf_bytes)


def test_receipt_omits_previously_paid_on_a_first_payment():
    """A $0.00 'Previously Paid' row reads as a missing figure rather than
    'this is the first payment'."""
    text = _page_texts(_build_receipt(dict(
        RECEIPT_MINIMAL_PAYLOAD, previously_paid=0, total_paid_to_date=2000, balance_remaining=3500,
    )))[0]
    assert "Previously Paid" not in text
    assert "This Payment" in text


def test_receipt_prints_a_credit_note_when_the_payment_overshoots():
    text = _page_texts(_build_receipt(dict(
        RECEIPT_MINIMAL_PAYLOAD, amount_paid=5000, total_paid_to_date=6000,
        balance_remaining=0, credit_amount=500,
    )))[0]
    assert "credit toward future invoices" in text
    assert "$500.00" in text


# --------------------------------------- the reported bug: header vs body date
@pytest.mark.parametrize(
    "build_fn, payload, issue_date",
    [
        (_build_invoice, INVOICE_MINIMAL_PAYLOAD, "09/09/2026"),
        (_build_receipt, RECEIPT_MINIMAL_PAYLOAD, "10/09/2026"),
    ],
)
def test_header_issued_date_matches_the_issue_date(build_fn, payload, issue_date):
    """The header's "Issued:" line (under the Doc ID) and the footer's
    "Verify:" stamp both print the document's own issue date -- not the
    moment the PDF happened to be rendered."""
    for text in _page_texts(build_fn(payload)):
        assert f"Issued: {issue_date}" in text
        assert f"• {issue_date}" in text  # footer "Verify: <doc id> • <date>"


@pytest.mark.parametrize(
    "build_fn, payload",
    [(_build_invoice, INVOICE_MINIMAL_PAYLOAD), (_build_receipt, RECEIPT_MINIMAL_PAYLOAD)],
)
def test_doc_id_is_stable_across_rebuilds(build_fn, payload):
    """Finance documents are numbered records: regenerating INV-0042's PDF
    must reprint the same Doc ID, or the footer's "Verify:" stamp means
    nothing. (The cost agreements deliberately seed theirs from the clock.)"""
    first = _page_texts(build_fn(payload))[0]
    second = _page_texts(build_fn(payload))[0]

    def doc_id_of(text: str) -> str:
        line = next(ln for ln in text.splitlines() if ln.startswith("Doc ID:"))
        return line.split("Doc ID:", 1)[1].strip()

    assert doc_id_of(first) == doc_id_of(second)
    assert doc_id_of(first).startswith("WZL-")


def test_a_different_invoice_number_gets_a_different_doc_id():
    a = _page_texts(_build_invoice(INVOICE_MINIMAL_PAYLOAD))[0]
    b = _page_texts(_build_invoice(dict(INVOICE_MINIMAL_PAYLOAD, invoice_number="INV-0043")))[0]
    assert a.split("Doc ID:")[1][:20] != b.split("Doc ID:")[1][:20]


# --------------------------------------------------------------------- totals
def test_totals_derived_when_the_caller_omits_them():
    """The finance UI computes GST out of the (GST-inclusive) line-item sum
    and sends all three figures. A direct API caller that sends none should
    get the same arithmetic, not a $0.00 invoice."""
    payload = {k: v for k, v in INVOICE_MINIMAL_PAYLOAD.items()
               if k not in ("subtotal", "gst_amount", "total")}
    data = InvoiceData.from_payload(payload)
    assert data.total == pytest.approx(5500.0)
    assert data.gst_amount == pytest.approx(500.0)
    assert data.subtotal == pytest.approx(5000.0)


def test_receipt_running_totals_derived_when_omitted():
    payload = {k: v for k, v in RECEIPT_MINIMAL_PAYLOAD.items()
               if k not in ("total_paid_to_date", "balance_remaining")}
    data = ReceiptData.from_payload(payload)
    assert data.total_paid_to_date == pytest.approx(3000.0)
    assert data.balance_remaining == pytest.approx(2500.0)
    assert data.credit_amount == pytest.approx(0.0)


def test_no_gst_invoice_drops_the_gst_row_and_the_tax_invoice_label():
    """The finance UI's "No GST" mode sends a zero rate and a zero amount.
    That document isn't a tax invoice, so it must not carry the label -- and
    its ledger shows the plain amount, not a "GST (0%)  $0.00" row."""
    text = _page_texts(_build_invoice(dict(
        INVOICE_MINIMAL_PAYLOAD, gst_rate_percent=0, gst_amount=0, subtotal=5500.0,
    )))[0]
    assert "GST" not in text
    assert "TAX INVOICE" not in text
    assert "INVOICE" in text
    assert "Subtotal" in text
    assert "$5,500.00 AUD" in text


def test_whole_quantities_print_without_a_decimal_tail():
    text = _page_texts(_build_invoice(INVOICE_MINIMAL_PAYLOAD))[0]
    assert "1.0" not in text
    assert "2.0" not in text


# --------------------------------------------------------------------- paging
def test_a_long_invoice_paginates_and_repeats_the_ledger_header():
    """40 line items force a page break mid-ledger. ReportLab decides where;
    the assertion is on the outcome -- every page carries the column header,
    and nothing spills into the footer."""
    payload = dict(INVOICE_MINIMAL_PAYLOAD, line_items=[
        {"description": f"Item {i} — disbursement and associated correspondence",
         "quantity": 1, "unit_price": 110, "amount": 110}
        for i in range(40)
    ])
    pdf_bytes = _build_invoice(payload)
    pages = _page_texts(pdf_bytes)
    assert len(pages) > 1
    ledger_pages = [t for t in pages if "Item 1 " in t or "Item 39" in t]
    for text in ledger_pages:
        assert "Unit Price" in text, "ledger header not repeated on a continuation page"
    _assert_no_body_overflow(pdf_bytes)


def test_markup_characters_in_client_data_are_escaped_not_rendered():
    """Client-supplied strings flow into ReportLab's Paragraph mini-markup;
    a stray '<' must not be read as a tag (or swallow the rest of the line)."""
    text = _page_texts(_build_invoice(dict(
        INVOICE_MINIMAL_PAYLOAD,
        client_name="Smith <&> Co",
        line_items=[{"description": "Advice re s501 <cancellation>", "quantity": 1,
                     "unit_price": 100, "amount": 100}],
    )))[0]
    assert "Smith <&> Co" in text
    assert "Advice re s501 <cancellation>" in text


# ---------------------------------------------------------------- HTTP route
# See tests/test_cost_agreements.py for why UPLOADS_DIR is set directly
# inside this module-scoped fixture rather than via monkeypatch.
@pytest.fixture(scope="module")
def app(tmp_path_factory):
    import importlib
    import os

    os.environ["UPLOADS_DIR"] = str(tmp_path_factory.mktemp("finance_docs_cache"))
    try:
        if "app" in sys.modules:
            importlib.reload(sys.modules["app"])
        else:
            import app  # type: ignore  # noqa: F401
        yield sys.modules["app"]
    finally:
        del os.environ["UPLOADS_DIR"]


@pytest.fixture()
def client(app):
    return app.app.test_client()


@pytest.mark.parametrize(
    "doc_type, payload",
    [("invoice", INVOICE_MINIMAL_PAYLOAD), ("receipt", RECEIPT_MINIMAL_PAYLOAD)],
)
def test_fill_route_returns_pdf(client, doc_type, payload):
    res = client.post(f"/cost-agreements/{doc_type}/fill", json=payload)
    assert res.status_code == 200
    assert res.headers["X-Cache"] == "miss"
    doc = pymupdf.open(stream=res.data, filetype="pdf")
    assert doc.page_count == 1
    doc.close()


@pytest.mark.parametrize("doc_type", ["invoice", "receipt"])
def test_fill_route_validation_error(client, doc_type):
    res = client.post(f"/cost-agreements/{doc_type}/fill", json={})
    assert res.status_code == 400
    body = res.get_json()
    assert body["errors"]
    assert all({"field", "code", "message"} <= e.keys() for e in body["errors"])


def test_iso_and_au_dates_hit_the_same_cache_entry(client):
    """Normalisation runs before the cache key is computed, so the same
    invoice sent with an ISO issue date and with a DD/MM/YYYY one is one
    cache entry, not two."""
    au = dict(INVOICE_MINIMAL_PAYLOAD, invoice_number="INV-CACHE-TEST",
               issue_date="09/09/2026", due_date="23/09/2026")
    iso = dict(au, issue_date="2026-09-09", due_date="2026-09-23")
    first = client.post("/cost-agreements/invoice/fill", json=au)
    second = client.post("/cost-agreements/invoice/fill", json=iso)
    assert first.headers["X-Cache"] == "miss"
    assert second.headers["X-Cache"] == "hit"
    assert first.headers["X-Cache-Key"] == second.headers["X-Cache-Key"]


def test_invoice_and_receipt_payloads_do_not_collide_in_the_cache(client):
    """cache_key() has no per-type salt of its own; the route adds one. Two
    documents of different types must never share an entry."""
    shared = {
        "issue_date": "09/09/2026", "client_name": "Same Payload Pty Ltd",
        "invoice_number": "INV-COLLIDE", "case_reference": "WZL-COLLIDE",
        "amount_paid": 100, "invoice_total": 100, "gst_rate_percent": 10,
        "line_items": [{"description": "Fee", "quantity": 1, "unit_price": 100, "amount": 100}],
    }
    inv = client.post("/cost-agreements/invoice/fill", json=shared)
    rct = client.post("/cost-agreements/receipt/fill", json=shared)
    assert inv.status_code == rct.status_code == 200
    assert inv.headers["X-Cache-Key"] != rct.headers["X-Cache-Key"]
    assert "TAX INVOICE" in pymupdf.open(stream=inv.data, filetype="pdf")[0].get_text()
    assert "PAYMENT RECEIPT" in pymupdf.open(stream=rct.data, filetype="pdf")[0].get_text()


def test_health_lists_the_finance_document_types(client):
    types = client.get("/health").get_json()["cost_agreement_types"]
    assert "invoice" in types
    assert "receipt" in types
