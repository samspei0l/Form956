# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Flask HTTP service with **two structurally different PDF capabilities**
behind one app:

1. **`/forms/*` — AcroForm filling** (`pdfform/`). Fills Australian
   Government Form 956 (and any other AcroForm PDF) by writing directly to
   the PDF's real AcroForm widget positions — no coordinate-based overlay
   drawing, no drift when the official template changes.
2. **`/cost-agreements/*` — document generation** (`costagreements/`).
   Builds 13 kinds of Winzoy Legal cost-agreement PDF *plus* the two
   finance documents (`invoice`, `receipt`) *from scratch* with ReportLab
   Platypus flowables. Nothing is being filled in; there is no template
   PDF.

They share only `app.py`, `pdfform.cache`, `pdfform.validate`'s date
helpers/`ValidationError`, and the JSON-logging setup. Keep them separate:
a change to the form engine should not need to touch the builders, and
vice versa.

A TypeScript/React frontend (`form956Service.ts`) POSTs case data to the
`/forms/*` side and renders the returned PDF blob.

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
pytest tests/test_cost_agreements.py::test_sigmeta_boxes_match_actual_rendered_signature_positions

# just one of the two subsystems
pytest tests/test_cost_agreements.py
pytest tests/test_finance_documents.py   # invoice + receipt

# production server (2 workers x 4 threads)
gunicorn -w 2 -k gthread --threads 4 -b 0.0.0.0:5000 app:app

# smoke test (end-to-end fill against a live server)
powershell -ExecutionPolicy Bypass -File fill_acroform.ps1

# enumerate a PDF's AcroForm widget names when writing a new forms/*.yaml
python -c "import pymupdf; [print(w.field_name) for p in pymupdf.open('pdfs/<file>.pdf') for w in p.widgets()]"

# docker
docker build -t form956-service .
docker run --rm -p 5000:5000 \
    -v $(pwd)/forms:/app/forms:ro \
    -v $(pwd)/pdfs:/app/pdfs:ro \
    -v form956-uploads:/app/uploads \
    form956-service
```

Both suites isolate the on-disk cache into a `tmp_path` via the
`UPLOADS_DIR` env var, but by **different mechanisms**, and the difference
matters when adding tests:

- `tests/test_form956_service.py` uses an autouse function-scoped
  `monkeypatch.setenv` fixture (`_isolated_cache`).
- `tests/test_cost_agreements.py` sets `os.environ["UPLOADS_DIR"]`
  directly inside its *module-scoped* `app` fixture, before importing/
  reloading `app.py`. A function-scoped monkeypatch fixture would resolve
  *after* the module-scoped one, letting `app.py` import with `UPLOADS_DIR`
  unset and silently point `CACHE` at the real repo `uploads/cache/`.

Either way the production `uploads/cache/` is never touched by the suite.

## Architecture — `/forms/*` (AcroForm filling)

**Config-driven, not code-driven.** Adding a new fillable PDF means dropping
a YAML file in `forms/<form_id>.yaml` — no Python changes required unless the
form needs domain-specific validation beyond shape checks. `app.py` loads
every `*.yaml` in `forms/` at startup via `pdfform.engine.load_all()` and
exposes each as `/forms/<id>`.

**Request pipeline** (`app.py` `form_fill`), each stage can short-circuit:
1. **Adapter** (`pdfform/adapt_form956.py`, optional per-form, registered in
   `ADAPTERS`) — rewrites legacy/alternate client payload shapes (e.g. old
   React overlay-builder keys like `_client_family`) into the canonical
   schema before anything else touches the payload.
2. **Domain validator** (`pdfform/validate.py`, optional per-form,
   registered in `app.py`'s `VALIDATORS` dict) — enforces business rules the
   generic engine can't know about (MARN must be 7 digits, dates must be
   real, postcodes must be 4 digits for AU). Returns `400` with
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

## Architecture — `/cost-agreements/*` (document generation)

Ported from a separate React/pdf-lib project (`winzoylegal_new`); the
builder docstrings cite the exact `.ts` file each was transcribed from, and
clause text is **verbatim** from those sources. Treat wording changes as
legal-content changes, not refactors.

**Why ReportLab Platypus and not coordinate drawing** (`costagreements/layout.py`
carries the full rationale): the pdf-lib original had every builder
hand-compute how much vertical space a block needed and compare that guess
to remaining page space. Wrong guesses collided body content with the
signature/initials/date band. Here content is built as flowables
(`Table`/`Paragraph`/`KeepTogether`) inside a single `Frame`, and ReportLab
measures and paginates. **Do not reintroduce hand-computed y-coordinates
for content**; only the per-page chrome draws at absolute positions.

**Module layout** (`costagreements/`):
- `layout.py` — single source of truth for page geometry, the brand
  palette, fonts, firm details, and every `ParagraphStyle`. Import from
  here rather than redefining constants in a builder.
- `components.py` — the shared flowables (`parties_table`,
  `works_fee_table`, `disbursement_table`, `cost_summary_table`,
  `bank_details_box`, `signature_block`, `checkbox_row`, ...). Also the
  `esc()`/`P()`/`PT()` helpers: user-supplied strings flow into ReportLab's
  Paragraph mini-markup, so **escape anything client-supplied** before
  interpolating it, or a stray `<`/`&` is misread as markup.
  `signature_block()` wraps the whole initials/signature/date block in
  `KeepTogether` — that is the structural fix for the spacing bug class.
- `annexures.py` — Annexures A–D as flowable lists.
- `chrome.py` — watermark, header, footer. The footer needs "Page X of Y",
  unknown until layout completes, so it uses the two-pass `NumberedCanvas`
  recipe (buffer each page in `showPage`, replay in `save`). Header and
  watermark draw in the `onPage` callback.
- `money.py` — `parse_amt`/`fmt_amt`/`sum_amounts` and the 1.4%
  `VAC_SURCHARGE_RATE` DoHA card surcharge.
- `sigmeta.py` — see below.
- `schema.py` + `validate.py` — the *General* agreement's dataclass and
  validator. Every other type keeps its own alongside its builder.
- `finance.py` + `validate_finance.py` — the card-family flowables
  (`panel_row`, `line_items_table`, `totals_card`, ...) and shared
  validator behind the two finance documents. See below.
- `builders/*.py` — one module per agreement type (~400–1200 lines each),
  plus `invoice.py` / `receipt.py`.

**The finance documents (`invoice`, `receipt`)** are not cost agreements,
but they are built by the same engine, wear the same brand chrome, and
export the same four-part builder contract, so they register in the same
`app.py` dicts and ride the same route (`/cost-agreements/invoice/fill`).
Ported from winzoylegal_new's `features/invoice/buildInvoicePdf.ts` and
`features/receipt/buildReceiptPdf.ts`. Three things about them differ from
every agreement type, all deliberate:

- **They are never signed.** No SIGMETA metadata, and no reserved
  post-signing stamp band — their frame is `layout.DOC_FRAME_Y` /
  `DOC_FRAME_HEIGHT`, which runs down to just above the footer. Don't add
  them to `tests/test_cost_agreements.py`'s parametrised lists; they have
  their own suite in `tests/test_finance_documents.py`.
- **The header's "Issued:" line is the document's own issue date**, not a
  render timestamp — `chrome.draw_header` prints `generated_at` verbatim,
  and these builders pass `data.issue_date`. Previously (in the pdf-lib
  original) regenerating an invoice months later printed today's date at
  the top of a page whose Issue Date field still showed the real one.
- **The Doc ID is deterministic** (`finance.stable_doc_id`, seeded only
  from the document's identifying fields — no clock). A numbered financial
  record must reprint the same Doc ID every time its PDF is rebuilt, or the
  footer's "Verify:" stamp means nothing.

`chrome.draw_header`'s `badge_label` / `badge_every_page` keywords exist for
these two: the navy badge reads "TAX INVOICE" / "PAYMENT RECEIPT" on every
page instead of "COST AGREEMENT — <service>" on the cover only.

An invoice sent with a zero `gst_rate_percent` and `gst_amount` (the finance
UI's "No GST" mode) is *not* a tax invoice: it is badged, titled and
PDF-named "INVOICE" and its ledger drops the GST row entirely rather than
printing "GST (0%)  $0.00". `InvoiceData.has_gst` / `.badge_label` decide.

**SIGMETA — how remote signing interoperates** (`sigmeta.py`): the
downstream project owns the client-signing workflow (Supabase
`document_signatures` + an `on-document-signed` edge function). That
function finds where to stamp a signature image by parsing a
`SIGMETA:{...}` JSON blob out of the PDF's `/Subject`. ReportLab only knows
where a flowable actually landed *during* `doc.build()`, so `MeasuredBox`
wraps a flowable and reports its true absolute `(page, x, y, w, h)` at draw
time; `SigMetaState` re-serialises and re-pushes the full accumulated JSON
on every measurement, because there is no "layout finished" hook and the
Info dict is finalized at `canvas.save()` with whatever `setSubject` was
called with last. `parse_sigmeta()` is the inverse, for tests.

**Route pipeline** (`app.py` `cost_agreement_fill`): builder lookup (404 on
unknown type) -> validator (400 with `{field, code, message}`) ->
normaliser -> cache -> `from_payload` + build + cache write, all inside one
`try` that returns a JSON 500. That `try` deliberately covers schema
construction and the cache write, not just the build: an unhandled
exception would otherwise render Flask's HTML 500 page, which a caller
saving the response straight to `.pdf` sees as "corrupted PDF" rather than
a diagnosable error.

The cache key is salted with the agreement type
(`cache_key({"_agreement_type": ..., **body})`) because `cache_key()` has no
per-form salt of its own — without it, identical payloads across two types
(or a Form 956 payload) could collide.

Generation is **synchronous and final**: pass `client_signature_data` /
`rep_signature_data` as base64 image data URIs up front, or the signature
boxes render empty for print-and-sign. (Not applicable to `invoice` /
`receipt` — those are never signed.)

## Adding a new form (`/forms/*`)

1. Drop the template PDF in `pdfs/` (the YAML's `pdf:` key names the path
   relative to the project root; the filename need not match the form id —
   `form956.yaml` points at `pdfs/956.pdf`).
2. Add `forms/<form_id>.yaml` — copy `forms/_example.yaml`, which documents
   every field type inline. Enumerate widget names with the PyMuPDF
   one-liner under Commands.
3. Optionally add `pdfform/validate_<form_id>.py` exposing
   `validate(payload, known_apps) -> list[ValidationError]` and register it
   in `app.py`'s `VALIDATORS` dict.
4. Restart the server — no other code changes needed; the form appears at
   `/forms/<form_id>` and the schema-driven renderer in `templates/form.html`
   picks it up automatically via `/forms/<form_id>/schema.json`.

Files starting with `_` in `forms/` are skipped by the loader (used by
`forms/_example.yaml` as a template, not a live form).

## Adding a new cost agreement type

Each builder module exports a consistent **four-part contract**, and each
part registers in its own dict in `app.py`. Missing one registration is the
easy mistake — the type will 404, or silently skip validation/normalisation.

1. Create `costagreements/builders/<type>.py` exporting:
   - `<Type>CostAgreementData` with a `from_payload(dict)` classmethod
   - `validate_<type>_cost_agreement(payload) -> list[ValidationError]`
   - `apply_<type>_normalisations(payload) -> dict`
   - `build_<type>_cost_agreement(data) -> bytes`
2. Register all four in `COST_AGREEMENT_BUILDERS`, `COST_AGREEMENT_SCHEMAS`,
   `COST_AGREEMENT_VALIDATORS`, and `COST_AGREEMENT_NORMALISERS` in `app.py`.
3. Build the story from `components.py`/`annexures.py` flowables and
   `layout.py` styles. `builders/general.py` is the canonical reference.
4. Add the type to the parametrised lists in `tests/test_cost_agreements.py`
   — they already assert, for every registered type, that a minimal payload
   validates, builds a valid PDF, and returns 200 from the route.

`/health` lists every registered type under `cost_agreement_types` —
including `invoice` and `receipt`, which follow the same four-part contract
(`InvoiceData.from_payload` / `validate_invoice` / `apply_invoice_
normalisations` / `build_invoice`) but build from `finance.py` flowables
rather than `components.py` ones.

## Cross-cutting concerns (wired in `app.py` before routes register)

- `pdfform/logging_setup.py` — JSON logs, one object per line, a
  12-char request ID bound to Flask `g` and echoed as `X-Request-Id` on
  every response (including error bodies, for support tickets). A
  `PIIFilter` masks emails and MARN-shaped 7-digit numbers in log lines
  and strips known PII keys (`agent_email`, `agent_marn`, names, DOBs)
  from any `extra={"payload": ...}` before it's emitted — route handlers
  should never log raw payloads regardless.
- CORS and rate limiting (`flask-cors`, `flask-limiter`) are optional
  imports — if absent, the service logs a warning and runs without them
  rather than failing to start. `RATE_LIMIT` (default `60/minute`, per IP,
  on both `/fill` routes) does control the limiter.
- **The CORS allow-list is hardcoded in `app.py`, not read from
  `CORS_ORIGINS`** despite what README's env-var table implies: a
  `*.lovable.app` regex, `https://pipeline.winzoylegal.com.au`, and
  `http://localhost:8080`. Its `resources` dict must list **both**
  `/forms/*` and `/cost-agreements/*`. Omitting a route there fails
  quietly — the route still returns 200 with a real PDF body, but
  flask-cors omits `Access-Control-Allow-Origin`, so the browser blocks
  the caller from reading it and it looks like a network failure
  client-side, not a CORS one.

## Frontend integration

`form956Service.ts` is a standalone TypeScript client meant to be copied
into a separate React project's `src/services/`. It retries 5xx/network
errors up to 3 times with exponential backoff, maps `400` to
`Form956ValidationError` (with per-field `{field, code, message}`), other
4xx to `Form956ServerError`, and exhausted retries to `Form956NetworkError`.
See `INTEGRATION.md` for the full migration guide from the old overlay-based
`buildForm956Pdf` client-side builder. It covers the `/forms/*` side only;
cost agreements are called directly.
