"""Flask app — generic form picker + dynamic form filler.

Drop a YAML config in ``forms/`` and the app picks it up on startup. No
code changes needed for a new PDF. Each form gets its own URL at
``/forms/<id>``; the renderer in ``templates/form.html`` fetches
``/forms/<id>/schema.json`` and builds the inputs from the config.

Run:
    python app.py
Then open http://127.0.0.1:5000
"""
from __future__ import annotations

import logging
import os
import re

from flask import Flask, jsonify, render_template, request, send_file

os.chdir(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__)

# --------------------------------------------------------------------- logging
# JSON logs + request-id middleware + PII mask. Wire BEFORE the routes
# register their before/after handlers, which is just "before we run".
from pdfform.logging_setup import init_logging
init_logging(app, level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("app")

# --------------------------------------------------------------------- CORS
# Optional. If flask-cors isn't installed, fall back to a no-op so the
# service still runs in the local-dev case (where the React app and
# Flask are same-origin or proxied).
try:
    from flask_cors import CORS
    CORS(
        app,
        resources={r"/forms/*": {"origins": [
            re.compile(r"^https://([a-z0-9-]+\.)*lovable\.app$"),
            "https://pipeline.winzoylegal.com.au",
            "http://localhost:8080",
        ]}},
        methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-Id"],
        expose_headers=["X-Cache-Key", "X-Cache", "X-Request-Id"],
        max_age=86400,
    )
except ImportError:
    log.warning("flask-cors not installed; CORS headers not added (fine in dev).")

# --------------------------------------------------------------------- rate limit
# Optional. Default 60 fills/min per IP. Override with RATE_LIMIT env var
# (e.g. "200/minute"). If flask-limiter isn't installed, log and skip.
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    _limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[],
        storage_uri="memory://",
    )
    FILL_RATE_LIMIT = os.environ.get("RATE_LIMIT", "60/minute")
    log.info("rate limit on /fill", extra={"limit": FILL_RATE_LIMIT})
except ImportError:
    _limiter = None
    log.warning("flask-limiter not installed; rate limiting disabled.")

# --------------------------------------------------------------------- forms
from pdfform import engine as pdfform_engine
from pdfform.cache import PdfCache, cache_key
from pdfform.schema import ConfigError

FORMS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "forms")
ENGINES: dict[str, pdfform_engine.FormEngine] = pdfform_engine.load_all(FORMS_DIR)

# --------------------------------------------------------------------- cache
# Cache dir is overridable via UPLOADS_DIR (used by the test suite to
# isolate from the production on-disk cache). Default: <repo>/uploads/cache.
_BASE_UPLOADS = os.environ.get("UPLOADS_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "uploads"
)
CACHE_DIR = os.path.join(_BASE_UPLOADS, "cache")
CACHE = PdfCache(CACHE_DIR)

# --------------------------------------------------------------------- validators
# Per-form validator registry. Add a sibling module + register it here
# when a new form needs stricter domain rules. The default (no entry)
# falls back to the engine's permissive ``validate()``.
from pdfform.validate import validate_form956
VALIDATORS: dict[str, callable] = {  # type: ignore[type-arg]
    "form956": validate_form956,
}


def _form_listing() -> list[dict]:
    """Picklist payload for the form-picker landing page."""
    out: list[dict] = []
    for eng in ENGINES.values():
        schema = eng.schema_dict()
        out.append({
            "id": eng.id,
            "title": eng.title,
            "pages": eng.total_pages,
            "field_count": len(schema["fields"]),
        })
    # Sort: title asc, then id for stability.
    out.sort(key=lambda d: (d["title"].lower(), d["id"]))
    return out


# --------------------------------------------------------------------- routes
@app.route("/")
def index():
    return render_template("index.html", forms=_form_listing())


@app.route("/forms/<form_id>")
def form_page(form_id: str):
    eng = ENGINES.get(form_id)
    if not eng:
        return jsonify({"error": f"Unknown form {form_id!r}"}), 404
    return render_template("form.html", title=eng.title, form_id=form_id)


@app.route("/forms/<form_id>/schema.json")
def form_schema(form_id: str):
    eng = ENGINES.get(form_id)
    if not eng:
        return jsonify({"error": f"Unknown form {form_id!r}"}), 404
    return jsonify(eng.schema_dict())


# Rate-limit decorator: applied at registration time. If flask-limiter
# isn't installed, ``_limiter`` is None and the route registers plain.
_fill_decorator = _limiter.limit(FILL_RATE_LIMIT) if _limiter is not None else (lambda f: f)

@app.route("/forms/<form_id>/fill", methods=["POST"])
@_fill_decorator
def form_fill(form_id: str):
    """Fill a form. Returns the PDF bytes directly with X-Cache-Key.

    Status codes:
      - 200: PDF returned (cache hit OR fresh fill). X-Cache: hit|miss.
      - 400: validation error → JSON body ``{errors: [{field, code, message}]}``.
      - 404: unknown form.
      - 422: engine fill failure (radio value not in on-state list, etc.).
      - 500: unexpected.
    """
    eng = ENGINES.get(form_id)
    if not eng:
        return jsonify({"error": f"Unknown form {form_id!r}"}), 404
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "Body must be a JSON object"}), 400

    # 1. Per-form validator (stricter domain rules).
    validator = VALIDATORS.get(form_id)
    if validator is not None:
        known_apps = {f["app"] for f in eng.schema_dict()["fields"]}
        verrs = validator(body, known_apps)
        if verrs:
            log.info("validation failed", extra={"form_id": form_id})
            return jsonify({
                "error": "Validation failed",
                "errors": [v.to_dict() for v in verrs],
            }), 400
        # Apply date normalisations before hashing + filling.
        from pdfform.validate import apply_normalisations
        body = apply_normalisations(body)

    # 2. Engine shape check (unknown app fields, radio values).
    shape_errors = eng.validate(body)
    if shape_errors:
        return jsonify({
            "error": "Validation failed",
            "errors": [{"field": "?", "code": "shape", "message": e} for e in shape_errors],
        }), 400

    # 3. Idempotence: hash the payload, return cached PDF if present.
    key = cache_key(body)
    if CACHE.has(key):
        log.info("cache hit", extra={"form_id": form_id, "cache_hit": True})
        resp = send_file(CACHE.path_of(key), mimetype="application/pdf", as_attachment=False)
        resp.headers["X-Cache-Key"] = key
        resp.headers["X-Cache"] = "hit"
        return resp

    # 4. Cold fill: write to a temp file inside the cache dir so the
    # same canonical key never collides on disk.
    out_path = CACHE.path_of(key)
    try:
        eng.fill(body, out_path)
    except ConfigError as e:
        log.info("engine fill failed", extra={"form_id": form_id})
        return jsonify({
            "error": "PDF generation failed",
            "errors": [{"field": "?", "code": "engine", "message": str(e)}],
        }), 422

    log.info("cache miss", extra={"form_id": form_id, "cache_hit": False})
    resp = send_file(out_path, mimetype="application/pdf", as_attachment=False)
    resp.headers["X-Cache-Key"] = key
    resp.headers["X-Cache"] = "miss"
    return resp


@app.route("/forms/<form_id>/fill")
def form_fill_by_key(form_id: str):
    """Re-fetch a previously filled PDF by its cache key.

    Usage: GET /forms/<id>/fill?key=<32-hex>. Returns 404 if the key
    is unknown (caller should refill).
    """
    eng = ENGINES.get(form_id)
    if not eng:
        return jsonify({"error": f"Unknown form {form_id!r}"}), 404
    key = request.args.get("key", "")
    if not key or not CACHE.has(key):
        return jsonify({"error": "Unknown or expired cache key"}), 404
    return send_file(CACHE.path_of(key), mimetype="application/pdf", as_attachment=False)


@app.route("/forms/<form_id>/extract")
def form_extract(form_id: str):
    """Read the live widget values from a cached filled PDF.

    Usage: GET /forms/<id>/extract?key=<32-hex>.
    """
    eng = ENGINES.get(form_id)
    if not eng:
        return jsonify({"error": f"Unknown form {form_id!r}"}), 404
    key = request.args.get("key", "")
    if not key or not CACHE.has(key):
        return jsonify({"error": "Unknown or expired cache key"}), 404
    return jsonify(eng.extract(CACHE.path_of(key)))


@app.route("/forms/<form_id>/preview")
def form_preview(form_id: str):
    """Render the cached PDF inline (for browser preview)."""
    eng = ENGINES.get(form_id)
    if not eng:
        return jsonify({"error": f"Unknown form {form_id!r}"}), 404
    key = request.args.get("key", "")
    if not key or not CACHE.has(key):
        return jsonify({"error": "Unknown or expired cache key"}), 404
    return send_file(CACHE.path_of(key), mimetype="application/pdf", as_attachment=False)


# --------------------------------------------------------------------- ops
@app.route("/health")
def health():
    """Liveness probe. No auth, ~1ms."""
    return jsonify({
        "ok": True,
        "forms_loaded": len(ENGINES),
        "forms": sorted(ENGINES.keys()),
        **CACHE.stats(),
    })


if __name__ == "__main__":
    # debug=False — auto-reload on file changes would wipe the in-memory
    # ENGINE / CACHE state on every edit, which is fine for a dev box
    # but noisy when the form HTML is open in a tab.
    app.run(host="127.0.0.1", port=5000, debug=False)
