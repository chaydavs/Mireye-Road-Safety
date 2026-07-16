# Subgrade — code & errors guide (for understanding + presenting)

A plain-English tour of what every piece of code does, how the pieces fit, the scoring model, the
agentic layer, and the full story of what went wrong and how it was caught. Read this end-to-end and
you can present the project confidently.

---

## 1. The one-sentence pitch

> Subgrade takes a county's road network and returns a **ranked, per-segment forecast of which roads
> will deteriorate fastest and why**, with **every input traceable to a federal source**. It's the
> *cause* layer for pavement management — not "which roads are rough today," but "which roads sit on
> ground that makes them fail early."

Built entirely on **Mireye** (a geospatial API that returns 170+ provenance-tagged ground/climate
fields per coordinate), because that provenance — *"USDA says poorly-drained clay, fetched
2026-07-16, here's the URL"* — is exactly what a county engineer needs to defend a budget.

---

## 2. The pipeline (how the code fits together)

Five stages, left to right. Each is one script; artifacts flow through `data/`.

```
 ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐   ┌────────────┐
 │ probe.py │→  │ network.py│→  │ fetch.py │→  │ score.py │→  │ app.py     │
 │ learn the│   │ build the │   │ pull     │   │ weighted │   │ map +      │
 │ API      │   │ road net  │   │ Mireye   │   │ risk +   │   │ why-card + │
 │          │   │ + traffic │   │ (cached) │   │ grade    │   │ copilot    │
 └──────────┘   └───────────┘   └──────────┘   └──────────┘   └────────────┘
                                     │                              ↑
                                     │  provenance store (SQLite)   │
                                     └──────────────┬───────────────┘
                                                    │
                                          validate.py (LTPP: does it predict reality?)
```

**Design principle (from the PRD): agentic where judgment lives, deterministic where the work is
bulk.** The fetch loop is a plain script (an agent looping over 8,000 API calls would be slower,
costlier, flakier). Four things that need *judgment* are agents: snap-QA, the why-card, the copilot,
and the audit narrator.

---

## 3. Module by module

### `src/probe.py` — Session 1, "learn the API before trusting it"
Fires ~100 real calls to measure latency, rate-limit behavior, and per-field null rates, and
**validates every field name against the live catalog** (`/v1/meta/fields`) before fetching. This is
where we discovered the `/v1/fetch` contract (POST, `lng` not `lon`) and that **there is no
rate-limiting** — the binding constraint is per-call latency, not quota. That single finding shaped
every later scaling decision.

### `src/network.py` — Session 2, "build the road network"
Downloads **Census TIGER "All Roads"** for Loudoun County (FIPS 51107), filters to secondary + local
roads, **segments each road into ~500 m pieces**, drops 3 sample points on each (snapped exactly onto
the centerline), and joins **VDOT AADT** traffic counts by a 30 m spatial match. Outputs
`segments.parquet` and `points.parquet`.
- *Why TIGER, not VDOT?* VDOT's road layer has **no local roads** in Loudoun — but local roads are
  the whole product thesis. TIGER has them.
- It also computes the **kill criterion**: at the measured fetch rate, would the whole county fit in
  ~1 hour? It didn't, which drove the scope decision to the Leesburg+Ashburn corridor.

### `src/fetch.py` — Session 3, "the engineering core"
A **cache-backed, resumable, 4-concurrent** fetch loop. For each point it pulls all 43 Mireye fields
in one call. Key parts:
- **Cache** keyed by `(round(lat,5), round(lon,5), field)` — so re-runs never re-spend the API.
- **Provenance store**: every value saved with `source, source_url, fetched_at, confidence, status`.
  *No provenance row, no value.*
- **`status`** derived per field as **present / absent-semantic / failed** — driven by the catalog's
  `null_meaning` and Mireye's own `"absent"` signal. A null is *never* treated as zero.
- **Snap-QA** (`qa_triage_decision`): the one judgment call, deterministic but with a named boundary
  a smarter agent could replace — flag/keep/discard a point based on distance from the nearest road.
- **`audit.json`**: null rates, confidence distribution, calls-vs-cache, and the corridor cost.

### `src/score.py` — Session 4, "the transparent scoring engine"
`Risk = 100 × (0.30·W + 0.20·S + 0.20·C + 0.20·T + 0.10·G)` — five factors, weights from the
literature, **not a black box** (a county engineer and a Mireye founder can both read it).
- **W** water, **S** soil movement, **C** climate, **T** traffic, **G** terrain. Each field maps to
  0–1 via **published-threshold lookup tables** in one visible dict at the top of the file (grounded
  in the *real* Mireye value formats — I inspected the data, didn't guess).
- **Missing components drop out of a factor's average** — never default to zero. If a whole factor
  has no data, the weights renormalize.
- **Confidence grade A/B/C** propagates from Mireye's per-value confidence. In Loudoun there are
  **zero A grades** — because Mireye caps confidence on reconnaissance-scale (STATSGO) soil. That's
  honest-by-construction, not a bug.
- Outputs `scores.parquet` and the **Folium map** (`output/map.html`).

### `src/validate.py` — Session 5, "does it predict reality?"
The **LTPP test** — the "what worked" evidence. Explained fully in §5 below.

### `src/app.py` + `src/agents/` — Session 6, the UI + agentic layer
Explained in §4 below.

---

## 4. The agentic layer (four agents, one hard rule)

**The hard rule, everywhere:** *a factual claim must trace to a provenance row / tool result; if the
data can't answer, say so.* This is what makes the output trustworthy instead of an LLM guessing.

1. **Snap-QA** (`fetch.py`) — decides keep / re-snap / discard for each point. Deterministic today,
   agent-replaceable.
2. **Why-card agent** (`agents/why_card.py`) — for the top-20 riskiest segments, composes a cited
   explanation where **every line carries a federal source URL** (USDA soil, USGS landslide, FEMA
   flood, VDOT traffic), plus a Mireye `/v1/ask` narrative included only as a *labeled supplement*.
3. **County copilot** (`agents/copilot.py`) — an Anthropic agent with **exactly two tools**:
   `query_scores` (the scored data + provenance) and `mireye_lookup` (live Mireye). It answers "why
   is this segment ranked first?" with citations — and **refuses** "which segment will fail in March
   2027?" (a future-date prediction the data can't make) with zero tool calls.
4. **Audit narrator** (`agents/audit_narrator.py`) — reads `audit.json` + `ERRORS.md` and drafts
   `docs/shortfalls.md` (the "where Mireye falls short" report), clearly marked AGENT-DRAFTED.

**The UI** (`app.py`) is one thin Streamlit page: Folium map on the left, cited why-card on the
right, copilot chat at the bottom. It's verified by a headless Streamlit `AppTest`.

---

## 5. The LTPP validation (the "what worked" chart)

**The question:** do the roads Subgrade flags as high-risk actually deteriorate faster in reality?
**The answer key:** FHWA's LTPP program — decades of measured pavement roughness on real sections.

`validate.py` pulls 51 LTPP sections (VA + 5 climate-adjacent states), computes each section's
**measured deterioration rate** (roughness slope over time, *within one construction cycle*), fetches
Mireye's ground score at each section, and tests — **controlling for age and traffic** — whether high
ground-risk sections deteriorated faster. A **permutation test** (shuffle the labels 2,000×) doubles
as a leakage sanity check.

**Result (honest):** top-quartile ground-risk sections deteriorated **~17% faster** (right
direction), but **not statistically significant** (p ≈ 0.26, n = 51); shuffled coefficient ≈ 0
(no leakage). We did **not** tune anything to manufacture significance. *Why it's weak is the
finding:* LTPP sections are Interstate/arterial roads with engineered, stabilized subgrades — little
ground variation — while Subgrade's signal is strongest on the local roads LTPP barely samples.
(Chart: `output/ltpp_validation.png`.)

---

## 6. What the model got wrong, and how it was caught (`ERRORS.md`)

This is the assignment's "what did the model get wrong" — collected live, 18 entries. Grouped by
theme, these are your best talking points because they show *process*, not just output.

**API-contract discovery (don't trust memory).** Assumed the longitude param was `lon` (it's `lng`)
and that `/v1/fetch` was a GET (it's a POST) — both caught immediately by the live API (422 / 404).

**Field-name validation (the CLAUDE.md CRITICAL rule).** Twice — in `fetch.py` and `validate.py` — I
fetched using field names from memory without re-validating against the live catalog. The
code-reviewer caught both; both now validate before any fetch. This is the single most-repeated
lesson: *never trust a field name you didn't just verify.*

**Null semantics (a null is never zero).** Mireye's status vocabulary includes `"absent"` (semantic
absence, e.g. "no waterbody nearby"), which I first mislabeled as `"failed"` — inflating apparent
failure rates from ~0% to 96% on some fields. And the audit at first lumped "absent-semantic" with
"failed," which would have tripped the scoring kill-criterion on legitimate absences. Both fixed to
keep the distinction.

**The provenance hard rule.** The why-card, for segments using the housing-density traffic *proxy*,
printed the housing number as a fabricated **"AADT (VDOT)"** citation. That's exactly the
hallucinated-citation failure the whole design exists to prevent — caught by the reviewer, fixed by
tagging the driver and citing the proxy to its own real source.

**Measurement integrity.** Three separate traps: a 429-retry that *undercounted* rate-limiting; a
probe that measured Mireye's *warm cache* (0.13 s) instead of cold first-touch (2 s), which would
have made us wrongly conclude the county fits in minutes; and an *unseeded* permutation test whose
p-value wasn't reproducible. Each fixed.

**Reliability at scale.** The fetch **hung for 7 hours at 0% CPU** — one shared `httpx.Client` across
4 threads reused keepalive sockets that went stale when Mireye's server cycled, and the timeout never
fired. Diagnosed from `ps` (4 s CPU in 7 hr) + `lsof` (idle sockets). Fixed with per-thread clients +
strict timeouts. Then the full run **crashed in QA** on an unnamed road's `NaN` name (NaN is truthy,
so the guard missed it) — caught only because the *full* dataset had unnamed roads the smoke tests
didn't.

**Scientific method.** The LTPP deterioration slope was first fit across *all* visits, so a
mid-monitoring road resurfacing flipped 94% of slopes negative (pavements don't get smoother with
age). Fixed to fit *within* a construction cycle.

**Honesty over pretending.** The snap-QA first discarded 28% of points as "off-road" that were the
same road named differently (Overture vs TIGER) — caught by inspecting the discards (they were major
arterials). And a `TODO` falsely claimed bridge exclusion was done — caught by the final sweep, made
into an honest known-limitation instead.

**LLM-specific gotchas.** The audit narrator produced *empty* output because `claude-sonnet-5`'s
default extended thinking ate the whole token budget; and it once truncated the audit JSON and
mislabeled snap-QA as the "LTPP" result. Both fixed.

---

## 7. How to present it (talking points)

1. **The gap** (30 sec): symptom sensing is commoditizing; the *cause* layer for local roads is dark,
   and nobody assembles the data. That's the opening.
2. **The product** (live demo): open the app → map → click a segment → **every why-card line has a
   federal source URL**. This is the anti-LLM point made visible.
3. **The agent** (live): ask the copilot "why is the top segment ranked first?" (cited answer), then
   "which segment will fail in March 2027?" (**it refuses**). Honesty by construction.
4. **What worked** (the LTPP chart): validated against federal ground truth; right direction, weak
   signal, reported honestly — *and here's why it's weak* (LTPP under-samples local roads).
5. **Where Mireye falls short** (`docs/shortfalls.md`): the headline is **no corridor primitive** —
   we sprayed **7,870 point calls to cover one corridor** of one county. A polyline endpoint opens the
   entire linear-asset market.
6. **The process** (`ERRORS.md`): 18 caught mistakes, each with how it was found — the field-name
   discipline, the 7-hour hang diagnosis, the honest weak LTPP result. This is the "what did the model
   get wrong" answer, and it's the strongest evidence of rigor.

*Companion docs: `README.md` (overview), `WALKTHROUGH.md` (demo order), `WORKLOG.md` (per-session
decisions), `ERRORS.md` (mistakes), `FUTURE.md` (deferred), `docs/shortfalls.md` (Mireye gaps).*
