"""Builds the Tax Invoice PDF.

Transcribed from winzoylegal_new's
``src/features/invoice/buildInvoicePdf.ts`` (+ its ``types.ts``) -- same
content, same card family, same navy/gold accents; only the layout
mechanism changed (ReportLab Platypus flowables in place of pdf-lib's
manual y-coordinate bookkeeping -- see costagreements/finance.py).

Two behavioural differences from the pdf-lib original, both deliberate:

1. **The header's "Issued:" line is the invoice's own issue date**, not the
   moment the PDF was rendered. On the original, regenerating an invoice
   months later reprinted today's timestamp at the top while the "Issue
   Date" field in the body still showed the real one -- two different dates
   on the same page. ``generated_at`` is the issue date here, so the header,
   the footer's "Verify:" stamp and the Invoice Details card always agree.
2. **The Doc ID is deterministic** (see ``finance.stable_doc_id``): the same
   invoice number always prints the same Doc ID, however many times its PDF
   is rebuilt.

Unlike the cost agreements this document is never signed, so it embeds no
SIGMETA metadata and reserves no post-signing stamp band -- its frame runs
down to just above the footer (``layout.DOC_FRAME_Y``).
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Spacer

from .. import chrome
from .. import finance as F
from .. import layout as L
from ..components import bind_headings
from ..money import fmt_amt, parse_amt
from ..validate_finance import (
    apply_finance_normalisations,
    validate_finance_document,
)

BADGE_LABEL = "TAX INVOICE"


@dataclass
class InvoiceLineItem:
    description: str
    quantity: float
    unit_price: float
    amount: float

    @classmethod
    def from_payload(cls, raw: dict) -> "InvoiceLineItem":
        quantity = parse_amt(raw.get("quantity")) or 1.0
        unit_price = parse_amt(raw.get("unit_price"))
        amount = raw.get("amount")
        return cls(
            description=str(raw.get("description") or ""),
            quantity=quantity,
            unit_price=unit_price,
            # A caller that omits the extended amount gets it computed
            # rather than a zero row.
            amount=parse_amt(amount) if amount is not None else quantity * unit_price,
        )


@dataclass
class InvoiceData:
    issue_date: str  # DD/MM/YYYY (or YYYY-MM-DD, normalised before this is built)
    client_name: str
    line_items: list[InvoiceLineItem] = field(default_factory=list)

    invoice_number: str = ""
    due_date: str = ""
    case_reference: str = ""

    # Sponsorship visas (482/186) bill the sponsoring company, not just the
    # applicant; the rest of the Bill To block comes from the case record.
    bill_to_company_name: str = ""
    bill_to_address: str = ""
    bill_to_phone: str = ""
    bill_to_email: str = ""

    subtotal: float = 0.0
    gst_rate_percent: float = 0.0
    gst_amount: float = 0.0
    total: float = 0.0

    notes: str = ""

    @classmethod
    def from_payload(cls, payload: dict) -> "InvoiceData":
        raw_items = payload.get("line_items") or []
        line_items = [InvoiceLineItem.from_payload(i) for i in raw_items if isinstance(i, dict)]
        gst_rate = parse_amt(payload.get("gst_rate_percent"))

        # The finance UI enters unit prices GST-*inclusive*, so the line-item
        # sum IS the total and GST is extracted out of it, never added on
        # top. It sends the three computed figures; a caller that doesn't
        # (e.g. a direct API user) gets them derived the same way rather than
        # an invoice reading $0.00.
        if payload.get("total") is None:
            total = sum(i.amount for i in line_items)
            gst_amount = total * (gst_rate / (100 + gst_rate)) if gst_rate else 0.0
            subtotal = total - gst_amount
        else:
            total = parse_amt(payload.get("total"))
            gst_amount = parse_amt(payload.get("gst_amount"))
            subtotal = parse_amt(payload.get("subtotal"))

        return cls(
            issue_date=str(payload.get("issue_date") or ""),
            client_name=str(payload.get("client_name") or ""),
            line_items=line_items,
            invoice_number=str(payload.get("invoice_number") or ""),
            due_date=str(payload.get("due_date") or ""),
            case_reference=str(payload.get("case_reference") or ""),
            bill_to_company_name=str(payload.get("bill_to_company_name") or ""),
            bill_to_address=str(payload.get("bill_to_address") or ""),
            bill_to_phone=str(payload.get("bill_to_phone") or ""),
            bill_to_email=str(payload.get("bill_to_email") or ""),
            subtotal=subtotal,
            gst_rate_percent=gst_rate,
            gst_amount=gst_amount,
            total=total,
            notes=str(payload.get("notes") or ""),
        )


# --------------------------------------------------------------------- validate
def validate_invoice(payload: dict) -> list:
    """Validate an invoice payload. Empty list = valid."""
    return validate_finance_document(
        payload,
        required=("issue_date", "client_name"),
        date_fields=("issue_date", "due_date"),
        money_fields=("subtotal", "gst_amount", "total", "gst_rate_percent"),
        require_line_items=True,
    )


def apply_invoice_normalisations(payload: dict) -> dict:
    return apply_finance_normalisations(payload, ("issue_date", "due_date"))


# --------------------------------------------------------------------- build
def build_invoice(data: InvoiceData) -> bytes:
    doc_id = F.stable_doc_id("INV", [data.invoice_number, data.case_reference, data.issue_date])
    # See module docstring: the header's "Issued:" line is the invoice's own
    # issue date, so it can never disagree with the Invoice Details card.
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
        title=f"Tax Invoice {data.invoice_number}".strip(), author=L.FIRM_NAME,
        topMargin=0, bottomMargin=0, leftMargin=0, rightMargin=0,
    )
    bind_headings(story)
    doc.build(story, canvasmaker=chrome.make_canvas_factory(doc_id, generated_at))
    return buf.getvalue()


def _build_story(data: InvoiceData) -> list:
    meta_rows = [
        ("Invoice No", data.invoice_number or "DRAFT"),
        ("Issue Date", data.issue_date),
    ]
    if data.due_date:
        meta_rows.append(("Due Date", data.due_date))
    meta_rows.append(("Case Reference", data.case_reference))

    story: list = [*F.doc_title("TAX INVOICE")]
    story.append(F.panel_row(
        "Bill To",
        F.bill_to_flows(data.client_name, data.bill_to_company_name, data.bill_to_address,
                         data.bill_to_phone, data.bill_to_email),
        "Invoice Details",
        F.meta_flows(meta_rows),
    ))
    story.append(Spacer(1, 22))

    story.append(F.line_items_table(data.line_items))
    story.append(Spacer(1, 20))

    story.append(F.totals_card(
        [
            ("Subtotal", F.money(data.subtotal)),
            (f"GST ({fmt_gst_rate(data.gst_rate_percent)}%)", F.money(data.gst_amount)),
        ],
        "Total Due", F.money(data.total, " AUD"),
    ))
    story.append(Spacer(1, 20))

    notes = F.notes_panel(data.notes)
    if notes is not None:
        story.append(notes)
        story.append(Spacer(1, 16))

    story.append(F.how_to_pay_panel())
    return story


def fmt_gst_rate(rate: float) -> str:
    """'10' rather than '10.00' for whole rates -- the label reads
    'GST (10%)'."""
    return str(int(rate)) if float(rate) == int(rate) else fmt_amt(rate)
