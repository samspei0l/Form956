"""Builds the Skills Assessment Only Cost Agreement PDF.

Content (every clause, bullet, and disclosure paragraph) is transcribed
verbatim from winzoylegal_new's
src/features/skillsAssessmentOnlyCostAgreement/buildSkillsAssessmentOnlyCostAgreementPdf.ts
-- only the layout mechanism changed. See costagreements/layout.py for why:
ReportLab Platypus flowables replace pdf-lib's manual y-coordinate
bookkeeping, which is what caused winzoylegal_new's long-running
client-initials/signature/date spacing bugs.

This type uses the *compact* signature-block style (no 'SIGNATURES' banner
or intro sentence) -- winzoylegal_new's own source uses a 36pt signature
box height (``const sigH = 36;``) reserved via ``compactSignatureBlockHeight
(sigH)``, so this port passes ``sig_h=36`` into ``compact_signature_block()``
to match the original box proportions.

This is a synchronous, single-call generator: the returned PDF is the
final document. There is no SIGMETA-metadata / post-signing remote-stamp
step (winzoylegal_new's two-stage e-signing flow) -- out of scope per the
architecture decision for this Flask port. If a signature image isn't
supplied in the payload, the signature box renders empty/bordered for
print-and-sign, matching winzoylegal_new's own "no signature yet" branch.
"""
from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime

from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import BaseDocTemplate, Frame, PageBreak, PageTemplate, Spacer

from pdfform.validate import DATE_DDMMYYYY, DATE_YYYYMMDD, ValidationError, normalise_date

from .. import annexures as annex
from .. import chrome
from .. import layout as L
from ..components import (
    P,
    PT,
    bank_details_box,
    bulleted_html,
    checkbox_row,
    compact_signature_block,
    cost_summary_table,
    decode_data_uri,
    esc,
    staff_note_box,
    two_column_terms_box,
)
from ..money import fmt_amt, parse_amt, sum_amounts
from ..sigmeta import SigMetaState

SIG_BOX_H = 36  # matches buildSkillsAssessmentOnlyCostAgreementPdf.ts's `const sigH = 36;`


# =========================================================================== schema
@dataclass
class SkillsAssessmentOnlyCostAgreementData:
    """Payload shape mirroring winzoylegal_new's
    skillsAssessmentOnlyCostAgreement/types.ts field-for-field (snake_case
    to match this API's existing JSON convention)."""

    # Header
    date: str  # DD/MM/YYYY (or YYYY-MM-DD, normalised before this is built)
    our_ref: str
    client_name: str
    client_address: str

    # Capacity of representative (top of page 1)
    capacity: str  # "solicitor" | "rma"

    # A. Scope of work
    occupation: str
    anzsco_code: str
    assessing_authority: str

    # B/D. Fees
    professional_fee: str
    skill_assessment_authority_fee: str
    priority_processing_fee: str
    total_cost: str
    skill_assessment_processing_time: str

    # F. Payment schedule
    payment_stage1_label: str
    payment_stage1_amount: str
    payment_stage2_label: str
    payment_stage2_amount: str

    rep_name: str

    lodgement_uses_client_card: bool = False
    extra_stages: list[dict] = field(default_factory=list)  # [{"label": str, "amount": str}, ...]

    # Signature block
    marn: str = ""
    lpn: str = ""
    rep_signature_data: str | None = None       # base64 data URI -- see costagreements/validate.py
    client_signature_data: str | None = None    # base64 data URI
    rep_signature_url: str | None = None        # metadata passthrough only, never fetched -- see sigmeta.py

    staff_note: str = ""
    ack_languages: list[str] = field(default_factory=list)
    translation_banner_text: str = ""
    translated_ack_text: str = ""
    include_annexure_d: bool = False

    @classmethod
    def from_payload(cls, payload: dict) -> "SkillsAssessmentOnlyCostAgreementData":
        extra_stages = payload.get("extra_stages") or []
        if not isinstance(extra_stages, list):
            extra_stages = []
        norm_stages = []
        for stage in extra_stages:
            if isinstance(stage, dict):
                norm_stages.append({
                    "label": str(stage.get("label") or ""),
                    "amount": str(stage.get("amount") or ""),
                })

        ack_languages = payload.get("ack_languages") or []
        if isinstance(ack_languages, str):
            ack_languages = [ack_languages] if ack_languages.strip() else []

        return cls(
            date=str(payload.get("date") or ""),
            our_ref=str(payload.get("our_ref") or ""),
            client_name=str(payload.get("client_name") or ""),
            client_address=str(payload.get("client_address") or ""),
            capacity=str(payload.get("capacity") or ""),
            occupation=str(payload.get("occupation") or ""),
            anzsco_code=str(payload.get("anzsco_code") or ""),
            assessing_authority=str(payload.get("assessing_authority") or ""),
            professional_fee=str(payload.get("professional_fee") or ""),
            skill_assessment_authority_fee=str(payload.get("skill_assessment_authority_fee") or ""),
            priority_processing_fee=str(payload.get("priority_processing_fee") or ""),
            total_cost=str(payload.get("total_cost") or ""),
            skill_assessment_processing_time=str(payload.get("skill_assessment_processing_time") or ""),
            payment_stage1_label=str(payload.get("payment_stage1_label") or ""),
            payment_stage1_amount=str(payload.get("payment_stage1_amount") or ""),
            payment_stage2_label=str(payload.get("payment_stage2_label") or ""),
            payment_stage2_amount=str(payload.get("payment_stage2_amount") or ""),
            rep_name=str(payload.get("rep_name") or ""),
            lodgement_uses_client_card=bool(payload.get("lodgement_uses_client_card", False)),
            extra_stages=norm_stages,
            marn=str(payload.get("marn") or ""),
            lpn=str(payload.get("lpn") or ""),
            rep_signature_data=payload.get("rep_signature_data") or None,
            client_signature_data=payload.get("client_signature_data") or None,
            rep_signature_url=payload.get("rep_signature_url") or None,
            staff_note=str(payload.get("staff_note") or ""),
            ack_languages=list(ack_languages),
            translation_banner_text=str(payload.get("translation_banner_text") or ""),
            translated_ack_text=str(payload.get("translated_ack_text") or ""),
            include_annexure_d=bool(payload.get("include_annexure_d", False)),
        )


# =========================================================================== validate
REQUIRED_FIELDS = frozenset({
    "date", "our_ref", "client_name", "client_address", "capacity",
    "occupation", "assessing_authority",
    "professional_fee",
    "payment_stage1_label", "payment_stage1_amount",
    "payment_stage2_label", "payment_stage2_amount",
    "rep_name",
})

VALID_CAPACITIES = frozenset({"solicitor", "rma"})
MONEY_FIELDS = frozenset({
    "professional_fee", "skill_assessment_authority_fee", "priority_processing_fee",
    "payment_stage1_amount", "payment_stage2_amount", "total_cost",
})
IMAGE_FIELDS = frozenset({"client_signature_data", "rep_signature_data"})


def validate_skills_assessment_only_cost_agreement(payload: dict) -> list[ValidationError]:
    """Validate a Skills Assessment Only Cost Agreement payload. Empty list = valid."""
    errs: list[ValidationError] = []

    for f in REQUIRED_FIELDS:
        v = payload.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            errs.append(ValidationError(field=f, code="required", message=f"{f!r} is required"))

    capacity = payload.get("capacity")
    if capacity and capacity not in VALID_CAPACITIES:
        errs.append(ValidationError(
            field="capacity", code="value",
            message=f"'capacity' must be one of {sorted(VALID_CAPACITIES)}, got {capacity!r}",
        ))

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

    for f in MONEY_FIELDS:
        v = payload.get(f)
        if v in (None, ""):
            continue
        if parse_amt(v) < 0:
            errs.append(ValidationError(field=f, code="value", message=f"{f!r} must not be negative"))

    extra_stages = payload.get("extra_stages")
    if extra_stages and isinstance(extra_stages, list):
        for i, stage in enumerate(extra_stages):
            if not isinstance(stage, dict):
                errs.append(ValidationError(
                    field="extra_stages", code="format",
                    message=f"extra_stages[{i}] must be an object with 'label'/'amount'",
                ))
                continue
            amt = stage.get("amount")
            if amt not in (None, "") and parse_amt(amt) < 0:
                errs.append(ValidationError(
                    field="extra_stages", code="value",
                    message=f"extra_stages[{i}].amount must not be negative",
                ))

    for f in IMAGE_FIELDS:
        v = payload.get(f)
        if v and not (isinstance(v, str) and v.startswith("data:image/")):
            errs.append(ValidationError(
                field=f, code="format",
                message=f"{f!r} must be a base64 image data URI (data:image/png;base64,...)",
            ))

    return errs


def apply_skills_assessment_only_normalisations(payload: dict) -> dict:
    """Return a shallow copy of ``payload`` with 'date' normalised to DD/MM/YYYY."""
    out = dict(payload)
    v = out.get("date")
    if isinstance(v, str) and DATE_YYYYMMDD.match(v):
        out["date"] = normalise_date(v)
    return out


# =========================================================================== build
def build_skills_assessment_only_cost_agreement(data: SkillsAssessmentOnlyCostAgreementData) -> bytes:
    doc_id = _make_doc_id(data)
    generated_at = datetime.now().strftime("%d/%m/%Y %H:%M")
    today_short = datetime.now().strftime("%d/%m/%Y")

    story = _build_story(data, today_short)

    buf = io.BytesIO()
    frame = Frame(
        L.ML, L.FRAME_Y, L.CONTENT_W, L.FRAME_HEIGHT, id="main",
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )

    def on_page(canvas, doc):
        chrome.draw_watermark(canvas, doc)
        chrome.draw_header(canvas, doc, doc_id, generated_at, "Skills Assessment")

    template = PageTemplate(id="main", frames=[frame], onPage=on_page)
    doc = BaseDocTemplate(
        buf, pagesize=(L.PAGE_W, L.PAGE_H), pageTemplates=[template],
        title="Costs Disclosure and Costs Agreement", author="Winzoy Legal",
        topMargin=0, bottomMargin=0, leftMargin=0, rightMargin=0,
    )
    doc.build(story, canvasmaker=chrome.make_canvas_factory(doc_id, generated_at))
    return buf.getvalue()


def _make_doc_id(data: SkillsAssessmentOnlyCostAgreementData) -> str:
    seed = f"SAO|{data.our_ref}|{data.client_name}|{time.time()}"
    h = 0
    for ch in seed:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    hex_str = format(h, "08x").upper()
    return f"WZL-SAO-{hex_str[:4]}-{hex_str[4:8]}"


def _build_ack_text(client_name: str, languages: list[str]) -> str:
    langs = [lang for lang in (languages or []) if lang] or ["English"]
    lang_list = " and/or ".join(langs)
    name_phrase = f'I, "{client_name}",' if client_name and client_name.strip() else "I"
    return (
        f"{name_phrase} acknowledge that I understand and accepted the above agreement. "
        f"The agreement has been explained to me and my partner in {lang_list}."
    )


def _build_story(data: SkillsAssessmentOnlyCostAgreementData, today_short: str) -> list:
    story: list = []

    # ═══════════════════════════════════════ PAGE 1 — Agreement details
    if data.translation_banner_text:
        banner_style = ParagraphStyle(
            "SAO_TransBanner", fontName=L.FONT_REGULAR, fontSize=8, leading=14,
            textColor=L.WHITE, backColor=L.TRANSLATION_BANNER_BG, leftIndent=8,
        )
        story.append(PT(data.translation_banner_text, banner_style))
        story.append(Spacer(1, 10))

    story.append(P("Costs Disclosure and Costs Agreement", L.STYLE_TITLE))
    story.append(Spacer(1, 6))
    date_style = ParagraphStyle("SAO_DateLine", fontName=L.FONT_REGULAR, fontSize=10.5, alignment=2)
    story.append(P(f'<font color="{L.hex_of(L.MUTED)}">Date:</font> {esc(data.date)}', date_style))
    story.append(Spacer(1, 10))
    story.append(P("Between", L.STYLE_CENTER_BOLD))
    story.append(Spacer(1, 16))

    story.append(_parties_table(data.date, data.our_ref, data.client_name, data.client_address))
    story.append(Spacer(1, 16))

    story.append(PT(
        "This document, together with our General Terms of Business, sets out the "
        "terms of our offer to provide legal services to you and constitutes our "
        'costs agreement and disclosure pursuant to the Legal Profession Uniform '
        'Law (NSW) ("the Uniform Law").',
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 10))

    story.append(P("CAPACITY OF REPRESENTATIVE", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(checkbox_row(
        data.capacity == "solicitor",
        "Legal Practitioner (Solicitor) – Regulated by the Law Society of NSW and the "
        "Legal Profession Uniform Law (NSW).",
        L.CONTENT_W,
    ))
    story.append(Spacer(1, 4))
    story.append(checkbox_row(
        data.capacity == "rma",
        "Registered Migration Agent (RMA) – Regulated by the Office of the Migration "
        "Agents Registration Authority (OMARA).",
        L.CONTENT_W,
    ))
    story.append(Spacer(1, 8))

    # A. Scope of Work
    story.append(P("A. Scope of Work", L.STYLE_H2))
    story.append(Spacer(1, 4))
    occ_text = (
        f"{data.occupation}{f' (ANZSCO {data.anzsco_code})' if data.anzsco_code else ''}"
        if data.occupation else "_________________________"
    )
    authority_text = data.assessing_authority or "_________________________"
    story.append(PT(
        f"1. You have instructed us to process your Skill Assessment by {authority_text} "
        f"for your nominated occupation {occ_text}. The services to be performed under "
        "this agreement include (but are not limited to):",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 4))
    story.append(P(bulleted_html([
        "Eligibility assessment (analysis of the applicable law and policy and its "
        "application to your circumstances and goals);",
        "Providing advice and assistance in relation to the information and documents "
        "required to support your application;",
        "Preparing your application, including any necessary application forms and "
        "supporting submissions;",
        "Compiling your application in the required form for lodgment;",
        "Lodging your application with the relevant authority for processing as soon "
        "as practicable;",
        "Monitoring the relevant authority's processing of your application after the "
        "date of lodgment, including advising you of any requests and/or "
        "communications received and notifying you of that;",
        "Following up with the final decision and informing you of the outcome.",
    ]), L.STYLE_BODY_SMALL))
    story.append(Spacer(1, 8))

    # B. Professional Fees
    story.append(P("B. Professional Fees", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(PT(
        "We will charge you professional fees for the work we do on a fixed fee of: "
        f"${fmt_amt(data.professional_fee)} inclusive of 10% GST.",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 8))

    # C. Disbursements and Internal Expenses
    story.append(P("C. Disbursements and Internal Expenses", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(PT(
        "You will need to pay the lodgement fees for the Skill Assessment Body. We "
        "will notify you of these Disbursements and you are required to pay them "
        "accordingly or instruct us to assist you with the payment directly.",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 10))

    # D. Estimate of Professional Fees and Internal Expenses
    story.append(P("D. Estimate of Professional Fees and Internal Expenses", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(_estimate_table(data))
    story.append(Spacer(1, 12))

    professional_cost = parse_amt(data.professional_fee)
    disbursements_cost = sum_amounts(data.skill_assessment_authority_fee, data.priority_processing_fee)
    story.append(cost_summary_table(professional_cost, disbursements_cost, total_suffix=" inclusive of GST"))
    story.append(Spacer(1, 14))

    # Variables
    story.append(P("Variables", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(PT("Some of the variables which may affect and change the costs estimate include:", L.STYLE_BODY_SMALL))
    story.append(Spacer(1, 4))
    story.append(P(bulleted_html([
        "(a) your prompt and efficient response to requests for information or instructions;",
        "(b) whether your instructions are varied;",
        "(c) whether documents have to be revised in light of varied instructions;",
        "(d) changes in the law; and",
        "(e) the complexity or uncertainty concerning legal issues affecting your matter.",
    ]), L.STYLE_BODY_SMALL))
    story.append(Spacer(1, 6))
    story.append(PT(
        "Please note that this is an estimate only and not a fixed quote. The total "
        "costs may exceed the estimate. In the event costs change, we will notify you "
        "immediately.",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 12))

    # F. Payment Schedule
    story.append(P("F. Payment Schedule for Our Professional Fee (inclusive of GST)", L.STYLE_H2))
    story.append(Spacer(1, 4))
    stages = [
        (data.payment_stage1_label or "1. PF at the time of signing the Agreement", data.payment_stage1_amount),
        (data.payment_stage2_label or "2. PF before submission of the Application", data.payment_stage2_amount),
    ]
    for i, stage in enumerate(data.extra_stages or []):
        stages.append((stage.get("label") or f"{2 + i + 1}. Stage", stage.get("amount") or ""))
    story.append(_payment_schedule_table(stages))
    story.append(Spacer(1, 14))

    # Bank details
    story.append(PT("Payment of estimated legal fees to the account below:", L.STYLE_ITALIC_MUTED))
    story.append(Spacer(1, 6))
    story.append(bank_details_box())
    story.append(Spacer(1, 14))

    # G. Breach of Payment Schedule and Termination
    story.append(P("G. Breach of Payment Schedule and Termination", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(PT(
        "We may cease to act for you or refuse to perform further work, including if "
        "you do not within 7 days comply with any request to pay an amount in respect "
        "of disbursements or future costs as outlined in the schedule above. You may "
        "terminate our services by written notice at any time. However, if you do so "
        "you will be required to pay our costs incurred up to the date of termination.",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 8))

    # H. Processing Times and Outcome
    story.append(P("H. Processing Times and Outcome", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(PT(
        f"Skills assessment by {data.assessing_authority or 'the relevant Assessing Authority'} "
        "– Standard processing time is approximately "
        f"{data.skill_assessment_processing_time or 'as advised by the Assessing Authority'}, "
        "whereas priority processing time is usually 10 working days upon approval for "
        "priority processing.",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 4))
    story.append(PT(
        "The processing times are not definitive and are completely dependent on the "
        "relevant Assessing Authority.",
        L.STYLE_ITALIC_MUTED,
    ))
    story.append(Spacer(1, 12))

    # I. Acknowledgement and Acceptance of Offer
    story.append(P("I. Acknowledgement and Acceptance of Offer", L.STYLE_H2))
    story.append(Spacer(1, 6))
    ack_text = data.translated_ack_text or _build_ack_text(data.client_name, data.ack_languages)
    story.append(PT(ack_text, L.STYLE_BODY_SMALL))
    story.append(Spacer(1, 10))

    note_box = staff_note_box(data.staff_note)
    if note_box is not None:
        story.append(note_box)
        story.append(Spacer(1, 8))

    client_sig_bytes = decode_data_uri(data.client_signature_data)
    rep_sig_bytes = decode_data_uri(data.rep_signature_data)
    signed_date = data.date or today_short
    sigmeta = SigMetaState()
    story.append(P("Client Signature", L.STYLE_H2))
    story.append(P("FOR WINZOY LEGAL", L.STYLE_H2)) if False else None  # placeholder removed below
    story.pop() if story and story[-1] is None else None
    story.append(compact_signature_block(
        client_name=data.client_name, rep_name=data.rep_name,
        capacity=data.capacity, lpn=data.lpn, marn=data.marn,
        client_sig_bytes=client_sig_bytes, rep_sig_bytes=rep_sig_bytes,
        signed_date_text=signed_date, today_text=today_short,
        sig_h=SIG_BOX_H,
        sigmeta=sigmeta, rep_signature_url=data.rep_signature_url,
    ))

    # ═══════════════════════════════════════ WHAT WE / YOU MUST DO
    story.append(PageBreak())
    story.extend(_what_we_you_must_do())

    # ═══════════════════════════════════════ REGULATORY COMPLIANCE
    story.append(PageBreak())
    story.extend(_regulatory_compliance())

    # ═══════════════════════════════════════ GENERAL TERMS OF BUSINESS
    story.append(PageBreak())
    story.extend(_general_terms())

    # ═══════════════════════════════════════ ANNEXURES A / B / C / (D)
    story.append(PageBreak())
    story.extend(annex.annexure_a_flowables())
    story.append(PageBreak())
    story.extend(annex.annexure_b_flowables())
    story.append(PageBreak())
    story.extend(annex.annexure_c_flowables(data.client_name, client_sig_bytes, sigmeta=sigmeta))
    if data.include_annexure_d:
        story.append(PageBreak())
        story.extend(annex.annexure_d_flowables())

    return story


# --------------------------------------------------------------------- page-1 tables
def _parties_table(date_text: str, our_ref: str, client_name: str, client_address: str):
    """Same three-row 'Winzoy Legal / AND / Our Ref + Client name + Address'
    layout as the source TS's inline drawTableRow calls, transcribed as a
    ReportLab Table (see components.parties_table() for the near-identical
    General Cost Agreement version -- kept local here since the TS source
    draws this one with its own address-wrapping row rather than calling
    a shared helper)."""
    from reportlab.platypus import Table, TableStyle

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


def _estimate_table(data: SkillsAssessmentOnlyCostAgreementData):
    """The 'D. Estimate of Professional Fees and Internal Expenses' table:
    row 1 is the fixed professional-cost line, row 2 is the lodgement-fee
    disbursement cell containing the authority-fee and priority-fee
    sub-bullets."""
    from reportlab.platypus import Table, TableStyle

    w1 = L.CONTENT_W * 0.50
    w2 = L.CONTENT_W * 0.50
    amt_style = ParagraphStyle("SAO_EstAmt", fontName=L.FONT_REGULAR, fontSize=9.5, alignment=1)
    card_note = " (using client card)" if data.lodgement_uses_client_card else ""
    authority_suffix = f" ({data.assessing_authority})" if data.assessing_authority else ""
    authority_fee_text = f"${data.skill_assessment_authority_fee}" if data.skill_assessment_authority_fee else "as advised"
    priority_fee_text = f"${data.priority_processing_fee}" if data.priority_processing_fee else "as advised"
    disb_lines = [
        f"Skill Assessment Authority Fee{authority_suffix}: {authority_fee_text}",
        f"Priority Processing Fee (if applicable): {priority_fee_text}",
    ]

    header = [PT("", L.STYLE_TABLE_HEAD), PT("Amount", L.STYLE_TABLE_HEAD)]
    row1 = [PT("1. Professional Cost", L.STYLE_TABLE_CELL),
            P(f"${esc(fmt_amt(data.professional_fee))} inclusive of GST", amt_style)]
    row2 = [PT(f"2. Disbursement Lodgement Fee{card_note}", L.STYLE_TABLE_CELL),
            P(bulleted_html(disb_lines), L.STYLE_BODY_TINY)]

    t = Table([header, row1, row2], colWidths=[w1, w2])
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


def _payment_schedule_table(stages: list[tuple[str, str]]):
    """The 'F. Payment Schedule' stage-label / amount rows."""
    from reportlab.platypus import Table, TableStyle

    w1 = L.CONTENT_W * 0.70
    w2 = L.CONTENT_W * 0.30
    amt_style = ParagraphStyle("SAO_StageAmt", fontName=L.FONT_REGULAR, fontSize=9.5, alignment=1)
    data = []
    for label, amount in stages:
        data.append([PT(label, L.STYLE_TABLE_CELL), P(f"${esc(fmt_amt(amount))}", amt_style)])
    t = Table(data, colWidths=[w1, w2])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, L.BLACK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


# --------------------------------------------------------------------- pages 2+
def _what_we_you_must_do() -> list:
    wmd = [
        "Act in your best legitimate interests, with honesty, fairness and integrity. "
        "Ensure that our advice is timely and accurate.",
        "Do nothing to increase your costs unnecessarily. Keep accurate and complete "
        "records of your case. Do everything reasonably necessary to perform the "
        "services listed in this agreement where the services are not listed in full details.",
        "Provide you with advice about the processes, issues and legal requirements "
        "involved in your Skills Assessment.",
        "Lodge the Skills Assessment to the relevant Assessing Authority with "
        "supporting documentation, liaise with you as necessary, and inform you of "
        "the result.",
        "Inform you of the outcome of your application.",
    ]
    ymd = [
        "Let us know about changes to your circumstances that might affect your "
        "application. Advise us promptly if you change your address for more than "
        "fourteen consecutive days during the processing of your application.",
        "Provide us, in a timely manner, with documents and information that we need "
        "to act for you. Ensure that the information you give us is true and accurate. "
        "If you discover that information you gave us is wrong, let us know at once so "
        "we can make the necessary corrections immediately.",
        "Provide us with the documents described in the attached letters and Documents "
        "Listed or as per our request to you via our correspondences and telephone "
        "attendances, or our previous conference at the office.",
    ]
    ila_head_style = ParagraphStyle("SAO_ILA", fontName=L.FONT_BOLD, fontSize=9, textColor=L.NAVY)
    extra_right = [
        PT("Independent Legal Advice", ila_head_style),
        P(bulleted_html([
            "It is desirable for you to obtain independent legal advice in relation "
            "to this agreement before you sign it.",
        ]), L.STYLE_BODY_TINY),
    ]
    box = two_column_terms_box("WHAT WE MUST DO", "WHAT YOU MUST DO", wmd, ymd, extra_right=extra_right)
    return [box]


def _regulatory_compliance() -> list:
    flows: list = [
        P("REGULATORY COMPLIANCE AND APPLICABLE LAW", L.STYLE_H2),
        Spacer(1, 6),
        PT("Depending on the designated capacity of your representative as selected above:", L.STYLE_BODY),
        Spacer(1, 10),
    ]
    blocks = [
        (
            "a) If represented by a Registered Migration Agent (RMA):",
            "This agreement is subject to the Migration (Migration Agents Code of "
            'Conduct) Regulations 2021 ("the Code"). The Client acknowledges that they '
            "have been notified of the Code of Conduct and have been provided with a "
            "copy of the official OMARA 'Consumer Guide' prior to or at the time of "
            "signing this agreement. A copy of the Code can be verified online at www.mara.gov.au.",
        ),
        (
            "b) If represented by a Legal Practitioner (Solicitor):",
            "The representative is an Australian Legal Practitioner and does not "
            "operate under the OMARA regulatory framework. This document serves as a "
            "Costs Disclosure and Costs Agreement pursuant to the Legal Profession "
            "Uniform Law (NSW) and the Legal Profession Uniform General Rules 2015.",
        ),
        (
            "c) No Guarantee of Outcome (Applicable to both):",
            "While the representative and Winzoy Legal will perform the agreed work "
            "with professional diligence and competence, the Client acknowledges that "
            "the firm cannot guarantee the successful outcome of any Skills Assessment "
            "application, as all final decisions rest solely with the relevant "
            "Assessing Authority.",
        ),
    ]
    for heading, body in blocks:
        flows.append(PT(heading, L.STYLE_TERMS_HEAD))
        flows.append(Spacer(1, 4))
        flows.append(PT(body, L.STYLE_TERMS_BODY))
        flows.append(Spacer(1, 8))
    return flows


_GENERAL_TERMS: list[tuple[str, str]] = [
    ("General Terms of Business", ""),
    (
        "1 Billing Arrangements",
        "All tax invoices are due and payable 30 days from the date of the tax "
        "invoice. You consent to us sending our tax invoices to you electronically "
        "at your usual email address or mobile phone number as specified by you.",
    ),
    (
        "2 Acceptance of Offer",
        "You may accept the Costs Disclosure and Costs Agreement by:\n"
        "a) signing and returning this document to us or:\n"
        "b) continuing to instruct us. Upon acceptance you agree to pay for our "
        "services on these terms.",
    ),
    (
        "3 Interest Charges",
        "Interest at the maximum rate prescribed in Rule 75 of the Legal Profession "
        'Uniform General Rules 2015 ("Uniform General Rules") (being the Cash Rate '
        "Target set by the Reserve Bank of Australia plus 2%) will be charged on any "
        "amounts unpaid after the expiry of 30 days after a tax invoice is given to "
        "you. Our tax invoices will specify the interest rate to be charged.",
    ),
    (
        "4 Recovery of Costs",
        'The Legal Profession Uniform Law (NSW) ("the Uniform Law") provides that we '
        "cannot take action for recovery of legal costs until 30 days after a tax "
        "invoice (which complies with the Uniform Law) has been given to you.",
    ),
    (
        "5 Your Rights",
        "It is your right to:\n"
        "(a) negotiate a costs agreement with us;\n"
        "(b) negotiate the method of billing (e.g. task based or time based);\n"
        "(c) receive a bill and to request and receive an itemised bill within 30 "
        "days after a lump sum bill or partially itemised bill is payable;\n"
        "(d) seek the assistance of the designated local regulatory authority (the "
        "NSW Commissioner) in the event of a dispute about legal costs;\n"
        "(e) be notified as soon as is reasonably practicable of any significant "
        "change to any matter affecting costs;\n"
        "(f) accept or reject any offer we make for an interstate costs law to apply "
        "to your matter; and\n"
        "(g) notify us that you require an interstate costs law to apply to your matter.",
    ),
    (
        "6 Your Rights in Relation to a Dispute",
        "If you have a dispute in relation to any aspect of our services or legal "
        "costs, we encourage you to discuss your concerns with us in the first "
        "instance so that any issue can be identified and resolved promptly. If the "
        "matter cannot be resolved:\n"
        "(a) For Legal Practitioner (Solicitor) clients: You may apply to the "
        "Manager, Costs Assessment located at the Supreme Court of NSW for an "
        "assessment of our costs, or seek assistance from the designated local "
        "regulatory authority (e.g., the NSW Legal Services Commissioner).\n"
        "(b) For Registered Migration Agent (RMA) clients: You have the right to "
        "lodge a complaint with the Office of the Migration Agents Registration "
        "Authority (OMARA).",
    ),
    (
        "7 Payment Methods",
        "It is our policy that, when acting for new clients, we do one or more of "
        "the following:\n"
        "(a) approve credit;\n"
        "(b) ask the client for their credit card details.\n"
        "Unless otherwise agreed with you, we may determine not to incur fees or "
        "expenses in excess of the amount that we hold in trust on your behalf or "
        "for which credit is approved.",
    ),
    (
        "8 Retention of Your Documents and Electronic Files",
        "(a) Electronic Storage: We maintain a paperless office. By signing this "
        "agreement, you agree that we will store all documents and correspondence "
        "related to your matter in electronic format only.\n"
        "(b) Destruction of Hard Copies: Any hard copy documents provided by you or "
        "third parties will be scanned into our electronic filing system. Once "
        "scanned, the hard copies will be destroyed or returned to you at our "
        "discretion, unless we are legally required to keep the original.\n"
        "(c) Client Responsibility: If you require the return of original hard copy "
        "documents, you must notify us in writing at the time the documents are "
        "provided to us.\n"
        "(d) Archive Period: We will retain your electronic file for at least seven "
        "(7) years after the conclusion of your matter, after which we may delete "
        "the electronic data without further notice to you.\n"
        "(e) Cost of Retrieval: Should you request a copy of your electronic file "
        "during the retention period, we reserve the right to charge a reasonable "
        "administrative fee for the time spent retrieving and providing the data.\n"
        "(f) We are entitled to retain your documents while there is money owing to "
        "us for our costs.",
    ),
    (
        "9 Termination by Us",
        "We may cease to act for you or refuse to perform further work, including "
        "while any of our tax invoices remain unpaid;\n"
        "(a) if you do not within 7 days comply with any request to pay an amount in "
        "respect of disbursements or future costs;\n"
        "(b) if you fail to provide us with clear and timely instructions to enable "
        "us to advance your matter;\n"
        "(c) if you refuse to accept our advice;\n"
        "(d) if you indicate to us or we form the view that you have lost confidence "
        "in us;\n"
        "(e) if there are any ethical grounds which we consider require us to cease "
        "acting for you, for example a conflict of interest;\n"
        "(f) for any other reason outside our control which has the effect of "
        "compromising our ability to perform the work required within the required "
        "timeframe; or\n"
        "(g) if in our sole discretion we consider it is no longer appropriate to "
        "act for you; or\n"
        "(h) for just cause.\n"
        "We will give you reasonable written notice of termination of our services. "
        "You will be required to pay our costs incurred up to the date of termination.",
    ),
    (
        "10 Termination by You",
        "You may terminate our services by written notice at any time. However, if "
        "you do so you will be required to pay our costs incurred up to the date of "
        "termination.",
    ),
    (
        "11 Lien",
        "Without affecting any lien to which we are otherwise entitled at law over "
        "funds, papers and other property of yours:\n"
        "(a) we shall be entitled to retain by way of lien any funds, property or "
        "papers of yours, which are from time to time in our possession or control, "
        "until all costs, disbursements, interest and other moneys due to the firm "
        "have been paid; and\n"
        "(b) our lien will continue notwithstanding that we cease to act for you.",
    ),
    (
        "12 Privacy",
        "We will collect personal information from you in the course of providing "
        "our legal services. We may also obtain personal information from third "
        "party searches, other investigations and, sometimes, from adverse parties. "
        "We are required to collect the full name and address of our clients by "
        "Rule 93 of the Uniform General Rules. Your personal information will only "
        "be used for the purposes for which it is collected or in accordance with "
        "the Privacy Act 1988 (Cth). We manage and protect your personal information "
        "in accordance with our privacy policy which can be found on our firm "
        "website or a copy of which we shall provide at your request.",
    ),
    (
        "13 Sending Material Electronically",
        "We are able to send and receive documents electronically. However, as such "
        "transmission is not secure it may be copied, recorded, read or interfered "
        "with by third parties while in transit. If you ask us to transmit any "
        "document electronically, you release us from any claim you may have as a "
        "result of any unauthorised copying, recording, reading or interference with "
        "that document, for any delay or non-delivery of any document and for any "
        "damage caused to your system or any files.",
    ),
    (
        "14 GST",
        "Where applicable, GST is payable on our professional fees and expenses and "
        "will be clearly shown on our tax invoices. By accepting these terms you "
        "agree to pay us an amount equivalent to the GST imposed on these charges.",
    ),
    (
        "15 Governing Law",
        "The law of New South Wales governs these terms and legal costs in relation "
        "to any matter upon which we are instructed to act.",
    ),
]


def _general_terms() -> list:
    flows: list = []
    for i, (heading, body) in enumerate(_GENERAL_TERMS):
        is_title = i == 0
        style = L.STYLE_TERMS_TITLE if is_title else L.STYLE_TERMS_HEAD
        flows.append(PT(heading, style))
        flows.append(Spacer(1, 6 if is_title else 4))
        if not body:
            continue
        for para in body.split("\n"):
            flows.append(PT(para, L.STYLE_TERMS_BODY))
            flows.append(Spacer(1, 2))
        flows.append(Spacer(1, 6))
    return flows
