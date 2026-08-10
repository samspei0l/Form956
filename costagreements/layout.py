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

# Frame content area: everything below the header line and above the footer
# line. There is no separate "post-signing stamp band" here (see CLAUDE.md
# note in builders/general.py) so, unlike winzoylegal_new, only the footer
# itself needs to be reserved.
FRAME_TOP_PAD = 14
FRAME_BOTTOM_PAD = 20
FRAME_Y = FOOTER_H + FRAME_BOTTOM_PAD
FRAME_HEIGHT = PAGE_H - HEADER_H - FRAME_TOP_PAD - FRAME_Y

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


def hex_of(color: Color) -> str:
    """'#rrggbb' for use inside Paragraph inline <font color="..."> markup."""
    return "#%02x%02x%02x" % (round(color.red * 255), round(color.green * 255), round(color.blue * 255))
