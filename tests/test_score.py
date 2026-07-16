"""Session 4 scoring tests — lookup tables and factor/grade logic (written before score.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import score  # noqa: E402


def test_drainage_lookup_endpoints():
    # PRD section 6 example: poorly drained -> 1.0, well drained -> 0.2
    assert score.component_score("soil_drainage_class", "Poorly drained") == 1.0
    assert score.component_score("soil_drainage_class", "Well drained") == 0.2
    assert score.component_score("soil_drainage_class", "Very poorly drained") == 1.0


def test_unknown_category_is_missing_not_zero():
    # An unmappable value is missing (None), never silently 0.0 (CLAUDE.md null handling).
    assert score.component_score("soil_drainage_class", "Nonsense") is None
    assert score.component_score("soil_drainage_class", None) is None


def test_boolean_and_hydrologic_dual_group():
    assert score.component_score("within_floodplain_polygon", True) == 1.0
    assert score.component_score("within_floodplain_polygon", False) == 0.0
    # dual hydrologic group uses the worse (undrained) letter: C/D -> D -> 1.0
    assert score.component_score("soil_hydrologic_group", "C/D") == 1.0
    assert score.component_score("soil_hydrologic_group", "A") == 0.2


def test_numeric_direction():
    # slope: steeper = worse
    assert score.component_score("slope_degrees", 20.0) == 1.0
    assert score.component_score("slope_degrees", 1.0) < 0.5
    # wetland distance: closer = worse (reverse direction)
    assert score.component_score("nearest_wetland_distance_m", 10.0) == 1.0
    assert score.component_score("nearest_wetland_distance_m", 480.0) < 0.5


def test_factor_drops_missing_components_not_zero():
    # A factor is the MEAN of present components; missing ones drop out (not counted as 0).
    # "Poorly drained" -> 1.0; the ponding component is missing (None) and must drop out.
    fs = score.factor_score({"soil_drainage_class": "Poorly drained",
                             "soil_ponding_frequency_class": None})
    assert fs == 1.0  # mean of the one present component, not (1.0 + 0)/2 = 0.5
    assert score.factor_score({"soil_drainage_class": None,
                               "soil_ponding_frequency_class": None}) is None


def test_all_null_water_drops_and_caps_grade_c():
    # A segment with no W data: W factor is None, and grade is capped at C.
    result = score.score_segment(
        field_values={"slope_degrees": 2.0, "landslide_susceptibility_index": 40.0},
        field_conf={"slope_degrees": "high", "landslide_susceptibility_index": "high"},
        aadt=5000.0, traffic_source="vdot_spatial", housing_density=None,
    )
    assert result["factors"]["W"] is None       # water dropped
    assert result["grade"] == "C"               # missing load-bearing W caps grade
    assert 0.0 <= result["score"] <= 100.0


def _full_ws():
    """All 11 W/S fields present with valid, mappable values."""
    return {
        "soil_drainage_class": "Well drained", "soil_ponding_frequency_class": "None",
        "within_floodplain_polygon": False, "fema_flood_zone": "X",
        "surface_water_permanence_pct": 0.0, "nearest_wetland_distance_m": 300.0,
        "soil_available_water_capacity": 0.15, "soil_hydrologic_group": "B",
        "soil_shrink_swell_class": "Low", "soil_erodibility_k_factor": 0.3, "bedrock_depth_cm": 100.0,
    }


def test_grade_a_requires_all_ws_present_and_high():
    fv = _full_ws()
    result = score.score_segment(
        field_values=fv, field_conf={f: "high" for f in fv},
        aadt=5000.0, traffic_source="vdot_spatial", housing_density=None,
    )
    assert result["grade"] == "A"


def test_missing_ws_component_lowers_grade_to_b():
    # PRD: absence lowers the grade. Drop one W field -> A becomes B even if all present are high.
    fv = _full_ws()
    del fv["soil_hydrologic_group"]
    result = score.score_segment(
        field_values=fv, field_conf={f: "high" for f in fv},
        aadt=5000.0, traffic_source="vdot_spatial", housing_density=None,
    )
    assert result["grade"] == "B"


def test_medium_confidence_lowers_grade_to_b():
    fv = _full_ws()
    conf = {f: "high" for f in fv}
    conf["soil_drainage_class"] = "medium"
    result = score.score_segment(
        field_values=fv, field_conf=conf,
        aadt=5000.0, traffic_source="vdot_spatial", housing_density=None,
    )
    assert result["grade"] == "B"


def test_traffic_proxy_caps_grade_c():
    result = score.score_segment(
        field_values={"soil_drainage_class": "Poorly drained", "soil_shrink_swell_class": "Low"},
        field_conf={"soil_drainage_class": "high", "soil_shrink_swell_class": "high"},
        aadt=None, traffic_source="none", housing_density=500.0,
    )
    assert result["grade"] == "C"  # housing-density proxy for traffic downgrades
