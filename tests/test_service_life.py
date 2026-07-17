"""RSL (remaining-service-life) tests — written before service_life.py. Transparent rate-stretch;
a single-year answer is a bug; unknown treatment year takes the prior path at grade C."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import service_life as sl  # noqa: E402


def test_effective_life_rate_stretch():
    # 2x the median deterioration rate consumes a 10-year treatment in ~5 effective years
    assert sl.effective_life(10, 2.0) == 5.0
    assert sl.effective_life(10, 1.0) == 10.0


def test_relative_rate_median_is_one_and_clamped():
    assert sl.relative_rate(50, 50) == 1.0                 # median score -> 1.0x
    assert sl.relative_rate(100, 50) > 1.0                 # higher risk deteriorates faster
    assert sl.MIN_RATE <= sl.relative_rate(1, 50) <= sl.MAX_RATE  # clamped, never absurd


def test_estimated_rsl_is_always_a_range_never_a_single_year():
    r = sl.rsl_for_segment(score=50, median=50, current_year=2026, grade="B",
                           vdot_year=2019, treatment_type="mill and overlay")
    assert r["rsl_estimated"] is True
    assert r["rsl_year_high"] > r["rsl_year_low"]          # a RANGE by construction (a point = bug)


def test_unknown_treatment_year_is_not_estimated_and_caps_grade_c():
    r = sl.rsl_for_segment(score=50, median=50, current_year=2026, grade="A",  # was A...
                           hpms_year=None, vdot_year=None, treatment_type=None)
    assert r["rsl_basis"] == "prior"
    assert r["rsl_estimated"] is False                     # no fabricated window on the prior path
    assert r["grade"] == "C"                                # ...capped to C
    assert r["last_treated_year"] is None                  # never fabricate a treatment year
    assert r["rsl_year_low"] is None and r["rsl_year_high"] is None   # no range shown


def test_estimated_rsl_never_lands_in_the_past():
    # A very old treatment year would compute a past poor-condition year — the floor forbids it.
    r = sl.rsl_for_segment(score=60, median=50, current_year=2026, grade="B",
                           vdot_year=1990, treatment_type="chip seal")
    assert r["rsl_estimated"] is True
    assert r["rsl_year_low"] >= 2026 and r["rsl_year_high"] >= 2026   # never already-failed


def test_prior_basis_renders_as_not_estimated():
    line = sl.render_rsl({"rsl_basis": "prior", "rsl_year_low": None, "rsl_year_high": None,
                          "last_treated_year": None, "grade": "C"})
    assert "not estimated" in line and "no treatment-year data" in line


def test_source_priority_hpms_over_vdot_over_prior():
    common = dict(score=50, median=50, current_year=2026, grade="B", treatment_type="thin overlay")
    assert sl.rsl_for_segment(**common, hpms_year=None, vdot_year=None)["rsl_basis"] == "prior"
    assert sl.rsl_for_segment(**common, hpms_year=None, vdot_year=2019)["rsl_basis"] == "vdot"
    assert sl.rsl_for_segment(**common, hpms_year=2015, vdot_year=2019)["rsl_basis"] == "hpms"


def test_faster_rate_shortens_rsl():
    slow = sl.rsl_for_segment(score=40, median=50, current_year=2026, grade="B",
                              vdot_year=2019, treatment_type="mill and overlay")
    fast = sl.rsl_for_segment(score=60, median=50, current_year=2026, grade="B",
                              vdot_year=2019, treatment_type="mill and overlay")
    assert fast["rsl_year_high"] <= slow["rsl_year_high"]   # more fragile -> reaches poor sooner
