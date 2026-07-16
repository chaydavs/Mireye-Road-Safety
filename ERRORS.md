# AI error log

One line per caught error: what was wrong, how it was caught. No editorializing.

<!-- format: YYYY-MM-DD | session N | what was wrong | how it was caught -->

2026-07-15 | session 1 | Assumed `/v1/fetch` longitude param was `lon`; POST returned HTTP 422 "missing field: lng". Corrected to `lng`. | Mireye API request validation (422 body named the missing field).
2026-07-15 | session 1 | Assumed `/v1/fetch` was a GET with query params; bare GET returned 404. Endpoint is POST with a JSON body. | Discovery probe against the live API (404 on GET, 200 on POST).
2026-07-16 | session 1 | `fetch_point` retried a 429 and returned the retry's status, so `run_probe` only counted a rate-limit on a double-429; single recovered throttles were invisible ("429: 0" could be masking throttling). | code-reviewer subagent; fixed by returning a `saw_429` flag counted regardless of retry outcome.
2026-07-16 | session 1 | Re-running the probe on the same fixed 100 points measured Mireye's warm server-side cache (~0.13 s/call) not cold first-touch (~0.3–2 s), overstating sustainable throughput ~4-15x — would have made Session 2 wrongly conclude the county fits in minutes. | Caught by comparing run 1 (cold) vs re-runs (warm); fixed by jittering points per run so every run measures cold.
2026-07-16 | session 1 | `import random` was silently deleted by the PostToolUse `ruff --fix` hook because the import was added one edit before the code that used it (momentarily unused). | Next `ruff`/`pytest` failed with F821/NameError; re-added after its use existed.
