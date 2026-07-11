# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Flask HTTP service that fills Australian Government Form 956 (and any other
AcroForm-based PDF) by writing directly to the PDF's real AcroForm widget
positions — no coordinate-based overlay drawing, no drift when the official
template changes. A TypeScript/React frontend (`form956Service.ts`) POSTs
case data here and renders the returned PDF blob.

## Commands

```bash
# install deps
pip install -r requirements.txt

# run dev server -> http://127.0.0.1:5000
python app.py

# run tests
pytest

# run a single test
pytest tests/test_form956_service.py::test_fill_returns_pdf_bytes

# production server (2 workers x 4 threads)
gunicorn -w 2 -k gthread --threads 4 -b 0.0.0.0:5000 app:app

# smoke test (end-to-end fill against a live server)
powershell -ExecutionPolicy Bypass -File fill_acroform.ps1

# docker
docker build -t form956-service .
docker run --rm -p 5000:5000 \
    -v $(pwd)/forms:/app/forms:ro \
    -v $(pwd)/pdfs:/app/pdfs:ro \
    -v form956-uploads:/app/uploads \
    form956-service
```

Tests isolate the on-disk cache into a `tmp_path` via the `UPLOADS_DIR` env
var (see the `_isolated_cache` autouse fixture) — the production
`uploads/cache/` is never touched by the suite.

## Architecture

**Config-driven, not code-driven.** Adding a new fillable PDF means dropping
a YAML file in `forms/<form_id>.yaml` — no Python changes required unless the
form needs domain-specific validation beyond shape checks. `app.py` loads
every `*.yaml` in `forms/` at startup via `pdfform.engine.load_all()` and
exposes each as `/forms/<id>`.

**Request pipeline** (`app.py` `form_fill`), each stage can short-circuit:
1. **Adapter** (`pdfform/adapt_form956.py`, optional per-form) — rewrites
   legacy/alternate client payload shapes (e.g. old React overlay-builder
   keys like `_client_family`) into the canonical schema before anything
   else touches the payload.
2. **Domain validator** (`pdfform/validate.py`, optional per-form,
   registered in `app.py`'s `VALIDATORS` dict) — enforces business rules the
   generic engine can't know about (MARN must be 7 digits, dates must be
   real, postcodes must be 4 digits). Returns `400` with
   `{field, code, message}` errors. Runs *before* the engine's shape check.
3. **Date normalisation** (`apply_normalisations`) — converts `YYYY-MM-DD` to
   `DD/MM/YYYY` so that both date formats hash to the same cache key.
4. **Engine shape check** (`FormEngine.validate`) — generic checks derived
   purely from the YAML (unknown app field, required-but-blank, radio value
   not in the on-state list).
5. **Idempotence cache** (`pdfform/cache.py`) — SHA-256 of the canonicalised
   payload (sorted keys, `None` values stripped). Cache hit returns the
   stored PDF immediately; miss falls through to the engine fill and is
   written to `uploads/cache/<key>.pdf`. Bounded to 1000 entries / 2 GB,
   oldest-by-mtime evicted first.
6. **Engine fill** (`pdfform/engine.py` `FormEngine.fill`) — opens the PDF
   once, writes every mapped widget, saves to the cache path. A missing
   widget or invalid radio value raises `ConfigError`, mapped to `422`
   (a PDF/schema mismatch, not a client input problem).

**The engine/schema/widgets split** (`pdfform/`):
- `schema.py` — plain dataclasses (`TextField`, `RadioField`, `GroupField`,
  `CheckAllField`) parsed from YAML. No Pydantic. `ConfigError` on any
  YAML shape problem.
- `engine.py` — `FormEngine` scans the PDF once at load time to build
  `widget_name -> page_index` and on-state maps, then fills/validates/
  extracts against that scan. Radio fields with no declared `options` in
  YAML get their on-state list backfilled from the live PDF (single
  checkbox tick-boxes like "I agree").
- `widgets.py` — the only code that touches PyMuPDF's raw `/AS` and `/V`
  xref keys directly. This form's checkboxes have *non-boolean* on-states
  (`mr`, `mrs`, `Application`, ...); going through `Widget.field_value`
  would normalise everything to `Yes`/`Off` and destroy the original
  export value, so ticking/unticking is done via `Document.xref_set_key`
  instead.

**Field types and their PDF fan-out**, all declared in `forms/<id>.yaml`:
- `text` — one app value -> one PDF text widget.
- `radio` — one app value -> tick the matching on-state widget, untick
  siblings sharing the same field name. Also handles boolean single-state
  checkboxes (`options` has exactly one entry).
- `group` — a list of dicts -> fans out to widgets named `pdf_base`,
  `pdf_base " " 2`, `pdf_base " " 3`, ... (index 0 maps to the bare name,
  index N>=1 maps to suffix N+1). Used for repeated rows like additional
  people.
- `check_all` — one bool -> ticks every widget matching `pdf` or
  `pdf " " <digit>` (e.g. the four declaration checkboxes on page 6).

**Cross-cutting concerns wired in `app.py` before routes register:**
- `pdfform/logging_setup.py` — JSON logs, one object per line, a
  12-char request ID bound to Flask `g` and echoed as `X-Request-Id` on
  every response (including error bodies, for support tickets). A
  `PIIFilter` masks emails and MARN-shaped 7-digit numbers in log lines
  and strips known PII keys (`agent_email`, `agent_marn`, names, DOBs)
  from any `extra={"payload": ...}` before it's emitted — route handlers
  should never log raw payloads regardless.
- CORS and rate limiting (`flask-cors`, `flask-limiter`) are optional
  imports — if absent, the service logs a warning and runs without them
  rather than failing to start. `CORS_ORIGINS` and `RATE_LIMIT` env vars
  control them when present; the CORS allow-list in `app.py` is currently
  hardcoded to specific origins (lovable.app subdomains, winzoylegal,
  localhost:8080) rather than reading `CORS_ORIGINS` — check `app.py`
  directly before assuming the env var controls it.

## Adding a new form

1. Drop the template PDF in `pdfs/<form_id>.pdf`.
2. Add `forms/<form_id>.yaml` (see `forms/form956.yaml` for the shape).
   Widget names to map `app` -> `pdf` from can be enumerated with PyMuPDF,
   e.g. `python -c "import pymupdf; [print(w.field_name) for p in pymupdf.open('pdfs/<form_id>.pdf') for w in p.widgets()]"`.
3. Optionally add `pdfform/validate_<form_id>.py` exposing
   `validate(payload, known_apps) -> list[ValidationError]` and register it
   in `app.py`'s `VALIDATORS` dict.
4. Restart the server — no other code changes needed; the form appears at
   `/forms/<form_id>` and the schema-driven renderer in `templates/form.html`
   picks it up automatically via `/forms/<form_id>/schema.json`.

Files starting with `_` in `forms/` are skipped by the loader (used by
`forms/_example.yaml` as a template, not a live form).

## Frontend integration

`form956Service.ts` is a standalone TypeScript client meant to be copied
into a separate React project's `src/services/`. It retries 5xx/network
errors up to 3 times with exponential backoff, maps `400` to
`Form956ValidationError` (with per-field `{field, code, message}`), other
4xx to `Form956ServerError`, and exhausted retries to `Form956NetworkError`.
See `INTEGRATION.md` for the full migration guide from the old overlay-based
`buildForm956Pdf` client-side builder.
