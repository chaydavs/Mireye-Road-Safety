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
