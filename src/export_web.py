"""Export the scored network + live layer to static JSON for the Vercel web app (no Python server
at runtime). Produces web/public/data/segments.geojson (geometry + relative color + top-5 cited
drivers + RSL per Fix 2) and live.json (status counts + watched segment ids + per-segment triggers).

Relative coloring: segments are colored by their PERCENTILE rank within this corridor (worst -> best),
because the absolute scores cluster in a narrow band (32-62) and an absolute ramp paints ~89% one
color. The legend states plainly that this is relative ranking among local roads, not a good/bad claim.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "agents"))
import attribution  # noqa: E402
import service_life  # noqa: E402
import why_card  # noqa: E402  (VDOT AADT citation)

REPO = SRC.parent
DATA = REPO / "data"
SCORES = DATA / "scores.parquet"
SEGMENTS = DATA / "segments.parquet"
CACHE_DB = DATA / "cache.sqlite"
WATCHLIST = DATA / "watchlist.parquet"
WATCH_META = DATA / "watchlist_meta.json"
ABLATION = DATA / "ablation.parquet"
ABLATION_META = DATA / "ablation_meta.json"
OUT_DIR = REPO / "web" / "public" / "data"

# Relative-rank color ramp (worst -> best quintile). Diverging green<->red; legend explains it.
RAMP = [
    (0.80, "#d7191c", "worst 20% (relative)"),
    (0.60, "#fdae61", "60–80% risk"),
    (0.40, "#ffffbf", "40–60% risk"),
    (0.20, "#a6d96a", "20–40% risk"),
    (0.00, "#1a9641", "lowest 20% (relative)"),
]
# Friendly labels for the driver components; fall back to a prettified field name.
COMPONENT_LABEL = {
    "traffic_aadt": "Traffic load (AADT)",
    "housing_units_density_per_km2": "Housing density (traffic proxy)",
    "soil_erodibility_k_factor": "Soil erodibility (K-factor)",
    "bedrock_depth_cm": "Depth to bedrock",
    "soil_drainage_class": "Soil drainage class",
    "within_floodplain_polygon": "Within floodplain",
    "fema_flood_zone": "FEMA flood zone",
    "landslide_susceptibility_index": "Landslide susceptibility",
    "nearest_wetland_distance_m": "Distance to wetland",
    "water_table_depth_cm": "Depth to water table",
    "slope_percent": "Slope",
}


def _label(field: str) -> str:
    return COMPONENT_LABEL.get(field, field.replace("_", " ").capitalize())


def cited_drivers(conn: sqlite3.Connection, segment_id: int, drivers: list) -> list[dict]:
    """Structured, cited driver rows for one segment (mirrors the why-card provenance rule: no
    provenance row, no line). traffic_aadt is a VDOT join, cited to its VDOT source."""
    pids = [f"{segment_id}_{k}" for k in range(3)]
    out = []
    for d in drivers:
        field = d["component"]
        if field == "traffic_aadt":
            out.append({"label": _label(field), "value": d["value"], "field": field,
                        "source": why_card.VDOT_SOURCE, "source_url": why_card.VDOT_URL,
                        "fetched_at": None, "contribution": d["contribution"]})
            continue
        row = conn.execute(
            "SELECT value, source, source_url, fetched_at FROM provenance WHERE field=? "
            f"AND status='present' AND point_id IN ({','.join('?' * len(pids))}) LIMIT 1",
            (field, *pids),
        ).fetchone()
        if row and row[1] and row[2]:  # source + url required (provenance discipline)
            # Show the value the score actually used (d["value"], the per-segment aggregate), not the
            # single provenance point row[0]; the provenance row supplies only the source + url.
            out.append({"label": _label(field), "value": d["value"], "field": field,
                        "source": row[1], "source_url": row[2], "fetched_at": (row[3] or "")[:10],
                        "contribution": d["contribution"]})
    return out


def _color(rank_pct: float) -> tuple[str, str]:
    for lo, hexc, lab in RAMP:
        if rank_pct >= lo:
            return hexc, lab
    return RAMP[-1][1], RAMP[-1][2]


def _rsl_props(r) -> dict:
    """RSL per Fix 2: a range only for hpms/vdot basis (floored); prior basis says not-estimated."""
    estimated = bool(r.get("rsl_estimated")) and pd.notna(r.get("rsl_year_low"))
    row = {"rsl_year_low": int(r["rsl_year_low"]) if estimated else None,
           "rsl_year_high": int(r["rsl_year_high"]) if estimated else None,
           "rsl_basis": r["rsl_basis"], "last_treated_year": r.get("rsl_last_treated"),
           "grade": r["rsl_grade"]}
    return {"estimated": estimated, "basis": r["rsl_basis"],
            "low": row["rsl_year_low"], "high": row["rsl_year_high"],
            "last_treated": int(r["rsl_last_treated"]) if estimated and pd.notna(r.get("rsl_last_treated")) else None,
            "text": service_life.render_rsl(row)}


def build_segments() -> dict:
    scores = pd.read_parquet(SCORES)
    geoms = gpd.read_parquet(SEGMENTS)[["segment_id", "geometry"]].to_crs("EPSG:4326")
    gdf = gpd.GeoDataFrame(scores.merge(geoms, on="segment_id"), geometry="geometry", crs="EPSG:4326")
    gdf["rank_pct"] = gdf["score"].rank(pct=True)

    current_year = datetime.now(timezone.utc).year
    # Ablation (traffic-only vs +Mireye priority ranking) — per-segment coloring + rank movement.
    abl = pd.read_parquet(ABLATION).set_index("segment_id") if ABLATION.exists() else None

    conn = sqlite3.connect(CACHE_DB)
    conn.execute("PRAGMA busy_timeout=8000")
    features = []
    for r in gdf.itertuples(index=False):
        rd = r._asdict()
        color, bucket = _color(rd["rank_pct"])
        ablation = {}
        if abl is not None and rd["segment_id"] in abl.index:
            a = abl.loc[rd["segment_id"]]
            ablation = {
                "color_no_mireye": _color(float(a["no_mireye_pct"]))[0],
                "quintile_full": int(a["quintile_full"]),
                "quintile_no_mireye": int(a["quintile_no_mireye"]),
                "q_changed": int(a["quintile_full"]) != int(a["quintile_no_mireye"]),
                "rank_delta": int(a["rank_delta"]),
                "is_flip": bool(a["flip"]),
            }
        name = rd["route_name"]
        name = name if isinstance(name, str) and name else "unnamed road"  # route_name is NaN when unnamed
        rsl = _rsl_props(rd)
        rsl_mid = (rsl["low"] + rsl["high"]) / 2 if rsl["estimated"] else None
        # Un-normalized decision-input weights (Mireye / VDOT traffic / Local records). The client
        # normalizes and adds a "Live stress" slice when the Right-now view is on for a watched segment.
        weights = attribution.static_weights(float(rd["mireye_share"]), rsl["estimated"],
                                             rsl["last_treated"], rsl_mid, current_year)
        features.append({
            "type": "Feature",
            "geometry": rd["geometry"].__geo_interface__,
            "properties": {
                "segment_id": int(rd["segment_id"]),
                "route_name": name,
                "score": float(rd["score"]), "grade": rd["grade"],
                "rank_pct": round(float(rd["rank_pct"]), 3), "color": color, "bucket": bucket,
                "mireye_share": round(float(rd["mireye_share"]), 3),
                "mireye_field_count": int(rd["mireye_field_count"]),
                "decision_weights": {k: round(v, 4) for k, v in weights.items()},
                "drivers": cited_drivers(conn, int(rd["segment_id"]), json.loads(rd["drivers"])),
                "rsl": rsl,
                **ablation,
            },
        })
    conn.close()
    return {"type": "FeatureCollection", "features": features}


def build_live() -> dict:
    """Status counts for the always-visible live line (Fix 3) + watched ids + per-segment triggers."""
    if not WATCH_META.exists():
        return {"available": False}
    meta = json.loads(WATCH_META.read_text())
    watched, triggers = [], {}
    if WATCHLIST.exists():
        wl = pd.read_parquet(WATCHLIST)
        w = wl[wl["watched"]]
        watched = [int(s) for s in w["segment_id"]]
        for row in w.itertuples(index=False):
            trigs = json.loads(row.triggers or "[]")
            if trigs:
                triggers[str(int(row.segment_id))] = trigs
    return {
        "available": True,
        "generated_at": meta.get("generated_at"),
        "calm": bool(meta.get("calm", True)),
        "active_alerts": len(meta.get("active_alerts") or []),
        "alert_names": meta.get("active_alerts") or [],
        "elevated_gages": len(meta.get("elevated_gages") or []),
        "gage_ids": meta.get("elevated_gages") or [],
        "wet_week": bool((meta.get("wet_week") or {}).get("wet")),
        "watched_segments": int(meta.get("watched_segments", len(watched))),
        "watched": watched,
        "triggers": triggers,
    }


def build_summary(props: list[dict]) -> dict:
    """Countywide 'how much of the decision does Mireye power, and which fields' — BY CONTRIBUTION,
    with a naive field-count median shown alongside so the gap is visible (field-count overstates
    Mireye because it just counts fields we chose, ignoring how much each moved the score)."""
    mireye_c, vdot_c, records_c, mireye_fc = [], [], [], []
    field_freq: Counter = Counter()
    for p in props:
        shares = attribution.normalize(p["decision_weights"])
        mireye_c.append(shares.get("Mireye", 0.0))
        vdot_c.append(shares.get("VDOT traffic", 0.0))
        records_c.append(shares.get("Local records", 0.0))
        n_non = 1 if p["decision_weights"].get("VDOT traffic", 0) > 0 else 0
        total_fields = p["mireye_field_count"] + n_non
        mireye_fc.append(p["mireye_field_count"] / total_fields if total_fields else 1.0)
        for d in p["drivers"]:
            if d["field"] != "traffic_aadt":
                field_freq[d["label"]] += 1
    med = statistics.median
    return {
        "segments": len(props),
        "mireye_contribution": {"median": round(med(mireye_c), 3), "min": round(min(mireye_c), 3),
                                "max": round(max(mireye_c), 3)},
        "mireye_by_fieldcount_median": round(med(mireye_fc), 3),  # naive; overstates — shown for contrast
        "non_mireye_median": {"vdot_traffic": round(med(vdot_c), 3),
                              "local_records": round(med(records_c), 3)},
        "top_mireye_fields": [{"field": f, "in_top_drivers": n} for f, n in field_freq.most_common(3)],
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fc = build_segments()
    # allow_nan=False: fail loud if any NaN slips in (NaN is invalid JSON — breaks browser + copilot parse).
    (OUT_DIR / "segments.geojson").write_text(json.dumps(fc, allow_nan=False))
    live = build_live()
    (OUT_DIR / "live.json").write_text(json.dumps(live, indent=2, allow_nan=False))

    # Slim per-segment records (no geometry) for the copilot serverless function.
    props = [f["properties"] for f in fc["features"]]
    (OUT_DIR / "scores.json").write_text(json.dumps(props, allow_nan=False))
    summary = build_summary(props)
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False))
    # Ablation summary (churn %, Spearman, the 5 ground-revealed flips) for the ablation view.
    if ABLATION_META.exists():
        (OUT_DIR / "ablation.json").write_text(ABLATION_META.read_text())
    estimated = sum(1 for p in props if p["rsl"]["estimated"])
    size_mb = (OUT_DIR / "segments.geojson").stat().st_size / 1_000_000
    print(f"Wrote {len(fc['features'])} segments -> {OUT_DIR/'segments.geojson'} ({size_mb:.1f} MB)")
    print(f"  RSL: {estimated} estimated (hpms/vdot) · {len(props) - estimated} not-estimated (prior)")

    # Honesty verification (per spec): 5 segments' group shares sum to 1.0; a segment with a real
    # treatment year shows a Local-records slice, one on the prior does not.
    print("\n5-segment attribution check (shares of this decision's inputs):")
    sample = [p for p in props if p["rsl"]["estimated"]][:3] + [p for p in props if not p["rsl"]["estimated"]][:2]
    for p in sample:
        sh = attribution.normalize(p["decision_weights"])
        tag = "has Local-records" if "Local records" in sh else "no Local-records (prior)"
        print(f"  seg {p['segment_id']}: { {k: f'{v:.0%}' for k, v in sh.items()} }  sum={sum(sh.values()):.2f}  {tag}")
    mc = summary["mireye_contribution"]
    print(f"\nCountywide Mireye share: median {mc['median']:.0%} (range {mc['min']:.0%}-{mc['max']:.0%}) "
          f"BY CONTRIBUTION vs {summary['mireye_by_fieldcount_median']:.0%} by naive field-count "
          f"— field-count overstates Mireye (it counts fields we chose, not how much each moved the score).")
    print(f"Top Mireye fields countywide: {[f['field'] for f in summary['top_mireye_fields']]}")
    print(f"Wrote live.json: calm={live.get('calm')} watched={live.get('watched_segments')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
