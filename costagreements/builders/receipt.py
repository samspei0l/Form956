"""Builds the Payment Receipt PDF.

Transcribed from winzoylegal_new's
``src/features/receipt/buildReceiptPdf.ts`` (+ its ``types.ts``). It mirrors
the invoice's card family on purpose -- "Bill To" / "Receipt Details" side
by side, the same soft-gold headline field -- so a receipt and an invoice
for the same client read as one document family.

The same two deliberate differences from the pdf-lib original apply here as
on the invoice (see builders/invoice.py's docstring): the header's
"Issued:" line is the receipt's own issue date, and the Doc ID is
deterministic. Like the invoice, this document is never signed, so it
carries no SIGMETA metadata and reserves no post-signing stamp band.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Spacer

from .. import chrome
from .. import finance as F
from .. import layout as L
from ..components import P, bind_headings, multiline_html
from ..money import parse_amt
from ..validate_finance import apply_finance_normalisations, validate_finance_document

BADGE_LABEL = "PAYMENT RECEIPT"

# Printed at the foot of every receipt: a receipt evidences payment received,
# it is not itself a tax invoice. Verbatim from the pdf-lib original.
CLOSING_NOTE = "This receipt confirms payment received and is not a tax invoice."


@dataclass
class ReceiptData:
    issue_date: str  # DD/MM/YYYY (or YYYY-MM-DD, normalised before this is built)
    client_name: str
    invoice_number: str = ""
    receipt_number: str = ""
    case_reference: str = ""

    bill_to_company_name: str = ""
    bill_to_address: str = ""
    bill_to_phone: str = ""
    bill_to_email: str = ""

    amount_paid: float = 0.0
    payment_method: str = ""
    reference: str = ""
    notes: str = ""

    invoice_total: float = 0.0
    previously_paid: float = 0.0
    total_paid_to_date: float = 0.0
    balance_remaining: float = 0.0
    credit_amount: float = 0.0

    @classmethod
    def from_payload(cls, payload: dict) -> "ReceiptData":
        amount_paid = parse_amt(payload.get("amount_paid"))
        invoice_total = parse_amt(payload.get("invoice_total"))
        previously_paid = parse_amt(payload.get("previously_paid"))

        # The finance UI sends the running totals (it has the full
        # transaction history for the invoice); a caller that doesn't gets
        # them derived from this payment, rather than a receipt claiming
        # $0.00 paid to date against a real payment.
        if payload.get("total_paid_to_date") is None:
            total_paid_to_date = previously_paid + amount_paid
        else:
            total_paid_to_date = parse_amt(payload.get("total_paid_to_date"))

        if payload.get("balance_remaining") is None:
            balance_remaining = max(invoice_total - total_paid_to_date, 0.0)
        else:
            balance_remaining = parse_amt(payload.get("balance_remaining"))

        if payload.get("credit_amount") is None:
            credit_amount = max(total_paid_to_date - invoice_total, 0.0)
        else:
            credit_amount = parse_amt(payload.get("credit_amount"))

        return cls(
            issue_date=str(payload.get("issue_date") or ""),
            client_name=str(payload.get("client_name") or ""),
            invoice_number=str(payload.get("invoice_number") or ""),
            receipt_number=str(payload.get("receipt_number") or ""),
            case_reference=str(payload.get("case_reference") or ""),
            bill_to_company_name=str(payload.get("bill_to_company_name") or ""),
            bill_to_address=str(payload.get("bill_to_address") or ""),
            bill_to_phone=str(payload.get("bill_to_phone") or ""),
            bill_to_email=str(payload.get("bill_to_email") or ""),
            amount_paid=amount_paid,
            payment_method=str(payload.get("payment_method") or ""),
            reference=str(payload.get("reference") or ""),
            notes=str(payload.get("notes") or ""),
            invoice_total=invoice_total,
            previously_paid=previously_paid,
            total_paid_to_date=total_paid_to_date,
            balance_remaining=balance_remaining,
            credit_amount=credit_amount,
        )


# --------------------------------------------------------------------- validate
def validate_receipt(payload: dict) -> list:
    """Validate a receipt payload. Empty list = valid."""
    return validate_finance_document(
        payload,
        required=("issue_date", "client_name", "invoice_number"),
        date_fields=("issue_date",),
        money_fields=(
            "amount_paid", "invoice_total", "previously_paid",
            "total_paid_to_date", "balance_remaining", "credit_amount",
        ),
        positive_fields=("amount_paid",),
    )


def apply_receipt_normalisations(payload: dict) -> dict:
    return apply_finance_normalisations(payload, ("issue_date",))


# --------------------------------------------------------------------- build
def build_receipt(data: ReceiptData) -> bytes:
    doc_id = F.stable_doc_id("RCT", [data.receipt_number, data.invoice_number, data.issue_date])
    generated_at = data.issue_date

    story = _build_story(data)

    buf = io.BytesIO()
    frame = Frame(
        L.ML, L.DOC_FRAME_Y, L.CONTENT_W, L.DOC_FRAME_HEIGHT, id="main",
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )

    def on_page(canvas, doc):
        chrome.draw_watermark(canvas, doc)
        chrome.draw_header(canvas, doc, doc_id, generated_at,
                            badge_label=BADGE_LABEL, badge_every_page=True)

    template = PageTemplate(id="main", frames=[frame], onPage=on_page)
    doc = BaseDocTemplate(
        buf, pagesize=(L.PAGE_W, L.PAGE_H), pageTemplates=[template],
        title=f"Payment Receipt {data.receipt_number}".strip(), author=L.FIRM_NAME,
        topMargin=0, bottomMargin=0, leftMargin=0, rightMargin=0,
    )
    bind_headings(story)
    doc.build(story, canvasmaker=chrome.make_canvas_factory(doc_id, generated_at))
    return buf.getvalue()


def _build_story(data: ReceiptData) -> list:
    meta_rows = [
        ("Receipt No", data.receipt_number or "DRAFT"),
        ("Issue Date", data.issue_date),
        ("Paid Toward Invoice", data.invoice_number),
        ("Case Reference", data.case_reference),
    ]

    story: list = [*F.doc_title("PAYMENT RECEIPT")]
    story.append(F.panel_row(
        "Bill To",
        F.bill_to_flows(data.client_name, data.bill_to_company_name, data.bill_to_address,
                         data.bill_to_phone, data.bill_to_email),
        "Receipt Details",
        F.meta_flows(meta_rows),
    ))
    story.append(Spacer(1, 22))

    # "Previously Paid" is omitted when this is the first payment against
    # the invoice -- a $0.00 row there reads as a missing figure.
    ledger_rows = [("Invoice Total", F.money(data.invoice_total))]
    if data.previously_paid > 0:
        ledger_rows.append(("Previously Paid", F.money(data.previously_paid)))
    ledger_rows.append(("This Payment", F.money(data.amount_paid)))

    story.append(F.totals_card(ledger_rows, "Paid to Date", F.money(data.total_paid_to_date, " AUD")))
    story.append(Spacer(1, 14))

    credit_note = (
        f"Includes a {F.money(data.credit_amount)} credit toward future invoices."
        if data.credit_amount > 0 else ""
    )
    story.append(F.balance_line("Balance Remaining", F.money(data.balance_remaining, " AUD"), credit_note))
    story.append(Spacer(1, 24))

    payment_fields = [
        ("Payment Method", F.humanize_method(data.payment_method) or "—"),
        ("Reference", data.reference or "—"),
    ]
    payment_flows: list = [F.field_grid(payment_fields, L.CONTENT_W - L.PANEL_PAD * 2)]
    if data.notes and data.notes.strip():
        payment_flows.append(P(multiline_html(data.notes), L.STYLE_NOTE_ITALIC))
    story.append(F.panel("Payment Details", payment_flows))
    story.append(Spacer(1, 14))

    story.append(F.closing_note(CLOSING_NOTE))
    return story
