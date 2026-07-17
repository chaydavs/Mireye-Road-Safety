"""Remaining Service Life (RSL): turn the static fragility score into a YEAR RANGE for reaching
poor condition, using the transparent rate-stretch agencies use — NOT a survival model or ML.

RSL = (treatment expected life / our relative deterioration rate) - elapsed age since last treatment.
A single-year answer is a bug by definition; the output is always a range (from the treatment
lifespan range and the age uncertainty).

Age-since-last-treatment source priority (never fabricate a year):
  (a) HPMS "Year of Last Improvement" (Field Manual item 54) on federal-aid roads — loaded from
      data/hpms_treatment.parquet if present (federal-aid subset only);
  (b) VDOT paving completions (src/paving.py, basis 'vdot_paving');
  (c) otherwise a functional-class prior on age with WIDE uncertainty, grade capped at C.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))

REPO = SRC.parent
DATA = REPO / "data"
SCORES = DATA / "scores.parquet"
TREATMENT = DATA / "segment_treatment.parquet"     # from src/paving.py
HPMS_TREATMENT = DATA / "hpms_treatment.parquet"   # optional: segment_id -> hpms_year (federal-aid)

# FHWA pavement-preservation service lives (years), RANGES not points, cited per row.
TREATMENT_LIFE = {
    "chip seal":        (4, 7),    # FHWA Pavement Preservation: bituminous surface treatment / chip seal
    "slurry/micro":     (5, 8),    # FHWA: slurry seal / micro-surfacing
    "thin overlay":     (7, 12),   # FHWA: thin hot-mix asphalt overlay
    "mill and overlay": (12, 18),  # FHWA/NCHRP: mill & structural HMA overlay
    "reconstruction":   (20, 30),  # FHWA: full-depth reconstruction / new pavement
}
DEFAULT_LIFE = (8, 12)  # unknown treatment type -> a mid overlay-grade range
# VDOT TREATMENT_TYPE codes -> a lifespan family above.
VDOT_TREATMENT_MAP = {
    "PM": "mill and overlay", "SM": "thin overlay", "LM": "thin overlay", "TL": "thin overlay",
    "CM": "mill and overlay", "ST": "chip seal", "SL": "slurry/micro", "MS": "slurry/micro",
    "RC": "reconstruction",
}
MIN_RATE, MAX_RATE = 0.5, 2.0
GRADE_RANK = {"A": 3, "B": 2, "C": 1}


def effective_life(expected_life: float, rate: float) -> float:
    """A faster deterioration rate consumes the treatment's life proportionally faster."""
    return expected_life / rate


def relative_rate(score: float, median: float) -> float:
    """Score-proportional relative deterioration rate (median score -> 1.0x), clamped. The LTPP
    test directionally validated 'higher ground risk -> faster deterioration' (weakly), so this is
    screening-grade — hence the wide output range, not false precision."""
    if not median:
        return 1.0
    return max(MIN_RATE, min(MAX_RATE, score / median))


def treatment_life(treatment_type) -> tuple[int, int]:
    if isinstance(treatment_type, str):
        if treatment_type in TREATMENT_LIFE:
            return TREATMENT_LIFE[treatment_type]
        mapped = VDOT_TREATMENT_MAP.get(treatment_type.strip().upper())
        if mapped:
            return TREATMENT_LIFE[mapped]
    return DEFAULT_LIFE


def _cap_grade(grade: str | None, cap: str) -> str:
    if grade and GRADE_RANK.get(grade, 1) <= GRADE_RANK[cap]:
        return grade
    return cap


def rsl_for_segment(score: float, median: float, current_year: int, grade: str,
                    hpms_year: int | None = None, vdot_year: int | None = None,
                    treatment_type=None) -> dict:
    """Resolve the age source, then the transparent rate-stretch RSL as a year range — but ONLY
    when a real treatment year is known (HPMS or VDOT). Without one we do not fabricate a window:
    the functional-class prior produced past 'already failed' years (worse than nothing), so a
    prior-basis segment is reported as not-estimated. Estimated ranges are floored at the current
    year; a road is never displayed as already poor."""
    if hpms_year is not None:
        basis, treated = "hpms", int(hpms_year)
    elif vdot_year is not None:
        basis, treated = "vdot", int(vdot_year)
    else:
        # No real treatment year -> say so rather than invent a range. Confidence capped at C.
        return {"rsl_year_low": None, "rsl_year_high": None, "rsl_basis": "prior",
                "last_treated_year": None, "grade": _cap_grade(grade, "C"), "rsl_estimated": False}

    age = current_year - treated
    life = treatment_life(treatment_type)
    rate = relative_rate(score, median)
    year_low = current_year + effective_life(life[0], rate) - age   # shortest life
    year_high = current_year + effective_life(life[1], rate) - age  # longest life
    # Floor at the current year: an overdue road reads as 'due now', never as already-failed.
    year_low, year_high = max(current_year, round(year_low)), max(current_year, round(year_high))
    return {
        "rsl_year_low": int(year_low), "rsl_year_high": int(year_high),
        "rsl_basis": basis, "last_treated_year": treated, "grade": grade, "rsl_estimated": True,
    }


def render_rsl(row: dict) -> str:
    """Why-card line. A year range is shown only when a real treatment year is known (HPMS/VDOT);
    a prior-basis segment says the data is missing instead of inventing a past window."""
    if row.get("rsl_basis") == "prior" or row.get("rsl_year_low") is None:
        return "no treatment-year data for this segment; RSL not estimated (grade C)"
    basis_txt = {"hpms": "last treated {yr} per HPMS", "vdot": "last treated {yr} per VDOT paving"}
    tail = basis_txt[row["rsl_basis"]].format(yr=int(row["last_treated_year"]))
    lo, hi = int(row["rsl_year_low"]), int(row["rsl_year_high"])
    window = f"{lo}" if lo == hi else f"{lo}–{hi}"
    overdue = " (at or past due)" if lo == hi == datetime.now(timezone.utc).year else ""
    return (f"estimated to reach poor condition {window}{overdue} "
            f"(grade {row['grade']}; {tail})")


def annotate_scores() -> pd.DataFrame:
    """Add rsl_year_low/high, rsl_basis, rsl_estimated (+ rsl_last_treated, rsl_grade) to
    scores.parquet. Year bounds are null when rsl_estimated is False (prior basis)."""
    scores = pd.read_parquet(SCORES)
    treat = pd.read_parquet(TREATMENT) if TREATMENT.exists() else pd.DataFrame()
    hpms = pd.read_parquet(HPMS_TREATMENT) if HPMS_TREATMENT.exists() else pd.DataFrame()
    current_year = datetime.now(timezone.utc).year
    median = scores["score"].median()

    vdot_year, vdot_type = {}, {}
    if not treat.empty:
        done = treat[treat["basis"] == "vdot_paving"]
        vdot_year = dict(zip(done["segment_id"], done["last_treated_year"]))
        vdot_type = dict(zip(done["segment_id"], done["treatment_type"]))
    hpms_year = dict(zip(hpms["segment_id"], hpms["hpms_year"])) if not hpms.empty else {}

    rows = []
    for r in scores.itertuples(index=False):
        sid = int(r.segment_id)
        res = rsl_for_segment(
            score=float(r.score), median=median, current_year=current_year, grade=r.grade,
            hpms_year=hpms_year.get(sid),
            vdot_year=int(vdot_year[sid]) if sid in vdot_year and pd.notna(vdot_year[sid]) else None,
            treatment_type=vdot_type.get(sid))
        rows.append(res)
    rsl = pd.DataFrame(rows)
    return scores.assign(rsl_year_low=rsl["rsl_year_low"].values,
                         rsl_year_high=rsl["rsl_year_high"].values,
                         rsl_basis=rsl["rsl_basis"].values,
                         rsl_estimated=rsl["rsl_estimated"].values,
                         rsl_last_treated=rsl["last_treated_year"].values,
                         rsl_grade=rsl["grade"].values)


def main() -> int:
    out = annotate_scores()
    out.to_parquet(SCORES)
    print(f"Annotated {len(out)} segments with RSL. basis: "
          f"{out['rsl_basis'].value_counts().to_dict()}")

    # Hard invariant: no ESTIMATED segment may land in the past (the floor guarantees it).
    current_year = datetime.now(timezone.utc).year
    est = out[out["rsl_estimated"]]
    past = est[est["rsl_year_high"] < current_year]
    print(f"\nestimated: {len(est)} (hpms/vdot) · not-estimated: {len(out) - len(est)} (prior)")
    if len(past):
        print(f"STOP: {len(past)} estimated segments show a past poor-condition year — the floor "
              "failed; investigate before trusting RSL.")
        return 3

    print("\n5 sample why-card lines:")
    for r in out.sample(min(5, len(out)), random_state=0).itertuples(index=False):
        print("  " + render_rsl({
            "rsl_year_low": r.rsl_year_low, "rsl_year_high": r.rsl_year_high,
            "rsl_basis": r.rsl_basis, "last_treated_year": r.rsl_last_treated,
            "grade": r.rsl_grade}))
    print(f"\nWrote RSL columns to {SCORES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
