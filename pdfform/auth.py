"""Caller authentication for the PDF-generating routes.

Until this existed, ``/cost-agreements/*`` and ``/forms/*`` accepted any
caller: CORS only stops *browsers* on other origins from reading the
response, so anyone with the URL could mint a Winzoy-branded tax invoice,
receipt or Statement of Services with whatever figures they liked.

Two ways in, either is enough:

1. ``Authorization: Bearer <Supabase access token>`` -- what the CRM's
   browser client sends (the signed-in staff member's session). Verified
   by asking Supabase Auth (``GET <SUPABASE_URL>/auth/v1/user``), which
   works for both the legacy HS256 secret and the newer asymmetric signing
   keys without this service holding any key material. Successful checks
   are cached briefly (keyed by a hash of the token, never the token
   itself) and never past the token's own ``exp``.
2. ``X-Service-Token: <FORM956_SERVICE_TOKEN>`` -- for server-to-server
   callers with no user session, i.e. the ``submit-declaration`` edge
   function, which runs on behalf of a client signing without a login.

Rollout is staged by ``AUTH_MODE``:

- ``off`` (default): nothing is checked; a warning is logged at startup.
  Deploy this first -- it also adds ``Authorization`` to the CORS
  allow-list, without which the CRM's preflight would fail.
- ``enforce``: protected routes return ``401`` without valid credentials.
  Switch to this only after the CRM build that sends tokens is live.

Only the routes that produce or return a PDF are protected (see
``PROTECTED``). ``/health``, the form-picker pages and ``schema.json`` stay
open; CORS preflight (``OPTIONS``) is never challenged.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

from flask import Flask, jsonify, request

log = logging.getLogger("auth")

PROTECTED = re.compile(r"^/(?:cost-agreements/[^/]+/fill|forms/[^/]+/(?:fill|extract|preview))/?$")

_CACHE_TTL_S = 120
_CACHE_MAX = 2000


class AuthUnavailable(Exception):
    """Supabase Auth couldn't be reached -- a 503, not a 401."""


def _jwt_exp(token: str) -> float | None:
    """The token's ``exp`` claim, read without verifying the signature
    (only used to bound the cache lifetime; Supabase does the verifying)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return float(exp) if exp is not None else None
    except (IndexError, ValueError, TypeError, AttributeError):
        return None


def supabase_verifier(supabase_url: str, api_key: str, timeout: float = 5.0) -> Callable[[str], bool]:
    """Returns ``verify(token) -> bool`` that asks Supabase Auth whether the
    token belongs to a live session. Raises ``AuthUnavailable`` when Supabase
    can't answer."""
    url = supabase_url.rstrip("/") + "/auth/v1/user"

    def verify(token: str) -> bool:
        req = urllib.request.Request(url, headers={
            "apikey": api_key,
            "Authorization": f"Bearer {token}",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 -- fixed https URL from config
                return 200 <= res.status < 300
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return False
            raise AuthUnavailable(f"Supabase Auth returned HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise AuthUnavailable(str(e)) from e

    return verify


class _TokenCache:
    def __init__(self) -> None:
        self._entries: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def valid(self, token: str) -> bool:
        k = self._key(token)
        with self._lock:
            exp = self._entries.get(k)
            if exp is None:
                return False
            if exp <= time.time():
                del self._entries[k]
                return False
            return True

    def remember(self, token: str) -> None:
        now = time.time()
        until = now + _CACHE_TTL_S
        exp = _jwt_exp(token)
        if exp is not None:
            until = min(until, exp)
        if until <= now:
            return
        with self._lock:
            if len(self._entries) >= _CACHE_MAX:
                self._entries.clear()
            self._entries[self._key(token)] = until


def init_auth(
    app: Flask,
    *,
    mode: str | None = None,
    service_token: str | None = None,
    verify: Callable[[str], bool] | None = None,
) -> None:
    """Register the before-request check. Arguments default to the env vars
    ``AUTH_MODE``, ``FORM956_SERVICE_TOKEN``, ``SUPABASE_URL`` and
    ``SUPABASE_PUBLISHABLE_KEY`` (or ``SUPABASE_ANON_KEY``); tests pass them
    directly."""
    mode = (mode if mode is not None else os.environ.get("AUTH_MODE", "off")).strip().lower()
    if mode not in ("off", "enforce"):
        raise RuntimeError(f"AUTH_MODE must be 'off' or 'enforce', got {mode!r}")
    service_token = service_token if service_token is not None else os.environ.get("FORM956_SERVICE_TOKEN", "")

    if verify is None:
        sb_url = os.environ.get("SUPABASE_URL", "")
        sb_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
        if sb_url and sb_key:
            verify = supabase_verifier(sb_url, sb_key)

    if mode == "off":
        log.warning("AUTH_MODE=off: PDF routes accept unauthenticated callers.")
        return
    if verify is None and not service_token:
        raise RuntimeError(
            "AUTH_MODE=enforce needs SUPABASE_URL + SUPABASE_PUBLISHABLE_KEY and/or FORM956_SERVICE_TOKEN"
        )

    cache = _TokenCache()

    def _deny(status: int, message: str):
        return jsonify({"error": message}), status

    @app.before_request
    def _require_auth():
        if request.method == "OPTIONS" or not PROTECTED.match(request.path):
            return None

        presented = request.headers.get("X-Service-Token", "")
        if presented and service_token and hmac.compare_digest(presented.encode(), service_token.encode()):
            return None

        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header[:7].lower() == "bearer " else ""
        if not token or verify is None:
            return _deny(401, "Authentication required")
        if cache.valid(token):
            return None
        try:
            ok = verify(token)
        except AuthUnavailable:
            log.exception("auth backend unavailable")
            return _deny(503, "Authentication service unavailable, please retry")
        if not ok:
            return _deny(401, "Invalid or expired session")
        cache.remember(token)
        return None

    log.info("AUTH_MODE=enforce on PDF routes")
