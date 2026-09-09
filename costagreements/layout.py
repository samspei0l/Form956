"""Shared page geometry, brand palette, and paragraph styles for
cost-agreement PDF generation.

This is the single source of truth every builder imports from, on purpose:
winzoylegal_new's equivalent (`_shared/pdfLayout.ts`) documents a bug class
where every builder hand-computed how much vertical space a block needed
and compared that guess to the remaining page space -- when the guess was
wrong, body content collided with the signature/initials/date band. Here
that entire category of bug is avoided structurally: content is built as
ReportLab Platypus flowables (Table/Paragraph/KeepTogether) inside a single
Frame, and ReportLab itself measures each flowable and decides pagination --
see `components.signature_block()` for the specific fix (KeepTogether around
the whole client-initials/signature/date block so it can never be split
across a page boundary).
"""
from __future__ import annotations

import os

from reportlab.lib.colors import Color
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle

# --------------------------------------------------------------------- page geometry
PAGE_W, PAGE_H = A4  # ~595 x 842pt, matching winzoylegal_new's A4 convention

ML = 55
MR = 55
CONTENT_W = PAGE_W - ML - MR

HEADER_H = 78
FOOTER_H = 56

# Post-signing stamp band. Both consumers of these PDFs -- winzoylegal_new's
# `on-document-signed` edge function and its retroactiveSignature.ts -- stamp a
# "signature image + Date" band onto every body page at
# y in [STAMP_DATE_Y, STAMP_BASELINE_Y + STAMP_SIG_H] = [61, 96]. Neither can
# import this module, so these mirror winzoylegal_new/src/features/_shared/
# pdfLayout.ts -- keep them in sync. STAMP_ZONE_TOP is also the edge function's
# SAFE_MIN_Y: a SIGMETA box reporting a bottom below it is rejected outright and
# the client's real signature is silently never embedded.
STAMP_SIG_H = 26
STAMP_BASELINE_Y = 70
STAMP_DATE_Y = STAMP_BASELINE_Y - 9
STAMP_ZONE_TOP = 100

# Clear whitespace between the last line of body content and the stamp band, so
# stamped ink never lands flush against a table rule.
STAMP_ZONE_GAP = 30

# Frame content area: everything below the header line and above the reserved
# stamp band (which itself sits above the footer).
FRAME_TOP_PAD = 14
FRAME_Y = STAMP_ZONE_TOP + STAMP_ZONE_GAP
FRAME_HEIGHT = PAGE_H - HEADER_H - FRAME_TOP_PAD - FRAME_Y

# Frame for documents that are never signed and therefore never stamped --
# the finance documents (invoice, receipt). They reserve no stamp band, so
# their content runs all the way down to just above the footer, matching
# winzoylegal_new's CONTENT_BOTTOM_Y (_shared/pdfBranding.ts).
DOC_FRAME_Y = FOOTER_H + 20
DOC_FRAME_HEIGHT = PAGE_H - HEADER_H - FRAME_TOP_PAD - DOC_FRAME_Y

# Minimum vertical whitespace (pt) reserved between the last content drawn
# above the signature block and the block's own first line. KeepTogether
# (see components.signature_block()) guarantees the block never gets split
# across a page boundary, but says nothing about how close it can sit to
# whatever flowable immediately precedes it -- without an explicit Spacer,
# two flowables can be laid out flush against each other. Mirrors
# winzoylegal_new's SIG_BLOCK_GAP (_shared/pdfLayout.ts).
SIG_BLOCK_GAP = 20

# --------------------------------------------------------------------- brand palette
# 0-1 scale, matching winzoylegal_new's rgb(...) constants exactly.
BLACK = Color(0, 0, 0)
WHITE = Color(1, 1, 1)
GRAY = Color(0.88, 0.88, 0.88)
NAVY = Color(0.075, 0.165, 0.310)
GOLD = Color(0.788, 0.659, 0.298)
MUTED = Color(0.42, 0.42, 0.45)
NOTE_BG = Color(0.96, 0.97, 1.0)
NOTE_BORDER = NAVY
FINAL_BG = Color(0.94, 0.96, 1.0)
BANK_BG = Color(0.95, 0.97, 1.0)
SIG_BOX_BG = Color(0.98, 0.98, 1.0)
LINK_BLUE = Color(0.05, 0.3, 0.7)
TRANSLATION_BANNER_BG = Color(0.18, 0.45, 0.85)

# Card family for the finance documents (see finance.py). Two tints carry
# the accent language: a near-white navy-grey for card surfaces, and a soft
# gold for the one figure on a page that should read as emphasised (Total
# Due / Paid to Date) -- as a full-width fill, never a border. Mirrors
# winzoylegal_new's _shared/pdfBranding.ts constants exactly.
PANEL_PAD = 14
PANEL_GAP = 18
PANEL_BG = Color(0.965, 0.969, 0.977)
GOLD_TINT = Color(0.982, 0.951, 0.888)
ROW_ALT = Color(0.976, 0.979, 0.985)

# --------------------------------------------------------------------- assets
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
LOGO_PATH = os.path.join(ASSETS_DIR, "winzoy-logo.png")
WATERMARK_PATH = os.path.join(ASSETS_DIR, "winzoy-logo-watermark.jpg")

# --------------------------------------------------------------------- fonts
FONT_REGULAR = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
FONT_ITALIC = "Helvetica-Oblique"

FIRM_NAME = "Winzoy Legal"
FIRM_ADDRESS_LINE = "Suite 1/304 Windsor St, Richmond NSW 2753"
FIRM_POSTAL_LINE = "PO Box 267, Richmond NSW 2753"
FIRM_CONTACT_LINE = "M: 0424 010 868   |   E: chi@winzoylegal.com.au   |   W: winzoylegal.com.au"
FIRM_ACN = "ACN 675 741 036"

# Trust/office account the "BANK ACCOUNT DETAILS" box (components.py) and
# the invoice's "How to Pay" panel (finance.py) both print.
BANK_NAME = "Commonwealth Bank of Australia"
BANK_ACCOUNT_NAME = "Winzoy Legal"
BANK_BSB = "067 873"
BANK_ACCOUNT_NO = "1007 5448"


def _style(name: str, **kw) -> ParagraphStyle:
    base = dict(fontName=FONT_REGULAR, fontSize=10, leading=13, textColor=BLACK)
    base.update(kw)
    return ParagraphStyle(name, **base)


# --------------------------------------------------------------------- paragraph styles
STYLE_BODY = _style("CA_Body")
STYLE_BODY_SMALL = _style("CA_BodySmall", fontSize=9.5, leading=12.5)
STYLE_BODY_TINY = _style("CA_BodyTiny", fontSize=8.5, leading=10.5)
STYLE_BODY_MUTED_SMALL = _style("CA_BodyMutedSmall", fontSize=9, leading=12, textColor=MUTED)
STYLE_TITLE = _style("CA_Title", fontName=FONT_BOLD, fontSize=16, leading=19,
                      textColor=NAVY, alignment=1)
STYLE_H2 = _style("CA_H2", fontName=FONT_BOLD, fontSize=11, leading=14, textColor=NAVY)
STYLE_H3 = _style("CA_H3", fontName=FONT_BOLD, fontSize=13, leading=16, textColor=NAVY)
STYLE_BOLD = _style("CA_Bold", fontName=FONT_BOLD)
STYLE_BOLD_SMALL = _style("CA_BoldSmall", fontName=FONT_BOLD, fontSize=9.5, leading=12)
STYLE_ITALIC_MUTED = _style("CA_ItalicMuted", fontName=FONT_ITALIC, fontSize=9.5,
                             leading=12, textColor=MUTED)
STYLE_CENTER_BOLD = _style("CA_CenterBold", fontName=FONT_BOLD, fontSize=13,
                            leading=16, textColor=NAVY, alignment=1)
STYLE_TABLE_HEAD = _style("CA_TableHead", fontName=FONT_BOLD, fontSize=11,
                           leading=14, alignment=1, textColor=BLACK)
STYLE_TABLE_HEAD_WHITE = _style("CA_TableHeadWhite", fontName=FONT_BOLD, fontSize=11,
                                 leading=14, alignment=1, textColor=WHITE)
STYLE_TABLE_CELL = _style("CA_TableCell", fontSize=10.5, leading=13.5)
STYLE_TERMS_HEAD = _style("CA_TermsHead", fontName=FONT_BOLD, fontSize=11, leading=14, textColor=NAVY)
STYLE_TERMS_TITLE = _style("CA_TermsTitle", fontName=FONT_BOLD, fontSize=13, leading=17, textColor=NAVY)
STYLE_TERMS_BODY = _style("CA_TermsBody", fontSize=9.5, leading=12.5)

# ------------------------------------------------- finance document styles
# Used only by finance.py / builders/invoice.py / builders/receipt.py. Sizes
# and colours mirror the pdf-lib originals (winzoylegal_new's
# features/invoice/buildInvoicePdf.ts and features/receipt/buildReceiptPdf.ts)
# so an invoice generated here is visually indistinguishable from one the
# React app used to draw client-side.
STYLE_DOC_TITLE = _style("FIN_DocTitle", fontName=FONT_BOLD, fontSize=20, leading=23, textColor=NAVY)
STYLE_PANEL_HEAD = _style("FIN_PanelHead", fontName=FONT_BOLD, fontSize=8, leading=10, textColor=NAVY)
STYLE_FIELD_LABEL = _style("FIN_FieldLabel", fontSize=7.5, leading=10, textColor=MUTED)
STYLE_FIELD_VALUE = _style("FIN_FieldValue", fontName=FONT_BOLD, fontSize=10.5, leading=13)
STYLE_FIELD_VALUE_SMALL = _style("FIN_FieldValueSmall", fontName=FONT_BOLD, fontSize=10, leading=13)
STYLE_BILL_STRONG = _style("FIN_BillStrong", fontName=FONT_BOLD, fontSize=10.5, leading=14)
STYLE_BILL_NAME = _style("FIN_BillName", fontSize=10.5, leading=14)
STYLE_BILL_MUTED = _style("FIN_BillMuted", fontSize=9.5, leading=13, textColor=MUTED)
STYLE_LINE_HEAD = _style("FIN_LineHead", fontName=FONT_BOLD, fontSize=9.5, leading=12, textColor=WHITE)
STYLE_LINE_HEAD_RIGHT = _style("FIN_LineHeadRight", fontName=FONT_BOLD, fontSize=9.5, leading=12,
                                textColor=WHITE, alignment=2)
STYLE_LINE_CELL = _style("FIN_LineCell", fontSize=9.5, leading=12)
STYLE_LINE_CELL_RIGHT = _style("FIN_LineCellRight", fontSize=9.5, leading=12, alignment=2)
STYLE_TOTALS_LABEL = _style("FIN_TotalsLabel", fontSize=10, leading=13, textColor=MUTED)
STYLE_TOTALS_VALUE = _style("FIN_TotalsValue", fontSize=10, leading=13, alignment=2)
STYLE_TOTAL_DUE_LABEL = _style("FIN_TotalDueLabel", fontName=FONT_BOLD, fontSize=12, leading=15, textColor=NAVY)
STYLE_TOTAL_DUE_VALUE = _style("FIN_TotalDueValue", fontName=FONT_BOLD, fontSize=13, leading=16,
                                textColor=NAVY, alignment=2)
STYLE_BALANCE_LABEL = _style("FIN_BalanceLabel", fontName=FONT_BOLD, fontSize=11, leading=14, textColor=NAVY)
STYLE_BALANCE_VALUE = _style("FIN_BalanceValue", fontName=FONT_BOLD, fontSize=11, leading=14,
                              textColor=NAVY, alignment=2)
STYLE_NOTE_ITALIC = _style("FIN_NoteItalic", fontName=FONT_ITALIC, fontSize=9.5, leading=13, textColor=MUTED)
STYLE_CREDIT_NOTE = _style("FIN_CreditNote", fontSize=8.5, leading=11, textColor=MUTED, alignment=2)
STYLE_CLOSING_NOTE = _style("FIN_ClosingNote", fontName=FONT_ITALIC, fontSize=8, leading=11, textColor=MUTED)

# A heading must never be the last thing on a page with the content it
# introduces stranded overleaf. ReportLab's ``Paragraph.getKeepWithNext()``
# reads this flag off the style, and ``BaseDocTemplate.handle_flowable``
# then keeps the heading together with the flowable that follows it --
# pushing both to the next page rather than splitting them. Set here
# rather than per-builder so all 13 agreement types inherit it, the same
# way ``components.signature_block()`` uses KeepTogether for the
# signature band (see this module's docstring).
for _heading_style in (STYLE_TITLE, STYLE_H2, STYLE_H3, STYLE_CENTER_BOLD,
                        STYLE_TERMS_HEAD, STYLE_TERMS_TITLE):
    _heading_style.keepWithNext = 1


def hex_of(color: Color) -> str:
    """'#rrggbb' for use inside Paragraph inline <font color="..."> markup."""
    return "#%02x%02x%02x" % (round(color.red * 255), round(color.green * 255), round(color.blue * 255))
