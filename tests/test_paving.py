"""VDOT paving tests: contact-field guard, name normalization (no spurious county match), and the
geometric same-road join (a crossing street must NOT match). All offline."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from shapely.geometry import LineString  # noqa: E402

import paving  # noqa: E402


def test_assert_no_contact_blocks_pii():
    with pytest.raises(AssertionError):
        paving.assert_no_contact(pd.DataFrame({"segment_id": [1], "PROJECT_MANAGER": ["Jane"]}), "t")
    paving.assert_no_contact(pd.DataFrame({"segment_id": [1], "year": [2017]}), "t")  # clean: no raise


def test_tokens_strip_county_parenthetical():
    # the spurious bug: "Loudoun" appears in every VDOT name via "(Loudoun County)"
    assert "LOUDOUN" not in paving._tokens("SC-719N (Loudoun County)")
    assert "COUNTY" not in paving._tokens("SC-719N (Loudoun County)")
    # so a street literally named Loudoun does NOT falsely agree with a route in Loudoun County
    assert paving._names_agree("E Loudoun St", "SC-719N (Loudoun County)", None) is False


def test_names_agree_via_streetnames():
    assert paving._names_agree("Old Ox Rd", "SC-606 (Loudoun County)", "OLD OX RD") is True


def test_geometric_join_excludes_crossstreet():
    segs = gpd.GeoDataFrame({"segment_id": [1], "route_name": ["Main St"]},
                            geometry=[LineString([(0, 0), (0, 500)])], crs="EPSG:32618")
    paving_gdf = gpd.GeoDataFrame(
        {"ROUTE_COMMON_NAME": ["SC-1 (Loudoun County)", "SC-2 (Loudoun County)"],
         "STREETNAMES": ["MAIN ST", "CROSS ST"], "pav_year": [2017, 2017],
         "TREATMENT_TYPE": ["PM", "PM"], "SCHEDULE": ["A", "B"]},
        geometry=[LineString([(5, 0), (5, 500)]),        # parallel, 5 m away -> same road (500 m overlap)
                  LineString([(-50, 250), (50, 250)])],  # crossing -> ~50 m overlap, must be excluded
        crs="EPSG:32618")
    cols = {"common": "ROUTE_COMMON_NAME", "streets": "STREETNAMES", "year": "pav_year",
            "treatment": "TREATMENT_TYPE", "schedule": "SCHEDULE", "completed": True}
    matches = paving.join_to_segments(paving_gdf, segs, cols)
    assert list(matches["segment_id"]) == [1]                 # the crossing street didn't add a match
    assert matches.iloc[0]["vdot_route"] == "SC-1 (Loudoun County)"  # the parallel same-road line
    assert matches.iloc[0]["join_confidence"] == "high"       # STREETNAMES "MAIN ST" agrees
    assert matches.iloc[0]["overlap_m"] >= 400                # long overlap = genuinely the same road


def test_plan_comparison_buckets():
    # clean top-decile: quantile(0.9) == 100, so both 100s are top-decile; median == 50.
    scores = pd.DataFrame({
        "segment_id": list(range(1, 11)), "route_name": [str(i) for i in range(1, 11)],
        "score": [100.0, 100.0, 50, 50, 50, 50, 50, 50, 10.0, 10.0], "grade": ["C"] * 10})
    treat = pd.DataFrame({"segment_id": [1, 9], "scheduled": [True, True]})  # a top one + a low one
    _, counts = paving.plan_comparison(scores, treat)
    assert counts["b_high_risk_scheduled"] == 1      # seg 1 (score 100 + scheduled)
    assert counts["a_high_risk_unscheduled"] == 1    # seg 2 (score 100, not scheduled)
    assert counts["c_scheduled_lower_risk"] == 1     # seg 9 (scheduled, score 10 < median)
