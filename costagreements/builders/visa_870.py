"""Builds the Visa 870 (Sponsored Parent Temporary visa) Cost Agreement PDF.

Content (every clause, bullet, and disclosure paragraph) is transcribed
verbatim from winzoylegal_new's
src/features/visa870CostAgreement/buildVisa870CostAgreementPdf.ts
(schema mirrored from that feature's types.ts) -- only the layout
mechanism changed. See costagreements/layout.py for why: ReportLab
Platypus flowables replace pdf-lib's manual y-coordinate bookkeeping,
which is what caused winzoylegal_new's long-running client-initials/
signature/date spacing bugs.

visa_482.py and skilled_visa.py are the closest analogs (other single-
visa-pathway cost agreements with SIGMETA-based signatures), but this
type differs from both of them in several *major* structural ways, all
confirmed by reading the TS source (and its shared helper,
``_shared/costAgreementAnnexures.ts``) directly rather than assumed from
precedent:

  1. **No "WHAT WE MUST DO / WHAT YOU MUST DO", "REGULATORY COMPLIANCE
     AND APPLICABLE LAW", or "General Terms of Business" sections at
     all.** buildVisa870CostAgreementPdf.ts's tail end calls only
     ``appendCostAgreementAnnexures(...)`` (Annexures A/B/C) and,
     conditionally, ``appendAnnexureD(...)``. Reading
     ``_shared/costAgreementAnnexures.ts`` directly confirms that shared
     helper draws *only* Annexures A, B, C and D -- no "what we/you must
     do" two-column box, no regulatory-compliance paragraphs, no 15-clause
     General Terms of Business. Every other already-ported visa-pathway
     sibling (visa_482.py, skilled_visa.py, partner_visa.py, ...) draws
     those three sections locally as private helpers *in addition to*
     calling the Annexure A/B/C/D module -- this builder is the first one
     that genuinely doesn't have them, because the TS source doesn't
     either. This makes the ported PDF much shorter: page 1, then
     Annexure A, B, C, and optionally D. Nothing else.
  2. **No lettered A/B/C/D/E/F/G/H section scheme.** Every other visa
     cost agreement in this package numbers its page-1 sections
     ("A. Scope of Work", "B. Professional Fees", ...). Visa 870's TS
     source uses plain, unlettered headings instead: "CAPACITY OF
     REPRESENTATIVE", a two-column "OUR WORKS FOR YOU / OUR PROFESSIONAL
     FEE" table, an unheaded estimate-days paragraph, an unheaded
     disbursement table, an unheaded total-cost paragraph + "Total
     Professional Fee:" row, "PAYMENT SCHEDULE", and "ACKNOWLEDGEMENT".
     There is also no "This document, together with our General Terms of
     Business... constitutes our costs agreement and disclosure..."
     intro paragraph anywhere in the TS source (confirmed by reading it
     in full) -- every other sibling has that paragraph right after the
     parties table; this one goes straight from the parties table to
     CAPACITY OF REPRESENTATIVE.
  3. **A dedicated mid-page-1 "Client Initials: ___  Representative
     Initials: ___" row** (``drawLine`` + two labelled underlines in the
     TS source), separate from the full end-of-page signature block.
     ``costagreements/components.py``'s ``initials_row()`` was built
     specifically for this: its own docstring names
     "buildVisa870CostAgreementPdf.ts's dedicated initials line" as the
     thing it mirrors. No other already-ported sibling calls it.
  4. **The Visa Application Charge (VAC) is a menu of options, not a
     single figure.** visa_482/skilled_visa each disclose exactly one VAC
     amount. Here, up to three VAC options can be listed as bullet
     sub-rows under a single "Visa Application Charge (VAC) incl. 1.4%
     surcharge:" header cell: an "up to 3 years" tier and an "up to 5
     years" tier (each independently toggleable via ``show_vac_3yr``/
     ``show_vac_5yr``, defaulting to shown -- the TS source's own
     ``data.showVac3yr !== false`` check, mirrored here the same way
     visa_482.py's ``saf_levy_*_applicable`` flags mirror the identical
     JS idiom: "true unless the payload explicitly sends `false`"), plus
     an optional fully custom third option
     (``visa_application_fee_other_label``/``_amount``, both required
     together -- the TS source's own ``hasOther`` check). The whole
     disbursement block (header + Service Fee row + Sponsorship
     Application Fee row + the VAC sub-block) is rendered here as a
     single ReportLab Table so the outer border and the single vertical
     column divider stay continuous the way ``drawTableRow``'s per-column
     bordered rectangles do in the TS source, while *no* horizontal line
     is drawn between the VAC header and its bullet rows or between
     consecutive bullet rows (only ``drawTableRow``'s three fixed rows --
     header/service-fee/sponsorship-fee -- get full grid lines; the VAC
     block is two continuous bordered rectangles with free-flowing text
     inside, which a table with ``GRID`` restricted to just those three
     rows plus an unbroken outer ``BOX``/``LINEBEFORE`` reproduces
     exactly).
  5. **``total_cost`` is directly rendered, not vestigial.** Unlike every
     other sibling's own ``total_cost`` field (present in its TS
     ``types.ts`` but never actually drawn -- see visa_482.py's module
     docstring), this TS source *does* read ``data.totalCost`` directly
     into two places: the "we estimate that your total costs will be
     approximately $X" paragraph and the bold "Total Professional Fee:"
     row beneath it. Nothing sums the disbursement/professional fee
     fields into it server-side (there is no ``cost_summary_table()``
     call anywhere in this builder). Following the same reasoning
     visa_482.py's module docstring applies to its own load-bearing
     ``disbursements_sub_total`` field, ``total_cost`` is therefore
     included in ``REQUIRED_FIELDS`` here, unlike every sibling that
     leaves its own unused ``total_cost`` optional.
  6. **Compact signature block at ``sig_h=44``**, matching the TS
     source's own ``const sigH = 44;`` -- this is ``compact_signature_
     block()``'s own default parameter value (see
     ``costagreements/components.py``, which names Visa 870 as one of
     its three known compact-block callers), unlike Visa 482's
     ``sig_h=36``.
  7. **Disbursement header cell literally reads "Disbursement"** (plus an
     optional " (Paid by client's card)" suffix when
     ``lodgement_uses_client_card`` is set), not the "2. Disbursement
     Lodgement Fee(s)" numbered-line convention every other sibling uses.
  8. **No payment-stage extras.** ``types.ts`` has no ``extraStages``
     field (unlike visa_482's/skilled_visa's own optional
     ``extra_stages`` list) -- exactly three fixed payment stages here,
     with TS-literal default labels ("On the day the Cost Agreement is
     signed" / "...the Sponsorship application is lodged" / "...the Visa
     application is lodged").
  9. Uses SigMetaState (costagreements/sigmeta.py) -- the TS source calls
     ``pdfDoc.setSubject('SIGMETA:' + ...)`` itself, and every sibling
     builder that already ported that behaviour does the same here.

Structural decisions where the TS source was ambiguous:
  - ``REQUIRED_FIELDS``: types.ts marks almost every field as required
    (all the VAC/SAF-adjacent booleans, both payment-stage triples, marn,
    lpn, lodgementUsesClientCard) but, following the precedent set by
    every sibling builder, only the fields load-bearing for page 1's body
    text/cost math to render sensibly are required here:
    date/our_ref/client_name/client_address/capacity/professional_fee/
    rep_name, plus total_cost (see point 5 above -- this one *is*
    required, unlike every sibling's own unused total_cost, because
    nothing derives it server-side and it's directly printed twice).
    estimated_days, service_fee, sponsorship_application_fee, both VAC
    tier amounts, all three payment-stage label/amount pairs, marn, and
    lpn all have the TS source's own literal fallback text/defaults
    ('05', '0', '425.88', '6,155', '12,310', "") and are therefore
    optional here, exactly like every sibling's own optional fields.
  - ``show_vac_3yr``/``show_vac_5yr``: typed as plain (non-optional)
    booleans in types.ts but read via a ``!== false`` check in the TS
    builder (i.e. "shown unless explicitly false") -- parsed here as
    ``bool(payload.get(..., True))``, matching visa_482.py's own
    ``saf_levy_*_applicable`` precedent for the identical JS idiom.
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
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    PageBreak,
    PageTemplate,
    Spacer,
    Table,
    TableStyle,
)

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
    decode_data_uri,
    esc,
    initials_row,
    parties_table,
    staff_note_box,
)
from ..money import fmt_amt, parse_amt
from ..sigmeta import SigMetaState

SIG_BOX_H = 44  # matches buildVisa870CostAgreementPdf.ts's `const sigH = 44;`


# --------------------------------------------------------------------- schema
@dataclass
class Visa870CostAgreementData:
    """Payload shape for the Visa 870 Cost Agreement, mirroring
    winzoylegal_new's visa870CostAgreement/types.ts field-for-field
    (snake_case to match this API's existing JSON convention)."""

    # Header
    date: str  # DD/MM/YYYY (or YYYY-MM-DD, normalised before this is built)
    our_ref: str
    client_name: str
    client_address: str

    # Capacity of representative (top of page 1)
    capacity: str  # "solicitor" | "rma"

    # Fees
    professional_fee: str
    total_cost: str  # directly rendered -- see module docstring point 5

    rep_name: str

    estimated_days: str = "05"

    service_fee: str = ""                     # default '0'
    sponsorship_application_fee: str = ""      # default '425.88'
    visa_application_fee_3yr: str = ""         # default '6,155'
    visa_application_fee_5yr: str = ""         # default '12,310'
    show_vac_3yr: bool = True
    show_vac_5yr: bool = True
    visa_application_fee_other_label: str = ""
    visa_application_fee_other_amount: str = ""
    lodgement_uses_client_card: bool = False

    # Payment schedule (3 fixed stages -- no extras, unlike visa_482/skilled_visa)
    payment_stage1_label: str = ""
    payment_stage1_amount: str = ""
    payment_stage2_label: str = ""
    payment_stage2_amount: str = ""
    payment_stage3_label: str = ""
    payment_stage3_amount: str = ""

    # Signature block
    marn: str = ""
    lpn: str = ""
    rep_signature_data: str | None = None       # base64 data URI -- see validate_visa_870_cost_agreement()
    client_signature_data: str | None = None     # base64 data URI
    rep_signature_url: str | None = None         # metadata passthrough only, never fetched -- see sigmeta.py

    staff_note: str = ""
    ack_languages: list[str] = field(default_factory=list)
    translation_banner_text: str = ""
    translated_ack_text: str = ""
    include_annexure_d: bool = False

    @classmethod
    def from_payload(cls, payload: dict) -> "Visa870CostAgreementData":
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
            total_cost=str(payload.get("total_cost") or ""),
            rep_name=str(payload.get("rep_name") or ""),
            estimated_days=str(payload.get("estimated_days") or "05"),
            service_fee=str(payload.get("service_fee") or ""),
            sponsorship_application_fee=str(payload.get("sponsorship_application_fee") or ""),
            visa_application_fee_3yr=str(payload.get("visa_application_fee_3yr") or ""),
            visa_application_fee_5yr=str(payload.get("visa_application_fee_5yr") or ""),
            show_vac_3yr=bool(payload.get("show_vac_3yr", True)),
            show_vac_5yr=bool(payload.get("show_vac_5yr", True)),
            visa_application_fee_other_label=str(payload.get("visa_application_fee_other_label") or ""),
            visa_application_fee_other_amount=str(payload.get("visa_application_fee_other_amount") or ""),
            lodgement_uses_client_card=bool(payload.get("lodgement_uses_client_card", False)),
            payment_stage1_label=str(payload.get("payment_stage1_label") or ""),
            payment_stage1_amount=str(payload.get("payment_stage1_amount") or ""),
            payment_stage2_label=str(payload.get("payment_stage2_label") or ""),
            payment_stage2_amount=str(payload.get("payment_stage2_amount") or ""),
            payment_stage3_label=str(payload.get("payment_stage3_label") or ""),
            payment_stage3_amount=str(payload.get("payment_stage3_amount") or ""),
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
    "professional_fee", "total_cost", "rep_name",
})

VALID_CAPACITIES = frozenset({"solicitor", "rma"})
MONEY_FIELDS = frozenset({
    "professional_fee", "total_cost", "service_fee", "sponsorship_application_fee",
    "visa_application_fee_3yr", "visa_application_fee_5yr", "visa_application_fee_other_amount",
    "payment_stage1_amount", "payment_stage2_amount", "payment_stage3_amount",
})
IMAGE_FIELDS = frozenset({"client_signature_data", "rep_signature_data"})


def validate_visa_870_cost_agreement(payload: dict) -> list[ValidationError]:
    """Validate a Visa 870 Cost Agreement payload. Empty list = valid."""
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


def apply_visa_870_normalisations(payload: dict) -> dict:
    """Return a shallow copy of ``payload`` with 'date' normalised to DD/MM/YYYY."""
    out = dict(payload)
    v = out.get("date")
    if isinstance(v, str) and DATE_YYYYMMDD.match(v):
        out["date"] = normalise_date(v)
    return out


# --------------------------------------------------------------------- builder
_STYLE_DISB_SMALL = ParagraphStyle(
    "V870_DisbSmall", fontName=L.FONT_REGULAR, fontSize=10, leading=13, textColor=L.BLACK,
)
_STYLE_DISB_AMT = ParagraphStyle(
    "V870_DisbAmt", fontName=L.FONT_REGULAR, fontSize=11, leading=14, textColor=L.BLACK, alignment=1,
)
_STYLE_DISB_AMT_BOLD = ParagraphStyle(
    "V870_DisbAmtBold", fontName=L.FONT_BOLD, fontSize=11, leading=14, textColor=L.BLACK, alignment=1,
)
_STYLE_DISB_HEAD_AMT = ParagraphStyle(
    "V870_DisbHeadAmt", fontName=L.FONT_BOLD, fontSize=10, leading=12.5, textColor=L.BLACK, alignment=1,
)
_STYLE_WORKS_FEE = ParagraphStyle(
    "V870_WorksFee", fontName=L.FONT_BOLD, fontSize=11, leading=14, textColor=L.BLACK, alignment=1,
)
_STYLE_TOTAL_LABEL = ParagraphStyle(
    "V870_TotalLabel", fontName=L.FONT_BOLD, fontSize=12, leading=15, textColor=L.BLACK,
)
_STYLE_TOTAL_AMT = ParagraphStyle(
    "V870_TotalAmt", fontName=L.FONT_BOLD, fontSize=11, leading=14, textColor=L.BLACK, alignment=1,
)
_STYLE_PAYSCHED_AMT = ParagraphStyle(
    "V870_PaySchedAmt", fontName=L.FONT_REGULAR, fontSize=10.5, leading=13, alignment=1,
)


def build_visa_870_cost_agreement(data: Visa870CostAgreementData) -> bytes:
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
        chrome.draw_header(canvas, doc, doc_id, generated_at, "SUBCLASS 870 (SPONSORED PARENT)")

    template = PageTemplate(id="main", frames=[frame], onPage=on_page)
    doc = BaseDocTemplate(
        buf, pagesize=(L.PAGE_W, L.PAGE_H), pageTemplates=[template],
        title="Costs Disclosure and Costs Agreement (Visa 870)", author="Winzoy Legal",
        topMargin=0, bottomMargin=0, leftMargin=0, rightMargin=0,
    )
    doc.build(story, canvasmaker=chrome.make_canvas_factory(doc_id, generated_at))
    return buf.getvalue()


def _make_doc_id(data: Visa870CostAgreementData) -> str:
    seed = f"V870|{data.our_ref}|{data.client_name}|{time.time()}"
    h = 0
    for ch in seed:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    hex_str = format(h, "08x").upper()
    return f"WZL-V870-{hex_str[:4]}-{hex_str[4:8]}"


def _build_ack_text(client_name: str, languages: list[str]) -> str:
    langs = [lang for lang in (languages or []) if lang] or ["English"]
    lang_list = " and/or ".join(langs)
    name_phrase = f'I, "{client_name}",' if client_name and client_name.strip() else "I"
    return (
        f"{name_phrase} acknowledge that I understand and accepted the above agreement. "
        f"The agreement has been explained to me and my partner in {lang_list}."
    )


def _works_fee_table(bullets: list[str], fee_text: str) -> Table:
    """The 'OUR WORKS FOR YOU / OUR PROFESSIONAL FEE' two-column table --
    matches buildVisa870CostAgreementPdf.ts's own two navy header
    rectangles + shared light-blue body background, 50/50 split (not the
    65/35 split of the generic ``works_fee_table()`` in components.py,
    which also uses different header text -- this is a local, faithful
    variant instead of reusing that shared helper)."""
    w1 = L.CONTENT_W * 0.5
    w2 = L.CONTENT_W * 0.5
    data = [
        [PT("OUR WORKS FOR YOU", L.STYLE_TABLE_HEAD_WHITE), PT("OUR PROFESSIONAL FEE", L.STYLE_TABLE_HEAD_WHITE)],
        [P(bulleted_html(bullets), L.STYLE_BODY_SMALL), P(esc(fee_text), _STYLE_WORKS_FEE)],
    ]
    t = Table(data, colWidths=[w1, w2])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, L.BLACK),
        ("BACKGROUND", (0, 0), (-1, 0), L.NAVY),
        ("BACKGROUND", (0, 1), (-1, 1), L.FINAL_BG),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def _service_fee_text(service_fee: str) -> str:
    """``data.serviceFee && data.serviceFee !== '0' ? '$'+fmtAmt(...) : '$0'``"""
    if service_fee and service_fee.strip() and service_fee.strip() != "0":
        return f"${fmt_amt(service_fee)}"
    return "$0"


def _vac_options(data: Visa870CostAgreementData) -> list[tuple[str, str]]:
    opts: list[tuple[str, str]] = []
    if data.show_vac_3yr:
        opts.append(("up to 3 years", data.visa_application_fee_3yr or "6,155"))
    if data.show_vac_5yr:
        opts.append(("up to 5 years", data.visa_application_fee_5yr or "12,310"))
    if data.visa_application_fee_other_label and data.visa_application_fee_other_amount:
        opts.append((data.visa_application_fee_other_label, data.visa_application_fee_other_amount))
    return opts


def _disbursement_table(data: Visa870CostAgreementData) -> Table:
    """Header + Service Fee row + Sponsorship Application Fee row + VAC
    option sub-block, all as a single Table -- see module docstring
    point 4 for why this is one flowable instead of several: the TS
    source's own ``drawTableRow`` gives the first three rows full grid
    lines, while the VAC block below them is two continuous bordered
    rectangles (outer box + one vertical divider, no horizontal lines
    between the VAC header and its bullet rows). Restricting ``GRID`` to
    just the header/service-fee/sponsorship-fee rows, and adding a
    standalone ``BOX``/``LINEBEFORE`` across every row, reproduces that
    exactly."""
    w1 = L.CONTENT_W * 0.62
    w2 = L.CONTENT_W * 0.38

    header_left = "Disbursement" + (" (Paid by client's card)" if data.lodgement_uses_client_card else "")
    rows: list[list] = [
        [PT(header_left, L.STYLE_BOLD), P("Amount (incl 1.4%<br/>credit card surcharge)", _STYLE_DISB_HEAD_AMT)],
        [PT("-  Service Fee (Photocopies, postage)", L.STYLE_BODY),
         P(esc(_service_fee_text(data.service_fee)), _STYLE_DISB_AMT)],
        [PT("-  Sponsorship Application Fee", L.STYLE_BODY),
         P(f"${esc(fmt_amt(data.sponsorship_application_fee or '425.88'))}", _STYLE_DISB_AMT)],
        [PT("-  Visa Application Charge (VAC) incl. 1.4% surcharge:", _STYLE_DISB_SMALL), ""],
    ]
    for label, amount in _vac_options(data):
        rows.append([
            PT(f"   •  {label}", _STYLE_DISB_SMALL),
            P(f"${esc(fmt_amt(amount))}", _STYLE_DISB_AMT_BOLD),
        ])

    t = Table(rows, colWidths=[w1, w2])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, 2), 0.5, L.BLACK),
        ("BOX", (0, 0), (-1, -1), 0.5, L.BLACK),
        ("LINEBEFORE", (1, 0), (1, -1), 0.5, L.BLACK),
        ("BACKGROUND", (0, 0), (-1, 0), L.GRAY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _total_professional_fee_row(total_cost_text: str) -> Table:
    w1 = L.CONTENT_W * 0.62
    w2 = L.CONTENT_W * 0.38
    t = Table(
        [[PT("Total Professional Fee:", _STYLE_TOTAL_LABEL), P(esc(total_cost_text), _STYLE_TOTAL_AMT)]],
        colWidths=[w1, w2],
    )
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, L.BLACK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def _payment_schedule_table(stages: list[tuple[str, str]]) -> Table:
    """A bordered label/amount table with no header row -- matches
    buildVisa870CostAgreementPdf.ts's 'PAYMENT SCHEDULE' rows (3 fixed
    stages, no extras -- see module docstring point 8)."""
    w1 = L.CONTENT_W * 0.70
    w2 = L.CONTENT_W * 0.30
    data = [[PT(label, L.STYLE_TABLE_CELL), P(esc(amount_text), _STYLE_PAYSCHED_AMT)] for label, amount_text in stages]
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


def _build_payment_stages(data: Visa870CostAgreementData) -> list[tuple[str, str]]:
    return [
        (data.payment_stage1_label or "1. On the day the Cost Agreement is signed",
         f"${fmt_amt(data.payment_stage1_amount)}"),
        (data.payment_stage2_label or "2. On the day the Sponsorship application is lodged",
         f"${fmt_amt(data.payment_stage2_amount)}"),
        (data.payment_stage3_label or "3. On the day the Visa application is lodged",
         f"${fmt_amt(data.payment_stage3_amount)}"),
    ]


def _build_story(data: Visa870CostAgreementData, today_short: str) -> list:
    story: list = []

    # ═══════════════════════════════════════ PAGE 1 — Agreement details
    if data.translation_banner_text:
        banner_style = ParagraphStyle(
            "V870_TransBanner", fontName=L.FONT_REGULAR, fontSize=8, leading=14,
            textColor=L.WHITE, backColor=L.TRANSLATION_BANNER_BG, leftIndent=8,
        )
        story.append(PT(data.translation_banner_text, banner_style))
        story.append(Spacer(1, 10))

    story.append(P("Costs Disclosure and Costs Agreement", L.STYLE_TITLE))
    story.append(Spacer(1, 6))
    date_style = ParagraphStyle("V870_DateLine", fontName=L.FONT_REGULAR, fontSize=10.5, alignment=2)
    story.append(P(f'<font color="{L.hex_of(L.MUTED)}">Date:</font> {esc(data.date)}', date_style))
    story.append(Spacer(1, 10))
    story.append(P("Between", L.STYLE_CENTER_BOLD))
    story.append(Spacer(1, 16))

    story.append(parties_table(data.date, data.our_ref, data.client_name, data.client_address))
    story.append(Spacer(1, 18))

    # ── Capacity of representative (no lead-in sentence -- see module docstring) ──
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
    story.append(Spacer(1, 12))

    # ── OUR WORKS FOR YOU / OUR PROFESSIONAL FEE ──
    scope_items = [
        "Prepare and lodge your Sponsorship Application",
        "Prepare and lodge your Parent Visa (Subclass 870) Application",
        "Follow up until a decision is made",
    ]
    fee_text = f"${fmt_amt(data.professional_fee)} incl GST"
    story.append(_works_fee_table(scope_items, fee_text))
    story.append(Spacer(1, 12))

    # ── Estimate text ──
    story.append(PT(
        f"We estimate that it will take us {esc(data.estimated_days or '05')} days from the date "
        "you provide all the required documents to complete the agreed services, for which our "
        "fixed professional fee will be based on current fees and charges.",
        L.STYLE_BODY,
    ))
    story.append(Spacer(1, 14))

    # ── Disbursement table ──
    story.append(_disbursement_table(data))
    story.append(Spacer(1, 14))

    # ── Total cost estimate text + Total Professional Fee row ──
    story.append(PT(
        "Based on current fees and charges, we estimate that your total costs will be "
        f"approximately ${fmt_amt(data.total_cost)} (incl GST) which may vary due to any further "
        "disbursement (if any).",
        L.STYLE_BODY,
    ))
    story.append(Spacer(1, 6))
    story.append(_total_professional_fee_row(f"${fmt_amt(data.total_cost)} incl GST"))
    story.append(Spacer(1, 18))

    # ── Client / Representative initials row (dedicated -- see module docstring) ──
    story.append(HRFlowable(width="100%", thickness=0.4, color=L.MUTED, spaceAfter=10))
    story.append(initials_row())
    story.append(Spacer(1, 18))

    # ── Payment schedule ──
    story.append(P("PAYMENT SCHEDULE", L.STYLE_H2))
    story.append(Spacer(1, 8))
    story.append(_payment_schedule_table(_build_payment_stages(data)))
    story.append(Spacer(1, 14))

    story.append(PT("Payment of estimated legal fees to the account below:", L.STYLE_ITALIC_MUTED))
    story.append(Spacer(1, 6))
    story.append(bank_details_box())
    story.append(Spacer(1, 16))

    # ── Acknowledgement ──
    story.append(P("ACKNOWLEDGEMENT", L.STYLE_H2))
    story.append(Spacer(1, 8))
    ack_text = data.translated_ack_text or _build_ack_text(data.client_name, data.ack_languages)
    story.append(PT(ack_text, L.STYLE_BODY))
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

    # ═══════════════════════════════════════ ANNEXURES A / B / C / (D)
    # No "WHAT WE / YOU MUST DO", "REGULATORY COMPLIANCE", or "General
    # Terms of Business" sections -- see module docstring point 1.
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
