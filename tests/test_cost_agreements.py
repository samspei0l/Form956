"""Tests for the General Cost Agreement PDF builder (costagreements/).

Strategy:
  - Real ReportLab build + real PyMuPDF read-back for the PDF round-trip
    (no mocks for the parts that actually touch the PDF).
  - A real Flask test client for the HTTP route, with the on-disk cache
    isolated to ``tmp_path`` (matches tests/test_form956_service.py).
  - The key regression this guards against: winzoylegal_new's pdf-lib
    version had a long history of body content colliding with the
    client-initials/signature/date block near the page bottom (see
    costagreements/layout.py's module docstring). The overlap check here
    asserts the *outcome* (no body text below the content frame boundary,
    on any page) rather than re-implementing the height math the source
    bug came from.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from costagreements import layout as L  # noqa: E402
from costagreements.builders.art import ArtCostAgreementData  # noqa: E402
from costagreements.builders.art import build_art_cost_agreement  # noqa: E402
from costagreements.builders.art import validate_art_cost_agreement  # noqa: E402
from costagreements.builders.bfa import BfaCostAgreementData  # noqa: E402
from costagreements.builders.bfa import build_bfa_cost_agreement  # noqa: E402
from costagreements.builders.bfa import validate_bfa_cost_agreement  # noqa: E402
from costagreements.builders.client_agreement import ClientAgreementData  # noqa: E402
from costagreements.builders.client_agreement import build_client_agreement  # noqa: E402
from costagreements.builders.client_agreement import validate_client_agreement  # noqa: E402
from costagreements.builders.divorce import DivorceCostAgreementData  # noqa: E402
from costagreements.builders.divorce import build_divorce_cost_agreement  # noqa: E402
from costagreements.builders.divorce import validate_divorce_cost_agreement  # noqa: E402
from costagreements.builders.general import build_general_cost_agreement  # noqa: E402
from costagreements.builders.jrp import JrpCostAgreementData  # noqa: E402
from costagreements.builders.jrp import build_jrp_cost_agreement  # noqa: E402
from costagreements.builders.jrp import validate_jrp_cost_agreement  # noqa: E402
from costagreements.builders.partner_visa import PartnerVisaCostAgreementData  # noqa: E402
from costagreements.builders.partner_visa import build_partner_visa_cost_agreement  # noqa: E402
from costagreements.builders.partner_visa import validate_partner_visa_cost_agreement  # noqa: E402
from costagreements.builders.skilled_visa import SkilledVisaCostAgreementData  # noqa: E402
from costagreements.builders.skilled_visa import build_skilled_visa_cost_agreement  # noqa: E402
from costagreements.builders.skilled_visa import validate_skilled_visa_cost_agreement  # noqa: E402
from costagreements.builders.skills_assessment import SkillsAssessmentCostAgreementData  # noqa: E402
from costagreements.builders.skills_assessment import build_skills_assessment_cost_agreement  # noqa: E402
from costagreements.builders.skills_assessment import validate_skills_assessment_cost_agreement  # noqa: E402
from costagreements.builders.skills_assessment_186 import SkillsAssessment186CostAgreementData  # noqa: E402
from costagreements.builders.skills_assessment_186 import build_skills_assessment_186_cost_agreement  # noqa: E402
from costagreements.builders.skills_assessment_186 import validate_skills_assessment_186_cost_agreement  # noqa: E402
from costagreements.builders.skills_assessment_only import SkillsAssessmentOnlyCostAgreementData  # noqa: E402
from costagreements.builders.skills_assessment_only import build_skills_assessment_only_cost_agreement  # noqa: E402
from costagreements.builders.skills_assessment_only import validate_skills_assessment_only_cost_agreement  # noqa: E402
from costagreements.builders.visa_482 import Visa482CostAgreementData  # noqa: E402
from costagreements.builders.visa_482 import build_visa_482_cost_agreement  # noqa: E402
from costagreements.builders.visa_482 import validate_visa_482_cost_agreement  # noqa: E402
from costagreements.builders.visa_870 import Visa870CostAgreementData  # noqa: E402
from costagreements.builders.visa_870 import build_visa_870_cost_agreement  # noqa: E402
from costagreements.builders.visa_870 import validate_visa_870_cost_agreement  # noqa: E402
from costagreements.schema import GeneralCostAgreementData  # noqa: E402
from costagreements.validate import validate_general_cost_agreement  # noqa: E402

# A 1x1 red PNG, valid enough to embed as a test signature image.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
TINY_PNG_DATA_URI = "data:image/png;base64," + _TINY_PNG_B64

MINIMAL_PAYLOAD = {
    "date": "10/08/2026",
    "our_ref": "WZL-1234",
    "client_name": "John Citizen",
    "client_address": "12 Sample St, Sampleville NSW 2000",
    "capacity": "rma",
    "professional_fee": "3500",
    "estimated_weeks": "8",
    "service_fee": "150",
    "visa_lodgment_fee": "1000",
    "rep_name": "Chi Nguyen",
}


def _chrome_snippets():
    return ["WINZOY LEGAL", "Solicitors", "ACN 675", "Doc ID:", "Issued:", "COST AGREEMENT",
            "Winzoy Legal  •", "M: 0424", "Page ", "Verify:"]


def _assert_no_body_overflow(pdf_bytes: bytes) -> dict[int, str]:
    """Fails if any non-chrome text sits below the content frame's bottom
    boundary on any page. Returns {page_index: full_text} for callers that
    want to inspect specific pages afterward."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    frame_bottom_topdown = L.PAGE_H - L.FRAME_Y
    chrome = _chrome_snippets()
    page_text: dict[int, str] = {}
    for i, page in enumerate(doc):
        page_text[i] = page.get_text()
        d = page.get_text("dict")
        for block in d["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"]
                    if not text.strip() or any(s in text for s in chrome):
                        continue
                    y1 = span["bbox"][3]
                    assert y1 <= frame_bottom_topdown + 0.5, (
                        f"page {i + 1}: body text {text!r} extends to y={y1:.1f}, "
                        f"past the frame boundary at {frame_bottom_topdown:.1f} "
                        "-- content is overlapping the footer/signature band"
                    )
    doc.close()
    return page_text


# ---------------------------------------------------------------- validation
def test_minimal_payload_is_valid():
    assert validate_general_cost_agreement(MINIMAL_PAYLOAD) == []


def test_missing_required_fields_reported():
    errs = validate_general_cost_agreement({})
    fields = {e.field for e in errs}
    assert "client_name" in fields
    assert "capacity" in fields
    assert all(e.code == "required" for e in errs)


def test_invalid_capacity_rejected():
    payload = dict(MINIMAL_PAYLOAD, capacity="attorney")
    errs = validate_general_cost_agreement(payload)
    assert any(e.field == "capacity" and e.code == "value" for e in errs)


def test_non_data_uri_signature_rejected():
    payload = dict(MINIMAL_PAYLOAD, client_signature_data="https://example.com/sig.png")
    errs = validate_general_cost_agreement(payload)
    assert any(e.field == "client_signature_data" and e.code == "format" for e in errs)


# ---------------------------------------------------------------- builder
def test_minimal_payload_builds_a_valid_pdf():
    data = GeneralCostAgreementData.from_payload(MINIMAL_PAYLOAD)
    pdf_bytes = build_general_cost_agreement(data)
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    assert doc.page_count > 1
    doc.close()


def test_build_is_deterministic_page_count():
    data = GeneralCostAgreementData.from_payload(MINIMAL_PAYLOAD)
    pdf1 = build_general_cost_agreement(data)
    pdf2 = build_general_cost_agreement(data)
    doc1 = pymupdf.open(stream=pdf1, filetype="pdf")
    doc2 = pymupdf.open(stream=pdf2, filetype="pdf")
    assert doc1.page_count == doc2.page_count
    doc1.close()
    doc2.close()


def test_full_payload_with_signatures_and_annexure_d():
    payload = dict(
        MINIMAL_PAYLOAD,
        marn="1234567",
        lpn="",
        service_bullets=["Prepare and lodge visa application", "Liaise with the Department"],
        staff_note="Client requested expedited processing.\nFollow up in 2 weeks.",
        client_signature_data=TINY_PNG_DATA_URI,
        rep_signature_data=TINY_PNG_DATA_URI,
        include_annexure_d=True,
        translation_banner_text="This document has also been explained to you in Vietnamese.",
    )
    assert validate_general_cost_agreement(payload) == []
    data = GeneralCostAgreementData.from_payload(payload)
    pdf_bytes = build_general_cost_agreement(data)
    page_text = _assert_no_body_overflow(pdf_bytes)
    full_text = "\n".join(page_text.values())
    assert "ANNEXURE D" in full_text
    assert "ANNEXURE A" in full_text
    assert "SIGNATURES" in full_text


def test_signature_block_never_overlaps_content_and_stays_together():
    """The specific bug the user reported: content colliding with the
    client-initials/signature/date + admin-staff-signature block. Forces
    heavy overflow (many bullets, a long staff note) -- the scenario that
    repeatedly broke winzoylegal_new's hand-computed layout -- and asserts
    the signature block still lands intact on a single page with no
    content overflowing the frame anywhere in the document."""
    long_bullets = [
        f"Service line item number {i} with a moderately long description "
        "to force wrapping and extra page space consumption."
        for i in range(1, 25)
    ]
    long_note = "\n".join(
        f"Staff note line {i}: some additional detail about this matter." for i in range(1, 20)
    )
    payload = dict(MINIMAL_PAYLOAD, service_bullets=long_bullets, staff_note=long_note)
    data = GeneralCostAgreementData.from_payload(payload)
    pdf_bytes = build_general_cost_agreement(data)
    page_text = _assert_no_body_overflow(pdf_bytes)

    sig_pages = [i for i, txt in page_text.items() if "SIGNATURES" in txt]
    assert len(sig_pages) == 1, "signature banner should appear on exactly one page"
    sig_text = page_text[sig_pages[0]]
    for marker in ("Client Signature", "FOR WINZOY LEGAL", "Acting Capacity", "Name:", "Date:"):
        assert marker in sig_text, f"{marker!r} split away from the rest of the signature block"


def test_no_body_overflow_on_default_payload():
    data = GeneralCostAgreementData.from_payload(MINIMAL_PAYLOAD)
    pdf_bytes = build_general_cost_agreement(data)
    _assert_no_body_overflow(pdf_bytes)


# ---------------------------------------------------------------- SIGMETA
def test_sigmeta_boxes_match_actual_rendered_signature_positions():
    """The whole point of SIGMETA is that winzoylegal_new's existing
    signing infrastructure can find the real signature box on the page
    from these coordinates alone. Build with real signature images and
    verify each recorded box's coordinates actually contain the image
    ReportLab drew -- not just that some JSON got embedded."""
    from costagreements.sigmeta import parse_sigmeta

    payload = dict(
        MINIMAL_PAYLOAD,
        client_signature_data=TINY_PNG_DATA_URI,
        rep_signature_data=TINY_PNG_DATA_URI,
        rep_signature_url="https://storage.example.com/reps/chi.png",
        lpn="", marn="1234567",
    )
    data = GeneralCostAgreementData.from_payload(payload)
    pdf_bytes = build_general_cost_agreement(data)

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    meta = parse_sigmeta(doc.metadata.get("subject"))
    assert meta is not None
    assert meta["repUrl"] == payload["rep_signature_url"]
    assert meta["marn"] == "1234567"
    assert meta["capacity"] == "rma"

    def image_centers_bottom_up(page):
        h = page.rect.height
        centers = []
        for img in page.get_image_info():
            x0, y0, x1, y1 = img["bbox"]
            centers.append(((x0 + x1) / 2, h - (y0 + y1) / 2))
        return centers

    def contains(box_x, box_y, box_w, box_h, point, tol=2):
        px, py = point
        return (box_x - tol <= px <= box_x + box_w + tol) and (box_y - tol <= py <= box_y + box_h + tol)

    sig_page_centers = image_centers_bottom_up(doc[meta["p"]])
    assert any(contains(meta["cX"], meta["cY"], meta["w"], meta["h"], c) for c in sig_page_centers), (
        "client signature box coordinates don't contain the actual rendered image"
    )
    assert any(contains(meta["rX"], meta["rY"], meta["w"], meta["h"], c) for c in sig_page_centers), (
        "rep signature box coordinates don't contain the actual rendered image"
    )

    annex_c = meta["annexC"]
    annex_c_centers = image_centers_bottom_up(doc[annex_c["p"]])
    assert any(contains(annex_c["x"], annex_c["y"], annex_c["w"], annex_c["h"], c) for c in annex_c_centers), (
        "Annexure C signature box coordinates don't contain the actual rendered image"
    )
    doc.close()


def test_sigmeta_absent_signature_still_records_boxes():
    """Boxes must be recorded even when no signature image was supplied
    yet -- that's what lets winzoylegal_new stamp one in later."""
    from costagreements.sigmeta import parse_sigmeta

    data = GeneralCostAgreementData.from_payload(MINIMAL_PAYLOAD)
    pdf_bytes = build_general_cost_agreement(data)
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    meta = parse_sigmeta(doc.metadata.get("subject"))
    assert meta is not None
    for key in ("p", "cX", "cY", "rX", "rY", "w", "h"):
        assert key in meta
    assert "annexC" in meta and {"p", "x", "y", "w", "h", "dateX", "dateY"} <= meta["annexC"].keys()
    doc.close()


# ---------------------------------------------------------------- client_agreement / art / jrp / skills_assessment_only
CLIENT_AGREEMENT_MINIMAL_PAYLOAD = dict(
    MINIMAL_PAYLOAD,
    visa_type="Skilled Independent (subclass 189)",
)

ART_MINIMAL_PAYLOAD = dict(
    MINIMAL_PAYLOAD,
    lodgment_fee="1000",
)

JRP_MINIMAL_PAYLOAD = {
    "date": "10/08/2026",
    "our_ref": "WZL-1234",
    "client_name": "John Citizen",
    "client_address": "12 Sample St, Sampleville NSW 2000",
    "capacity": "rma",
    "scope_text": "Request for Ministerial Intervention under s351 of the Migration Act.",
    "professional_fee": "3500",
    "estimated_weeks": "8",
    "processing_days": "90",
    "rep_name": "Chi Nguyen",
}

SKILLS_ASSESSMENT_ONLY_MINIMAL_PAYLOAD = {
    "date": "10/08/2026",
    "our_ref": "WZL-1234",
    "client_name": "John Citizen",
    "client_address": "12 Sample St, Sampleville NSW 2000",
    "capacity": "rma",
    "occupation": "Software Engineer",
    "assessing_authority": "ACS",
    "professional_fee": "1500",
    "payment_stage1_label": "On engagement",
    "payment_stage1_amount": "750",
    "payment_stage2_label": "On lodgment",
    "payment_stage2_amount": "750",
    "rep_name": "Chi Nguyen",
}

PARTNER_VISA_MINIMAL_PAYLOAD = {
    "date": "10/08/2026",
    "our_ref": "WZL-1234",
    "client_name": "John Citizen",
    "client_address": "12 Sample St, Sampleville NSW 2000",
    "capacity": "rma",
    "visa_subclass": "820",
    "professional_fee": "5500",
    "disbursement_lodgement_fee": "9497",
    "processing_time": "15 - 23 months",
    "rep_name": "Chi Nguyen",
}

SKILLED_VISA_MINIMAL_PAYLOAD = {
    "date": "10/08/2026",
    "our_ref": "WZL-1234",
    "client_name": "John Citizen",
    "client_address": "12 Sample St, Sampleville NSW 2000",
    "capacity": "rma",
    "visa_subclass": "491",
    "nomination_state": "NSW",
    "professional_fee": "5500",
    "state_nomination_fee": "370",
    "visa_application_fee": "4770",
    "nomination_processing_time": "6 - 8 weeks",
    "visa_processing_time": "3 - 15 months",
    "rep_name": "Chi Nguyen",
}

SKILLS_ASSESSMENT_MINIMAL_PAYLOAD = {
    "date": "10/08/2026",
    "our_ref": "WZL-1234",
    "client_name": "John Citizen",
    "client_address": "12 Sample St, Sampleville NSW 2000",
    "capacity": "rma",
    "occupation": "Software Engineer",
    "assessing_authority": "ACS",
    "visa_subclass": "491",
    "nomination_state": "NSW",
    "professional_fee": "6600",
    "state_nomination_fee": "370",
    "visa_application_fee": "4770",
    "nomination_processing_time": "6 - 8 weeks",
    "visa_processing_time": "3 - 15 months",
    "rep_name": "Chi Nguyen",
}

SKILLS_ASSESSMENT_186_MINIMAL_PAYLOAD = {
    "date": "10/08/2026",
    "our_ref": "WZL-1234",
    "client_name": "John Citizen",
    "client_address": "12 Sample St, Sampleville NSW 2000",
    "capacity": "rma",
    "occupation": "Cabinet Maker",
    "assessing_authority": "ATTC",
    "professional_fee": "5500",
    "nomination_fee": "540",
    "visa_application_fee": "4910",
    "sponsorship_processing_time": "14 - 60 days",
    "nomination_processing_time": "4 - 120 days",
    "visa_processing_time": "40 - 150 days",
    "rep_name": "Chi Nguyen",
}

BFA_MINIMAL_PAYLOAD = {
    "date": "10/08/2026",
    "our_ref": "WZL-1234",
    "client_name": "John Citizen",
    "client_address": "12 Sample St, Sampleville NSW 2000",
    "capacity": "solicitor",
    "professional_fee": "5000",
    "estimated_days": "05",
    "service_fee": "150",
    "rep_name": "Chi Nguyen",
}

DIVORCE_MINIMAL_PAYLOAD = {
    "date": "10/08/2026",
    "our_ref": "WZL-5678",
    "client_name": "John Citizen",
    "client_address": "12 Sample St, Sampleville NSW 2000",
    "capacity": "solicitor",
    "professional_fee": "1500",
    "estimated_days": "02",
    "service_fee": "150",
    "rep_name": "Chi Nguyen",
}

VISA_482_MINIMAL_PAYLOAD = {
    "date": "10/08/2026",
    "our_ref": "WZL-1234",
    "client_name": "John Citizen",
    "client_address": "12 Sample St, Sampleville NSW 2000",
    "capacity": "rma",
    "stream": "Core Skills Stream",
    "professional_fee": "5500",
    "sponsorship_fee": "420",
    "nomination_fee": "330",
    "visa_application_fee": "2770",
    "disbursements_sub_total": "3520",
    "sponsorship_processing_time": "14 - 60 days",
    "nomination_processing_time": "4 - 120 days",
    "visa_processing_time": "40 - 150 days",
    "rep_name": "Chi Nguyen",
}

VISA_870_MINIMAL_PAYLOAD = {
    "date": "10/08/2026",
    "our_ref": "WZL-9999",
    "client_name": "Jane Citizen",
    "client_address": "12 Sample St, Sampleville NSW 2000",
    "capacity": "rma",
    "professional_fee": "8800",
    "total_cost": "16000",
    "rep_name": "Chi Nguyen",
}


@pytest.mark.parametrize(
    "validate_fn, payload",
    [
        (validate_client_agreement, CLIENT_AGREEMENT_MINIMAL_PAYLOAD),
        (validate_art_cost_agreement, ART_MINIMAL_PAYLOAD),
        (validate_jrp_cost_agreement, JRP_MINIMAL_PAYLOAD),
        (validate_skills_assessment_only_cost_agreement, SKILLS_ASSESSMENT_ONLY_MINIMAL_PAYLOAD),
        (validate_partner_visa_cost_agreement, PARTNER_VISA_MINIMAL_PAYLOAD),
        (validate_skilled_visa_cost_agreement, SKILLED_VISA_MINIMAL_PAYLOAD),
        (validate_skills_assessment_cost_agreement, SKILLS_ASSESSMENT_MINIMAL_PAYLOAD),
        (validate_skills_assessment_186_cost_agreement, SKILLS_ASSESSMENT_186_MINIMAL_PAYLOAD),
        (validate_bfa_cost_agreement, BFA_MINIMAL_PAYLOAD),
        (validate_divorce_cost_agreement, DIVORCE_MINIMAL_PAYLOAD),
        (validate_visa_482_cost_agreement, VISA_482_MINIMAL_PAYLOAD),
        (validate_visa_870_cost_agreement, VISA_870_MINIMAL_PAYLOAD),
    ],
)
def test_other_types_minimal_payload_is_valid(validate_fn, payload):
    assert validate_fn(payload) == []


@pytest.mark.parametrize(
    "validate_fn",
    [
        validate_client_agreement,
        validate_art_cost_agreement,
        validate_jrp_cost_agreement,
        validate_skills_assessment_only_cost_agreement,
        validate_partner_visa_cost_agreement,
        validate_skilled_visa_cost_agreement,
        validate_skills_assessment_cost_agreement,
        validate_skills_assessment_186_cost_agreement,
        validate_bfa_cost_agreement,
        validate_divorce_cost_agreement,
        validate_visa_482_cost_agreement,
        validate_visa_870_cost_agreement,
    ],
)
def test_other_types_missing_required_fields_reported(validate_fn):
    errs = validate_fn({})
    assert errs
    assert all(e.code == "required" for e in errs)


@pytest.mark.parametrize(
    "schema_cls, build_fn, payload",
    [
        (ClientAgreementData, build_client_agreement, CLIENT_AGREEMENT_MINIMAL_PAYLOAD),
        (ArtCostAgreementData, build_art_cost_agreement, ART_MINIMAL_PAYLOAD),
        (JrpCostAgreementData, build_jrp_cost_agreement, JRP_MINIMAL_PAYLOAD),
        (SkillsAssessmentOnlyCostAgreementData, build_skills_assessment_only_cost_agreement, SKILLS_ASSESSMENT_ONLY_MINIMAL_PAYLOAD),
        (PartnerVisaCostAgreementData, build_partner_visa_cost_agreement, PARTNER_VISA_MINIMAL_PAYLOAD),
        (SkilledVisaCostAgreementData, build_skilled_visa_cost_agreement, SKILLED_VISA_MINIMAL_PAYLOAD),
        (SkillsAssessmentCostAgreementData, build_skills_assessment_cost_agreement, SKILLS_ASSESSMENT_MINIMAL_PAYLOAD),
        (SkillsAssessment186CostAgreementData, build_skills_assessment_186_cost_agreement, SKILLS_ASSESSMENT_186_MINIMAL_PAYLOAD),
        (BfaCostAgreementData, build_bfa_cost_agreement, BFA_MINIMAL_PAYLOAD),
        (DivorceCostAgreementData, build_divorce_cost_agreement, DIVORCE_MINIMAL_PAYLOAD),
        (Visa482CostAgreementData, build_visa_482_cost_agreement, VISA_482_MINIMAL_PAYLOAD),
        (Visa870CostAgreementData, build_visa_870_cost_agreement, VISA_870_MINIMAL_PAYLOAD),
    ],
)
def test_other_types_minimal_payload_builds_a_valid_pdf(schema_cls, build_fn, payload):
    data = schema_cls.from_payload(payload)
    pdf_bytes = build_fn(data)
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    assert doc.page_count >= 1
    doc.close()
    _assert_no_body_overflow(pdf_bytes)


# ---------------------------------------------------------------- HTTP route
# NOTE: ``app`` is module-scoped (importing/reloading app.py is not free)
# but a function-scoped ``monkeypatch``-based env fixture would run *after*
# pytest resolves module-scoped fixtures, letting app.py import with
# UPLOADS_DIR unset first -- silently pointing CACHE at the real repo
# ``uploads/cache/`` dir instead of a temp one. Set the env var directly,
# synchronously, inside this same module-scoped fixture so there's no
# ordering hazard.
@pytest.fixture(scope="module")
def app(tmp_path_factory):
    import importlib
    import os

    os.environ["UPLOADS_DIR"] = str(tmp_path_factory.mktemp("cost_agreements_cache"))
    try:
        if "app" in sys.modules:
            importlib.reload(sys.modules["app"])
        else:
            import app  # type: ignore  # noqa: F401
        yield sys.modules["app"]
    finally:
        del os.environ["UPLOADS_DIR"]


@pytest.fixture()
def client(app):
    return app.app.test_client()


def test_fill_route_returns_pdf(client):
    res = client.post("/cost-agreements/general/fill", json=MINIMAL_PAYLOAD)
    assert res.status_code == 200
    assert res.headers["X-Cache"] == "miss"
    doc = pymupdf.open(stream=res.data, filetype="pdf")
    assert doc.page_count > 1
    doc.close()


def test_fill_route_cache_hit_on_repeat(client):
    # Distinct our_ref so this test's cache entry can't already exist from
    # another test sharing the module-scoped ``app``/CACHE fixture.
    payload = dict(MINIMAL_PAYLOAD, our_ref="WZL-CACHE-TEST-ONLY")
    first = client.post("/cost-agreements/general/fill", json=payload)
    second = client.post("/cost-agreements/general/fill", json=payload)
    assert first.headers["X-Cache"] == "miss"
    assert second.headers["X-Cache"] == "hit"
    assert first.headers["X-Cache-Key"] == second.headers["X-Cache-Key"]


def test_fill_route_validation_error(client):
    res = client.post("/cost-agreements/general/fill", json={})
    assert res.status_code == 400
    body = res.get_json()
    assert body["errors"]
    assert all({"field", "code", "message"} <= e.keys() for e in body["errors"])


def test_fill_route_unknown_agreement_type(client):
    res = client.post("/cost-agreements/nope/fill", json={})
    assert res.status_code == 404


def test_health_lists_cost_agreement_types(client):
    res = client.get("/health")
    assert res.status_code == 200
    types = res.get_json()["cost_agreement_types"]
    for t in (
        "general", "client_agreement", "art", "jrp", "skills_assessment_only",
        "partner_visa", "skilled_visa", "skills_assessment", "skills_assessment_186",
        "bfa", "divorce", "visa_482", "visa_870",
    ):
        assert t in types


@pytest.mark.parametrize(
    "agreement_type, payload",
    [
        ("client_agreement", CLIENT_AGREEMENT_MINIMAL_PAYLOAD),
        ("art", ART_MINIMAL_PAYLOAD),
        ("jrp", JRP_MINIMAL_PAYLOAD),
        ("skills_assessment_only", SKILLS_ASSESSMENT_ONLY_MINIMAL_PAYLOAD),
        ("partner_visa", PARTNER_VISA_MINIMAL_PAYLOAD),
        ("skilled_visa", SKILLED_VISA_MINIMAL_PAYLOAD),
        ("skills_assessment", SKILLS_ASSESSMENT_MINIMAL_PAYLOAD),
        ("skills_assessment_186", SKILLS_ASSESSMENT_186_MINIMAL_PAYLOAD),
        ("bfa", BFA_MINIMAL_PAYLOAD),
        ("divorce", DIVORCE_MINIMAL_PAYLOAD),
        ("visa_482", VISA_482_MINIMAL_PAYLOAD),
        ("visa_870", VISA_870_MINIMAL_PAYLOAD),
    ],
)
def test_other_types_fill_route_returns_pdf(client, agreement_type, payload):
    res = client.post(f"/cost-agreements/{agreement_type}/fill", json=payload)
    assert res.status_code == 200
    assert res.headers["X-Cache"] == "miss"
    doc = pymupdf.open(stream=res.data, filetype="pdf")
    assert doc.page_count >= 1
    doc.close()
