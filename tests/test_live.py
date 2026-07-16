"""Live-stress-layer tests: alert filtering/intersection, gage stress vs own median, wet-week,
and the calm/empty state — all offline via stubs."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402
from shapely.geometry import LineString, box  # noqa: E402

import live  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _Client:
    """Returns a queued payload per URL substring match."""
    def __init__(self, routes):
        self.routes = routes

    def get(self, url, params=None):
        for key, payload in self.routes.items():
            if key in url:
                return _Resp(payload)
        raise AssertionError(f"unexpected url {url}")


def test_fetch_alerts_keeps_only_flood_and_winter():
    fc = {"features": [
        {"id": "a1", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]},
         "properties": {"event": "Flash Flood Warning", "geocode": {"UGC": ["VAC107"]},
                        "expires": "2026-07-16T22:00:00Z"}},
        {"id": "a2", "geometry": None,
         "properties": {"event": "Winter Storm Watch", "geocode": {"SAME": ["051107"]}}},
        {"id": "a3", "geometry": None, "properties": {"event": "Heat Advisory", "geocode": {}}},
    ]}
    alerts = live.fetch_alerts(_Client({"alerts/active": fc}))
    events = {a["event"] for a in alerts}
    assert events == {"Flash Flood Warning", "Winter Storm Watch"}  # Heat Advisory dropped
    assert all(a["source"] == "NWS" and a["fetched_at"] for a in alerts)  # provenance stamped


def test_segments_under_alert_polygon_vs_county():
    segs = gpd.GeoDataFrame(
        {"segment_id": [1, 2]},
        geometry=[LineString([(0.1, 0.1), (0.2, 0.2)]), LineString([(5, 5), (6, 6)])],
        crs="EPSG:4326")
    poly_alert = {"event": "Flood Warning", "geometry": box(0, 0, 1, 1), "affects_loudoun": True,
                  "source": "NWS", "source_url": "u", "fetched_at": "t", "expires": None}
    hits = live.segments_under_alert(segs, [poly_alert])
    assert set(hits) == {1}  # only segment 1 is inside the polygon
    county_alert = {"event": "Winter Storm Watch", "geometry": None, "affects_loudoun": True,
                    "source": "NWS", "source_url": "u", "fetched_at": "t", "expires": None}
    assert set(live.segments_under_alert(segs, [county_alert])) == {1, 2}  # county-wide -> all


def test_usgs_site_strip_and_series_median():
    assert live._usgs_site("USGS-01644000") == "01644000"
    assert live._usgs_site("01644000") == "01644000"
    dv = {"value": {"timeSeries": [{"values": [{"value": [
        {"value": "10"}, {"value": "30"}, {"value": "20"}, {"value": "-999999"}]}]}]}}
    assert live._series_median(dv) == 20.0  # ignores the -999999 sentinel


def test_gage_stress_elevated_vs_own_median_and_distance():
    seg_gages = pd.DataFrame({
        "segment_id": [1, 2, 3], "gage_id": ["USGS-1", "USGS-1", "USGS-2"],
        "gage_distance_m": [5000.0, 99999.0, 5000.0], "gage_name": ["G1", "G1", "G2"]})
    stress = {"USGS-1": {"elevated": True, "current_cfs": 100, "median_cfs": 40,
                         "source_url": "u", "fetched_at": "t"},
              "USGS-2": {"elevated": False, "current_cfs": 5, "median_cfs": 40,
                         "source_url": "u", "fetched_at": "t"}}
    hits = live.segments_gage_stressed(seg_gages, stress)
    assert set(hits) == {1}  # seg2 too far (99999m), seg3 gage not elevated
    assert hits[1]["type"] == "gage" and "cfs" in hits[1]["detail"]


def test_build_watchlist_calm_state_fixed_schema(tmp_path, monkeypatch):
    # feed empty stress everywhere -> calm, but the schema is the same fixed columns
    scores = pd.DataFrame({"segment_id": [1, 2], "score": [55.0, 40.0]})
    segs = gpd.GeoDataFrame({"segment_id": [1, 2]},
                            geometry=[LineString([(0, 0), (0, 1)]), LineString([(1, 1), (1, 2)])],
                            crs="EPSG:4326")
    scores.to_parquet(tmp_path / "scores.parquet")
    segs.to_parquet(tmp_path / "segments.parquet")
    monkeypatch.setattr(live, "SCORES", tmp_path / "scores.parquet")
    monkeypatch.setattr(live, "SEGMENTS", tmp_path / "segments.parquet")
    monkeypatch.setattr(live, "SEGMENT_GAGES", tmp_path / "none.parquet")
    monkeypatch.setattr(live, "fetch_alerts", lambda c: [])
    monkeypatch.setattr(live, "fetch_gage_stress", lambda c, g: {})
    monkeypatch.setattr(live, "fetch_wet_week", lambda c: {
        "wet": False, "total_mm": 0.0, "threshold_mm": 25.4, "station": None,
        "source": "NWS", "source_url": None, "fetched_at": "t"})

    df, meta = live.build_watchlist(client=_Client({}))
    assert list(df.columns) == ["segment_id", "static_score", "watched", "watch_score", "triggers"]
    assert meta["calm"] is True
    assert (df["watched"] == False).all()  # noqa: E712
    assert (df["watch_score"] == 0.0).all()


def test_build_watchlist_active_stress(tmp_path, monkeypatch):
    import json as _json
    scores = pd.DataFrame({"segment_id": [1, 2], "score": [55.0, 40.0]})
    segs = gpd.GeoDataFrame({"segment_id": [1, 2]},
                            geometry=[LineString([(0, 0), (0, 1)]), LineString([(1, 1), (1, 2)])],
                            crs="EPSG:4326")
    scores.to_parquet(tmp_path / "scores.parquet")
    segs.to_parquet(tmp_path / "segments.parquet")
    monkeypatch.setattr(live, "SCORES", tmp_path / "scores.parquet")
    monkeypatch.setattr(live, "SEGMENTS", tmp_path / "segments.parquet")
    monkeypatch.setattr(live, "SEGMENT_GAGES", tmp_path / "none.parquet")
    # a county-wide (null-geometry) flood alert -> every segment watched
    monkeypatch.setattr(live, "fetch_alerts", lambda c: [
        {"event": "Flood Warning", "geometry": None, "affects_loudoun": True,
         "source": "NWS", "source_url": "u", "fetched_at": "t"}])
    monkeypatch.setattr(live, "fetch_gage_stress", lambda c, g: {})
    monkeypatch.setattr(live, "fetch_wet_week", lambda c: {
        "wet": False, "total_mm": 0.0, "threshold_mm": 25.4, "station": None,
        "source": "NWS", "source_url": None, "fetched_at": "t"})

    df, meta = live.build_watchlist(client=_Client({}))
    assert meta["calm"] is False
    assert df["watched"].all()
    assert (df["watch_score"] == df["static_score"]).all()  # gated static score
    assert all(_json.loads(t) for t in df["triggers"])       # every watched row has a trigger


def test_latest_value_reversed_scan_and_none():
    iv = {"value": {"timeSeries": [{"values": [{"value": [
        {"value": "10"}, {"value": "30"}, {"value": "-999999"}]}]}]}}
    assert live._latest_value(iv) == 30.0            # last non-sentinel, scanning from the end
    assert live._latest_value({"value": {"timeSeries": []}}) is None


def test_fetch_wet_week_threshold():
    routes = {
        "observations": {"features": [
            {"properties": {"precipitationLastHour": {"value": 20.0}}},
            {"properties": {"precipitationLastHour": {"value": 10.0}}}]},  # 30mm total >= 25.4
        "stations": {"features": [{"id": "https://api.weather.gov/stations/KTEST"}]},
        "points": {"properties": {
            "observationStations": "https://api.weather.gov/gridpoints/LWX/1,1/stations"}},
    }
    wet = live.fetch_wet_week(_Client(routes))
    assert wet["wet"] is True and wet["total_mm"] == 30.0 and wet["source_url"]
