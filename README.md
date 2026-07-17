# Subgrade

**Cited road-deterioration risk for local road networks, built on Mireye.**

Subgrade takes a county's road network and returns a ranked, per-segment forecast of *which roads
will deteriorate fastest and why* — with every input traceable to a federal source. It is the
**cause layer** for pavement management: not "which roads are rough today" (increasingly
commoditized by connected-vehicle sensing) but "which roads sit on ground conditions that make them
fail early."

> **Live app: https://subgrade-roads.vercel.app** — see [§3 Using the tool](#3-using-the-tool).
>
> Status: working end-to-end prototype on the Leesburg + Ashburn corridor of Loudoun County, VA.
> Built as a Mireye take-home over 8 disciplined sessions (see `WORKLOG.md`); every model mistake
> caught along the way is logged in `ERRORS.md`.

---

## 1. The problem and who it's for

America has far more failing road-miles than budget. The 2025 ASCE Report Card grades roads **D+**
with a **$684B** ten-year gap. Timing dominates the economics: a dollar of preventive maintenance
saves **$4–$10** of later rehabilitation. So the decision that matters is *which segments to treat
next* — and that requires **predicting** deterioration, not just measuring it.

The deployed prediction models are deterministic and omit the localized factors that provably drive
failure: **subsurface soil, drainage, and climate**. Moisture is the single most important predictor
of pavement structure; expansive soils add 20–30% to rehab cost. Yet **no agency assembles the cause
data** — joining USDA soil, USGS water, FEMA flood, and NOAA climate to road segments — and the
local roads that make up most US road-miles are still rated by windshield survey.

**Primary user:** county and city public-works directors and engineers — the owners of most US
road-miles — who today prioritize repaving by visual ratings and complaints. Their mandate
(risk-based asset management) is real; their cause data is not.

## 2. Why Mireye (vs the alternatives)

- **vs Google Maps** — Google has *zero* subsurface, soil, drainage, or hazard data. It does not
  know what is under the road.
- **vs a GIS analyst** — reproducing one county's inputs means stitching SSURGO, FEMA NFHL, NOAA
  normals, NWI, and USGS terrain rasters: days of work per county, versus **one API call per point**.
  Mireye is the assembly step, productized.
- **vs a generic LLM** — an LLM will confidently invent a soil type and cite nothing. A county
  engineer defending a budget to a board needs *"USDA says poorly-drained silty clay,"* with a
  **source URL and fetch date**. Mireye's provenance tagging is the load-bearing feature here — it is
  why every why-card line in this build carries a federal source link, and why the copilot refuses
  what its tools can't source.
- **Mireye-native differentiator: confidence propagation.** Mireye caps confidence on gap-filled
  soil (STATSGO vs SSURGO); Subgrade carries that through, so every segment score has an A/B/C
  confidence grade. The output is honest by construction — in Loudoun that means **no A grades**,
  because the soil is reconnaissance-scale everywhere.

## 3. Using the tool

### The live app — no install

**→ https://subgrade-roads.vercel.app**

The corridor of Loudoun County, VA, ranked and explained. Four things to do:

1. **Priority map** — roads colored by *relative* deterioration risk (worst = red). **Click any road**
   for its cited why-card: the **top-5 drivers** (each linked to its federal source), the **RSL** year
   estimate (or an honest "no treatment-year data" when there isn't one), and the **"share of this
   decision's inputs"** bar (how much Mireye's data vs VDOT traffic drove that road's score).
2. **Right now (live stress)** toggle — overlays *today's* stress: active NWS flood/winter alerts and
   USGS gauges running above their own median. The status line always states the counts, so "calm" is
   never confused with "broken." Fragility × current stress.
3. **Ablation study** view — toggle **Traffic-only ↔ + Mireye** to watch the priority list reorder
   (**72% of the worst-priority roads change** when Mireye's ground data is added). The **"Roads Mireye
   reveals"** list is clickable — each opens that road's why-card. This is the proof that Mireye
   *reorders* priorities, not just adds fields.
4. **County copilot** — ask about the roads in plain English (e.g. *"why is the top segment ranked
   first?"*, *"which 3 are worst?"*). Answers are tool-grounded, cited, and refuse to fabricate a
   single-year RSL or invent data.

### Run / regenerate it locally

Requires **Python 3.11** and **Node 18+**. Put a Mireye token in `.env` as `MIREYE_TOKEN=...` (and
`ANTHROPIC_API_KEY=...` for the copilot). `mdbtools` (`brew install mdbtools`) is only needed for the
optional LTPP validation.

```bash
# 1. Python data pipeline
uv venv --python 3.11 .venv && uv pip install -r <(echo "geopandas shapely pyproj pandas pyarrow httpx folium streamlit streamlit-folium anthropic matplotlib arcgis")
.venv/bin/python src/network.py       # road network + AADT join      -> data/segments.parquet, points.parquet
.venv/bin/python src/fetch.py         # cache-backed Mireye fetch      -> provenance store + audit.json
.venv/bin/python src/score.py         # scoring engine + Folium map    -> data/scores.parquet, output/map.html
.venv/bin/python src/paving.py        # VDOT paving via ArcGIS API     -> data/segment_treatment.parquet
.venv/bin/python src/service_life.py  # RSL year ranges                -> annotates scores.parquet
.venv/bin/python src/live.py          # live NWS/USGS stress layer      -> data/watchlist.parquet   (optional)
.venv/bin/python src/ablation.py      # traffic-only vs +Mireye        -> data/ablation.parquet
.venv/bin/python src/export_web.py    # -> web/public/data/*.json for the web app

# 2. The web app (Next.js + MapLibre; the copilot is one Vercel serverless route)
cd web && npm install && npm run dev   # http://localhost:3000
#   deploy:  vercel        (then set ANTHROPIC_API_KEY in the Vercel project)

# Legacy one-page Streamlit app (still works; offline/airplane-mode capable via `./run.sh`):
.venv/bin/streamlit run src/app.py
```

Validation (optional): `.venv/bin/python src/validate.py` runs the LTPP test and writes
`output/ltpp_validation.png`. Full feature + methodology reference for a presentation:
[`docs/PRESENTATION.md`](docs/PRESENTATION.md).

## 4. What worked

- **End-to-end, at scale, cached.** A cache-backed, resumable fetch loop pulls Mireye's ground +
  climate fields for the corridor, storing every value with full provenance (`value, source,
  source_url, fetched_at, confidence`). Re-runs never re-spend the rate limit.
- **A deployed, interactive map a county engineer could act on** — the live web app
  ([subgrade-roads.vercel.app](https://subgrade-roads.vercel.app)): segments colored by *relative*
  risk, with **cited why-cards** where *every driver line traces to a provenance row* (no provenance
  row, no sentence). The top-5 drivers are ranked by their actual contribution to that segment's score.
- **A remaining-service-life (RSL) forecast.** A transparent *rate-stretch* (`effective_life =
  expected_life / relative_rate`) turns the fragility score into a **year range** for when a segment
  reaches poor condition — sourced from HPMS/VDOT treatment years, **never fabricated**. When no real
  treatment year exists, the card says *"no treatment-year data; RSL not estimated"* rather than invent
  a past window; a single-year answer is treated as a bug.
- **VDOT paving integration + plan-vs-risk.** The county's completed/planned paving is pulled via the
  **ArcGIS Python API** (anonymous, documented ops only; **contact fields dropped and asserted absent**),
  spatially joined geometry-first, and compared to the risk ranking: **265 top-decile-risk segments are
  *not* on the paving plan** — framed as a lens for a conversation, not an error claim.
- **A live "Right now" layer.** NWS flood/winter alerts + USGS gauges running above their *own* median
  overlay today's stress on the static fragility map (**fragility × current stress**) — and double as a
  working demo of the real-time tier Mireye lacks.
- **Honest data attribution.** Every why-card shows the **share of the decision Mireye's data drove**,
  weighted by actual contribution: **median 78% Mireye vs a median 21% VDOT traffic** — with the naive
  **field-count figure (94%) shown alongside** so it can't mislead.
- **An ablation study that proves Mireye is load-bearing.** Strip every Mireye-served field and score on
  the county's own VDOT traffic alone: **72% of the worst-priority repaving list reorders** (Spearman
  **0.16**), and **1,110 of 2,644 roads are invisible to a traffic-only model**. This directly answers
  *"isn't Mireye's share high just because you built the score around its fields?"* by **measuring** how
  much the county's priorities change — a decision-divergence test, never an accuracy claim. Live in the
  app's **"Ablation study"** view (toggle *Traffic-only ↔ + Mireye* to watch the roads reorder).
- **A county copilot** wired to the scored dataset that answers "why is this segment ranked first?"
  (cited, rendered markdown) and **refuses** "which segment will fail in March 2027?" — because the data
  cannot answer it. (Transcripts in `WALKTHROUGH.md`.)
- **Empirical validation against federal ground truth (LTPP).**

![LTPP validation](output/ltpp_validation.png)

  For 51 LTPP pavement sections (Virginia + climate-adjacent states), we compared each section's
  *measured* deterioration rate (roughness slope over time, within one construction cycle) to
  Subgrade's ground-risk score, **controlling for age and traffic**. Top-quartile ground-risk
  sections deteriorated **~17% faster** (median 0.0127 vs 0.0109 m/km/yr) — the right direction — but
  the effect is **not statistically significant** (permutation *p* = 0.26, n = 51). A shuffled-label
  permutation test confirms **no pipeline leakage** (shuffled coefficient ≈ 0). We did **not** tune
  thresholds to manufacture significance. Why the signal is weak is itself the finding: LTPP sections
  are Interstate/arterial roads with *engineered, often soil-stabilized* subgrades — little native
  ground variation — while Subgrade's signal is strongest on the **local roads LTPP barely samples.**

## 5. Where the data fell short

The full, agent-drafted report is **[`docs/shortfalls.md`](docs/shortfalls.md)** (generated from the
audit log and human-edited). The headline gaps, confirmed by this build:

- **No corridor primitive** (see §6 — the headline product feedback).
- **No precipitation field** — the climate layer has temperature/snow/solar but not rainfall, a
  top-tier deterioration driver. (Fillable from NOAA; not integrated here.)
- **No freeze-thaw cycle count** — only snow-cover days as a proxy for the cycling that cracks
  pavement.
- **SSURGO/STATSGO soil is reconnaissance-scale** — scores are screening-grade, not geotech-grade,
  and Mireye's confidence caps make that visible (no A-grade segments in Loudoun).

**Known limitations of this build** (consciously deferred; see [`FUTURE.md`](FUTURE.md)): **bridge
spans are not excluded** (PRD §5 — a robust filter needs a bridge-distance field Mireye doesn't
expose); the HPMS spot check, NOAA precip/freeze gap-fill, and truck-share uplift are not wired in.

## 6. The corridor critique (headline product feedback)

**Mireye is a point API; roads, pipes, rail, and transmission are lines.** This build simulates
corridor coverage by *spraying point queries* across the network — thousands of them — which is both
a **cost driver** (calls scale with point density, not corridor length) and a **fidelity loss**
(everything between sample points is interpolated, not measured).

The evidence is this build's own call counts (from `data/audit.json`):

> **7,870 Mireye `/v1/fetch` calls to cover 7,877 sample points** across the Leesburg + Ashburn
> corridor — essentially *one call per point* (only 7 coordinates deduped). Cost scales with point
> density, not corridor length. **0 rate-limiting** observed across the whole run; the binding
> constraint is per-call latency (2–6 s cold), not quota — which is why a full Loudoun County run was
> scoped down to this corridor. And this corridor is a fraction of one county: the point-spray
> approach does not scale to a route network without a native line primitive.

A native **polyline endpoint** — post a linestring, get sampled fields back — would open the
linear-infrastructure asset-management market and remove both the cost and the fidelity penalty. This
is the single highest-value ask for a linear-asset use case, and the build itself is the evidence.

---

*Scope, decisions, and kill-criteria per session: `WORKLOG.md`. Caught model mistakes and how they
were found: `ERRORS.md`. Demo script: `WALKTHROUGH.md`. Deferred work: `FUTURE.md`.*
