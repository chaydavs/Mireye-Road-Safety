"""Session 2 network stage (PRD section 6 stage 1): centerlines -> segments -> sample points,
with VDOT AADT joined. No Mireye fetch, no scoring, no map (non-goals).

Source note: VirginiaRoads' VA_Primary_and_Secondary_Roads layer has NO local roads in
Loudoun (primary+secondary only), but local roads are the product's whole point. So centerlines
come from Census TIGER/Line All Roads for FIPS 51107 (Loudoun), filtered to secondary (S1200) and
local (S1400) MTFCC. AADT comes from VDOT's Bidirectional Traffic Volume 2025 service, joined
spatially (TIGER has no VDOT route id, so the route-id join the PRD mentions degenerates to the
30 m spatial fallback).
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd
from shapely.geometry import shape
from shapely.ops import substring

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
ROADS_ZIP = DATA / "tl_2024_51107_roads.zip"
SEGMENTS_OUT = DATA / "segments.parquet"
POINTS_OUT = DATA / "points.parquet"

WORK_CRS = "EPSG:32618"  # UTM 18N, meters — for length, segmentation, 30 m join
OUT_CRS = "EPSG:4326"    # WGS84 lat/lon — Mireye input and map output

SECONDARY_LOCAL_MTFCC = {"S1200", "S1400"}  # secondary + local; excludes primary/ramps/private
SEGMENT_METERS = 500.0
POINTS_PER_SEGMENT = 3
AADT_JOIN_METERS = 30.0
METERS_PER_MILE = 1609.344

# VDOT AADT service. ADT = annual daily traffic. (Truck share is a Stage-3 scoring input, added
# in Session 4, not here.)
AADT_QUERY_URL = (
    "https://services.arcgis.com/p5v98VHDX9Atv3l7/arcgis/rest/services/"
    "VDOT_Bidirectional_Traffic_Volume_2025/FeatureServer/0/query"
)
LOUDOUN_BBOX = (-77.96, 38.84, -77.32, 39.32)  # xmin, ymin, xmax, ymax (WGS84)

# Session 1 measured sustainable cold throughput (calls/min). Used for the coverage/kill check.
SUSTAINED_CPM_COLD = 100.0     # typical cold-ish sequential (Session 1)
SUSTAINED_CPM_FLOOR = 30.0     # worst-case fully-cold sequential (Session 1)
CONCURRENCY = 4                # Session 3's planned concurrency
FETCH_BUDGET_MINUTES = 60.0    # kill criterion: county must fit ~1 hour


def cut_line(line, seg_len: float) -> list:
    """Cut one LineString into pieces closest to seg_len (even division, ~250-750 m)."""
    total = line.length
    if total <= seg_len:
        return [line]
    n = max(1, round(total / seg_len))
    step = total / n
    return [substring(line, i * step, (i + 1) * step) for i in range(n)]


def cut_geometry(geom, seg_len: float) -> list:
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return cut_line(geom, seg_len)
    if geom.geom_type == "MultiLineString":
        parts = []
        for part in geom.geoms:
            parts.extend(cut_line(part, seg_len))
        return parts
    return []


def segment_gdf(gdf: gpd.GeoDataFrame, seg_len: float) -> gpd.GeoDataFrame:
    """Cut every geometry into ~seg_len segments, replicating attributes; assign segment_id."""
    attr_cols = [c for c in gdf.columns if c != gdf.geometry.name]
    rows, geoms = [], []
    for _, r in gdf.iterrows():
        for part in cut_geometry(r.geometry, seg_len):
            if part.is_empty or part.length == 0:
                continue
            rows.append({c: r[c] for c in attr_cols})
            geoms.append(part)
    out = gpd.GeoDataFrame(rows, geometry=geoms, crs=gdf.crs)
    out.insert(0, "segment_id", range(len(out)))
    return out


def sample_points(segments: gpd.GeoDataFrame, n: int) -> gpd.GeoDataFrame:
    """n points per segment at evenly spaced fractions, snapped onto the line (interpolate)."""
    recs, geoms = [], []
    for _, r in segments.iterrows():
        seg = r.geometry
        length = seg.length
        seg_id = r["segment_id"]
        for k in range(n):
            frac = (k + 0.5) / n
            recs.append({"point_id": f"{seg_id}_{k}", "segment_id": seg_id})
            geoms.append(seg.interpolate(frac * length))
    return gpd.GeoDataFrame(recs, geometry=geoms, crs=segments.crs)


def load_centerlines(path: Path) -> gpd.GeoDataFrame:
    """Read TIGER roads, filter to secondary+local, reproject to the metric work CRS."""
    if not path.exists():
        sys.exit(f"FATAL: {path} not found. Download TIGER roads for FIPS 51107 first.")
    g = gpd.read_file(path)
    g = g[g["MTFCC"].isin(SECONDARY_LOCAL_MTFCC)].copy()
    # TIGER carries ~9% of roads as exact-duplicate geometries (a road recorded twice); drop
    # them so a physical road is not segmented, fetched, scored, and mapped twice. Caught by
    # the data-qa agent in Session 2.
    g = g[~g.geometry.apply(lambda geom: geom.wkb).duplicated()]
    g = g.rename(columns={"FULLNAME": "route_name", "MTFCC": "mtfcc"})
    g = g.to_crs(WORK_CRS)
    # KNOWN LIMITATION (see FUTURE.md): TIGER does not mark bridge spans, and bridge exclusion
    # (PRD section 5 non-goal) is NOT implemented — a robust filter needs a bridge-distance field
    # we don't fetch. `nearest_bridge_name` is fetched as the intended input for this future QA.
    return g[["route_name", "mtfcc", "geometry"]]


def fetch_aadt(bbox: tuple[float, float, float, float]) -> gpd.GeoDataFrame:
    """Download VDOT AADT lines in the bbox as GeoJSON (paginated). Returns EMPTY gdf on failure."""
    xmin, ymin, xmax, ymax = bbox
    out_fields = "ADT"
    features: list[dict] = []
    offset = 0
    page = 2000
    try:
        with httpx.Client(timeout=60.0) as client:
            while True:
                params = {
                    "where": "ADT IS NOT NULL",
                    "geometry": f"{xmin},{ymin},{xmax},{ymax}",
                    "geometryType": "esriGeometryEnvelope",
                    "inSR": "4326",
                    "outSR": "4326",
                    "spatialRel": "esriSpatialRelIntersects",
                    "outFields": out_fields,
                    "returnGeometry": "true",
                    "resultOffset": offset,
                    "resultRecordCount": page,
                    "f": "geojson",
                }
                resp = client.get(AADT_QUERY_URL, params=params)
                resp.raise_for_status()
                fc = resp.json()
                batch = fc.get("features", [])
                features.extend(batch)
                if len(batch) < page:
                    break
                offset += page
    except (httpx.HTTPError, ValueError) as exc:
        print(f"WARN: AADT fetch failed ({exc}); proceeding with no traffic counts.")
        return gpd.GeoDataFrame({"ADT": []}, geometry=[], crs=OUT_CRS)

    rows, geoms = [], []
    for feat in features:
        props = feat.get("properties", {})
        geom = feat.get("geometry")
        if geom is None:
            continue
        rows.append({"ADT": props.get("ADT")})
        geoms.append(shape(geom))
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs=OUT_CRS)
    return gdf.to_crs(WORK_CRS)


def join_aadt(segments: gpd.GeoDataFrame, aadt: gpd.GeoDataFrame, max_dist: float) -> gpd.GeoDataFrame:
    """Attach nearest AADT within max_dist. No match -> aadt null, traffic_source 'none'.
    Null is never coerced to zero (CLAUDE.md)."""
    segments = segments.copy()
    if len(aadt) == 0:
        segments["aadt"] = pd.NA
        segments["traffic_source"] = "none"
        return segments
    joined = gpd.sjoin_nearest(
        segments[["segment_id", "geometry"]], aadt[["ADT", "geometry"]],
        how="left", max_distance=max_dist, distance_col="_dist",
    )
    joined = joined[~joined["segment_id"].duplicated(keep="first")].set_index("segment_id")
    segments = segments.set_index("segment_id")
    segments["aadt"] = joined["ADT"]
    segments["traffic_source"] = segments["aadt"].apply(
        lambda v: "vdot_spatial" if pd.notna(v) else "none"
    )
    return segments.reset_index()


def write_outputs(segments: gpd.GeoDataFrame, points: gpd.GeoDataFrame) -> None:
    seg_out = segments.to_crs(OUT_CRS)
    seg_out.to_parquet(SEGMENTS_OUT)

    pts = points.to_crs(OUT_CRS)
    df = pd.DataFrame({
        "point_id": pts["point_id"].values,
        "segment_id": pts["segment_id"].values,
        "lat": pts.geometry.y.values,
        "lon": pts.geometry.x.values,
    })
    df.to_parquet(POINTS_OUT)


def print_summary(segments: gpd.GeoDataFrame, n_points: int) -> bool:
    """Print the required summary. Return True if the fetch budget is blown (kill criterion)."""
    seg_count = len(segments)
    total_miles = segments.geometry.length.sum() / METERS_PER_MILE
    with_aadt = int(segments["traffic_source"].eq("vdot_spatial").sum())
    pct_aadt = 100.0 * with_aadt / seg_count if seg_count else 0.0

    minutes_seq = n_points / SUSTAINED_CPM_COLD
    minutes_seq_floor = n_points / SUSTAINED_CPM_FLOOR
    minutes_conc = minutes_seq / CONCURRENCY

    print("\n" + "=" * 64)
    print("SUBGRADE SESSION 2 NETWORK SUMMARY")
    print("=" * 64)
    print(f"  Segments (secondary+local)   : {seg_count}")
    print(f"  Sample points (= Mireye calls): {n_points}")
    print(f"  Total road miles             : {total_miles:.1f}")
    print(f"  Segments with VDOT AADT      : {with_aadt} ({pct_aadt:.1f}%)")
    print("  Projected fetch time (cold):")
    print(f"    sequential @ {SUSTAINED_CPM_COLD:.0f}/min : {minutes_seq:.0f} min "
          f"(worst-case @ {SUSTAINED_CPM_FLOOR:.0f}/min: {minutes_seq_floor:.0f} min)")
    print(f"    {CONCURRENCY}-concurrent            : {minutes_conc:.0f} min")
    print("=" * 64)

    blown = minutes_conc > FETCH_BUDGET_MINUTES
    if blown:
        print(f"\nKILL CRITERION: county exceeds the ~{FETCH_BUDGET_MINUTES:.0f} min fetch budget "
              f"even at {CONCURRENCY}-concurrent ({minutes_conc:.0f} min).")
        print("Loudoun sits within ONE VDOT district (NoVA), so scope by road class / area, e.g.:")
        _print_scope_options(segments, n_points)
        print("\nSTOP: choose a scope before Session 3 fetch.")
    return blown


def _print_scope_options(segments: gpd.GeoDataFrame, n_points: int) -> None:
    """Show concrete narrower scopes with their point counts and 4-concurrent fetch time."""
    per_seg = n_points / len(segments) if len(segments) else 0
    sec = segments[segments["mtfcc"] == "S1200"]
    sec_points = int(len(sec) * per_seg)
    print(f"  A) Secondary only (S1200): ~{len(sec)} segments, ~{sec_points} calls, "
          f"~{sec_points / SUSTAINED_CPM_COLD / CONCURRENCY:.0f} min @ {CONCURRENCY}x")
    cent_lon = segments.geometry.centroid.to_crs(OUT_CRS).x  # centroid in projected CRS, then lon
    east = segments[cent_lon.values > -77.55]
    east_points = int(len(east) * per_seg)
    print(f"  B) Eastern Loudoun (lon > -77.55): ~{len(east)} segments, ~{east_points} calls, "
          f"~{east_points / SUSTAINED_CPM_COLD / CONCURRENCY:.0f} min @ {CONCURRENCY}x")
    print(f"  C) Full county (secondary+local): {len(segments)} segments, {n_points} calls "
          "(over budget)")


def main() -> int:
    roads = load_centerlines(ROADS_ZIP)
    segments = segment_gdf(roads, SEGMENT_METERS)
    points = sample_points(segments, POINTS_PER_SEGMENT)
    aadt = fetch_aadt(LOUDOUN_BBOX)
    segments = join_aadt(segments, aadt, AADT_JOIN_METERS)
    write_outputs(segments, points)
    blown = print_summary(segments, len(points))
    return 3 if blown else 0


if __name__ == "__main__":
    raise SystemExit(main())
