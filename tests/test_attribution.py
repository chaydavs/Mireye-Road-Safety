"""Attribution honesty tests: shares sum to 1.0; a prior-basis segment has NO Local-records slice;
an older treatment year gives a larger Local-records share; a segment with no VDOT traffic is 100%
Mireye. Weighting is by contribution, never by field count."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import attribution as at  # noqa: E402


def test_prior_segment_has_no_local_records():
    sh = at.normalize(at.static_weights(0.8, rsl_estimated=False, last_treated=None,
                                        rsl_mid=None, current_year=2026))
    assert "Local records" not in sh
    assert set(sh) <= {"Mireye", "VDOT traffic"}
    assert abs(sum(sh.values()) - 1.0) < 1e-6


def test_estimated_segment_has_local_records_and_sums_to_one():
    sh = at.normalize(at.static_weights(0.8, rsl_estimated=True, last_treated=2017,
                                        rsl_mid=2030, current_year=2026))
    assert sh.get("Local records", 0) > 0
    assert abs(sum(sh.values()) - 1.0) < 1e-6


def test_all_mireye_when_no_traffic():
    # mireye_share 1.0 -> VDOT traffic weight 0 -> dropped by normalize
    sh = at.normalize(at.static_weights(1.0, rsl_estimated=False, last_treated=None,
                                        rsl_mid=None, current_year=2026))
    assert sh == {"Mireye": 1.0}


def test_older_treatment_gives_larger_local_records_share():
    old = at.normalize(at.static_weights(0.8, True, 2000, 2028, 2026))
    new = at.normalize(at.static_weights(0.8, True, 2020, 2028, 2026))
    assert old["Local records"] > new["Local records"]
