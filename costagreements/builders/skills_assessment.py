"""Builds the Skills Assessment (full pathway) Cost Agreement PDF -- Skills
Assessment + State Nomination + full Skilled Visa Application, bundled into
a single agreement.

Content (every clause, bullet, and disclosure paragraph) is transcribed
verbatim from winzoylegal_new's
src/features/skillsAssessmentCostAgreement/buildSkillsAssessmentCostAgreementPdf.ts
(schema mirrored from that feature's types.ts) -- only the layout mechanism
changed. See costagreements/layout.py for why: ReportLab Platypus
flowables replace pdf-lib's manual y-coordinate bookkeeping, which is what
caused winzoylegal_new's long-running client-initials/signature/date
spacing bugs.

NOT THE SAME TYPE AS skills_assessment_only.py
------------------------------------------------
``skills_assessment_only.py`` covers *only* the skills-assessment step: no
visa/nomination fields, a 2-stage payment schedule, and a compact
signature block. This module is the fuller "assessment + nomination +
visa" pathway confirmed via ``diff`` between the two TS ``types.ts``
files -- it additionally carries:
  - ``anzsco_code``, ``visa_subclass``, ``nomination_state``,
    ``state_nomination_fee``, ``visa_application_fee``,
    ``nomination_processing_time``, ``visa_processing_time`` (all absent
    from skills_assessment_only's schema);
  - a 4-stage payment schedule (``payment_stage1``..``payment_stage4``
    label/amount) instead of skills_assessment_only's 2 stages;
  - the full "SIGNATURES" banner signature block (``components.
    signature_block()``) rather than skills_assessment_only's compact,
    banner-less variant (``components.compact_signature_block()``);
  - a VAC-surcharge-inclusive Cost Summary total (via
    ``costagreements/money.py``'s ``apply_vac_surcharge``), matching the
    nomination/visa fee math skilled_visa.py already ports, folded in
    alongside the skills-assessment-specific disbursement fees.
skilled_visa.py is the closer sibling for the nomination/visa-shaped
fields (anzsco/visa_subclass/nomination_state/state_nomination_fee/
visa_application_fee) -- this module reuses that shape verbatim for those
fields, and skills_assessment_only.py's shape for the assessment-specific
fields (occupation/anzsco_code/assessing_authority/
skill_assessment_authority_fee/priority_processing_fee/
skill_assessment_processing_time). jrp.py's 4-stage payment-schedule-table
pattern (plain bordered label/amount rows, no header row) is reused for
this type's 4 payment stages.

Structural decisions where the TS source was ambiguous or the two sibling
ports disagreed:
  - ``REQUIRED_FIELDS``: types.ts marks almost every field as required
    (including anzscoCode, skillAssessmentAuthorityFee, marn, lpn,
    totalCost, lodgementUsesClientCard, and all four payment-stage
    label/amount pairs) but neither sibling builder treats its own TS
    type that literally -- skilled_visa.py's docstring calls this out
    explicitly and excludes booleans, derived/echoed totals, marn/lpn,
    and payment-stage fields from its own REQUIRED_FIELDS even where
    types.ts marks them non-optional. This port follows skilled_visa.py's
    precedent (the closer sibling for the visa-shaped half of this type)
    over skills_assessment_only.py's stricter precedent (which *does*
    require its 2 payment-stage label/amount pairs): only the fields
    load-bearing for page 1's body text to render sensibly are required
    -- date/our_ref/client_name/client_address/capacity,
    occupation/assessing_authority, visa_subclass/nomination_state/
    professional_fee/state_nomination_fee/visa_application_fee/
    nomination_processing_time/visa_processing_time, rep_name.
    anzsco_code, skill_assessment_authority_fee,
    priority_processing_fee, skill_assessment_processing_time,
    lodgement_uses_client_card, total_cost, marn, lpn, and all four
    payment-stage label/amount pairs have sensible fallback text/defaults
    in ``_build_story`` exactly like every sibling's own optional fields.
  - ``rep_signature_data``: not present in types.ts (which only has
    ``repSignatureUrl``, fetched directly by the TS builder). Added here
    anyway, matching every sibling builder's architecture decision:
    fetching a staff-supplied URL server-side would be an SSRF vector, so
    only a base64 data URI (``rep_signature_data``) is ever drawn, and
    ``rep_signature_url`` is metadata-passthrough-only into SIGMETA.

This is a synchronous, single-call generator: the returned PDF is the
final document. There is no SIGMETA-based post-signing remote-stamp step
beyond metadata embedding -- winzoylegal_new's own two-stage e-signing
flow (tokens, pending/signed status) is out of scope for this Flask port.
If a signature image isn't supplied in the payload, the signature box
renders empty/bordered for print-and-sign.
"""
from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime

from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import BaseDocTemplate, Frame, PageBreak, PageTemplate, Spacer, Table, TableStyle

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
    cost_summary_table,
    decode_data_uri,
    esc,
    parties_table,
    signature_block,
    staff_note_box,
    two_column_terms_box,
)
from ..money import apply_vac_surcharge, fmt_amt, parse_amt, sum_amounts
from ..sigmeta import SigMetaState


# --------------------------------------------------------------------- schema
@dataclass
class SkillsAssessmentCostAgreementData:
    """Payload shape for the (full-pathway) Skills Assessment Cost
    Agreement, mirroring winzoylegal_new's skillsAssessmentCostAgreement/
    types.ts field-for-field (snake_case to match this API's existing JSON
    convention)."""

    # Header
    date: str  # DD/MM/YYYY (or YYYY-MM-DD, normalised before this is built)
    our_ref: str
    client_name: str
    client_address: str

    # Capacity of representative (top of page 1)
    capacity: str  # "solicitor" | "rma"

    # Scope of work / assessment fields
    occupation: str
    assessing_authority: str      # e.g. "Vetassess", "ACS", "Engineers Australia"

    # Scope of work / visa+nomination fields
    visa_subclass: str            # 491 / 190 / 189
    nomination_state: str         # e.g. "NSW"

    # Fees
    professional_fee: str
    state_nomination_fee: str     # e.g. "370"
    visa_application_fee: str     # e.g. "4770"

    # Processing times
    nomination_processing_time: str  # e.g. "6 - 8 weeks"
    visa_processing_time: str        # e.g. "3 - 15 months"

    rep_name: str

    anzsco_code: str = ""                     # e.g. "261313"
    skill_assessment_authority_fee: str = ""  # e.g. "1100"
    priority_processing_fee: str = ""         # optional
    skill_assessment_processing_time: str = ""  # e.g. "3 - 4 months (priority: 10 working days)"

    lodgement_uses_client_card: bool = False
    total_cost: str = ""  # present in types.ts; not used in totals math (cost summary is derived, not echoed)

    # Payment schedule (4 editable stages + optional extra stages)
    payment_stage1_label: str = ""
    payment_stage1_amount: str = ""
    payment_stage2_label: str = ""
    payment_stage2_amount: str = ""
    payment_stage3_label: str = ""
    payment_stage3_amount: str = ""
    payment_stage4_label: str = ""
    payment_stage4_amount: str = ""
    extra_stages: list[dict] = field(default_factory=list)  # [{"label": str, "amount": str}, ...]

    # Signature block
    marn: str = ""
    lpn: str = ""
    rep_signature_data: str | None = None       # base64 data URI -- see validate_skills_assessment_cost_agreement()
    client_signature_data: str | None = None     # base64 data URI
    rep_signature_url: str | None = None         # metadata passthrough only, never fetched -- see sigmeta.py

    staff_note: str = ""
    ack_languages: list[str] = field(default_factory=list)
    translation_banner_text: str = ""
    translated_ack_text: str = ""
    include_annexure_d: bool = False

    @classmethod
    def from_payload(cls, payload: dict) -> "SkillsAssessmentCostAgreementData":
        ack_languages = payload.get("ack_languages") or []
        if isinstance(ack_languages, str):
            ack_languages = [ack_languages] if ack_languages.strip() else []

        extra_stages_raw = payload.get("extra_stages") or []
        extra_stages: list[dict] = []
        if isinstance(extra_stages_raw, list):
            for item in extra_stages_raw:
                if isinstance(item, dict):
                    extra_stages.append({
                        "label": str(item.get("label") or ""),
                        "amount": str(item.get("amount") or ""),
                    })

        return cls(
            date=str(payload.get("date") or ""),
            our_ref=str(payload.get("our_ref") or ""),
            client_name=str(payload.get("client_name") or ""),
            client_address=str(payload.get("client_address") or ""),
            capacity=str(payload.get("capacity") or ""),
            occupation=str(payload.get("occupation") or ""),
            assessing_authority=str(payload.get("assessing_authority") or ""),
            visa_subclass=str(payload.get("visa_subclass") or ""),
            nomination_state=str(payload.get("nomination_state") or ""),
            professional_fee=str(payload.get("professional_fee") or ""),
            state_nomination_fee=str(payload.get("state_nomination_fee") or ""),
            visa_application_fee=str(payload.get("visa_application_fee") or ""),
            nomination_processing_time=str(payload.get("nomination_processing_time") or ""),
            visa_processing_time=str(payload.get("visa_processing_time") or ""),
            rep_name=str(payload.get("rep_name") or ""),
            anzsco_code=str(payload.get("anzsco_code") or ""),
            skill_assessment_authority_fee=str(payload.get("skill_assessment_authority_fee") or ""),
            priority_processing_fee=str(payload.get("priority_processing_fee") or ""),
            skill_assessment_processing_time=str(payload.get("skill_assessment_processing_time") or ""),
            lodgement_uses_client_card=bool(payload.get("lodgement_uses_client_card", False)),
            total_cost=str(payload.get("total_cost") or ""),
            payment_stage1_label=str(payload.get("payment_stage1_label") or ""),
            payment_stage1_amount=str(payload.get("payment_stage1_amount") or ""),
            payment_stage2_label=str(payload.get("payment_stage2_label") or ""),
            payment_stage2_amount=str(payload.get("payment_stage2_amount") or ""),
            payment_stage3_label=str(payload.get("payment_stage3_label") or ""),
            payment_stage3_amount=str(payload.get("payment_stage3_amount") or ""),
            payment_stage4_label=str(payload.get("payment_stage4_label") or ""),
            payment_stage4_amount=str(payload.get("payment_stage4_amount") or ""),
            extra_stages=extra_stages,
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


# --------------------------------------------------------------------- validation
REQUIRED_FIELDS = frozenset({
    "date", "our_ref", "client_name", "client_address", "capacity",
    "occupation", "assessing_authority",
    "visa_subclass", "nomination_state",
    "professional_fee", "state_nomination_fee", "visa_application_fee",
    "nomination_processing_time", "visa_processing_time",
    "rep_name",
})

VALID_CAPACITIES = frozenset({"solicitor", "rma"})
MONEY_FIELDS = frozenset({
    "professional_fee", "skill_assessment_authority_fee", "priority_processing_fee",
    "state_nomination_fee", "visa_application_fee",
    "payment_stage1_amount", "payment_stage2_amount",
    "payment_stage3_amount", "payment_stage4_amount",
    "total_cost",
})
IMAGE_FIELDS = frozenset({"client_signature_data", "rep_signature_data"})


def validate_skills_assessment_cost_agreement(payload: dict) -> list[ValidationError]:
    """Validate a (full-pathway) Skills Assessment Cost Agreement payload.
    Empty list = valid."""
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

    extra_stages = payload.get("extra_stages") or []
    if isinstance(extra_stages, list):
        for i, item in enumerate(extra_stages):
            if not isinstance(item, dict):
                errs.append(ValidationError(
                    field="extra_stages", code="format",
                    message=f"extra_stages[{i}] must be an object with 'label'/'amount'",
                ))
                continue
            amt = item.get("amount")
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


def apply_skills_assessment_normalisations(payload: dict) -> dict:
    """Return a shallow copy of ``payload`` with 'date' normalised to DD/MM/YYYY."""
    out = dict(payload)
    v = out.get("date")
    if isinstance(v, str) and DATE_YYYYMMDD.match(v):
        out["date"] = normalise_date(v)
    return out


# --------------------------------------------------------------------- builder
_STYLE_ITALIC_SMALL = ParagraphStyle(
    "SA_ItalicSmall", fontName=L.FONT_ITALIC, fontSize=9.5, leading=12.5, textColor=L.BLACK,
)
_STYLE_DISB_CELL = ParagraphStyle(
    "SA_DisbCell", fontName=L.FONT_REGULAR, fontSize=8.5, leading=11, textColor=L.BLACK,
)


def build_skills_assessment_cost_agreement(data: SkillsAssessmentCostAgreementData) -> bytes:
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
        chrome.draw_header(canvas, doc, doc_id, generated_at, "SKILLS ASSESSMENT + SKILLED VISA")

    template = PageTemplate(id="main", frames=[frame], onPage=on_page)
    doc = BaseDocTemplate(
        buf, pagesize=(L.PAGE_W, L.PAGE_H), pageTemplates=[template],
        title="Costs Disclosure and Costs Agreement (Skills Assessment + Skilled Visa)", author="Winzoy Legal",
        topMargin=0, bottomMargin=0, leftMargin=0, rightMargin=0,
    )
    doc.build(story, canvasmaker=chrome.make_canvas_factory(doc_id, generated_at))
    return buf.getvalue()


def _make_doc_id(data: SkillsAssessmentCostAgreementData) -> str:
    seed = f"SA|{data.our_ref}|{data.client_name}|{time.time()}"
    h = 0
    for ch in seed:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    hex_str = format(h, "08x").upper()
    return f"WZL-SA-{hex_str[:4]}-{hex_str[4:8]}"


def _build_ack_text(client_name: str, languages: list[str]) -> str:
    langs = [lang for lang in (languages or []) if lang] or ["English"]
    lang_list = " and/or ".join(langs)
    name_phrase = f'I, "{client_name}",' if client_name and client_name.strip() else "I"
    return (
        f"{name_phrase} acknowledge that I understand and accepted the above agreement. "
        f"The agreement has been explained to me and my partner in {lang_list}."
    )


def _estimate_table(data: SkillsAssessmentCostAgreementData, vac_surcharged: float) -> Table:
    """The 'D. Estimate of Professional Fees and Internal Expenses' 3-row
    table -- matches buildSkillsAssessmentCostAgreementPdf.ts's own
    drawTableRow() calls (blank/'Amount' shaded header, centred amount
    column). Row 2 (Disbursement Lodgement Fees) has a multi-line right
    cell of four bulleted sub-lines (assessment authority fee, priority
    processing fee, state nomination fee, VAC incl. surcharge) -- no intro
    sentence, unlike skilled_visa.py's equivalent 2-bullet row."""
    w1 = L.CONTENT_W * 0.50
    w2 = L.CONTENT_W * 0.50
    head_amt_style = ParagraphStyle("SA_EstimateHeadAmt", fontName=L.FONT_BOLD, fontSize=10, alignment=1)
    amt_style = ParagraphStyle("SA_EstimateAmt", fontName=L.FONT_REGULAR, fontSize=10, alignment=1)

    authority_fee_text = f"${fmt_amt(data.skill_assessment_authority_fee)}"
    priority_fee_text = f"${data.priority_processing_fee}" if data.priority_processing_fee else "as advised"
    disb_bullets = [
        f"Skill Assessment Authority Fee ({data.assessing_authority or 'e.g. Vetassess'}): {authority_fee_text}",
        f"Priority Processing Fee (if applicable): {priority_fee_text}",
        f"State Nomination fee (visa {data.visa_subclass or '491/190'}): "
        f"${fmt_amt(data.state_nomination_fee or '370')} (varies by state)",
        f"Visa Application Charge (VAC) incl. 1.4% surcharge: ${fmt_amt(vac_surcharged)} "
        f"(main applicant, visa {data.visa_subclass or '491/190/189'})",
    ]
    disb_label = f"2. Disbursement Lodgement Fees{' (using client card)' if data.lodgement_uses_client_card else ''}"

    data_rows = [
        ["", PT("Amount", head_amt_style)],
        [PT("1. Professional Cost", L.STYLE_BODY),
         P(f"${esc(fmt_amt(data.professional_fee))} inclusive of GST", amt_style)],
        [PT(disb_label, L.STYLE_BODY), P(bulleted_html(disb_bullets), _STYLE_DISB_CELL)],
    ]
    t = Table(data_rows, colWidths=[w1, w2])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, L.BLACK),
        ("BACKGROUND", (0, 0), (-1, 0), L.GRAY),
        ("VALIGN", (0, 0), (-1, 1), "MIDDLE"),
        ("VALIGN", (0, 2), (-1, 2), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def _payment_schedule_table(stages: list[tuple[str, str]]) -> Table:
    """A bordered label/amount table with no header row -- matches
    buildSkillsAssessmentCostAgreementPdf.ts's 'F. Payment Schedule for
    Cost and Disbursement' rows (4 default stages + optional extras)."""
    w1 = L.CONTENT_W * 0.70
    w2 = L.CONTENT_W * 0.30
    amt_style = ParagraphStyle("SA_PaySchedAmt", fontName=L.FONT_REGULAR, fontSize=10.5, alignment=1)
    data = [[PT(label, L.STYLE_TABLE_CELL), P(esc(amount_text), amt_style)] for label, amount_text in stages]
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


def _build_payment_stages(data: SkillsAssessmentCostAgreementData) -> list[tuple[str, str]]:
    stages = [
        (data.payment_stage1_label or "1. Skill Assessment", f"${fmt_amt(data.payment_stage1_amount)}"),
        (data.payment_stage2_label or "2. EOI lodgement", f"${fmt_amt(data.payment_stage2_amount)}"),
        (data.payment_stage3_label or "3. Nomination", f"${fmt_amt(data.payment_stage3_amount)}"),
        (data.payment_stage4_label or "4. Visa application", f"${fmt_amt(data.payment_stage4_amount)}"),
    ]
    for i, extra in enumerate(data.extra_stages):
        label = extra.get("label") or f"{4 + i + 1}. Stage"
        amount = f"${fmt_amt(extra.get('amount'))}"
        stages.append((label, amount))
    return stages


def _build_story(data: SkillsAssessmentCostAgreementData, today_short: str) -> list:
    story: list = []

    # ═══════════════════════════════════════ PAGE 1 — Agreement details
    if data.translation_banner_text:
        banner_style = ParagraphStyle(
            "SA_TransBanner", fontName=L.FONT_REGULAR, fontSize=8, leading=14,
            textColor=L.WHITE, backColor=L.TRANSLATION_BANNER_BG, leftIndent=8,
        )
        story.append(PT(data.translation_banner_text, banner_style))
        story.append(Spacer(1, 10))

    story.append(P("Costs Disclosure and Costs Agreement", L.STYLE_TITLE))
    story.append(Spacer(1, 6))
    date_style = ParagraphStyle("SA_DateLine", fontName=L.FONT_REGULAR, fontSize=10.5, alignment=2)
    story.append(P(f'<font color="{L.hex_of(L.MUTED)}">Date:</font> {esc(data.date)}', date_style))
    story.append(Spacer(1, 10))
    story.append(P("Between", L.STYLE_CENTER_BOLD))
    story.append(Spacer(1, 16))

    story.append(parties_table(data.date, data.our_ref, data.client_name, data.client_address))
    story.append(Spacer(1, 24))

    # ── Intro paragraph ─────────────────────────────
    story.append(PT(
        "This document, together with our General Terms of Business, sets out the "
        "terms of our offer to provide legal services to you and constitutes our "
        'costs agreement and disclosure pursuant to the Legal Profession Uniform '
        'Law (NSW) ("the Uniform Law").',
        L.STYLE_BODY,
    ))
    story.append(Spacer(1, 12))

    # ── Capacity of representative ──────────────────
    story.append(P("CAPACITY OF REPRESENTATIVE", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(PT(
        "The services provided under this agreement are being rendered by Winzoy Legal "
        "through the specific representative designated in the signature block. Please "
        "note the capacity in which your representative is acting:",
        L.STYLE_BODY,
    ))
    story.append(Spacer(1, 8))
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
    story.append(Spacer(1, 10))

    # ── A. Scope of Work ─────────────────────────────
    story.append(P("A. Scope of Work", L.STYLE_H2))
    story.append(Spacer(1, 4))
    occ_text = (
        f"{data.occupation}{f' (ANZSCO {data.anzsco_code})' if data.anzsco_code else ''}"
        if data.occupation else "_________________________"
    )
    story.append(PT(
        f"1. You have instructed us to lodge your Skills Assessment, EOI, nomination and "
        f"application for your Australian skilled visa (Subclass {data.visa_subclass or '___'}) "
        f"for the occupation {occ_text}.",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 10))

    # ── B. Professional Fees ──────────────────────────
    story.append(P("B. Professional Fees", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(PT(
        f"I will charge you professional fees for the work I do on a fixed fee of: "
        f"${fmt_amt(data.professional_fee)} inclusive of GST.",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 10))

    # ── C. Disbursements and Internal Expenses ────────
    story.append(P("C. Disbursements and Internal Expenses", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(PT(
        f"You will need to pay Disbursement Lodgement Fees to the "
        f"{data.assessing_authority or 'relevant Assessing Authority'} and the Department "
        "before the lodgement of your application. We will notify you of these "
        "Disbursements and you are required to pay them accordingly or instruct us to "
        "assist you with the payment directly.",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 12))

    # ── D. Estimate of Professional Fees and Internal Expenses ──
    story.append(P("D. Estimate of Professional Fees and Internal Expenses", L.STYLE_H2))
    story.append(Spacer(1, 4))

    vac_surcharged = apply_vac_surcharge(data.visa_application_fee or "4770")
    story.append(_estimate_table(data, vac_surcharged))
    story.append(Spacer(1, 10))

    professional_cost = parse_amt(data.professional_fee)
    disbursements_cost = sum_amounts(
        data.skill_assessment_authority_fee, data.priority_processing_fee, data.state_nomination_fee,
    ) + vac_surcharged
    story.append(cost_summary_table(professional_cost, disbursements_cost, total_suffix=" incl GST"))
    story.append(Spacer(1, 14))

    # ── Variables ─────────────────────────────────────
    story.append(P("Variables", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(PT("Some of the variables which may affect and change the costs estimate include:", L.STYLE_BODY))
    story.append(Spacer(1, 2))
    story.append(P(bulleted_html([
        "(a) your prompt and efficient response to requests for information or instructions;",
        "(b) whether your instructions are varied;",
        "(c) whether documents have to be revised in light of varied instructions;",
        "(d) changes in the law; and",
        "(e) the complexity or uncertainty concerning legal issues affecting your matter.",
    ]), L.STYLE_BODY))
    story.append(Spacer(1, 8))
    story.append(PT(
        "Please note that this is an estimate only and not a fixed quote. The total "
        "costs may exceed the estimate. In the event costs change, we will notify "
        "you immediately.",
        L.STYLE_BODY,
    ))
    story.append(Spacer(1, 12))

    # ── F. Payment Schedule for Cost and Disbursement ──
    story.append(P("F. Payment Schedule for Cost and Disbursement", L.STYLE_H2))
    story.append(Spacer(1, 6))
    story.append(_payment_schedule_table(_build_payment_stages(data)))
    story.append(Spacer(1, 10))
    story.append(PT(
        "Note: By submitting an EOI and accepting an invitation from the Department, "
        "the applicant commits to the full payment of fees. This commitment remains "
        "in effect even if the applicant chooses to withdraw from the application "
        "process at a later stage.",
        _STYLE_ITALIC_SMALL,
    ))
    story.append(Spacer(1, 14))

    # ── G. Breach of Payment Schedule and Termination ──
    story.append(P("G. Breach of Payment Schedule and Termination", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(PT(
        "We may cease to act for you or refuse to perform further work, including if "
        "you do not within 7 days comply with any request to pay an amount in "
        "respect of disbursements or future costs as outlined in the schedule above. "
        "You may terminate our services by written notice at any time. However, if "
        "you do so you will be required to pay our costs incurred up to the date of "
        "termination.",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 14))

    # ── H. Processing Times and Outcome ────────────────
    story.append(P("H. Processing Times and Outcome", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(PT(
        f"Skills assessment by {data.assessing_authority or 'Vetassess'} – Standard "
        "processing time is approximately "
        f"{data.skill_assessment_processing_time or 'Depends upon DoHA Global Processing Times'}, "
        "whereas priority processing time is usually 10 working days upon approval for "
        "priority processing.",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 8))
    story.append(PT(
        f"Based on current processing times, once you receive the invitation to "
        f"lodge a skilled visa (Subclass {data.visa_subclass or '491/190/189'}), the "
        f"processing time for Nomination in {data.nomination_state or 'NSW'} is "
        f"between {data.nomination_processing_time or 'Depends upon DoHA Global Processing Times'} "
        f"and the processing time for the visa application is between "
        f"{data.visa_processing_time or 'Depends upon DoHA Global Processing Times'} "
        f"from the date of the formal lodgment.",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 8))
    story.append(PT(
        "The processing times are not definitive and are completely dependent on the "
        "relevant Assessing Authority and the Department of Home Affairs.",
        _STYLE_ITALIC_SMALL,
    ))
    story.append(Spacer(1, 8))
    story.append(PT(
        "You will make careful note and independent judgment after your discussion "
        "with our firm, whether to proceed with your case for a particular visa. We "
        "are duty bound to advise that any application, the success of winning or "
        "being granted a visa, and the overall performance of your case will be "
        "largely dependent upon many factors such as but not limited to:",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 4))
    story.append(P(bulleted_html([
        "a) The complete evidence being made or submitted at the time of the "
        "application, and at the time of the decision-making process, which "
        "includes any information being supplied by a third party;",
        "b) The merit of the applicant's case, and the grounds upon which the "
        "decision by the Department of Immigration had made in respect of the visa "
        "application;",
        "c) The relevance of the applicant's character;",
        "d) The relevance of the applicant's health factors;",
        "e) The relevance of your full and complete disclosure as required under "
        "the Migration Act 1958;",
        "f) The applicant's past migration history.",
    ]), _STYLE_ITALIC_SMALL))
    story.append(Spacer(1, 8))
    story.append(PT(
        "Every individual case requires the consideration of merits and the legal "
        "grounds. We undertake to perform to the best of our ability in order to "
        "obtain a favourable outcome on your case, but offer no promise or guarantee "
        "on the probabilities of complete success having regard to the aforementioned "
        "factors.",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 14))

    story.append(PT("Payment of estimated legal fees to the account below:", L.STYLE_ITALIC_MUTED))
    story.append(Spacer(1, 6))
    story.append(bank_details_box())
    story.append(Spacer(1, 20))

    story.append(P("Acknowledgement and Acceptance of Offer", L.STYLE_H2))
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
    story.append(signature_block(
        client_name=data.client_name, rep_name=data.rep_name,
        capacity=data.capacity, lpn=data.lpn, marn=data.marn,
        client_sig_bytes=client_sig_bytes, rep_sig_bytes=rep_sig_bytes,
        signed_date_text=signed_date, today_text=today_short,
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


def _what_we_you_must_do() -> list:
    wmd = [
        "Act in your best legitimate interests, with honesty, fairness and integrity. "
        "Ensure that our advice is timely and accurate.",
        "Do nothing to increase your costs unnecessarily. Keep accurate and complete "
        "records of your case. Do everything reasonably necessary to perform the "
        "services listed in this agreement where the services are not listed in full details.",
        "Provide you with advice about the processes, issues and legal requirements "
        "involved in your Skills Assessment and visa application.",
        "Lodge the Skills Assessment, EOI, nomination and visa applications to the "
        "relevant Assessing Authority, State authority and the Department of Home "
        "Affairs, with supporting documentation, liaise with you as to necessary "
        "application, and inform of the result.",
        "Professionally prepare your Visa Application form, and any other forms "
        "incidental to the substantive application processes, send these forms to you "
        "for signature. Lodge these forms, with the required supporting documentation "
        "and fees to the appropriate office.",
        "Inform you of the outcome of your visa application.",
    ]
    ymd = [
        "Let us know about changes to your circumstances that might affect your "
        "application, for example, marriage or the birth of a child. Advise us "
        "promptly if you change your address for more than fourteen consecutive days "
        "during the processing of a visa application.",
        "Provide us, in a timely manner, with documents and information that we need "
        "to act for you. Ensure that the information you give us is true and accurate. "
        "If you discover that information you gave us is wrong, let us know at once so "
        "we can make the necessary corrections immediately.",
        "You should be aware that if DoHA discover that you, or anyone else, gave them "
        "information that was false or misleading in a material particular they can "
        "cancel your visa, even if you did not know that the information was false or misleading.",
        "Provide us with the documents described in the attached letters and Documents "
        "Listed or as per our request to you via our correspondences and telephone "
        "attendances, or our previous conference at the office.",
    ]
    ila_head_style = ParagraphStyle("SA_ILA", fontName=L.FONT_BOLD, fontSize=9, textColor=L.NAVY)
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
            "c) No Guarantee of Visa Outcome (Applicable to both):",
            "While the representative and Winzoy Legal will perform the agreed work "
            "with professional diligence and competence, the Client acknowledges that "
            "the firm cannot guarantee the successful approval of any visa application, "
            "as all final decisions rest solely with the Department of Home Affairs.",
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
        "We are required to collect the full name and address of our clients by Rule "
        "93 of the Uniform General Rules. Your personal information will only be "
        "used for the purposes for which it is collected or in accordance with the "
        "Privacy Act 1988 (Cth). We manage and protect your personal information in "
        "accordance with our privacy policy which can be found on our firm website "
        "or a copy of which we shall provide at your request.",
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
