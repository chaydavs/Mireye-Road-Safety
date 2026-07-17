# Walkthrough — the timed 30-minute arc

One-command launch: **`./run.sh`** (or `make demo`) — snapshots the live layer and opens the app in
`--demo` mode (airplane-mode safe: the live layer serves from `data/demo_snapshot/`). Record nothing;
this is the happy path to screen-capture yourself.

---

### Problem (0–3 min)
America has more failing road-miles than budget, and the decision that matters — *which segments to
treat next* — requires **predicting** deterioration, not measuring it. Symptom sensing (connected
vehicles) is commoditizing, but only on arterials; on **local roads the cause data is dark**, and
nobody joins USDA soil + USGS water + NOAA climate to road segments. (README §1–2.)

### The research question (3–6 min)
"Can we build the *cause* layer for local-road deterioration, entirely on Mireye, with every input
traceable to a federal source — and does it predict reality?" Scope: the Leesburg + Ashburn corridor
of Loudoun County, VA (2,644 segments).

### Live demo (6–14 min) — run `./run.sh`
- **Map (left):** segments colored by deterioration risk, worst in red. This is the ranked output a
  public-works engineer acts on.
- **Click the top segment → why-card (right).** *Read the citations aloud:* every driver line carries
  a **federal source URL + fetch date** (USDA soil, USGS landslide, FEMA flood, VDOT traffic). This is
  the anti-LLM point — cited, not invented.
- **RSL on the same card:** *"estimated to reach poor condition 2027–2032 (grade C; last treated 2017
  per VDOT paving)"* — a transparent year **range** with its basis, never a fake single date.
- **Copilot (bottom):** ask *"why is the top segment ranked first?"* (cited answer), then
  *"when will it reach poor condition?"* (gives the range + basis), then *"give me the exact date"*
  (**it refuses** — screening-grade estimate, not a prediction).
- **"Right now" toggle:** recolors the map to segments under **current stress** — a gauge running
  above its own median, a flood alert, or a wet week — each with a timestamped, cited trigger. On a
  calm day it says *"no active stress"* rather than pretending. (fragility × current stress.)

### What worked — the LTPP validation chart (14–18 min)
`output/ltpp_validation.png`. Against 51 FHWA LTPP sections, top-quartile ground-risk sections
deteriorated **~17% faster** — the right direction — but **not statistically significant** (permutation
*p* ≈ 0.26, n = 51); the shuffled-label check confirms no leakage. **The honest read:** we did not
tune anything to manufacture it, and *why it's weak is the finding* — LTPP sections are Interstate/
arterial with engineered subgrades, while our signal is strongest on the **local roads LTPP barely
samples.**

### Where Mireye fell short (18–22 min) — `docs/shortfalls.md`
- **Audit numbers:** ~**7,870 point calls to cover one corridor** (≈ one call per point), **0
  rate-limiting** — the cost scales with point density, not corridor length.
- **Missing fields:** no **precipitation**, no **freeze-thaw cycle count** (only snow-cover days as a
  proxy) — both top-tier deterioration drivers, filled from NOAA.
- **Corridor critique (the headline):** Mireye is a **point API**; a native **polyline endpoint**
  would open the whole linear-asset market (roads, pipes, rail, transmission).
- **No real-time tier:** the moment we needed "what's happening now," we had to leave Mireye for NWS +
  USGS — the live layer is a working demo of the tier Mireye lacks.
- **Source-attribution drift (confirmed):** the catalog documents `bedrock_depth_cm` as "USDA
  STATSGO," but the live API returns **`NRCS_gNATSGO` / `PELLETIER_DTB`** — a documented-vs-live
  mismatch that matters when provenance is the product.

### The closer (22–25 min)
These counties — county and city public works — are the **same local governments now gating
data-center approvals** across Northern Virginia. Ground truth about soil, water, and drainage under
infrastructure is exactly Mireye's flagship vertical; road deterioration is one wedge into a buyer
Mireye already serves.

### Questions (25–30 min)
Open floor. Back-pocket: the AI error log (`ERRORS.md`) — every mistake caught and how (the 7-hour
httpx hang, the fabricated-AADT citation, the spurious county-name join, the negative-deterioration
methodology bug) — is the "what did the model get wrong" answer, collected live.
