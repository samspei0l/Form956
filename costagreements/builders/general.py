"""Builds the General Cost Agreement PDF.

Content (every clause, bullet, and disclosure paragraph) is transcribed
verbatim from winzoylegal_new's
src/features/generalCostAgreement/buildGeneralCostAgreementPdf.ts --
only the layout mechanism changed. See costagreements/layout.py for why:
ReportLab Platypus flowables replace pdf-lib's manual y-coordinate
bookkeeping, which is what caused winzoylegal_new's long-running
client-initials/signature/date spacing bugs.

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
from datetime import datetime

from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import BaseDocTemplate, Frame, PageBreak, PageTemplate, Spacer

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
    disbursement_table,
    esc,
    parties_table,
    signature_block,
    staff_note_box,
    two_column_terms_box,
    works_fee_table,
)
from ..money import apply_vac_surcharge, fmt_amt, parse_amt, sum_amounts
from ..schema import GeneralCostAgreementData
from ..sigmeta import SigMetaState


def build_general_cost_agreement(data: GeneralCostAgreementData) -> bytes:
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
        chrome.draw_header(canvas, doc, doc_id, generated_at, data.service_type_label)

    template = PageTemplate(id="main", frames=[frame], onPage=on_page)
    doc = BaseDocTemplate(
        buf, pagesize=(L.PAGE_W, L.PAGE_H), pageTemplates=[template],
        title="Costs Disclosure and Costs Agreement", author="Winzoy Legal",
        topMargin=0, bottomMargin=0, leftMargin=0, rightMargin=0,
    )
    doc.build(story, canvasmaker=chrome.make_canvas_factory(doc_id, generated_at))
    return buf.getvalue()


def _make_doc_id(data: GeneralCostAgreementData) -> str:
    seed = f"{data.our_ref}|{data.client_name}|{time.time()}"
    h = 0
    for ch in seed:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    hex_str = format(h, "08x").upper()
    return f"WZL-{hex_str[:4]}-{hex_str[4:8]}"


def _build_ack_text(client_name: str, languages: list[str]) -> str:
    langs = [lang for lang in (languages or []) if lang] or ["English"]
    lang_list = " and/or ".join(langs)
    name_phrase = f'I, "{client_name}",' if client_name and client_name.strip() else "I"
    return (
        f"{name_phrase} acknowledge that I understand and accepted the above agreement. "
        f"The agreement has been explained to me and my partner in {lang_list}."
    )


def _build_story(data: GeneralCostAgreementData, today_short: str) -> list:
    story: list = []

    # ═══════════════════════════════════════ PAGE 1 — Agreement details
    if data.translation_banner_text:
        banner_style = ParagraphStyle(
            "CA_TransBanner", fontName=L.FONT_REGULAR, fontSize=8, leading=14,
            textColor=L.WHITE, backColor=L.TRANSLATION_BANNER_BG, leftIndent=8,
        )
        story.append(PT(data.translation_banner_text, banner_style))
        story.append(Spacer(1, 10))

    story.append(P("Costs Disclosure and Costs Agreement", L.STYLE_TITLE))
    story.append(Spacer(1, 6))
    date_style = ParagraphStyle("CA_DateLine", fontName=L.FONT_REGULAR, fontSize=10.5, alignment=2)
    story.append(P(f'<font color="{L.hex_of(L.MUTED)}">Date:</font> {esc(data.date)}', date_style))
    story.append(Spacer(1, 10))
    story.append(P("Between", L.STYLE_CENTER_BOLD))
    story.append(Spacer(1, 16))

    story.append(parties_table(data.date, data.our_ref, data.client_name, data.client_address))
    story.append(Spacer(1, 20))

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
    story.append(Spacer(1, 12))

    fee_text = f"${fmt_amt(data.professional_fee)} (Incl. GST)"
    story.append(works_fee_table(data.service_bullets, fee_text))
    story.append(Spacer(1, 16))
    weeks = data.estimated_weeks or "01"
    story.append(PT(
        f"We estimate that it will take us {weeks} week(s) from the date you provide all "
        "the required documents to complete the agreed services, for which our fixed "
        "professional fee will be based on current fees and charges.",
        L.STYLE_BODY,
    ))
    story.append(Spacer(1, 18))

    vac_surcharged = apply_vac_surcharge(data.visa_lodgment_fee)
    lodge_amt = f"${fmt_amt(vac_surcharged)}"
    if data.lodgment_uses_client_card:
        lodge_amt += " (using client card)"
    story.append(disbursement_table([
        ("Service Fee (Photocopies, postage)", f"${fmt_amt(data.service_fee)}"),
        ("Visa Application Charge (VAC) incl. 1.4% surcharge", lodge_amt),
    ]))
    story.append(Spacer(1, 24))

    professional_cost = parse_amt(data.professional_fee)
    disbursements_cost = sum_amounts(data.service_fee) + vac_surcharged
    story.append(cost_summary_table(professional_cost, disbursements_cost, total_suffix=" incl GST"))
    story.append(Spacer(1, 14))

    story.append(P("PAYMENT SCHEDULE", L.STYLE_H2))
    story.append(Spacer(1, 4))
    story.append(PT(
        "You are required to pay our fees immediately after your application has been "
        "completely prepared, finalized, and formally lodged. You will also upon our "
        "request make payment for any disbursement which is incurred during the course "
        "of our work.",
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
        "involved in applying for your application.",
        "Lodge the applications to DoHA, as prepared by us, with the supporting "
        "documentation, liaise with you as to necessary application, and inform of the result.",
        "Professionally prepare your Visa Application form, and any other forms "
        "incidental to the substantive application processes, send these forms to you "
        "for signature. Lodge these forms, with the required supporting documentation "
        "and fees to the appropriate DoHA office.",
        "Inform you of the outcome of your visa application.",
    ]
    ymd = [
        "Let us know about the changes to your circumstances that might affect your "
        "application, for example, marriage or the birth of a child. Advise us "
        "promptly if you change your address for more than fourteen consecutive days "
        "during the processing of a visa application.",
        "Provide us, in a timely manner, with documents and information that we need "
        "to act for you. Ensure that the information you give us is true and accurate. "
        "If you discover that information that you gave us is wrong, let us know at "
        "once so we can make the necessary corrections immediately.",
        "You should be aware that if DoHA discover that you, or anyone else, gave them "
        "information that was false or misleading in a material particular they can "
        "cancel your visa, even if you did not know that the information was false or misleading.",
        "Provide us with the documents described in the attached letters and Documents "
        "Listed or as per our request to you via our correspondences and telephone "
        "attendances, or our previous conference at the office.",
    ]
    ila_head_style = ParagraphStyle("CA_ILA", fontName=L.FONT_BOLD, fontSize=9, textColor=L.NAVY)
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
        "You have a number of rights in relation to our legal costs, including a "
        "right to seek assistance from the Office of the Legal Services Commissioner.",
    ),
    (
        "6 Complaints",
        "(a) For Legal Practitioner (Solicitor) clients: You may make a complaint to "
        "the Office of the Legal Services Commissioner.\n"
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
        "8 Retention of Your Documents and Electronic files",
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
        "us to advance your matter, for example, compromising our ability to comply "
        "with Court directions, orders or practice notes;\n"
        "(c) if you refuse to accept our advice;\n"
        "(d) if you indicate to us or we form the view that you have lost confidence "
        "in us;\n"
        "(e) for any other reason outside our control which has the effect of "
        "compromising our ability to perform the work required within the required "
        "time-frame; or\n"
        "(f) for just cause.",
    ),
    (
        "10 Termination by You",
        "You may terminate our retainer at any time by written notice. You will be "
        "required to pay all our outstanding professional fees and disbursements up "
        "to the date of termination.",
    ),
    (
        "11 Lien",
        "We are entitled to retain by way of lien all your documents in our "
        "possession (including documents brought into existence by us in the course "
        "of the work) until all our costs have been paid.",
    ),
    (
        "12 Privacy",
        "We collect personal information about you in order to provide legal "
        "services. If you do not provide us with the full name and address "
        "information required by law we cannot act for you. If you do not provide us "
        "with the other personal information that we request our advice may be "
        "wrong for you or misleading.\n"
        "Depending on the nature of your matter the types of bodies to whom we may "
        "disclose your personal information include the courts, the other party or "
        "parties to litigation, experts and barristers, the Office of State Revenue, "
        "PEXA Limited, the Land and Property Information Division of the Department "
        "of Lands, the Registrar General and third parties involved in the "
        "completion or processing of a transaction.\n"
        "We do not disclose your information overseas unless your instructions "
        "involve dealing with parties overseas. If your matter involves parties "
        "overseas we may disclose select personal information to overseas "
        "recipients associated with that matter in order to carry out your "
        "instructions.\n"
        "We manage and protect your personal information in accordance with our "
        "privacy policy which can be found on our firm website or a copy of which "
        "we shall provide at your request. Our privacy policy contains information "
        "about how you can access and correct the personal information we hold "
        "about you and how you can raise any concerns about our personal "
        "information handling practices. For more information, please contact us "
        "in writing.",
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
