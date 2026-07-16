"""One-time static enrichment: fetch each segment's nearest USGS gage (id + distance) from Mireye
into data/segment_gages.parquet, with provenance. Requirement input for the live gage-stress layer
(the gage fields are in Mireye's catalog but weren't in the PRD section 7 fetch list).
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import pandas as pd

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))
import fetch  # noqa: E402  (reuse the hardened per-thread client + fetch_coord)
import probe  # noqa: E402

REPO = SRC.parent
SEGMENTS = REPO / "data" / "segments.parquet"
OUT = REPO / "data" / "segment_gages.parquet"
GAGE_FIELDS = ["nearest_usgs_gage_id", "nearest_usgs_gage_distance_m", "nearest_usgs_gage_name"]


def main() -> int:
    segs = gpd.read_parquet(SEGMENTS)
    cent = segs.geometry.centroid.to_crs("EPSG:4326")
    jobs = [(int(sid), round(g.y, 5), round(g.x, 5)) for sid, g in zip(segs["segment_id"], cent)]

    token = probe.load_token()
    with fetch.make_client(token) as client:  # CLAUDE.md: validate field names before fetching
        missing = probe.validate_fields(probe.fetch_catalog(client), GAGE_FIELDS)
    if missing:
        print("STOP: gage fields not in live catalog:", [m for m, _ in missing])
        return 2

    rows = []
    print(f"Fetching nearest-gage for {len(jobs)} segment centroids (4 concurrent)...")
    with ThreadPoolExecutor(max_workers=fetch.MAX_CONCURRENCY) as ex:
        futs = {ex.submit(fetch.fetch_coord, token, lat, lng, GAGE_FIELDS): sid
                for sid, lat, lng in jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            sid = futs[fut]
            payloads, _ = fut.result()
            gid = payloads.get("nearest_usgs_gage_id", {})
            dist = payloads.get("nearest_usgs_gage_distance_m", {})
            rows.append({
                "segment_id": sid,
                "gage_id": gid.get("value"),
                "gage_distance_m": dist.get("value"),
                "gage_name": payloads.get("nearest_usgs_gage_name", {}).get("value"),
                "source": gid.get("source"),
                "source_url": gid.get("source_url"),
                "fetched_at": gid.get("fetched_at"),
            })
            if i % 500 == 0:
                pd.DataFrame(rows).to_parquet(OUT)  # checkpoint
                print(f"  {i}/{len(jobs)}")
    pd.DataFrame(rows).to_parquet(OUT)
    got = sum(1 for r in rows if r["gage_id"])
    print(f"Done. {got}/{len(rows)} segments have a nearest gage. distinct gages: "
          f"{len({r['gage_id'] for r in rows if r['gage_id']})}. Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
