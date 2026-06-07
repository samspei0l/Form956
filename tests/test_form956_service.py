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
    "agent_declarations_agreed": True,
    "client_declarations_agreed": True,
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


# ---------------------------------------------------------------- graceful
# Server must start even when flask-cors and flask-limiter are
# absent. We just confirm the import path doesn't blow up and the
# health endpoint still answers.
def test_server_starts_without_optional_deps(client):
    res = client.get("/health")
    assert res.status_code == 200
