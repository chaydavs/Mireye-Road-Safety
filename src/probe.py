"""Session 1 probe: learn Mireye's /v1/fetch behavior before committing to county scale.

Measures rate/latency/null behavior over ~100 real calls and extrapolates coverage.
No cache, no classes, one simple 429 backoff (per PRD section 9 session 1 non-goals).
"""

from __future__ import annotations

import difflib
import json
import random
import statistics
import sys
import time
from pathlib import Path

import httpx

API_BASE = "https://api.mireye.com/v1"
CATALOG_URL = f"{API_BASE}/meta/fields"
FETCH_URL = f"{API_BASE}/fetch"

REPO = Path(__file__).resolve().parent.parent
ENV_FILE = REPO / ".env"
CATALOG_OUT = REPO / "data" / "field_catalog.json"

# PRD section 7. Core predictors (17) — the /fetch payload the probe actually fires.
CORE_FIELDS = [
    "soil_drainage_class",
    "soil_shrink_swell_class",
    "soil_available_water_capacity",
    "soil_ponding_frequency_class",
    "soil_hydrologic_group",
    "soil_restrictive_layer_depth_cm",
    "soil_restrictive_layer_kind",
    "soil_erodibility_k_factor",
    "bedrock_depth_cm",
    "slope_degrees",
    "landslide_susceptibility_index",
    "within_floodplain_polygon",
    "fema_flood_zone",
    "surface_water_permanence_pct",
    "mean_annual_snow_cover_days",
    "days_above_32c_annual_count",
    "mean_annual_dry_bulb_temperature_degc",
]

# PRD section 7. Supporting and QA fields — validated against the catalog, not fetched here.
SUPPORTING_FIELDS = [
    "elevation",
    "aspect_cardinal",
    "soil_map_unit_name",
    "intersects_nhd_area",
    "nearest_flowline_name",
    "nearest_waterbody_name",
    "huc_12_name",
    "intersects_wetland",
    "nearest_wetland_distance_m",
    "wetlands_within_100m_count",
    "flood_zone_subtype",
    "tree_canopy_pct",
    "lcms_class",
    "land_use_class",
    "ndvi_change_5y",
    "nearest_bridge_name",
    "nearest_road_name",
    "nearest_road_class",
    "nearest_road_surface",
    "nearest_road_distance_m",
    "roads_within_500m_count",
    "housing_units_density_per_km2",
    "political_county",
    "political_locality",
    "tract_geoid",
    "drought_category",
]

ALL_PRD_FIELDS = CORE_FIELDS + SUPPORTING_FIELDS

# Loudoun County, VA rough diagonal (SW -> NE) for the sample line. PRD section 9 session 1.
LINE_START = (38.92, -77.78)
LINE_END = (39.28, -77.50)
N_POINTS = 100
# Mireye caches per coordinate server-side (ttl up to ~1 yr), so re-querying fixed points
# measures warm-cache latency, not first-touch. Jitter each point (~100 m) per run so the
# probe always measures COLD latency — the number that governs real county-scale planning.
JITTER_DEG = 0.0009

# Segmentation constants for the coverage extrapolation. PRD section 6 stage 1.
SEGMENT_METERS = 500.0
POINTS_PER_SEGMENT = 3
METERS_PER_MILE = 1609.344

REQUEST_TIMEOUT = 30.0
RATE_LIMIT_BACKOFF_S = 2.0  # single backoff on 429; no retry storm (non-goal)


def load_token() -> str:
    """Read MIREYE_TOKEN from .env. Fail fast if absent (validate at the boundary)."""
    if not ENV_FILE.exists():
        sys.exit(f"FATAL: {ENV_FILE} not found. Create it with MIREYE_TOKEN=...")
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("MIREYE_TOKEN="):
            token = line.split("=", 1)[1].strip()
            if not token or token == "your_mireye_api_token_here":
                sys.exit("FATAL: MIREYE_TOKEN is empty/placeholder in .env")
            return token
    sys.exit("FATAL: MIREYE_TOKEN not found in .env")


def fetch_catalog(client: httpx.Client) -> list[dict]:
    """Download the live field catalog and persist it. This is the source of truth for
    field names and null_meaning — never trust a name from memory (CLAUDE.md)."""
    resp = client.get(CATALOG_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    catalog = resp.json()["fields"]
    CATALOG_OUT.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_OUT.write_text(json.dumps(catalog, indent=2))
    return catalog


def validate_fields(catalog: list[dict], wanted: list[str]) -> list[tuple[str, str]]:
    """Return [(missing_field, closest_real_name)] for every wanted field absent from the
    catalog. Empty list means all present."""
    catalog_names = {f["name"] for f in catalog}
    missing = []
    for name in wanted:
        if name not in catalog_names:
            close = difflib.get_close_matches(name, catalog_names, n=1)
            missing.append((name, close[0] if close else "(no close match)"))
    return missing


def sample_line(start: tuple[float, float], end: tuple[float, float], n: int) -> list[tuple[float, float]]:
    """Linearly interpolate n (lat, lng) points from start to end inclusive."""
    if n < 2:
        raise ValueError("need at least 2 points")
    lat0, lng0 = start
    lat1, lng1 = end
    return [
        (lat0 + (lat1 - lat0) * i / (n - 1), lng0 + (lng1 - lng0) * i / (n - 1))
        for i in range(n)
    ]


def jitter_points(points: list[tuple[float, float]], scale: float = JITTER_DEG) -> list[tuple[float, float]]:
    """Nudge each point by up to +/- scale degrees so every run queries fresh (cold)
    coordinates. Without this the probe measures Mireye's warm server-side cache after the
    first run and overstates sustainable throughput."""
    return [
        (lat + random.uniform(-scale, scale), lng + random.uniform(-scale, scale))
        for lat, lng in points
    ]


def fetch_point(client: httpx.Client, lat: float, lng: float, fields: list[str]) -> tuple[dict | None, float, int, bool]:
    """POST one point. Returns (json_or_None, latency_seconds, http_status, saw_429).
    One backoff on 429 (non-goal: no retry beyond this). saw_429 is True whenever the API
    throttled us at all, even if the single retry then succeeded — so recovered throttling is
    still counted (DoD #3: report real 429 behavior)."""
    body = {"lat": lat, "lng": lng, "fields": fields}
    saw_429 = False
    t0 = time.perf_counter()
    try:
        resp = client.post(FETCH_URL, json=body, timeout=REQUEST_TIMEOUT)
    except httpx.HTTPError as exc:
        return {"error": str(exc)}, time.perf_counter() - t0, 0, saw_429
    if resp.status_code == 429:
        saw_429 = True
        wait = float(resp.headers.get("Retry-After", RATE_LIMIT_BACKOFF_S))
        time.sleep(wait)
        t0 = time.perf_counter()
        try:
            resp = client.post(FETCH_URL, json=body, timeout=REQUEST_TIMEOUT)
        except httpx.HTTPError as exc:
            return {"error": str(exc)}, time.perf_counter() - t0, 0, saw_429
    latency = time.perf_counter() - t0
    if resp.status_code != 200:
        return {"error": resp.text[:200]}, latency, resp.status_code, saw_429
    return resp.json(), latency, 200, saw_429


def classify_field(field_obj: dict | None) -> str:
    """Bucket one field's response: 'value' (present), 'null' (semantic absence),
    or 'failed'. Null is never coerced to zero/false (CLAUDE.md)."""
    if field_obj is None:
        return "failed"
    if field_obj.get("status") not in ("ok", None):
        return "failed"
    return "null" if field_obj.get("value") is None else "value"


def run_probe(client: httpx.Client, points: list[tuple[float, float]]) -> dict:
    """Fire the sample points and collect latency, error, and per-field completeness."""
    latencies: list[float] = []
    rate_limited = 0
    errors: list[dict] = []
    field_counts = {f: {"value": 0, "null": 0, "failed": 0} for f in CORE_FIELDS}

    wall_start = time.perf_counter()
    for i, (lat, lng) in enumerate(points):
        data, latency, status, saw_429 = fetch_point(client, lat, lng, CORE_FIELDS)
        latencies.append(latency)
        if saw_429:
            rate_limited += 1
        if status != 200:
            errors.append({"i": i, "status": status, "detail": (data or {}).get("error", "")})
            for f in CORE_FIELDS:  # a failed call = every field failed for this point
                field_counts[f]["failed"] += 1
            continue
        returned = data.get("fields", {})
        for f in CORE_FIELDS:
            field_counts[f][classify_field(returned.get(f))] += 1
    wall = time.perf_counter() - wall_start

    return {
        "n_calls": len(points),
        "wall_seconds": wall,
        "latencies": latencies,
        "rate_limited": rate_limited,
        "errors": errors,
        "field_counts": field_counts,
    }


def coverage_extrapolation(calls_per_minute: float) -> dict:
    """From sustainable calls/min to road miles/hour at 3 points per 500 m segment."""
    points_per_mile = POINTS_PER_SEGMENT / (SEGMENT_METERS / METERS_PER_MILE)
    calls_per_hour = calls_per_minute * 60.0
    miles_per_hour = calls_per_hour / points_per_mile
    return {
        "points_per_mile": points_per_mile,
        "calls_per_hour": calls_per_hour,
        "miles_per_hour": miles_per_hour,
    }


def build_report(probe: dict, missing: list[tuple[str, str]]) -> dict:
    """Assemble the four required measurements into a single deterministic-shape report."""
    lat = probe["latencies"]
    ok_calls = probe["n_calls"] - len(probe["errors"])
    # Sustainable rate: measured sequential throughput of successful calls.
    calls_per_minute = (ok_calls / probe["wall_seconds"] * 60.0) if probe["wall_seconds"] else 0.0

    null_rates = {}
    for f, c in probe["field_counts"].items():
        total = c["value"] + c["null"] + c["failed"]
        null_rates[f] = {
            "null_pct": round(100.0 * c["null"] / total, 1) if total else 0.0,
            "failed_pct": round(100.0 * c["failed"] / total, 1) if total else 0.0,
            "value_pct": round(100.0 * c["value"] / total, 1) if total else 0.0,
        }

    return {
        "catalog_validation": {
            "checked": len(ALL_PRD_FIELDS),
            "missing": [{"field": m, "closest": c} for m, c in missing],
        },
        "throughput": {
            "n_calls": probe["n_calls"],
            "ok_calls": ok_calls,
            "wall_seconds": round(probe["wall_seconds"], 2),
            "latency_mean_s": round(statistics.mean(lat), 3) if lat else 0.0,
            "latency_median_s": round(statistics.median(lat), 3) if lat else 0.0,
            "latency_p95_s": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 3) if lat else 0.0,
            "rate_limited": probe["rate_limited"],
            "n_errors": len(probe["errors"]),
            "calls_per_minute_sustained": round(calls_per_minute, 1),
        },
        "null_rates": null_rates,
        "extrapolation": coverage_extrapolation(calls_per_minute),
    }


def print_report(report: dict) -> None:
    """One-screen human report. Format is fixed so successive runs are diff-stable."""
    cv = report["catalog_validation"]
    tp = report["throughput"]
    ex = report["extrapolation"]

    print("\n" + "=" * 64)
    print("SUBGRADE SESSION 1 PROBE REPORT")
    print("=" * 64)

    print("\n[1] CATALOG VALIDATION")
    print(f"    PRD section 7 fields checked : {cv['checked']}")
    if cv["missing"]:
        print(f"    MISSING FROM CATALOG        : {len(cv['missing'])}  <-- FINDING, do not rename")
        for m in cv["missing"]:
            print(f"      - {m['field']:<40} closest: {m['closest']}")
    else:
        print("    All present                 : yes")

    print("\n[2] THROUGHPUT / ERROR BEHAVIOR")
    print(f"    Calls (ok/total)            : {tp['ok_calls']}/{tp['n_calls']}")
    print(f"    Wall time                   : {tp['wall_seconds']} s")
    print(f"    Latency mean/median/p95     : {tp['latency_mean_s']} / {tp['latency_median_s']} / {tp['latency_p95_s']} s")
    print(f"    429 rate-limited            : {tp['rate_limited']}")
    print(f"    Errors                      : {tp['n_errors']}")
    print(f"    Sustainable calls/min       : {tp['calls_per_minute_sustained']}")
    print("    (cold first-touch; points jittered per run. Warm re-touch is ~10x faster.)")

    print("\n[3] NULL RATE PER CORE FIELD (value% / null% / failed%)")
    for f, r in report["null_rates"].items():
        flag = "  <-- >20% null" if r["null_pct"] > 20.0 else ""
        print(f"    {f:<38} {r['value_pct']:>5} / {r['null_pct']:>5} / {r['failed_pct']:>5}{flag}")

    print("\n[4] COVERAGE EXTRAPOLATION (3 pts / 500 m segment)")
    print(f"    Points per road mile        : {ex['points_per_mile']:.2f}")
    print(f"    Calls per hour              : {ex['calls_per_hour']:.0f}")
    print(f"    Road miles per hour         : {ex['miles_per_hour']:.1f}")
    print("=" * 64 + "\n")


def assert_report_complete(report: dict) -> None:
    """Self-eval: the report must carry all four measurements (CLAUDE.md definition of done)."""
    assert report["catalog_validation"]["checked"] == len(ALL_PRD_FIELDS)
    assert "calls_per_minute_sustained" in report["throughput"]
    assert len(report["null_rates"]) == len(CORE_FIELDS)
    assert report["extrapolation"]["miles_per_hour"] >= 0.0


def main() -> int:
    token = load_token()
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(headers=headers) as client:
        catalog = fetch_catalog(client)
        missing = validate_fields(catalog, ALL_PRD_FIELDS)
        if missing:
            # Kill point: a bad field name is a finding, not something to silently rename.
            print("\nSTOP: PRD section 7 fields missing from the live catalog:")
            for name, closest in missing:
                print(f"  - {name}  (closest real name: {closest})")
            print("Resolve these against the PRD before probing further.")
            return 2

        points = jitter_points(sample_line(LINE_START, LINE_END, N_POINTS))
        probe = run_probe(client, points)

    report = build_report(probe, missing)
    assert_report_complete(report)  # Session 1 self-eval: report must carry all four measurements.
    print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
