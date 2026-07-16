"""Session 3 fetch stage (PRD section 6 stage 2): cache-backed, resumable Mireye fetch with a
provenance store, an audit log, and deterministic snap QA.

Non-goals (PRD section 9 session 3): no parallelism beyond 4 concurrent, no ORM, no retry beyond
the single 429 backoff (reused from probe.fetch_point).
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd

import probe  # single source of truth for the field list, token loader, and fetch call

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"
POINTS = DATA / "points.parquet"
SEGMENTS = DATA / "segments.parquet"
CACHE_DB = DATA / "cache.sqlite"
AUDIT_OUT = DATA / "audit.json"

FIELDS = probe.ALL_PRD_FIELDS  # 43 PRD section 7 fields, fetched per point
# Town-scale scope: Leesburg + Ashburn core (user decision, Session 3) — ~7.9k points, ~1 hr.
# bbox = (lon_min, lat_min, lon_max, lat_max), WGS84.
SCOPE_BBOX = (-77.57, 39.01, -77.48, 39.12)
SCOPE_LABEL = "Leesburg+Ashburn core (bbox -77.57,39.01,-77.48,39.12)"
MAX_CONCURRENCY = 4
NULL_KILL_THRESHOLD = 0.40     # >40% null on a W/S scoring field -> stop
# Strict timeouts + short keepalive so a stale socket (server cycled) is never reused for hours.
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0, read=30.0)
HTTP_LIMITS = httpx.Limits(max_connections=2, max_keepalive_connections=1, keepalive_expiry=15.0)
SNAP_MAX_DIST_M = 50.0         # a point farther than this from any Mireye road is a real bad snap

# PRD section 6 scoring table, W (water) and S (soil movement) rows — the load-bearing scoring
# fields the kill criterion guards.
W_FIELDS = [
    "soil_drainage_class", "soil_ponding_frequency_class", "within_floodplain_polygon",
    "fema_flood_zone", "surface_water_permanence_pct", "nearest_wetland_distance_m",
    "soil_available_water_capacity", "soil_hydrologic_group",
]
S_FIELDS = ["soil_shrink_swell_class", "soil_erodibility_k_factor", "bedrock_depth_cm"]
SCORING_WS_FIELDS = W_FIELDS + S_FIELDS

ROAD_SUFFIXES = {
    "RD", "ROAD", "ST", "STREET", "DR", "DRIVE", "AVE", "AVENUE", "LN", "LANE", "CT", "COURT",
    "HWY", "HIGHWAY", "BLVD", "PL", "PLACE", "TER", "TERRACE", "CIR", "CIRCLE", "PKWY", "WAY",
    "N", "S", "E", "W", "NE", "NW", "SE", "SW", "ROUTE", "VA", "US",
}


# ---------- database ----------

def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(CACHE_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache("
        "lat5 REAL, lon5 REAL, field TEXT, payload TEXT, PRIMARY KEY(lat5, lon5, field))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS provenance("
        "point_id TEXT, field TEXT, value TEXT, source TEXT, source_url TEXT, "
        "fetched_at TEXT, confidence TEXT, status TEXT, PRIMARY KEY(point_id, field))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS qa_log("
        "point_id TEXT PRIMARY KEY, decision TEXT, reason TEXT, "
        "nearest_road_name TEXT, route_name TEXT)"
    )
    conn.commit()
    return conn


def key(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, 5), round(lon, 5)


def cached_payloads(conn: sqlite3.Connection, lat5: float, lon5: float) -> dict:
    rows = conn.execute(
        "SELECT field, payload FROM cache WHERE lat5=? AND lon5=?", (lat5, lon5)
    ).fetchall()
    return {f: json.loads(p) for f, p in rows}


def put_cache(conn: sqlite3.Connection, lat5: float, lon5: float, payloads: dict) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO cache VALUES(?,?,?,?)",
        [(lat5, lon5, f, json.dumps(p)) for f, p in payloads.items()],
    )


def _prov_value(conn: sqlite3.Connection, point_id: str, field: str):
    row = conn.execute(
        "SELECT value FROM provenance WHERE point_id=? AND field=?", (point_id, field)
    ).fetchone()
    return json.loads(row[0]) if row else None


# ---------- status + QA (the judgment surface) ----------

def derive_status(field: str, value, api_status, null_meaning_map: dict) -> str:
    """present / absent-semantic / failed.

    Mireye's own per-field status is the primary signal: 'absent' means a real semantic absence
    (e.g. no named waterbody within range), which is NOT a failure. For the ambiguous 'ok'+null
    case, the catalog's null_meaning drives interpretation (CLAUDE.md): a documented null_meaning
    means the null is semantic; otherwise it is an unexpected failure. A null is never a value."""
    if api_status == "absent":
        return "absent-semantic"
    if api_status in (None, "ok"):
        if value is not None:
            return "present"
        return "absent-semantic" if null_meaning_map.get(field) else "failed"
    return "failed"


def _road_tokens(name) -> set[str]:
    if not isinstance(name, str) or not name:  # None, or a float NaN (unnamed road)
        return set()
    toks = set(re.findall(r"[A-Z0-9]+", name.upper()))
    return toks - ROAD_SUFFIXES


def qa_triage_decision(
    nearest_road_name: str | None, route_name: str | None, nearest_road_distance_m: float | None
) -> tuple[str, str]:
    """The one judgment call, deterministic; a QA agent could replace THIS function.

    Snap quality is judged by DISTANCE: points are interpolated exactly onto TIGER centerlines,
    so a large nearest_road_distance_m is the real signal of a bad snap (point floating off any
    road). Name disagreement alone is NOT a discard — Mireye/Overture uses local street names
    while TIGER uses route designations (e.g. 'East Market Street' == 'State Rte 7 Bus'), so a
    name mismatch is logged as a low-confidence flag, not thrown away.

    Returns (decision, reason); decision in keep | keep_flag | resnap."""
    if nearest_road_distance_m is not None and nearest_road_distance_m > SNAP_MAX_DIST_M:
        return "resnap", "far_from_road"
    # Non-string names (None, or a float NaN for an unnamed road) count as missing, not a mismatch.
    route_name = route_name if isinstance(route_name, str) else None
    nearest_road_name = nearest_road_name if isinstance(nearest_road_name, str) else None
    if not route_name or not nearest_road_name:
        return "keep", "unnamed_or_missing"
    if _road_tokens(nearest_road_name) & _road_tokens(route_name):
        return "keep", "name_match"
    return "keep_flag", "name_source_mismatch"


# ---------- fetch ----------

_thread_local = threading.local()


def make_client(token: str) -> httpx.Client:
    return httpx.Client(
        headers={"Authorization": f"Bearer {token}"},
        timeout=HTTP_TIMEOUT, limits=HTTP_LIMITS,
    )


def _thread_client(token: str) -> httpx.Client:
    """One isolated client per worker thread — avoids the shared-pool stall that hung the run."""
    client = getattr(_thread_local, "client", None)
    if client is None:
        client = make_client(token)
        _thread_local.client = client
    return client


def payloads_from_response(data: dict | None, status: int, fields: list[str]) -> dict:
    """Normalize a /v1/fetch response into {field: payload}. A failed call -> all fields failed."""
    if status != 200 or not data:
        return {f: {"value": None, "status": "failed", "source": None, "source_url": None,
                    "confidence": None, "fetched_at": None} for f in fields}
    returned = data.get("fields", {})
    out = {}
    for f in fields:
        fo = returned.get(f)
        out[f] = fo if fo is not None else {
            "value": None, "status": "failed", "source": None, "source_url": None,
            "confidence": None, "fetched_at": None,
        }
    return out


def fetch_coord(token: str, lat: float, lon: float, fields: list[str]) -> tuple[dict, bool]:
    """Fetch all fields at one coordinate using this thread's own client. Returns (payloads, saw_429)."""
    data, _latency, status, saw_429 = probe.fetch_point(_thread_client(token), lat, lon, fields)
    return payloads_from_response(data, status, fields), saw_429


def write_provenance(conn: sqlite3.Connection, point_id: str, payloads: dict, nm_map: dict) -> None:
    rows = []
    for f, p in payloads.items():
        value = p.get("value")
        source, source_url = p.get("source"), p.get("source_url")
        fetched_at = p.get("fetched_at")
        st = derive_status(f, value, p.get("status"), nm_map)
        # CLAUDE.md "no provenance row, no value": a present value missing any provenance field
        # is not trustworthy — downgrade it to failed rather than store an unsourced value.
        if st == "present" and not (source and source_url and fetched_at):
            st = "failed"
        rows.append((point_id, f, json.dumps(value), source, source_url,
                     fetched_at, p.get("confidence"), st))
    conn.executemany("INSERT OR REPLACE INTO provenance VALUES(?,?,?,?,?,?,?,?)", rows)


# ---------- orchestration ----------

def load_scope(limit: int | None) -> tuple[pd.DataFrame, dict, dict]:
    """Return (scoped points df, segment_id->route_name, segment_id->(alt_lat,alt_lon) resnap)."""
    pts = pd.read_parquet(POINTS)
    lon0, lat0, lon1, lat1 = SCOPE_BBOX
    pts = pts[(pts["lon"] > lon0) & (pts["lon"] < lon1)
              & (pts["lat"] > lat0) & (pts["lat"] < lat1)].reset_index(drop=True)
    if limit is not None:
        pts = pts.head(limit)
    segs = gpd.read_parquet(SEGMENTS)
    route = dict(zip(segs["segment_id"], segs["route_name"]))
    # resnap target: the segment midpoint (in metric CRS), as (lat, lon)
    segs_m = segs.to_crs("EPSG:32618")
    mids = segs_m.geometry.interpolate(0.5, normalized=True)
    mids_ll = gpd.GeoSeries(mids, crs="EPSG:32618").to_crs("EPSG:4326")
    resnap = {sid: (geom.y, geom.x) for sid, geom in zip(segs_m["segment_id"], mids_ll)}
    return pts, route, resnap


def already_done(conn: sqlite3.Connection) -> set[str]:
    """Points that need no more work on resume: those with provenance, plus those the QA pass
    terminally discarded (whose provenance was deleted) so they are not re-fetched every run."""
    prov = conn.execute("SELECT DISTINCT point_id FROM provenance").fetchall()
    discarded = conn.execute(
        "SELECT point_id FROM qa_log WHERE decision='discarded'"
    ).fetchall()
    return {r[0] for r in prov} | {r[0] for r in discarded}


def fetch_all(conn, token, points, nm_map, stats) -> None:
    """Fetch every scoped point (cache-first), 4 concurrent. Writes cache + provenance."""
    done = already_done(conn)
    todo = points[~points["point_id"].isin(done)]
    # unique coordinates still needing a network call (not fully cached)
    coord_to_points: dict[tuple, list] = {}
    for row in todo.itertuples(index=False):
        coord_to_points.setdefault(key(row.lat, row.lon), []).append(row)

    need_fetch = []
    for (lat5, lon5), rows in coord_to_points.items():
        have = cached_payloads(conn, lat5, lon5)
        if all(f in have for f in FIELDS):
            _persist_coord(conn, rows, have, nm_map, stats, from_cache=True)
        else:
            need_fetch.append((lat5, lon5, rows[0].lat, rows[0].lon, rows))

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as ex:
        futures = {
            ex.submit(fetch_coord, token, lat, lon, FIELDS): (lat5, lon5, rows)
            for (lat5, lon5, lat, lon, rows) in need_fetch
        }
        for i, fut in enumerate(as_completed(futures), 1):
            lat5, lon5, rows = futures[fut]
            payloads, saw_429 = fut.result()
            stats["calls"] += 1
            stats["rate_limited"] += int(saw_429)
            put_cache(conn, lat5, lon5, payloads)
            _persist_coord(conn, rows, payloads, nm_map, stats, from_cache=False)
            if i % 25 == 0:  # commit often so a crash/kill re-spends few calls on resume
                conn.commit()
            if i % 200 == 0:
                print(f"  fetched {i}/{len(futures)} coords", flush=True)
    conn.commit()


def _persist_coord(conn, rows, payloads, nm_map, stats, from_cache: bool) -> None:
    for row in rows:
        write_provenance(conn, row.point_id, payloads, nm_map)
        if from_cache:
            stats["cache_hits"] += 1


def run_qa(conn, token, points, route, resnap, nm_map, stats) -> None:
    """Post-fetch snap QA: keep / re-snap once / discard, logged. PRD section 6 stage 2."""
    for row in points.itertuples(index=False):
        nrn = _prov_value(conn, row.point_id, "nearest_road_name")
        ndist = _prov_value(conn, row.point_id, "nearest_road_distance_m")
        route_name = route.get(row.segment_id)
        decision, reason = qa_triage_decision(nrn, route_name, ndist)

        if decision == "resnap":
            decision, reason = _resnap_once(conn, token, row, route_name, resnap, nm_map, stats)
        stats["qa"][decision] = stats["qa"].get(decision, 0) + 1
        conn.execute(
            "INSERT OR REPLACE INTO qa_log VALUES(?,?,?,?,?)",
            (row.point_id, decision, reason, nrn, route_name),
        )
    conn.commit()


def _resnap_once(conn, token, row, route_name, resnap, nm_map, stats) -> tuple[str, str]:
    """Re-sample at the segment midpoint once; keep (rewriting provenance) or discard."""
    target = resnap.get(row.segment_id)
    if target is None:
        return "keep", "no_resnap_geometry"
    alt_lat, alt_lon = target
    lat5, lon5 = key(alt_lat, alt_lon)
    have = cached_payloads(conn, lat5, lon5)
    if not all(f in have for f in FIELDS):
        have, saw_429 = fetch_coord(token, alt_lat, alt_lon, FIELDS)
        stats["calls"] += 1
        stats["rate_limited"] += int(saw_429)
        put_cache(conn, lat5, lon5, have)
    alt_nrn = have.get("nearest_road_name", {}).get("value")
    alt_dist = have.get("nearest_road_distance_m", {}).get("value")
    decision, _ = qa_triage_decision(alt_nrn, route_name, alt_dist)
    if decision != "resnap":  # resnapped point is on a road now -> keep it
        write_provenance(conn, row.point_id, have, nm_map)  # re-point to the resnapped sample
        return "resnapped", "resnap_on_road"
    conn.execute("DELETE FROM provenance WHERE point_id=?", (row.point_id,))
    return "discarded", "resnap_still_far_from_road"


# ---------- audit + kill ----------

def build_audit(conn, points, stats) -> dict:
    """Audit: per-field status breakdown (present/absent-semantic/failed), null & failed rates,
    confidence dist, QA outcomes, calls vs cache. The kill criterion is judged on the FAILED rate
    of W/S scoring fields — semantic absence is handled downstream (components drop out of a
    factor's average), so it must not trip a 'scoring design needs rethinking' stop."""
    rows = conn.execute("SELECT field, status, confidence FROM provenance").fetchall()
    status_dist: dict[str, dict[str, int]] = {}
    conf_dist: dict[str, int] = {}
    for field, status, conf in rows:
        status_dist.setdefault(field, {"present": 0, "absent-semantic": 0, "failed": 0})
        status_dist[field][status] = status_dist[field].get(status, 0) + 1
        conf_dist[conf or "none"] = conf_dist.get(conf or "none", 0) + 1

    null_rate, failed_rate = {}, {}
    for field, dist in status_dist.items():
        total = sum(dist.values()) or 1
        null_rate[field] = round((total - dist["present"]) / total, 3)   # non-present (semantic+failed)
        failed_rate[field] = round(dist["failed"] / total, 3)            # data gaps only

    ws_failed = {f: failed_rate.get(f, 1.0) for f in SCORING_WS_FIELDS}
    over = {f: r for f, r in ws_failed.items() if r > NULL_KILL_THRESHOLD}
    # Cumulative corridor cost: distinct coordinates ever fetched (the real /v1/fetch call count to
    # cover the corridor), independent of how many resumed runs it took. This is the headline
    # number for the corridor critique (point API -> one call per sample point).
    total_coord_fetches = conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT lat5, lon5 FROM cache)"
    ).fetchone()[0]
    return {
        "scope": SCOPE_LABEL,
        "points_in_scope": len(points),
        "total_coordinate_fetches": total_coord_fetches,  # cumulative corridor cost (point API)
        "calls_made": stats["calls"],                 # network coordinate-fetches THIS run
        "points_from_cache": stats["cache_hits"],     # points served from a prior run's cache
        "rate_limited": stats["rate_limited"],
        "wall_seconds": round(stats["wall_seconds"], 1),
        "qa": stats["qa"],
        "confidence_distribution": conf_dist,
        "status_distribution_per_field": status_dist,
        "null_rate_per_field": null_rate,
        "failed_rate_per_field": failed_rate,
        "kill_check_ws_failed_rate": ws_failed,
        "kill_fired": bool(over),
        "kill_offenders": over,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="fetch only the first N scoped points")
    args = ap.parse_args()

    points, route, resnap = load_scope(args.limit)
    stats = {"calls": 0, "cache_hits": 0, "rate_limited": 0, "wall_seconds": 0.0,
             "qa": {"keep": 0, "resnapped": 0, "discarded": 0}}

    conn = open_db()
    token = probe.load_token()
    t0 = time.perf_counter()
    # Main-thread client (strict timeouts) for catalog validation + QA resnaps; worker threads
    # each build their own client inside fetch_all.
    with make_client(token) as client:
        # CLAUDE.md: validate every field name against the LIVE catalog before any fetch.
        catalog = probe.fetch_catalog(client)
        qa_fields = ["nearest_road_name", "nearest_road_distance_m"]
        missing = probe.validate_fields(catalog, FIELDS + SCORING_WS_FIELDS + qa_fields)
        if missing:
            print("STOP: field names not in the live catalog:", [m for m, _ in missing])
            conn.close()
            return 2
        nm_map = {f["name"]: f.get("null_meaning") for f in catalog}

        print(f"Fetching {len(points)} scoped points ({len(FIELDS)} fields each)...")
        fetch_all(conn, token, points, nm_map, stats)
        print("Running snap QA...")
        run_qa(conn, token, points, route, resnap, nm_map, stats)
    stats["wall_seconds"] = time.perf_counter() - t0

    audit = build_audit(conn, points, stats)
    AUDIT_OUT.write_text(json.dumps(audit, indent=2))
    conn.close()

    print(json.dumps({k: audit[k] for k in (
        "points_in_scope", "calls_made", "points_from_cache", "rate_limited", "wall_seconds",
        "qa", "kill_fired", "kill_offenders")}, indent=2))
    if audit["kill_fired"]:
        print(f"\nKILL CRITERION: W/S scoring fields over {NULL_KILL_THRESHOLD:.0%} null: "
              f"{audit['kill_offenders']}. Scoring design needs rethinking, not imputation.")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
