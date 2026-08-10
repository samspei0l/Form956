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
from pdfform.adapt_form956 import adapt_form956_payload
from pdfform.validate import validate_form956
VALIDATORS: dict[str, callable] = {  # type: ignore[type-arg]
    "form956": validate_form956,
}
ADAPTERS: dict[str, callable] = {  # type: ignore[type-arg]
    "form956": adapt_form956_payload,
}

# --------------------------------------------------------------------- cost agreements
# A second, structurally different PDF-generation capability: builds a new
# PDF from scratch (ReportLab Platypus flowables) rather than filling an
# existing AcroForm, so it gets its own registries/routes instead of being
# folded into VALIDATORS/ADAPTERS above. See costagreements/layout.py for
# why (the "spacing between content and the signature/initials/date block"
# bug class this replaces).
from costagreements.builders.art import ArtCostAgreementData, apply_art_normalisations, build_art_cost_agreement, validate_art_cost_agreement
from costagreements.builders.bfa import BfaCostAgreementData, apply_bfa_normalisations, build_bfa_cost_agreement, validate_bfa_cost_agreement
from costagreements.builders.client_agreement import (
    ClientAgreementData,
    apply_client_agreement_normalisations,
    build_client_agreement,
    validate_client_agreement,
)
from costagreements.builders.divorce import (
    DivorceCostAgreementData,
    apply_divorce_normalisations,
    build_divorce_cost_agreement,
    validate_divorce_cost_agreement,
)
from costagreements.builders.general import build_general_cost_agreement
from costagreements.builders.jrp import JrpCostAgreementData, apply_jrp_normalisations, build_jrp_cost_agreement, validate_jrp_cost_agreement
from costagreements.builders.partner_visa import (
    PartnerVisaCostAgreementData,
    apply_partner_visa_normalisations,
    build_partner_visa_cost_agreement,
    validate_partner_visa_cost_agreement,
)
from costagreements.builders.skilled_visa import (
    SkilledVisaCostAgreementData,
    apply_skilled_visa_normalisations,
    build_skilled_visa_cost_agreement,
    validate_skilled_visa_cost_agreement,
)
from costagreements.builders.skills_assessment import (
    SkillsAssessmentCostAgreementData,
    apply_skills_assessment_normalisations,
    build_skills_assessment_cost_agreement,
    validate_skills_assessment_cost_agreement,
)
from costagreements.builders.skills_assessment_186 import (
    SkillsAssessment186CostAgreementData,
    apply_skills_assessment_186_normalisations,
    build_skills_assessment_186_cost_agreement,
    validate_skills_assessment_186_cost_agreement,
)
from costagreements.builders.skills_assessment_only import (
    SkillsAssessmentOnlyCostAgreementData,
    apply_skills_assessment_only_normalisations,
    build_skills_assessment_only_cost_agreement,
    validate_skills_assessment_only_cost_agreement,
)
from costagreements.builders.visa_482 import (
    Visa482CostAgreementData,
    apply_visa_482_normalisations,
    build_visa_482_cost_agreement,
    validate_visa_482_cost_agreement,
)
from costagreements.builders.visa_870 import (
    Visa870CostAgreementData,
    apply_visa_870_normalisations,
    build_visa_870_cost_agreement,
    validate_visa_870_cost_agreement,
)
from costagreements.schema import GeneralCostAgreementData
from costagreements.validate import apply_normalisations as apply_ca_normalisations
from costagreements.validate import validate_general_cost_agreement

COST_AGREEMENT_BUILDERS: dict[str, callable] = {  # type: ignore[type-arg]
    "general": build_general_cost_agreement,
    "client_agreement": build_client_agreement,
    "art": build_art_cost_agreement,
    "jrp": build_jrp_cost_agreement,
    "skills_assessment_only": build_skills_assessment_only_cost_agreement,
    "partner_visa": build_partner_visa_cost_agreement,
    "skilled_visa": build_skilled_visa_cost_agreement,
    "skills_assessment": build_skills_assessment_cost_agreement,
    "skills_assessment_186": build_skills_assessment_186_cost_agreement,
    "bfa": build_bfa_cost_agreement,
    "divorce": build_divorce_cost_agreement,
    "visa_482": build_visa_482_cost_agreement,
    "visa_870": build_visa_870_cost_agreement,
}
COST_AGREEMENT_SCHEMAS: dict[str, callable] = {  # type: ignore[type-arg]
    "general": GeneralCostAgreementData.from_payload,
    "client_agreement": ClientAgreementData.from_payload,
    "art": ArtCostAgreementData.from_payload,
    "jrp": JrpCostAgreementData.from_payload,
    "skills_assessment_only": SkillsAssessmentOnlyCostAgreementData.from_payload,
    "partner_visa": PartnerVisaCostAgreementData.from_payload,
    "skilled_visa": SkilledVisaCostAgreementData.from_payload,
    "skills_assessment": SkillsAssessmentCostAgreementData.from_payload,
    "skills_assessment_186": SkillsAssessment186CostAgreementData.from_payload,
    "bfa": BfaCostAgreementData.from_payload,
    "divorce": DivorceCostAgreementData.from_payload,
    "visa_482": Visa482CostAgreementData.from_payload,
    "visa_870": Visa870CostAgreementData.from_payload,
}
COST_AGREEMENT_VALIDATORS: dict[str, callable] = {  # type: ignore[type-arg]
    "general": validate_general_cost_agreement,
    "client_agreement": validate_client_agreement,
    "art": validate_art_cost_agreement,
    "jrp": validate_jrp_cost_agreement,
    "skills_assessment_only": validate_skills_assessment_only_cost_agreement,
    "partner_visa": validate_partner_visa_cost_agreement,
    "skilled_visa": validate_skilled_visa_cost_agreement,
    "skills_assessment": validate_skills_assessment_cost_agreement,
    "skills_assessment_186": validate_skills_assessment_186_cost_agreement,
    "bfa": validate_bfa_cost_agreement,
    "divorce": validate_divorce_cost_agreement,
    "visa_482": validate_visa_482_cost_agreement,
    "visa_870": validate_visa_870_cost_agreement,
}
COST_AGREEMENT_NORMALISERS: dict[str, callable] = {  # type: ignore[type-arg]
    "general": apply_ca_normalisations,
    "client_agreement": apply_client_agreement_normalisations,
    "art": apply_art_normalisations,
    "jrp": apply_jrp_normalisations,
    "skills_assessment_only": apply_skills_assessment_only_normalisations,
    "partner_visa": apply_partner_visa_normalisations,
    "skilled_visa": apply_skilled_visa_normalisations,
    "skills_assessment": apply_skills_assessment_normalisations,
    "skills_assessment_186": apply_skills_assessment_186_normalisations,
    "bfa": apply_bfa_normalisations,
    "divorce": apply_divorce_normalisations,
    "visa_482": apply_visa_482_normalisations,
    "visa_870": apply_visa_870_normalisations,
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

    adapter = ADAPTERS.get(form_id)
    if adapter is not None:
        body = adapter(body)

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


# --------------------------------------------------------------------- cost agreements
_ca_fill_decorator = _limiter.limit(FILL_RATE_LIMIT) if _limiter is not None else (lambda f: f)


@app.route("/cost-agreements/<agreement_type>/fill", methods=["POST"])
@_ca_fill_decorator
def cost_agreement_fill(agreement_type: str):
    """Build a cost-agreement PDF from scratch and return it.

    Unlike ``/forms/<id>/fill`` this doesn't fill an existing AcroForm --
    it builds a brand-new document (see costagreements/). Synchronous only:
    the returned PDF is final; supply ``client_signature_data`` /
    ``rep_signature_data`` (base64 image data URIs) up front if signatures
    are already captured, otherwise the signature boxes render empty for
    print-and-sign.

    Status codes:
      - 200: PDF returned (cache hit OR fresh build). X-Cache: hit|miss.
      - 400: validation error -> JSON body {errors: [{field, code, message}]}.
      - 404: unknown agreement type.
      - 500: unexpected build failure.
    """
    builder = COST_AGREEMENT_BUILDERS.get(agreement_type)
    if builder is None:
        return jsonify({"error": f"Unknown cost agreement type {agreement_type!r}"}), 404

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "Body must be a JSON object"}), 400

    validator = COST_AGREEMENT_VALIDATORS[agreement_type]
    verrs = validator(body)
    if verrs:
        log.info("cost agreement validation failed", extra={"agreement_type": agreement_type})
        return jsonify({
            "error": "Validation failed",
            "errors": [v.to_dict() for v in verrs],
        }), 400

    normaliser = COST_AGREEMENT_NORMALISERS.get(agreement_type)
    if normaliser is not None:
        body = normaliser(body)

    # Idempotence cache, reusing pdfform.cache.PdfCache. Namespaced by
    # agreement_type in the hashed payload (not just the raw body) so an
    # identical payload for two different agreement types, or a Form-956
    # payload that happens to hash the same, can never collide -- cache_key()
    # itself has no per-form/per-type salt (true for /forms/* too).
    key = cache_key({"_agreement_type": agreement_type, **body})
    if CACHE.has(key):
        log.info("cache hit", extra={"agreement_type": agreement_type, "cache_hit": True})
        resp = send_file(CACHE.path_of(key), mimetype="application/pdf", as_attachment=False)
        resp.headers["X-Cache-Key"] = key
        resp.headers["X-Cache"] = "hit"
        return resp

    # Cover schema construction + the cache write too, not just the build
    # itself -- any unhandled exception here would otherwise surface as
    # Flask's generic HTML 500 page, which a caller saving the response
    # straight to a .pdf file would see as "the PDF is corrupted" rather
    # than a diagnosable error.
    try:
        schema_fn = COST_AGREEMENT_SCHEMAS[agreement_type]
        data = schema_fn(body)
        pdf_bytes = builder(data)
        out_path = CACHE.put(key, pdf_bytes)
    except Exception:
        log.exception("cost agreement build failed", extra={"agreement_type": agreement_type})
        return jsonify({"error": "PDF generation failed"}), 500

    log.info("cache miss", extra={"agreement_type": agreement_type, "cache_hit": False})
    resp = send_file(out_path, mimetype="application/pdf", as_attachment=False)
    resp.headers["X-Cache-Key"] = key
    resp.headers["X-Cache"] = "miss"
    return resp


# --------------------------------------------------------------------- ops
@app.route("/health")
def health():
    """Liveness probe. No auth, ~1ms."""
    return jsonify({
        "ok": True,
        "forms_loaded": len(ENGINES),
        "forms": sorted(ENGINES.keys()),
        "cost_agreement_types": sorted(COST_AGREEMENT_BUILDERS.keys()),
        **CACHE.stats(),
    })


if __name__ == "__main__":
    # debug=False — auto-reload on file changes would wipe the in-memory
    # ENGINE / CACHE state on every edit, which is fine for a dev box
    # but noisy when the form HTML is open in a tab.
    app.run(host="127.0.0.1", port=5000, debug=False)
