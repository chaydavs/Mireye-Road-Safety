"""Unit tests for probe.py pure helpers (no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import probe  # noqa: E402


def test_sample_line_count_and_endpoints():
    pts = probe.sample_line((38.0, -77.0), (39.0, -76.0), 100)
    assert len(pts) == 100
    assert pts[0] == (38.0, -77.0)
    assert pts[-1] == (39.0, -76.0)
    # monotonic in both coords for a SW->NE line
    assert all(pts[i][0] <= pts[i + 1][0] for i in range(len(pts) - 1))


def test_jitter_points_bounds_and_count():
    base = probe.sample_line((38.0, -77.0), (39.0, -76.0), 50)
    jittered = probe.jitter_points(base, scale=0.001)
    assert len(jittered) == len(base)
    for (blat, blng), (jlat, jlng) in zip(base, jittered):
        assert abs(jlat - blat) <= 0.001
        assert abs(jlng - blng) <= 0.001
    # jitter actually moves points (not a no-op)
    assert any(j != b for j, b in zip(jittered, base))


def test_validate_fields_flags_missing_with_closest():
    catalog = [{"name": "soil_drainage_class"}, {"name": "elevation"}]
    missing = probe.validate_fields(catalog, ["soil_drainage_class", "soil_drainage_clas"])
    assert len(missing) == 1
    bad, closest = missing[0]
    assert bad == "soil_drainage_clas"
    assert closest == "soil_drainage_class"  # difflib finds the near-name


def test_validate_fields_all_present():
    catalog = [{"name": "a"}, {"name": "b"}]
    assert probe.validate_fields(catalog, ["a", "b"]) == []


def test_classify_field_buckets():
    assert probe.classify_field({"value": "Well drained", "status": "ok"}) == "value"
    assert probe.classify_field({"value": None, "status": "ok"}) == "null"
    assert probe.classify_field({"value": 1, "status": "error"}) == "failed"
    assert probe.classify_field(None) == "failed"


def test_coverage_extrapolation_math():
    # 3 points per 500 m segment -> 500 m = 0.31069 mi -> 9.656 points/mile
    ex = probe.coverage_extrapolation(calls_per_minute=60.0)
    assert abs(ex["points_per_mile"] - 9.656) < 0.01
    assert ex["calls_per_hour"] == 3600.0
    # 3600 calls/hr / 9.656 pts/mi ~= 372.8 mi/hr
    assert abs(ex["miles_per_hour"] - 372.8) < 1.0


def test_build_report_aggregation():
    """The session's real output math: null-rate percentages and sustained calls/min."""
    counts = {f: {"value": 0, "null": 0, "failed": 0} for f in probe.CORE_FIELDS}
    f0 = probe.CORE_FIELDS[0]
    counts[f0] = {"value": 7, "null": 3, "failed": 0}  # 70% value / 30% null
    probe_data = {
        "n_calls": 10,
        "wall_seconds": 20.0,  # 10 ok calls / 20 s * 60 = 30 calls/min
        "latencies": [2.0] * 10,
        "rate_limited": 0,
        "errors": [],
        "field_counts": counts,
    }
    report = probe.build_report(probe_data, [])
    assert report["throughput"]["calls_per_minute_sustained"] == 30.0
    assert report["null_rates"][f0]["null_pct"] == 30.0
    assert report["null_rates"][f0]["value_pct"] == 70.0
    assert len(report["null_rates"]) == len(probe.CORE_FIELDS)
    probe.assert_report_complete(report)  # self-eval must accept a well-formed report


class _FakeResp:
    def __init__(self, status_code, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._json


class _FakeClient:
    """Returns queued responses in order; records how many posts happened."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def post(self, url, json=None, timeout=None):
        r = self._responses[self.calls]
        self.calls += 1
        return r


def test_fetch_point_counts_recovered_429():
    """A 429 that recovers on the single retry must still be counted (DoD #3)."""
    client = _FakeClient([
        _FakeResp(429, headers={"Retry-After": "0"}),  # Retry-After 0 => no real sleep
        _FakeResp(200, json_data={"fields": {}, "partial_failures": []}),
    ])
    data, latency, status, saw_429 = probe.fetch_point(client, 39.0, -77.0, ["x"])
    assert status == 200
    assert saw_429 is True  # the throttle is visible even though the retry succeeded
    assert client.calls == 2


def test_fetch_point_no_429_when_first_call_ok():
    client = _FakeClient([_FakeResp(200, json_data={"fields": {}, "partial_failures": []})])
    _, _, status, saw_429 = probe.fetch_point(client, 39.0, -77.0, ["x"])
    assert status == 200
    assert saw_429 is False
    assert client.calls == 1
