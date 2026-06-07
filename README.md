# Form 956 Service

A Flask HTTP service that fills Australian Government Form 956 (and
other AcroForm-based PDFs) by writing to the real PDF widget
positions. The TypeScript/React frontend in the user's
`Form956Generator.tsx` POSTs case data here and renders the returned
PDF blob in the preview pane.

> **Why this approach?** Drawing 'X' characters at hand-coded x/y
> coordinates drifts off the tick boxes whenever the official
> template changes. The engine here writes to the AcroForm widget
> directly, so the PDF viewer renders ticks at the widget's exact
> position — no drift, no overlay bugs.

---

## API contract

### `POST /forms/<form_id>/fill`

Fill a form. Returns the PDF bytes with cache headers.

**Request body** — JSON, all field app-names from `forms/<form_id>.yaml`.
Example for `form956`:

```json
{
  "agent_family_name": "Smith",
  "agent_given_names": "Jane",
  "agent_dob": "01/01/1980",
  "agent_marn": "1234567",
  "agent_email": "jane@example.com",
  "client_role": "visa",
  "application_type": "Skilled visa"
}
```

**Response on success** (`200 OK`, `Content-Type: application/pdf`):

- Body: the filled PDF bytes
- `X-Cache-Key`: 32-char hex hash of the canonical payload
- `X-Cache`: `hit` (reused) or `miss` (fresh fill)
- `X-Request-Id`: 12-char UUID, also echoed in the JSON error body

**Response on validation error** (`400 Bad Request`):

```json
{
  "error": "Validation failed",
  "errors": [
    { "field": "agent_marn", "code": "format",
      "message": "MARN must be exactly 7 digits" }
  ]
}
```

**Response on engine error** (`422 Unprocessable Entity`): a
`ConfigError` from the engine (e.g. radio value not in on-state list).
Same `{error, errors}` shape.

### `GET /forms/<form_id>/fill?key=<key>`

Re-fetch a previously filled PDF by its cache key. `404` if the key is
unknown (caller should refill).

### `GET /forms/<form_id>/extract?key=<key>`

Read the live widget values back from a previously filled PDF.
Returns a `{ "field_name": "value", ... }` map. Used to verify ticks
landed in the right place.

### `GET /health`

Liveness probe. No auth. ~1 ms.

```json
{ "ok": true, "forms_loaded": 1, "forms": ["form956"],
  "cache_size": 42, "cache_bytes": 18823140 }
```

### `GET /forms/<form_id>/schema.json`

Returns the engine's field schema. The form renderer in
`templates/form.html` fetches this to build the dynamic input grid.

---

## Local development

```bash
# 1. install deps
pip install -r requirements.txt

# 2. run the dev server
python app.py
# → http://127.0.0.1:5000

# 3. smoke test
powershell -ExecutionPolicy Bypass -File fill_acroform.ps1
```

The dev server uses Flask's built-in WSGI (single-threaded). For
realistic load, run gunicorn:

```bash
gunicorn -w 2 -k gthread --threads 4 -b 0.0.0.0:5000 app:app
```

---

## Docker

```bash
docker build -t form956-service .
docker run --rm -p 5000:5000 \
    -v $(pwd)/forms:/app/forms:ro \
    -v $(pwd)/pdfs:/app/pdfs:ro \
    -v form956-uploads:/app/uploads \
    form956-service
```

Mount `forms/` and `pdfs/` read-only so template updates don't
require a rebuild. The `uploads/` volume persists the cache across
container restarts.

---

## Adding a new form

1. Drop the template PDF in `pdfs/<form_id>.pdf`.
2. Add `forms/<form_id>.yaml` describing the field map. See
   `forms/form956.yaml` for the schema.
3. (Optional) Add a validator in `pdfform/validate_<form_id>.py`
   exposing a `validate(payload, known_apps) -> list[ValidationError]`
   function, and register it in `app.py`:

   ```python
   from pdfform.validate_form1000 import validate_form1000
   VALIDATORS["form1000"] = validate_form1000
   ```

4. Restart the server. The new form is now at
   `http://localhost:5000/forms/form1000` and the engine picks it up
   automatically.

---

## Idempotence cache

Each filled PDF is cached on disk at
`uploads/cache/<sha256>.pdf` keyed by a canonical hash of the
payload. Same payload → same PDF, instantly. The cache is bounded
at 1000 entries / 2 GB, oldest-first eviction.

To invalidate the cache: `rm -rf uploads/cache/`.

---

## Frontend integration

`form956Service.ts` is a drop-in TypeScript client for the React app.
Import and use:

```ts
import { form956Service } from './services/form956Service';

const { blob, cacheKey, cacheHit } =
  await form956Service.generatePdf(payload);
const bytes = new Uint8Array(await blob.arrayBuffer());
// pass bytes to the existing PDF preview component
```

The client retries 5xx up to 3 times with exponential backoff, maps
400 to `Form956ValidationError`, and surfaces network failures as
`Form956NetworkError` after exhausting retries.

---

## File map

| File                              | Role                              |
|-----------------------------------|-----------------------------------|
| `app.py`                          | Flask app, routes, wiring         |
| `pdfform/engine.py`               | AcroForm fill + extract           |
| `pdfform/schema.py`               | YAML config loader                |
| `pdfform/widgets.py`              | pymupdf widget helpers            |
| `pdfform/validate.py`             | per-form-956 domain validator     |
| `pdfform/cache.py`                | SHA-256 idempotence cache         |
| `pdfform/logging_setup.py`        | JSON logs + PII mask              |
| `forms/form956.yaml`              | Form 956 field map                |
| `templates/form.html`             | Dynamic form renderer             |
| `form956Service.ts`               | TypeScript client for React       |
| `fill_acroform.ps1`               | end-to-end smoke test             |
| `Dockerfile` + `requirements.txt` | production container              |

---

## Environment variables

| Variable          | Default              | Description                          |
|-------------------|----------------------|--------------------------------------|
| `LOG_LEVEL`       | `INFO`               | Python logging level                 |
| `CORS_ORIGINS`    | `*`                  | Allowed origins for CORS             |
| `RATE_LIMIT`      | `60/minute`          | Per-IP rate limit on `/fill`         |
| `GUNICORN_CMD_ARGS` | unset              | Extra gunicorn flags (Docker)        |
