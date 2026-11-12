"""Builds the Statement of Services PDF.

Built from the firm's Word template ("Winzoy- Statement of Services-
Template.docx"): a per-matter record of the services *actually performed*
and what was charged for each, reconciled against payments received. It is
explicitly not an invoice -- the closing lines say so -- so it is badged
"STATEMENT OF SERVICES", never "TAX INVOICE", and carries no How-to-Pay
panel.

It joins the invoice and receipt as the third finance document, and follows
the same three deliberate rules they do (see builders/invoice.py):

- never signed: no SIGMETA, no stamp band, frame runs to ``DOC_FRAME_Y``;
- the header's "Issued:" line is the statement's own issue date;
- the Doc ID is deterministic (``finance.stable_doc_id``).

Two rules of its own:

1. **Every total is recomputed here, from the rows.** The caller may send
   its own figures (the finance UI shows a live summary), but they are
   ignored: a statement whose account summary doesn't add up is worse than
   none. Rows are GST-inclusive, like invoice line items, and GST is
   extracted out of the professional-fee rows only -- a disbursement (the
   Department's visa application charge, a skills-assessment fee paid on
   the client's behalf) carries no GST of the firm's.
2. **The closing wording is a constant**, never accepted from the caller --
   the same rule builders/declaration.py follows for legal wording.

The balance is::

    total charges - payments received + refunds made - credits applied

A refund is money the firm paid back, so it adds back to what is owed; a
credit applied (a fee waiver, a credit note) reduces it. The template's
single "Refunds / credits applied" row is split into those two rows because
they move the balance in opposite directions.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, PageTemplate, Spacer, Table, TableStyle

from pdfform.validate import DATE_YYYYMMDD, ValidationError, normalise_date

from .. import chrome
from .. import finance as F
from .. import layout as L
from ..components import P, PT, bind_headings, esc, multiline_html
from ..money import parse_amt
from ..validate_finance import apply_finance_normalisations, validate_finance_document

BADGE_LABEL = "STATEMENT OF SERVICES"

# Verbatim from the firm's template. Never accepted from a caller.
CLOSING_LINES = (
    "This statement records services actually performed and the charges for those services. "
    "It is not an additional invoice or request for duplicate payment.",
    "Please notify us if further information is required.",
)

ROW_KINDS = ("fee", "disbursement")
_DATE_FIELDS = ("issue_date", "agreement_date", "period_from", "period_to")


def _r2(v: float) -> float:
    return round(v + 0.0, 2)


@dataclass
class StatementRow:
    date: str  # free text: a date (DD/MM/YYYY) or a stage name
    particulars: str
    kind: str  # 'fee' | 'disbursement'
    amount: float

    @classmethod
    def from_payload(cls, raw: dict) -> "StatementRow":
        kind = str(raw.get("kind") or "fee")
        return cls(
            date=str(raw.get("date") or ""),
            particulars=str(raw.get("particulars") or ""),
            kind=kind if kind in ROW_KINDS else "fee",
            amount=parse_amt(raw.get("amount")),
        )


@dataclass
class DocumentRef:
    number: str
    date: str = ""

    @classmethod
    def from_payload(cls, raw) -> "DocumentRef":
        if isinstance(raw, dict):
            return cls(number=str(raw.get("number") or ""), date=str(raw.get("date") or ""))
        return cls(number=str(raw or ""))

    def label(self) -> str:
        return f"{self.number} ({self.date})" if self.date else self.number


@dataclass
class StatementOfServicesData:
    issue_date: str
    client_name: str
    statement_number: str = ""
    matter: str = ""
    case_reference: str = ""
    agreement_date: str = ""
    period_from: str = ""
    period_to: str = ""

    rows: list[StatementRow] = field(default_factory=list)
    disbursement_notes: str = ""
    gst_rate_percent: float = 0.0

    payments_received: float = 0.0
    refunds: float = 0.0
    credits: float = 0.0

    invoice_refs: list[DocumentRef] = field(default_factory=list)
    receipt_refs: list[DocumentRef] = field(default_factory=list)

    # ---- computed (never trusted from the payload) ----
    @property
    def fee_rows(self) -> list[StatementRow]:
        return [r for r in self.rows if r.kind == "fee"]

    @property
    def disbursement_rows(self) -> list[StatementRow]:
        return [r for r in self.rows if r.kind == "disbursement"]

    @property
    def fees_incl_gst(self) -> float:
        return _r2(sum(r.amount for r in self.fee_rows))

    @property
    def gst_amount(self) -> float:
        rate = self.gst_rate_percent
        return _r2(self.fees_incl_gst * rate / (100 + rate)) if rate > 0 else 0.0

    @property
    def fees_excl_gst(self) -> float:
        return _r2(self.fees_incl_gst - self.gst_amount)

    @property
    def disbursements(self) -> float:
        return _r2(sum(r.amount for r in self.disbursement_rows))

    @property
    def total_charges(self) -> float:
        return _r2(self.fees_incl_gst + self.disbursements)

    @property
    def balance(self) -> float:
        """Positive: owing by the client. Negative: credit in their favour."""
        return _r2(self.total_charges - self.payments_received + self.refunds - self.credits)

    @property
    def period_text(self) -> str:
        if self.period_from and self.period_to:
            return f"{self.period_from} – {self.period_to}"
        if self.period_to:
            return f"Up to {self.period_to}"
        if self.period_from:
            return f"From {self.period_from}"
        return "Whole matter to date"

    @classmethod
    def from_payload(cls, payload: dict) -> "StatementOfServicesData":
        rows = [StatementRow.from_payload(r) for r in (payload.get("rows") or []) if isinstance(r, dict)]
        return cls(
            issue_date=str(payload.get("issue_date") or ""),
            client_name=str(payload.get("client_name") or ""),
            statement_number=str(payload.get("statement_number") or ""),
            matter=str(payload.get("matter") or ""),
            case_reference=str(payload.get("case_reference") or ""),
            agreement_date=str(payload.get("agreement_date") or ""),
            period_from=str(payload.get("period_from") or ""),
            period_to=str(payload.get("period_to") or ""),
            rows=rows,
            disbursement_notes=str(payload.get("disbursement_notes") or ""),
            gst_rate_percent=parse_amt(payload.get("gst_rate_percent")),
            payments_received=parse_amt(payload.get("payments_received")),
            refunds=parse_amt(payload.get("refunds")),
            credits=parse_amt(payload.get("credits")),
            invoice_refs=[DocumentRef.from_payload(r) for r in (payload.get("invoice_refs") or [])],
            receipt_refs=[DocumentRef.from_payload(r) for r in (payload.get("receipt_refs") or [])],
        )


# --------------------------------------------------------------------- validate
def validate_statement_of_services(payload: dict) -> list:
    """Validate a statement payload. Empty list = valid."""
    errs = validate_finance_document(
        payload,
        required=("issue_date", "client_name"),
        date_fields=_DATE_FIELDS,
        money_fields=("gst_rate_percent", "payments_received", "refunds", "credits"),
    )

    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        errs.append(ValidationError(field="rows", code="required",
                                    message="'rows' must be a non-empty list"))
    else:
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(ValidationError(field=f"rows[{i}]", code="format",
                                            message=f"'rows[{i}]' must be an object"))
                continue
            if not str(row.get("particulars") or "").strip():
                errs.append(ValidationError(field=f"rows[{i}].particulars", code="required",
                                            message=f"'rows[{i}].particulars' is required"))
            kind = row.get("kind") or "fee"
            if kind not in ROW_KINDS:
                errs.append(ValidationError(
                    field=f"rows[{i}].kind", code="value",
                    message=f"'rows[{i}].kind' must be one of {', '.join(ROW_KINDS)}",
                ))
            if parse_amt(row.get("amount")) < 0:
                errs.append(ValidationError(field=f"rows[{i}].amount", code="value",
                                            message=f"'rows[{i}].amount' must not be negative"))

    if parse_amt(payload.get("gst_rate_percent")) > 100:
        errs.append(ValidationError(field="gst_rate_percent", code="value",
                                    message="'gst_rate_percent' must be between 0 and 100"))

    # A period that ends before it starts is a data-entry slip.
    pf, pt = payload.get("period_from"), payload.get("period_to")
    if not any(e.field in ("period_from", "period_to") for e in errs) and pf and pt:
        try:
            a = normalise_date(pf).split("/")
            b = normalise_date(pt).split("/")
            if (a[2], a[1], a[0]) > (b[2], b[1], b[0]):
                errs.append(ValidationError(field="period_to", code="value",
                                            message="'period_to' must not be before 'period_from'"))
        except (ValueError, IndexError, AttributeError):
            pass

    return errs


def apply_statement_of_services_normalisations(payload: dict) -> dict:
    """Dates to DD/MM/YYYY (top-level, row dates that are ISO dates, and
    document-ref dates), so ISO and AU twins hash to the same cache key, and
    caller-sent totals dropped -- they are never printed (module docstring),
    so they must not split the cache either."""
    out = apply_finance_normalisations(payload, _DATE_FIELDS)
    for k in ("fees_excl_gst", "gst_amount", "fees_incl_gst", "disbursements",
              "total_charges", "balance"):
        out.pop(k, None)

    def _norm(v):
        return normalise_date(v) if isinstance(v, str) and DATE_YYYYMMDD.match(v) else v

    if isinstance(out.get("rows"), list):
        out["rows"] = [dict(r, date=_norm(r.get("date"))) if isinstance(r, dict) else r
                       for r in out["rows"]]
    for key in ("invoice_refs", "receipt_refs"):
        if isinstance(out.get(key), list):
            out[key] = [dict(r, date=_norm(r.get("date"))) if isinstance(r, dict) else r
                        for r in out[key]]
    return out


# --------------------------------------------------------------------- build
def build_statement_of_services(data: StatementOfServicesData) -> bytes:
    doc_id = F.stable_doc_id("SOS", [data.statement_number, data.case_reference, data.issue_date])
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
        title=f"Statement of Services {data.statement_number}".strip(), author=L.FIRM_NAME,
        topMargin=0, bottomMargin=0, leftMargin=0, rightMargin=0,
    )
    bind_headings(story)
    doc.build(story, canvasmaker=chrome.make_canvas_factory(doc_id, generated_at))
    return buf.getvalue()


def _balance_parts(balance: float) -> tuple[str, str]:
    if abs(balance) < 0.005:
        return "Balance", "Nil"
    if balance > 0:
        return "Balance owing", F.money(balance, " AUD")
    return "Credit in your favour", F.money(-balance, " AUD")


def _build_story(data: StatementOfServicesData) -> list:
    story: list = [*F.doc_title("STATEMENT OF SERVICES")]
    story[-1:] = [Spacer(1, 10)]  # tighten: the agent line sits under the rule
    story.append(PT(f"Registered Migration Agent: {L.RMA_NAME}  |  MARN {L.RMA_MARN}",
                    L.STYLE_BILL_MUTED))
    story.append(Spacer(1, 12))

    # One full-width card, three to a row: two side-by-side stacked cards
    # (the invoice's arrangement) cost a fifth of the page here and pushed
    # the closing lines onto a second one.
    story.append(F.panel("Particulars", [F.field_grid([
        ("Client", data.client_name),
        ("Matter / Visa subclass", data.matter or "—"),
        ("Statement No.", data.statement_number or "DRAFT"),
        ("Date issued", data.issue_date),
        ("Service agreement date", data.agreement_date or "—"),
        ("Services covered", data.period_text),
    ], L.CONTENT_W - L.PANEL_PAD * 2, col_count=3)]))
    story.append(Spacer(1, 16))

    story.append(P("Services performed and charges", L.STYLE_H2))
    story.append(Spacer(1, 6))
    story.append(F.services_table(
        [(r.date, r.particulars, F.money(r.amount)) for r in data.fee_rows],
        empty_text="No professional services charged in this period.",
    ))
    story.append(Spacer(1, 18))

    # Account summary on the right; the disbursement particulars and the
    # related invoices/receipts in a column beside it. Stacked, the three
    # blocks pushed an ordinary statement onto a second page.
    side_w = L.CONTENT_W * 0.40
    gap = L.PANEL_GAP
    card_w = L.CONTENT_W - side_w - gap

    balance_label, balance_value = _balance_parts(data.balance)
    card = F.account_summary_card(
        [
            ("Professional fees (excl. GST)", F.money(data.fees_excl_gst)),
            ("GST on professional fees", F.money(data.gst_amount)),
            ("Professional fees (incl. GST)", F.money(data.fees_incl_gst)),
            ("Disbursements paid / incurred", F.money(data.disbursements)),
        ],
        "Total charges", F.money(data.total_charges, " AUD"),
        [
            ("Payments received", F.money(data.payments_received)),
            ("Refunds made", F.money(data.refunds)),
            ("Credits applied", F.money(data.credits)),
        ],
        balance_label, balance_value,
        width=card_w, label_frac=0.57,
    )

    disb_flows: list = []
    for i, r in enumerate(data.disbursement_rows):
        if i:
            disb_flows.append(Spacer(1, 6))
        head = f"<b>{esc(F.money(r.amount))}</b>"
        if r.date:
            head += f"  ·  {esc(r.date)}"
        disb_flows.append(P(head, L.STYLE_BILL_NAME))
        disb_flows.append(P(multiline_html(r.particulars), L.STYLE_BILL_MUTED))
    if not disb_flows:
        disb_flows = [PT("Nil", L.STYLE_FIELD_VALUE_SMALL)]
    if data.disbursement_notes.strip():
        disb_flows += [Spacer(1, 6), P(multiline_html(data.disbursement_notes), L.STYLE_NOTE_ITALIC)]

    def _refs(refs: list[DocumentRef]) -> str:
        labels = [r.label() for r in refs if r.number]
        return ", ".join(labels) if labels else "None"

    # One card, two labelled sections: as two stacked cards this column
    # out-grew the account summary beside it.
    side = [F.panel("Disbursements", [
        *disb_flows,
        Spacer(1, 12),
        P("RELATED DOCUMENTS", L.STYLE_PANEL_HEAD),
        Spacer(1, 8),
        *F.meta_flows([
            *([("Case reference", data.case_reference)] if data.case_reference else []),
            ("Related tax invoice(s)", _refs(data.invoice_refs)),
            ("Related receipt(s)", _refs(data.receipt_refs)),
        ]),
    ], width=side_w)]
    row = Table([[side, "", card]], colWidths=[side_w, gap, card_w])
    row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(P("Account summary", L.STYLE_H2))
    story.append(Spacer(1, 6))
    story.append(row)
    story.append(Spacer(1, 14))

    closing: list = []
    for line in CLOSING_LINES:
        closing += [P(esc(line), L.STYLE_CLOSING_NOTE), Spacer(1, 3)]
    story.append(KeepTogether(closing))
    return story
