<!-- AGENT-DRAFTED by src/agents/audit_narrator.py from data/audit.json + ERRORS.md. DRAFT for human editing — verify every number before sharing. -->

# Where Mireye Falls Short for Linear-Asset Risk Scoring

This build queried 1,090 points across the Leesburg+Ashburn core via 40 API calls (cold, uncached), passed the result through snap-QA (992 kept, 98 keep-flagged, 0 discarded), and validated the resulting risk scores against 51 LTPP pavement sections. The validation found no signal — permutation p = 0.2555 — while structural gaps in the API (no corridor primitive, no precipitation, no freeze-thaw counts, reconnaissance-grade soils) compound to limit both coverage and confidence. None of this is fatal to Mireye as a point-data source, but none of it supports treating its output as a validated linear-asset risk score today.

## 1. No corridor/polyline primitive
Mireye exposes only a point-fetch endpoint (`POST /v1/fetch`); there is no polyline or corridor query. Corridor coverage in this build was simulated by spraying 1,090 discrete point queries across the bbox and snapping them to road centerlines in post-processing — a costly, lossy substitute for a native linear query. The error log documents the downstream cost of this approach directly: an earlier centerline-name mismatch QA rule discarded 28% of points as "off-road" when they were in fact on the same road under a different name (Overture vs. TIGER naming), which had to be caught and rewritten as a distance-based check. Spraying points at a corridor is a workaround, not a feature.

## 2. No precipitation field
There is no precipitation field anywhere in the 41-field schema returned by this run (see the full `null_rate_per_field` list below — temperature, snow-cover, and drought fields are present; precipitation is absent). For a linear-asset risk model where rainfall-driven erosion and drainage loading matter, this is a schema gap, not a data-quality issue — the field simply isn't queryable.

## 3. No freeze-thaw cycle count
Mireye has no field for freeze-thaw cycle count. The only cold-climate proxy available is `mean_annual_snow_cover_days`, which had a 7.9% null rate in this run. Snow-cover days is a weak proxy for freeze-thaw mechanical stress (the actual driver of pavement/asset cracking), not a substitute for it.

## 4. Reconnaissance-scale soils cap confidence
Soil fields (`soil_drainage_class`, `soil_hydrologic_group`, `soil_available_water_capacity`, `soil_ponding_frequency_class`, `soil_erodibility_k_factor`) each carry a 10.8% null rate, and the two restrictive-layer fields (`soil_restrictive_layer_depth_cm`, `soil_restrictive_layer_kind`) are missing at 50.8% — roughly half of all points have no restrictive-layer read at all. This reflects SSURGO/STATSGO's reconnaissance-scale mapping, not a fetch failure. Consistent with this, the confidence distribution shows almost no top-tier certainty: of 47,000-ish scored attributes, 20,310 are "high" and 21,743 "medium," but 3,655 are "none" and 821 "unknown" — the soil-dependent scores are screening-grade by construction, and the distribution shows few/no clean A-grades.

## Measured from this build

- **Coverage/calls:** 1,090 points in scope, 40 calls made, 0 served from cache, 0 rate-limited, wall time 19.5s. Snap-QA kept 992 of these, flagged 98 as low-confidence keeps, discarded 0.
- **Null rates (selected):** soil core fields 10.8%; soil restrictive-layer fields 50.8%; most environmental/hydrology/road fields cluster at 7.8–8.3%; `nearest_flowline_name` 76.7%; `nearest_waterbody_name` 100%; `nearest_bridge_name` 64.8%; `nearest_road_surface` 58.7%; `nearest_wetland_distance_m` 20.2%. The near-total nulls on waterbody/flowline/bridge names are largely semantic absence (no nearby feature) rather than fetch failure, per the error log's correction to `derive_status`.
- **LTPP validation (`ltpp_validation` key):** 51 pavement sections tested. Ground-truth coefficient = 0.00068 against a shuffled-label mean of -0.00001 — a negligible, near-zero effect size either way. Permutation p-value = 0.2555, well above any conventional significance threshold. Bottom-quartile sections showed median deterioration of 0.0109 vs. 0.0127 for the top quartile — a small, non-significant gap in the direction expected but not distinguishable from noise. **This build's Mireye-derived risk score does not predict LTPP deterioration.**

## Headline product feedback

The single change that would most improve fitness for linear-asset use: **a native corridor/polyline query endpoint**. Everything else in this report — the 28%-of-points QA rewrite for road-name mismatches, the cost of 40 calls to cover one small bbox, the reliance on point-spraying to approximate a line — traces back to Mireye being a point API pressed into service for a linear problem. A polyline primitive (query by route/segment, return along-line aggregates) would remove the spray-and-snap step entirely and likely improve both coverage and the confidence distribution, since much of the "none"/"unknown" mass here comes from points that landed off-road or in gaps between point queries rather than from genuine data absence.
