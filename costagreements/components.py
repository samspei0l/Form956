"""Reusable Platypus flowables shared across cost-agreement builders.

Two structural fixes live here relative to winzoylegal_new's pdf-lib
version (see costagreements/layout.py for the full rationale):

1. Tables (``works_fee_table``, ``disbursement_table``, ...) let ReportLab
   measure wrapped ``Paragraph`` content to size each row, instead of a
   hand-picked ``Math.max(110, totalBulletLines * 14 + 20)``-style guess.
2. ``signature_block()`` wraps the entire client-initials/signature/date
   block in ``KeepTogether`` so ReportLab pushes the *whole block* to the
   next page rather than risking a split -- this is the direct fix for the
   spacing bug the user reported.
"""
from __future__ import annotations

import io
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Flowable, Image, KeepTogether, Paragraph, Spacer, Table, TableStyle

from . import layout as L
from .money import fmt_amt

# --------------------------------------------------------------------- text safety
# User-supplied strings (names, addresses, notes) flow into ReportLab's
# Paragraph mini-markup. Escape them before interpolation so stray
# '<', '>', '&' can't be misread as markup -- everything *we* control
# (bullet joiners, <br/> line breaks) is added after escaping the parts.


def esc(text) -> str:
    return _xml_escape(str(text if text is not None else ""))


def bulleted_html(items: list[str], gap: str = "<br/>") -> str:
    return gap.join(f"•  {esc(item)}" for item in items)


def multiline_html(text: str) -> str:
    return "<br/>".join(esc(line) for line in (text or "").split("\n"))


def P(text: str, style: ParagraphStyle) -> Paragraph:
    """Paragraph over already-escaped/markup-safe text (headings, labels,
    or output of bulleted_html/multiline_html)."""
    return Paragraph(text, style)


def PT(text: str, style: ParagraphStyle) -> Paragraph:
    """Paragraph over raw, untrusted text -- escapes first."""
    return Paragraph(esc(text), style)


# --------------------------------------------------------------------- images
def decode_data_uri(data_uri: str | None) -> bytes | None:
    """Decode a 'data:image/png;base64,...' URI. Returns None on anything
    that isn't a well-formed image data URI (including empty/None input)."""
    if not data_uri:
        return None
    import base64
    import re

    m = re.match(r"^data:image/(png|jpe?g);base64,(.+)$", data_uri.strip(), re.DOTALL)
    if not m:
        return None
    try:
        return base64.b64decode(m.group(2))
    except Exception:
        return None


def _scaled_image(image_bytes: bytes, max_w: float, max_h: float) -> Image:
    reader = ImageReader(io.BytesIO(image_bytes))
    iw, ih = reader.getSize()
    ratio = min(max_w / iw, max_h / ih)
    return Image(io.BytesIO(image_bytes), width=iw * ratio, height=ih * ratio)


# --------------------------------------------------------------------- checkbox
class _Checkbox(Flowable):
    def __init__(self, checked: bool, size: float = 10):
        super().__init__()
        self.checked = checked
        self.size = size
        self.width = size
        self.height = size

    def wrap(self, avail_width, avail_height):
        return self.size, self.size

    def draw(self):
        c = self.canv
        s = self.size
        c.setStrokeColor(L.BLACK)
        c.setLineWidth(0.7)
        c.rect(0, 0, s, s, stroke=1, fill=0)
        if self.checked:
            c.setLineWidth(1)
            c.line(s * 0.15, s * 0.5, s * 0.4, s * 0.2)
            c.line(s * 0.4, s * 0.2, s * 0.9, s * 0.8)


def checkbox_row(checked: bool, text: str, width: float, style: ParagraphStyle | None = None) -> Table:
    """A checkbox + wrapped label, safe to place anywhere ReportLab needs a
    single flowable (used for the capacity checkboxes on page 1 and in the
    signature block's 'Acting Capacity' section)."""
    style = style or L.STYLE_BODY_SMALL
    t = Table([[_Checkbox(checked), PT(text, style)]], colWidths=[16, width - 16])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("TOPPADDING", (0, 0), (0, 0), 2),
        ("LEFTPADDING", (1, 0), (1, 0), 4),
        ("TOPPADDING", (1, 0), (1, 0), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


# --------------------------------------------------------------------- tables
def parties_table(date_text: str, our_ref: str, client_name: str, client_address: str) -> Table:
    col1w = L.CONTENT_W * 0.45
    col2w = L.CONTENT_W * 0.10
    col3w = L.CONTENT_W * 0.45
    data = [
        [PT("Winzoy Legal", L.STYLE_BOLD), PT("AND", L.STYLE_TABLE_HEAD), PT(f"Our Ref: {our_ref or ''}", L.STYLE_BODY)],
        [PT(L.FIRM_ADDRESS_LINE, L.STYLE_BODY), "", PT(f"Client's name: {client_name or ''}", L.STYLE_BODY)],
        [PT("RICHMOND NSW 2753", L.STYLE_BODY), "", PT(f"Address: {client_address or ''}", L.STYLE_BODY)],
    ]
    t = Table(data, colWidths=[col1w, col2w, col3w])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, L.BLACK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def works_fee_table(bullets: list[str], professional_fee_text: str) -> Table:
    w1 = L.CONTENT_W * 0.65
    w2 = L.CONTENT_W * 0.35
    default_bullets = [
        "Process your application",
        "Follow up your case until it is finalized",
        "Inform you of the outcome of your visa application",
    ]
    fee_style = ParagraphStyle("CA_FeeCell", fontName=L.FONT_REGULAR, fontSize=10.5, alignment=1)
    data = [
        [PT("Our works for you", L.STYLE_TABLE_HEAD), PT("Our Professional Cost", L.STYLE_TABLE_HEAD)],
        [P(bulleted_html(bullets or default_bullets), L.STYLE_BODY_SMALL), P(esc(professional_fee_text), fee_style)],
    ]
    t = Table(data, colWidths=[w1, w2])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, L.BLACK),
        ("BACKGROUND", (0, 0), (-1, 0), L.GRAY),
        ("VALIGN", (0, 1), (-1, 1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def disbursement_table(rows: list[tuple[str, str]]) -> Table:
    w1 = L.CONTENT_W * 0.70
    w2 = L.CONTENT_W * 0.30
    amt_style = ParagraphStyle("CA_DisbAmt", fontName=L.FONT_REGULAR, fontSize=10.5, alignment=1)
    data = [[PT("Disbursement", L.STYLE_TABLE_HEAD), PT("Amount", L.STYLE_TABLE_HEAD)]]
    for label, amount_text in rows:
        data.append([PT(label, L.STYLE_TABLE_CELL), P(esc(amount_text), amt_style)])
    t = Table(data, colWidths=[w1, w2])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, L.BLACK),
        ("BACKGROUND", (0, 0), (-1, 0), L.GRAY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def cost_summary_table(professional_cost: float, disbursements_cost: float, total_suffix: str = "") -> Table:
    total_cost = professional_cost + disbursements_cost
    rows = [
        ("Total Professional Cost", professional_cost, False),
        ("Total Disbursements Cost", disbursements_cost, False),
        ("Total Cost", total_cost, True),
    ]
    data = [[PT("Cost Summary", L.STYLE_TABLE_HEAD_WHITE), ""]]
    final_row_idx = None
    for i, (label, amount, final) in enumerate(rows, start=1):
        label_style = ParagraphStyle(
            f"CA_CS_Label_{i}", fontName=L.FONT_BOLD if final else L.FONT_REGULAR,
            fontSize=12 if final else 10.5, textColor=L.NAVY if final else L.BLACK,
        )
        amt_style = ParagraphStyle(
            f"CA_CS_Amt_{i}", fontName=L.FONT_BOLD if final else L.FONT_REGULAR,
            fontSize=12 if final else 10.5, textColor=L.NAVY if final else L.BLACK, alignment=2,
        )
        suffix = total_suffix if final else ""
        data.append([PT(label, label_style), P(f"${fmt_amt(amount)}{esc(suffix)}", amt_style)])
        if final:
            final_row_idx = i
    t = Table(data, colWidths=[L.CONTENT_W * 0.6, L.CONTENT_W * 0.4])
    style = [
        ("SPAN", (0, 0), (1, 0)),
        ("BACKGROUND", (0, 0), (1, 0), L.NAVY),
        ("GRID", (0, 0), (-1, -1), 0.5, L.BLACK),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    if final_row_idx is not None:
        style.append(("BACKGROUND", (0, final_row_idx), (1, final_row_idx), L.FINAL_BG))
        style.append(("LINEBEFORE", (0, final_row_idx), (0, final_row_idx), 4, L.GOLD))
    t.setStyle(TableStyle(style))
    return t


def bank_details_box() -> Table:
    data = [
        [PT("BANK ACCOUNT DETAILS", L.STYLE_H2), ""],
        [PT("Bank: Commonwealth Bank of Australia", L.STYLE_BODY_SMALL),
         PT("Account Name: Winzoy Legal", L.STYLE_BODY_SMALL)],
        [PT("BSB: 067 873", L.STYLE_BODY_SMALL),
         PT("Account No: 1007 5448", L.STYLE_BODY_SMALL)],
    ]
    t = Table(data, colWidths=[L.CONTENT_W / 2, L.CONTENT_W / 2])
    t.setStyle(TableStyle([
        ("SPAN", (0, 0), (1, 0)),
        ("BOX", (0, 0), (-1, -1), 1, L.NAVY),
        ("BACKGROUND", (0, 0), (-1, -1), L.BANK_BG),
        ("LINEBEFORE", (0, 0), (0, -1), 4, L.GOLD),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def staff_note_box(note_text: str) -> Table | None:
    if not note_text or not note_text.strip():
        return None
    label_style = ParagraphStyle("CA_NoteLabel", fontName=L.FONT_BOLD, fontSize=8, textColor=L.NAVY)
    body_style = ParagraphStyle("CA_NoteBody", fontName=L.FONT_REGULAR, fontSize=9, leading=12, textColor=L.BLACK)
    t = Table(
        [[P("NOTE", label_style)], [P(multiline_html(note_text), body_style)]],
        colWidths=[L.CONTENT_W],
    )
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, L.NOTE_BORDER),
        ("BACKGROUND", (0, 0), (-1, -1), L.NOTE_BG),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (0, 0), 8),
        ("BOTTOMPADDING", (0, 0), (0, 0), 2),
        ("TOPPADDING", (0, 1), (0, 1), 0),
        ("BOTTOMPADDING", (0, 1), (0, 1), 8),
    ]))
    return t


def two_column_terms_box(header_left: str, header_right: str,
                          left_bullets: list[str], right_bullets: list[str],
                          extra_right: list | None = None) -> Table:
    """The 'WHAT WE MUST DO' / 'WHAT YOU MUST DO' two-column page."""
    col_w = (L.CONTENT_W - 10) / 2
    head_style = ParagraphStyle("CA_TwoColHead", fontName=L.FONT_BOLD, fontSize=10,
                                 alignment=1, textColor=L.WHITE, backColor=L.NAVY)
    left_flow = [P(bulleted_html(left_bullets, gap="<br/><br/>"), L.STYLE_BODY_TINY)]
    right_flow = [P(bulleted_html(right_bullets, gap="<br/><br/>"), L.STYLE_BODY_TINY)]
    if extra_right:
        right_flow.append(Spacer(1, 8))
        right_flow.extend(extra_right)
    data = [[PT(header_left, head_style), PT(header_right, head_style)], [left_flow, right_flow]]
    t = Table(data, colWidths=[col_w, col_w])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (0, -1), 0.5, L.BLACK),
        ("BOX", (1, 0), (1, -1), 0.5, L.BLACK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 1), (-1, 1), 6),
        ("RIGHTPADDING", (0, 1), (-1, 1), 6),
        ("TOPPADDING", (0, 1), (-1, 1), 10),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
    ]))
    return t


# --------------------------------------------------------------------- signature block
def _underlined_line(text: str, width: float) -> Table:
    t = Table([[PT(text, L.STYLE_BODY_SMALL)]], colWidths=[width])
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, L.MUTED),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _signature_box(sig_bytes: bytes | None, box_w: float, box_h: float) -> Table:
    if sig_bytes:
        content = _scaled_image(sig_bytes, box_w - 16, box_h - 12)
    else:
        placeholder_style = ParagraphStyle("CA_SigPlaceholder", fontName=L.FONT_ITALIC,
                                            fontSize=9, textColor=L.GRAY, alignment=1)
        content = P("Signature", placeholder_style)
    t = Table([[content]], colWidths=[box_w], rowHeights=[box_h])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, L.NAVY),
        ("BACKGROUND", (0, 0), (-1, -1), L.SIG_BOX_BG),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _signature_column(heading_text: str, name: str, sig_bytes: bytes | None, date_text: str,
                       box_w: float, box_h: float,
                       capacity: str | None = None, lpn: str | None = None, marn: str | None = None) -> list:
    heading_style = ParagraphStyle("CA_SigColHead", fontName=L.FONT_BOLD, fontSize=9.5, textColor=L.NAVY)
    flows: list = [
        PT(heading_text, heading_style),
        Spacer(1, 10),
        _signature_box(sig_bytes, box_w, box_h),
        Spacer(1, 12),
        _underlined_line(f"Name: {name or ''}", box_w),
        Spacer(1, 6),
        _underlined_line(f"Date: {date_text or ''}", box_w),
    ]
    if capacity is not None:
        lpn_display = (lpn or "________") if capacity == "solicitor" else "________"
        marn_display = (marn or "________") if capacity == "rma" else "________"
        cap_head_style = ParagraphStyle("CA_ActCap", fontName=L.FONT_BOLD, fontSize=9, textColor=L.NAVY)
        flows.extend([
            Spacer(1, 14),
            PT("Acting Capacity (check one):", cap_head_style),
            Spacer(1, 6),
            checkbox_row(capacity == "solicitor", f"Legal Practitioner (Solicitor) – LPN: {lpn_display}", box_w),
            checkbox_row(capacity == "rma", f"Registered Migration Agent – MARN: {marn_display}", box_w),
        ])
    return flows


def signature_block(client_name: str, rep_name: str, capacity: str | None, lpn: str | None, marn: str | None,
                     client_sig_bytes: bytes | None, rep_sig_bytes: bytes | None,
                     signed_date_text: str, today_text: str) -> KeepTogether:
    """The full 'client initials / signature / date' + 'admin staff
    signature' block, as a single KeepTogether unit -- see module docstring.
    """
    box_w = (L.CONTENT_W - 24) / 2
    sig_box_h = 82

    banner_style = ParagraphStyle("CA_SigBanner", fontName=L.FONT_BOLD, fontSize=12, leading=26,
                                   alignment=1, textColor=L.WHITE, backColor=L.NAVY)
    banner = P("SIGNATURES", banner_style)
    intro = PT(
        "By signing below, both parties confirm they have read, understood, and agree to "
        "the terms of this Costs Agreement.",
        L.STYLE_BODY_MUTED_SMALL,
    )

    client_col = _signature_column("Client Signature", client_name, client_sig_bytes,
                                    signed_date_text, box_w, sig_box_h)
    rep_col = _signature_column("FOR WINZOY LEGAL", rep_name, rep_sig_bytes,
                                 today_text, box_w, sig_box_h,
                                 capacity=capacity, lpn=lpn, marn=marn)

    cols_table = Table([[client_col, rep_col]], colWidths=[box_w, box_w])
    cols_table.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 24),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))

    return KeepTogether([banner, Spacer(1, 14), intro, Spacer(1, 16), cols_table])
