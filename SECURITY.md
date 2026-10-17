# Security policy

Saathi is used by low-income workers, so we treat their data, their trust and the shared AI quota with care.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Report them privately with the **"Report a vulnerability"**
button on the repository's [Security tab](https://github.com/AryanBansal1/promptwar/security). If that button is not
available, open an issue that only asks for a private contact, without any details of the problem.
Include steps to reproduce, the affected URL or endpoint, and the impact you expect. We aim to acknowledge reports within
3 working days and to fix confirmed issues as quickly as their severity requires. Please give us a reasonable time to fix
an issue before disclosing it. Testing must not degrade the service for real users or access anyone else's data.

## Data we hold

- **No accounts, no server-side storage.** Income entries, the work profile, checklists and drafts stay in the worker's
  browser (`localStorage` / `sessionStorage`). The server is stateless.
- **Uploads and audio** are read into memory, validated, sent to Gemini and discarded. They are never written to disk,
  stored or logged.
- **No personal data is sent to the LLM** beyond what the worker types for that request: advice uses aggregated
  numbers only, scheme explanations receive no profile values, letters use `[Your Name]`-style placeholders, and long
  digit runs (phone, account, UPI reference numbers) are masked in pasted SMS text.

## Protections in place

| Area | Protection |
|---|---|
| Secrets | Only from environment variables / Secret Manager (`SecretStr`, never logged); `.env*` is git-, docker- and Vercel-ignored; no secrets in the repository or its history. Configuration errors report field names, never values. |
| Input validation | Pydantic models with `extra="forbid"` on every request; length, range, enum and date checks. Non-JSON POSTs → 415 and oversized bodies → 413 **before the body is read**. Uploads: type allow-list plus magic-byte check, size cap enforced while streaming (4 MB images/PDF, 2 MB audio). |
| Rate limiting | Cost-weighted per-client AI budget, per-instance AI budget and a general per-client API limit (429 with `Retry-After`). The client key is the connection address; only on Vercel is Vercel's `x-vercel-forwarded-for` trusted. Limits are per instance: see the README for the shared-store (Redis) fix. |
| Errors and logs | A last-resort barrier returns a generic `{"error": {"code", "message"}}` 500. Logs carry request id, method, route template, status and latency only: no bodies, query strings, IPs, raw paths, stack traces or environment values. |
| Prompt injection | User text is wrapped in `<user_input>` delimiters and treated as data; curated facts sit in `<records>`; Gemini output is schema-constrained, validated item by item, and stripped of control/bidi characters, links and (in chats) phone numbers before reaching the browser. |
| Browser | Strict CSP (`'self'` only, no inline code, no `unsafe-*`, `frame-ancestors 'none'`), HSTS, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, COOP, CORP, `Permissions-Policy` (microphone for this origin only). All dynamic text is rendered with `textContent`, never `innerHTML`; `mailto:` and `wa.me` links are URL-encoded; external links use `rel="noopener noreferrer"`. API answers are sent with `Cache-Control: no-store`. |
| Platform | CORS is same-origin unless explicitly configured; `/docs` and `/openapi.json` are disabled in production; unknown routes get the same generic 404. Production refuses to start without `GEMINI_API_KEY` and refuses any non-Gemini (test) AI client. |
| Dependencies | Every runtime package pinned to an exact version in `requirements.txt`; `pip-audit` runs with `make audit` and reports no known vulnerabilities. |

Every protection above is covered by automated tests (`tests/test_hardening.py`, `tests/test_platform.py`,
`tests/test_imports.py`, `tests/test_voice.py`).
