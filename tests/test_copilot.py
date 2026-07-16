"""Session 6 copilot tool tests — query_scores is offline (reads scores.parquet + provenance),
no Anthropic key needed. The tool-use loop / model call is covered by the manual transcripts."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "agents"))

import copilot  # noqa: E402


def test_query_scores_top_returns_ranked_rows():
    rows = copilot.query_scores("top", n=3)
    assert isinstance(rows, list) and 1 <= len(rows) <= 3
    assert {"segment_id", "score", "grade", "top_drivers"}.issubset(rows[0])
    # "top" is ranked descending by score
    assert rows == sorted(rows, key=lambda r: -r["score"])


def test_query_scores_bad_segment_returns_error_not_crash():
    res = copilot.query_scores("segment", segment_id=-999999)
    assert isinstance(res, dict) and "error" in res


def test_query_scores_segment_includes_cited_provenance():
    top_id = copilot.query_scores("top", n=1)[0]["segment_id"]
    res = copilot.query_scores("segment", segment_id=top_id)
    assert res["segment_id"] == top_id
    assert "provenance" in res  # driver fields carry source + source_url


def test_query_scores_unknown_action():
    assert "error" in copilot.query_scores("nonsense")
