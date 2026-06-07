# Integrating the Form 956 service into your React app

A step-by-step guide to replace the buggy `buildForm956Pdf(payload)` overlay
PDF builder with a call to the Python Flask service.

The Python service writes to the real AcroForm widgets in the official
Form 956 template, so ticks land in the exact right place — no more
coordinate drift.

---

## Overview of the architecture

```
┌─────────────────────┐    POST /forms/form956/fill    ┌──────────────────────┐
│   React app         │ ──────────────────────────────▶│  Flask service        │
│   (Form956Generator │  JSON body, full case data     │  (this repo)          │
│   .tsx)             │ ◀──────────────────────────────│                       │
│                     │  application/pdf bytes         │  pymupdf AcroForm     │
│  iframe blob:url    │  + X-Cache-Key + X-Cache        │  engine, no overlay   │
└─────────────────────┘                                └──────────────────────┘
```

The service is stateless from React's perspective: it can be hit
directly in dev (CORS allows `*` by default) and behind a reverse proxy
in production (nginx, Vercel rewrites, etc.).

---

## 1. Get the service running locally

### Option A — Python directly (simplest for dev)

```bash
cd "path/to/Pdf_readerForm950"
pip install -r requirements.txt
python app.py
# → http://127.0.0.1:5000
```

### Option B — Docker (production-like)

```bash
cd "path/to/Pdf_readerForm950"
docker build -t form956-service .
docker run --rm -p 5000:5000 \
    -v "$(pwd)/forms:/app/forms:ro" \
    -v "$(pwd)/pdfs:/app/pdfs:ro" \
    -v form956-uploads:/app/uploads \
    form956-service
```

Confirm it's up:
```bash
curl http://127.0.0.1:5000/health
# {"ok": true, "forms_loaded": 1, "forms": ["form956"],
#  "cache_size": 0, "cache_bytes": 0}
```

---

## 2. Add the service file to your React project

Copy `form956Service.ts` from this repo into your React project:

```
your-react-app/
└── src/
    └── services/
        ├── ... (your existing services)
        └── form956Service.ts   ← copy here
```

The file exports three things you'll use:
- `form956Service` — the singleton (already wired to read
  `VITE_FORM_API_URL` from your `.env`)
- `Form956ValidationError` — thrown on 400
- `Form956NetworkError` — thrown after retries are exhausted

### Point it at the right URL

In your React app's `.env` (or `.env.local`):

```bash
# dev
VITE_FORM_API_URL=http://127.0.0.1:5000

# production (nginx/Vercel reverse proxy in front of the service)
VITE_FORM_API_URL=https://api.your-domain.com
```

> If you don't set `VITE_FORM_API_URL`, the service defaults to
> `http://127.0.0.1:5000` — fine for dev, override before deploying.

---

## 3. Replace the buggy PDF builder call

In `Form956Generator.tsx` you'll find a function that calls
`buildForm956Pdf(payload)` and gets back a `Uint8Array` for the preview.
The fix is a 3-line swap.

### Before (the buggy overlay path)

```tsx
import { buildForm956Pdf } from "@/features/form956/pdf/form956Template";

// ...inside your component or a handler:
const bytes = await buildForm956Pdf(payload);
const blob = new Blob([bytes], { type: "application/pdf" });
const url = URL.createObjectURL(blob);
iframeRef.current.src = url;
```

### After (the AcroForm service)

```tsx
import { form956Service, Form956ValidationError } from "@/services/form956Service";

// ...inside the same handler:
const { blob } = await form956Service.generatePdf(payload);
const url = URL.createObjectURL(blob);
iframeRef.current.src = url;
```

The service handles retries, timeout, and PII-safe logging on the
server side. You get the same `Blob` you were building by hand, but
the PDF inside it has ticks in the right place.

### Optional: show a per-field validation error inline

The new service throws `Form956ValidationError` on 400 with a list of
`{field, code, message}` objects. If you have a status banner in the
generator, wire it up:

```tsx
import {
  form956Service,
  Form956ValidationError,
  Form956NetworkError,
  Form956ServerError,
} from "@/services/form956Service";

async function handleGenerate(payload: Form956Payload) {
  setStatus("Generating…");
  try {
    const { blob } = await form956Service.generatePdf(payload);
    const url = URL.createObjectURL(blob);
    iframeRef.current.src = url;
    setStatus("Done.");
  } catch (e) {
    if (e instanceof Form956ValidationError) {
      // Show the first error inline; or render all of them.
      const first = e.errors[0];
      setStatus(`${first.field}: ${first.message}`, "error");
      // If your form has a per-field error map, you can loop:
      //   setFieldErrors(Object.fromEntries(e.errors.map(x => [x.field, x.message])));
    } else if (e instanceof Form956NetworkError) {
      setStatus("Service unavailable — is the PDF server running?", "error");
    } else if (e instanceof Form956ServerError) {
      setStatus(`Server error (${e.status}): ${e.message}`, "error");
    } else {
      setStatus("Unexpected error", "error");
    }
  }
}
```

---

## 4. Wire a "Download PDF" button

The same blob the preview uses can be saved to disk:

```tsx
function downloadPdf(blob: Blob, applicantFamilyName: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `Form_956_${applicantFamilyName || "draft"}_${new Date()
    .toISOString()
    .slice(0, 10)}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke after a short delay so the browser has time to start the download.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
```

---

## 5. (Optional) Keep a per-case cache key

The service returns a `cacheKey` on every successful fill. Stash it in
your case record so subsequent re-opens of the same case can re-fetch
the existing PDF instead of re-filling:

```tsx
const { blob, cacheKey } = await form956Service.generatePdf(payload);

// store on the case in your DB
await supabase
  .from("cases")
  .update({ form956_cache_key: cacheKey })
  .eq("id", caseId);
```

Later, to re-open:

```tsx
const { data: c } = await supabase
  .from("cases")
  .select("form956_cache_key")
  .eq("id", caseId)
  .single();

if (c?.form956_cache_key) {
  const blob = await form956Service.fetchPdfByKey(c.form956_cache_key);
  if (blob) {
    // render
    return;
  }
  // Cache miss (server restarted and lost the cache, or eviction) — refill.
}
```

---

## 6. Remove the dead TypeScript PDF builder

Once the integration is working, delete:

```
src/features/form956/pdf/form956Template.ts   ← the overlay-tick buggy code
```

and remove its import from `Form956Generator.tsx`. Drop the dependency
on `pdf-lib` / `jspdf` / whatever else that file pulled in, if they're
no longer used elsewhere in the app.

---

## 7. Deploy the service to production

You have a few options. Pick whichever matches your infra.

### 7a. Standalone VM (simplest)

The Dockerfile in this repo produces a production image. Push it to a
registry and run it on a small VM behind nginx:

```nginx
# /etc/nginx/sites-available/form-api.conf
server {
  listen 443 ssl http2;
  server_name api.your-domain.com;

  ssl_certificate     /etc/letsencrypt/live/api.your-domain.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/api.your-domain.com/privkey.pem;

  client_max_body_size 10m;
  proxy_read_timeout 60s;

  location / {
    proxy_pass         http://127.0.0.1:5000;
    proxy_set_header   Host              $host;
    proxy_set_header   X-Real-IP         $remote_addr;
    proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header   X-Forwarded-Proto $scheme;
  }
}
```

Then point `VITE_FORM_API_URL=https://api.your-domain.com` in your
React app's prod `.env`.

### 7b. Render / Railway / Fly.io (PaaS)

1. Push this repo to GitHub.
2. New Web Service → connect repo → choose **Docker** as the runtime.
3. Set port `5000`.
4. Add a persistent disk mounted at `/app/uploads` (this is the
   idempotence cache; without it, the cache resets on every deploy).
5. Health check path: `/health`.

### 7c. AWS / GCP / Azure

Use the same Dockerfile. Mount a small persistent volume at
`/app/uploads` (5–10 GB is plenty for the bounded LRU). The service
runs comfortably in 512 MB RAM.

---

## 8. CORS for production

For local dev the service accepts any origin (`CORS_ORIGINS=*` default).
For production, set the env var to your React app's origin only:

```bash
CORS_ORIGINS=https://your-react-app.com
```

---

## 9. Quick checklist

- [ ] `python app.py` runs locally and `curl /health` returns 200
- [ ] `form956Service.ts` is in `src/services/`
- [ ] `VITE_FORM_API_URL` is set in `.env.local`
- [ ] The `buildForm956Pdf` call site is swapped for `form956Service.generatePdf`
- [ ] Validation errors show inline in the form
- [ ] The legacy `form956Template.ts` is deleted
- [ ] Service is deployed, `CORS_ORIGINS` is set, persistent disk is mounted
- [ ] React app's prod `.env` points at the deployed service URL
- [ ] Generate a real form in prod, verify the ticks land in the right place

---

## Troubleshooting

**`fetch` fails with CORS error in dev.** Set
`CORS_ORIGINS=http://localhost:5173` (or whatever your Vite dev port is)
and restart the service. Vite's default is 5173.

**`Form956NetworkError` after 3 retries.** The service is not
reachable from your React app. Check `VITE_FORM_API_URL`, check the
service is running (`curl /health`), and check the browser's network
tab for the failing request.

**`Form956ValidationError` with a `shape` code.** The payload has a
field name that the engine's YAML doesn't know about, or a radio value
not in the on-state list. The error message will say which one — fix
the payload key in the React form, the schema is correct.

**Ticks are still off.** You're still on the old code path. Check the
React app is actually calling `form956Service.generatePdf` and not
`buildForm956Pdf`. Open DevTools → Network, look for the POST to
`/forms/form956/fill` — if you see it, the new path is wired; if not,
the old path is still in use.

**Service uses too much disk.** The cache is bounded at 1000 entries
/ 2 GB and evicts oldest-first. To force-clear:
`docker exec <container> rm -rf /app/uploads/cache/*.pdf`.
