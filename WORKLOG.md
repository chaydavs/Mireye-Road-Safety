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

---

## Session 2 — Network
**Date:** 2026-07-16 · **Commit:** `cab01b7`

**Done**
- ✅ `src/network.py` (functions only) + `tests/test_network.py` (6 tests: 4 TDD invariants written
  first — 3 points/segment, points within 5 m of their line, unique point_ids, segments ≤1.5×500 m
  — plus 2 `join_aadt` tests added on review). `ruff` clean, `pytest` 6/6.

**code-reviewer pass (findings addressed):** Fixed a real null-as-zero bug + scope creep by
**removing `truck_pct`** (`props.get(f) or 0.0` coerced unknown truck-share to 0%; and it's a
Session-4 scoring input, not Session 2). Removed dead fetch fields (`RTE_ID`/`ROUTE_NAME`) and
unused `length_m` column; made the bridge deferral a literal `TODO`; added `join_aadt` tests.
Rejected (spec-mandated): kept the kill-criterion/scope-projection machinery — Session 2 spec
requires comparing projected calls to Session 1's rate and printing scope options. See `ERRORS.md`.
- ✅ Ran pipeline end-to-end → `data/segments.parquet` (17,200) + `data/points.parquet` (51,600).

**Sourcing decision (documented deviation)**
- VirginiaRoads `VA_Primary_and_Secondary_Roads` has **NO local (S1400) roads** in Loudoun (only
  48 primary + 273 secondary) — but local roads are the product's thesis. So centerlines come from
  **Census TIGER/Line All Roads, FIPS 51107**, filtered to MTFCC S1200 (secondary) + S1400 (local);
  excluded private/ramps/alleys/trails. TIGER has no VDOT route id, so the PRD's "join by route id"
  degenerates to the **30 m spatial fallback** against VDOT Bidirectional Traffic Volume 2025.

**Findings**
- After deduping (below): **15,643 segments, 3,143 road miles, 46,929 points** (= projected calls).
- **AADT coverage 67.0%** (S1200 99.8%, S1400 67.6%), matched-distance median **0.0 m** (coincident
  centerlines, not spurious). **Inverts PRD §6 assumption** that local roads mostly lack counts —
  in Loudoun most have them; housing-density fallback needed for only ~33%.

**data-qa pass:** PASS on all hard invariants (counts, 3-pts/segment, uniqueness, referential
integrity, enum domains, aadt-null-never-0, no negatives). One benign WARN (9 pts ≤200 m past the
rectangular bbox = real points on Loudoun's non-rectangular border). Flagged ~9% duplicate segment
geometries → **fixed**: dedupe exact-duplicate TIGER geometries at load (17,200→15,643 segments).

**⚠️ KILL CRITERION FIRED**
- Full county = 46,929 calls → 469 min sequential @100/min (1,564 min worst-case @30/min),
  **117 min even at 4-concurrent** > the ~60 min budget. Pipeline exits 3 and STOPS for a scope
  decision. NOTE: Session 1 saw **0 rate-limiting**, so the true constraint is one-time wall-clock,
  not quota — and the fetch is resumable + cached. Scope options surfaced: (A) secondary-only
  ~2.4k calls/~6 min [drops local roads — off-thesis], (B) eastern Loudoun ~26k calls/~64 min,
  (C) full county ~117 min.

**SCOPE DECISION (user, 2026-07-16):** **Eastern Loudoun (lon > −77.55)** — ~8,549 segments,
~25,647 calls, ~64 min @ 4-concurrent. Rationale: keeps local-roads thesis, contiguous/mappable,
~1 hr one-time; full county remains reachable later via cache. Session 2 parquets stay full-county
(complete network); the eastern scope is applied as a **fetch-time filter in Session 3**.

**Left for next session (Session 3 — Fetch)**
- ⏸ SQLite cache keyed by rounded (lat,lon,field); resumable fetch loop over the eastern-scope
  points (lon > −77.55) for the 17 core + supporting fields; ≤4 concurrent; backoff per Session 1.
- ⏸ Provenance store (point_id, field, value, source, source_url, fetched_at, confidence, status
  from null_meaning); audit.json (null rate/field, confidence dist, wall time, calls vs cache hits).
- ⏸ Snap QA: where nearest_road_name disagrees with the segment route → re-snap once then discard,
  logged; leave a named function boundary for the QA agent.
- ⚠️ Kill: if any core scoring field (W/S rows) > 40% nulls county-wide, STOP and report.

---

## Session 3 — Fetch
**Date:** 2026-07-16 · **Commit:** `<pending>` (code done; full fetch running in background)

**⚠️ 7-hour hang (fixed):** the fetch shared one `httpx.Client` across 4 threads; when Mireye's
fly.io app cycled, reused keepalive sockets went half-open, the per-request timeout never fired,
and httpcore's shared pool lock stalled all threads (0% CPU, 7 hr). Diagnosed via `ps` (4 s CPU in
7 hr) + `lsof` (4 idle ESTABLISHED sockets + open sqlite journal). Fixed: **per-thread clients**,
explicit connect/read timeout, 15 s keepalive expiry, commits every 25 coords. Verified (direct
calls + resume smoke complete cleanly). See `ERRORS.md`. Session 4 built in parallel against the
growing cache per user direction.

**Done (code)**
- ✅ `src/fetch.py` (functions only, no ORM, ≤4 concurrent, one 429 backoff) + `tests/test_fetch.py`
  (10 tests). `ruff` clean. SQLite cache keyed by `(round(lat,5), round(lon,5), field)`; resumable
  (`already_done` = provenance ∪ qa_log discards); provenance store; `audit.json`; distance-based
  snap QA with the named `qa_triage_decision` boundary. Reuses `probe` field list + fetch call.

**Scope (user):** trimmed from eastern Loudoun to **town-scale Leesburg+Ashburn** bbox
`(-77.57,39.01,-77.48,39.12)` — ~7,877 pts (~1 hr), because measured cold 4-concurrent throughput
is ~122 calls/min (not the ~400 my Session 2 note assumed), making full eastern ~2–3.4 hr.

**Two design pivots (documented in ERRORS.md), both evidence-driven:**
1. **Snap QA is distance-based, not name-based.** Literal "name disagreement → discard" discarded
   28% of points that were the SAME road named differently (Overture "East Market Street" vs TIGER
   "State Rte 7 Bus"). Now: discard only if far from any road; name mismatch → kept low-confidence
   flag. This is a real Mireye-integration finding for the shortfalls report.
2. **`derive_status` honors Mireye's `"absent"` status** (semantic absence) → absent-semantic, not
   failed. Fixed a 96%→0% mislabel on `nearest_waterbody_name`.

**code-reviewer pass (findings addressed):** added **live catalog validation** before fetch
(CLAUDE.md CRITICAL — was missing); audit now emits per-field `status_distribution` and bases the
kill on **failed-rate** (semantic absence no longer trips it); provenance-completeness guard
(present value missing source/url/fetched_at → downgraded, "no provenance row, no value"); discards
are a terminal resume state; commit every 100 coords. Rejected: kill criterion is NOT scope creep
(Session 3 spec mandates it). Added tests for `key`, `write_provenance`, `build_audit` kill logic.

**data-qa (50-pt trial):** PASS — kill gate clear (worst scoring field 14% null), provenance 100%
complete on present values, 0 discards.

**⏳ IN PROGRESS:** full town-scale fetch (~7,877 pts) running in background (~25 pts/min, ETA a few
hr; watchdog guarding). Pending on completion: final audit numbers, data-qa on full provenance,
kill check, and a follow-up commit noting results.

---

## Session 4 — Score & map  (built in parallel against the growing cache, per user)
**Date:** 2026-07-16 · **Commit:** `4e8aa46`

**Done**
- ✅ `src/score.py` — PRD §6 scoring table (W .30/S .20/C .20/T .20/G .10) with threshold lookups in
  one visible dict, **grounded in the real Mireye value formats** (inspected the cache, not guessed).
  Missing components drop out of a factor's average (never 0); absent factors renormalize the
  weights. Confidence grade A/B/C from provenance confidence + traffic fallback. Outputs
  `scores.parquet` + Folium `output/map.html`. + `tests/test_score.py` (8 lookup/grade tests first).
- ✅ `src/agents/why_card.py` — top-20 cited why-cards; hard rule enforced (a line is emitted only
  from a provenance row with source+source_url; `/v1/ask` narrative is a labeled supplement).

**Ran on partial data (833 segments so far):**
- Scores **34–60, median 49, well-spread** (modal value 2% ≪ 30% degeneracy threshold → not
  degenerate). Grades: 648 C, 185 B, **0 A**.
- Why-cards: **60/60 cited lines carry a source URL**; 10/20 `/ask` narratives (rest degraded
  gracefully under fetch contention).

**Findings (for the shortfalls report):**
- **0 A-grades** — Mireye caps confidence on STATSGO-gap-filled soil, so ~no segment is all-high on
  the 11 W/S fields. Honest-by-construction (PRD §4/§10 SSURGO caveat), not a bug.
- Climate (C) and shrink-swell (S) are **near-constant within one county** → little differentiation;
  the risk signal is water + terrain + traffic (the PRD moisture-first thesis).
- NOAA precip/freeze gap-fill NOT integrated (PRD §10 shortfall); truck-share uplift deferred.

**data-qa on scores.parquet:** all 6 checks PASS (well-spread, valid domains, top3 well-formed;
54% used the housing-density proxy — a real finding; 0 A-grades as expected).

**code-reviewer pass (findings addressed):** Fixed a **hard-rule violation** — the why-card would
have printed the housing-density proxy as a false "AADT (VDOT)" claim; now the traffic driver is
tagged `traffic_aadt` (cited to VDOT) vs `housing_units_density_per_km2` (cited to its own Mireye
provenance row). Made grade PRD-faithful (missing W/S component lowers A→B), defensive against
unknown confidence strings, and score `None` (not a fabricated 0) when no factor is mappable;
derived renorm weights from `FACTORS` instead of re-hardcoding. Added `tests/test_why_card.py`
(the hard-rule safety property + exact segment-id match). 39 tests pass, ruff clean.

**Pending:** full-data re-run of score + why_card once the fetch completes (top-20 why-cards are
high-AADT arterials, unaffected by the proxy fix; `why_cards.json` regenerates on full data).

---

## Session 5 — Validation (LTPP)
**Date:** 2026-07-16 · **Commit:** `a1615c1`

**Data:** InfoPave gates Analysis-Ready data behind its portal, but the Standard Data Release is on
CloudFront per state (`.../SDR/39/By_State_Province/SDR39_<ST>.ZIP`, Access `.accdb`). Pulled VA +
5 climate-adjacent states (MD, NC, PA, TN, WV), fixed up front (not chosen for results). Read via
`mdbtools` (`brew install mdbtools`; reads `.accdb`). matplotlib added for the chart; scipy avoided
(numpy + a permutation test).

**Done**
- ✅ `src/validate.py` + `tests/test_validate.py` (3 tests). Extracts per-section deterioration
  (MRI slope **within a CONSTRUCTION_NO cycle** — see below), age (INV_AGE), traffic (TRF_ESAL),
  coords (SECTION_COORDINATES); fetches Mireye W+S+G ground at each section (own cache, no contention
  with the town fetch); regresses deterioration ~ ground + age + log(traffic); permutation test
  doubles as the shuffled-label sanity check. Chart `output/ltpp_validation.png`; result appended to
  `data/audit.json`. `ruff` clean, `pytest` 3/3.

**Methodology bug caught (ERRORS.md):** first pass fit ONE MRI-vs-time slope across all visits, so a
mid-window overlay (MRI drops after resurfacing) flipped 94% of slopes negative. Fixed to fit within
a construction cycle → 94% correctly positive.

**Result (n=51 sections) — honest, weak-but-directional (PRD §11 planned for this):**
- Top-quartile ground-risk sections deteriorate ~**17% faster** (median 0.0127 vs 0.0109 m/km/yr);
  regression ground coefficient **positive** but **NOT significant** (permutation p=**0.26**, seeded).
- **Shuffled-label sanity check passes**: shuffled-coef mean ≈ −1e-05 (~0) → no pipeline leakage.
- **Did NOT tune anything to manufacture significance** (Session 5 non-goal).
- Interpretation (a finding, not a failure): LTPP sections are Interstate/arterial with engineered,
  often-stabilized subgrades — little native-ground variation — while Subgrade's signal is strongest
  on the *local* roads LTPP barely samples. This IS the PRD's thesis (§2, §10), evidenced.

**code-reviewer pass (findings addressed):** confirmed the science is clean (no p-hacking,
permutation correct, controls used, null handling correct). Fixed: **live catalog validation before
fetch** (CLAUDE.md CRITICAL — was missing, same class as Session 3); a **provenance guard** so an
unsourced value can't enter the regression; **seeded permutation** (p now reproducible); weights
read from `score.FACTORS`. Added 4 tests (planted-signal → significant + shuffled ~0; pure-noise →
not significant; reproducibility; `score_ground` drops unsourced/null, never zero). 43 tests, ruff clean.

**Deferred:** HPMS spot check (PRD item 2) — a lighter rank-correlation vs published IRI; the LTPP
calibration test is the stronger, headline evidence and is complete. Noted for follow-up.

---

## Session 6 — App & agents
**Date:** 2026-07-16 · **Commit:** `61f6289`

**Done**
- ✅ `src/app.py` — Streamlit one-pager: left Folium risk map (streamlit-folium), right the cited
  why-card for the selected segment, bottom the copilot chat. Thin, default styling.
- ✅ `src/agents/copilot.py` — Anthropic (`claude-sonnet-5`) agent with EXACTLY two tools
  (`query_scores` over scores.parquet + provenance store; `mireye_lookup` via `/v1/ask`), tool-use
  loop, system rule mirrored from the why-card agent (claims from tool results only; refuse what the
  data can't answer). `.env`-aware key loader.
- ✅ `src/agents/audit_narrator.py` — reads `audit.json` + `ERRORS.md`, drafts `docs/shortfalls.md`,
  clearly marked AGENT-DRAFTED.
- ✅ `tests/test_app.py` (4 tests incl. a headless Streamlit **AppTest** that runs `main()` and
  asserts map + why-card + chat render). 50 tests total, ruff clean.

**Self-eval — 3 copilot transcripts (verbatim, all correct):**
1. "Why is the top segment ranked first?" → `query_scores(top)` → cites Sycolin Rd (3620, 60.5, C),
   drivers with sources.
2. "Compare 3620 and 1315" → two `query_scores(segment)` → cited comparison; volunteers that neither
   tool gives a failure date.
3. "Which segment will fail in March 2027?" → **REFUSES with reasons, zero tool calls** — "scores
   reflect relative risk, not a failure timeline… neither source produces forecasted dates."

**Bug caught (extended thinking):** `claude-sonnet-5` uses extended thinking by default; at
`max_tokens=2000` the narrator spent all tokens thinking → empty output. Fixed by budgeting
(narrator 8000, copilot 3000). Also fixed the narrator truncating the audit JSON so it mislabeled
the fetch snap-QA as "LTPP" — now passes a compact audit that keeps the `ltpp_validation` result.

**code-reviewer pass (findings addressed):** removed `tiles="CartoDB positron"` (UI styling scope
creep — deleted per the non-goal); completed the AADT why-card citation with its VDOT **source URL**
(AADT is a VDOT join, not a Mireye provenance row — now cited to a real, URL'd federal source);
added `tests/test_copilot.py` for the offline `query_scores` tool. Confirmed clean: exactly two
tools, no auth/deploy, no null-as-zero, copilot can't bypass tool results. 54 tests, ruff clean.
