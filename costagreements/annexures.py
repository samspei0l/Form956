"""Annexures A-D, transcribed verbatim from winzoylegal_new's
_shared/costAgreementAnnexures.ts. Content only changes if the source
legal text changes -- the layout mechanism (Platypus flowables) is what
was rewritten.

Each annexure starts on its own page; callers insert a ``PageBreak()``
before extending the story with the flowables returned here.
"""
from __future__ import annotations

from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from . import layout as L
from .components import P, PT, bulleted_html, esc, _scaled_image
from .sigmeta import MeasuredBox, SigMetaState


def _heading(text: str) -> Paragraph:
    return PT(text, L.STYLE_TERMS_TITLE)


def annexure_a_flowables() -> list:
    return [
        _heading("ANNEXURE A — DISCLOSURE OF INTERESTS"),
        Spacer(1, 8),
        PT(
            "We disclose that we have received or will receive financial benefits "
            "(e.g., commission) as a result of providing non-migration advice to you "
            "in relation to:",
            L.STYLE_TERMS_BODY,
        ),
        Spacer(1, 6),
        P(bulleted_html([
            "Health insurance",
            "Educational institutes or universities",
            "Use of credit card to make payment on your behalf",
        ]), L.STYLE_TERMS_BODY),
        Spacer(1, 10),
        PT(
            "You accept that we are not providing expert advice in relation to the "
            "subject matter of any contracts or arrangements that are the subject of "
            "this Annexure, and, to the maximum extent permitted by law, we shall bear "
            "no liability to you or any third party resulting from the provision of "
            "advice under this Annexure.",
            L.STYLE_TERMS_BODY,
        ),
    ]


def annexure_b_flowables() -> list:
    italic_style = ParagraphStyle("CA_AnnexBItalic", fontName=L.FONT_ITALIC, fontSize=10, textColor=L.NAVY)
    return [
        _heading("ANNEXURE B — REFERRALS"),
        Spacer(1, 8),
        PT("Services Offered (if applicable):", italic_style),
        Spacer(1, 6),
        P(bulleted_html([
            "We have tie-ups with Australian educational institutions and agents. "
            "You are advised about related colleges with your desired course.",
            "We have tie-ups with health insurance companies. You are provided all "
            "insurance policy options and related charges.",
            "You acknowledge and agree that we are offered a financial benefit "
            "(e.g., commission amount) in providing referrals to the concerned companies.",
        ], gap="<br/><br/>"), L.STYLE_TERMS_BODY),
    ]


def annexure_c_flowables(client_name: str, client_sig_bytes: bytes | None,
                          sigmeta: SigMetaState | None = None) -> list:
    """VEVO consent form + client acknowledgement box.

    The date field is intentionally left blank -- winzoylegal_new only
    fills it in once a remote-signing flow stamps the signed date, which
    is out of scope for this synchronous-only Flask port; leaving it blank
    here for print/manual completion matches what an unsigned document
    looks like in the source app.
    """
    box_w = L.CONTENT_W
    box_h = 95
    name_style = ParagraphStyle("CA_AnnexCName", fontName=L.FONT_BOLD, fontSize=10, textColor=L.NAVY)
    value_style = ParagraphStyle("CA_AnnexCValue", fontName=L.FONT_REGULAR, fontSize=10)

    sig_box_w, sig_box_h = 240, 50
    if client_sig_bytes:
        sig_content = _scaled_image(client_sig_bytes, sig_box_w - 8, sig_box_h - 8)
    else:
        sig_content = Spacer(sig_box_w - 8, sig_box_h - 8)
    date_content = Spacer(sig_box_w - 8, 10)
    if sigmeta is not None:
        sig_content = MeasuredBox(sig_content, sigmeta.annex_c_sig_recorder())
        date_content = MeasuredBox(date_content, sigmeta.annex_c_date_recorder())

    ack_table = Table(
        [
            [PT("Client Name:", name_style), PT(client_name or "", value_style), ""],
            [PT("Client Signature:", name_style), sig_content, ""],
            [PT("Date:", name_style), date_content, ""],
        ],
        colWidths=[95, sig_box_w, box_w - 95 - sig_box_w],
        rowHeights=[box_h * 0.32, box_h * 0.5, box_h * 0.18],
    )
    ack_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, L.NAVY),
        ("LINEBELOW", (1, 0), (1, 0), 0.4, L.BLACK),
        ("LINEBELOW", (1, 1), (1, 1), 0.4, L.BLACK),
        ("LINEBELOW", (1, 2), (1, 2), 0.4, L.BLACK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 1), (1, 1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    return [
        _heading("ANNEXURE C — VEVO CONSENT FORM"),
        Spacer(1, 8),
        PT(
            'I hereby acknowledge and give my express consent to all Registered '
            'Migration Agents ("Agents") and Legal Practitioners of Winzoy Legal Pty '
            "Ltd to access the Department of Home Affairs' Visa Entitlement "
            "Verification Online (VEVO) service database.",
            L.STYLE_TERMS_BODY,
        ),
        Spacer(1, 6),
        PT(
            "This consent authorises the firm to obtain information regarding my visa "
            "status, study entitlements, and work entitlements prior to, during, or "
            "after my consultation. I further agree that the firm may continue to make "
            "regular, ongoing checks of my visa status and entitlements using the "
            "information provided by me including my passport number, date of birth, "
            "country of citizenship and any other information required for the purpose.",
            L.STYLE_TERMS_BODY,
        ),
        Spacer(1, 6),
        PT(
            "I have been duly informed and I understand that the information obtained "
            "through the Visa Entitlement Verification Online (VEVO) service database "
            "will be used to:",
            L.STYLE_TERMS_BODY,
        ),
        Spacer(1, 4),
        P(bulleted_html([
            "Check and determine my current visa status; and/or",
            "Determine my eligibility for application for any visa for entry into Australia; and/or",
            "Any other matters related to my consultation with the Agents.",
        ]), L.STYLE_TERMS_BODY),
        Spacer(1, 6),
        PT(
            "I consent to the Agents continuing to make regular checks of my visa "
            "status or entitlements through the Department of Home Affairs' Visa "
            "Entitlement Verification Online (VEVO) service database throughout my "
            "consultation with them.",
            L.STYLE_TERMS_BODY,
        ),
        Spacer(1, 6),
        PT(
            "I understand that the Commonwealth may use information collected through "
            "the inquiry to determine if I am complying with my visa conditions and "
            "locate me if I am not entitled to be in Australia.",
            L.STYLE_TERMS_BODY,
        ),
        Spacer(1, 12),
        ack_table,
    ]


def annexure_d_flowables() -> list:
    link_style = ParagraphStyle("CA_AnnexDLink", fontName=L.FONT_REGULAR, fontSize=10,
                                 textColor=L.LINK_BLUE, leftIndent=12)
    bold_navy = ParagraphStyle("CA_AnnexDBold", fontName=L.FONT_BOLD, fontSize=10, textColor=L.NAVY)

    def link(url: str) -> Paragraph:
        return Paragraph(f'<link href="{esc(url)}"><u>{esc(url)}</u></link>', link_style)

    return [
        _heading("ANNEXURE D — ACKNOWLEDGEMENT OF RECEIPT OF CODE OF CONDUCT AND CONSUMER GUIDE"),
        Spacer(1, 8),
        PT(
            "I hereby acknowledge that I have received and been provided with access "
            "to the following documents by Winzoy Legal Pty Ltd and its Registered "
            "Migration Agents:",
            L.STYLE_TERMS_BODY,
        ),
        Spacer(1, 4),
        P(bulleted_html([
            "The Migration Agents Code of Conduct; and",
            "The Consumer Guide for Registered Migration Agents.",
        ]), L.STYLE_TERMS_BODY),
        Spacer(1, 8),
        PT("I confirm that I have been advised that these documents contain important information regarding:",
           L.STYLE_TERMS_BODY),
        Spacer(1, 4),
        P(bulleted_html([
            "The professional obligations and standards that Registered Migration Agents must comply with;",
            "My rights and responsibilities as a client;",
            "The services that may be provided by a Registered Migration Agent;",
            "How fees, costs and complaints are managed; and",
            "Information regarding the regulation of Registered Migration Agents in Australia.",
        ]), L.STYLE_TERMS_BODY),
        Spacer(1, 8),
        PT("I understand that copies of these documents are available at the following websites:",
           L.STYLE_TERMS_BODY),
        Spacer(1, 8),
        PT("Migration Agents Code of Conduct:", bold_navy),
        Spacer(1, 4),
        link("https://www.mara.gov.au/tools-for-registered-agents/code-of-conduct"),
        Spacer(1, 8),
        PT("Consumer Guide – Registered Migration Agents:", bold_navy),
        Spacer(1, 4),
        link("https://www.mara.gov.au/get-help-visa-subsite/FIles/consumer_guide_english.pdf"),
        Spacer(1, 10),
        PT(
            "I acknowledge that I have been given a reasonable opportunity to read "
            "and review these documents and to ask any questions regarding their contents.",
            L.STYLE_TERMS_BODY,
        ),
    ]
