"""Live "current stress" layer over the static fragility score. fragility (the static risk score)
x current stress -> a watch list. Sources are live and free (no key): NWS alerts + precipitation,
USGS instantaneous discharge. Every live value carries its own provenance (source, url, fetched_at)
inline in the trigger, so the UI can show its age. Refresh is a button; there is NO polling loop.

Design rules honored: keep only flood / flash-flood / winter-storm alerts; flag a gage only when its
current discharge exceeds the median of its OWN daily series (no invented flood-stage thresholds);
degrade to a calm "no active stress" state instead of erroring when the sky is clear.
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd
from shapely.geometry import shape

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))

REPO = SRC.parent
DATA = REPO / "data"
SCORES = DATA / "scores.parquet"
SEGMENTS = DATA / "segments.parquet"
SEGMENT_GAGES = DATA / "segment_gages.parquet"
WATCHLIST = DATA / "watchlist.parquet"
WATCH_META = DATA / "watchlist_meta.json"

USER_AGENT = "Subgrade/1.0 (chaydav4@gmail.com)"
NWS_ALERTS = "https://api.weather.gov/alerts/active?area=VA"
NWS_POINTS = "https://api.weather.gov/points/{lat},{lng}"
USGS_IV = "https://waterservices.usgs.gov/nwis/iv/"
USGS_DV = "https://waterservices.usgs.gov/nwis/dv/"
DISCHARGE = "00060"  # USGS parameter code: discharge, cfs

# Loudoun County identifiers used to tie a zone-based (null-geometry) alert to our segments.
LOUDOUN_UGC = "VAC107"
LOUDOUN_SAME = "051107"
ALERT_KEYWORDS = ("flood", "winter storm")  # matched case-insensitively against the event name
GAGE_NEAR_M = 15000.0     # a gage farther than this from a segment doesn't stress it
WET_WEEK_MM = 25.4        # heuristic: >1 inch over 7 days = a wet week (NOT a flood-stage threshold)
LOUDOUN_POINT = (39.08, -77.52)  # a representative point for the county's NWS station + precip


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"},
                        timeout=httpx.Timeout(30.0, connect=10.0))


# ---------- (1) NWS active alerts ----------

def fetch_alerts(client: httpx.Client) -> list[dict]:
    """Active VA flood / flash-flood / winter-storm alerts. Returns [] on any failure (calm state)."""
    try:
        resp = client.get(NWS_ALERTS)
        resp.raise_for_status()
        features = resp.json().get("features", [])
    except (httpx.HTTPError, ValueError):
        return []
    fetched = _now()
    out = []
    for f in features:
        p = f.get("properties", {})
        event = (p.get("event") or "")
        if not any(k in event.lower() for k in ALERT_KEYWORDS):
            continue
        geom = f.get("geometry")
        codes = (p.get("geocode") or {})
        affects_loudoun = (LOUDOUN_UGC in (codes.get("UGC") or [])
                           or LOUDOUN_SAME in (codes.get("SAME") or []))
        out.append({
            "event": event,
            "geometry": shape(geom) if geom else None,
            "affects_loudoun": affects_loudoun,
            "source": "NWS", "source_url": f.get("id") or NWS_ALERTS, "fetched_at": fetched,
        })
    return out


def segments_under_alert(segs: gpd.GeoDataFrame, alerts: list[dict]) -> dict[int, dict]:
    """segment_id -> triggering alert. Polygon intersect where a polygon exists; else Loudoun
    county coverage (all our segments are in Loudoun)."""
    hit: dict[int, dict] = {}
    for a in alerts:
        trig = {"type": "alert", "detail": a["event"], "source": a["source"],
                "source_url": a["source_url"], "at": a["fetched_at"], "confidence": "observed"}
        if a["geometry"] is not None:
            mask = segs.geometry.intersects(a["geometry"])
            for sid in segs.loc[mask, "segment_id"]:
                hit.setdefault(int(sid), trig)
        elif a["affects_loudoun"]:
            for sid in segs["segment_id"]:
                hit.setdefault(int(sid), trig)
    return hit


# ---------- (2) USGS gage stress ----------

def _usgs_site(gage_id) -> str | None:
    """Mireye returns 'USGS-01644000'; USGS water services want '01644000'."""
    if not isinstance(gage_id, str):
        return None
    return gage_id.split("-", 1)[1] if gage_id.startswith("USGS-") else gage_id


def fetch_gage_stress(client: httpx.Client, gage_ids: list[str]) -> dict[str, dict]:
    """Per gage: current instantaneous discharge vs the median of its OWN 365-day daily series.
    elevated = current > own median (no invented flood stage). Skips gages that fail."""
    out: dict[str, dict] = {}
    fetched = _now()
    for gid in gage_ids:
        site = _usgs_site(gid)
        if not site:
            continue
        try:
            iv = client.get(USGS_IV, params={"format": "json", "sites": site,
                                             "parameterCd": DISCHARGE, "siteStatus": "all"})
            dv = client.get(USGS_DV, params={"format": "json", "sites": site,
                                             "parameterCd": DISCHARGE, "period": "P365D"})
            iv.raise_for_status()
            dv.raise_for_status()
            current = _latest_value(iv.json())
            median = _series_median(dv.json())
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            continue
        if current is None or median is None:
            continue
        out[gid] = {
            "current_cfs": current, "median_cfs": median, "elevated": current > median,
            "source": "USGS", "fetched_at": fetched,
            "source_url": f"{USGS_IV}?format=json&sites={site}&parameterCd={DISCHARGE}",
        }
    return out


def _latest_value(payload: dict) -> float | None:
    ts = payload.get("value", {}).get("timeSeries", [])
    if not ts:
        return None
    vals = ts[0]["values"][0]["value"]
    for v in reversed(vals):
        if v["value"] not in ("", "-999999"):
            return float(v["value"])
    return None


def _series_median(payload: dict) -> float | None:
    ts = payload.get("value", {}).get("timeSeries", [])
    if not ts:
        return None
    vals = [float(x["value"]) for x in ts[0]["values"][0]["value"]
            if x["value"] not in ("", "-999999")]
    return statistics.median(vals) if vals else None


def segments_gage_stressed(seg_gages: pd.DataFrame, stress: dict[str, dict]) -> dict[int, dict]:
    """segment_id -> gage trigger, for segments whose nearest gage is elevated AND within range."""
    hit: dict[int, dict] = {}
    if seg_gages is None or seg_gages.empty:
        return hit
    for r in seg_gages.itertuples(index=False):
        s = stress.get(r.gage_id)
        dist = r.gage_distance_m
        if s and s["elevated"] and dist is not None and dist <= GAGE_NEAR_M:
            hit[int(r.segment_id)] = {
                "type": "gage", "source": "USGS", "source_url": s["source_url"],
                "at": s["fetched_at"], "confidence": "observed",
                "detail": f"{r.gage_name or r.gage_id}: {s['current_cfs']:.0f} cfs now vs "
                          f"{s['median_cfs']:.0f} cfs median",
            }
    return hit


# ---------- (3) wet-week ----------

def fetch_wet_week(client: httpx.Client) -> dict:
    """7-day precip sum at the county's nearest NWS station vs a wet-week heuristic. Calm on failure."""
    calm = {"wet": False, "total_mm": None, "threshold_mm": WET_WEEK_MM, "station": None,
            "source": "NWS", "source_url": None, "fetched_at": _now()}
    try:
        lat, lng = LOUDOUN_POINT
        pts = client.get(NWS_POINTS.format(lat=lat, lng=lng)).json()["properties"]
        stations = client.get(pts["observationStations"]).json()["features"]
        station_url = stations[0]["id"]
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=7)
        obs = client.get(f"{station_url}/observations", params={
            "start": start.isoformat(timespec="seconds"),
            "end": end.isoformat(timespec="seconds")}).json()["features"]
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return calm
    total = 0.0
    seen = False
    for o in obs:
        v = (o.get("properties", {}).get("precipitationLastHour") or {}).get("value")
        if v is not None:
            total += float(v)
            seen = True
    if not seen:
        return calm
    return {"wet": total >= WET_WEEK_MM, "total_mm": round(total, 1), "threshold_mm": WET_WEEK_MM,
            "station": station_url.rsplit("/", 1)[-1], "source": "NWS",
            "source_url": f"{station_url}/observations", "fetched_at": _now()}


# ---------- (4) watch list ----------

def build_watchlist(client: httpx.Client | None = None) -> tuple[pd.DataFrame, dict]:
    """watch_score = static score gated by (alert OR elevated gage OR wet week). Fixed schema
    regardless of what's active, so successive runs are diff-stable. Returns (df, meta)."""
    owns_client = client is None
    client = client or _client()
    try:
        scores = pd.read_parquet(SCORES)
        segs = gpd.read_parquet(SEGMENTS)[["segment_id", "geometry"]]
        seg_gages = pd.read_parquet(SEGMENT_GAGES) if SEGMENT_GAGES.exists() else None

        alerts = fetch_alerts(client)
        alert_hits = segments_under_alert(segs, alerts)
        gage_ids = sorted({g for g in seg_gages["gage_id"].dropna()}) if seg_gages is not None else []
        stress = fetch_gage_stress(client, gage_ids)
        gage_hits = segments_gage_stressed(seg_gages, stress)
        wet = fetch_wet_week(client)
        wet_trig = ({"type": "wet_week", "detail": f"{wet['total_mm']} mm in last 7 days "
                     f"(>= {wet['threshold_mm']} mm)", "source": wet["source"],
                     "source_url": wet["source_url"], "at": wet["fetched_at"],
                     "confidence": "observed"} if wet["wet"] else None)
    finally:
        if owns_client:
            client.close()

    rows = []
    for r in scores.itertuples(index=False):
        sid = int(r.segment_id)
        trigs = [t for t in (alert_hits.get(sid), gage_hits.get(sid), wet_trig) if t]
        rows.append({
            "segment_id": sid, "static_score": float(r.score),
            "watched": bool(trigs),
            "watch_score": float(r.score) if trigs else 0.0,
            "triggers": json.dumps(trigs),
        })
    df = pd.DataFrame(rows, columns=["segment_id", "static_score", "watched", "watch_score",
                                     "triggers"])
    meta = {
        "generated_at": _now(),
        "watched_segments": int(df["watched"].sum()),
        "active_alerts": [a["event"] for a in alerts],
        "elevated_gages": [g for g, s in stress.items() if s["elevated"]],
        "wet_week": wet,
        "calm": bool(df["watched"].sum() == 0),
    }
    return df, meta


def main() -> int:
    df, meta = build_watchlist()
    df.to_parquet(WATCHLIST)
    WATCH_META.write_text(json.dumps(meta, indent=2))
    if meta["calm"]:
        print(f"No active stress ({meta['generated_at']}). {len(df)} segments, 0 watch-listed.")
    else:
        print(f"{meta['watched_segments']} segments on the watch list. "
              f"alerts={meta['active_alerts']} gages={meta['elevated_gages']} "
              f"wet_week={meta['wet_week']['wet']}")
    print(f"Wrote {WATCHLIST} and {WATCH_META}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
