"""Session 2 invariants — written before network.py (TDD). Pure geometry, no network.

Three required assertions:
1. every segment has exactly 3 sample points,
2. every point lies within 5 m of its segment's line,
3. no duplicate point ids.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402
from shapely.geometry import LineString  # noqa: E402

import network  # noqa: E402


def _synthetic_segments():
    """Two roads in a metric CRS (EPSG:32618, meters): a 1.2 km and a 1.5 km line.
    Segmenting at 500 m yields multiple segments per road."""
    lines = [
        LineString([(0, 0), (0, 1200)]),      # 1200 m -> ~2 segments
        LineString([(0, 0), (1500, 0)]),      # 1500 m -> ~3 segments
    ]
    gdf = gpd.GeoDataFrame(
        {"route_name": ["Road A", "Road B"], "mtfcc": ["S1400", "S1200"]},
        geometry=lines,
        crs="EPSG:32618",
    )
    return network.segment_gdf(gdf, seg_len=network.SEGMENT_METERS)


def test_three_points_per_segment():
    segs = _synthetic_segments()
    pts = network.sample_points(segs, n=network.POINTS_PER_SEGMENT)
    counts = pts.groupby("segment_id").size()
    assert (counts == network.POINTS_PER_SEGMENT).all()
    assert len(counts) == len(segs)  # every segment represented


def test_points_within_5m_of_their_segment():
    segs = _synthetic_segments()
    pts = network.sample_points(segs, n=network.POINTS_PER_SEGMENT)
    seg_geom = segs.set_index("segment_id").geometry
    for _, row in pts.iterrows():
        assert seg_geom[row["segment_id"]].distance(row.geometry) <= 5.0


def test_no_duplicate_point_ids():
    segs = _synthetic_segments()
    pts = network.sample_points(segs, n=network.POINTS_PER_SEGMENT)
    assert pts["point_id"].is_unique


def test_segments_are_roughly_500m():
    segs = _synthetic_segments()
    # round()-based even division keeps every segment under 1.5x the target.
    assert (segs.geometry.length <= network.SEGMENT_METERS * 1.5 + 1.0).all()
    assert (segs.geometry.length > 100.0).all()


def test_join_aadt_match_and_nomatch():
    """DoD item 3: nearest AADT within 30 m; no match -> aadt NULL (never 0), source 'none'."""
    segs = gpd.GeoDataFrame(
        {"segment_id": [0, 1], "route_name": ["A", "B"], "mtfcc": ["S1200", "S1400"]},
        geometry=[LineString([(0, 0), (0, 100)]), LineString([(1000, 0), (1000, 100)])],
        crs="EPSG:32618",
    )
    aadt = gpd.GeoDataFrame(
        {"ADT": [5000.0]}, geometry=[LineString([(0, 0), (0, 100)])], crs="EPSG:32618"
    )
    out = network.join_aadt(segs, aadt, max_dist=30.0).set_index("segment_id")
    assert out.loc[0, "aadt"] == 5000.0
    assert out.loc[0, "traffic_source"] == "vdot_spatial"
    assert pd.isna(out.loc[1, "aadt"])  # 1000 m away -> no match
    assert out.loc[1, "traffic_source"] == "none"
    assert "truck_pct" not in out.columns  # truck share is deferred to scoring, not built here


def test_join_aadt_empty_source_is_all_none():
    segs = gpd.GeoDataFrame(
        {"segment_id": [0], "route_name": ["A"], "mtfcc": ["S1400"]},
        geometry=[LineString([(0, 0), (0, 100)])], crs="EPSG:32618",
    )
    empty = gpd.GeoDataFrame({"ADT": []}, geometry=[], crs="EPSG:32618")
    out = network.join_aadt(segs, empty, 30.0).set_index("segment_id")
    assert pd.isna(out.loc[0, "aadt"])
    assert out.loc[0, "traffic_source"] == "none"
