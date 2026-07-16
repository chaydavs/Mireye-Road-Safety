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
SEGMENTS = DATA / "segments.parquet"
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
# Functional-class prior on elapsed age (years) when no treatment year is known — WIDE by design.
FUNCTIONAL_PRIOR_AGE = {"S1200": (8, 16), "S1400": (10, 20)}
DEFAULT_PRIOR_AGE = (10, 20)
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


def rsl_for_segment(score: float, median: float, current_year: int, grade: str, mtfcc: str,
                    hpms_year: int | None = None, vdot_year: int | None = None,
                    treatment_type=None) -> dict:
    """Resolve the age source (never fabricating a year), then the transparent rate-stretch RSL
    as a year range."""
    if hpms_year is not None:
        basis, treated, age_lo, age_hi, out_grade = "hpms", int(hpms_year), \
            current_year - hpms_year, current_year - hpms_year, grade
        life = treatment_life(treatment_type)
    elif vdot_year is not None:
        basis, treated, age_lo, age_hi, out_grade = "vdot", int(vdot_year), \
            current_year - vdot_year, current_year - vdot_year, grade
        life = treatment_life(treatment_type)
    else:
        basis, treated = "prior", None
        age_lo, age_hi = FUNCTIONAL_PRIOR_AGE.get(mtfcc, DEFAULT_PRIOR_AGE)
        out_grade = _cap_grade(grade, "C")   # prior -> confidence capped at C
        life = DEFAULT_LIFE                    # unknown treatment type on the prior path

    rate = relative_rate(score, median)
    # low year: shortest life, oldest age; high year: longest life, youngest age
    year_low = current_year + effective_life(life[0], rate) - age_hi
    year_high = current_year + effective_life(life[1], rate) - age_lo
    return {
        "rsl_year_low": int(round(year_low)), "rsl_year_high": int(round(year_high)),
        "rsl_basis": basis, "last_treated_year": treated, "grade": out_grade,
    }


def render_rsl(row: dict) -> str:
    """Why-card line, e.g. 'estimated to reach poor condition 2029-2032 (grade B; last treated 2019
    per VDOT paving)'."""
    basis_txt = {"hpms": "last treated {yr} per HPMS", "vdot": "last treated {yr} per VDOT paving",
                 "prior": "treatment year unknown — functional-class prior"}
    tail = basis_txt[row["rsl_basis"]].format(yr=row["last_treated_year"])
    return (f"estimated to reach poor condition {row['rsl_year_low']}–{row['rsl_year_high']} "
            f"(grade {row['grade']}; {tail})")


def annotate_scores() -> pd.DataFrame:
    """Add rsl_year_low, rsl_year_high, rsl_basis (+ rsl_last_treated, rsl_grade) to scores.parquet."""
    scores = pd.read_parquet(SCORES)
    segs = pd.read_parquet(SEGMENTS)[["segment_id", "mtfcc"]] if "mtfcc" in \
        pd.read_parquet(SEGMENTS).columns else None
    treat = pd.read_parquet(TREATMENT) if TREATMENT.exists() else pd.DataFrame()
    hpms = pd.read_parquet(HPMS_TREATMENT) if HPMS_TREATMENT.exists() else pd.DataFrame()
    current_year = datetime.now(timezone.utc).year
    median = scores["score"].median()

    mtfcc = dict(zip(segs["segment_id"], segs["mtfcc"])) if segs is not None else {}
    vdot_year = {}
    vdot_type = {}
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
            mtfcc=mtfcc.get(sid, ""), hpms_year=hpms_year.get(sid),
            vdot_year=int(vdot_year[sid]) if sid in vdot_year and pd.notna(vdot_year[sid]) else None,
            treatment_type=vdot_type.get(sid))
        rows.append(res)
    rsl = pd.DataFrame(rows)
    out = scores.assign(rsl_year_low=rsl["rsl_year_low"].values,
                        rsl_year_high=rsl["rsl_year_high"].values,
                        rsl_basis=rsl["rsl_basis"].values,
                        rsl_last_treated=rsl["last_treated_year"].values,
                        rsl_grade=rsl["grade"].values)
    return out


def main() -> int:
    out = annotate_scores()
    out.to_parquet(SCORES)
    print(f"Annotated {len(out)} segments with RSL. basis: "
          f"{out['rsl_basis'].value_counts().to_dict()}")

    # Sanity check: 10 random segments; a segment already in the past should also be high-score.
    current_year = datetime.now(timezone.utc).year
    sample = out.sample(10, random_state=0)
    med = out["score"].median()
    print("\n10 random segments (score, RSL range, basis):")
    suspicious = []
    for r in sample.itertuples(index=False):
        past = r.rsl_year_high < current_year
        print(f"  seg {r.segment_id} score {r.score} ({r.grade}): "
              f"{r.rsl_year_low}-{r.rsl_year_high} [{r.rsl_basis}]"
              f"{'  <-- already poor' if past else ''}")
        if past and r.score < med:  # a low-risk road showing already-failed = wrong age source
            suspicious.append(int(r.segment_id))
    if suspicious:
        print(f"\nSTOP: low-risk segments show as already poor {suspicious} — age source likely "
              "wrong; investigate before trusting RSL.")
        return 3
    ex = sample.iloc[0]
    print("\nrender example: " + render_rsl({
        "rsl_year_low": int(ex["rsl_year_low"]), "rsl_year_high": int(ex["rsl_year_high"]),
        "rsl_basis": ex["rsl_basis"], "last_treated_year": ex["rsl_last_treated"],
        "grade": ex["rsl_grade"]}))
    print(f"Wrote RSL columns to {SCORES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
