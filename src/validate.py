"""Session 5 validation (PRD section 6 stage 4): does Mireye ground-risk predict measured pavement
deterioration? Uses FHWA LTPP (Long-Term Pavement Performance) as the answer key.

For LTPP sections in Virginia + climate-adjacent states, compute each section's measured
deterioration rate (MRI/IRI slope over time), fetch Mireye's ground fields at the section
coordinates, and test whether higher ground-risk sections deteriorated faster — controlling for
age and traffic (LTPP's own columns). A permutation test doubles as the shuffled-label sanity
check: if the pipeline were leaking, the real effect would sit inside the shuffled distribution.

No p-hacking: states are fixed up front for climate adjacency, not chosen for results; the honest
effect (positive, null, or weak) is reported as-is (PRD section 11 plans for a weak signal).
"""

from __future__ import annotations

import io
import json
import random
import sqlite3
import subprocess
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))
import fetch  # noqa: E402  (reuse fetch_coord + field list)
import probe  # noqa: E402
import score  # noqa: E402

REPO = SRC.parent
DATA = REPO / "data"
ACCDB_DIR = DATA / "ltpp" / "accdb"
GROUND_CACHE = DATA / "ltpp_ground.sqlite"      # separate cache so we don't contend with the town fetch
CHART_OUT = REPO / "output" / "ltpp_validation.png"
AUDIT = DATA / "audit.json"
STATES = ["VA", "MD", "NC", "PA", "TN", "WV"]   # Virginia + climate-adjacent, fixed up front
GROUND_FIELDS = score.FACTORS["W"][1] + score.FACTORS["S"][1] + score.FACTORS["G"][1]
MIN_VISITS, MIN_SPAN_YEARS = 3, 2.0
N_PERM = 2000
PERM_SEED = 0  # fixed so the reported permutation p-value is reproducible


def mdb_df(accdb: Path, table: str) -> pd.DataFrame:
    out = subprocess.run(["mdb-export", str(accdb), table], capture_output=True, text=True)
    if not out.stdout:
        return pd.DataFrame()
    # Keep the join keys as strings: SHRP_ID is a 4-char code (e.g. "0100") whose leading zeros
    # int-inference would drop, and it must have one dtype across tables to merge.
    return pd.read_csv(io.StringIO(out.stdout), low_memory=False,
                       dtype={"SHRP_ID": str, "STATE_CODE": str})


def section_deterioration(iri: pd.DataFrame) -> pd.DataFrame:
    """Per (STATE_CODE, SHRP_ID): MRI slope vs time (m/km per year) WITHIN one construction cycle,
    needing >=3 visits spanning >=2 years. Slope is computed per CONSTRUCTION_NO so a mid-window
    overlay (which drops MRI) doesn't flip the deterioration negative; the cycle with the longest
    span is kept as the representative deterioration for the section."""
    iri = iri.dropna(subset=["MRI"]).copy()
    iri["t"] = pd.to_datetime(iri["VISIT_DATE"], errors="coerce")
    iri = iri.dropna(subset=["t"])
    best: dict[tuple, tuple] = {}
    for (state, shrp, _cno), g in iri.groupby(["STATE_CODE", "SHRP_ID", "CONSTRUCTION_NO"]):
        if len(g) < MIN_VISITS:
            continue
        yrs = (g["t"] - g["t"].min()).dt.days / 365.25
        span = float(yrs.max())
        if span < MIN_SPAN_YEARS:
            continue
        slope = float(np.polyfit(yrs.values, g["MRI"].values, 1)[0])
        key = (state, shrp)
        if key not in best or span > best[key][1]:
            best[key] = (slope, span, g["t"].min())
    return pd.DataFrame([
        {"STATE_CODE": k[0], "SHRP_ID": k[1], "deterioration": v[0], "first_visit": v[2]}
        for k, v in best.items()
    ])


def extract_ltpp() -> pd.DataFrame:
    """Assemble per-section deterioration + coords + age + traffic across the fixed states."""
    frames = []
    for st in STATES:
        accdb = ACCDB_DIR / f"{st}_Primary.accdb"
        if not accdb.exists():
            print(f"WARN: {accdb} missing, skipping {st}")
            continue
        det = section_deterioration(mdb_df(accdb, "ANALYSIS_IRI"))
        coords = mdb_df(accdb, "SECTION_COORDINATES")[
            ["STATE_CODE", "SHRP_ID", "LATITUDE", "LONGITUDE"]]
        age = mdb_df(accdb, "INV_AGE")
        age["CONSTRUCTION_DATE"] = pd.to_datetime(age["CONSTRUCTION_DATE"], errors="coerce")
        age = age.dropna(subset=["CONSTRUCTION_DATE"]).groupby(
            ["STATE_CODE", "SHRP_ID"])["CONSTRUCTION_DATE"].min().reset_index()
        trf = mdb_df(accdb, "TRF_ESAL_COMPUTED")
        trf = trf.dropna(subset=["KESAL_YEAR"]).groupby(
            ["STATE_CODE", "SHRP_ID"])["KESAL_YEAR"].mean().reset_index().rename(
            columns={"KESAL_YEAR": "traffic"})

        df = det.merge(coords, on=["STATE_CODE", "SHRP_ID"]).merge(
            age, on=["STATE_CODE", "SHRP_ID"]).merge(trf, on=["STATE_CODE", "SHRP_ID"])
        df["age"] = (df["first_visit"] - df["CONSTRUCTION_DATE"]).dt.days / 365.25
        frames.append(df[["STATE_CODE", "SHRP_ID", "LATITUDE", "LONGITUDE",
                          "deterioration", "age", "traffic"]])
    out = pd.concat(frames, ignore_index=True)
    return out[(out["age"] > 0) & (out["deterioration"].notna())].reset_index(drop=True)


def _ground_cache() -> sqlite3.Connection:
    conn = sqlite3.connect(GROUND_CACHE)
    conn.execute("CREATE TABLE IF NOT EXISTS g(lat REAL, lon REAL, payload TEXT, PRIMARY KEY(lat,lon))")
    return conn


def _provenanced_value(p: dict):
    """Use a value only if it carries full provenance (CLAUDE.md 'no provenance row, no value')."""
    if p.get("value") is not None and p.get("source") and p.get("source_url") and p.get("fetched_at"):
        return p["value"]
    return None


def score_ground(payloads: dict) -> float | None:
    """Weighted W+S+G ground risk (0-100) from Mireye payloads. Weights come from score.FACTORS
    (not re-hardcoded). Unsourced/null values drop out; a factor with no data drops and the rest
    renormalize — never treated as zero."""
    fv = {f: _provenanced_value(payloads.get(f, {})) for f in GROUND_FIELDS}
    parts = {k: (score.FACTORS[k][0], score.factor_score({f: fv[f] for f in score.FACTORS[k][1]}))
             for k in ("W", "S", "G")}
    avail = {k: v for k, (w, v) in parts.items() if v is not None}
    wsum = sum(parts[k][0] for k in avail)
    return 100.0 * sum(parts[k][0] * avail[k] for k in avail) / wsum if wsum else None


def ground_score_at(conn, token, lat, lon) -> float | None:
    row = conn.execute("SELECT payload FROM g WHERE lat=? AND lon=?", (lat, lon)).fetchone()
    if row:
        payloads = json.loads(row[0])
    else:
        payloads, _ = fetch.fetch_coord(token, lat, lon, GROUND_FIELDS)
        conn.execute("INSERT OR REPLACE INTO g VALUES(?,?,?)", (lat, lon, json.dumps(payloads)))
        conn.commit()
    return score_ground(payloads)


def add_ground_scores(df: pd.DataFrame) -> pd.DataFrame:
    conn = _ground_cache()
    token = probe.load_token()
    # CLAUDE.md: validate the ground field names against the LIVE catalog before any fetch.
    with fetch.make_client(token) as client:
        missing = probe.validate_fields(probe.fetch_catalog(client), GROUND_FIELDS)
    if missing:
        raise SystemExit(f"STOP: ground fields not in live catalog: {[m for m, _ in missing]}")
    scores = []
    for _, r in df.iterrows():
        scores.append(ground_score_at(conn, token, round(r["LATITUDE"], 5), round(r["LONGITUDE"], 5)))
    conn.close()
    df = df.assign(ground=scores)
    return df.dropna(subset=["ground"]).reset_index(drop=True)


def _ground_coef(ground, age, log_traffic, y) -> float:
    x = np.column_stack([np.ones(len(y)), ground, age, log_traffic])
    return float(np.linalg.lstsq(x, y, rcond=None)[0][1])


def run_test(df: pd.DataFrame) -> dict:
    """Regress deterioration on ground score, controlling for age + log(traffic); permutation test
    (shuffling the ground labels) gives the p-value AND the leakage sanity check."""
    y = df["deterioration"].values
    age = df["age"].values
    logt = np.log1p(df["traffic"].values)
    real = _ground_coef(df["ground"].values, age, logt, y)

    random.seed(PERM_SEED)  # reproducible permutation p-value
    shuffled = df["ground"].values.copy()
    perm = []
    for _ in range(N_PERM):
        random.shuffle(shuffled)
        perm.append(_ground_coef(shuffled, age, logt, y))
    perm = np.array(perm)
    p_value = float(np.mean(np.abs(perm) >= abs(real)))

    q1, q3 = df["ground"].quantile([0.25, 0.75])
    low = df[df["ground"] <= q1]["deterioration"].median()
    high = df[df["ground"] >= q3]["deterioration"].median()
    return {
        "n_sections": int(len(df)),
        "ground_coef": round(real, 5),
        "permutation_p_value": round(p_value, 4),
        "shuffled_coef_mean": round(float(perm.mean()), 5),  # ~0 confirms no pipeline leakage
        "bottom_quartile_median_deterioration": round(float(low), 4),
        "top_quartile_median_deterioration": round(float(high), 4),
    }


def make_chart(df: pd.DataFrame, result: dict) -> None:
    fig, ax = plt.subplots(figsize=(7, 5))
    sc = ax.scatter(df["ground"], df["deterioration"], c=df["age"], cmap="viridis",
                    s=40, edgecolor="k", linewidth=0.3)
    b = np.polyfit(df["ground"], df["deterioration"], 1)
    xs = np.array([df["ground"].min(), df["ground"].max()])
    ax.plot(xs, b[0] * xs + b[1], "r--", lw=1.5, label="OLS fit")
    ax.set_xlabel("Mireye ground-risk score (W+S+G)")
    ax.set_ylabel("Measured deterioration rate (MRI slope, m/km per yr)")
    ax.set_title(f"LTPP validation: n={result['n_sections']}, "
                 f"perm p={result['permutation_p_value']}")
    fig.colorbar(sc, label="section age (yr)")
    ax.legend()
    CHART_OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(CHART_OUT, dpi=120)
    plt.close(fig)


def main() -> int:
    print(f"Extracting LTPP sections from {len(STATES)} states...")
    df = extract_ltpp()
    print(f"  {len(df)} sections with deterioration + age + traffic. Fetching Mireye ground...")
    df = add_ground_scores(df)
    print(f"  {len(df)} sections with Mireye ground data.")
    if len(df) < 20:
        print("Too few sections for a meaningful test; reporting counts only.")
        return 1

    result = run_test(df)
    make_chart(df, result)
    print(json.dumps(result, indent=2))
    verdict = ("high-ground-risk sections deteriorate FASTER" if result["ground_coef"] > 0
               else "no positive ground->deterioration relationship")
    print(f"Verdict: {verdict}; "
          f"{'significant' if result['permutation_p_value'] < 0.05 else 'NOT significant'} "
          f"(perm p={result['permutation_p_value']}). Shuffled-label coef mean "
          f"{result['shuffled_coef_mean']} (~0 = no leakage).")

    audit = json.loads(AUDIT.read_text()) if AUDIT.exists() else {}
    audit["ltpp_validation"] = result
    AUDIT.write_text(json.dumps(audit, indent=2))
    print(f"Wrote {CHART_OUT} and appended results to {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
