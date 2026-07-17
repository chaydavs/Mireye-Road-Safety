"""VDOT paving program -> segment treatment history + a plan-vs-risk comparison.

Ingests VDOT's paving feature services for Loudoun (completed history + planned/prospective), DROPS
all contact fields at ingestion (asserted — they must never enter our stores), spatial-joins to our
segments GEOMETRY-FIRST (buffer + same-road overlap length, so a crossing street is not a match),
with route/street name agreement as a confidence booster. Completed -> last_treated_year (basis
'vdot_paving', feeds service_life.py between HPMS and the prior); planned -> scheduled flag.

Schema note (logged to ERRORS.md): VDOT ROUTE_COMMON_NAME is a route NUMBER with the county in
parentheses ("SC-719N (Loudoun County)"), not a street name, so name agreement leans on STREETNAMES
and the join is geometry-primary. The prompt assumed route-name agreement would carry the join.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))

REPO = SRC.parent
DATA = REPO / "data"
SEGMENTS = DATA / "segments.parquet"
SCORES = DATA / "scores.parquet"
TREATMENT_OUT = DATA / "segment_treatment.parquet"
PLAN_OUT = DATA / "plan_comparison.parquet"

ARCGIS = "https://services.arcgis.com/p5v98VHDX9Atv3l7/arcgis/rest/services"
COMPLETED_URL = f"{ARCGIS}/Statewide_Paving_Status_Map_(Public_View)/FeatureServer/0/query"
PLANNED_URL = f"{ARCGIS}/2025_Asphalt_Locations_and_Prospective_Paving_Locations/FeatureServer/1/query"
# Only these are ever requested; contact fields are never fetched (asserted below).
COMPLETED_FIELDS = ["SCHEDULE", "SYSTEM", "SCHEDULE_START_YEAR", "DASHBOARD_YEAR", "COUNTY_NAME",
                    "ROUTE_COMMON_NAME", "STREETNAMES", "LANE_DIR", "FROM_DESCRIPTION",
                    "TO_DESCRIPTION", "LANE_MILES", "PROJECT_STATUS", "TREATMENT_TYPE", "EditDate"]
PLANNED_FIELDS = ["Schedule", "System", "Schedule_Start_Year", "Dashboard_Year", "County_Name",
                  "Route_Common_Name", "streetNames", "PMS_Treatment_Type", "Project_Status_Desc"]
CONTACT_FIELDS = {"PROJECT_MANAGER", "TELEPHONE", "EMAIL", "Creator", "Editor",
                  "project_manager", "telephone", "email"}
WORK_CRS = "EPSG:32618"
BUFFER_M = 25.0
OVERLAP_MIN_M = 100.0     # a same-road match must overlap this far; a cross-street only touches ~50 m
COMPLETED_STATUSES = {"completed", "complete", "rescheduled"}
COUNTY_STOP = {"LOUDOUN", "COUNTY"}
NAME_STOP = {"RD", "ROAD", "ST", "DR", "AVE", "LN", "CT", "HWY", "BLVD", "PKWY", "WAY", "N", "S",
             "E", "W", "NE", "NW", "SE", "SW", "ROUTE", "VA", "US", "STATE", "RTE", "SC", "PM"} | COUNTY_STOP


def assert_no_contact(df: pd.DataFrame, where: str) -> None:
    leaked = {c for c in df.columns if c in CONTACT_FIELDS}
    if leaked:
        raise AssertionError(f"CONTACT FIELDS present in {where}: {leaked} — must never be stored")


def _fetch(url: str, fields: list[str], county: str) -> gpd.GeoDataFrame:
    field = "COUNTY_NAME" if "COUNTY_NAME" in fields else "County_Name"
    params = {"where": f"{field} LIKE '%{county}%'", "outFields": ",".join(fields),
              "returnGeometry": "true", "outSR": "4326", "f": "geojson", "resultRecordCount": 2000}
    with httpx.Client(timeout=60.0) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        gdf = gpd.GeoDataFrame.from_features(resp.json().get("features", []), crs="EPSG:4326")
    assert_no_contact(gdf, url)
    return gdf


def _tokens(name, stop=NAME_STOP) -> set[str]:
    if not isinstance(name, str) or not name:
        return set()
    name = re.sub(r"\([^)]*\)", " ", name)  # drop "(Loudoun County)" parentheticals
    return set(re.findall(r"[A-Z0-9]+", name.upper())) - stop


def _names_agree(seg_name, vdot_common, vdot_streets) -> bool:
    seg = _tokens(seg_name)
    return bool(seg and (seg & _tokens(vdot_common)) or (seg & _tokens(vdot_streets)))


def join_to_segments(paving: gpd.GeoDataFrame, segs: gpd.GeoDataFrame, cols: dict) -> pd.DataFrame:
    """Geometry-first: keep a match only if the VDOT line overlaps the segment for >= OVERLAP_MIN_M
    (a crossing street won't), then record name agreement + overlap length as join confidence."""
    empty = pd.DataFrame(columns=["segment_id", "route_name", "vdot_route", "year", "completed",
                                  "treatment_type", "schedule_id", "overlap_m", "join_confidence"])
    if paving.empty:
        return empty
    pav_m = paving.to_crs(WORK_CRS)
    seg_m = segs.to_crs(WORK_CRS)
    seg_len = seg_m.geometry.length
    seg_buf = seg_m.assign(seg_len=seg_len.values, geometry=seg_m.geometry.buffer(BUFFER_M))
    cand = gpd.sjoin(seg_buf[["segment_id", "route_name", "seg_len", "geometry"]],
                     pav_m.assign(pav_idx=range(len(pav_m))), how="inner", predicate="intersects")
    rows = []
    for r in cand.itertuples(index=False):
        seg_geom = seg_buf.loc[seg_buf["segment_id"] == r.segment_id, "geometry"].iloc[0]
        vdot_geom = pav_m.geometry.iloc[int(r.pav_idx)]
        overlap = vdot_geom.intersection(seg_geom).length  # length of VDOT line inside the buffer
        if overlap < min(OVERLAP_MIN_M, 0.6 * r.seg_len):
            continue  # cross-street / incidental touch, not the same road
        common = getattr(r, cols["common"])
        streets = getattr(r, cols["streets"], None)
        agree = _names_agree(r.route_name, common, streets)
        rows.append({
            "segment_id": int(r.segment_id), "route_name": r.route_name, "vdot_route": common,
            "year": getattr(r, cols["year"]), "completed": cols["completed"],
            "treatment_type": getattr(r, cols["treatment"]), "schedule_id": getattr(r, cols["schedule"]),
            "overlap_m": round(overlap, 0),
            "join_confidence": "high" if agree else "medium",
        })
    df = pd.DataFrame(rows, columns=empty.columns)
    df = df.sort_values(["join_confidence", "overlap_m"], ascending=[True, False])
    return df.drop_duplicates("segment_id", keep="first")


def load_treatment(segs: gpd.GeoDataFrame) -> tuple[pd.DataFrame, dict]:
    done = _fetch(COMPLETED_URL, COMPLETED_FIELDS, "Loudoun")
    plan = _fetch(PLANNED_URL, PLANNED_FIELDS, "Loudoun")
    if not done.empty:
        done = done.assign(pav_year=done["SCHEDULE_START_YEAR"].fillna(done.get("DASHBOARD_YEAR")))
    if not plan.empty:
        plan = plan.assign(pav_year=plan["Schedule_Start_Year"].fillna(plan.get("Dashboard_Year")))

    m_done = join_to_segments(done, segs, {
        "common": "ROUTE_COMMON_NAME", "streets": "STREETNAMES", "year": "pav_year",
        "treatment": "TREATMENT_TYPE", "schedule": "SCHEDULE", "completed": True})
    m_plan = join_to_segments(plan, segs, {
        "common": "Route_Common_Name", "streets": "streetNames", "year": "pav_year",
        "treatment": "PMS_Treatment_Type", "schedule": "Schedule", "completed": False})
    meta = {"completed_projects": int(len(done)), "planned_projects": int(len(plan)),
            "segments_completed_matched": int(len(m_done)), "segments_planned_matched": int(len(m_plan))}

    rows = []
    for m in (m_done, m_plan):
        for r in m.itertuples(index=False):
            rows.append({
                "segment_id": r.segment_id,
                "last_treated_year": int(r.year) if r.completed and pd.notna(r.year) else None,
                "treatment_type": r.treatment_type if r.completed else None,
                "basis": "vdot_paving" if r.completed and pd.notna(r.year) else None,
                "scheduled": (not r.completed),
                "schedule_id": r.schedule_id if not r.completed else None,
                "scheduled_year": int(r.year) if (not r.completed) and pd.notna(r.year) else None,
                "join_confidence": r.join_confidence, "overlap_m": r.overlap_m,
            })
    treat = pd.DataFrame(rows, columns=["segment_id", "last_treated_year", "treatment_type", "basis",
                                        "scheduled", "schedule_id", "scheduled_year",
                                        "join_confidence", "overlap_m"])
    # one row per segment: a scheduled row and a completed row can both exist; keep both signals by
    # collapsing to: last_treated_year (from completed) + scheduled (from planned).
    treat = _collapse(treat)
    assert_no_contact(treat, "segment_treatment")
    return treat, meta


def _collapse(treat: pd.DataFrame) -> pd.DataFrame:
    if treat.empty:
        return treat
    out = []
    for sid, g in treat.groupby("segment_id"):
        done = g[g["basis"] == "vdot_paving"].sort_values("last_treated_year", ascending=False)
        plan = g[g["scheduled"]].sort_values("scheduled_year", ascending=False)
        d = done.iloc[0] if not done.empty else None
        p = plan.iloc[0] if not plan.empty else None
        out.append({
            "segment_id": int(sid),
            "last_treated_year": int(d["last_treated_year"]) if d is not None else None,
            "treatment_type": d["treatment_type"] if d is not None else None,
            "basis": "vdot_paving" if d is not None else None,
            "scheduled": p is not None,
            "schedule_id": p["schedule_id"] if p is not None else None,
            "scheduled_year": int(p["scheduled_year"]) if p is not None and pd.notna(p["scheduled_year"]) else None,
            "join_confidence": (d if d is not None else p)["join_confidence"],
        })
    return pd.DataFrame(out)


def plan_comparison(scores: pd.DataFrame, treat: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    top_cut, med = scores["score"].quantile(0.90), scores["score"].median()
    sched = set(treat.loc[treat["scheduled"], "segment_id"]) if not treat.empty else set()
    s = scores.assign(scheduled=scores["segment_id"].isin(sched))
    s["bucket"] = "other"
    s.loc[(s["score"] >= top_cut) & (~s["scheduled"]), "bucket"] = "a_high_risk_unscheduled"
    s.loc[(s["score"] >= top_cut) & (s["scheduled"]), "bucket"] = "b_high_risk_scheduled"
    s.loc[(s["score"] < med) & (s["scheduled"]), "bucket"] = "c_scheduled_lower_risk"
    counts = {b: int((s["bucket"] == b).sum()) for b in
              ("a_high_risk_unscheduled", "b_high_risk_scheduled", "c_scheduled_lower_risk")}
    return s[["segment_id", "route_name", "score", "grade", "scheduled", "bucket"]], counts


def main() -> int:
    segs = gpd.read_parquet(SEGMENTS)
    scores = pd.read_parquet(SCORES)
    treat, meta = load_treatment(segs)
    treat.to_parquet(TREATMENT_OUT)
    print(f"VDOT paving: {meta['completed_projects']} completed, {meta['planned_projects']} planned "
          f"in Loudoun; matched {meta['segments_completed_matched']} completed / "
          f"{meta['segments_planned_matched']} planned to segments (geometry-first).")

    print("\n5 matched pairs (our route_name vs VDOT route) — same road, not a cross-street:")
    show = treat[treat["last_treated_year"].notna() | treat["scheduled"]].head(5)
    seg_names = dict(zip(segs["segment_id"], segs["route_name"]))
    for r in show.itertuples(index=False):
        if pd.notna(r.basis):
            label = f"treated {int(r.last_treated_year)}"
        else:
            yr = int(r.scheduled_year) if pd.notna(r.scheduled_year) else "?"
            label = f"scheduled {r.schedule_id} ({yr})"
        print(f"  seg {r.segment_id}: '{seg_names.get(r.segment_id)}'  "
              f"[{r.join_confidence} confidence]  ({label})")

    plan, counts = plan_comparison(scores, treat)
    plan.to_parquet(PLAN_OUT)
    print("\nPlan vs risk (a LENS, not an error claim — VDOT has condition & funding context we do "
          "not model; disagreement is a prompt for a conversation, not a mistake):")
    print(f"  (a) top-decile risk, NOT on the paving plan : {counts['a_high_risk_unscheduled']}")
    print(f"  (b) top-decile risk, scheduled (agreement)  : {counts['b_high_risk_scheduled']}")
    print(f"  (c) scheduled but bottom-half risk          : {counts['c_scheduled_lower_risk']}")
    print("\n  Top 5 of bucket (a) — high risk, not on the plan (with why-drivers):")
    a = plan[plan["bucket"] == "a_high_risk_unscheduled"].sort_values("score", ascending=False)
    seg_drivers = dict(zip(scores["segment_id"], scores["drivers"]))
    import json
    for r in a.head(5).itertuples(index=False):
        drivers = ", ".join(d["component"] for d in json.loads(seg_drivers.get(r.segment_id, "[]"))[:3])
        print(f"    seg {r.segment_id} {r.route_name or 'unnamed'}: score {r.score} ({r.grade}) — {drivers}")
    print(f"\nWrote {TREATMENT_OUT} and {PLAN_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
