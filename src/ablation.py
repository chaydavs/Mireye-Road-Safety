"""Ablation: is Mireye's ground data LOAD-BEARING, or is its high "share" just an artifact of building
the score around Mireye's fields?

We answer by DECISION DIVERGENCE — how much the county's repaving PRIORITY LIST changes when every
Mireye-served field is stripped and roads are scored on the county's own VDOT data alone (traffic).

  FULL      = the live score (Mireye-served W/S/C/G  +  VDOT traffic T).
  NO_MIREYE = traffic only, using ONLY real VDOT AADT — renormalized to that single non-Mireye factor.
              (The housing-density proxy is served THROUGH Mireye, so it is excluded; we pass housing=None
               so no Mireye value is ever read.)

This is NOT an accuracy test — there is no ground truth here. It measures "how much would the county's
priority list change without Mireye," never "how much more accurate." We do not tune anything to
maximize divergence; the printed numbers are the real numbers, and if Mireye barely moved the ranking
that would be the finding.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))
import score  # noqa: E402

REPO = SRC.parent
DATA = REPO / "data"
SCORES = DATA / "scores.parquet"
SEGMENTS = DATA / "segments.parquet"
OUT = DATA / "ablation.parquet"
META_OUT = DATA / "ablation_meta.json"

# Every field served THROUGH Mireye /v1/fetch: all W/S/C/G components + the census housing proxy.
MIREYE_FIELDS = {f for _, fields in score.FACTORS.values() for f in fields} | {
    "housing_units_density_per_km2"}
# The ONLY non-Mireye score input a county has on its own: VDOT AADT.
NO_MIREYE_FIELDS = {"traffic_aadt"}
# Mireye's GROUND factors (W/S/C/G) — the soil/water/climate/terrain data, excluding the census
# housing proxy (which sits in the traffic factor). These are what the "roads Mireye reveals" panel shows.
GROUND_FIELDS = {f for name in ("W", "S", "C", "G") for f in score.FACTORS[name][1]}

# Plain-language phrase for the Mireye field that reveals a road (for the "roads Mireye reveals" list).
FIELD_PHRASE = {
    "soil_drainage_class": "poorly drained soil",
    "soil_ponding_frequency_class": "ponding-prone soil",
    "within_floodplain_polygon": "floodplain",
    "fema_flood_zone": "FEMA flood zone",
    "surface_water_permanence_pct": "standing surface water",
    "nearest_wetland_distance_m": "wetland-adjacent",
    "soil_available_water_capacity": "water-retentive soil",
    "soil_hydrologic_group": "poorly-infiltrating soil",
    "soil_shrink_swell_class": "shrink-swell soil",
    "soil_erodibility_k_factor": "erodible soil",
    "bedrock_depth_cm": "shallow bedrock",
    "slope_degrees": "steep slope",
    "landslide_susceptibility_index": "landslide-prone terrain",
    "mean_annual_snow_cover_days": "heavy snow cover",
    "days_above_32c_annual_count": "extreme heat days",
    "housing_units_density_per_km2": "dense development",
}
QUINTILE_CUTS = [0.8, 0.6, 0.4, 0.2]  # matches the app's relative-risk ramp; quintile 5 = worst 20%


def _quintile(pct: float) -> int:
    for i, cut in enumerate(QUINTILE_CUTS):
        if pct >= cut:
            return 5 - i
    return 1


def no_mireye_score(aadt, traffic_source) -> float:
    """Traffic-only score using ONLY real VDOT AADT. housing=None -> the Mireye proxy path is never
    taken, so a county with no AADT for a road has NO signal for it (score 0, bottom priority)."""
    t_score, t_tag = score.traffic_component(aadt, traffic_source, None)
    return round(100.0 * t_score, 1) if t_tag == "aadt" else 0.0


def build() -> tuple[pd.DataFrame, dict]:
    leaked = NO_MIREYE_FIELDS & MIREYE_FIELDS
    assert not leaked, f"Mireye field leaked into NO_MIREYE: {leaked}"

    scores = pd.read_parquet(SCORES)[["segment_id", "route_name", "score", "drivers"]]
    segs = gpd.read_parquet(SEGMENTS).reset_index()[["segment_id", "aadt", "traffic_source"]]
    df = scores.rename(columns={"score": "full_score"}).merge(segs, on="segment_id", how="left")

    df["no_mireye_score"] = [no_mireye_score(a, t) for a, t in zip(df["aadt"], df["traffic_source"])]
    # ranks: 1 = worst / highest priority
    df["full_rank"] = df["full_score"].rank(ascending=False, method="min").astype(int)
    df["no_mireye_rank"] = df["no_mireye_score"].rank(ascending=False, method="min").astype(int)
    df["rank_delta"] = df["no_mireye_rank"] - df["full_rank"]  # + = Mireye moves it up the priority list
    df["full_pct"] = df["full_score"].rank(pct=True)
    df["no_mireye_pct"] = df["no_mireye_score"].rank(pct=True)
    df["quintile_full"] = df["full_pct"].map(_quintile)
    df["quintile_no_mireye"] = df["no_mireye_pct"].map(_quintile)

    n = len(df)
    n_top = round(0.1 * n)
    full_top = set(df.nlargest(n_top, "full_score")["segment_id"])
    nm_top = set(df.nlargest(n_top, "no_mireye_score")["segment_id"])
    churn = full_top - nm_top  # in FULL's worst decile, NOT in a traffic-only worst decile
    churn_pct = round(100.0 * len(churn) / n_top, 1)
    # Spearman rho = Pearson correlation of the average ranks (avoids a scipy dependency).
    spearman = float(df["full_score"].rank(method="average").corr(
        df["no_mireye_score"].rank(method="average")))

    df["flip"] = df["segment_id"].isin(churn)
    df["flip_reason"] = ""
    df["flip_field"] = ""
    # The 5 flips showcase Mireye's GROUND data (W/S/C/G — soil/water/climate/terrain), not the
    # census housing proxy: housing lives in the traffic factor, so a housing-driven flip is
    # near-circular ("a traffic-only model misses a traffic proxy"). The GROUND-driven flips are the
    # actual "Mireye's ground data is load-bearing" story. Headline churn/Spearman above are unchanged.
    # Cleanest, confound-free flips: roads that HAVE real VDOT traffic (no_mireye_score > 0) yet still
    # drop out of a traffic-only top decile — so ground data reordered them EVEN controlling for traffic.
    # (For AADT segments the traffic factor uses AADT, not the housing proxy, so there is no
    # census-proxy confound; the remaining top drivers are pure ground fields.)
    cand = df[df["flip"] & (df["no_mireye_score"] > 0)].copy()
    cand["ground"] = cand["drivers"].map(
        lambda dj: [d["component"] for d in json.loads(dj) if d["component"] in GROUND_FIELDS][:2])
    cand = cand[cand["ground"].map(len) > 0].nlargest(5, "rank_delta")
    flip_list = []
    for r in cand.itertuples():
        phrases = [FIELD_PHRASE.get(f, f.replace("_", " ")) for f in r.ground]
        reason = (f"{' + '.join(phrases)}: a traffic-only model ranks it #{int(r.no_mireye_rank):,}, "
                  f"Mireye's ground data moves it to #{int(r.full_rank):,} (top-priority)")
        df.loc[df["segment_id"] == r.segment_id, ["flip_reason", "flip_field"]] = [reason, ", ".join(r.ground)]
        flip_list.append({"segment_id": int(r.segment_id), "route_name": r.route_name or "unnamed road",
                          "rank_delta": int(r.rank_delta), "reason": reason, "fields": r.ground,
                          "no_mireye_rank": int(r.no_mireye_rank), "full_rank": int(r.full_rank)})

    meta = {"segments": n, "spearman": round(spearman, 3), "top_decile_n": n_top,
            "churn_count": len(churn), "churn_pct": churn_pct, "flips": flip_list,
            "no_mireye_inputs": sorted(NO_MIREYE_FIELDS)}
    out = df.drop(columns=["drivers", "aadt", "traffic_source"])
    return out, meta


def main() -> int:
    out, meta = build()
    # Verify: rankings cover the identical segment set as the live score.
    scores = pd.read_parquet(SCORES)
    assert set(out["segment_id"]) == set(scores["segment_id"]), "ablation segment set != scores set"
    out.to_parquet(OUT)
    META_OUT.write_text(json.dumps(meta, indent=2))

    print(f"Ablation over {meta['segments']} segments. NO_MIREYE inputs = {meta['no_mireye_inputs']} "
          "(zero Mireye-served fields — asserted).")
    print("\nHeadline — how much the county's PRIORITY LIST changes without Mireye (not accuracy):")
    print(f"  1. Spearman(full, traffic-only) = {meta['spearman']}   (lower = Mireye reorders more)")
    print(f"  2. Top-decile churn: {meta['churn_count']}/{meta['top_decile_n']} = {meta['churn_pct']}% "
          "of the worst-priority list changes when Mireye's ground data is added.")
    print("  3. Rank movement stored per segment (rank_delta).")
    print("\n5 roads Mireye reveals (real traffic data, but ground data reorders them into the top "
          "priority list — a traffic-only model under-ranks them):")
    for f in meta["flips"]:
        print(f"  seg {f['segment_id']} {f['route_name']}: {f['reason']}")
    print(f"\nWrote {OUT} and {META_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
