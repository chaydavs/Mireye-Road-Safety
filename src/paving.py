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
import pandas as pd
from arcgis.features import FeatureLayer
from arcgis.gis import GIS

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))

REPO = SRC.parent
DATA = REPO / "data"
SEGMENTS = DATA / "segments.parquet"
SCORES = DATA / "scores.parquet"
TREATMENT_OUT = DATA / "segment_treatment.parquet"
PLAN_OUT = DATA / "plan_comparison.parquet"

ARCGIS = "https://services.arcgis.com/p5v98VHDX9Atv3l7/arcgis/rest/services"
COMPLETED_URL = f"{ARCGIS}/Statewide_Paving_Status_Map_(Public_View)/FeatureServer/0"
PLANNED_URL = f"{ARCGIS}/2025_Asphalt_Locations_and_Prospective_Paving_Locations/FeatureServer/1"
# County selector differs per service (logged to ERRORS.md): the completed layer uses an uppercase
# COUNTY_NAME with a bare value; the planned PMSS layer uses County_Name with a " (CO)" suffix.
# The status map mixes PROJECT_STATUS (Completed / Scheduled / In Progress / Rescheduled) — only
# 'Completed' is a real past treatment, so we filter to it (never fabricate a treatment year from a
# scheduled/in-progress row).
COMPLETED_WHERE = "COUNTY_NAME='LOUDOUN' AND PROJECT_STATUS='Completed'"
PLANNED_WHERE = "County_Name='Loudoun (CO)'"
# Only these are ever requested; contact fields are never fetched (asserted below).
COMPLETED_FIELDS = ["SCHEDULE", "SYSTEM", "SCHEDULE_START_YEAR", "DASHBOARD_YEAR", "COUNTY_NAME",
                    "ROUTE_COMMON_NAME", "STREETNAMES", "LANE_DIR", "FROM_DESCRIPTION",
                    "TO_DESCRIPTION", "LANE_MILES", "TREATMENT_TYPE", "EditDate"]
PLANNED_FIELDS = ["Schedule", "System", "Schedule_Start_Year", "Dashboard_Year", "County_Name",
                  "Route_Common_Name", "streetNames", "PMS_Treatment_Type", "Project_Status_Desc"]
# VDOT publishes a paving-status map PER YEAR; we combine the base 2016-17 layer with the annual
# layers that carry Loudoun data (2023/24/26) and take each road's MOST RECENT completed paving year
# (a road repaved in 2024 is fresher than one from 2017). Annual layers use a different schema:
# DASHBOARD_YEAR (not SCHEDULE_START_YEAR), COUNTY_NAME with a " (CO)" suffix, PMS_TREATMENT_TYPE.
ANNUAL_FIELDS = ["SCHEDULE", "SYSTEM", "DASHBOARD_YEAR", "COUNTY_NAME", "ROUTE_COMMON_NAME",
                 "STREETNAMES", "LANE_DIR", "FROM_DESCRIPTION", "TO_DESCRIPTION", "LANE_MILES",
                 "PMS_TREATMENT_TYPE"]
_ANNUAL_WHERE = "COUNTY_NAME='Loudoun (CO)' AND PROJECT_STATUS='Completed'"
_ANNUAL_URL = ARCGIS + "/Statewide_Paving_Status_Map_(Public_View)_{yr}/FeatureServer/0"
# (label, layer_url, where, out_fields, year_field, treatment_field)
COMPLETED_SOURCES = [
    ("2016-17", COMPLETED_URL, COMPLETED_WHERE, COMPLETED_FIELDS, "SCHEDULE_START_YEAR", "TREATMENT_TYPE"),
    ("2023", _ANNUAL_URL.format(yr=2023), _ANNUAL_WHERE, ANNUAL_FIELDS, "DASHBOARD_YEAR", "PMS_TREATMENT_TYPE"),
    ("2024", _ANNUAL_URL.format(yr=2024), _ANNUAL_WHERE, ANNUAL_FIELDS, "DASHBOARD_YEAR", "PMS_TREATMENT_TYPE"),
    ("2026", _ANNUAL_URL.format(yr=2026), _ANNUAL_WHERE, ANNUAL_FIELDS, "DASHBOARD_YEAR", "PMS_TREATMENT_TYPE"),
]
CONTACT_FIELDS = {"PROJECT_MANAGER", "TELEPHONE", "EMAIL", "Creator", "Editor", "NTLOGIN",
                  "project_manager", "telephone", "email", "ntlogin"}
WORK_CRS = "EPSG:32618"
BUFFER_M = 25.0
OVERLAP_MIN_M = 100.0     # a same-road match must overlap this far; a cross-street only touches ~50 m
COUNTY_STOP = {"LOUDOUN", "COUNTY"}
NAME_STOP = {"RD", "ROAD", "ST", "DR", "AVE", "LN", "CT", "HWY", "BLVD", "PKWY", "WAY", "N", "S",
             "E", "W", "NE", "NW", "SE", "SW", "ROUTE", "VA", "US", "STATE", "RTE", "SC", "PM"} | COUNTY_STOP


def assert_no_contact(df: pd.DataFrame, where: str) -> None:
    leaked = {c for c in df.columns if c in CONTACT_FIELDS}
    if leaked:
        raise AssertionError(f"CONTACT FIELDS present in {where}: {leaked} — must never be stored")


_GIS = None


def _anon_gis() -> GIS:
    """Anonymous connection — these are public FeatureServers (no token). We never touch Esri
    geocoding, routing, or enrichment; our provenance thesis is federal, cited, open-source."""
    global _GIS
    if _GIS is None:
        _GIS = GIS()
    return _GIS


def _fetch(layer_url: str, fields: list[str], where: str) -> gpd.GeoDataFrame:
    """Query a public VDOT FeatureLayer anonymously via the ArcGIS Python API (documented ops only).
    Whitelisted out_fields (never contact fields); the library handles paging; the returned row
    count is asserted against return_count_only. SEDF geometry -> shapely via arcgis .as_shapely."""
    fl = FeatureLayer(layer_url, gis=_anon_gis())
    p = fl.properties
    print(f"  layer '{p.name}': geometry={p.geometryType} maxRecordCount={p.maxRecordCount} "
          f"Query={'Query' in p.capabilities} fields={len(p.fields)}")
    expected = fl.query(where=where, return_count_only=True)
    sdf = fl.query(where=where, out_fields=",".join(fields), out_sr=4326,
                   return_geometry=True, as_df=True)
    assert len(sdf) == expected, f"{layer_url}: fetched {len(sdf)} rows != count {expected}"
    if sdf.empty:
        return gpd.GeoDataFrame()
    shape_col = sdf.spatial.name
    sdf = sdf.assign(geometry=sdf[shape_col].apply(lambda g: g.as_shapely if g is not None else None))
    gdf = gpd.GeoDataFrame(sdf.drop(columns=[shape_col]), geometry="geometry", crs="EPSG:4326")
    assert_no_contact(gdf, layer_url)
    return gdf


def _fetch_completed(url: str, where: str, fields: list[str], year_field: str,
                     treat_field: str) -> gpd.GeoDataFrame:
    """Fetch one completed-paving layer and normalize its schema to unified columns (pav_year,
    treat_type, ROUTE_COMMON_NAME, STREETNAMES, SCHEDULE, geometry) so all years concat cleanly."""
    g = _fetch(url, fields, where)
    if g.empty:
        return g
    g["pav_year"] = g["SCHEDULE_START_YEAR"].fillna(g.get("DASHBOARD_YEAR")) \
        if year_field == "SCHEDULE_START_YEAR" else g[year_field]
    g["treat_type"] = g[treat_field]
    keep = ["pav_year", "treat_type", "ROUTE_COMMON_NAME", "STREETNAMES", "SCHEDULE", "geometry"]
    return gpd.GeoDataFrame(g[[c for c in keep if c in g.columns]], geometry="geometry", crs=g.crs)


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
    # Keep the MOST RECENT paving year per segment (a road matched across several annual layers takes
    # its latest year), tie-broken by name-agreement confidence then overlap length.
    df["_yr"] = pd.to_numeric(df["year"], errors="coerce").fillna(0)
    df = df.sort_values(["_yr", "join_confidence", "overlap_m"], ascending=[False, True, False])
    return df.drop_duplicates("segment_id", keep="first").drop(columns="_yr")


def load_treatment(segs: gpd.GeoDataFrame) -> tuple[pd.DataFrame, dict]:
    # Combine the base 2016-17 layer with VDOT's annual paving maps -> the richest last-paved history.
    parts, by_year = [], {}
    for label, url, where, fields, yfield, tfield in COMPLETED_SOURCES:
        g = _fetch_completed(url, where, fields, yfield, tfield)
        by_year[label] = int(len(g))
        if not g.empty:
            parts.append(g)
    done = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry",
                            crs="EPSG:4326") if parts else gpd.GeoDataFrame()
    plan = _fetch(PLANNED_URL, PLANNED_FIELDS, PLANNED_WHERE)
    if not plan.empty:
        plan = plan.assign(pav_year=plan["Schedule_Start_Year"].fillna(plan.get("Dashboard_Year")))

    m_done = join_to_segments(done, segs, {
        "common": "ROUTE_COMMON_NAME", "streets": "STREETNAMES", "year": "pav_year",
        "treatment": "treat_type", "schedule": "SCHEDULE", "completed": True})
    m_plan = join_to_segments(plan, segs, {
        "common": "Route_Common_Name", "streets": "streetNames", "year": "pav_year",
        "treatment": "PMS_Treatment_Type", "schedule": "Schedule", "completed": False})
    meta = {"completed_projects": int(len(done)), "planned_projects": int(len(plan)),
            "completed_by_year": by_year,
            "segments_completed_matched": int(m_done["segment_id"].nunique()) if not m_done.empty else 0,
            "segments_planned_matched": int(m_plan["segment_id"].nunique()) if not m_plan.empty else 0}

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
        if d is None and p is None:
            continue  # a matched row with no usable year (neither completed-with-year nor scheduled)
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
    scores = pd.read_parquet(SCORES)
    # Join paving only to SCORED segments: RSL and the plan comparison apply to the scored corridor,
    # not the full 15k-segment county network (matching the whole county inflates the headline and
    # wastes work — 90% of county matches are segments we never scored).
    segs = gpd.read_parquet(SEGMENTS)
    segs = segs[segs["segment_id"].isin(scores["segment_id"])].reset_index(drop=True)
    treat, meta = load_treatment(segs)
    treat.to_parquet(TREATMENT_OUT)
    print(f"VDOT paving: {meta['completed_projects']} completed (by layer year: {meta['completed_by_year']}), "
          f"{meta['planned_projects']} planned in Loudoun; matched {meta['segments_completed_matched']} "
          f"completed / {meta['segments_planned_matched']} planned to segments (most-recent year per segment).")

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
    real = int(treat["last_treated_year"].notna().sum()) if not treat.empty else 0
    print(f"\nHEADLINE: {real}/{len(scores)} county segments now carry a real VDOT treatment year "
          f"(basis vdot_paving); the remaining {len(scores) - real} stay on the functional-class prior.")
    print(f"Wrote {TREATMENT_OUT} and {PLAN_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
