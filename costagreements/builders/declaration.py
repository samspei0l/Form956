"""Builds the client Declaration PDF.

The firm's workflow document ("DECLARATION -- WILL GO WITH Cost Agreement")
asks four questions the applicant must answer before an application is
lodged: visa-compliance history, refusal/cancellation history, health, and
character. Until now they were asked as prose in the ``draft_review_general``
outcome email and answered in a free-text reply, so nothing could be
reported on and no stage could be gated on them. This renders them as a
document of record instead.

WHY THIS IS ITS OWN DOCUMENT, NOT A COST-AGREEMENT ANNEXURE
The declaration is the applicant's own signed statement; a cost agreement is
a two-party contract about fees. Bolting one onto the other would mean a
client cannot re-declare without re-signing the fee agreement, and a case
with two services would have the declaration duplicated inside each one's
agreement. It is registered in the same ``app.py`` dicts and wears the same
brand chrome -- exactly the arrangement ``invoice``/``receipt`` already use
for documents that are not cost agreements but are built by this engine.

THE QUESTION AND CLAUSE TEXT IS SERVER-HELD.
``answers`` carries only yes/no plus the applicant's detail; the wording of
the questions and of the declaration clauses lives in this module and is
never accepted from the caller. Same rule that makes a checklist item's
label trustworthy downstream: if the client's browser could supply the
question, the document would be evidence of nothing. The clause text is
verbatim from winzoylegal_new's ``draft_review_general`` email template
(20260830100000), which is the firm's own wording -- treat changes to it as
legal-content changes, not copy edits.

UNSIGNED AND SIGNED ARE THE SAME BUILDER, CALLED TWICE.
The applicant is sent an unanswered copy to read, then answers and signs,
and the final copy is rebuilt with the answers and the signature image
baked in (``client_signature_data``). It is deliberately NOT stamped
after the fact the way cost agreements are: a detail answer is free text of
unknown length, and a stamp has to fit a box measured before the text
existed. Rebuilding lets ReportLab paginate it properly. That is also why
there is no SIGMETA here and why the frame runs down to the footer
(``DOC_FRAME_Y``) rather than reserving the post-signing stamp band -- see
costagreements/layout.py.

The Doc ID is deterministic (``stable_doc_id``, no clock) for the same
reason the finance documents' is: the read copy and the signed copy are the
same document in two states and must carry the same identifier.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime

from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, PageTemplate, Spacer, Table, TableStyle

from pdfform.validate import DATE_DDMMYYYY, DATE_YYYYMMDD, ValidationError, normalise_date

from .. import chrome
from .. import layout as L
from ..components import (
    P,
    PT,
    bind_headings,
    bulleted_html,
    checkbox_row,
    compact_signature_block,
    decode_data_uri,
    esc,
    multiline_html,
)
from ..finance import meta_flows, panel_row, stable_doc_id


# =============================================================================
# The questions. Order is the document's order; `key` is what the payload and
# winzoylegal_new's `case_declarations` columns use, and must not be reworded
# even if the prose is -- it is the stable identifier for a stored answer.
# =============================================================================
DECLARATION_QUESTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "visa_compliance",
        "Visa Compliance History",
        (
            "Has the applicant, or any person included in this application, ever been in "
            "Australia or any other country and failed to comply with visa conditions or "
            "remained beyond their authorised period of stay?"
        ),
    ),
    (
        "visa_refusal",
        "Visa Refusal / Cancellation History",
        (
            "Has the applicant, or any person included in this application, ever had an "
            "application for entry or further stay in Australia or any other country "
            "refused, or had a visa cancelled?"
        ),
    ),
    (
        "health",
        "Health Declaration",
        (
            "Does the applicant, or any person included in this application, have any "
            "health-related declarations, including but not limited to pregnancy, "
            "tuberculosis, cancer, heart disease, or any other significant medical "
            "condition?"
        ),
    ),
    (
        "character",
        "Character Declaration",
        (
            "Does the applicant, or any person included in this application, have any "
            "character-related declarations, including but not limited to court "
            "proceedings, criminal charges or convictions in any country, domestic "
            "violence matters, or debts owed to the Australian Government or any "
            "Australian public authority?"
        ),
    ),
)

QUESTION_KEYS: tuple[str, ...] = tuple(k for k, _, _ in DECLARATION_QUESTIONS)

# Verbatim from the firm's draft_review_general email template
# (winzoylegal_new 20260830100000). Legal content -- see module docstring.
DECLARATION_CLAUSES: tuple[str, ...] = (
    (
        "I/we have reviewed all draft forms, supporting documents, and information "
        "prepared for the visa application thoroughly."
    ),
    (
        "All information and documents provided to Winzoy Legal and included in the "
        "application are true, correct, genuine, complete, and accurate to the best of "
        "my/our knowledge."
    ),
    (
        "I/we confirm that no false, misleading, bogus, fabricated, or incorrect "
        "information or documents have been provided or included in the application."
    ),
    (
        "I/we understand that providing false or misleading information or bogus "
        "documents to the Department of Home Affairs may result in visa refusal, "
        "cancellation, Public Interest Criterion (PIC) issues including PIC 4020, "
        "exclusion periods, or other legal consequences."
    ),
    (
        "I/we acknowledge that the application is being lodged based on the information "
        "and documents provided by me/us, and I/we accept responsibility for the "
        "accuracy and genuineness of such information and documents."
    ),
    (
        "I/we authorise Winzoy Legal to proceed with the lodgement of the application "
        "based on the reviewed and confirmed documents."
    ),
)

VALID_ANSWERS = frozenset({"yes", "no"})


# =============================================================================
# Schema
# =============================================================================
@dataclass
class DeclarationAnswer:
    answer: str = ""   # "yes" | "no" | "" (not yet answered -- the read copy)
    detail: str = ""

    @property
    def is_yes(self) -> bool:
        return self.answer == "yes"


@dataclass
class DeclarationData:
    date: str                       # DD/MM/YYYY (or YYYY-MM-DD, normalised first)
    our_ref: str                    # the Lead Number this declaration belongs to
    client_name: str
    client_address: str = ""
    client_dob: str = ""
    visa_type: str = ""
    rep_name: str = ""
    capacity: str = ""              # "solicitor" | "rma" | ""
    lpn: str = ""
    marn: str = ""
    answers: dict[str, DeclarationAnswer] = field(default_factory=dict)
    signer_name: str = ""
    signed_at: str = ""
    client_signature_data: str | None = None   # base64 data URI
    rep_signature_data: str | None = None

    @classmethod
    def from_payload(cls, payload: dict) -> "DeclarationData":
        raw = payload.get("answers") or {}
        answers: dict[str, DeclarationAnswer] = {}
        for key in QUESTION_KEYS:
            entry = raw.get(key) or {}
            if not isinstance(entry, dict):
                entry = {}
            answers[key] = DeclarationAnswer(
                answer=str(entry.get("answer") or "").strip().lower(),
                detail=str(entry.get("detail") or "").strip(),
            )
        return cls(
            date=str(payload.get("date") or ""),
            our_ref=str(payload.get("our_ref") or ""),
            client_name=str(payload.get("client_name") or ""),
            client_address=str(payload.get("client_address") or ""),
            client_dob=str(payload.get("client_dob") or ""),
            visa_type=str(payload.get("visa_type") or ""),
            rep_name=str(payload.get("rep_name") or ""),
            capacity=str(payload.get("capacity") or ""),
            lpn=str(payload.get("lpn") or ""),
            marn=str(payload.get("marn") or ""),
            answers=answers,
            signer_name=str(payload.get("signer_name") or ""),
            signed_at=str(payload.get("signed_at") or ""),
            client_signature_data=payload.get("client_signature_data") or None,
            rep_signature_data=payload.get("rep_signature_data") or None,
        )


# =============================================================================
# Validation
# =============================================================================
REQUIRED_FIELDS = frozenset({"date", "our_ref", "client_name"})
IMAGE_FIELDS = frozenset({"client_signature_data", "rep_signature_data"})


def validate_declaration(payload: dict) -> list[ValidationError]:
    """Validate a Declaration payload. Empty list = valid.

    Answers are optional as a set -- an unanswered payload is the read copy
    the applicant is sent before they fill it in. What is NOT optional is
    coherence: an answer must be yes or no, and a "yes" must carry the
    detail the question asks for. That rule is enforced here as well as in
    the browser and in winzoylegal_new's own CHECK constraint, because a
    declaration recording "yes" with no detail is worse than no declaration.
    """
    errs: list[ValidationError] = []

    for f in REQUIRED_FIELDS:
        v = payload.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            errs.append(ValidationError(field=f, code="required", message=f"{f!r} is required"))

    date_val = payload.get("date")
    if isinstance(date_val, str) and date_val:
        if not (DATE_DDMMYYYY.match(date_val) or DATE_YYYYMMDD.match(date_val)):
            errs.append(ValidationError(
                field="date", code="format",
                message=f"'date' must be DD/MM/YYYY or YYYY-MM-DD, got {date_val!r}",
            ))
        else:
            try:
                d, m, y = normalise_date(date_val).split("/")
                _date(int(y), int(m), int(d))
            except ValueError:
                errs.append(ValidationError(
                    field="date", code="value", message=f"'date' is not a real date: {date_val!r}",
                ))

    raw = payload.get("answers")
    if raw is not None:
        if not isinstance(raw, dict):
            errs.append(ValidationError(
                field="answers", code="format", message="'answers' must be an object keyed by question",
            ))
        else:
            for key in raw:
                if key not in QUESTION_KEYS:
                    errs.append(ValidationError(
                        field=f"answers.{key}", code="unknown",
                        message=f"unknown declaration question {key!r}; expected one of {list(QUESTION_KEYS)}",
                    ))
            for key in QUESTION_KEYS:
                entry = raw.get(key)
                if entry is None:
                    continue
                if not isinstance(entry, dict):
                    errs.append(ValidationError(
                        field=f"answers.{key}", code="format",
                        message=f"answers.{key} must be an object with 'answer' and optional 'detail'",
                    ))
                    continue
                answer = str(entry.get("answer") or "").strip().lower()
                detail = str(entry.get("detail") or "").strip()
                if answer and answer not in VALID_ANSWERS:
                    errs.append(ValidationError(
                        field=f"answers.{key}.answer", code="value",
                        message=f"must be 'yes' or 'no', got {answer!r}",
                    ))
                if answer == "yes" and not detail:
                    errs.append(ValidationError(
                        field=f"answers.{key}.detail", code="required",
                        message=f"answers.{key}: details are required when the answer is 'yes'",
                    ))

    for f in IMAGE_FIELDS:
        v = payload.get(f)
        if v and not (isinstance(v, str) and v.startswith("data:image/")):
            errs.append(ValidationError(
                field=f, code="format",
                message=f"{f!r} must be a base64 image data URI (data:image/png;base64,...)",
            ))

    return errs


def apply_declaration_normalisations(payload: dict) -> dict:
    """Shallow copy with dates normalised to DD/MM/YYYY."""
    out = dict(payload)
    for f in ("date", "client_dob"):
        v = out.get(f)
        if isinstance(v, str) and DATE_YYYYMMDD.match(v):
            out[f] = normalise_date(v)
    return out


# =============================================================================
# Builder
# =============================================================================
STYLE_Q_HEAD = ParagraphStyle(
    "DEC_QHead", fontName=L.FONT_BOLD, fontSize=10, leading=13, textColor=L.NAVY,
)
STYLE_Q_BODY = ParagraphStyle(
    "DEC_QBody", fontName=L.FONT_REGULAR, fontSize=9.5, leading=13,
)
STYLE_DETAIL_LABEL = ParagraphStyle(
    "DEC_DetailLabel", fontName=L.FONT_BOLD, fontSize=8, leading=10, textColor=L.MUTED,
)
STYLE_DETAIL = ParagraphStyle(
    "DEC_Detail", fontName=L.FONT_REGULAR, fontSize=9.5, leading=13,
)
STYLE_UNANSWERED = ParagraphStyle(
    "DEC_Unanswered", fontName=L.FONT_ITALIC, fontSize=8.5, leading=11, textColor=L.MUTED,
)


def build_declaration(data: DeclarationData) -> bytes:
    doc_id = stable_doc_id("DEC", [data.our_ref, data.client_name, data.date])
    story = _build_story(data)

    buf = io.BytesIO()
    # DOC_FRAME_*, not FRAME_*: nothing stamps this document afterwards, so
    # there is no post-signing band to keep clear. See the module docstring.
    frame = Frame(
        L.ML, L.DOC_FRAME_Y, L.CONTENT_W, L.DOC_FRAME_HEIGHT, id="main",
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )

    def on_page(canvas, doc):
        chrome.draw_watermark(canvas, doc)
        # The declaration's own date, not a render timestamp -- rebuilding the
        # signed copy must not restamp the header with today's date.
        chrome.draw_header(canvas, doc, doc_id, data.date, "",
                           badge_label="DECLARATION", badge_every_page=True)

    template = PageTemplate(id="main", frames=[frame], onPage=on_page)
    doc = BaseDocTemplate(
        buf, pagesize=(L.PAGE_W, L.PAGE_H), pageTemplates=[template],
        title="Declaration", author="Winzoy Legal",
        topMargin=0, bottomMargin=0, leftMargin=0, rightMargin=0,
    )
    bind_headings(story)
    doc.build(story, canvasmaker=chrome.make_canvas_factory(doc_id, data.date))
    return buf.getvalue()


def _question_block(index: int, heading: str, question: str, answer: DeclarationAnswer) -> KeepTogether:
    """One numbered question with its yes/no marks and, for a yes, the
    applicant's detail. KeepTogether so a question never splits from its own
    answer across a page break -- the failure that would make the document
    ambiguous."""
    flows: list = [
        P(f"{index}. {esc(heading)}", STYLE_Q_HEAD),
        Spacer(1, 4),
        PT(question, STYLE_Q_BODY),
        Spacer(1, 6),
    ]

    half = (L.CONTENT_W - 12) / 2
    marks = Table(
        [[checkbox_row(answer.answer == "yes", "Yes", half),
          checkbox_row(answer.answer == "no", "No", half)]],
        colWidths=[half + 6, half + 6],
    )
    marks.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    flows.append(marks)

    if not answer.answer:
        flows.append(Spacer(1, 4))
        flows.append(P("Not yet answered.", STYLE_UNANSWERED))
    elif answer.is_yes:
        flows.append(Spacer(1, 8))
        flows.append(P("DETAILS PROVIDED", STYLE_DETAIL_LABEL))
        flows.append(Spacer(1, 3))
        # multiline_html escapes and converts newlines; the detail is the one
        # genuinely free-form, client-supplied string in this document, so it
        # must never reach Paragraph markup unescaped.
        detail = Table([[P(multiline_html(answer.detail), STYLE_DETAIL)]], colWidths=[L.CONTENT_W])
        detail.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), L.PANEL_BG),
            ("BOX", (0, 0), (-1, -1), 0.5, L.GRAY),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        flows.append(detail)

    flows.append(Spacer(1, 16))
    return KeepTogether(flows)


def _build_story(data: DeclarationData) -> list:
    story: list = []

    story.append(P("Declaration", L.STYLE_DOC_TITLE))
    story.append(Spacer(1, 4))
    story.append(P(
        "To be completed by the applicant before the application is lodged.",
        L.STYLE_BODY_MUTED_SMALL,
    ))
    story.append(Spacer(1, L.PANEL_GAP))

    applicant_rows = [("Name", data.client_name)]
    if data.client_dob:
        applicant_rows.append(("Date of birth", data.client_dob))
    if data.client_address:
        applicant_rows.append(("Address", data.client_address))

    matter_rows = [("Lead number", data.our_ref)]
    if data.visa_type:
        matter_rows.append(("Application", data.visa_type))
    matter_rows.append(("Date", data.date))

    story.append(panel_row("APPLICANT", meta_flows(applicant_rows),
                           "MATTER", meta_flows(matter_rows)))
    story.append(Spacer(1, L.PANEL_GAP))

    story.append(P(
        "Please answer every question. If you answer <b>Yes</b> to any question, you "
        "must provide details. Answering every question is required before your "
        "application can be lodged.",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 14))

    for i, (key, heading, question) in enumerate(DECLARATION_QUESTIONS, start=1):
        story.append(_question_block(i, heading, question,
                                     data.answers.get(key) or DeclarationAnswer()))

    story.append(P("DECLARATION", L.STYLE_H2))
    story.append(Spacer(1, 6))
    story.append(P("By signing below, I/we declare that:", L.STYLE_BODY_SMALL))
    story.append(Spacer(1, 6))
    story.append(P(bulleted_html(list(DECLARATION_CLAUSES)), L.STYLE_BODY_SMALL))
    story.append(Spacer(1, 6))

    # Client column only. A cost agreement is an agreement BETWEEN the firm
    # and the client, so both sign it; a declaration is the applicant's own
    # statement, and the firm has nothing to attest to on it. The FOR WINZOY
    # LEGAL box was never going to be filled, and an empty signature box on a
    # signed document reads as an oversight.
    story.append(compact_signature_block(
        client_name=data.signer_name or data.client_name,
        rep_name=data.rep_name,
        capacity=data.capacity or None,
        lpn=data.lpn or None,
        marn=data.marn or None,
        client_sig_bytes=decode_data_uri(data.client_signature_data),
        rep_sig_bytes=decode_data_uri(data.rep_signature_data),
        signed_date_text=data.signed_at or data.date,
        today_text=datetime.now().strftime("%d/%m/%Y"),
        client_only=True,
    ))

    return story
