"""JSON-formatted request logging with PII masking.

Every request gets a UUID. The UUID is:
  - bound to a Flask ``g`` value,
  - returned to the client in the ``X-Request-Id`` header,
  - added to every log record emitted during the request,
  - shown back in the JSON error body so the user can quote it in a
    support ticket.

PII masking runs as a log filter. The route handlers never log raw
payloads; even if a future change does, the filter catches the obvious
PII keys (``agent_email``, ``client_email``, ``agent_marn``, etc.)
before the line is emitted.

We deliberately don't bring in ``python-json-logger`` — stdlib's
``logging`` plus a tiny formatter is enough and means one fewer
dependency.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

from flask import Flask, g, request

# Keys we never want to see in plain text in logs.
PII_KEYS = frozenset({
    "agent_email", "client_email",
    "agent_marn", "agent_lpn",
    "agent_family_name", "agent_given_names",
    "client_family_name", "client_given_names",
    "agent_dob", "client_dob",
    "passport_number", "applicant_passport_number",
})

#: Loose email matcher for the masking filter (different from the
#: validator's regex — this one needs to match text inside log lines).
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
MARN_RE = re.compile(r"\b\d{7}\b")


def _mask_email(s: str) -> str:
    user, _, domain = s.partition("@")
    if not user:
        return s
    user_masked = (user[0] + "***") if len(user) > 1 else "*"
    return f"{user_masked}@{domain}"


def _mask_marn(s: str) -> str:
    return "******" + s[-1] if len(s) >= 1 else "*******"


def mask_text(s: str) -> str:
    """Replace emails and 7-digit numbers in ``s`` with masked versions."""
    s = EMAIL_RE.sub(lambda m: _mask_email(m.group(0)), s)
    s = MARN_RE.sub(lambda m: _mask_marn(m.group(0)), s)
    return s


class PIIFilter(logging.Filter):
    """Filter that masks known PII in record messages + extra dict."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_text(record.msg)
        if record.args:
            record.args = tuple(
                mask_text(a) if isinstance(a, str) else a for a in record.args
            )
        if hasattr(record, "payload") and isinstance(record.payload, dict):
            record.payload = {
                k: ("***" if k in PII_KEYS else v)
                for k, v in record.payload.items()
            }
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per log line, with request_id bound from Flask g."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Bind request id if we're inside a request.
        try:
            rid = g.get("request_id")
            if rid:
                payload["request_id"] = rid
        except RuntimeError:
            # Outside Flask application context — skip.
            pass
        # Promote known extra fields.
        for k in ("form_id", "cache_hit", "latency_ms", "status",
                  "payload", "method", "path", "ip"):
            v = getattr(record, k, None)
            if v is not None:
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def init_logging(app: Flask, level: str = "INFO") -> None:
    """Wire JSON logging + request-id middleware into ``app``."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(PIIFilter())
    root = logging.getLogger()
    # Replace any existing handlers so we don't get duplicate lines.
    root.handlers = [handler]
    root.setLevel(level)

    @app.before_request
    def _bind_request_id() -> None:
        g.request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
        g.request_started = time.perf_counter()

    @app.after_request
    def _emit_request_log(response):  # type: ignore[no-untyped-def]
        try:
            latency_ms = int((time.perf_counter() - g.request_started) * 1000)
        except Exception:
            latency_ms = -1
        response.headers["X-Request-Id"] = g.get("request_id", "")
        logging.getLogger("http").info(
            "%s %s -> %s", request.method, request.path, response.status_code,
            extra={
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "latency_ms": latency_ms,
                "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
            },
        )
        return response
