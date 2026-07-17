"""Session 4 why-card hard-rule tests: no factual line without a provenance row/source; the
housing-density proxy is never emitted as an AADT/VDOT claim."""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "agents"))

import why_card  # noqa: E402


def _mem_db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE provenance(point_id TEXT, field TEXT, value TEXT, source TEXT, "
        "source_url TEXT, fetched_at TEXT, confidence TEXT, status TEXT, "
        "PRIMARY KEY(point_id, field))"
    )
    return conn


def _put(conn, point_id, field, value, source, source_url, status="present"):
    conn.execute(
        "INSERT INTO provenance VALUES(?,?,?,?,?,?,?,?)",
        (point_id, field, json.dumps(value), source, source_url, "2026-07-16T00:00:00Z",
         "high", status),
    )


def test_provenance_line_requires_source_url():
    conn = _mem_db()
    # complete provenance -> a cited line with the URL
    _put(conn, "5_0", "soil_drainage_class", "Poorly drained", "NRCS_gNATSGO", "http://x")
    line = why_card.provenance_line(conn, 5, "soil_drainage_class")
    assert line and "http://x" in line and "Poorly drained" in line
    # missing source_url -> NO line (hard rule: no provenance row, no sentence)
    _put(conn, "6_0", "soil_drainage_class", "Poorly drained", "NRCS_gNATSGO", None)
    assert why_card.provenance_line(conn, 6, "soil_drainage_class") is None


def test_segment_id_match_is_exact_not_prefix():
    conn = _mem_db()
    # segment 1 must NOT pick up segment 10/15's rows (underscore-wildcard bug guard)
    _put(conn, "10_0", "slope_degrees", 20.0, "USGS", "http://u")
    assert why_card.provenance_line(conn, 1, "slope_degrees") is None


def test_proxy_traffic_is_never_labeled_aadt():
    conn = _mem_db()
    # a housing-density proxy driver must be cited from its own provenance row, not as AADT/VDOT
    _put(conn, "7_0", "housing_units_density_per_km2", 500.0, "US_CENSUS", "http://census")
    seg_row = {
        "segment_id": 7, "route_name": "Some Rd", "score": 50.0, "grade": "C",
        "drivers": json.dumps([
            {"component": "housing_units_density_per_km2", "value": 500.0, "contribution": 0.1},
        ]),
    }
    from shapely.geometry import LineString
    card = why_card.compose_card(conn, seg_row, LineString([(0, 0), (0, 0.001)]))
    joined = " ".join(card["cited_lines"])
    assert "AADT" not in joined           # no fabricated AADT claim
    assert "housing_units_density_per_km2" in joined and "http://census" in joined


def test_real_aadt_cites_vdot():
    conn = _mem_db()
    seg_row = {
        "segment_id": 8, "route_name": "Route 7", "score": 60.0, "grade": "B",
        "drivers": json.dumps([{"component": "traffic_aadt", "value": 40000.0, "contribution": 0.2}]),
    }
    from shapely.geometry import LineString
    card = why_card.compose_card(conn, seg_row, LineString([(0, 0), (0, 0.001)]))
    joined = " ".join(card["cited_lines"])
    assert "AADT = 40000.0" in joined and "VDOT" in joined and "http" in joined
