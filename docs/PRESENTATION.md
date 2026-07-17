# Subgrade — Complete Feature & Methodology Reference

Everything built, what each feature tells the audience, and exactly how it's calculated.
Written for building presentation slides. Every number here is real and reproducible from the code.

**One-liner:** Subgrade is a *cited cause-layer* for local-road deterioration risk — it predicts which
roads will fail, from the ground up (soil, water, bedrock, climate, terrain, traffic), with **every input
traceable to a federal source through Mireye's API**. Live at **https://web-one-rosy-32.vercel.app**.

**Scope of the demo:** the Leesburg + Ashburn corridor of Loudoun County, VA — **2,644 scored road
segments** (out of a 15,643-segment full county network).

---

## 0. The problem & research question

- **Problem:** America has more failing road-miles than budget. The decision that matters — *which
  segments to treat next* — needs **prediction of the cause**, not measurement of the symptom. Symptom
  sensing (connected-vehicle roughness) is commoditizing, and only covers arterials. On **local roads the
  cause data is dark**, and nobody joins USDA soil + USGS water + NOAA climate + FEMA flood to road
  segments.
- **Research question:** *Can we build the cause layer for local-road deterioration, entirely on Mireye,
  with every input cited to a federal source — and does it predict reality?*

---

## 1. Data foundation

### 1.1 Road network → segments
- **What it is:** the county's local/secondary road centerlines, split into scoring segments.
- **Tells us:** the unit of decision — one segment = one "treat / don't treat" call.
- **How:** road geometry ingested and segmented; **2,644** segments scored in the corridor (15,643 in
  the full county). Each segment carries a stable `segment_id`, `route_name`, and geometry.

### 1.2 Sample points & per-segment aggregation
- **What it is:** each segment is sampled at multiple points; ground data is fetched per point.
- **Tells us:** a segment's risk reflects the ground *along* it, not one lucky pixel.
- **How:** **7,877 sample points** total (~3 per segment). For each field, the segment value is the
  **median** of its present points (numeric) or the **mode** (categorical), carried with the **minimum
  confidence** across points.

### 1.3 Mireye fetch + field-name validation (CRITICAL discipline)
- **What it is:** all ground/climate fields pulled from **Mireye's `/v1/fetch`** API.
- **Tells us:** Mireye is the single integration point for USDA/USGS/NOAA/FEMA/USFWS data.
- **How:** before any fetch, **every field name is validated against Mireye's live catalog**
  (`/v1/meta/fields`) — never trusted from memory. **Corridor cost evidence: ~7,870 Mireye `/v1/fetch`
  calls to cover 7,877 points (≈ one call per point), 0 rate-limiting** — a headline number for the
  "point API doesn't scale to corridors" critique.

### 1.4 Provenance discipline (the spine of the whole product)
- **What it is:** **no value is stored without its provenance row.**
- **Tells us:** this is the anti-LLM guarantee — nothing is invented; everything is cited.
- **How:** every fetched value is stored as `value, source, source_url, fetched_at, confidence`. Nulls are
  handled by each field's `null_meaning` from the catalog — **a null is never silently treated as 0 or
  false** (a missing value must not read as "no risk").

---

## 2. The risk score (the core model)

### 2.1 The formula
```
Risk = 100 × ( 0.30·W  +  0.20·S  +  0.20·C  +  0.20·T  +  0.10·G )
```
Renormalized over the factors actually present (a fully-absent factor drops out of both numerator and
denominator — never contributes a fabricated 0).

- **What it tells us:** a 0–100 deterioration-**risk** score per segment (higher = more prone to
  deteriorate). **It is risk/susceptibility, not current pavement condition or safety.**
- **Why a transparent weighted model (not ML):** every point of the score is explainable and cited — the
  opposite of a black box. This is a deliberate design choice for a government buyer.

### 2.2 The five factors, their weights, and their fields
| Factor | Weight | What it captures | Fields (all Mireye-served) |
|---|---|---|---|
| **W — Water/moisture** | **0.30** | moisture is the #1 deterioration driver | soil drainage class, ponding frequency, within-floodplain, FEMA flood zone, surface-water permanence, distance-to-wetland, available water capacity, hydrologic soil group |
| **S — Soil/subsurface** | 0.20 | shrink-swell, erosion, shallow rock | shrink-swell class, erodibility (K-factor), depth-to-bedrock |
| **C — Climate** | 0.20 | freeze-thaw & heat stress | snow-cover days, days > 32 °C, mean dry-bulb temp |
| **T — Traffic (load)** | 0.20 | the load on top of the ground | AADT (VDOT) — or housing-density proxy |
| **G — Geohazard/terrain** | 0.10 | slope & landslide | slope, landslide susceptibility |

**Moisture-first thesis:** W is weighted highest because water is the dominant cause of subgrade failure.

### 2.3 How each component becomes a number (normalization)
- **What it is:** each raw field value → a `[0,1]` risk via **published threshold tables** (one visible
  place in the code, cited to the PRD). Higher = worse.
- **Examples (real thresholds):**
  - *Soil erodibility (K-factor):* ≥0.43 → 1.0; ≥0.37 → 0.7; ≥0.28 → 0.4; else 0.2.
  - *Depth to bedrock (cm):* ≤60 → 1.0; ≤100 → 0.7; ≤150 → 0.4; else 0.2 (shallower rock = worse).
  - *Soil drainage class:* "Poorly drained" → 1.0 … "Excessively drained" → 0.1.
  - *FEMA flood zone:* A/AE/V/VE (Special Flood Hazard Areas) → 1.0; X500 → 0.4; X → 0.1.
  - *Hydrologic soil group:* A → 0.2 (best infiltration) … D → 1.0 (holds water).
- **Factor score** = the **mean of its present component scores** (missing components drop out).
- **Traffic** = `log10(AADT+1) / log10(50,000+1)`; if no real AADT, a **housing-density proxy**
  `log10(density+1)/log10(2,000+1)` capped at 0.9 (and it *lowers the confidence grade*).

### 2.4 Result on the corridor
- Score range **32.1 – 62.1**, median **48.9**. No single score holds >1% of segments (non-degenerate).
- **What it tells us:** these are all local roads — none are "pristine" on an absolute scale; the value
  is in the **relative ranking** for triage (see §2.6).

### 2.5 Confidence grade (A / B / C)
- **What it is:** a per-segment confidence letter, separate from the score.
- **Tells us:** *how much to trust this segment's score* given data completeness.
- **How:** **C** if a whole load-bearing factor (W or S) is absent, or traffic is a proxy/none, or any
  W/S input is low-confidence. **B** if any W/S input is medium-confidence or any W/S component is
  missing. **A** only if **all** W/S components are present at high confidence. Corridor: **0 A · 938 B ·
  1,706 C** (honest — real AADT is sparse on local roads, so most segments cap at C).

### 2.6 Relative-percentile map coloring
- **What it is:** the map colors segments by their **percentile rank** within the corridor (green =
  lowest risk → red = highest), with a gradient legend that says "relative rank."
- **Tells us:** where to look *first* — the worst ~20% pop red.
- **How:** `rank_pct = score.rank(pct=True)`, bucketed into quintiles. **Why relative, not absolute:** the
  scores cluster in a narrow 32–62 band, so an absolute 0–100 ramp painted ~89% of the map one color and
  was useless. The legend states plainly it's relative ranking among local roads, not a good/bad claim.

---

## 3. Top-5 drivers (the why-card)

- **What it is:** for the selected segment, the **five inputs that contributed most** to its score, each
  with its value and a link to the **federal source**.
- **Tells us:** *why this road scored what it scored* — the explainability payoff.
- **How:** each contributor's weight = **factor_weight × normalized_component_value ÷ (components present
  in that factor)**. These are ranked; the top 5 are shown. Example (worst segment, Lents Mill Rd, 62.1):
  Traffic AADT (0.151) · Soil erodibility K-factor (0.067) · Depth to bedrock (0.047) · Soil drainage
  class (0.037) · Within floodplain (0.037). **Every line is cited** (VDOT, NRCS gNATSGO, PELLETIER_DTB,
  FEMA NFHL, …) — no provenance row, no line.

---

## 4. Remaining Service Life (RSL) — the year prediction

- **What it is:** an estimated **year range** for when a segment reaches poor condition.
- **Tells us:** not just *which* roads are at risk, but *when* — the planning horizon.
- **How (transparent rate-stretch, not a survival model or ML):**
  ```
  effective_life = treatment_expected_life / relative_rate
  relative_rate  = clamp( segment_score / median_score , 0.5 , 2.0 )   # faster than typical = shorter life
  reach_poor_year = last_treated_year + effective_life   (a RANGE, from the lifespan range)
  ```
  - **Treatment-year source priority (never fabricated):** HPMS "Year of Last Improvement" → VDOT paving
    completion → otherwise a functional-class prior.
  - **Treatment lifespans** come from one visible, FHWA-cited table (ranges, e.g. mill-and-overlay 12–18
    yrs, chip seal 4–7 yrs).
  - **Honesty rules (this is the differentiator):** a range is shown **only** when a real treatment year
    exists (HPMS/VDOT); it's **floored at the current year** (a road is never shown already-failed); and
    when there's no treatment year, the card says **"no treatment-year data; RSL not estimated"** instead
    of inventing a past window. Corridor: **106 segments get a real range (VDOT), 2,538 are honestly
    not-estimated.** A single-year answer is treated as a bug by definition.

---

## 5. VDOT paving integration + plan-vs-risk

### 5.1 Paving ingestion (anonymous ArcGIS Python API)
- **What it is:** VDOT's completed + planned paving pulled from its public ArcGIS FeatureServers.
- **Tells us:** what the county has already treated and what's scheduled — the real-world plan.
- **How:** anonymous `GIS()` / `FeatureLayer.query` (documented ops only, count-asserted, no Esri
  enrichment). **Completed = `PROJECT_STATUS='Completed'` only** (scheduled/in-progress rows are *not*
  treatments — never fabricate a year). **Contact fields (PM name, phone, email) are dropped at ingestion
  and asserted absent in every stored table.** Join is **geometry-first** (same-road overlap ≥ 100 m, so
  a cross-street can't match). Corridor: 494 completed + 160 planned projects; **106 scored segments carry
  a real treatment year.**

### 5.2 Plan-vs-risk comparison (3 buckets)
- **What it is:** compares Subgrade's risk ranking against VDOT's paving plan.
- **Tells us:** where risk and plan agree, and where they diverge — a **conversation-starter, not an
  error claim** (VDOT has condition & funding context Subgrade doesn't model).
- **How / result:** (a) top-decile risk **NOT** on the plan = **265**; (b) top-decile risk scheduled
  (agreement) = **4**; (c) scheduled but bottom-half risk = **8**.

---

## 6. Live "Right now" layer (fragility × current stress)

- **What it is:** a toggle that overlays *today's* stress on the static fragility map.
- **Tells us:** the static score says which roads are *inherently* weak; the live layer says which weak
  roads are *under load right now* → a "go look today" list vs a "plan the program" list.
- **How (three signals, each cited & timestamped):**
  1. **NWS active alerts** (flood / winter-storm) intersected with segments;
  2. **USGS current discharge** flagged only when **above that gauge's own daily-series median** (no
     invented flood-stage thresholds);
  3. **Wet-week** boolean from the last 7 days.
  A segment is watch-listed when fragile **AND** (alert OR elevated gauge OR wet week). **Explicit status
  line** always states the counts — e.g. *"0 active VA alerts, 8 segments flagged, 2 gauges elevated,
  checked 2h ago"* — so "calm" is never confused with "broken." No polling (refresh is a button), no
  radar tiles, no forecast modeling. **This layer is also the working demo of the real-time tier Mireye
  lacks** (we had to leave Mireye for NWS/USGS).

---

## 7. Data attribution — "how much of the decision does Mireye power?"

### 7.1 Per-segment "share of this decision's inputs"
- **What it is:** a stacked bar on each why-card splitting the decision into data-origin groups:
  **Mireye · VDOT traffic · Local records · Live stress**, summing to 100%.
- **Tells us:** exactly how much of *this* road's call Mireye's data drove vs. the gap-fill sources.
- **How (weighted by CONTRIBUTION, never by counting fields):** the Mireye-vs-traffic split of the score
  is exact (Mireye = every field served through Mireye `/v1/fetch`; VDOT traffic = `traffic_aadt`). Local
  records (the RSL treatment year) and Live stress are secondary decision dimensions credited a stated
  presentation weight; within RSL the record-vs-Mireye split is the real consumed-life fraction. **A
  prior-basis segment shows no Local-records slice** (nothing invented).

### 7.2 Countywide summary (the money slide)
- **Mireye-served data drives a median 78% of each risk decision (range 57–100%)** — *by actual
  contribution*.
- **A naive field-count would report 94%** — shown side-by-side to prove field-counting *overstates*
  Mireye (it counts fields we chose, not how much each moved the score). **This honesty is the point.**
- **Most influential Mireye fields countywide:** soil erodibility (K-factor), depth to bedrock, soil
  available water capacity. **The one input Mireye doesn't carry — VDOT traffic — is a median 21%** →
  *that gap is Mireye's product opportunity.*

---

## 8. Validation — does it predict reality? (LTPP)

- **What it is:** the model tested against **51 FHWA LTPP** long-term pavement sections with measured
  deterioration.
- **Tells us:** whether ground-risk actually correlates with faster deterioration — and it's presented
  **honestly**.
- **How / result:** top-quartile ground-risk sections deteriorated **~17% faster** — the right direction —
  but **not statistically significant** (permutation *p* ≈ 0.26, n = 51); a shuffled-label check confirms
  no leakage. **The honest read (a differentiator):** nothing was tuned to manufacture it, and *why it's
  weak is itself the finding* — LTPP sections are engineered Interstate/arterial subgrades, while the
  signal is strongest on the **local roads LTPP barely samples.**

---

## 9. County copilot (the agentic layer)

- **What it is:** a chat assistant that answers questions about the scored roads.
- **Tells us:** the data is queryable in plain English — and the assistant **refuses to fabricate**.
- **How:** an Anthropic (Claude) agent with a **tool-only** contract — every factual claim must come from
  a `query_scores` tool result, with its source. It gives the **RSL year range + basis** for "when"
  questions and **refuses a single exact date** (screening estimate, not a prediction); for prior-basis
  segments it says the data isn't in the record. Answers render as formatted markdown. (The Python build
  also has a `mireye_lookup` tool for live coordinate queries.)

---

## 10. Provenance & citations everywhere (the anti-LLM spine)
- **What it is:** every driver line, every live trigger, every value links to its federal source + fetch
  date.
- **Tells us:** this is *cited*, not *generated* — the core trust claim against "an LLM made this up."
- **How:** the `value, source, source_url, fetched_at, confidence` provenance row is enforced end-to-end;
  the UI renders the source link on every line. Confirmed **source-attribution drift** is disclosed
  (catalog documents `bedrock_depth_cm` as USDA STATSGO, but the live API returns NRCS gNATSGO /
  PELLETIER_DTB) — surfaced, not hidden.

---

## 11. Engineering rigor & honesty (talk-track gold)
- **`ERRORS.md`** — a running log of **every mistake the AI made and how it was caught** (e.g. a
  NaN-is-truthy crash, a fabricated-AADT citation, a spurious county-name join, a fabricated-treatment-year
  bug). This *is* the take-home's "what did the model get wrong?" answer, collected live.
- **Multi-agent adversarial review** — 14 agents across 5 dimensions caught 7 real issues (incl. the
  fabricated-treatment-year constitution violation), each verified before fixing.
- **Offline/airplane-mode demo** (Streamlit build) — proven to render with networking disabled.
- **Lean-code constitution** — no dead code, no single-caller abstractions, PRD is the authority.

---

## 12. Tech stack & deployment
- **Data pipeline:** Python (geopandas/shapely/pandas), Mireye API, ArcGIS Python API, httpx.
- **Web app:** Next.js + MapLibre GL (relative-risk map, why-card, RSL, live status, attribution) —
  **static-first** (pre-generated JSON, no server at runtime). **Copilot = one Vercel serverless
  function** calling Anthropic.
- **Deployed to production on Vercel:** https://web-one-rosy-32.vercel.app.
- **No database** — static JSON + stateless serverless by design (a DB would be unused weight).

---

## 13. Numbers to quote on stage
| Metric | Value |
|---|---|
| Scored segments (corridor) | **2,644** (of 15,643 county-wide) |
| Sample points | **7,877** |
| Mireye `/v1/fetch` calls | **~7,870** — **0 rate-limiting** (corridor-cost evidence) |
| Score range / median | 32.1 – 62.1 / 48.9 |
| Confidence grades | 0 A · 938 B · 1,706 C |
| **Mireye's share of each decision** | **median 78% by contribution** (vs 94% by naive field-count) |
| VDOT traffic (the gap) | median 21% |
| RSL estimated vs not | 106 (real treatment year) · 2,538 (honestly not estimated) |
| Plan vs risk | 265 high-risk unscheduled · 4 agree · 8 scheduled-lower-risk |
| LTPP validation | top-quartile ~17% faster, p ≈ 0.26, n = 51 (honest, not significant) |

---

## 14. Where Mireye fell short (your product-feedback slide)
1. **It's a point API, not a corridor API** — ~7,870 calls (≈1/point), 0 rate-limiting, to cover one
   corridor. A native **polyline endpoint** would open the whole linear-asset market (roads, pipes, rail,
   transmission).
2. **Missing fields** — no precipitation, no freeze-thaw cycle count (only snow-cover days as a proxy);
   both are top-tier deterioration drivers, filled from NOAA outside Mireye.
3. **No real-time tier** — the live "Right now" layer had to be built on NWS + USGS; it's a working demo
   of the tier Mireye lacks.
4. **Source-attribution drift** — documented-vs-live source mismatch on `bedrock_depth_cm` (matters when
   provenance is the product).

---

## 15. The closer
These counties — county & city public works — are the **same local governments now gating data-center
approvals** across Northern Virginia. Ground truth about soil, water, and drainage under infrastructure is
Mireye's flagship vertical; road deterioration is one wedge into a buyer Mireye already serves.
