"""Session 4 scoring engine (PRD section 6 stage 3): transparent weighted risk score per segment,
with confidence propagation. Deliberately not a black box.

    Risk = 100 * ( 0.30*W + 0.20*S + 0.20*C + 0.20*T + 0.10*G )

Each component maps to [0,1] via the published-threshold lookup tables below (higher = more
deterioration risk). Missing components drop out of a factor's average — never default to zero
(CLAUDE.md) — and their absence lowers the segment's confidence grade.

Loudoun note (from the fetched data): climate fields (snow/hot-days/temp) are near-constant across
one county, surface_water_permanence is ~0 at road points, and shrink-swell is mostly 'Low'. So the
score's differentiation comes from water (drainage/hydrologic/wetland), terrain, and traffic — the
PRD's moisture-first thesis. NOAA precipitation / freeze-thaw gap-fill is NOT integrated (PRD s10).
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
from collections import Counter
from pathlib import Path

import folium
import geopandas as gpd

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
CACHE_DB = DATA / "cache.sqlite"
SEGMENTS = DATA / "segments.parquet"
SCORES_OUT = DATA / "scores.parquet"
MAP_OUT = REPO / "output" / "map.html"

# ---- PRD section 6 threshold lookup tables (one visible place, cited to the PRD) ----

# Categorical value -> [0,1] risk. soil_drainage_class values/scores are the PRD's worked example.
CATEGORICAL = {
    "soil_drainage_class": {
        "Very poorly drained": 1.0, "Poorly drained": 1.0, "Somewhat poorly drained": 0.7,
        "Moderately well drained": 0.4, "Well drained": 0.2,
        "Somewhat excessively drained": 0.15, "Excessively drained": 0.1,
    },
    "soil_ponding_frequency_class": {
        "Frequent": 1.0, "Occasional": 0.7, "Rare": 0.4, "None": 0.1,
    },
    "soil_shrink_swell_class": {"Very high": 1.0, "High": 1.0, "Moderate": 0.6, "Low": 0.2},
    # FEMA: Special Flood Hazard Areas = high; X (minimal) low; X500/shaded moderate.
    "fema_flood_zone": {
        "A": 1.0, "AE": 1.0, "AH": 1.0, "AO": 1.0, "AR": 1.0, "A99": 1.0,
        "V": 1.0, "VE": 1.0, "X500": 0.4, "X": 0.1, "D": 0.5,
    },
}
# Hydrologic soil group: A best infiltration -> D worst (holds water). Dual groups use the worse
# (undrained, second) letter.
HYDRO = {"A": 0.2, "B": 0.4, "C": 0.7, "D": 1.0}
BOOLEAN = {"within_floodplain_polygon": (1.0, 0.0)}  # (True, False)

# Numeric: (direction, [(cutoff, score), ...]). "high" = higher value is worse (descending cutoffs,
# first value>=cutoff wins). "low" = lower value is worse (ascending cutoffs, first value<=cutoff).
NUMERIC = {
    "surface_water_permanence_pct": ("high", [(50, 1.0), (20, 0.7), (5, 0.4), (0, 0.1)]),
    "soil_available_water_capacity": ("high", [(0.20, 1.0), (0.17, 0.6), (0.13, 0.3), (0.0, 0.1)]),
    "soil_erodibility_k_factor": ("high", [(0.43, 1.0), (0.37, 0.7), (0.28, 0.4), (0.0, 0.2)]),
    "nearest_wetland_distance_m": ("low", [(50, 1.0), (150, 0.7), (300, 0.4), (float("inf"), 0.2)]),
    "bedrock_depth_cm": ("low", [(60, 1.0), (100, 0.7), (150, 0.4), (float("inf"), 0.2)]),
    "slope_degrees": ("high", [(15, 1.0), (8, 0.7), (3, 0.4), (0, 0.2)]),
    "landslide_susceptibility_index": ("high", [(50, 1.0), (30, 0.7), (15, 0.4), (0, 0.2)]),
    "mean_annual_snow_cover_days": ("high", [(60, 1.0), (30, 0.7), (10, 0.4), (0, 0.2)]),
    "days_above_32c_annual_count": ("high", [(60, 1.0), (30, 0.7), (10, 0.4), (0, 0.2)]),
    "mean_annual_dry_bulb_temperature_degc": ("low", [(5, 1.0), (10, 0.7), (15, 0.4), (float("inf"), 0.2)]),
}

FACTORS = {
    "W": (0.30, [
        "soil_drainage_class", "soil_ponding_frequency_class", "within_floodplain_polygon",
        "fema_flood_zone", "surface_water_permanence_pct", "nearest_wetland_distance_m",
        "soil_available_water_capacity", "soil_hydrologic_group"]),
    "S": (0.20, ["soil_shrink_swell_class", "soil_erodibility_k_factor", "bedrock_depth_cm"]),
    "C": (0.20, ["mean_annual_snow_cover_days", "days_above_32c_annual_count",
                 "mean_annual_dry_bulb_temperature_degc"]),
    "G": (0.10, ["slope_degrees", "landslide_susceptibility_index"]),
}
T_WEIGHT = 0.20
WS_FIELDS = FACTORS["W"][1] + FACTORS["S"][1]  # load-bearing fields for the confidence grade
CONF_RANK = {"high": 3, "medium": 2, "low": 1}
AADT_REF = 50000.0        # log-normalization reference for AADT
HOUSING_REF = 2000.0      # and for the housing-density proxy


def _numeric_score(value: float, direction: str, breakpoints: list) -> float:
    if direction == "high":
        for cutoff, s in breakpoints:
            if value >= cutoff:
                return s
    else:  # "low": smaller is worse
        for cutoff, s in breakpoints:
            if value <= cutoff:
                return s
    return breakpoints[-1][1]


def component_score(field: str, value) -> float | None:
    """Map one field's value to [0,1]. Returns None when the value is missing or unmappable —
    never 0.0 by default (a null must not read as 'no risk')."""
    if value is None:
        return None
    if field in CATEGORICAL:
        return CATEGORICAL[field].get(value)  # unknown category -> None
    if field == "soil_hydrologic_group":
        return HYDRO.get(str(value).split("/")[-1])
    if field in BOOLEAN:
        return BOOLEAN[field][0] if value else BOOLEAN[field][1]
    if field in NUMERIC and isinstance(value, (int, float)):
        direction, bps = NUMERIC[field]
        return _numeric_score(float(value), direction, bps)
    return None


def factor_score(components: dict) -> float | None:
    """Mean of the present (non-None) component scores; None if all are missing."""
    present = [component_score(f, v) for f, v in components.items()]
    present = [s for s in present if s is not None]
    return sum(present) / len(present) if present else None


def traffic_component(aadt, traffic_source, housing_density) -> tuple[float | None, str]:
    """AADT log-normalized; fallback to housing-density proxy (PRD T factor). Returns (score, tag)."""
    if aadt is not None and traffic_source == "vdot_spatial" and aadt > 0:
        return min(1.0, math.log10(aadt + 1) / math.log10(AADT_REF + 1)), "aadt"
    if housing_density is not None and housing_density > 0:
        # proxy: capped below a real count's ceiling, and it downgrades confidence
        return min(0.9, math.log10(housing_density + 1) / math.log10(HOUSING_REF + 1)), "housing_proxy"
    return None, "none"


def _grade(field_conf: dict, factors: dict, traffic_tag: str) -> str:
    """PRD section 6 stage 3 confidence grade. C if a whole load-bearing factor (W or S) is absent
    or traffic is a proxy/none. Among present W/S inputs: any low -> C. Then A only if ALL W/S
    components are present AND high confidence; any medium OR any missing component lowers to B
    ("their absence lowers the segment's confidence grade")."""
    if factors["W"] is None or factors["S"] is None or traffic_tag != "aadt":
        return "C"
    ranks = [CONF_RANK.get(field_conf.get(f), 0) for f in WS_FIELDS if f in field_conf]
    if not ranks or min(ranks) <= 1:            # any low/unknown-confidence W/S input
        return "C"
    any_ws_missing = any(f not in field_conf for f in WS_FIELDS)
    if min(ranks) == 2 or any_ws_missing:       # any medium, or any W/S component absent -> B
        return "B"
    return "A"                                   # all W/S present and high confidence


def score_segment(field_values: dict, field_conf: dict, aadt, traffic_source, housing_density) -> dict:
    """Compute risk (0-100), grade, factor scores, and the top-5 contributing components."""
    factors, contributions = {}, []
    for name, (weight, fields) in FACTORS.items():
        comps = {f: field_values.get(f) for f in fields}
        fs = factor_score(comps)
        factors[name] = fs
        if fs is not None:
            present = {f: component_score(f, field_values.get(f)) for f in fields}
            present = {f: s for f, s in present.items() if s is not None}
            for f, s in present.items():
                contributions.append((f, field_values.get(f), weight * s / len(present)))

    t_score, t_tag = traffic_component(aadt, traffic_source, housing_density)
    factors["T"] = t_score
    if t_score is not None:
        # Tag the contribution so the why-card cites AADT to VDOT but the proxy to its own
        # Mireye provenance row (housing density) — never a housing number labeled as AADT.
        t_field = "traffic_aadt" if t_tag == "aadt" else "housing_units_density_per_km2"
        t_val = aadt if t_tag == "aadt" else housing_density
        contributions.append((t_field, t_val, T_WEIGHT * t_score))

    # Renormalize over available factors (a fully absent factor drops out). Weights come from the
    # single source above — not re-hardcoded. All-unmappable -> score None (never a fabricated 0).
    weights = {k: FACTORS[k][0] for k in FACTORS}
    weights["T"] = T_WEIGHT
    avail = {k: factors[k] for k in weights if factors.get(k) is not None}
    wsum = sum(weights[k] for k in avail)
    score = round(100.0 * sum(weights[k] * avail[k] for k in avail) / wsum, 1) if wsum else None

    drivers = sorted(contributions, key=lambda c: -c[2])[:5]
    return {
        "score": score,
        "grade": _grade(field_conf, factors, t_tag),
        "factors": factors,
        "traffic_source": t_tag,
        "drivers": [{"component": f, "value": v, "contribution": round(c, 3)} for f, v, c in drivers],
    }


# ---- aggregation from the provenance store ----

def _aggregate(value_rows: list) -> tuple[dict, dict]:
    """Per (segment, field): median of present numeric values or mode of categoricals, plus the
    min confidence. value_rows: (point_id, field, value_json, confidence, status)."""
    by_seg: dict[int, dict[str, list]] = {}
    conf: dict[int, dict[str, list]] = {}
    for point_id, field, value_json, confidence, status in value_rows:
        if status != "present":
            continue
        seg = int(point_id.rsplit("_", 1)[0])
        by_seg.setdefault(seg, {}).setdefault(field, []).append(json.loads(value_json))
        conf.setdefault(seg, {}).setdefault(field, []).append(confidence)
    seg_values, seg_conf = {}, {}
    for seg, fields in by_seg.items():
        seg_values[seg] = {f: _median_or_mode(vals) for f, vals in fields.items()}
        seg_conf[seg] = {f: _min_conf(conf[seg][f]) for f in fields}
    return seg_values, seg_conf


def _median_or_mode(values: list):
    nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if nums:
        return statistics.median(nums)
    return Counter(values).most_common(1)[0][0]


def _min_conf(confs: list) -> str | None:
    ranked = [(CONF_RANK.get(c, 0), c) for c in confs if c]
    return min(ranked)[1] if ranked else None


def score_all() -> gpd.GeoDataFrame:
    """Load provenance + segments, score every segment that has data, return a scored GeoDataFrame."""
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("PRAGMA busy_timeout=10000")  # fetch may still be writing concurrently
    rows = conn.execute(
        "SELECT point_id, field, value, confidence, status FROM provenance"
    ).fetchall()
    conn.close()
    seg_values, seg_conf = _aggregate(rows)

    segs = gpd.read_parquet(SEGMENTS).set_index("segment_id")
    records = []
    for seg_id, fields in seg_values.items():
        if seg_id not in segs.index:
            continue
        row = segs.loc[seg_id]
        result = score_segment(
            fields, seg_conf[seg_id], row.get("aadt"), row.get("traffic_source"),
            fields.get("housing_units_density_per_km2"),
        )
        if result["score"] is None:  # no mappable factor data for this segment -> not scorable
            continue
        records.append({
            "segment_id": seg_id, "route_name": row.get("route_name"),
            "score": result["score"], "grade": result["grade"],
            "traffic_source": result["traffic_source"],
            "drivers": json.dumps(result["drivers"]), "geometry": row.geometry,
        })
    scored = gpd.GeoDataFrame(records, geometry="geometry", crs=segs.crs)
    return scored


def _color(score: float) -> str:
    """Green (low) -> red (high) by score."""
    if score >= 70:
        return "#b10026"
    if score >= 55:
        return "#e31a1c"
    if score >= 40:
        return "#fd8d3c"
    if score >= 25:
        return "#fecc5c"
    return "#78c679"


def render_map(scored: gpd.GeoDataFrame) -> None:
    minx, miny, maxx, maxy = scored.total_bounds
    center = [(miny + maxy) / 2, (minx + maxx) / 2]
    m = folium.Map(location=center, zoom_start=12, tiles="CartoDB positron")
    for _, r in scored.iterrows():
        top = json.loads(r["drivers"])
        drivers = ", ".join(f"{t['component']}={t['value']}" for t in top)
        tooltip = (f"{r['route_name'] or 'unnamed'} | score {r['score']} (grade {r['grade']}) | "
                   f"top: {drivers}")
        folium.GeoJson(
            r.geometry.__geo_interface__,
            style_function=lambda _f, c=_color(r["score"]): {"color": c, "weight": 4},
            tooltip=tooltip,
        ).add_to(m)
    MAP_OUT.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(MAP_OUT))


def main() -> int:
    scored = score_all()
    if scored.empty:
        print("No scored segments yet (provenance store empty).")
        return 1
    out = scored.drop(columns="geometry")
    out.to_parquet(SCORES_OUT)
    render_map(scored)

    # Property check (PRD self-eval): no single score value may hold > 30% of segments.
    counts = scored["score"].value_counts(normalize=True)
    top_frac = counts.iloc[0]
    print(f"Scored {len(scored)} segments. score range {scored['score'].min()}-{scored['score'].max()}, "
          f"median {scored['score'].median()}. grades {dict(scored['grade'].value_counts())}")
    print(f"Most common single score holds {top_frac:.0%} of segments "
          f"({'DEGENERATE — fix thresholds' if top_frac > 0.30 else 'ok'}).")
    print(f"Wrote {SCORES_OUT} and {MAP_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
