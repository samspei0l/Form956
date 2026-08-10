"""Builds the Divorce Cost Agreement PDF.

Content (every clause, bullet, and disclosure paragraph) is transcribed
verbatim from winzoylegal_new's
src/features/divorceCostAgreement/buildDivorceCostAgreementPdf.ts (schema
mirrored from that feature's types.ts) -- only the layout mechanism
changed. See costagreements/layout.py for why: ReportLab Platypus
flowables replace pdf-lib's manual y-coordinate bookkeeping, which is
what caused winzoylegal_new's long-running client-initials/signature/date
spacing bugs.

Structurally this is closest to builders/bfa.py (same "Costs Disclosure
and Costs Agreement" cover page shape, no visa/nomination pathway, a full
unnumbered "General Terms of Business" page, and only an optional
Annexure D -- no Annexure A/B/C, matching the TS source only importing
``appendAnnexureD``) but differs from it in three load-bearing ways:

1. **Disbursement table is optional.** BFA always renders its
   disbursement table; here it only appears when
   ``data.include_disbursement`` is true (``if (data.includeDisbursement)``
   in the TS source), and its second row is a divorce-application
   lodgment fee (with an optional "(client card)" label suffix, falling
   back to a flat ``$1,125`` display amount when
   ``divorce_lodgment_fee`` isn't supplied) rather than BFA's single
   photocopy/postage service-fee row.
2. **Payment schedule has two shapes, not BFA's fixed 2-row table.**
   When ``data.hide_payment_schedule`` is true, a single "Full payment of
   $X is payable in full..." box is drawn instead of any per-stage table
   (``PAYMENT SCHEDULE`` heading). Otherwise a 3-row instalment table is
   drawn (``PAYMENT SCHEDULE FOR COST AND DISBURSEMENT`` heading) whose
   amounts come from ``computeInstalments()`` in the TS source: if
   ``instalment1_amount``/``instalment2_amount``/``instalment3_amount``
   are all present, numeric, and sum to ``professional_fee`` (within
   1 cent), they're used verbatim; otherwise the three stages fall back
   to an even 3-way split of the professional fee. This port keeps that
   exact fallback semantics (``_compute_instalments`` below) rather than
   trusting client-supplied instalment amounts unconditionally -- the TS
   source's own comment calls this a deliberate "ponytail": mismatched
   custom splits are silently corrected rather than raising, since a
   staff UI is expected to warn separately.
3. **The "estimate cost" paragraph** draws from ``data.total_cost``
   verbatim (``$${fmtAmt(data.totalCost || '')}``), exactly like BFA's
   equivalent paragraph -- *not* the same figure as the separately
   computed Cost Summary table.

The General Terms of Business heading/body text transcribed below from
the divorce TS source's own ``terms`` array happens to be word-for-word
identical to BFA's own ``terms`` array (both firms share this same
boilerplate); it is nonetheless copied independently from
buildDivorceCostAgreementPdf.ts here rather than imported from bfa.py, to
keep this module self-contained per the porting convention every other
builder in this package follows.

Structural decisions where the TS source was ambiguous:
  - ``REQUIRED_FIELDS``: types.ts marks almost every field as required
    (including serviceBullets, divorceLodgmentFee, totalCost, marn, lpn)
    but, following the precedent set by every sibling builder (see
    bfa.py's own docstring), only the fields load-bearing for page 1's
    body text to render sensibly are required here: date/our_ref/
    client_name/client_address/capacity, professional_fee/
    estimated_days/service_fee, rep_name. serviceBullets/
    divorce_lodgment_fee/total_cost/instalment amounts/marn/lpn all have
    sensible fallback text/defaults in ``_build_story`` exactly like
    every sibling's own optional fields.
  - ``lodgment_uses_client_card`` / ``include_disbursement`` /
    ``hide_payment_schedule`` / ``include_annexure_d``: booleans, not
    validated beyond ``bool(...)`` coercion in ``from_payload`` --
    matching how every other boolean flag (e.g. General Cost Agreement's
    own ``lodgment_uses_client_card``) is handled across this package.
  - ``rep_signature_data``: not present in types.ts (which only has
    ``repSignatureUrl``, fetched directly by the TS builder). Added here
    anyway, matching every sibling builder's architecture decision:
    fetching a staff-supplied URL server-side would be an SSRF vector, so
    only a base64 data URI (``rep_signature_data``) is ever drawn, and
    ``rep_signature_url`` is metadata-passthrough-only into SIGMETA.
  - Money formatting uses this package's shared ``money.fmt_amt`` (always
    2 decimal places) everywhere, rather than porting the TS source's
    separate ``formatAmount()`` helper (which drops a trailing ".00").
    Every other builder in this package (BFA, JRP, client_agreement, ...)
    already made this same normalisation for consistency; this port
    follows suit rather than reintroducing a second money-formatting
    convention.

This is a synchronous, single-call generator: the returned PDF is the
final document. There is no SIGMETA-based post-signing remote-stamp step
beyond metadata embedding -- winzoylegal_new's own two-stage e-signing
flow (tokens, pending/signed status) is out of scope for this Flask port.
If a signature image isn't supplied in the payload, the signature box
renders empty/bordered for print-and-sign.
"""
from __future__ import annotations

import io
import math
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
from ..money import fmt_amt, parse_amt, sum_amounts
from ..sigmeta import SigMetaState


# --------------------------------------------------------------------- schema
@dataclass
class DivorceCostAgreementData:
    """Payload shape for the Divorce Cost Agreement, mirroring
    winzoylegal_new's divorceCostAgreement/types.ts field-for-field
    (snake_case to match this API's existing JSON convention)."""

    # Header
    date: str  # DD/MM/YYYY (or YYYY-MM-DD, normalised before this is built)
    our_ref: str
    client_name: str
    client_address: str

    # Capacity of representative (top of page 1)
    capacity: str  # "solicitor" | "rma"

    # Scope of work / fees
    professional_fee: str
    estimated_days: str

    # Disbursement (table only rendered if include_disbursement is true)
    service_fee: str

    rep_name: str

    service_bullets: str = ""
    divorce_lodgment_fee: str = ""  # falls back to a flat $1,125 display -- see module docstring
    lodgment_uses_client_card: bool = False
    total_cost: str = ""  # used verbatim in the "estimate cost" paragraph -- see module docstring
    include_disbursement: bool = False

    # Payment schedule
    hide_payment_schedule: bool = False
    payment_stage1_label: str = ""
    payment_stage2_label: str = ""
    payment_stage3_label: str = ""
    instalment1_amount: str = ""
    instalment2_amount: str = ""
    instalment3_amount: str = ""

    # Signature block
    marn: str = ""
    lpn: str = ""
    rep_signature_data: str | None = None       # base64 data URI -- see validate_divorce_cost_agreement()
    client_signature_data: str | None = None     # base64 data URI
    rep_signature_url: str | None = None         # metadata passthrough only, never fetched -- see sigmeta.py

    staff_note: str = ""
    ack_languages: list[str] = field(default_factory=list)
    translation_banner_text: str = ""
    translated_ack_text: str = ""
    include_annexure_d: bool = False

    @classmethod
    def from_payload(cls, payload: dict) -> "DivorceCostAgreementData":
        ack_languages = payload.get("ack_languages") or []
        if isinstance(ack_languages, str):
            ack_languages = [ack_languages] if ack_languages.strip() else []

        return cls(
            date=str(payload.get("date") or ""),
            our_ref=str(payload.get("our_ref") or ""),
            client_name=str(payload.get("client_name") or ""),
            client_address=str(payload.get("client_address") or ""),
            capacity=str(payload.get("capacity") or ""),
            professional_fee=str(payload.get("professional_fee") or ""),
            estimated_days=str(payload.get("estimated_days") or ""),
            service_fee=str(payload.get("service_fee") or ""),
            rep_name=str(payload.get("rep_name") or ""),
            service_bullets=str(payload.get("service_bullets") or ""),
            divorce_lodgment_fee=str(payload.get("divorce_lodgment_fee") or ""),
            lodgment_uses_client_card=bool(payload.get("lodgment_uses_client_card", False)),
            total_cost=str(payload.get("total_cost") or ""),
            include_disbursement=bool(payload.get("include_disbursement", False)),
            hide_payment_schedule=bool(payload.get("hide_payment_schedule", False)),
            payment_stage1_label=str(payload.get("payment_stage1_label") or ""),
            payment_stage2_label=str(payload.get("payment_stage2_label") or ""),
            payment_stage3_label=str(payload.get("payment_stage3_label") or ""),
            instalment1_amount=str(payload.get("instalment1_amount") or ""),
            instalment2_amount=str(payload.get("instalment2_amount") or ""),
            instalment3_amount=str(payload.get("instalment3_amount") or ""),
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
    "professional_fee", "estimated_days", "service_fee",
    "rep_name",
})

VALID_CAPACITIES = frozenset({"solicitor", "rma"})
MONEY_FIELDS = frozenset({
    "professional_fee", "service_fee", "divorce_lodgment_fee",
    "instalment1_amount", "instalment2_amount", "instalment3_amount",
    "total_cost",
})
IMAGE_FIELDS = frozenset({"client_signature_data", "rep_signature_data"})


def validate_divorce_cost_agreement(payload: dict) -> list[ValidationError]:
    """Validate a Divorce Cost Agreement payload. Empty list = valid."""
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

    for f in IMAGE_FIELDS:
        v = payload.get(f)
        if v and not (isinstance(v, str) and v.startswith("data:image/")):
            errs.append(ValidationError(
                field=f, code="format",
                message=f"{f!r} must be a base64 image data URI (data:image/png;base64,...)",
            ))

    return errs


def apply_divorce_normalisations(payload: dict) -> dict:
    """Return a shallow copy of ``payload`` with 'date' normalised to DD/MM/YYYY."""
    out = dict(payload)
    v = out.get("date")
    if isinstance(v, str) and DATE_YYYYMMDD.match(v):
        out["date"] = normalise_date(v)
    return out


# --------------------------------------------------------------------- builder
_DEFAULT_BULLETS = [
    "Process the affidavits and documents for your joint application for divorce to the Family Court",
    "Follow up your case until it is finalized",
    "Inform you of the outcome of your application",
]
_DEFAULT_LODGMENT_FEE_TEXT = "$1,125"

_STYLE_PAYMENT_NOTE = ParagraphStyle(
    "DIV_PaymentNote", fontName=L.FONT_REGULAR, fontSize=9.5, leading=13, textColor=L.BLACK,
)
_STYLE_AMT_CENTER = ParagraphStyle(
    "DIV_AmtCenter", fontName=L.FONT_REGULAR, fontSize=10, leading=13, alignment=1,
)
_STYLE_FULL_PAY = ParagraphStyle(
    "DIV_FullPay", fontName=L.FONT_REGULAR, fontSize=10, leading=13, textColor=L.BLACK,
)


def build_divorce_cost_agreement(data: DivorceCostAgreementData) -> bytes:
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
        chrome.draw_header(canvas, doc, doc_id, generated_at, "DIVORCE")

    template = PageTemplate(id="main", frames=[frame], onPage=on_page)
    doc = BaseDocTemplate(
        buf, pagesize=(L.PAGE_W, L.PAGE_H), pageTemplates=[template],
        title="Costs Disclosure and Costs Agreement (Divorce)", author="Winzoy Legal",
        topMargin=0, bottomMargin=0, leftMargin=0, rightMargin=0,
    )
    doc.build(story, canvasmaker=chrome.make_canvas_factory(doc_id, generated_at))
    return buf.getvalue()


def _make_doc_id(data: DivorceCostAgreementData) -> str:
    seed = f"DIV|{data.our_ref}|{data.client_name}|{time.time()}"
    h = 0
    for ch in seed:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    hex_str = format(h, "08x").upper()
    return f"WZL-DIV-{hex_str[:4]}-{hex_str[4:8]}"


def _build_ack_text(client_name: str, languages: list[str]) -> str:
    langs = [lang for lang in (languages or []) if lang] or ["English"]
    lang_list = " and/or ".join(langs)
    name_phrase = f'I, "{client_name}",' if client_name and client_name.strip() else "I"
    return (
        f"{name_phrase} acknowledge that I understand and accepted the above agreement. "
        f"The agreement has been explained to me and my partner in {lang_list}."
    )


def _round2(x: float) -> float:
    """Round-half-up to 2dp, matching JS's ``Math.round`` behaviour for the
    non-negative amounts this is used on (Python's built-in ``round`` uses
    banker's rounding, which can disagree at exact .005 boundaries)."""
    return math.floor(x * 100 + 0.5) / 100.0


def _compute_instalments(pf: float, a1: str, a2: str, a3: str) -> tuple[float, float, float]:
    """Port of buildDivorceCostAgreementPdf.ts's ``computeInstalments()``:
    a custom 3-way split is honoured only if all three amounts are numeric
    and sum to the professional fee (within 1 cent); otherwise falls back
    to an even 3-way split so the PDF never silently miscalculates."""
    def _to_float(v: str) -> float | None:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    a, b, c = _to_float(a1), _to_float(a2), _to_float(a3)
    if a is None or b is None or c is None or abs(a + b + c - pf) > 0.01:
        even = _round2(pf / 3)
        return even, even, _round2(pf - even * 2)
    return a, b, c


def _instalment_table(stages: list[tuple[str, str]]) -> Table:
    w1 = L.CONTENT_W * 0.70
    w2 = L.CONTENT_W * 0.30
    data = [[PT(label, L.STYLE_BODY), P(esc(amount_text), _STYLE_AMT_CENTER)] for label, amount_text in stages]
    t = Table(data, colWidths=[w1, w2])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, L.BLACK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def _full_pay_box(text: str) -> Table:
    t = Table([[PT(text, _STYLE_FULL_PAY)]], colWidths=[L.CONTENT_W])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1, L.NAVY),
        ("BACKGROUND", (0, 0), (-1, -1), L.BANK_BG),
        ("LINEBEFORE", (0, 0), (0, -1), 4, L.GOLD),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    return t


def _build_story(data: DivorceCostAgreementData, today_short: str) -> list:
    story: list = []

    # ═══════════════════════════════════════ PAGE 1 — Agreement details
    if data.translation_banner_text:
        banner_style = ParagraphStyle(
            "DIV_TransBanner", fontName=L.FONT_REGULAR, fontSize=8, leading=14,
            textColor=L.WHITE, backColor=L.TRANSLATION_BANNER_BG, leftIndent=8,
        )
        story.append(PT(data.translation_banner_text, banner_style))
        story.append(Spacer(1, 10))

    story.append(P("Costs Disclosure and Costs Agreement", L.STYLE_TITLE))
    story.append(Spacer(1, 6))
    date_style = ParagraphStyle("DIV_DateLine", fontName=L.FONT_REGULAR, fontSize=10.5, alignment=2)
    story.append(P(f'<font color="{L.hex_of(L.MUTED)}">Date:</font> {esc(data.date)}', date_style))
    story.append(Spacer(1, 10))
    story.append(P("Between", L.STYLE_CENTER_BOLD))
    story.append(Spacer(1, 16))

    story.append(parties_table(data.date, data.our_ref, data.client_name, data.client_address))
    story.append(Spacer(1, 24))

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

    # ── Our Works for You / Our Professional Cost ─────
    bullets_raw = data.service_bullets or ""
    bullets = [b.strip() for b in bullets_raw.split("\n") if b.strip()] if bullets_raw else []
    fee_text = f"${fmt_amt(data.professional_fee)} incl GST"
    story.append(works_fee_table(bullets or _DEFAULT_BULLETS, fee_text))
    story.append(Spacer(1, 16))

    days = data.estimated_days or "02"
    story.append(PT(
        f"We estimate that it will take us {days} days from the date you provide all "
        "the required documents to complete the agreed services, for which our fixed "
        "professional fee will be based on current fees and charges.",
        L.STYLE_BODY,
    ))
    story.append(Spacer(1, 18))

    # ── Disbursement table -- only rendered when include_disbursement ──
    if data.include_disbursement:
        lodgment_label = (
            "Divorce application lodgment fee (client card)"
            if data.lodgment_uses_client_card else
            "Divorce application lodgment fee"
        )
        lodgment_amount = (
            f"${fmt_amt(data.divorce_lodgment_fee)}" if data.divorce_lodgment_fee
            else _DEFAULT_LODGMENT_FEE_TEXT
        )
        story.append(disbursement_table([
            ("Service Fee (Photocopies, postage)", f"${fmt_amt(data.service_fee or '0')}"),
            (lodgment_label, lodgment_amount),
        ]))
        story.append(Spacer(1, 24))

    # ── Estimate cost paragraph -- draws data.total_cost verbatim ────
    story.append(PT(
        f"Based on current fees and charges, we estimate that your total costs will "
        f"be approximately ${fmt_amt(data.total_cost)} (incl GST) which may vary due "
        "to any further disbursement (if any).",
        L.STYLE_BODY,
    ))
    story.append(Spacer(1, 12))

    # ── Cost summary table ────────────────────────────
    professional_cost = parse_amt(data.professional_fee)
    if data.include_disbursement:
        lodgment_cost = parse_amt(data.divorce_lodgment_fee) if data.divorce_lodgment_fee else 1125.0
        disbursements_cost = sum_amounts(data.service_fee) + lodgment_cost
    else:
        disbursements_cost = 0.0
    story.append(cost_summary_table(professional_cost, disbursements_cost, total_suffix=" incl GST"))
    story.append(Spacer(1, 14))

    # ── Payment schedule ───────────────────────────────
    heading_text = "PAYMENT SCHEDULE" if data.hide_payment_schedule else "PAYMENT SCHEDULE FOR COST AND DISBURSEMENT"
    story.append(P(heading_text, L.STYLE_H2))
    story.append(Spacer(1, 6))

    professional_fee_num = parse_amt(data.professional_fee)
    if data.hide_payment_schedule:
        full_pay_text = (
            f"Full payment of ${fmt_amt(professional_fee_num)} (incl GST) is payable "
            "in full at the time file is ready for submission."
        )
        story.append(_full_pay_box(full_pay_text))
        story.append(Spacer(1, 12))
    else:
        amt1, amt2, amt3 = _compute_instalments(
            professional_fee_num, data.instalment1_amount, data.instalment2_amount, data.instalment3_amount,
        )
        stages: list[tuple[str, str]] = [
            (data.payment_stage1_label or "1st Instalment – On the day of Signing Cost Agreement", f"${fmt_amt(amt1)}"),
            (data.payment_stage2_label or "2nd Instalment – On the day of lodgement", f"${fmt_amt(amt2)}"),
            (data.payment_stage3_label or "3rd Instalment – On the day your application is finalised", f"${fmt_amt(amt3)}"),
        ]
        story.append(_instalment_table(stages))
        story.append(Spacer(1, 12))

    story.append(PT(
        "You are required to pay our fees immediately after your application has been "
        "completely prepared, finalised, and formally lodged. You will also, upon our "
        "request, make payment for any disbursement which is incurred during the course "
        "of our work.",
        _STYLE_PAYMENT_NOTE,
    ))
    story.append(Spacer(1, 14))

    story.append(PT("Payment of estimated legal fees to the account below:", L.STYLE_ITALIC_MUTED))
    story.append(Spacer(1, 6))
    story.append(bank_details_box())
    story.append(Spacer(1, 20))

    # ── Acknowledgement ────────────────────────────────
    story.append(P("ACKNOWLEDGEMENT", L.STYLE_H2))
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

    # ═══════════════════════════════════════ GENERAL TERMS OF BUSINESS
    story.append(PageBreak())
    story.extend(_general_terms())

    # ═══════════════════════════════════════ ANNEXURE D (optional only)
    if data.include_annexure_d:
        story.append(PageBreak())
        story.extend(annex.annexure_d_flowables())

    return story


# Headings/body transcribed verbatim from buildDivorceCostAgreementPdf.ts's
# own ``terms`` array -- deliberately *not* numbered (matches that source's
# own unnumbered headings, e.g. "Billing Arrangements" not "1 Billing
# Arrangements").
_DIVORCE_TERMS: list[tuple[str, str]] = [
    ("General Terms of Business", ""),
    (
        "Billing Arrangements",
        "All tax invoices are due and payable 30 days from the date of the tax "
        "invoice. You consent to us sending our tax invoices to you electronically "
        "at your usual email address or mobile phone number as specified by you.",
    ),
    (
        "Acceptance of Offer",
        "You may accept the Costs Disclosure and Costs Agreement by:\n"
        "a) signing and returning this document to us or:\n"
        "b) continuing to instruct us. Upon acceptance you agree to pay for our "
        "services on these terms.",
    ),
    (
        "Interest Charges",
        "Interest at the maximum rate prescribed in Rule 75 of the Legal Profession "
        'Uniform General Rules 2015 ("Uniform General Rules") (being the Cash Rate '
        "Target set by the Reserve Bank of Australia plus 2%) will be charged on any "
        "amounts unpaid after the expiry of 30 days after a tax invoice is given to "
        "you. Our tax invoices will specify the interest rate to be charged.",
    ),
    (
        "Recovery of Costs",
        'The Legal Profession Uniform Law (NSW) ("the Uniform Law") provides that we '
        "cannot take action for recovery of legal costs until 30 days after a tax "
        "invoice (which complies with the Uniform Law) has been given to you.",
    ),
    (
        "Your Rights",
        "It is your right to:\n"
        "• negotiate a costs agreement with us;\n"
        "• negotiate the method of billing (e.g. task based or time based);\n"
        "• receive a bill and to request and receive an itemised bill within 30 days "
        "after a lump sum bill or partially itemised bill is payable;\n"
        "• seek the assistance of the designated local regulatory authority (the NSW "
        "Commissioner) in the event of a dispute about legal costs;\n"
        "• be notified as soon as is reasonably practicable of any significant change "
        "to any matter affecting costs;\n"
        "• accept or reject any offer we make for an interstate costs law to apply to "
        "your matter; and\n"
        "• notify us that you require an interstate costs law to apply to your matter.\n"
        "If you request an itemised bill and the total amount of the legal costs "
        "specified in it exceeds the amount previously specified in the lump sum bill "
        "for the same matter, the additional costs may be recovered by us only if:\n"
        "(i) when the lump sum bill is given, we inform you in writing that the total "
        "amount of the legal costs specified in any itemised bill may be higher than "
        "the amount specified in the lump sum bill, and\n"
        "(ii) the costs are determined to be payable after a costs assessment or "
        "after a binding determination under section 292 of the Uniform Law.\n"
        "Nothing in these terms affects your rights under the Australian Consumer Law.",
    ),
    (
        "Your Rights in relation to a Dispute concerning Costs",
        "If you have a dispute in relation to any aspect of our legal costs you have "
        "the following avenues of redress:\n"
        "• in the first instance we encourage you to discuss your concerns with us so "
        "that any issue can be identified and we can have the opportunity of "
        "resolving the matter promptly and without it adversely impacting on our "
        "business relationship;\n"
        "• you may apply to the Manager, Costs Assessment located at the Supreme "
        "Court of NSW for an assessment of our costs. This application must be made "
        "within 12 months after the bill was provided or request for payment made or "
        "after the costs were paid.",
    ),
    (
        "Payment Methods",
        "It is our policy that, when acting for new clients, we do one or more of "
        "the following:\n"
        "• approve credit;\n"
        "• ask the client for their credit card details.\n"
        "Unless otherwise agreed with you, we may determine not to incur fees or "
        "expenses in excess of the amount that we hold in trust on your behalf or "
        "for which credit is approved.",
    ),
    (
        "Retention of Your Documents and Electronic files",
        "Electronic Storage: We maintain a paperless office. By signing this "
        "agreement, you agree that we will store all documents and correspondence "
        "related to your matter in electronic format only.\n"
        "Destruction of Hard Copies: Any hard copy documents provided by you or "
        "third parties will be scanned into our electronic filing system. Once "
        "scanned, the hard copies will be destroyed or returned to you at our "
        "discretion, unless we are legally required to keep the original.\n"
        "Client Responsibility: If you require the return of original hard copy "
        "documents, you must notify us in writing at the time the documents are "
        "provided to us.\n"
        "Archive Period: We will retain your electronic file for at least seven (7) "
        "years after the conclusion of your matter, after which we may delete the "
        "electronic data without further notice to you.\n"
        "Cost of Retrieval: Should you request a copy of your electronic file during "
        "the retention period, we reserve the right to charge a reasonable "
        "administrative fee for the time spent retrieving and providing the data.\n"
        "We are entitled to retain your documents while there is money owing to us "
        "for our costs.",
    ),
    (
        "Termination by Us",
        "We may cease to act for you or refuse to perform further work, including:\n"
        "• while any of our tax invoices remain unpaid;\n"
        "• if you do not within 7 days comply with any request to pay an amount in "
        "respect of disbursements or future costs;\n"
        "• if you fail to provide us with clear and timely instructions to enable us "
        "to advance your matter;\n"
        "• if you refuse to accept our advice;\n"
        "• if you indicate to us or we form the view that you have lost confidence "
        "in us;\n"
        "• if there are any ethical grounds which we consider require us to cease "
        "acting for you, for example a conflict of interest;\n"
        "• for any other reason outside our control which has the effect of "
        "compromising our ability to perform the work required within the required "
        "timeframe; or\n"
        "• if in our sole discretion we consider it is no longer appropriate to act "
        "for you; or\n"
        "• for just cause.\n"
        "We will give you reasonable written notice of termination of our services. "
        "You will be required to pay our costs incurred up to the date of termination.",
    ),
    (
        "Termination by You",
        "You may terminate our services by written notice at any time. However, if "
        "you do so you will be required to pay our costs incurred up to the date of "
        "termination (including if the matter is litigious, any cancellation fees or "
        "other fees such as hearing allocation fees for which we remain responsible).",
    ),
    (
        "Lien",
        "Without affecting any lien to which we are otherwise entitled at law over "
        "funds, papers and other property of yours:\n"
        "(a) we shall be entitled to retain by way of lien any funds, property or "
        "papers of yours, which are from time to time in our possession or control, "
        "until all costs, disbursements, interest and other moneys due to the firm "
        "have been paid; and\n"
        "(b) our lien will continue notwithstanding that we cease to act for you.",
    ),
    (
        "Privacy",
        "We will collect personal information from you in the course of providing "
        "our legal services. We may also obtain personal information from third "
        "party searches, other investigations and, sometimes, from adverse parties. "
        "We are required to collect the full name and address of our clients by Rule "
        "93 of the Uniform General Rules. Accurate name and address information must "
        "also be collected in order to comply with the trust account record keeping "
        "requirements of Rule 47 of the Uniform General Rules and to comply with our "
        "duty to the courts.\n"
        "Your personal information will only be used for the purposes for which it "
        "is collected or in accordance with the Privacy Act 1988 (Cth). For example, "
        "we may use your personal information to provide advice and recommendations "
        "that take into account your personal circumstances.\n"
        "We manage and protect your personal information in accordance with our "
        "privacy policy which can be found on our firm website or a copy of which we "
        "shall provide at your request.",
    ),
    (
        "Sending Material Electronically",
        "We are able to send and receive documents electronically. However, as such "
        "transmission is not secure it may be copied, recorded, read or interfered "
        "with by third parties while in transit. If you ask us to transmit any "
        "document electronically, you release us from any claim you may have as a "
        "result of any unauthorised copying, recording, reading or interference with "
        "that document, for any delay or non-delivery of any document and for any "
        "damage caused to your system or any files.",
    ),
    (
        "GST",
        "Where applicable, GST is payable on our professional fees and expenses and "
        "will be clearly shown on our tax invoices. By accepting these terms you "
        "agree to pay us an amount equivalent to the GST imposed on these charges.",
    ),
    (
        "Governing Law",
        "The law of New South Wales governs these terms and legal costs in relation "
        "to any matter upon which we are instructed to act.",
    ),
]


def _general_terms() -> list:
    flows: list = []
    for i, (heading, body) in enumerate(_DIVORCE_TERMS):
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
