"""Session 6 app render smoke tests (no Streamlit server): the map builds and the why-card lines
are cited. A headless `streamlit run` launch is checked separately in the session's smoke script."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import folium  # noqa: E402

import app  # noqa: E402


def test_load_scored_has_geometry_and_scores():
    g = app.load_scored()
    assert len(g) > 0
    assert {"score", "grade", "segment_id"}.issubset(g.columns)
    assert g.geometry.notna().all()


def test_build_map_renders_segment_layers():
    g = app.load_scored().head(20)
    m = app.build_map(g)
    assert isinstance(m, folium.Map)
    assert "seg " in m._repr_html_()  # per-segment tooltip present


def test_build_map_is_a_single_layer_not_per_segment():
    """Perf guard: the whole network renders as ONE GeoJson layer, not one layer per segment.
    Per-segment layers shipped a ~5 MB, 2,600-layer map on every rerun and lagged the browser."""
    g = app.load_scored()  # full network, not .head()
    m = app.build_map(g)
    # base tile layer + one segments layer — a handful, never thousands
    assert len(m._children) <= 4, f"expected a single segments layer, got {len(m._children)}"


def test_why_card_lines_are_all_cited():
    g = app.load_scored()
    r = g.sort_values("score", ascending=False).iloc[0]
    lines = app.provenance_lines(int(r["segment_id"]), json.loads(r["top3"]))
    assert lines  # the top segment has drivers
    # every line carries a source (a URL, or the named VDOT source for AADT)
    assert all(("http" in ln) or ("VDOT" in ln) for ln in lines)


def test_app_runs_headless_and_renders():
    """Streamlit AppTest executes app.main() headless: no exception, map + why-card + chat present."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "src" / "app.py"))
    at.run(timeout=60)
    assert not at.exception, at.exception
    assert any("Subgrade" in t.value for t in at.title)
    subs = [s.value for s in at.subheader]
    assert any("Why-card" in s for s in subs)         # why-card rendered
    assert any("copilot" in s.lower() for s in subs)  # chat section rendered
    assert len(at.metric) >= 1                          # the risk-score metric rendered


def test_data_age_branches():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    assert "min ago" in app.data_age((now - timedelta(minutes=5)).isoformat())
    assert "h ago" in app.data_age((now - timedelta(hours=5)).isoformat())
    assert app.data_age(None) == "unknown age"
    assert app.data_age("not-a-date") == "unknown age"


def test_trigger_lines_are_cited():
    tj = json.dumps([{"type": "alert", "detail": "Flood Warning", "source": "NWS",
                      "source_url": "http://x", "at": "2026-07-16T20:00:00+00:00"}])
    lines = app.trigger_lines(tj)
    assert lines and "Flood Warning" in lines[0] and "http://x" in lines[0]
    assert app.trigger_lines("[]") == []


def test_app_live_mode_renders_calm_or_stress_banner():
    """The 'Right now' toggle must render a banner (calm or stress), never error — demo day is sunny."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "src" / "app.py"))
    at.run(timeout=60)
    at.toggle[0].set_value(True).run(timeout=60)
    assert not at.exception, at.exception
    assert len(at.success) + len(at.error) + len(at.info) >= 1  # a live-state banner rendered


def test_app_demo_mode_renders_offline(monkeypatch):
    """Demo mode renders from local data only (no network in the render path) — airplane-mode safe."""
    monkeypatch.setenv("SUBGRADE_DEMO", "1")
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "src" / "app.py"))
    at.run(timeout=60)
    assert not at.exception, at.exception
    assert any("DEMO MODE" in c.value for c in at.caption)  # demo banner shown
    at.toggle[0].set_value(True).run(timeout=60)             # live layer from snapshot, offline
    assert not at.exception, at.exception
