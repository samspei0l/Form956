"""Tests for pdfform/auth.py -- caller authentication on the PDF routes.

Uses a throwaway Flask app with stand-in routes so the real app's
module-level state (cache dir, registries) isn't involved, and an injected
``verify`` so no test touches Supabase.
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pdfform.auth import AuthUnavailable, init_auth  # noqa: E402


def _jwt(exp: float) -> str:
    def b64(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'none'})}.{b64({'exp': exp, 'sub': 'u1'})}.sig"


def _make(mode="enforce", service_token="svc-secret", verify=None):
    app = Flask(__name__)
    init_auth(app, mode=mode, service_token=service_token, verify=verify)

    @app.post("/cost-agreements/<t>/fill")
    def fill(t):
        return "pdf"

    @app.get("/forms/<f>/preview")
    def preview(f):
        return "pdf"

    @app.get("/forms/<f>/schema.json")
    def schema(f):
        return "{}"

    @app.get("/health")
    def health():
        return "ok"

    return app.test_client()


def test_off_mode_lets_everything_through():
    c = _make(mode="off")
    assert c.post("/cost-agreements/invoice/fill").status_code == 200


def test_enforce_rejects_anonymous_pdf_requests():
    c = _make(verify=lambda t: True)
    assert c.post("/cost-agreements/invoice/fill").status_code == 401
    assert c.get("/forms/form956/preview").status_code == 401


def test_open_routes_and_preflight_stay_open():
    c = _make(verify=lambda t: False)
    assert c.get("/health").status_code == 200
    assert c.get("/forms/form956/schema.json").status_code == 200
    assert c.open("/cost-agreements/invoice/fill", method="OPTIONS").status_code != 401


def test_service_token_admits_and_wrong_token_does_not():
    c = _make(verify=lambda t: False)
    assert c.post("/cost-agreements/declaration/fill", headers={"X-Service-Token": "svc-secret"}).status_code == 200
    assert c.post("/cost-agreements/declaration/fill", headers={"X-Service-Token": "nope"}).status_code == 401


def test_bearer_token_checked_with_supabase_and_cached():
    calls = []

    def verify(token):
        calls.append(token)
        return token == good

    good = _jwt(time.time() + 3600)
    c = _make(verify=verify)
    h = {"Authorization": f"Bearer {good}"}
    assert c.post("/cost-agreements/invoice/fill", headers=h).status_code == 200
    assert c.post("/cost-agreements/invoice/fill", headers=h).status_code == 200
    assert calls == [good], "a verified token should be cached, not re-checked every request"

    bad = {"Authorization": f"Bearer {_jwt(time.time() + 3600)}x"}
    assert c.post("/cost-agreements/invoice/fill", headers=bad).status_code == 401


def test_expired_token_is_never_cached():
    calls = []
    expired = _jwt(time.time() - 10)

    def verify(token):
        calls.append(token)
        return True  # whatever Supabase says, the cache must not hold it

    c = _make(verify=verify)
    h = {"Authorization": f"Bearer {expired}"}
    c.post("/cost-agreements/invoice/fill", headers=h)
    c.post("/cost-agreements/invoice/fill", headers=h)
    assert len(calls) == 2


def test_auth_backend_down_is_503_not_401():
    def verify(token):
        raise AuthUnavailable("down")

    c = _make(verify=verify)
    res = c.post("/cost-agreements/invoice/fill", headers={"Authorization": "Bearer abc.def.ghi"})
    assert res.status_code == 503


def test_enforce_without_any_credential_source_refuses_to_start():
    with pytest.raises(RuntimeError):
        init_auth(Flask(__name__), mode="enforce", service_token="", verify=None)


def test_unknown_mode_refuses_to_start():
    with pytest.raises(RuntimeError):
        init_auth(Flask(__name__), mode="on", service_token="x")
