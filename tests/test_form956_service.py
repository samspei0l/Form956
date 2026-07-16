"""End-to-end tests for the Form 956 service.

Strategy:
  - Real PyMuPDF + the bundled ``956.pdf`` for the engine round-trip
    (no mocks for the parts that actually touch the PDF).
  - A real Flask test client for the HTTP routes.
  - A real on-disk cache directory scoped to ``tmp_path`` so the
    production cache is never touched.
  - For rate-limiter / CORS (optional deps) we just check that the
    server still starts when they're missing.
"""
from __future__ import annotations

import os
import sys
import shutil
import tempfile
from pathlib import Path

import pytest

# Make the project root importable so ``import app`` and
# ``from pdfform import ...`` work regardless of where pytest is run.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Force the cache into a per-test tempdir before app.py is imported.
@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    yield cache_dir


@pytest.fixture(scope="module")
def app():
    """Import app after env is patched and cache moved."""
    # Reload to make sure CACHE points at the temp dir if the module
    # imported it at collection time.
    import importlib
    if "app" in sys.modules:
        importlib.reload(sys.modules["app"])
    else:
        import app  # type: ignore
    return app


@pytest.fixture()
def client(app):
    return app.app.test_client()


@pytest.fixture()
def form956_engine(app):
    return app.ENGINES["form956"]


# ---------------------------------------------------------------- baseline
def test_health_returns_ok(client):
    res = client.get("/health")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert "form956" in data["forms"]
    assert "cache_size" in data


def test_form956_schema_loads(form956_engine):
    schema = form956_engine.schema_dict()
    assert "fields" in schema
    assert len(schema["fields"]) >= 50  # form 956 has 51 widgets


# ---------------------------------------------------------------- adapter
def test_adapt_legacy_react_payload(form956_engine):
    from pdfform.adapt_form956 import adapt_form956_payload
    from pdfform.validate import validate_form956

    legacy = {
        "agent_family_name": "Smith",
        "agent_given_names": "Jane",
        "agent_marn": "1234567",
        "agent_email": "j@e.com",
        "client_role": "visa",
        "application_type": "Skilled visa",
        "_client_family": "Doe",
        "_client_given": "John",
        "client_email": "john@example.com",
        "client_resadd_pc": "NSW 2000",
    }
    adapted = adapt_form956_payload(legacy)
    assert "client_email" not in adapted
    assert "_client_family" not in adapted
    assert adapted["people"] == [{"family": "Doe", "given": "John"}]
    assert adapted["client_resadd_pc"] == "2000"

    schema_apps = {f["app"] for f in form956_engine.schema_dict()["fields"]}
    errs = validate_form956(adapted, schema_apps)
    assert not any(e.code == "unknown" for e in errs)


def test_adapt_swaps_is_new_application_to_raw_pdf_state(form956_engine):
    """The `mg.app` widget pair's on-state names are swapped relative to
    their printed labels in the source PDF (New appointment's real on-state
    is "No"; Appointment has ended's is "Yes"). A prior bug passed the
    app-facing value straight through, ticking the wrong checkbox whenever
    a value was set."""
    from pdfform.adapt_form956 import adapt_form956_payload

    assert adapt_form956_payload({"is_new_application": "Yes"})["is_new_application"] == "No"
    assert adapt_form956_payload({"is_new_application": "No"})["is_new_application"] == "Yes"


def test_adapt_inserts_client_1_ahead_of_dependants(form956_engine):
    """Client 1 must land at people[0] without dropping dependants already
    in the list — a prior bug overwrote people[0] with Client 1's name,
    silently discarding the first dependant whenever one was present."""
    from pdfform.adapt_form956 import adapt_form956_payload

    legacy = {
        "_client_family": "Doe",
        "_client_given": "John",
        "people": [{"family": "Doe", "given": "Jane Jr"}],
    }
    adapted = adapt_form956_payload(legacy)
    assert adapted["people"] == [
        {"family": "Doe", "given": "John"},
        {"family": "Doe", "given": "Jane Jr"},
    ]


def test_fill_legacy_react_payload(client):
    legacy = {
        **GOOD_PAYLOAD,
        "_client_family": "Doe",
        "_client_given": "John",
        "client_email": "john@example.com",
    }
    legacy.pop("people", None)
    res = client.post("/forms/form956/fill", json=legacy)
    assert res.status_code == 200
    assert res.headers["Content-Type"].startswith("application/pdf")


# ---------------------------------------------------------------- validator
def test_validator_marn_must_be_7_digits(form956_engine):
    from pdfform.validate import validate_form956
    schema_apps = {f["app"] for f in form956_engine.schema_dict()["fields"]}
    base = {
        "agent_family_name": "Smith", "agent_given_names": "Jane",
        "agent_dob": "01/01/1980", "agent_marn": "1234567",
        "agent_email": "j@e.com", "client_role": "visa",
        "application_type": "Skilled visa",
    }
    errs = validate_form956({**base, "agent_marn": "abc"}, schema_apps)
    assert any(e.field == "agent_marn" and e.code == "format" for e in errs)


def test_validator_required_field_blank(form956_engine):
    from pdfform.validate import validate_form956
    schema_apps = {f["app"] for f in form956_engine.schema_dict()["fields"]}
    base = {
        "agent_family_name": "Smith", "agent_given_names": "Jane",
        "agent_dob": "01/01/1980", "agent_marn": "1234567",
        "agent_email": "j@e.com", "client_role": "visa",
        "application_type": "Skilled visa",
    }
    errs = validate_form956({**base, "agent_family_name": ""}, schema_apps)
    assert any(e.field == "agent_family_name" and e.code == "required" for e in errs)


def test_validator_bad_email_format(form956_engine):
    from pdfform.validate import validate_form956
    schema_apps = {f["app"] for f in form956_engine.schema_dict()["fields"]}
    base = {
        "agent_family_name": "Smith", "agent_given_names": "Jane",
        "agent_dob": "01/01/1980", "agent_marn": "1234567",
        "agent_email": "j@e.com", "client_role": "visa",
        "application_type": "Skilled visa",
    }
    errs = validate_form956({**base, "agent_email": "not-an-email"}, schema_apps)
    assert any(e.field == "agent_email" and e.code == "format" for e in errs)


def test_validator_bad_dob_format(form956_engine):
    from pdfform.validate import validate_form956
    schema_apps = {f["app"] for f in form956_engine.schema_dict()["fields"]}
    base = {
        "agent_family_name": "Smith", "agent_given_names": "Jane",
        "agent_dob": "01/01/1980", "agent_marn": "1234567",
        "agent_email": "j@e.com", "client_role": "visa",
        "application_type": "Skilled visa",
    }
    errs = validate_form956({**base, "agent_dob": "31-12-1990"}, schema_apps)
    assert any(e.field == "agent_dob" and e.code == "format" for e in errs)


def test_validator_dob_impossible_date(form956_engine):
    from pdfform.validate import validate_form956
    schema_apps = {f["app"] for f in form956_engine.schema_dict()["fields"]}
    base = {
        "agent_family_name": "Smith", "agent_given_names": "Jane",
        "agent_dob": "01/01/1980", "agent_marn": "1234567",
        "agent_email": "j@e.com", "client_role": "visa",
        "application_type": "Skilled visa",
    }
    errs = validate_form956({**base, "agent_dob": "31/02/2020"}, schema_apps)
    assert any(e.field == "agent_dob" and e.code == "value" for e in errs)


def test_validator_bad_postcode(form956_engine):
    from pdfform.validate import validate_form956
    schema_apps = {f["app"] for f in form956_engine.schema_dict()["fields"]}
    base = {
        "agent_family_name": "Smith", "agent_given_names": "Jane",
        "agent_dob": "01/01/1980", "agent_marn": "1234567",
        "agent_email": "j@e.com", "client_role": "visa",
        "application_type": "Skilled visa",
    }
    errs = validate_form956({**base, "agent_resadd_pc": "ABC"}, schema_apps)
    assert any(e.field == "agent_resadd_pc" and e.code == "format" for e in errs)


def test_validator_unknown_field(form956_engine):
    from pdfform.validate import validate_form956
    schema_apps = {f["app"] for f in form956_engine.schema_dict()["fields"]}
    base = {
        "agent_family_name": "Smith", "agent_given_names": "Jane",
        "agent_dob": "01/01/1980", "agent_marn": "1234567",
        "agent_email": "j@e.com", "client_role": "visa",
        "application_type": "Skilled visa",
    }
    errs = validate_form956({**base, "definitely_not_a_field": "x"}, schema_apps)
    assert any(e.field == "definitely_not_a_field" and e.code == "unknown" for e in errs)


@pytest.mark.parametrize(
    "value", ["visa", "sponsor", "nom", "proposer", "holder", "person"]
)
def test_client_role_accepts_all_engine_options(form956_engine, value):
    """Every option the schema declares for client_role must validate —
    a prior bug had the frontend sending 'nominator' (invalid) instead of
    'nom', and silently defaulting proposer/cancellation/ministerial to
    'visa' instead of proposer/holder/person."""
    payload = dict(GOOD_PAYLOAD, client_role=value)
    errs = form956_engine.validate(payload)
    assert not any(e.startswith("'client_role'") for e in errs)


@pytest.mark.parametrize(
    "value", ["close", "sponsor", "nominator", "diplom", "parlia", "public"]
)
def test_exemption_reason_accepts_all_engine_options(form956_engine, value):
    """Every option the schema declares for exemption_reason must validate —
    a prior bug passed the frontend's raw UI values (close_family, diplomatic,
    mp_staff, public_service) straight through unmapped."""
    payload = dict(GOOD_PAYLOAD, exemption_reason=value)
    errs = form956_engine.validate(payload)
    assert not any(e.startswith("'exemption_reason'") for e in errs)


def test_apply_normalisations_converts_iso_dates():
    from pdfform.validate import apply_normalisations
    out = apply_normalisations({"agent_dob": "1990-06-15", "name": "X"})
    assert out["agent_dob"] == "15/06/1990"
    assert out["name"] == "X"


# ---------------------------------------------------------------- cache
def test_cache_canonicalise_strips_none():
    from pdfform.cache import canonicalise
    a = canonicalise({"a": 1, "b": None})
    b = canonicalise({"a": 1, "b": None, "c": None})
    assert a == b == '{"a":1}'


def test_cache_key_is_deterministic():
    from pdfform.cache import cache_key
    p = {"a": 1, "b": 2}
    assert cache_key(p) == cache_key(p)
    assert len(cache_key(p)) == 64  # SHA-256 hex


def test_cache_key_normalises_dates():
    from pdfform.cache import canonicalise
    iso = canonicalise({"agent_dob": "2026-05-01", "x": "y"})
    aus = canonicalise({"agent_dob": "01/05/2026", "x": "y"})
    # canonicalise() does NOT normalise — that's apply_normalisations' job.
    # The route applies it before hashing, so these two produce different
    # hashes here but the same key in production.
    assert iso != aus


# ---------------------------------------------------------------- HTTP
GOOD_PAYLOAD = {
    "agent_title": "mr",
    "agent_family_name": "Smith",
    "agent_given_names": "Jane",
    "agent_dob": "01/01/1980",
    "agent_marn": "1234567",
    "agent_email": "jane@example.com",
    "agent_resadd_str": "1 Pitt St",
    "agent_resadd_sub": "Sydney",
    "agent_resadd_cntry": "Australia",
    "agent_resadd_pc": "2000",
    "client_role": "visa",
    "client_dob": "15/06/1990",
    "client_resadd_str": "42 George St",
    "client_resadd_sub": "Sydney",
    "client_resadd_cntry": "Australia",
    "client_resadd_pc": "2000",
    "application_type": "Skilled visa",
    "date_lodged": "01/05/2026",
    "agent_decl_appointment": True,
    "agent_decl_authorised_recipient": True,
    "agent_decl_ending_appointment": True,
    "agent_decl_withdrawal_recipient": True,
    "client_decl_appointment": True,
    "client_decl_authorised_recipient": True,
    "client_decl_ending_appointment": True,
    "client_decl_withdrawal_recipient": True,
    "agent_declaration_date": "06/06/2026",
    "client_declaration_date": "06/06/2026",
}


def test_fill_returns_pdf_bytes(client, app):
    res = client.post("/forms/form956/fill", json=GOOD_PAYLOAD)
    assert res.status_code == 200
    assert res.headers["Content-Type"].startswith("application/pdf")
    assert res.headers["X-Cache-Key"]
    assert res.headers["X-Cache"] in ("hit", "miss")
    assert res.headers["X-Request-Id"]
    body = res.get_data()
    assert body.startswith(b"%PDF-")


def test_fill_idempotent_same_payload_returns_cache_hit(client, app):
    res1 = client.post("/forms/form956/fill", json=GOOD_PAYLOAD)
    res2 = client.post("/forms/form956/fill", json=GOOD_PAYLOAD)
    assert res1.headers["X-Cache-Key"] == res2.headers["X-Cache-Key"]
    assert res2.headers["X-Cache"] == "hit"


def test_fill_normalises_dates_into_same_cache_key(client, app):
    p1 = dict(GOOD_PAYLOAD, agent_dob="01/01/1980")
    p2 = dict(GOOD_PAYLOAD, agent_dob="1980-01-01")
    res1 = client.post("/forms/form956/fill", json=p1)
    res2 = client.post("/forms/form956/fill", json=p2)
    assert res1.headers["X-Cache-Key"] == res2.headers["X-Cache-Key"]


def test_fill_validation_error_returns_400(client):
    bad = dict(GOOD_PAYLOAD, agent_marn="abc")
    res = client.post("/forms/form956/fill", json=bad)
    assert res.status_code == 400
    body = res.get_json()
    assert body["error"] == "Validation failed"
    assert any(e["field"] == "agent_marn" for e in body["errors"])


def test_fill_missing_required_returns_400(client):
    bad = dict(GOOD_PAYLOAD)
    bad.pop("agent_family_name")
    res = client.post("/forms/form956/fill", json=bad)
    assert res.status_code == 400
    body = res.get_json()
    assert any(e["field"] == "agent_family_name" for e in body["errors"])


def test_fill_unknown_form_returns_404(client):
    res = client.post("/forms/nope/fill", json=GOOD_PAYLOAD)
    assert res.status_code == 404


# ------------------------------------------------------ Q15/Q16 branch fields
def test_fill_cancellation_and_rid_trn_land_on_distinct_widgets(client):
    """cancellation_subclass/cancellation_date_granted/client_rid/client_trn
    each have their own PDF widget, distinct from application_type/date_lodged
    and client_diac_id — a prior bug collapsed RID/TRN into client_diac_id and
    dropped the cancellation branch entirely."""
    import fitz

    payload = dict(
        GOOD_PAYLOAD,
        client_role="holder",
        assistance_category="Cancellation",
        cancellation_subclass="189",
        cancellation_date_granted="15/03/2022",
        client_rid="RID-0099",
        client_trn="TRN-8877",
        client_diac_id="DIAC-555",
    )
    res = client.post("/forms/form956/fill", json=payload)
    assert res.status_code == 200

    doc = fitz.open(stream=res.get_data(), filetype="pdf")
    values = {
        w.field_name: w.field_value
        for page in doc
        for w in (page.widgets() or [])
        if w.field_name
    }
    assert values["ta.typecancel"] == "189"
    assert values["ta.lodgedcancel"] == "15/03/2022"
    assert values["ta.diac request id"] == "RID-0099"
    assert values["ta.diac trans id"] == "TRN-8877"
    assert values["cc.diac id"] == "DIAC-555"
    # application_type/date_lodged widgets untouched by the cancellation values
    assert values["ta.type"] == GOOD_PAYLOAD["application_type"]


def test_fill_specific_matter_details_lands_on_own_widget(client):
    import fitz

    payload = dict(
        GOOD_PAYLOAD,
        assistance_category="Specific",
        specific_matter_details="Sponsorship monitoring",
    )
    res = client.post("/forms/form956/fill", json=payload)
    assert res.status_code == 200

    doc = fitz.open(stream=res.get_data(), filetype="pdf")
    values = {
        w.field_name: w.field_value
        for page in doc
        for w in (page.widgets() or [])
        if w.field_name
    }
    assert values["ta.specific matter"] == "Sponsorship monitoring"


def _mg_app_checked_state(pdf_bytes: bytes) -> dict[str, str]:
    """Return {"new": "on"/"off", "ended": "on"/"off"} for the mg.app
    checkbox pair, keyed by their vertical position on the page (the two
    widgets share one field name, so field_value alone can't distinguish
    them — the per-widget /AS appearance state can)."""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[2]
    widgets = [w for w in (page.widgets() or []) if w.field_name == "mg.app"]
    widgets.sort(key=lambda w: w.rect.y0)  # top widget ("New appointment") first
    result = {}
    for label, w in zip(("new", "ended"), widgets):
        obj = doc.xref_object(w.xref, compressed=False)
        as_line = next(line for line in obj.splitlines() if "/AS" in line)
        result[label] = "off" if "/Off" in as_line else "on"
    return result


def test_fill_new_appointment_ticks_new_not_ended(client):
    payload = dict(GOOD_PAYLOAD, is_new_application="Yes")
    res = client.post("/forms/form956/fill", json=payload)
    assert res.status_code == 200
    assert _mg_app_checked_state(res.get_data()) == {"new": "on", "ended": "off"}


def test_fill_ended_appointment_ticks_ended_not_new(client):
    payload = dict(GOOD_PAYLOAD, is_new_application="No")
    res = client.post("/forms/form956/fill", json=payload)
    assert res.status_code == 200
    assert _mg_app_checked_state(res.get_data()) == {"new": "off", "ended": "on"}


def test_fill_part_b_rid_trn_lands_on_own_widgets(client):
    """Q22 (Part B "provide at least one of RID/TRN") has its own widgets
    (mg.client diac request id/trans id), distinct from Q16's client_rid/
    client_trn (ta.diac request id/trans id) and from cc.diac id. These
    were previously entirely unmapped in the schema, so any value the app
    sent for them was silently dropped."""
    import fitz

    payload = dict(
        GOOD_PAYLOAD,
        end_client_rid="RID-PARTB-01",
        end_client_trn="TRN-PARTB-02",
        client_rid="RID-PARTA-01",
        client_trn="TRN-PARTA-02",
    )
    res = client.post("/forms/form956/fill", json=payload)
    assert res.status_code == 200

    doc = fitz.open(stream=res.get_data(), filetype="pdf")
    values = {
        w.field_name: w.field_value
        for page in doc
        for w in (page.widgets() or [])
        if w.field_name
    }
    assert values["mg.client diac request id"] == "RID-PARTB-01"
    assert values["mg.client diac trans id"] == "TRN-PARTB-02"
    # Part A's Q16 widgets are untouched by the Part B values
    assert values["ta.diac request id"] == "RID-PARTA-01"
    assert values["ta.diac trans id"] == "TRN-PARTA-02"


def test_fill_bad_radio_value_returns_422(client):
    bad = dict(GOOD_PAYLOAD, agent_title="emperor")  # not in on-state list
    res = client.post("/forms/form956/fill", json=bad)
    assert res.status_code in (400, 422)


def test_fill_by_key_returns_cached_pdf(client):
    res1 = client.post("/forms/form956/fill", json=GOOD_PAYLOAD)
    key = res1.headers["X-Cache-Key"]
    res2 = client.get(f"/forms/form956/fill?key={key}")
    assert res2.status_code == 200
    assert res2.get_data() == res1.get_data()


def test_fill_by_unknown_key_returns_404(client):
    res = client.get("/forms/form956/fill?key=deadbeef" * 8)
    assert res.status_code == 404


def test_extract_returns_widget_values(client, app):
    res_fill = client.post("/forms/form956/fill", json=GOOD_PAYLOAD)
    key = res_fill.headers["X-Cache-Key"]
    res = client.get(f"/forms/form956/extract?key={key}")
    assert res.status_code == 200
    data = res.get_json()
    # Widget names match the YAML config; case matters.
    assert data["mg.title"] == "mr"
    assert data["mg.name fam"] == "Smith"
    assert data["mg.name giv"] == "Jane"
    assert data["mg.marn"] == "1234567"


# ---------------------------------------------------------------- logging
def test_pii_filter_masks_email_and_marn():
    from pdfform.logging_setup import mask_text
    assert "j***@example.com" in mask_text("jane@example.com")
    assert "******7" in mask_text("1234567")


def test_pii_filter_handles_pii_keys_in_payload():
    from pdfform.logging_setup import PIIFilter
    import logging
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    rec.payload = {"agent_email": "j@e.com", "agent_marn": "1234567",
                   "agent_title": "mr"}
    f = PIIFilter()
    f.filter(rec)
    assert rec.payload["agent_email"] == "***"
    assert rec.payload["agent_marn"] == "***"
    assert rec.payload["agent_title"] == "mr"  # not PII — pass through


# ---------------------------------------------------------------- engine round-trip
def test_engine_round_trip_preserves_values(form956_engine, tmp_path):
    out = tmp_path / "filled.pdf"
    form956_engine.fill(GOOD_PAYLOAD, str(out))
    assert out.exists()
    extracted = form956_engine.extract(str(out))
    assert extracted.get("mg.title") == "mr"
    assert extracted.get("mg.name fam") == "Smith"
    assert extracted.get("mg.name giv") == "Jane"
    assert extracted.get("mg.marn") == "1234567"


# ---------------------------------------------------------------- Part B (ending appointment)
# Q18-Q21 previously had no app-schema fields at all (only the three Q19/Q21
# Yes/No radios existed) — the agent/client identity, address, phone and
# MARN/LPN widgets on page 5 were unreachable from the API.
PART_B_PAYLOAD = {
    **GOOD_PAYLOAD,
    "end_agent_family_name": "Trinh",
    "end_agent_given_names": "Chi",
    "end_agent_org_name": "Winzoy Legal",
    "end_agent_off_ph_cc": "61",
    "end_agent_off_ph_ac": "02",
    "end_agent_off_ph": "90001234",
    "end_agent_mob": "0424010868",
    "end_agent_marn": "1465990",
    "end_agent_lpn": "10138",
    "also_assisting_in_ending": "Yes",
    "ending_this_appointment": "No",
    "end_client_family_name": "Doe",
    "end_client_given_names": "John",
    "end_client_dob": "15/06/1990",
    "end_client_org_name": "Acme Pty Ltd",
    "end_client_resadd_str": "42 George St",
    "end_client_resadd_sub": "Sydney",
    "end_client_resadd_cntry": "Australia",
    "end_client_resadd_pc": "2000",
    "end_client_off_ph_cc": "61",
    "end_client_off_ph_ac": "02",
    "end_client_off_ph": "90005678",
    "end_client_mob": "0400000000",
    "communicated_ending": "Yes",
    "end_client_email": "john@example.com",
}


def test_part_b_fields_are_known_to_schema(form956_engine):
    schema_apps = {f["app"] for f in form956_engine.schema_dict()["fields"]}
    for app_field in PART_B_PAYLOAD:
        assert app_field in schema_apps, f"{app_field!r} missing from form956.yaml"


def test_part_b_round_trip_preserves_values(form956_engine, tmp_path):
    out = tmp_path / "filled_partb.pdf"
    form956_engine.fill(PART_B_PAYLOAD, str(out))
    assert out.exists()
    extracted = form956_engine.extract(str(out))
    assert extracted.get("mg.mig name fam") == "Trinh"
    assert extracted.get("mg.mig name giv") == "Chi"
    assert extracted.get("mg.mig agent name org") == "Winzoy Legal"
    assert extracted.get("mg.mig mobile pn") == "0424010868"
    assert extracted.get("mg.end mig marn num") == "1465990"
    assert extracted.get("mg.end mig lpn num") == "10138"
    assert extracted.get("mg.also ar") == "Yes"
    assert extracted.get("mg.ending ar") == "No"
    assert extracted.get("mg.client name fam") == "Doe"
    assert extracted.get("mg.client name giv") == "John"
    assert extracted.get("mg.client dob") == "15/06/1990"
    assert extracted.get("mg.client org name") == "Acme Pty Ltd"
    assert extracted.get("mg.end resadd str") == "42 George St"
    assert extracted.get("mg.end resadd pc") == "2000"
    assert extracted.get("mg.end mob") == "0400000000"
    assert extracted.get("mg.end comm") == "Yes"
    assert extracted.get("mg.end email") == "john@example.com"


def test_part_b_fill_via_http_returns_pdf(client):
    res = client.post("/forms/form956/fill", json=PART_B_PAYLOAD)
    assert res.status_code == 200
    assert res.headers["Content-Type"].startswith("application/pdf")


# ---------------------------------------------------------------- graceful
# Server must start even when flask-cors and flask-limiter are
# absent. We just confirm the import path doesn't blow up and the
# health endpoint still answers.
def test_server_starts_without_optional_deps(client):
    res = client.get("/health")
    assert res.status_code == 200
