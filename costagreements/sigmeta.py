"""Signature-box position metadata, embedded into the PDF's /Subject field
as ``SIGMETA:{...}`` JSON -- the exact convention winzoylegal_new's pdf-lib
builders already use (see e.g. buildGeneralCostAgreementPdf.ts's
``pdfDoc.setSubject('SIGMETA:' + JSON.stringify({...}))``).

Why this exists: per this session's architecture decision, winzoylegal_new
keeps owning the client-signing workflow (tokens, pending/signed status,
notifying the client) via its existing Supabase `document_signatures`
table + `on-document-signed` edge function. That edge function locates
where to stamp a signature image by parsing this exact SIGMETA blob out of
the PDF Subject. Emitting the same shape here means Form956-built PDFs
plug into that existing machinery with zero changes on winzoylegal_new's
side.

The hard part: ReportLab (like pdf-lib) only knows where a flowable
actually landed -- which page, which x/y -- once it's been laid out during
``doc.build()``, not when the story is assembled. ``MeasuredBox`` solves
this the same way winzoylegal_new's own post-signing stamp code discovers
box coordinates from SIGMETA at read time: by recording the real, final
position at draw time (inside ReportLab's rendering pass, where the
canvas's current transform IS the box's true page position) rather than
computing it in advance.
"""
from __future__ import annotations

import json

from reportlab.platypus import Flowable

SIGMETA_PREFIX = "SIGMETA:"


class MeasuredBox(Flowable):
    """Wraps another flowable and, at draw time, reports the absolute
    (page, x, y, w, h) it landed at -- then draws the wrapped flowable
    unchanged. Zero visual effect; purely an observation point."""

    def __init__(self, inner, on_measured):
        super().__init__()
        self.inner = inner
        self.on_measured = on_measured
        self.width = 0.0
        self.height = 0.0

    def wrap(self, avail_width, avail_height):
        self.width, self.height = self.inner.wrap(avail_width, avail_height)
        return self.width, self.height

    def draw(self):
        x, y = self.canv.absolutePosition(0, 0)
        self.on_measured(
            canv=self.canv,
            page_index=self.canv.getPageNumber() - 1,
            x=x, y=y, width=self.width, height=self.height,
        )
        self.inner.drawOn(self.canv, 0, 0)


class SigMetaState:
    """Accumulates SIGMETA fields as boxes are measured during
    ``doc.build()``. There is no single "layout finished" hook to set the
    PDF Subject once at the end (ReportLab, like pdf-lib, finalizes the
    Info dict at ``canvas.save()`` using whatever ``setSubject`` was last
    called with) -- so instead every measurement re-serialises the full
    accumulated JSON and pushes it to the canvas immediately. The very
    last box measured (chronologically last in the document) wins, and by
    then every earlier field is already folded in.
    """

    def __init__(self, extra: dict | None = None):
        self.fields: dict = dict(extra or {})

    def _push(self, canv) -> None:
        canv.setSubject(SIGMETA_PREFIX + json.dumps(self.fields, separators=(",", ":")))

    def client_box_recorder(self):
        def on_measured(canv, page_index, x, y, width, height):
            self.fields.update(p=page_index, cX=round(x, 1), cY=round(y, 1),
                                w=round(width, 1), h=round(height, 1))
            self._push(canv)
        return on_measured

    def rep_box_recorder(self):
        def on_measured(canv, page_index, x, y, width, height):
            self.fields.update(p=page_index, rX=round(x, 1), rY=round(y, 1),
                                w=round(width, 1), h=round(height, 1))
            self._push(canv)
        return on_measured

    def annex_c_sig_recorder(self):
        """Records Annexure C's signature-cell position. Its date field
        sits in a separate table cell drawn immediately after, so it's
        recorded by ``annex_c_date_recorder`` and merged into the same
        ``annexC`` dict (whichever draws last -- the date cell -- pushes
        the fully-merged object)."""
        def on_measured(canv, page_index, x, y, width, height):
            annex_c = self.fields.setdefault("annexC", {})
            annex_c.update(p=page_index, x=round(x, 1), y=round(y, 1),
                            w=round(width, 1), h=round(height, 1))
            self._push(canv)
        return on_measured

    def annex_c_date_recorder(self):
        def on_measured(canv, page_index, x, y, width, height):
            annex_c = self.fields.setdefault("annexC", {})
            annex_c.setdefault("p", page_index)
            annex_c.update(dateX=round(x, 1), dateY=round(y, 1))
            self._push(canv)
        return on_measured


def parse_sigmeta(subject: str | None) -> dict | None:
    """Inverse of the above -- reads SIGMETA back out of a PDF Subject
    string. Not used by the builders themselves; provided for tests and
    for anything on the Form956 side that wants to sanity-check output."""
    if not subject or not subject.startswith(SIGMETA_PREFIX):
        return None
    try:
        return json.loads(subject[len(SIGMETA_PREFIX):])
    except (ValueError, TypeError):
        return None
