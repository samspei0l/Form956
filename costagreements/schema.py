"""Payload shape for the General Cost Agreement, mirroring winzoylegal_new's
generalCostAgreement/types.ts field-for-field (snake_case to match this
API's existing JSON convention, e.g. pdfform's ``agent_family_name``).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GeneralCostAgreementData:
    # Header
    date: str  # DD/MM/YYYY (or YYYY-MM-DD, normalised before this is built)
    our_ref: str
    client_name: str
    client_address: str

    # Capacity of representative (top of page 1)
    capacity: str  # "solicitor" | "rma"

    # Works / fee table
    professional_fee: str
    estimated_weeks: str

    # Disbursement table
    service_fee: str
    visa_lodgment_fee: str
    rep_name: str

    service_type_label: str = ""
    service_bullets: list[str] = field(default_factory=list)
    lodgment_uses_client_card: bool = False

    # Signature block
    marn: str = ""
    lpn: str = ""
    rep_signature_data: str | None = None      # base64 data URI -- see costagreements/validate.py
    client_signature_data: str | None = None    # base64 data URI
    rep_signature_url: str | None = None        # metadata passthrough only, never fetched -- see sigmeta.py

    staff_note: str = ""
    ack_languages: list[str] = field(default_factory=list)
    translation_banner_text: str = ""
    translated_ack_text: str = ""
    include_annexure_d: bool = False

    @classmethod
    def from_payload(cls, payload: dict) -> "GeneralCostAgreementData":
        bullets = payload.get("service_bullets") or []
        if isinstance(bullets, str):
            bullets = [b.strip() for b in bullets.split("\n") if b.strip()]

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
            estimated_weeks=str(payload.get("estimated_weeks") or ""),
            service_fee=str(payload.get("service_fee") or ""),
            visa_lodgment_fee=str(payload.get("visa_lodgment_fee") or ""),
            rep_name=str(payload.get("rep_name") or ""),
            service_type_label=str(payload.get("service_type_label") or ""),
            service_bullets=list(bullets),
            lodgment_uses_client_card=bool(payload.get("lodgment_uses_client_card", False)),
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
