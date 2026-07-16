# Subgrade work log

Running record of what each build session did, decided, and left for the next.
Workflow: **one continuous conversation** (no `/clear` between sessions). At the start of a
session, read this file + `ERRORS.md`; at the end, append the session's entry here.

Legend: ✅ done · ⏸ deferred/blocked · ⚠️ kill-criterion note

---

## Session 0 — Repo, constitution, subagents, hooks
**Date:** 2026-07-15 · **Commit:** `40a7127`

**Done**
- ✅ `CLAUDE.md` — standing rules (PRD authority, lean code, field-name validation vs live
  catalog, `null_meaning`-driven null handling, provenance-or-no-value, 3-part definition of
  done, ERRORS.md protocol).
- ✅ `.claude/agents/code-reviewer.md` + `data-qa.md` — the two judgment subagents.
- ✅ `.claude/settings.json` — PostToolUse hook runs `.venv/bin/ruff check --fix` on edited/written
  `.py` files. Verified end-to-end (stripped unused imports from a test file).
- ✅ `.gitignore` (`data/`, `.env`, `__pycache__`, `.venv/`), empty `ERRORS.md`, git init.
- ✅ Python 3.11 `.venv` via uv with the 11 required packages only; all import clean, `ruff` +
  `pytest` run.
- ✅ `PRD-subgrade.md` in repo root; `.env` with a valid `MIREYE_TOKEN` (gitignored) +
  `.env.example`.

**Learned / decided**
- Mireye catalog `https://api.mireye.com/v1/meta/fields` is **public** (HTTP 200, no token) and
  returns per-field `null_meaning`, `layer`, `interpretation_hints`.
- Token is a 90-day `api_token` JWT (`iss: mireye-earth`, exp **2026-10-14**); `Bearer` header
  accepted on catalog.
- `/v1/fetch` request contract (auth shape, params, rate limits) **not** explored — that is
  Session 1's job by design.

**Left for next session (Session 1 — Probe)**
- ⏸ Discover the `/v1/fetch` contract; validate all 17 core + supporting field names (PRD §7)
  against `data/field_catalog.json`.
- ⏸ ~100-call probe across Loudoun County: latency, 429 behavior, per-field null rates,
  road-miles-per-hour extrapolation at 3 points/segment.
- ⚠️ Kill criterion: if sustainable rate can't cover the county in ~1 hour, stop and report the
  three VDOT districts to scope down to.

---

## Session 1 — Probe
**Date:** 2026-07-15/16 · **Commit:** `66c1fd2`

**Done**
- ✅ `src/probe.py` (functions only, no classes) + `tests/test_probe.py` (9 pure-helper tests).
- ✅ Definition of done met: `ruff` clean, `pytest` 9/9, probe runs end-to-end; report asserts
  all four measurements present; report format verified deterministic across runs (numbers masked).
- ✅ `data/field_catalog.json` saved (269 fields total). All **43** PRD §7 fields (17 core + 26
  supporting) present — catalog-validation kill point did NOT fire.

**code-reviewer pass (findings addressed)**
- Fixed real bug: recovered 429s weren't counted (`saw_429` flag). Accepted: dropped the
  unrequested `data/probe_report.json` write; added tests for `build_report` + the 429 path.
  Rejected (spec-mandated): kept `assert_report_complete` — Session 1 spec says "assert in code
  that the report covers all four measurements". See `ERRORS.md` for the caught bugs.

**Discovered — the `/v1/fetch` contract** (was unknown at Session 0):
- `POST /v1/fetch`, `Authorization: Bearer <token>`, JSON body `{"lat":.., "lng":.., "fields":[...]}`.
  Param is **`lng`** not `lon`; it is **POST** not GET. Both mis-assumptions caught by the API and
  logged in `ERRORS.md`.
- Per-field response: `value, unit, source, source_url, confidence, fetched_at, dataset_vintage,
  ttl_seconds, notes, status` + top-level `partial_failures[]`. Exactly the provenance CLAUDE.md
  requires — Session 3's provenance store maps 1:1 to this.

**Measured (100 calls/run, Loudoun diagonal, sequential)**
- **0 errors, 0× 429 across every run** — quota is NOT the binding constraint at this volume.
- Latency is **cache-state dependent** (Mireye caches per coordinate server-side, ttl up to ~1 yr):
  - warm re-touch (fixed points): ~0.13 s/call → ~445 calls/min
  - cold-ish jittered points (fresh coords, but ~100 m apart share soil map-units): median
    ~0.3 s, p95 0.5–2.6 s → ~100–160 calls/min
  - first cold-start run (connection warmup + fully cold): ~2 s mean → ~29 calls/min
- **Binding constraint is latency variance, not rate limits.** Concurrency (Session 3's
  4-concurrent) is the scale lever.
- Null rates: all W/S **scoring** fields 97–100% value. Two non-scoring core fields flagged:
  `soil_restrictive_layer_depth_cm` / `_kind` at 63–68% null — but **0% failed**; `null_meaning`
  confirms semantic absence (no restrictive layer for the dominant component), not gaps.
  Does NOT threaten Session 3's >40%-null-on-a-scoring-field kill criterion.

**Extrapolation (feeds Session 2 decision)**
- 9.66 points/mile. Coverage spans **~180 mi/hr (worst-case cold-start) to ~1,000 mi/hr
  (typical cold-ish)** sequential, ×4 at 4-concurrent. Session 2 should compute Loudoun's actual
  road miles and plan against the **conservative** cold floor (~180 mi/hr sequential).

**Left for next session (Session 2 — Network)**
- ⏸ Compute Loudoun's actual secondary+local road miles; at ~179 mi/hr sequential (or ~716 mi/hr
  at 4-concurrent), decide if the county fits ~1 hour or if we scope to VDOT districts (Session 2
  kill criterion).
- ⏸ Centerlines → filter → ~500 m segments → 3 snapped points/segment → `segments.parquet` /
  `points.parquet`; AADT join with 30 m spatial fallback; `traffic_source='none'` where no count.
