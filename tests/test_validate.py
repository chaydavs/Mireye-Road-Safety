"""Session 5 validation logic tests (no network, no Access DB)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import validate  # noqa: E402


def test_deterioration_slope_within_construction_cycle():
    # One section, one cycle: MRI rises 0.1/yr over 5 yearly visits -> slope ~0.1.
    rows = []
    for i in range(5):
        rows.append({"STATE_CODE": "51", "SHRP_ID": "0100", "CONSTRUCTION_NO": 1,
                     "VISIT_DATE": f"200{i}-06-01", "MRI": 1.0 + 0.1 * i})
    df = validate.section_deterioration(pd.DataFrame(rows))
    assert len(df) == 1
    assert abs(df.iloc[0]["deterioration"] - 0.1) < 0.01


def test_overlay_does_not_flip_slope_negative():
    # Cycle 1: 4 visits rising over 4 yr. Cycle 2 (after overlay): MRI resets low, 3 visits rising
    # over 5 yr. The longer-span cycle (cycle 2) is kept, and its slope is positive — not the
    # negative slope you'd get fitting across the overlay drop.
    rows = []
    for i in range(4):
        rows.append({"STATE_CODE": "51", "SHRP_ID": "0200", "CONSTRUCTION_NO": 1,
                     "VISIT_DATE": f"200{i}-06-01", "MRI": 2.0 + 0.05 * i})
    for i in range(3):
        rows.append({"STATE_CODE": "51", "SHRP_ID": "0200", "CONSTRUCTION_NO": 2,
                     "VISIT_DATE": f"20{10 + i * 2}-06-01", "MRI": 1.0 + 0.08 * (i * 2)})
    df = validate.section_deterioration(pd.DataFrame(rows))
    assert len(df) == 1
    assert df.iloc[0]["deterioration"] > 0


def test_ground_coef_recovers_known_slope():
    # deterioration = 0.5*ground + 0.1*age + noise-free -> ground coef ~0.5
    rng = np.arange(1, 21, dtype=float)
    ground = rng
    # non-collinear age (digits of pi) so the design matrix is full rank
    age = np.array([3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5, 8, 9, 7, 9, 3, 2, 3, 8, 4], dtype=float)
    logt = np.log1p(rng)
    y = 0.5 * ground + 0.1 * age + 0.2 * logt
    coef = validate._ground_coef(ground, age, logt, y)
    assert abs(coef - 0.5) < 1e-6


def _synthetic_df(n, coupling, seed):
    rs = np.random.RandomState(seed)
    ground = rs.uniform(20, 60, n)
    age = rs.uniform(2, 20, n)
    traffic = rs.uniform(10, 2000, n)
    noise = rs.normal(0, 0.02, n)
    deterioration = coupling * (ground - 40) / 100 + 0.001 * age + noise
    return pd.DataFrame({"ground": ground, "age": age, "traffic": traffic,
                         "deterioration": deterioration})


def test_permutation_detects_planted_signal_and_no_leakage():
    df = _synthetic_df(120, coupling=0.5, seed=1)  # strong ground->deterioration signal
    res = validate.run_test(df)
    assert res["ground_coef"] > 0
    assert res["permutation_p_value"] < 0.05                 # signal is significant
    assert abs(res["shuffled_coef_mean"]) < 0.02             # shuffled labels ~0 -> no leakage


def test_permutation_pure_noise_not_significant():
    df = _synthetic_df(120, coupling=0.0, seed=2)  # ground unrelated to deterioration
    res = validate.run_test(df)
    assert res["permutation_p_value"] > 0.05                 # no effect -> not significant


def test_permutation_is_reproducible():
    df = _synthetic_df(80, coupling=0.3, seed=3)
    assert validate.run_test(df) == validate.run_test(df)   # seeded -> identical


def test_score_ground_drops_unsourced_and_null_never_zero():
    def payload(value, sourced=True):
        return {"value": value, "source": "NRCS" if sourced else None,
                "source_url": "http://x" if sourced else None, "fetched_at": "t" if sourced else None}
    # a fully-sourced high-risk drainage value scores; an UNSOURCED value must be dropped, not used
    p = {"soil_drainage_class": payload("Poorly drained"),          # W -> 1.0
         "soil_shrink_swell_class": payload("Low", sourced=False),  # unsourced -> dropped
         "slope_degrees": payload(20.0)}                            # G -> 1.0
    s = validate.score_ground(p)
    assert s is not None and s > 0
    # all-null/unsourced -> None (not 0)
    assert validate.score_ground({f: payload(None) for f in validate.GROUND_FIELDS}) is None
