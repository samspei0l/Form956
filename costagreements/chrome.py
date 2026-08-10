"""Per-page chrome: watermark, header, footer.

Header/watermark are drawn immediately in the ``onPage`` callback (page
number is already known at draw time). The footer needs "Page X of Y",
which isn't known until the whole document has been laid out, so it uses
the standard two-pass ReportLab recipe: ``NumberedCanvas`` buffers each
page's drawing via an overridden ``showPage``, then replays them in
``save()`` once the total page count is known, drawing the footer on each
buffered page before it's actually emitted.
"""
from __future__ import annotations

from reportlab.pdfgen import canvas as pdfgen_canvas

from . import layout as L


def draw_watermark(canvas, doc) -> None:
    wm_size = 380
    canvas.saveState()
    try:
        canvas.setFillAlpha(0.28)
    except AttributeError:
        pass  # very old reportlab without alpha support -- draw opaque
    canvas.drawImage(
        L.WATERMARK_PATH,
        (L.PAGE_W - wm_size) / 2, (L.PAGE_H - wm_size) / 2,
        width=wm_size, height=wm_size, mask="auto",
    )
    canvas.restoreState()


def draw_header(canvas, doc, doc_id: str, generated_at: str, service_type_label: str = "") -> None:
    canvas.saveState()
    W, H = L.PAGE_W, L.PAGE_H
    ML, MR = L.ML, L.MR

    # Top navy bar.
    canvas.setFillColor(L.NAVY)
    canvas.rect(0, H - 6, W, 6, stroke=0, fill=1)

    # Logo.
    logo_size = 46
    logo_y = H - L.HEADER_H + 14
    canvas.drawImage(L.LOGO_PATH, ML, logo_y, width=logo_size, height=logo_size,
                      mask="auto", preserveAspectRatio=True)

    word_x = ML + logo_size + 12
    canvas.setFont(L.FONT_BOLD, 15)
    canvas.setFillColor(L.NAVY)
    canvas.drawString(word_x, H - 36, "WINZOY LEGAL")
    canvas.setFont(L.FONT_ITALIC, 8.5)
    canvas.setFillColor(L.MUTED)
    canvas.drawString(word_x, H - 50, "Solicitors  •  Registered Migration Agents")
    canvas.setFont(L.FONT_REGULAR, 7.5)
    canvas.drawString(word_x, H - 62, L.FIRM_ACN)

    right_x = W - MR
    is_cover = canvas.getPageNumber() == 1
    if is_cover:
        svc = (service_type_label or "").upper()
        label = f"COST AGREEMENT — {svc}" if svc else "COST AGREEMENT"
        canvas.setFont(L.FONT_BOLD, 9)
        lw = canvas.stringWidth(label, L.FONT_BOLD, 9)
        canvas.setFillColor(L.NAVY)
        canvas.rect(right_x - lw - 14, H - 36, lw + 14, 16, stroke=0, fill=1)
        canvas.setFillColor(L.WHITE)
        canvas.drawString(right_x - lw - 7, H - 32, label)

    canvas.setFont(L.FONT_REGULAR, 7.5)
    canvas.setFillColor(L.MUTED)
    ref = f"Doc ID: {doc_id}"
    canvas.drawRightString(right_x, H - 52, ref)
    canvas.drawRightString(right_x, H - 62, f"Issued: {generated_at}")

    canvas.setStrokeColor(L.NAVY)
    canvas.setLineWidth(0.6)
    canvas.line(ML, H - L.HEADER_H + 6, W - MR, H - L.HEADER_H + 6)
    canvas.restoreState()


def draw_translation_banner(canvas, doc, y: float, text: str) -> None:
    """Optional slim banner just below the header line, on the cover page only."""
    canvas.saveState()
    canvas.setFillColor(L.TRANSLATION_BANNER_BG)
    canvas.rect(L.ML, y - 1, L.CONTENT_W, 22, stroke=0, fill=1)
    canvas.setFillColor(L.WHITE)
    canvas.setFont(L.FONT_REGULAR, 8)
    canvas.drawString(L.ML + 8, y + 5, text[:140])
    canvas.restoreState()


def _draw_footer(canvas, doc_id: str, generated_at: str, page_num: int, total_pages: int) -> None:
    canvas.saveState()
    W = L.PAGE_W
    ML, MR = L.ML, L.MR

    canvas.setStrokeColor(L.NAVY)
    canvas.setLineWidth(0.5)
    canvas.line(ML, L.FOOTER_H + 2, W - MR, L.FOOTER_H + 2)

    canvas.setFont(L.FONT_REGULAR, 7.5)
    canvas.setFillColor(L.NAVY)
    f1 = f"{L.FIRM_NAME}  •  {L.FIRM_ADDRESS_LINE}  •  {L.FIRM_POSTAL_LINE}"
    canvas.drawCentredString(W / 2, L.FOOTER_H - 14, f1)
    canvas.setFont(L.FONT_REGULAR, 7)
    canvas.setFillColor(L.MUTED)
    canvas.drawCentredString(W / 2, L.FOOTER_H - 24, L.FIRM_CONTACT_LINE)

    canvas.setFont(L.FONT_REGULAR, 7)
    canvas.setFillColor(L.MUTED)
    canvas.drawString(ML, 14, f"Page {page_num} of {total_pages}")
    canvas.drawRightString(W - MR, 14, f"Verify: {doc_id} • {generated_at}")

    canvas.setFillColor(L.NAVY)
    canvas.rect(0, 0, W, 4, stroke=0, fill=1)
    canvas.restoreState()


def make_canvas_factory(doc_id: str, generated_at: str):
    """Returns a canvasmaker for ``BaseDocTemplate.build(..., canvasmaker=...)``
    that injects doc_id/generated_at (constant per document, unknown to
    ReportLab's own Canvas constructor) into every buffered page's footer."""

    class NumberedCanvas(pdfgen_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states: list[dict] = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                _draw_footer(self, doc_id, generated_at, self._pageNumber, total_pages)
                super().showPage()
            super().save()

    return NumberedCanvas
