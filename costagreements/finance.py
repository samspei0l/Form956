"""Reusable Platypus flowables for the finance documents (invoice, receipt).

Ported from winzoylegal_new's ``src/features/_shared/pdfBranding.ts`` card
primitives plus the per-document draw helpers in
``features/invoice/buildInvoicePdf.ts`` and
``features/receipt/buildReceiptPdf.ts``. Same visual language as the
cost agreements -- same header/footer chrome, same navy/gold palette -- but
the page furniture is a *card family* rather than the agreements' ruled
tables, so it lives here instead of in components.py.

Two things this module fixes structurally, relative to the pdf-lib
originals (the same class of fix costagreements/layout.py documents for the
agreements):

1. The "Bill To" / "Invoice Details" pair had to be sized by
   hand -- ``Math.max(leftH, rightH, 96)`` over line counts guessed from the
   font metrics -- so a long address could overflow its card. Here both
   panels are cells of one ReportLab table row, so they are always exactly
   as tall as the taller one, measured rather than guessed.
2. Line-item rows grew via ``Math.max(MIN_ROW_H, descLines.length * 12 + 10)``
   and the caller compared ``y < 200`` to decide when to break the page.
   Here the table's ``repeatRows=1`` lets ReportLab split it wherever it
   actually needs to, re-drawing the navy header on each page.
"""
from __future__ import annotations

from reportlab.platypus import HRFlowable, Paragraph, Spacer, Table, TableStyle

from . import layout as L
from .components import P, PT, esc, multiline_html
from .money import fmt_amt


def money(value, suffix: str = "") -> str:
    """'$1,234.50' / '$1,234.50 AUD' -- the only money format these
    documents print."""
    return f"${fmt_amt(value)}{suffix}"


def humanize_method(method: str) -> str:
    """'bank_transfer' -> 'Bank Transfer'. Payment methods are stored as
    snake_case codes by the finance UI."""
    return " ".join(w[:1].upper() + w[1:] for w in (method or "").split("_") if w)


# --------------------------------------------------------------------- title
def doc_title(text: str) -> list:
    """Document title + the short gold rule under it."""
    return [
        P(esc(text), L.STYLE_DOC_TITLE),
        Spacer(1, 6),
        HRFlowable(width=130, thickness=2, color=L.GOLD, spaceBefore=0, spaceAfter=0, hAlign="LEFT"),
        Spacer(1, 20),
    ]


# --------------------------------------------------------------------- cards
def _panel_cell_style(col: int) -> list:
    return [
        ("BACKGROUND", (col, 0), (col, 0), L.PANEL_BG),
        ("BOX", (col, 0), (col, 0), 0.75, L.GRAY),
        ("LEFTPADDING", (col, 0), (col, 0), L.PANEL_PAD),
        ("RIGHTPADDING", (col, 0), (col, 0), L.PANEL_PAD),
        ("TOPPADDING", (col, 0), (col, 0), L.PANEL_PAD),
        ("BOTTOMPADDING", (col, 0), (col, 0), L.PANEL_PAD),
    ]


def panel(header: str, flows: list, width: float | None = None) -> Table:
    """A full-width bordered card with an uppercase navy header label. Every
    auxiliary block on an invoice or receipt uses this exact shape, so the
    page reads as one card family rather than a mix of treatments."""
    width = L.CONTENT_W if width is None else width
    cell = [P(esc(header).upper(), L.STYLE_PANEL_HEAD), Spacer(1, 10), *flows]
    t = Table([[cell]], colWidths=[width])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), *_panel_cell_style(0)]))
    return t


def panel_row(left_header: str, left_flows: list, right_header: str, right_flows: list) -> Table:
    """Two cards side by side. They are cells of a single table row, so
    ReportLab makes them equal height for free -- see module docstring."""
    col_w = (L.CONTENT_W - L.PANEL_GAP) / 2
    left = [P(esc(left_header).upper(), L.STYLE_PANEL_HEAD), Spacer(1, 10), *left_flows]
    right = [P(esc(right_header).upper(), L.STYLE_PANEL_HEAD), Spacer(1, 10), *right_flows]
    t = Table([[left, "", right]], colWidths=[col_w, L.PANEL_GAP, col_w])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        # The gutter column carries no card chrome at all.
        ("LEFTPADDING", (1, 0), (1, 0), 0),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("TOPPADDING", (1, 0), (1, 0), 0),
        ("BOTTOMPADDING", (1, 0), (1, 0), 0),
        *_panel_cell_style(0),
        *_panel_cell_style(2),
    ]))
    return t


def bill_to_flows(client_name: str, company_name: str = "", address: str = "",
                   phone: str = "", email: str = "") -> list:
    """The "Bill To" card's contents. Sponsorship visas (482/186) bill the
    sponsoring company, in which case the company leads in bold and the
    applicant's name sits under it."""
    flows: list = []
    if company_name:
        flows.append(PT(company_name, L.STYLE_BILL_STRONG))
    flows.append(PT(client_name, L.STYLE_BILL_NAME if company_name else L.STYLE_BILL_STRONG))
    for value in (address, phone, email):
        if value:
            flows.append(PT(value, L.STYLE_BILL_MUTED))
    return flows


def meta_flows(rows: list[tuple[str, str]]) -> list:
    """Label-over-value rows for the "Invoice Details" / "Receipt Details"
    card."""
    flows: list = []
    for i, (label, value) in enumerate(rows):
        if i:
            flows.append(Spacer(1, 9))
        flows.append(PT(label, L.STYLE_FIELD_LABEL))
        flows.append(PT(value, L.STYLE_FIELD_VALUE))
    return flows


def field_grid(fields: list[tuple[str, str]], width: float, col_count: int = 2) -> Table:
    """Label-over-value fields laid out in a grid, for use inside a
    ``panel()`` (the invoice's "How to Pay", the receipt's "Payment
    Details")."""
    col_w = width / col_count
    data: list[list] = []
    for i in range(0, len(fields), col_count):
        chunk = fields[i:i + col_count]
        row = [[PT(label, L.STYLE_FIELD_LABEL), PT(value, L.STYLE_FIELD_VALUE_SMALL)]
               for label, value in chunk]
        row.extend([""] * (col_count - len(chunk)))
        data.append(row)
    t = Table(data, colWidths=[col_w] * col_count)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def notes_panel(notes: str) -> Table | None:
    """Client-facing notes as a fourth card in the same family. Returns None
    for empty notes so the caller can just skip it."""
    if not notes or not notes.strip():
        return None
    return panel("Notes", [P(multiline_html(notes), L.STYLE_NOTE_ITALIC)])


def how_to_pay_panel() -> Table:
    inner_w = L.CONTENT_W - L.PANEL_PAD * 2
    return panel("How to Pay", [field_grid([
        ("Bank", L.BANK_NAME),
        ("Account Name", L.BANK_ACCOUNT_NAME),
        ("BSB", L.BANK_BSB),
        ("Account No", L.BANK_ACCOUNT_NO),
    ], inner_w)])


# --------------------------------------------------------------------- ledger
# Description gets the lion's share of the row; qty/unit/amount are narrow
# and numeric.
_LINE_COLS = (0.50, 0.10, 0.20, 0.20)


def line_items_table(items: list) -> Table:
    """The invoice's line-item ledger: a navy header row, zebra-tinted body
    rows separated by hairlines only -- no per-cell grid -- which reads as a
    ledger rather than a spreadsheet.

    ``items`` are objects with ``description``/``quantity``/``unit_price``/
    ``amount`` attributes (builders/invoice.py's ``InvoiceLineItem``).
    """
    data: list[list] = [[
        P("Description", L.STYLE_LINE_HEAD),
        P("Qty", L.STYLE_LINE_HEAD_RIGHT),
        P("Unit Price", L.STYLE_LINE_HEAD_RIGHT),
        P("Amount", L.STYLE_LINE_HEAD_RIGHT),
    ]]
    for item in items:
        qty = item.quantity
        # Whole quantities print as "1", not "1.0" -- these are counts of a
        # billed item, and the fractional form reads like a rate.
        qty_text = str(int(qty)) if float(qty) == int(qty) else f"{float(qty):g}"
        data.append([
            PT(item.description or "(no description)", L.STYLE_LINE_CELL),
            P(esc(qty_text), L.STYLE_LINE_CELL_RIGHT),
            P(money(item.unit_price), L.STYLE_LINE_CELL_RIGHT),
            P(money(item.amount), L.STYLE_LINE_CELL_RIGHT),
        ])

    t = Table(data, colWidths=[L.CONTENT_W * f for f in _LINE_COLS], repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), L.NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 9),
        ("TOPPADDING", (0, 1), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
    ]
    for row in range(1, len(data)):
        if (row - 1) % 2 == 0:
            style.append(("BACKGROUND", (0, row), (-1, row), L.ROW_ALT))
        style.append(("LINEBELOW", (0, row), (-1, row), 0.5, L.GRAY))
    t.setStyle(TableStyle(style))
    return t


# The totals card and the balance line under it share one width and one
# column split so their figures sit in the same right-hand column. The
# inner gutter is deliberately narrow (4pt, against 12pt at the outer
# edges): the widest label ("Balance Remaining") and the widest figure
# ("$xxx,xxx.xx AUD" at 13pt bold) both have to fit on one line, and a
# symmetric 14pt pad wrapped them.
TOTALS_BOX_W = L.CONTENT_W * 0.55
_TOTALS_PADDING = [
    ("LEFTPADDING", (0, 0), (0, -1), 12),
    ("RIGHTPADDING", (0, 0), (0, -1), 4),
    ("LEFTPADDING", (1, 0), (1, -1), 4),
    ("RIGHTPADDING", (1, 0), (1, -1), 12),
]


def totals_card(rows: list[tuple[str, str]], total_label: str, total_value: str) -> Table:
    """A single right-aligned ledger card: quiet label/value rows, a navy
    rule, then the headline figure picked out on a soft gold field. A
    full-width fill, not a border, is the one place on the page where colour
    carries emphasis -- and it is the same treatment on both document types
    (invoice "Total Due", receipt "Paid to Date")."""
    data: list[list] = [[P(esc(label), L.STYLE_TOTALS_LABEL), P(esc(value), L.STYLE_TOTALS_VALUE)]
                        for label, value in rows]
    total_row = len(data)
    data.append([P(esc(total_label), L.STYLE_TOTAL_DUE_LABEL),
                 P(esc(total_value), L.STYLE_TOTAL_DUE_VALUE)])

    t = Table(data, colWidths=[TOTALS_BOX_W * 0.5, TOTALS_BOX_W * 0.5])
    t.hAlign = "RIGHT"
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.75, L.GRAY),
        ("LINEABOVE", (0, total_row), (-1, total_row), 0.8, L.NAVY),
        ("BACKGROUND", (0, total_row), (-1, total_row), L.GOLD_TINT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        *_TOTALS_PADDING,
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, total_row), (-1, total_row), 9),
        ("BOTTOMPADDING", (0, total_row), (-1, total_row), 9),
    ]))
    return t


def balance_line(label: str, value: str, note: str = "") -> Table:
    """A plain bold line beneath the totals card, aligned to the same column
    -- with an optional one-line note under it. No second ledger; a single
    honest sentence."""
    data: list[list] = [[P(esc(label), L.STYLE_BALANCE_LABEL), P(esc(value), L.STYLE_BALANCE_VALUE)]]
    if note:
        # The credit note spans both columns so a full sentence isn't
        # squeezed into the figure column.
        data.append([P(esc(note), L.STYLE_CREDIT_NOTE), ""])
    t = Table(data, colWidths=[TOTALS_BOX_W * 0.5, TOTALS_BOX_W * 0.5])
    t.hAlign = "RIGHT"
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        *_TOTALS_PADDING,
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if note:
        style += [("SPAN", (0, 1), (1, 1)), ("TOPPADDING", (0, 1), (-1, 1), 4)]
    t.setStyle(TableStyle(style))
    return t


def closing_note(text: str) -> Paragraph:
    return PT(text, L.STYLE_CLOSING_NOTE)


# --------------------------------------------------------------------- doc id
def stable_doc_id(prefix: str, seed_parts: list[str]) -> str:
    """'WZL-INV-A1B2-C3D4'.

    Unlike the cost agreements' ``_make_doc_id()`` (and winzoylegal_new's
    ``makeDocId()``), this seeds *only* from the document's own identifying
    fields -- no ``time.time()``. A finance document is a numbered record:
    regenerating invoice INV-0042's PDF must reprint the same Doc ID, or the
    "Verify:" stamp in the footer means nothing.
    """
    seed = "|".join([prefix, *[s or "" for s in seed_parts]])
    h = 0
    for ch in seed:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    hex_str = format(h, "08x").upper()
    return f"WZL-{prefix}-{hex_str[:4]}-{hex_str[4:8]}"
