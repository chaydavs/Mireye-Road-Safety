"""Unit tests for fetch.py logic (no network; in-memory SQLite for DB paths)."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd  # noqa: E402

import fetch  # noqa: E402


def _mem_provenance_db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE provenance(point_id TEXT, field TEXT, value TEXT, source TEXT, "
        "source_url TEXT, fetched_at TEXT, confidence TEXT, status TEXT, "
        "PRIMARY KEY(point_id, field))"
    )
    return conn


def test_derive_status_present_absent_failed():
    nm = {"soil_drainage_class": None, "soil_restrictive_layer_depth_cm": "no restrictive layer",
          "nearest_waterbody_name": "no named waterbody within range"}
    # value present -> present
    assert fetch.derive_status("soil_drainage_class", "Well drained", "ok", nm) == "present"
    # Mireye status 'absent' -> absent-semantic (real absence, NOT a failure)
    assert fetch.derive_status("nearest_waterbody_name", None, "absent", nm) == "absent-semantic"
    # ok + null on a field WITH null_meaning -> absent-semantic (semantic absence, not a gap)
    assert fetch.derive_status("soil_restrictive_layer_depth_cm", None, "ok", nm) == "absent-semantic"
    # ok + null on a field WITHOUT null_meaning -> failed (unexpected null, never a value)
    assert fetch.derive_status("soil_drainage_class", None, "ok", nm) == "failed"
    # Mireye status 'failed' -> failed
    assert fetch.derive_status("nearest_bridge_name", None, "failed", nm) == "failed"
    # API error -> failed regardless of value
    assert fetch.derive_status("soil_drainage_class", "x", "error", nm) == "failed"


def test_qa_triage_name_match_keeps():
    assert fetch.qa_triage_decision("Evergreen Mills Rd", "Evergreen Mills Rd", 0.0)[0] == "keep"
    # suffix/case differences still match on the distinctive token
    assert fetch.qa_triage_decision("EVERGREEN MILLS ROAD", "Evergreen Mills Rd", 2.0)[0] == "keep"


def test_qa_triage_name_mismatch_on_road_is_flagged_not_discarded():
    # Same road, different naming systems (Overture street name vs TIGER route designation),
    # point IS on a road (small distance) -> keep with a flag, never discard.
    decision, reason = fetch.qa_triage_decision("East Market Street", "State Rte 7 Bus", 1.5)
    assert decision == "keep_flag"
    assert reason == "name_source_mismatch"


def test_qa_triage_far_from_road_resnaps():
    # A point floating far from any road is the real bad-snap signal -> resnap.
    decision, reason = fetch.qa_triage_decision("Whatever Rd", "Some Rd", 120.0)
    assert decision == "resnap"
    assert reason == "far_from_road"


def test_qa_triage_missing_name_keeps():
    assert fetch.qa_triage_decision(None, "Some Rd", 0.0)[0] == "keep"
    assert fetch.qa_triage_decision("Some Rd", None, 3.0)[0] == "keep"


def test_payloads_from_failed_call_all_failed():
    out = fetch.payloads_from_response(None, 500, ["a", "b"])
    assert out["a"]["status"] == "failed" and out["a"]["value"] is None
    assert set(out) == {"a", "b"}


def test_payloads_from_ok_response():
    data = {"fields": {"a": {"value": 1, "status": "ok", "source": "X"}}}
    out = fetch.payloads_from_response(data, 200, ["a", "b"])
    assert out["a"]["value"] == 1
    assert out["b"]["status"] == "failed"  # requested but absent from response


def test_key_rounds_to_5dp():
    assert fetch.key(39.083612, -77.653499) == (39.08361, -77.6535)


def test_write_provenance_downgrades_incomplete_present():
    conn = _mem_provenance_db()
    nm = {"soil_drainage_class": None, "nearest_waterbody_name": "no waterbody"}
    payloads = {
        "soil_drainage_class": {"value": "Well drained", "status": "ok", "source": "NRCS",
                                "source_url": "http://x", "fetched_at": "t", "confidence": "high"},
        # present value MISSING source_url -> must not be stored as a trusted value
        "nearest_waterbody_name": {"value": "Pond", "status": "ok", "source": "NHD",
                                   "source_url": None, "fetched_at": "t", "confidence": "high"},
    }
    fetch.write_provenance(conn, "p1", payloads, nm)
    rows = dict(conn.execute("SELECT field, status FROM provenance").fetchall())
    assert rows["soil_drainage_class"] == "present"
    assert rows["nearest_waterbody_name"] == "failed"  # incomplete provenance downgraded


def _insert(conn, field, status, n):
    for i in range(n):
        conn.execute(
            "INSERT INTO provenance VALUES(?,?,?,?,?,?,?,?)",
            (f"{field}_{status}_{i}", field, "null", "s", "u", "t", "high", status),
        )


def test_build_audit_kill_fires_on_failed_not_absent():
    conn = _mem_provenance_db()
    # A W/S scoring field that is mostly semantic-absent must NOT trip the kill.
    _insert(conn, "soil_shrink_swell_class", "absent-semantic", 6)
    _insert(conn, "soil_shrink_swell_class", "present", 4)
    # A W/S scoring field with a real data-failure rate > 40% MUST trip the kill.
    _insert(conn, "soil_drainage_class", "failed", 6)
    _insert(conn, "soil_drainage_class", "present", 4)
    conn.commit()
    stats = {"calls": 0, "cache_hits": 0, "rate_limited": 0, "wall_seconds": 0.0, "qa": {}}
    audit = fetch.build_audit(conn, pd.DataFrame({"x": [1, 2, 3]}), stats)

    assert audit["failed_rate_per_field"]["soil_drainage_class"] == 0.6
    assert audit["kill_fired"] is True
    assert "soil_drainage_class" in audit["kill_offenders"]
    # semantic absence is handled downstream, not a kill:
    assert audit["failed_rate_per_field"]["soil_shrink_swell_class"] == 0.0
    assert "soil_shrink_swell_class" not in audit["kill_offenders"]
    # full status breakdown is emitted for the shortfalls report:
    assert audit["status_distribution_per_field"]["soil_shrink_swell_class"]["absent-semantic"] == 6
