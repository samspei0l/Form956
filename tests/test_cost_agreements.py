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
from costagreements.builders.general import build_general_cost_agreement  # noqa: E402
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
    assert "general" in res.get_json()["cost_agreement_types"]
