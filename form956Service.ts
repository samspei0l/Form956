/**
 * Form 956 API client.
 *
 * Drop this into the React app's `src/services/` and replace the 5-line
 * call site in `Form956Generator.tsx`:
 *
 *   // before
 *   const bytes = await buildForm956Pdf(payload);
 *
 *   // after
 *   const { blob, cacheKey } = await form956Service.generatePdf(payload);
 *   const bytes = new Uint8Array(await blob.arrayBuffer());
 *   lastCacheKey = cacheKey;
 *
 * The PDF bytes produced by the Python engine are written to the real
 * AcroForm widgets in the official Form 956 template, so the preview
 * pane renders ticks at the exact widget position — no coordinate
 * drift, no overlay bugs.
 *
 * The service is retry-aware: 5xx and network errors trigger up to 3
 * attempts with exponential backoff (200ms, 400ms, 800ms). 4xx errors
 * are surfaced immediately as `Form956ValidationError` (400) or
 * `Form956ServerError` (any other 4xx) — the server already told us
 * what's wrong, retrying won't help.
 */

// ----- error types -------------------------------------------------------
export interface ValidationErrorDetail {
  field: string;
  code: 'required' | 'format' | 'unknown' | 'value' | 'engine' | 'shape';
  message: string;
}

export class Form956Error extends Error {
  constructor(message: string, public readonly cause?: unknown) {
    super(message);
    this.name = new.target.name;
  }
}

export class Form956ValidationError extends Form956Error {
  constructor(
    message: string,
    public readonly errors: ValidationErrorDetail[],
  ) {
    super(message);
  }
}

export class Form956NetworkError extends Form956Error {}

export class Form956ServerError extends Form956Error {
  constructor(message: string, public readonly status: number) {
    super(message);
  }
}

// ----- payload typing ----------------------------------------------------
// Matches the engine's expected app-field names. See forms/form956.yaml
// for the canonical list. The fields are commented here so an IDE can
// hint them; the server is the source of truth on validation.
export interface Form956Payload {
  // Part A — agent identity
  agent_title?: 'mr' | 'mrs' | 'miss' | 'ms' | string;
  agent_title_other?: string;
  is_new_application?: 'Yes' | 'No';
  preferred_communication?: 'Yes' | 'No';
  assistance_type?: 'Legal' | 'reg' | 'exampt' | 'visa' | 'sponsor' | string;
  another_migration_agent?: 'Yes' | 'No';
  exemption_reason?: string;
  agent_family_name: string;
  agent_given_names: string;
  agent_dob: string;            // DD/MM/YYYY or YYYY-MM-DD
  agent_org_name?: string;
  agent_resadd_str?: string;
  agent_resadd_sub?: string;
  agent_resadd_cntry?: string;
  agent_resadd_pc?: string;     // 4 digits
  agent_postal_str?: string;
  agent_postal_sub?: string;
  agent_postal_cntry?: string;
  agent_postal_pc?: string;     // 4 digits
  agent_off_ph_cc?: string;
  agent_off_ph_ac?: string;
  agent_off_ph?: string;
  agent_mob?: string;
  agent_email: string;          // required
  agent_marn: string;           // 7 digits, required
  agent_lpn?: string;

  // Part B — client
  client_role: 'visa' | 'sponsor' | 'nominator' | string;  // required
  assistance_category?: 'Application' | 'Cancellation' | 'Specific' | string;
  not_yet_decided?: boolean;
  also_assisting_another?: 'Yes' | 'No';
  client_dob?: string;
  client_org_name?: string;
  client_resadd_str?: string;
  client_resadd_sub?: string;
  client_resadd_cntry?: string;
  client_resadd_pc?: string;
  client_off_ph_cc?: string;
  client_off_ph_ac?: string;
  client_off_ph?: string;
  client_mob?: string;
  client_email?: string;
  client_diac_id?: string;

  // Part C — application
  application_type: string;     // required
  date_lodged?: string;         // DD/MM/YYYY

  // Repeating group (people assisted)
  people?: Array<{ family: string; given: string }>;

  // End-of-appointment questions
  also_assisting_in_ending?: 'Yes' | 'No';
  ending_this_appointment?: 'Yes' | 'No';
  communicated_ending?: 'Yes' | 'No';

  // Declarations
  agent_declarations_agreed?: boolean;
  client_declarations_agreed?: boolean;
  agent_declaration_date?: string;
  client_declaration_date?: string;

  // Catch-all for fields the engine knows about that we haven't typed here.
  [k: string]: unknown;
}

export interface GeneratePdfResult {
  blob: Blob;
  cacheKey: string | null;
  cacheHit: boolean;
  requestId: string | null;
}

export interface Form956ServiceOptions {
  /** Request timeout in ms. Default 15000. */
  timeoutMs?: number;
  /** Max retry attempts for 5xx and network errors. Default 3. */
  maxRetries?: number;
  /** Base delay for exponential backoff in ms. Default 200. */
  retryBaseMs?: number;
  /** Optional fetch impl (for tests). Defaults to globalThis.fetch. */
  fetchImpl?: typeof fetch;
  /** Optional X-Request-Id to forward (for tracing). */
  requestId?: string;
}

// ----- client ------------------------------------------------------------
export class Form956Service {
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly retryBaseMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(baseUrl: string, opts: Form956ServiceOptions = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.timeoutMs = opts.timeoutMs ?? 15_000;
    this.maxRetries = opts.maxRetries ?? 3;
    this.retryBaseMs = opts.retryBaseMs ?? 200;
    this.fetchImpl = opts.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  /**
   * POST the payload to /forms/form956/fill and return the PDF blob.
   *
   * Throws:
   *   - Form956ValidationError on 400 (server returned field errors)
   *   - Form956ServerError       on other 4xx / 5xx
   *   - Form956NetworkError      on timeout or transport failure
   *     after exhausting all retries.
   */
  async generatePdf(payload: Form956Payload): Promise<GeneratePdfResult> {
    const url = `${this.baseUrl}/forms/form956/fill`;
    const body = JSON.stringify(payload);

    let lastErr: unknown = null;
    for (let attempt = 0; attempt < this.maxRetries; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.timeoutMs);
      try {
        const res = await this.fetchImpl(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(this._requestId ? { 'X-Request-Id': this._requestId } : {}),
          },
          body,
          signal: controller.signal,
        });
        clearTimeout(timer);

        // 200 → PDF bytes.
        if (res.ok) {
          const blob = await res.blob();
          return {
            blob,
            cacheKey: res.headers.get('X-Cache-Key'),
            cacheHit: res.headers.get('X-Cache') === 'hit',
            requestId: res.headers.get('X-Request-Id'),
          };
        }

        // 400 → validation. Don't retry — server told us it's wrong.
        if (res.status === 400) {
          const data = await res.json().catch(() => ({}));
          const errors = (data.errors ?? []) as ValidationErrorDetail[];
          throw new Form956ValidationError(
            data.error ?? 'Validation failed',
            errors,
          );
        }

        // 5xx + 408 + 429 → retry with backoff.
        if (this._shouldRetry(res.status) && attempt + 1 < this.maxRetries) {
          await this._sleep(this._backoffMs(attempt));
          continue;
        }

        // 4xx (other) or final 5xx → server error.
        let msg = `Form 956 fill failed (HTTP ${res.status})`;
        try {
          const data = await res.json();
          if (data?.error) msg = data.error;
        } catch {
          // ignore — keep generic message
        }
        throw new Form956ServerError(msg, res.status);
      } catch (e) {
        clearTimeout(timer);
        if (
          e instanceof Form956ValidationError ||
          e instanceof Form956ServerError
        ) {
          throw e;
        }
        // AbortError or network failure.
        lastErr = e;
        if (attempt + 1 < this.maxRetries) {
          await this._sleep(this._backoffMs(attempt));
          continue;
        }
      }
    }
    throw new Form956NetworkError(
      `Form 956 fill failed after ${this.maxRetries} attempts: ` +
        (lastErr instanceof Error ? lastErr.message : String(lastErr)),
      lastErr,
    );
  }

  /**
   * Re-fetch a previously generated PDF by its cache key.
   * Returns null if the key is unknown (caller should refill).
   */
  async fetchPdfByKey(cacheKey: string): Promise<Blob | null> {
    const url = `${this.baseUrl}/forms/form956/fill?key=${encodeURIComponent(cacheKey)}`;
    const res = await this.fetchImpl(url, { method: 'GET' });
    if (res.status === 404) return null;
    if (!res.ok) {
      throw new Form956ServerError(
        `fetchPdfByKey failed (HTTP ${res.status})`,
        res.status,
      );
    }
    return res.blob();
  }

  /**
   * Read the live widget values back out of a previously generated PDF.
   * Useful for verifying that ticks landed in the right place after
   * the React preview renders the blob.
   */
  async extractValues(
    cacheKey: string,
  ): Promise<Record<string, string>> {
    const url = `${this.baseUrl}/forms/form956/extract?key=${encodeURIComponent(cacheKey)}`;
    const res = await this.fetchImpl(url, { method: 'GET' });
    if (!res.ok) {
      throw new Form956ServerError(
        `extractValues failed (HTTP ${res.status})`,
        res.status,
      );
    }
    return res.json();
  }

  // ----- internals -------------------------------------------------------
  private get _requestId(): string | undefined {
    return undefined; // hook for future tracing
  }

  private _shouldRetry(status: number): boolean {
    return status === 408 || status === 429 || (status >= 500 && status < 600);
  }

  private _backoffMs(attempt: number): number {
    // Exponential: 200ms, 400ms, 800ms, ...
    return this.retryBaseMs * Math.pow(2, attempt);
  }

  private _sleep(ms: number): Promise<void> {
    return new Promise((r) => setTimeout(r, ms));
  }
}

// ----- module-level singleton -------------------------------------------
// Construct once at app boot from VITE_FORM_API_URL. The React app can
// import this directly; tests can construct their own Form956Service.
let _default: Form956Service | null = null;

export function getForm956Service(): Form956Service {
  if (_default) return _default;
  const baseUrl =
    (typeof import.meta !== 'undefined' &&
      (import.meta as { env?: Record<string, string> }).env?.VITE_FORM_API_URL) ||
    'http://127.0.0.1:5000';
  _default = new Form956Service(baseUrl);
  return _default;
}

// ----- Jest test markers -------------------------------------------------
// (Not run as part of the build — placeholder for where the React
// project's Jest suite would add tests. The Python pytest suite in
// tests/test_form956_service.py covers the server contract; these
// markers exist so a TS author knows where to add the JS-side tests.)
//
//   describe('Form956Service', () => {
//     test('retries 5xx up to 3 times', async () => { ... });
//     test('maps 400 to Form956ValidationError', async () => { ... });
//     test('aborts after timeoutMs', async () => { ... });
//   });
