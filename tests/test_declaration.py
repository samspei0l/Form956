"""Tests for the client Declaration builder.

Kept separate from tests/test_cost_agreements.py for the same reason the
finance documents are: the declaration is not a cost agreement and differs
in exactly the things that file asserts. It carries no SIGMETA and reserves
no post-signing stamp band (its frame runs to ``layout.DOC_FRAME_Y``),
because it is rebuilt with the signature baked in rather than stamped
afterwards -- a detail answer is free text of unknown length and will not
fit a box measured before the text existed.

The regressions this file exists for:
  * a "yes" recorded with no details -- a declaration that says something
    happened but not what is worse than no declaration at all, so the rule
    is asserted here as well as in the browser and in winzoylegal_new's
    CHECK constraint;
  * question and clause wording arriving from the caller -- if the client's
    browser could supply the question, the signed document proves nothing;
  * the read copy and the signed copy drifting apart -- they are one
    document in two states and must share a Doc ID.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from costagreements import layout as L  # noqa: E402
from costagreements.builders.declaration import (  # noqa: E402
    DECLARATION_CLAUSES,
    DECLARATION_QUESTIONS,
    QUESTION_KEYS,
    DeclarationData,
    apply_declaration_normalisations,
    build_declaration,
    validate_declaration,
)
from costagreements.sigmeta import parse_sigmeta  # noqa: E402

MINIMAL_PAYLOAD = {
    "date": "16/09/2026",
    "our_ref": "WINZOY26-1234",
    "client_name": "Jane Example",
}

FULL_PAYLOAD = {
    **MINIMAL_PAYLOAD,
    "client_address": "1 Example St, Sydney NSW 2000",
    "client_dob": "02/04/1990",
    "visa_type": "Subclass 482",
    "rep_name": "Case Officer",
    "capacity": "rma",
    "marn": "1234567",
    "signer_name": "Jane Example",
    "signed_at": "16/09/2026",
    "answers": {
        "visa_compliance": {"answer": "no"},
        "visa_refusal": {"answer": "yes", "detail": "Student visa refused in 2019.\nGranted on appeal in 2020."},
        "health": {"answer": "no"},
        "character": {"answer": "no"},
    },
}


# --------------------------------------------------------------------- helpers
def _build(payload: dict) -> bytes:
    return build_declaration(DeclarationData.from_payload(apply_declaration_normalisations(payload)))


def _page_texts(pdf_bytes: bytes) -> list[str]:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    texts = [page.get_text() for page in doc]
    doc.close()
    return texts


def _all_text(pdf_bytes: bytes) -> str:
    return "\n".join(_page_texts(pdf_bytes))


def _doc_id(pdf_bytes: bytes) -> str:
    text = _all_text(pdf_bytes)
    marker = "Doc ID: "
    i = text.index(marker) + len(marker)
    return text[i:].split("\n", 1)[0].strip()


# -------------------------------------------------------------------- validate
def test_minimal_payload_is_valid():
    assert validate_declaration(MINIMAL_PAYLOAD) == []


def test_full_payload_is_valid():
    assert validate_declaration(FULL_PAYLOAD) == []


def test_an_unanswered_payload_is_valid():
    """The read copy the applicant is sent before filling anything in."""
    assert validate_declaration({**MINIMAL_PAYLOAD, "answers": {}}) == []


def test_missing_required_fields_reported():
    errs = validate_declaration({})
    assert {e.field for e in errs} == {"date", "our_ref", "client_name"}
    assert all(e.code == "required" for e in errs)


@pytest.mark.parametrize("key", QUESTION_KEYS)
def test_yes_without_details_is_rejected(key):
    errs = validate_declaration({**MINIMAL_PAYLOAD, "answers": {key: {"answer": "yes", "detail": "   "}}})
    assert any(e.field == f"answers.{key}.detail" and e.code == "required" for e in errs), errs


@pytest.mark.parametrize("key", QUESTION_KEYS)
def test_no_without_details_is_fine(key):
    assert validate_declaration({**MINIMAL_PAYLOAD, "answers": {key: {"answer": "no"}}}) == []


def test_an_answer_other_than_yes_or_no_is_rejected():
    errs = validate_declaration({**MINIMAL_PAYLOAD, "answers": {"health": {"answer": "maybe"}}})
    assert any(e.field == "answers.health.answer" and e.code == "value" for e in errs), errs


def test_an_unknown_question_is_rejected():
    """The caller does not get to invent questions."""
    errs = validate_declaration({**MINIMAL_PAYLOAD, "answers": {"favourite_colour": {"answer": "no"}}})
    assert any(e.field == "answers.favourite_colour" and e.code == "unknown" for e in errs), errs


def test_answers_must_be_an_object():
    errs = validate_declaration({**MINIMAL_PAYLOAD, "answers": ["yes", "no"]})
    assert any(e.field == "answers" and e.code == "format" for e in errs), errs


def test_an_impossible_date_is_rejected():
    errs = validate_declaration({**MINIMAL_PAYLOAD, "date": "31/02/2026"})
    assert any(e.field == "date" and e.code == "value" for e in errs), errs


def test_a_signature_that_is_not_an_image_data_uri_is_rejected():
    errs = validate_declaration({**MINIMAL_PAYLOAD, "client_signature_data": "https://example.com/sig.png"})
    assert any(e.field == "client_signature_data" and e.code == "format" for e in errs), errs


def test_iso_dates_normalise_to_au_format():
    out = apply_declaration_normalisations({**MINIMAL_PAYLOAD, "date": "2026-09-16", "client_dob": "1990-04-02"})
    assert out["date"] == "16/09/2026"
    assert out["client_dob"] == "02/04/1990"


# ----------------------------------------------------------------------- build
def test_every_question_and_clause_appears_verbatim():
    """The wording is the document. If a builder edit drops or rewords a
    clause, that is a legal-content change and this should fail."""
    text = " ".join(_all_text(_build(FULL_PAYLOAD)).split())
    for _key, heading, question in DECLARATION_QUESTIONS:
        assert heading in text, f"heading {heading!r} missing"
        assert " ".join(question.split()) in text, f"question {heading!r} missing"
    for clause in DECLARATION_CLAUSES:
        assert " ".join(clause.split()) in text, f"clause missing: {clause[:60]!r}"


def test_the_applicant_and_matter_panels_are_printed():
    text = _all_text(_build(FULL_PAYLOAD))
    for expected in ("Declaration", "APPLICANT", "MATTER", "Jane Example",
                     "WINZOY26-1234", "02/04/1990", "Subclass 482",
                     "1 Example St, Sydney NSW 2000", "16/09/2026"):
        assert expected in text, f"{expected!r} missing"


def test_a_yes_prints_the_applicants_own_words():
    text = _all_text(_build(FULL_PAYLOAD))
    assert "DETAILS PROVIDED" in text
    assert "Student visa refused in 2019." in text
    # The newline in the detail must survive as a line break, not as markup.
    assert "Granted on appeal in 2020." in text


def test_detail_text_is_escaped_not_interpreted_as_markup():
    """The one genuinely free-form client-supplied string. A stray tag must
    print, not render -- and must not swallow the rest of the paragraph."""
    payload = {
        **MINIMAL_PAYLOAD,
        "answers": {"character": {"answer": "yes", "detail": "<b>R v Example</b> & others <br/> 2019"}},
    }
    text = _all_text(_build(payload))
    assert "<b>R v Example</b>" in text
    assert "& others" in text
    assert "<br/>" in text


def test_the_read_copy_shows_no_answers():
    text = _all_text(_build(MINIMAL_PAYLOAD))
    assert "Not yet answered." in text
    assert "DETAILS PROVIDED" not in text


def test_the_badge_reads_declaration_on_every_page():
    pages = _page_texts(_build(FULL_PAYLOAD))
    assert len(pages) >= 2, "expected the declaration to run to a second page"
    for i, page in enumerate(pages):
        assert "DECLARATION" in page, f"page {i + 1} is missing the badge"


def test_the_header_issued_date_is_the_declarations_own_date():
    """Not a render timestamp: rebuilding the signed copy must not restamp
    the header with today's date. Same rule as the finance documents."""
    for page in _page_texts(_build(FULL_PAYLOAD)):
        assert "Issued: 16/09/2026" in " ".join(page.split())


def test_the_doc_id_is_stable_across_rebuilds():
    assert _doc_id(_build(FULL_PAYLOAD)) == _doc_id(_build(FULL_PAYLOAD))


def test_the_read_copy_and_the_signed_copy_share_a_doc_id():
    """One document in two states."""
    assert _doc_id(_build(MINIMAL_PAYLOAD)) == _doc_id(_build(FULL_PAYLOAD))


def test_a_different_lead_gets_a_different_doc_id():
    other = {**FULL_PAYLOAD, "our_ref": "WINZOY26-9999"}
    assert _doc_id(_build(FULL_PAYLOAD)) != _doc_id(_build(other))


def test_it_carries_no_sigmeta():
    """Nothing stamps this document, so there is no box to advertise. If
    SIGMETA ever appears here, on-document-signed would try to stamp a
    signature into a rebuilt PDF and double up."""
    doc = pymupdf.open(stream=_build(FULL_PAYLOAD), filetype="pdf")
    subject = doc.metadata.get("subject")
    doc.close()
    assert parse_sigmeta(subject) is None


def test_no_body_text_falls_below_the_frame():
    """The finance frame runs to the footer; content must stay inside it."""
    pdf_bytes = _build(FULL_PAYLOAD)
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    frame_bottom_topdown = L.PAGE_H - L.DOC_FRAME_Y
    chrome = (L.FIRM_NAME, L.FIRM_ACN, "Doc ID:", "Issued:", "Verify:", "Page ",
              "WINZOY LEGAL", "Solicitors", "PO Box", "M: 0424", "DECLARATION")
    for i, page in enumerate(doc):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"]
                    if not text.strip() or any(s in text for s in chrome):
                        continue
                    assert span["bbox"][3] <= frame_bottom_topdown + 0.5, (
                        f"page {i + 1}: body text {text!r} extends past the frame"
                    )
    doc.close()


def test_a_long_detail_answer_paginates_instead_of_overflowing():
    """The reason this document is rebuilt rather than stamped: there is no
    bound on how much an applicant writes."""
    long_detail = " ".join(f"Incident {n} occurred and was resolved." for n in range(1, 120))
    payload = {**FULL_PAYLOAD, "answers": {
        key: {"answer": "yes", "detail": long_detail} for key in QUESTION_KEYS
    }}
    pdf_bytes = _build(payload)
    assert len(_page_texts(pdf_bytes)) > 2
    assert "Incident 119 occurred" in _all_text(pdf_bytes)


# ---------------------------------------------------------------- HTTP route
# See tests/test_cost_agreements.py for why UPLOADS_DIR is set directly
# inside this module-scoped fixture rather than via monkeypatch.
@pytest.fixture(scope="module")
def app(tmp_path_factory):
    import importlib
    import os

    os.environ["UPLOADS_DIR"] = str(tmp_path_factory.mktemp("declaration_cache"))
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


def test_route_returns_a_pdf(client):
    res = client.post("/cost-agreements/declaration/fill", json=FULL_PAYLOAD)
    assert res.status_code == 200, res.get_data(as_text=True)[:400]
    assert res.data.startswith(b"%PDF")


def test_route_rejects_an_invalid_payload_with_field_errors(client):
    res = client.post("/cost-agreements/declaration/fill", json={})
    assert res.status_code == 400
    body = res.get_json()
    assert {e["field"] for e in body["errors"]} == {"date", "our_ref", "client_name"}


def test_route_rejects_a_yes_with_no_detail(client):
    res = client.post("/cost-agreements/declaration/fill", json={
        **MINIMAL_PAYLOAD, "answers": {"health": {"answer": "yes", "detail": ""}},
    })
    assert res.status_code == 400
    assert any(e["field"] == "answers.health.detail" for e in res.get_json()["errors"])


def test_declaration_is_registered_everywhere(app):
    """Missing one of the four dicts is the easy mistake -- the type 404s or
    silently skips validation."""
    assert "declaration" in app.COST_AGREEMENT_BUILDERS
    assert "declaration" in app.COST_AGREEMENT_SCHEMAS
    assert "declaration" in app.COST_AGREEMENT_VALIDATORS
    assert "declaration" in app.COST_AGREEMENT_NORMALISERS


def test_health_lists_the_declaration(client):
    res = client.get("/health")
    assert "declaration" in res.get_json()["cost_agreement_types"]
