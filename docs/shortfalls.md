<!-- AGENT-DRAFTED by src/agents/audit_narrator.py from data/audit.json + ERRORS.md. DRAFT for human editing — verify every number before sharing. -->

# Where Mireye Falls Short for Linear-Asset Risk Scoring

**Summary:** This build scored 7,877 points across the Leesburg+Ashburn core using Mireye as the sole geospatial source. The scoring model runs, and the fetch/QA pipeline is healthy (6,936 kept, 0 discarded, kill-check did not fire), but four structural gaps limit the product's fitness for corridor-based linear-asset risk: no polyline primitive, no precipitation field, no freeze-thaw counter, and reconnaissance-scale soil data that caps confidence. Below is what the numbers actually show.

## 1. No corridor/polyline primitive
Mireye has no line-geometry query. Corridor "coverage" in this build is simulated by spraying dense point queries along the route: 7,877 points in scope generated 7,870 individual coordinate fetches for a bounding box that's roughly 8km x 12km. That's the whole cost model — every mile of asset is N discrete point calls, not one line query, so cost and latency scale with point density rather than route length. On top of that, this run shows `calls_made: 0` and `points_from_cache: 0` against 7,870 attempted fetches (`wall_seconds: 1.3`) — an anomaly worth flagging on its own: either this run was pre-cached upstream of the counters or the calls never registered, and either way the audit JSON as supplied doesn't explain it.

## 2. No precipitation field
`null_rate_per_field` and `failed_rate_per_field` enumerate 43 fields; none of them is precipitation, or anything resembling it. This isn't a null-rate problem — the field is structurally absent from the API. Any risk score component that needs rainfall intensity/accumulation has to come from elsewhere.

## 3. No freeze-thaw cycle count
Same finding: no freeze-thaw field exists in the 43-field list. The only cold-climate proxy present is `mean_annual_snow_cover_days` (null rate 0.055, failed rate 0.055). Snow-cover days is a weather-pattern proxy, not a cycle count, and will misrepresent freeze-thaw stress in low-snow/high-cycle climates.

## 4. SSURGO/STATSGO soil is reconnaissance-scale
The soil fields show the two-tier pattern typical of reconnaissance mapping: six core fields (`soil_drainage_class`, `soil_shrink_swell_class`, `soil_available_water_capacity`, `soil_ponding_frequency_class`, `soil_hydrologic_group`, `soil_erodibility_k_factor`) sit at a 0.062 null rate, but `soil_restrictive_layer_depth_cm` and `soil_restrictive_layer_kind` — the fields that most directly drive settlement/bearing-capacity risk — jump to 0.347 null. Failed rate for restrictive-layer fields is only 0.055, so the gap is data absence in the source, not fetch failure. Confidence distribution across the 337,765 field-observations is 145,727 high / 165,846 medium / 5,404 unknown / 2,341 low / 18,447 none — i.e. only ~43% of observations land in the top bucket, consistent with a screening-grade dataset rather than site-investigation-grade.

## Measured from this build

**Null rates (selected):** soil core fields 0.062; soil restrictive-layer fields 0.347; slope/aspect 0.08; most environmental/hydro/political fields cluster at 0.055–0.065; `nearest_flowline_name` 0.814, `nearest_waterbody_name` 0.993, `nearest_bridge_name` 0.622, `nearest_road_surface` 0.521.

Most of those high-null fields are *not* fetch failures — `failed_rate_per_field` for `nearest_flowline_name` and `nearest_waterbody_name` is 0.059, and for `nearest_road_surface` is 0.055, meaning the nulls are semantic absence (no nearby feature to name) rather than broken calls. `nearest_bridge_name` is the exception: its failed rate (0.622) matches its null rate exactly, meaning most of those nulls are genuine fetch failures, not absence.

**Calls vs. cache:** `calls_made: 0`, `points_from_cache: 0`, `rate_limited: 0`, against `total_coordinate_fetches: 7,870` for `points_in_scope: 7,877`, completing in `wall_seconds: 1.3`. As noted above, this combination doesn't add up to a normal cold-fetch run and should be re-audited before the numbers are used to model throughput or cost.

**LTPP validation:** the supplied audit JSON does not contain an `ltpp_validation` key (n, ground-truth coefficient, permutation p-value, quartile deterioration medians are all absent from this artifact). We cannot report a ground-truth validation result here — this is a gap in the supplied evidence, not a finding about Mireye, and should not be conflated with the `qa` block (which only covers fetch snap-QA: `keep: 6936`, `resnapped: 0`, `discarded: 0`, `keep_flag: 941`).

## Headline product feedback

The single biggest mismatch between Mireye and this use case is architectural: **Mireye is a point API being asked to do corridor work.** Every asset segment costs one call per sample point (7,870 fetches for one 8x12km tile), there's no way to request "everything along this line," and point density becomes a manual tradeoff between cost and missing short-segment hazards between samples. A native polyline/corridor primitive — even a simple "sample along this LineString at N-meter intervals, return once" — would remove the spraying step entirely and let cost scale with route length instead of an externally-chosen sample rate.
