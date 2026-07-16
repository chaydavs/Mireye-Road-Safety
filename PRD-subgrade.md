# PRD: Subgrade

**Cited road deterioration risk for local road networks, built on Mireye**

| | |
|---|---|
| Author | Sai Chaitanya (Chay) Davuluri |
| Status | Draft v0.1 |
| Date | July 2026 |
| Context | Mireye take-home assignment: find a real-world use case, build it end to end, walk through it on a 30-minute call |

Working name "Subgrade" refers to the soil layer beneath a road, the thing this product makes visible. Rename freely.

---

## 1. One-liner

Subgrade takes a county's road network and returns a ranked, per-segment forecast of which roads will deteriorate fastest and why, with every input traceable to a federal source. It is the cause layer for pavement management: not "which roads are rough today" (increasingly commoditized by connected vehicle sensing) but "which roads are sitting on conditions that make them fail early."

## 2. Problem

America has far more failing road miles than budget. The 2025 ASCE Report Card grades roads D+ with a $684B ten-year funding gap and roughly 39% of major roads in poor or mediocre condition ([ASCE 2025](https://infrastructurereportcard.org/cat-item/roads-infrastructure/)). Timing dominates the economics: a dollar of preventive maintenance saves $4 to $10 of later rehabilitation, and Michigan DOT found skipping prevention would have cost 8x more ([FHWA](https://www.fhwa.dot.gov/publications/focus/97sep/97mich.cfm)). So the decision that matters is which segments to treat next, and that decision requires predicting deterioration, not just measuring it.

Three findings from the literature define the gap this product fills:

**The deployed prediction models are outdated and systematically wrong.** The most recent field review (Feb 2026) finds practice anchored to deterministic models and names limited data availability as the top persistent challenge ([IJPE 2026](https://www.tandfonline.com/doi/full/10.1080/10298436.2026.2633306)). Deterministic models fail to capture uncertainty, leading agencies to underestimate costs and overestimate post-repair condition ([Scientific Reports 2025](https://www.nature.com/articles/s41598-025-92469-9)), and they omit localized factors such as climate, traffic loads, and construction variability ([IJTST 2025](https://www.sciencedirect.com/science/article/pii/S2046043025000802)).

**The omitted factors are ground and climate variables, and they provably drive deterioration.** Subgrade soil properties plus environment predict required pavement structure at 0.917 accuracy, with moisture the single most important predictor ([Scientific Reports 2025](https://www.nature.com/articles/s41598-025-13852-0)). FHWA's own LTPP analysis quantifies moisture's role in fatigue cracking ([FHWA-HRT-20-006](https://www.fhwa.dot.gov/publications/research/infrastructure/pavements/ltpp/20006/)). Rehab runs 20 to 30% more expensive on expansive soils and 10 to 20% more on frost-susceptible soils ([ASCE 2025](https://www.asce.org/publications-and-news/civil-engineering-source/article/2025/04/28/roads-that-last-a-century-not-a-season)). Models that omit soil swelling and drainage show their largest regional errors exactly where those factors dominate ([IJTDI 2025](https://www.acadlore.com/article/IJTDI/2025_9_4/ijtdi090403)).

**Nobody is responsible for assembling the cause data.** Federal rule 23 CFR 490 requires states to measure only surface symptoms (roughness, cracking, rutting) and only on the Interstate and NHS ([eCFR](https://www.ecfr.gov/current/title-23/chapter-I/subchapter-E/part-490)). Local governments, which own the large majority of road miles, still rate roads by windshield survey ([Infrastructures 2025](https://doi.org/10.3390/infrastructures10090248)). No agency anywhere has the job of joining USDA soil, USGS water, and NOAA climate to road segments.

Meanwhile, condition sensing is commoditizing: connected vehicle fleets now estimate roughness at 93 to 100% coverage on arterials ([Future Transportation, Feb 2026](https://doi.org/10.3390/futuretransp6010047)) but collapse to 37% on local streets and 14% in rural counties. The symptom side is being solved; the cause side is open, and the local roads are dark.

## 3. Users

**Primary user:** county and city public works directors and engineers, the owners of most US road miles, who today prioritize repaving by visual ratings and complaints. Their mandate is real (risk-based asset management, [FHWA](https://www.fhwa.dot.gov/asset/)); their data is not.

**Secondary users:** civil engineering consultancies performing pavement management contracts for local agencies; state DOT districts covering secondary systems; long-term road concession operators whose profit depends on durability.

**Explicitly not the buyer:** paving contractors (misaligned incentives). This tool exists partly so the payer has a source of ground truth independent of the people selling asphalt.

## 4. Why Mireye specifically

- **vs Google Maps:** Google has zero subsurface, soil, drainage, or hazard data. It does not know what is under the road.
- **vs a GIS analyst:** reproducing one county's inputs means stitching SSURGO, FEMA NFHL, NOAA normals, NWI, USGS landslide and terrain rasters, days of work per county versus one API call per point. Mireye is the assembly step, productized.
- **vs a generic LLM:** an LLM will confidently invent a soil type and can cite nothing. A county engineer defending a budget to a board needs "USDA says poorly drained expansive clay," with a source URL and fetch date. Mireye's provenance tagging is the load-bearing feature, not a nicety.
- **Mireye-native differentiator:** confidence propagation. Mireye caps confidence on gap-filled soil values (STATSGO vs SSURGO); Subgrade carries that through so every segment score has a confidence grade. The output is honest by construction.

## 5. Goals and non-goals

**Goals**

1. End-to-end working pipeline on one Virginia county's secondary/local road network, touching Mireye /fetch at scale and /ask for explanations.
2. A ranked segment output a real county engineer could act on, with a cited "why" per segment.
3. Empirical validation against federal ground truth (LTPP), not just literature weights.
4. A rigorous "where Mireye falls short" report generated as a byproduct of the build.

**Non-goals**

- Not a condition detector. Connected vehicles and AI cameras own that; Subgrade is the cause and forecast layer beneath them.
- Not a safety or crash predictor. The defensible claim is deterioration risk prioritization; do not frame as "road safety" outcomes.
- Not corridor-native (yet). Mireye is a point API; corridor coverage is simulated by sampling. The gap is documented as product feedback, not solved here.
- No bridges. Bridges deteriorate under a different regime (NBI inspection program); flagged via nearest_bridge_name and excluded.

## 6. System architecture

Five stages. Sources feed an assembly layer, assembly feeds scoring, scoring feeds three outputs.

```
[VDOT centerlines + AADT]   [Mireye /fetch]   [NOAA normals + LTPP labels]
            \                     |                     /
             v                    v                    v
        ┌─────────────────────────────────────────────────┐
        │ Assembly: segment, sample every 300 m, cache,    │
        │ provenance store, null/confidence audit log      │
        └─────────────────────────────────────────────────┘
                                  |
                                  v
        ┌─────────────────────────────────────────────────┐
        │ Scoring engine: ground x climate x traffic,      │
        │ 0-100, confidence carried through                 │
        └─────────────────────────────────────────────────┘
                    |             |              |
                    v             v              v
          [Ranked risk map] [Cited why card] [Audit report]
```

### Stage 1: Inputs

**Road network.** Centerlines from Virginia's open roads portal, filtered to secondary and local systems, bridge spans excluded. Segments of ~500 m; 3 sample points per segment at ~300 m spacing (positions snapped to the centerline); per-field median across the segment's points so one bad snap cannot poison a segment.

**Traffic.** VDOT AADT and truck share joined by route ID (spatial join fallback). Local roads without counts fall back to Mireye's housing_units_density_per_km2 as a usage proxy, with an explicit confidence downgrade.

**Gap fillers and labels.** NOAA climate normals supply the two variables Mireye lacks (annual precipitation, freeze-thaw proxy). LTPP is the calibration/validation answer key, not a runtime input.

### Stage 2: Assembly (where the engineering lives)

- Fetch loop over sample points against Mireye /v1/fetch with an explicit field list (Section 8).
- Cache keyed by rounded (lat, lon) in SQLite/parquet; re-runs never re-spend the rate limit.
- Provenance store: per field, retain value, source, source_url, fetched_at, confidence, and null_meaning-informed status. This store powers the why-cards and the audit report.
- Null and confidence audit log appends automatically per call; the "where Mireye falls short" writeup generates itself.
- QA triage agent: when a point's nearest_road_* disagrees with its segment, or a null needs interpreting against the field's null_meaning, an agent decides re-snap, discard, or confidence downgrade, and logs its reasoning. Judgment calls only; the bulk fetch itself stays deterministic.
- Field-name validation against the live catalog (api.mireye.com/v1/meta/fields) before any fetch, which is also the standing guard against AI-assisted coding hallucinating field names.

### Stage 3: Scoring engine

Version 1 is a transparent weighted score. Deliberately not a black box: a county engineer and a Mireye founder should both be able to read it.

```
Risk = 100 x ( 0.30*W + 0.20*S + 0.20*C + 0.20*T + 0.10*G )
```

| Factor | Weight | Composed from (Mireye fields unless noted) | Weight justification |
|---|---|---|---|
| W, water | 0.30 | soil_drainage_class, soil_ponding_frequency_class, within_floodplain_polygon, fema_flood_zone, surface_water_permanence_pct, nearest_wetland_distance_m, soil_available_water_capacity, soil_hydrologic_group | Moisture was the #1 predictor in Sci Rep 2025; FHWA-HRT-20-006 quantifies moisture-driven fatigue cracking |
| S, soil movement | 0.20 | soil_shrink_swell_class, soil_erodibility_k_factor, bedrock_depth_cm | ASCE: expansive soils add 20-30% to rehab cost |
| C, climate load | 0.20 | mean_annual_snow_cover_days, days_above_32c_annual_count, mean_annual_dry_bulb_temperature_degc, plus NOAA precipitation and freeze proxy | SHAP ranking in Infrastructures 2025 cold-regions model (precipitation, freeze-thaw among top drivers) |
| T, traffic stress | 0.20 | VDOT AADT (log-normalized) with truck-share uplift; fallback housing_units_density_per_km2 | AADT among top predictors in IJTDI 2025 |
| G, terrain | 0.10 | slope_degrees, landslide_susceptibility_index | Slope/instability drives embankment and drainage stress |

Each component maps to 0-1 via published-threshold lookup tables, for example soil_drainage_class: Very poorly/Poorly drained = 1.0, Somewhat poorly = 0.7, Moderately well = 0.4, Well = 0.2, Excessively = 0.1. Missing components drop out of a factor's average rather than defaulting to zero, and their absence lowers the segment's confidence grade.

**Confidence propagation.** Segment confidence = minimum confidence among load-bearing inputs (W and S fields especially). Mireye's STATSGO-capped soil values and AADT fallbacks visibly downgrade a segment. Output grades: A (all core inputs high confidence), B (any medium), C (any low or proxy).

**Stretch (only if time remains):** fit the five weights by regression against LTPP deterioration rates instead of taking them from literature. V1 ships with literature weights either way.

### Stage 4: Validation

1. **LTPP test (calibration-grade evidence).** Pull 100-200 LTPP sections (Virginia and neighboring climate zones), compute each section's measured deterioration rate (IRI slope over time) from the Analysis Ready Datasets, fetch Mireye's ground fields at each section's coordinates, and test whether high ground-risk sections deteriorated faster, controlling for age and traffic (which LTPP records). Deliverable: one chart, ground score vs measured deterioration.
2. **HPMS spot check.** Rank-correlate Subgrade scores against published roughness on the county's federal-aid roads. Expect weak-to-moderate positive (current condition also reflects age and maintenance history, which we do not model); state that honestly.

### Stage 5: Outputs

- **Ranked risk map.** County road network colored by score, worst first (Folium/Leaflet HTML). Fallback if time-boxed: ranked table in a notebook. Working beats pretty.
- **Cited why card.** Click a segment: "Score 84 (confidence B). Drivers: poorly drained silty clay subgrade (USDA SSURGO, fetched 2026-07-xx), high shrink-swell (USDA), floodplain crossing (FEMA NFHL), 34 snow-cover days (NSIDC), 4,800 vehicles/day (VDOT 2025)." Every line links to its source. Generated via the provenance store; top segments additionally run through /v1/ask for a plain-English narrative, exercising both endpoints.
- **Audit report.** Null rate per field, confidence distribution, fields Mireye lacks for this use case, actual API call counts, and the corridor critique (Section 10).

### Agentic layer

Design principle: agentic where judgment lives, deterministic where the work is bulk. The fetch loop is a script (an agent looping over 5,000 tool calls is slower, costlier, and less reliable); agents own the four judgment tasks, and together they exercise all three Mireye surfaces (/fetch, /ask, MCP).

1. **QA triage agent** (Stage 2, above): snap disputes and null interpretation.
2. **Why-card agent** (Stage 5): reads the provenance store for a segment, calls Mireye /v1/ask for the plain-English narrative, and composes the cited explanation under a hard rule: every claim must trace to a stored provenance row; no citation, no sentence.
3. **Audit narrator** (Stage 5): reads the null/confidence log and drafts the "where Mireye falls short" report for human editing.
4. **County copilot** (UI, below): an agent with two tools, the scored segment dataset and Mireye's MCP server (hosted at api.mireye.com/mcp or local via uvx mireye-mcp). It answers questions like "why is Route 603 ranked above Route 611," "show only truck-route segments," or "re-check this segment fresh," pulling live cited data on demand. This is Mireye's own "infrastructure for physical world AI agents" positioning, demonstrated on a user they have not named.

### UI

One Streamlit page, deliberately thin (the assignment: a rough prototype that genuinely works beats a beautiful mockup):

- Left: the Folium risk map, county roads colored by score.
- Right: the cited why-card for the selected segment (from the why-card agent), every line linking to its federal source.
- Bottom: a chat box wired to the county copilot agent. This is how the agentic approach is visible in the demo rather than claimed in prose.

Stack: Streamlit + streamlit-folium, all Python. If time compresses, the chat box is the last thing cut, the map and why-card survive as static outputs.

## 7. Mireye field list (the /fetch payload per point)

Core predictors (17): soil_drainage_class, soil_shrink_swell_class, soil_available_water_capacity, soil_ponding_frequency_class, soil_hydrologic_group, soil_restrictive_layer_depth_cm, soil_restrictive_layer_kind, soil_erodibility_k_factor, bedrock_depth_cm, slope_degrees, landslide_susceptibility_index, within_floodplain_polygon, fema_flood_zone, surface_water_permanence_pct, mean_annual_snow_cover_days, days_above_32c_annual_count, mean_annual_dry_bulb_temperature_degc.

Supporting and QA (~18): elevation, aspect_cardinal, soil_map_unit_name, intersects_nhd_area, nearest_flowline_name, nearest_waterbody_name, huc_12_name, intersects_wetland, nearest_wetland_distance_m, wetlands_within_100m_count, flood_zone_subtype, tree_canopy_pct, lcms_class, land_use_class, ndvi_change_5y, nearest_bridge_name, nearest_road_name, nearest_road_class, nearest_road_surface, nearest_road_distance_m, roads_within_500m_count, housing_units_density_per_km2, political_county, political_locality, tract_geoid, drought_category.

nearest_road_* fields serve as snap QA: if the nearest road's name/class disagrees with the segment being sampled, the point is re-snapped or discarded.

## 8. Open source resources

### Data

| Resource | What it supplies | Link | License/access |
|---|---|---|---|
| Mireye API | 170+ provenance-tagged fields per coordinate; /v1/fetch, /v1/ask, MCP server | https://www.mireye.com/docs and https://api.mireye.com/v1/meta/fields | API token (free signup per assignment) |
| VirginiaRoads open data | Road centerlines and VDOT GIS layers | https://www.virginiaroads.org/datasets | Open |
| VDOT traffic counts | AADT and truck share, statewide, spreadsheet/shapefile/feature service | https://www.vdot.virginia.gov/doing-business/technical-guidance-and-support/traffic-operations/traffic-counts/ and https://data.virginia.gov/dataset/vdot-bidirectional-traffic-volume | Open |
| FHWA LTPP InfoPave | ~2,500 monitored sections since 1987; Analysis Ready Datasets for climate, traffic, materials, performance; calibration/validation ground truth | https://infopave.fhwa.dot.gov/ | Open |
| FHWA HPMS shapefiles | Measured roughness (IRI) on federal-aid roads per state, for the spot check | https://www.fhwa.dot.gov/policyinformation/hpms/shapefiles.cfm | Open |
| NOAA NCEI US Climate Normals | Annual precipitation and freeze proxies (the two variables Mireye lacks) | https://www.ncei.noaa.gov/products/land-based-station/us-climate-normals | Open |
| OpenStreetMap / Geofabrik extracts | Fallback centerlines if VDOT geometry is awkward | https://download.geofabrik.de/ | ODbL |
| Overture Maps | Reference for road schema (also Mireye's own road source) | https://overturemaps.org/ | Open |
| NWS API | Active flood/flash-flood/winter-storm alert polygons; last-7-day precipitation | https://api.weather.gov (no key; User-Agent header required) | Open |
| USGS Water Services | Real-time instantaneous stream gauge discharge for the live stress layer | https://waterservices.usgs.gov (no key) | Open |
| VDOT paving program | Recently treated routes and treatment year, for service-life age | Published annually on vdot.virginia.gov | Open |
| FHWA HPMS (additional items) | Year of last improvement/construction as a model input (previously validation-only); verify item names in the Field Manual | https://www.fhwa.dot.gov/policyinformation/hpms/fieldmanual/ | Open |
| FHWA pavement preservation guidance | Treatment lifespan ranges (chip seal, overlay, reconstruction); a cited lookup table, not an API | Literature values, source-commented per row in code | Open |

### Software (all open source)

| Tool | Role |
|---|---|
| Python 3.11+, Jupyter | Pipeline and analysis |
| GeoPandas, Shapely, pyproj | Centerline handling, segmentation, point sampling, spatial joins |
| pandas, pyarrow | Tabular processing, parquet cache |
| requests / httpx | Mireye API client with retry/backoff |
| SQLite (stdlib) | Response cache and provenance store |
| Folium (Leaflet.js) + streamlit-folium | Interactive risk map |
| Streamlit | One-page app: map, why-card, copilot chat |
| Anthropic SDK / MCP client, mireye-mcp (uvx) | The four agents; copilot connects to Mireye's MCP server |
| scipy / scikit-learn (stretch) | LTPP correlation tests, optional weight calibration |

### Research underpinning the weights (all linked in Section 2 plus)

- ML in pavement management review: https://www.mdpi.com/2412-3811/9/12/213
- Cold-regions deterioration model, variable table and SHAP: https://www.mdpi.com/2412-3811/10/8/212
- Pavement degradation models overview: https://onlinelibrary.wiley.com/doi/10.1155/2022/7783588
- Pavement moisture prediction: https://www.sciencedirect.com/science/article/pii/S2097049825000186
- Digital twin PMS limitations: https://arxiv.org/pdf/2511.02957
- CV roughness statewide evaluation: https://doi.org/10.3390/infrastructures10090248
- CV roughness vs PCI comparison: https://doi.org/10.3390/futuretransp6010047

## 9. Build plan (seven sessions)

1. **Probe.** API token; ~100-call probe to learn rate limits, latency, and per-call cost behavior; pick the county based on VDOT data quality there. Everything else scopes off this session's answer.
2. **Network.** Centerlines, filtering, segmentation, sampling, AADT join. Deliverable: sample-point table with traffic attached.
3. **Fetch.** Cache-backed fetch loop with provenance store; run the county; review the null audit.
4. **Score and map.** Scoring v1 with literature weights; Folium map; why-card agent over the provenance store plus /ask for the top 20 segments.
5. **Validate.** LTPP test and HPMS spot check; tune threshold tables (not weights) if mappings are clearly miscalibrated.
6. **App and agents.** Streamlit page (map, why-card, chat); county copilot wired to the scored dataset and Mireye's MCP server; audit narrator drafts the shortfall report.
7. **Package.** README mirroring the assignment rubric (problem, user, why Mireye, what worked, what fell short), audit report, AI error log, walkthrough script.

## 10. Where Mireye falls short (pre-registered findings, to be confirmed by the build)

1. **No corridor primitive.** Mireye is a point API; roads, pipes, rail, and transmission are lines. Corridor coverage is simulated here by spraying point queries (thousands per county), which is costly and lossy. A polyline endpoint (post a linestring, get sampled fields back) would open the linear-infrastructure asset management market. This is the headline product feedback, and the build itself is the evidence.
2. **No precipitation field.** The climate layer carries temperature, snow, solar, and wind, but not annual rainfall, despite precipitation ranking among the top deterioration drivers. Filled from NOAA in this build.
3. **No freeze-thaw cycle count.** Snow-cover days is only a proxy for the freeze-thaw cycling that damages pavement. Also fillable from NOAA normals.
4. **SSURGO scale caveats.** Reconnaissance-scale soil mapping with STATSGO gap-fill; Mireye's confidence caps handle this honestly, and Subgrade propagates them, but users should know segment scores are screening-grade, not geotech-grade.
5. **No real-time tier.** Mireye serves slow truth: its freshest fields refresh daily and most refresh monthly to yearly, which is correct for soil and flood zones but leaves no live layer at all. The moment this use case needed "what is happening right now" (active flood warnings, current gauge levels, this week's rain), the pipeline had to leave Mireye for NWS and USGS directly. A live exposure tier, or webhooks on value change, is a natural product extension; the watch list in this build is a working demo of what it would power.
6. **Source-attribution drift (confirmed by the build).** The live API stamps sources that do not match the documented catalog. Verified example: `bedrock_depth_cm` is documented as "from USDA STATSGO," but live responses in this corridor return **`NRCS_gNATSGO`** (shallow) and **`PELLETIER_DTB`** (deep-bedrock) — never `USDA_STATSGO`. Because provenance is load-bearing here, a documented-vs-live source mismatch matters: a why-card citing the catalog source would be wrong. Subgrade stores the *live* source, so its citations are correct, but the catalog should be reconciled with what `/v1/fetch` actually returns.
7. To be appended from the audit log: measured null rates per field, rate-limit behavior at pipeline volume, and any further surprises.

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Rate limits make county-scale fetch infeasible | Session 1 probe before committing; scope to one road system or district; cache aggressively; 3 points/segment not 10 |
| AADT joins fail on local roads | housing-density fallback with confidence downgrade, disclosed per segment |
| LTPP signal is weak after controlling for age/traffic | Report it honestly; the submission survives on the pipeline, the audit, and the corridor critique; the thesis adjusts rather than the data being tortured |
| Snapped points land on the wrong road | nearest_road_* QA check per point; discard or re-snap mismatches |
| AI coding assistant hallucinates field names or misreads nulls | All field names validated against /v1/meta/fields pre-fetch; null handling driven by each field's null_meaning; every caught error logged (interviewers will ask) |
| Overclaiming | Frame as deterioration risk prioritization, never safety/crash prediction |
| Over-agentification (agent doing bulk fetch) | Hybrid split enforced: scripts move data, agents make judgment calls and explain; be ready to defend this on the call |
| Copilot hallucinating beyond the data | Copilot answers only from the scored dataset and live Mireye calls; why-card rule applies (no provenance row, no claim) |

## 12. Assignment rubric mapping

| Assignment ask | Where this PRD answers it |
|---|---|
| A user/industry/decision they haven't thought of | Section 3: county public works, budget triage on existing roads; no Mireye preset or example covers linear-asset maintenance |
| Build end to end, actually touch the data | Sections 6-7, 9: thousands of /fetch calls plus /ask, producing a user-facing result |
| Why Mireye vs Google Maps / GIS analyst / generic LLM | Section 4 |
| What worked | Section 6 stage 4: the LTPP validation chart |
| Where the data fell short | Section 10, generated from the audit log |
| Write-up on learnings | Session 6 deliverables; Section 10 is the spine |
| What the model got wrong and how you caught it | AI error log protocol, Section 11 |
