"""Builds the Visa 482 (Skills in Demand / Temporary Skill Shortage,
subclass 482, employer-sponsored) Cost Agreement PDF.

Content (every clause, bullet, and disclosure paragraph) is transcribed
verbatim from winzoylegal_new's
src/features/visa482CostAgreement/buildVisa482CostAgreementPdf.ts
(schema mirrored from that feature's types.ts) -- only the layout
mechanism changed. See costagreements/layout.py for why: ReportLab
Platypus flowables replace pdf-lib's manual y-coordinate bookkeeping,
which is what caused winzoylegal_new's long-running client-initials/
signature/date spacing bugs.

skills_assessment_186.py is the closest analog (another employer-
sponsorship/nomination pathway with an SAF levy and a sponsorship
processing-time field), but this type differs from it in several
structural ways (all mirrored from the TS source, not invented here):
  1. Three distinct application stages, not two: this is a *sponsorship +
     nomination + visa* pathway (Standard Business Sponsorship ->
     Nomination -> Subclass 482 visa) vs 186's *skills assessment +
     nomination + visa* pathway. There is no ``occupation``/
     ``assessing_authority``/``anzsco_code`` concept here at all -- scope
     of work is a single fixed sentence ("the Standard Business
     Sponsorship Nomination/Visa application whichever is applicable -
     for Skills in Demand (subclass 482)"), not an assessment-body
     lodgement.
  2. ``sponsorship_fee`` (a flat SBS lodgement fee) is a new field with no
     186 equivalent -- 186 Direct Entry has no separate sponsorship-stage
     fee of its own on the disclosure page (its scope starts from the
     nomination).
  3. The SAF levy is *two independent either/or amounts with year
     multipliers* (``saf_levy_per_year_small``/``saf_levy_per_year_large``,
     each with its own ``saf_years_small``/``saf_years_large`` count and
     its own ``saf_levy_small_applicable``/``saf_levy_large_applicable``
     boolean), not 186's single flat ``saf_levy_small``/``saf_levy_large``
     pair with no year dimension. The TS source multiplies the per-year
     rate out into a "(N yrs = AUD X)" suffix when more than one year
     applies, and each tier can be independently toggled off (defaulting
     to "applicable" when the flag is absent, per the TS's own
     ``!== false`` check) -- this port mirrors both behaviours in
     ``_saf_bullets()`` below.
  4. ``disbursements_sub_total`` feeds the Cost Summary table's
     disbursements figure *directly* -- the TS builder's own inline
     comment says this mirrors "the form's auto-calculated (but
     staff-overridable) sub-total, so the PDF always shows the same
     disbursements figure the staff member saw and approved." This is a
     structural departure from every other builder in this package
     (skills_assessment_186.py, skilled_visa.py, ...), which all *derive*
     the disbursements total by summing the individual fee fields
     server-side. Here nothing is summed: ``disbursements_cost`` passed
     into ``cost_summary_table()`` is simply ``parse_amt(data.
     disbursements_sub_total)``, taken on faith from the caller. Because
     this field is therefore load-bearing for the cost summary to render
     a meaningful total (unlike every sibling's own vestigial/unused
     ``total_cost``), it is included in ``REQUIRED_FIELDS`` here even
     though the closest siblings don't require their own analogous
     "totals" field -- see the REQUIRED_FIELDS note below.
  5. An optional "Applications included in this engagement" checkbox row
     (SBS / Nomination / Visa) appears near the top of page 1, unique to
     this agreement type -- absent from every sibling. Only rendered when
     ``applications_included`` is supplied.
  6. The signature block uses the *compact* style (``sig_h=36``, no
     "SIGNATURES" banner/intro sentence) -- see
     ``costagreements/components.py``'s ``compact_signature_block()``
     docstring, which names this exact agreement type as one of its
     three known callers (Visa 482 / Visa 870 / Skills Assessment Only).
     186 and skilled_visa both use the full ``signature_block()`` banner
     style instead.
  7. "Acknowledgement and Acceptance of Offer" is explicitly lettered
     "H." in the TS source (after G. Processing Times and Outcome) --
     186's and skilled_visa's equivalent sections are unlettered. This
     port preserves the "H." prefix verbatim.
  8. "B. Professional Fees" carries a second sentence ("I will be the
     solicitor with principal responsibility for assisting you in this
     matter.") identical to 186's own -- skilled_visa.py's "B." section
     does not have this second sentence. This port includes it, matching
     186 and the TS source here (not skilled_visa).
  9. "CAPACITY OF REPRESENTATIVE" has no lead-in sentence before the two
     checkboxes (unlike 186's "The services provided under this agreement
     are being rendered by Winzoy Legal..." paragraph) -- matches
     skilled_visa.py's leaner shape instead. Transcribed faithfully: no
     sentence was invented here.
 10. "E. Payment Schedule for Cost and Disbursement (inclusive of GST)"
     is followed straight by the bank-details box -- no italic note like
     skilled_visa.py's EOI-commitment sentence after its own payment
     table. 186's payment section has no trailing note either; this
     matches that.
 11. TSMIT (Temporary Skilled Migration Income Threshold) is *not*
     referenced anywhere in the TS source -- despite being a standard
     482-pathway concept, the winzoylegal_new builder never draws or
     validates it. No TSMIT field is invented here; the schema below is a
     field-for-field mirror of the real TS type, nothing more.

Structural decisions where the TS source was ambiguous:
  - ``REQUIRED_FIELDS``: types.ts marks almost every field as required
    (including all four SAF fields, lodgementUsesClientCard, totalCost,
    marn, lpn, and all three payment-stage label/amount pairs) but,
    following the precedent set by every sibling builder, only the
    fields load-bearing for page 1's body text/cost math to render
    sensibly are required here: date/our_ref/client_name/client_address/
    capacity, stream, professional_fee/sponsorship_fee/nomination_fee/
    visa_application_fee, disbursements_sub_total (see point 4 above --
    this one *is* required, unlike every sibling's own unused
    total_cost, because nothing derives it server-side),
    sponsorship_processing_time/nomination_processing_time/
    visa_processing_time, rep_name. The SAF fields, payment-stage
    labels/amounts, marn, lpn, total_cost, and lodgement_uses_client_card
    all have sensible fallback text/defaults in ``_build_story`` exactly
    like every sibling's own optional fields.
  - ``lodgement_uses_client_card``: present in types.ts but never read
    anywhere in buildVisa482CostAgreementPdf.ts (confirmed by grep against
    the TS source) -- kept here as a plain unused bool for schema parity,
    matching skilled_visa.py's own precedent for its vestigial
    ``service_bullets`` field.
  - ``rep_signature_data``: not present in types.ts (which only has
    ``repSignatureUrl``, fetched directly by the TS builder). Added here
    anyway, matching every sibling builder's architecture decision:
    fetching a staff-supplied URL server-side would be an SSRF vector, so
    only a base64 data URI (``rep_signature_data``) is ever drawn, and
    ``rep_signature_url`` is metadata-passthrough-only into SIGMETA.
  - ``saf_years_small``/``saf_years_large``: typed as optional numbers in
    types.ts; parsed here as optional positive ints (``None`` if absent
    or unparseable), defaulting to an implicit "1 year" in the year-total
    math exactly like the TS source's own ``data.safYearsSmall || 1``.
  - JS ``Number.prototype.toLocaleString()`` (used by the TS source to
    format the "(N yrs = AUD X)" year-total suffix) has no exact Python
    equivalent; ``_js_locale_number()`` below reproduces its common-case
    output (thousands separators, no forced decimal places for whole
    numbers) closely enough for this disclosure-only figure.

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
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, PageBreak, PageTemplate, Spacer, Table, TableStyle

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
    parties_table,
    staff_note_box,
    two_column_terms_box,
)
from ..money import apply_vac_surcharge, fmt_amt, parse_amt
from ..sigmeta import SigMetaState

SIG_BOX_H = 36  # matches buildVisa482CostAgreementPdf.ts's `const sigH = 36;`


# --------------------------------------------------------------------- schema
@dataclass
class Visa482CostAgreementData:
    """Payload shape for the Visa 482 Cost Agreement, mirroring
    winzoylegal_new's visa482CostAgreement/types.ts field-for-field
    (snake_case to match this API's existing JSON convention)."""

    # Header
    date: str  # DD/MM/YYYY (or YYYY-MM-DD, normalised before this is built)
    our_ref: str
    client_name: str
    client_address: str

    # Capacity of representative (top of page 1)
    capacity: str  # "solicitor" | "rma"

    # Scope of work / stream
    stream: str  # e.g. "Medium-term", "Short-term"

    # Fees
    professional_fee: str
    sponsorship_fee: str          # e.g. 420
    nomination_fee: str           # e.g. 330
    visa_application_fee: str     # e.g. 2770
    disbursements_sub_total: str  # manually entered by staff -- see module docstring point 4

    sponsorship_processing_time: str  # 14 - 60 days
    nomination_processing_time: str   # 4 - 120 days
    visa_processing_time: str         # 40 - 150 days

    rep_name: str

    # SAF levy -- two independent either/or tiers, each with its own
    # per-year rate, year count, and applicability flag.
    saf_levy_per_year_small: str = ""   # e.g. 1200 (<$10m turnover)
    saf_levy_per_year_large: str = ""   # e.g. 1800 (>=$10m turnover)
    saf_years_small: int | None = None
    saf_years_large: int | None = None
    saf_levy_small_applicable: bool = True
    saf_levy_large_applicable: bool = True

    lodgement_uses_client_card: bool = False  # present in types.ts; never read by the TS builder -- see module docstring
    total_cost: str = ""  # present in types.ts; not used in totals math -- disbursements_sub_total drives the Cost Summary directly

    # Payment schedule (3 editable stages + optional extra stages)
    payment_stage1_label: str = ""
    payment_stage1_amount: str = ""
    payment_stage2_label: str = ""
    payment_stage2_amount: str = ""
    payment_stage3_label: str = ""
    payment_stage3_amount: str = ""
    extra_stages: list[dict] = field(default_factory=list)  # [{"label": str, "amount": str}, ...]

    # "Applications included in this engagement" checkbox row (optional)
    applications_included: dict | None = None  # {"sbs": bool, "nomination": bool, "visa": bool}

    # Signature block
    marn: str = ""
    lpn: str = ""
    rep_signature_data: str | None = None       # base64 data URI -- see validate_visa_482_cost_agreement()
    client_signature_data: str | None = None     # base64 data URI
    rep_signature_url: str | None = None         # metadata passthrough only, never fetched -- see sigmeta.py

    staff_note: str = ""
    ack_languages: list[str] = field(default_factory=list)
    translation_banner_text: str = ""
    translated_ack_text: str = ""
    include_annexure_d: bool = False

    @classmethod
    def from_payload(cls, payload: dict) -> "Visa482CostAgreementData":
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

        applications_included_raw = payload.get("applications_included")
        applications_included = None
        if isinstance(applications_included_raw, dict):
            applications_included = {
                "sbs": bool(applications_included_raw.get("sbs")),
                "nomination": bool(applications_included_raw.get("nomination")),
                "visa": bool(applications_included_raw.get("visa")),
            }

        return cls(
            date=str(payload.get("date") or ""),
            our_ref=str(payload.get("our_ref") or ""),
            client_name=str(payload.get("client_name") or ""),
            client_address=str(payload.get("client_address") or ""),
            capacity=str(payload.get("capacity") or ""),
            stream=str(payload.get("stream") or ""),
            professional_fee=str(payload.get("professional_fee") or ""),
            sponsorship_fee=str(payload.get("sponsorship_fee") or ""),
            nomination_fee=str(payload.get("nomination_fee") or ""),
            visa_application_fee=str(payload.get("visa_application_fee") or ""),
            disbursements_sub_total=str(payload.get("disbursements_sub_total") or ""),
            sponsorship_processing_time=str(payload.get("sponsorship_processing_time") or ""),
            nomination_processing_time=str(payload.get("nomination_processing_time") or ""),
            visa_processing_time=str(payload.get("visa_processing_time") or ""),
            rep_name=str(payload.get("rep_name") or ""),
            saf_levy_per_year_small=str(payload.get("saf_levy_per_year_small") or ""),
            saf_levy_per_year_large=str(payload.get("saf_levy_per_year_large") or ""),
            saf_years_small=_parse_int(payload.get("saf_years_small")),
            saf_years_large=_parse_int(payload.get("saf_years_large")),
            saf_levy_small_applicable=bool(payload.get("saf_levy_small_applicable", True)),
            saf_levy_large_applicable=bool(payload.get("saf_levy_large_applicable", True)),
            lodgement_uses_client_card=bool(payload.get("lodgement_uses_client_card", False)),
            total_cost=str(payload.get("total_cost") or ""),
            payment_stage1_label=str(payload.get("payment_stage1_label") or ""),
            payment_stage1_amount=str(payload.get("payment_stage1_amount") or ""),
            payment_stage2_label=str(payload.get("payment_stage2_label") or ""),
            payment_stage2_amount=str(payload.get("payment_stage2_amount") or ""),
            payment_stage3_label=str(payload.get("payment_stage3_label") or ""),
            payment_stage3_amount=str(payload.get("payment_stage3_amount") or ""),
            extra_stages=extra_stages,
            applications_included=applications_included,
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


def _parse_int(v) -> int | None:
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------- validation
REQUIRED_FIELDS = frozenset({
    "date", "our_ref", "client_name", "client_address", "capacity",
    "stream",
    "professional_fee", "sponsorship_fee", "nomination_fee", "visa_application_fee",
    "disbursements_sub_total",
    "sponsorship_processing_time", "nomination_processing_time", "visa_processing_time",
    "rep_name",
})

VALID_CAPACITIES = frozenset({"solicitor", "rma"})
MONEY_FIELDS = frozenset({
    "professional_fee", "sponsorship_fee", "nomination_fee", "visa_application_fee",
    "disbursements_sub_total", "saf_levy_per_year_small", "saf_levy_per_year_large",
    "payment_stage1_amount", "payment_stage2_amount", "payment_stage3_amount",
    "total_cost",
})
IMAGE_FIELDS = frozenset({"client_signature_data", "rep_signature_data"})


def validate_visa_482_cost_agreement(payload: dict) -> list[ValidationError]:
    """Validate a Visa 482 Cost Agreement payload. Empty list = valid."""
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

    for f in ("saf_years_small", "saf_years_large"):
        v = payload.get(f)
        if v in (None, ""):
            continue
        parsed = _parse_int(v)
        if parsed is None or parsed < 1:
            errs.append(ValidationError(
                field=f, code="value", message=f"{f!r} must be a positive integer, got {v!r}",
            ))

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

    applications_included = payload.get("applications_included")
    if applications_included is not None and not isinstance(applications_included, dict):
        errs.append(ValidationError(
            field="applications_included", code="format",
            message="'applications_included' must be an object with 'sbs'/'nomination'/'visa' booleans",
        ))

    for f in IMAGE_FIELDS:
        v = payload.get(f)
        if v and not (isinstance(v, str) and v.startswith("data:image/")):
            errs.append(ValidationError(
                field=f, code="format",
                message=f"{f!r} must be a base64 image data URI (data:image/png;base64,...)",
            ))

    return errs


def apply_visa_482_normalisations(payload: dict) -> dict:
    """Return a shallow copy of ``payload`` with 'date' normalised to DD/MM/YYYY."""
    out = dict(payload)
    v = out.get("date")
    if isinstance(v, str) and DATE_YYYYMMDD.match(v):
        out["date"] = normalise_date(v)
    return out


# --------------------------------------------------------------------- builder
_STYLE_ITALIC_SMALL = ParagraphStyle(
    "V482_ItalicSmall", fontName=L.FONT_ITALIC, fontSize=9.5, leading=12.5, textColor=L.BLACK,
)
_STYLE_DISB_CELL = ParagraphStyle(
    "V482_DisbCell", fontName=L.FONT_REGULAR, fontSize=8.5, leading=11, textColor=L.BLACK,
)
_STYLE_APP_LABEL = ParagraphStyle(
    "V482_AppLabel", fontName=L.FONT_REGULAR, fontSize=9.5, leading=12,
)
_STYLE_APP_HEADING = ParagraphStyle(
    "V482_AppHeading", fontName=L.FONT_BOLD, fontSize=9.5, leading=12,
    textColor=L.NAVY, alignment=1,
)


class _MiniCheckbox(Flowable):
    """A small tick-box, sized to sit inline with a short label -- used
    only for the 'Applications included in this engagement' row, which
    (unlike every other checkbox in this package) needs three checkboxes
    side by side rather than one spanning the full content width. See
    ``costagreements/components.py``'s ``_Checkbox`` for the shared,
    full-row-width equivalent used everywhere else."""

    def __init__(self, checked: bool, size: float = 9):
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
            c.setLineWidth(0.9)
            c.line(s * 0.15, s * 0.5, s * 0.4, s * 0.2)
            c.line(s * 0.4, s * 0.2, s * 0.9, s * 0.8)


def build_visa_482_cost_agreement(data: Visa482CostAgreementData) -> bytes:
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
        chrome.draw_header(canvas, doc, doc_id, generated_at, "SUBCLASS 482 (SID)")

    template = PageTemplate(id="main", frames=[frame], onPage=on_page)
    doc = BaseDocTemplate(
        buf, pagesize=(L.PAGE_W, L.PAGE_H), pageTemplates=[template],
        title="Costs Disclosure and Costs Agreement (Visa 482)", author="Winzoy Legal",
        topMargin=0, bottomMargin=0, leftMargin=0, rightMargin=0,
    )
    doc.build(story, canvasmaker=chrome.make_canvas_factory(doc_id, generated_at))
    return buf.getvalue()


def _make_doc_id(data: Visa482CostAgreementData) -> str:
    seed = f"V482|{data.our_ref}|{data.client_name}|{time.time()}"
    h = 0
    for ch in seed:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    hex_str = format(h, "08x").upper()
    return f"WZL-V482-{hex_str[:4]}-{hex_str[4:8]}"


def _build_ack_text(client_name: str, languages: list[str]) -> str:
    langs = [lang for lang in (languages or []) if lang] or ["English"]
    lang_list = " and/or ".join(langs)
    name_phrase = f'I, "{client_name}",' if client_name and client_name.strip() else "I"
    return (
        f"{name_phrase} acknowledge that I understand and accepted the above agreement. "
        f"The agreement has been explained to me and my partner in {lang_list}."
    )


def _js_locale_number(x: float) -> str:
    """Approximates JS ``Number.prototype.toLocaleString()`` for the
    SAF-levy year-total suffix: thousands separators, no forced decimal
    places for whole numbers (the common case here -- a per-year rate
    times an integer year count)."""
    if x == int(x):
        return f"{int(x):,}"
    return f"{x:,.2f}"


def _saf_bullets(data: Visa482CostAgreementData) -> list[str]:
    """The SAF-levy lines of the 'D.' disbursement cell -- two
    independent either/or tiers, each individually toggleable and each
    carrying its own year-multiplied total when more than one year
    applies. Matches buildVisa482CostAgreementPdf.ts's own
    ``safSmallTotal``/``safLargeTotal`` suffix logic exactly (module
    docstring point 3)."""
    saf_small = data.saf_levy_per_year_small or "1200"
    saf_large = data.saf_levy_per_year_large or "1800"
    saf_small_year_total = parse_amt(saf_small) * (data.saf_years_small or 1)
    saf_large_year_total = parse_amt(saf_large) * (data.saf_years_large or 1)
    saf_small_suffix = (
        f" ({data.saf_years_small} yrs = AUD {_js_locale_number(saf_small_year_total)})"
        if data.saf_years_small and data.saf_years_small > 1 else ""
    )
    saf_large_suffix = (
        f" ({data.saf_years_large} yrs = AUD {_js_locale_number(saf_large_year_total)})"
        if data.saf_years_large and data.saf_years_large > 1 else ""
    )
    bullets: list[str] = []
    if data.saf_levy_small_applicable:
        bullets.append(f"SAF levy (turnover under $10m): AUD {fmt_amt(saf_small)}/yr{saf_small_suffix}")
    if data.saf_levy_large_applicable:
        bullets.append(f"SAF levy (turnover $10m+): AUD {fmt_amt(saf_large)}/yr{saf_large_suffix}")
    return bullets


def _disb_bullets_html(data: Visa482CostAgreementData, vac_surcharged: float) -> str:
    lines = [
        f"Business Sponsorship Fee: AUD {fmt_amt(data.sponsorship_fee or '420')}",
        f"Nomination Fee: AUD {fmt_amt(data.nomination_fee or '330')}",
        f"Visa Application Charge (VAC) incl. 1.4% surcharge: AUD {fmt_amt(vac_surcharged)} "
        f"({data.stream or 'Core Skills Stream'})",
        *_saf_bullets(data),
    ]
    return bulleted_html(lines)


def _estimate_table(data: Visa482CostAgreementData, vac_surcharged: float) -> Table:
    """The 'D. Estimate of Professional Fees and Internal Expenses' 3-row
    table -- matches buildVisa482CostAgreementPdf.ts's own drawTableRow()
    calls (blank/'Amount' shaded header, centred amount column). Row 2's
    label carries a fixed '(All disbursements incur 1.4% surcharge)'
    sub-note -- unlike 186's/skilled_visa's 'using client card' note,
    this one isn't conditional on lodgement_uses_client_card (which the
    TS source never reads -- see module docstring)."""
    w1 = L.CONTENT_W * 0.50
    w2 = L.CONTENT_W * 0.50
    head_amt_style = ParagraphStyle("V482_EstimateHeadAmt", fontName=L.FONT_BOLD, fontSize=10, alignment=1)
    amt_style = ParagraphStyle("V482_EstimateAmt", fontName=L.FONT_REGULAR, fontSize=10, alignment=1)

    disb_label = (
        "2. Disbursement Lodgement Fees<br/>"
        '<font size="8">(All disbursements incur 1.4% surcharge)</font>'
    )

    data_rows = [
        ["", PT("Amount", head_amt_style)],
        [PT("1. Professional Cost", L.STYLE_BODY),
         P(f"${esc(fmt_amt(data.professional_fee))} inclusive of GST", amt_style)],
        [P(disb_label, L.STYLE_BODY), P(_disb_bullets_html(data, vac_surcharged), _STYLE_DISB_CELL)],
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
    buildVisa482CostAgreementPdf.ts's 'E. Payment Schedule for Cost and
    Disbursement' rows (3 default stages + optional extras)."""
    w1 = L.CONTENT_W * 0.70
    w2 = L.CONTENT_W * 0.30
    amt_style = ParagraphStyle("V482_PaySchedAmt", fontName=L.FONT_REGULAR, fontSize=10.5, alignment=1)
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


def _build_payment_stages(data: Visa482CostAgreementData) -> list[tuple[str, str]]:
    stages = [
        (data.payment_stage1_label or "1. When we start the SBS application", f"${fmt_amt(data.payment_stage1_amount)}"),
        (data.payment_stage2_label or "2. On the day of nomination lodgement", f"${fmt_amt(data.payment_stage2_amount)}"),
        (data.payment_stage3_label or "3. On the day of application lodgement", f"${fmt_amt(data.payment_stage3_amount)}"),
    ]
    for i, extra in enumerate(data.extra_stages):
        label = extra.get("label") or f"{3 + i + 1}. Stage"
        amount = f"${fmt_amt(extra.get('amount'))}"
        stages.append((label, amount))
    return stages


def _applications_included_row(apps: dict | None) -> list:
    """The optional 'Applications included in this engagement' heading +
    3-checkbox row (SBS / Nomination / Visa), centred as three equal
    columns -- unique to this agreement type. Returns [] if ``apps`` is
    None (matching the TS source's own ``if (data.applicationsIncluded)``
    guard)."""
    if not apps:
        return []
    items = [
        (bool(apps.get("sbs")), "SBS (Standard Business Sponsorship)"),
        (bool(apps.get("nomination")), "Nomination"),
        (bool(apps.get("visa")), "Visa"),
    ]
    cell_w = L.CONTENT_W / 3
    cells = []
    for checked, label in items:
        inner = Table([[_MiniCheckbox(checked), PT(label, _STYLE_APP_LABEL)]], colWidths=[14, None])
        inner.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        cells.append(inner)
    row = Table([cells], colWidths=[cell_w, cell_w, cell_w])
    row.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [
        P("Applications included in this engagement:", _STYLE_APP_HEADING),
        Spacer(1, 6),
        row,
        Spacer(1, 6),
    ]


def _build_story(data: Visa482CostAgreementData, today_short: str) -> list:
    story: list = []

    # ═══════════════════════════════════════ PAGE 1 — Agreement details
    if data.translation_banner_text:
        banner_style = ParagraphStyle(
            "V482_TransBanner", fontName=L.FONT_REGULAR, fontSize=8, leading=14,
            textColor=L.WHITE, backColor=L.TRANSLATION_BANNER_BG, leftIndent=8,
        )
        story.append(PT(data.translation_banner_text, banner_style))
        story.append(Spacer(1, 10))

    story.append(P("Costs Disclosure and Costs Agreement", L.STYLE_TITLE))
    story.append(Spacer(1, 6))
    date_style = ParagraphStyle("V482_DateLine", fontName=L.FONT_REGULAR, fontSize=10.5, alignment=2)
    story.append(P(f'<font color="{L.hex_of(L.MUTED)}">Date:</font> {esc(data.date)}', date_style))
    story.append(Spacer(1, 8))

    story.extend(_applications_included_row(data.applications_included))

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
    story.append(Spacer(1, 6))

    # ── A. Scope of Work ─────────────────────────────
    story.append(P("A. Scope of Work", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(PT(
        "1. You have instructed us to process the Standard Business Sponsorship "
        "Nomination/Visa application whichever is applicable - for Skills in "
        "Demand (subclass 482).",
        L.STYLE_BODY,
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
    story.append(Spacer(1, 2))
    story.append(PT(
        "I will be the solicitor with principal responsibility for assisting you in this matter.",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 10))

    # ── C. Disbursements and Internal Expenses ────────
    story.append(P("C. Disbursements and Internal Expenses", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(PT(
        "You will need to pay Disbursement Lodgement Fees to the Department by the "
        "time you accept the Nomination and we lodge your visa application. We will "
        "notify you of these Disbursements and you are required to pay them "
        "accordingly or instruct us to assist you with the payment directly.",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 12))

    # ── D. Estimate of Professional Fees and Internal Expenses ──
    story.append(P("D. Estimate of Professional Fees and Internal Expenses", L.STYLE_H2))
    story.append(Spacer(1, 4))

    vac_surcharged = apply_vac_surcharge(data.visa_application_fee or "2770")
    story.append(_estimate_table(data, vac_surcharged))
    story.append(Spacer(1, 10))

    # NOTE: disbursements_cost comes straight from the staff-entered
    # disbursements_sub_total, not from summing the fee fields above --
    # see module docstring point 4 (mirrors the TS source's own inline
    # comment on this exact line).
    professional_cost = parse_amt(data.professional_fee)
    disbursements_cost = parse_amt(data.disbursements_sub_total)
    story.append(cost_summary_table(professional_cost, disbursements_cost, total_suffix=" inclusive of GST"))
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

    # ── E. Payment Schedule for Cost and Disbursement ──
    story.append(P("E. Payment Schedule for Cost and Disbursement (inclusive of GST)", L.STYLE_H2))
    story.append(Spacer(1, 6))
    story.append(_payment_schedule_table(_build_payment_stages(data)))
    story.append(Spacer(1, 14))

    story.append(PT("Payment of estimated legal fees to the account below:", L.STYLE_ITALIC_MUTED))
    story.append(Spacer(1, 6))
    story.append(bank_details_box())
    story.append(Spacer(1, 14))

    # ── F. Breach of Payment Schedule and Termination ──
    story.append(P("F. Breach of Payment Schedule and Termination", L.STYLE_H2))
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

    # ── G. Processing Times and Outcome ────────────────
    story.append(P("G. Processing Times and Outcome", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(PT(
        "Based on current processing times, once the business sponsorship "
        f"application is approved ({data.sponsorship_processing_time or 'Depends upon DoHA Global Processing Times'}), "
        "we can start to lodge the nomination application. The processing time for "
        f"nomination is between {data.nomination_processing_time or 'Depends upon DoHA Global Processing Times'}, "
        "and the processing time for the visa application depends on the stream "
        f"under which the visa is being applied for ({data.visa_processing_time or 'Depends upon DoHA Global Processing Times'}).",
        L.STYLE_BODY_SMALL,
    ))
    story.append(Spacer(1, 8))
    story.append(PT(
        "The processing times are not definitive and are completely dependent on "
        "the Department of Home Affairs.",
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

    # ── H. Acknowledgement and Acceptance of Offer ─────
    story.append(P("H. Acknowledgement and Acceptance of Offer", L.STYLE_H2))
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


def _what_we_you_must_do() -> list:
    wmd = [
        "Act in your best legitimate interests, with honesty, fairness and integrity. "
        "Ensure that our advice is timely and accurate.",
        "Do nothing to increase your costs unnecessarily. Keep accurate and complete "
        "records of your case. Do everything reasonably necessary to perform the "
        "services listed in this agreement where the services are not listed in full details.",
        "Provide you with advice about the processes, issues and legal requirements "
        "involved in your Subclass 482 visa application, including business "
        "sponsorship and nomination stages.",
        "Lodge the business sponsorship, nomination and Subclass 482 visa "
        "applications to the Department of Home Affairs, with supporting "
        "documentation, liaise with you as to necessary application, and inform of "
        "the result.",
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
    ila_head_style = ParagraphStyle("V482_ILA", fontName=L.FONT_BOLD, fontSize=9, textColor=L.NAVY)
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
