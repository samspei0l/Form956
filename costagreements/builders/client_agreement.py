"""Builds the (legacy) Client Agreement PDF.

Content is transcribed verbatim from winzoylegal_new's
src/features/costAgreement/buildCostAgreementPdf.ts (+ its sibling
types.ts) -- only the layout mechanism changed. See
costagreements/layout.py for why: ReportLab Platypus flowables replace
pdf-lib's manual y-coordinate bookkeeping, which is what caused
winzoylegal_new's long-running client-initials/signature/date spacing
bugs.

This is the *original/legacy* "Client Agreement" cost-agreement type --
simpler than General Cost Agreement: it has no "WHAT WE/YOU MUST DO",
"REGULATORY COMPLIANCE" or "GENERAL TERMS OF BUSINESS" sections, and only
optionally appends Annexure D (Code of Conduct / Consumer Guide
acknowledgement) -- it never calls the shared Annexure A/B/C helper the
way General Cost Agreement does (confirmed from the TS source: it only
imports ``appendAnnexureD``, not ``appendCostAgreementAnnexures``).

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
    checkbox_row,
    cost_summary_table,
    decode_data_uri,
    disbursement_table,
    esc,
    parties_table,
    signature_block,
    staff_note_box,
    works_fee_table,
)
from ..money import apply_vac_surcharge, fmt_amt, parse_amt, sum_amounts
from ..sigmeta import SigMetaState


# =============================================================================
# Schema -- mirrors winzoylegal_new's costAgreement/types.ts field-for-field
# (snake_case to match this API's existing JSON convention). ``rep_signature_data``
# is added even though types.ts has no such field, matching the pattern
# General Cost Agreement's schema already established: ``rep_signature_url``
# is metadata-passthrough only (never fetched server-side -- SSRF risk),
# while an actual signature image must arrive as a base64 data URI in
# ``rep_signature_data``, drawn directly.
# =============================================================================
@dataclass
class ClientAgreementData:
    # Header
    date: str  # DD/MM/YYYY (or YYYY-MM-DD, normalised before this is built)
    our_ref: str
    client_name: str
    client_address: str

    # Capacity of representative (top of page 1)
    capacity: str  # "solicitor" | "rma"

    # Works / fee table
    visa_type: str
    professional_fee: str
    estimated_weeks: str

    # Disbursement table
    service_fee: str
    rep_name: str

    visa_application_charges: list[str] = field(default_factory=list)
    total_cost: str = ""  # carried through from types.ts; unused by the builder itself

    # Signature block
    marn: str = ""
    lpn: str = ""
    client_signature_data: str | None = None     # base64 data URI
    signed_at: str | None = None                  # carried through from types.ts; unused by the builder itself
    rep_signature_data: str | None = None         # base64 data URI -- see module docstring
    rep_signature_url: str | None = None          # metadata passthrough only, never fetched -- see sigmeta.py

    staff_note: str = ""
    ack_languages: list[str] = field(default_factory=list)
    translation_banner_text: str = ""
    translated_ack_text: str = ""
    include_annexure_d: bool = False

    @classmethod
    def from_payload(cls, payload: dict) -> "ClientAgreementData":
        charges = payload.get("visa_application_charges") or []
        if isinstance(charges, str):
            charges = [c.strip() for c in charges.split("\n") if c.strip()]

        ack_languages = payload.get("ack_languages") or []
        if isinstance(ack_languages, str):
            ack_languages = [ack_languages] if ack_languages.strip() else []

        return cls(
            date=str(payload.get("date") or ""),
            our_ref=str(payload.get("our_ref") or ""),
            client_name=str(payload.get("client_name") or ""),
            client_address=str(payload.get("client_address") or ""),
            capacity=str(payload.get("capacity") or ""),
            visa_type=str(payload.get("visa_type") or ""),
            professional_fee=str(payload.get("professional_fee") or ""),
            estimated_weeks=str(payload.get("estimated_weeks") or ""),
            service_fee=str(payload.get("service_fee") or ""),
            rep_name=str(payload.get("rep_name") or ""),
            visa_application_charges=list(charges),
            total_cost=str(payload.get("total_cost") or ""),
            marn=str(payload.get("marn") or ""),
            lpn=str(payload.get("lpn") or ""),
            client_signature_data=payload.get("client_signature_data") or None,
            signed_at=payload.get("signed_at") or None,
            rep_signature_data=payload.get("rep_signature_data") or None,
            rep_signature_url=payload.get("rep_signature_url") or None,
            staff_note=str(payload.get("staff_note") or ""),
            ack_languages=list(ack_languages),
            translation_banner_text=str(payload.get("translation_banner_text") or ""),
            translated_ack_text=str(payload.get("translated_ack_text") or ""),
            include_annexure_d=bool(payload.get("include_annexure_d", False)),
        )


# =============================================================================
# Validation -- reuses pdfform.validate's date regexes/ValidationError/
# normalise_date, same DD/MM/YYYY <-> YYYY-MM-DD handling Form 956 already
# does. Mirrors costagreements/validate.py's shape/pattern exactly.
# =============================================================================
REQUIRED_FIELDS = frozenset({
    "date", "our_ref", "client_name", "client_address", "capacity",
    "visa_type", "professional_fee", "estimated_weeks", "service_fee",
    "rep_name",
})

VALID_CAPACITIES = frozenset({"solicitor", "rma"})
MONEY_FIELDS = frozenset({"professional_fee", "service_fee"})
IMAGE_FIELDS = frozenset({"client_signature_data", "rep_signature_data"})


def validate_client_agreement(payload: dict) -> list[ValidationError]:
    """Validate a Client Agreement payload. Empty list = valid."""
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

    charges = payload.get("visa_application_charges") or []
    if isinstance(charges, str):
        charges = [c.strip() for c in charges.split("\n") if c.strip()]
    for i, c in enumerate(charges):
        if c in (None, ""):
            continue
        if parse_amt(c) < 0:
            errs.append(ValidationError(
                field="visa_application_charges", code="value",
                message=f"visa_application_charges[{i}] must not be negative",
            ))

    for f in IMAGE_FIELDS:
        v = payload.get(f)
        if v and not (isinstance(v, str) and v.startswith("data:image/")):
            errs.append(ValidationError(
                field=f, code="format",
                message=f"{f!r} must be a base64 image data URI (data:image/png;base64,...)",
            ))

    return errs


def apply_client_agreement_normalisations(payload: dict) -> dict:
    """Return a shallow copy of ``payload`` with 'date' normalised to DD/MM/YYYY."""
    out = dict(payload)
    v = out.get("date")
    if isinstance(v, str) and DATE_YYYYMMDD.match(v):
        out["date"] = normalise_date(v)
    return out


# =============================================================================
# Builder
# =============================================================================
def build_client_agreement(data: ClientAgreementData) -> bytes:
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
        chrome.draw_header(canvas, doc, doc_id, generated_at, "")

    template = PageTemplate(id="main", frames=[frame], onPage=on_page)
    doc = BaseDocTemplate(
        buf, pagesize=(L.PAGE_W, L.PAGE_H), pageTemplates=[template],
        title="Costs Disclosure and Costs Agreement", author="Winzoy Legal",
        topMargin=0, bottomMargin=0, leftMargin=0, rightMargin=0,
    )
    doc.build(story, canvasmaker=chrome.make_canvas_factory(doc_id, generated_at))
    return buf.getvalue()


def _make_doc_id(data: ClientAgreementData) -> str:
    seed = f"CA|{data.our_ref}|{data.client_name}|{time.time()}"
    h = 0
    for ch in seed:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    hex_str = format(h, "08x").upper()
    return f"WZL-CA-{hex_str[:4]}-{hex_str[4:8]}"


def _build_ack_text(client_name: str, languages: list[str]) -> str:
    langs = [lang for lang in (languages or []) if lang] or ["English"]
    lang_list = " and/or ".join(langs)
    name_phrase = f'I, "{client_name}",' if client_name and client_name.strip() else "I"
    return (
        f"{name_phrase} acknowledge that I understand and accepted the above agreement. "
        f"The agreement has been explained to me and my partner in {lang_list}."
    )


def _build_story(data: ClientAgreementData, today_short: str) -> list:
    story: list = []

    # ═══════════════════════════════════════ PAGE 1 — Agreement details
    if data.translation_banner_text:
        banner_style = ParagraphStyle(
            "CLA_TransBanner", fontName=L.FONT_REGULAR, fontSize=8, leading=14,
            textColor=L.WHITE, backColor=L.TRANSLATION_BANNER_BG, leftIndent=8,
        )
        story.append(PT(data.translation_banner_text, banner_style))
        story.append(Spacer(1, 10))

    story.append(P("Costs Disclosure and Costs Agreement", L.STYLE_TITLE))
    story.append(Spacer(1, 6))
    date_style = ParagraphStyle("CLA_DateLine", fontName=L.FONT_REGULAR, fontSize=10.5, alignment=2)
    story.append(P(f'<font color="{L.hex_of(L.MUTED)}">Date:</font> {esc(data.date)}', date_style))
    story.append(Spacer(1, 10))
    story.append(P("Between", L.STYLE_CENTER_BOLD))
    story.append(Spacer(1, 16))

    story.append(parties_table(data.date, data.our_ref, data.client_name, data.client_address))
    story.append(Spacer(1, 20))

    story.append(P("CAPACITY OF REPRESENTATIVE", L.STYLE_H2))
    story.append(Spacer(1, 8))
    lpn_label = f"  (LPN: {data.lpn})" if data.capacity == "solicitor" and data.lpn else ""
    story.append(checkbox_row(
        data.capacity == "solicitor",
        f"Legal Practitioner (Solicitor){lpn_label}",
        L.CONTENT_W,
    ))
    story.append(Spacer(1, 4))
    marn_label = f"  (MARN: {data.marn})" if data.capacity == "rma" and data.marn else ""
    story.append(checkbox_row(
        data.capacity == "rma",
        f"Registered Migration Agent (RMA){marn_label}",
        L.CONTENT_W,
    ))
    story.append(Spacer(1, 12))

    visa_type_text = data.visa_type or "?"
    bullets = [
        f"Process your application for {visa_type_text}",
        "Follow up your case until it is finalized",
        "Inform you of the outcome of your visa application",
    ]
    fee_text = f"${fmt_amt(data.professional_fee)} (Incl. GST)"
    story.append(works_fee_table(bullets, fee_text))
    story.append(Spacer(1, 16))
    weeks = data.estimated_weeks or "01"
    story.append(PT(
        f"We estimate that it will take us {weeks} week(s) to complete the agreed "
        "services for which our fixed professional fee will be based on current "
        "fees and charges.",
        L.STYLE_BODY,
    ))
    story.append(Spacer(1, 18))

    charges = data.visa_application_charges or [""]
    disb_rows = [(
        "Service Fee (Photocopies, postage)",
        f"${fmt_amt(data.service_fee)}" if data.service_fee else "$",
    )]
    multi = len(charges) > 1
    for idx, charge in enumerate(charges):
        label = (
            f"Visa Application Charge (VAC) incl. 1.4% surcharge ({idx + 1})"
            if multi else
            "Visa Application Charge (VAC) incl. 1.4% surcharge"
        )
        amount_text = f"${fmt_amt(apply_vac_surcharge(charge))}" if charge else "$"
        disb_rows.append((label, amount_text))
    story.append(disbursement_table(disb_rows))
    story.append(Spacer(1, 24))

    professional_cost = parse_amt(data.professional_fee)
    vac_total = sum(apply_vac_surcharge(c) for c in charges)
    disbursements_cost = sum_amounts(data.service_fee) + vac_total
    story.append(cost_summary_table(professional_cost, disbursements_cost, total_suffix=" incl GST"))
    story.append(Spacer(1, 14))

    story.append(P("PAYMENT SCHEDULE", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(PT(
        "You will promptly pay the agreeable amount before the lodgment of your "
        "visa. You will also upon our request make payment for any disbursement "
        "which is incurred during the course of our work.",
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

    # ═══════════════════════════════════════ ANNEXURE D (optional only)
    if data.include_annexure_d:
        story.append(PageBreak())
        story.extend(annex.annexure_d_flowables())

    return story
