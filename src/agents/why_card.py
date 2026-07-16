"""Session 4 why-card agent (PRD section 6 stage 5). For the top-N riskiest segments, compose a
CITED explanation whose every factual line traces to a provenance row (value + source + source_url
+ fetched_at). Mireye /v1/ask adds a plain-English narrative, included ONLY as a quoted supplement
— never as the source of a factual claim (PRD hard rule).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))
import probe  # noqa: E402

REPO = SRC.parent
DATA = REPO / "data"
CACHE_DB = DATA / "cache.sqlite"
SCORES = DATA / "scores.parquet"
SEGMENTS = DATA / "segments.parquet"
CARDS_OUT = DATA / "why_cards.json"
ASK_URL = "https://api.mireye.com/v1/ask"
TOP_N = 20
VDOT_SOURCE = "VDOT Bidirectional Traffic Volume 2025"
VDOT_URL = ("https://services.arcgis.com/p5v98VHDX9Atv3l7/arcgis/rest/services/"
            "VDOT_Bidirectional_Traffic_Volume_2025/FeatureServer/0")


def provenance_line(conn: sqlite3.Connection, segment_id: int, field: str) -> str | None:
    """A cited line for one field from ANY present point of the segment. Emitted only if the
    provenance is complete (value + source + source_url) — no provenance row, no sentence."""
    pids = [f"{segment_id}_{k}" for k in range(3)]  # a segment's 3 points, matched exactly
    row = conn.execute(
        "SELECT value, source, source_url, fetched_at FROM provenance "
        f"WHERE field=? AND status='present' AND point_id IN ({','.join('?' * len(pids))}) LIMIT 1",
        (field, *pids),
    ).fetchone()
    if not row:
        return None
    value, source, source_url, fetched_at = row
    if not (source and source_url):
        return None
    date = (fetched_at or "")[:10]
    return f"{field} = {json.loads(value)} ({source}, fetched {date}) [{source_url}]"


def traffic_line(aadt) -> str:
    return f"traffic AADT = {aadt} vehicles/day ({VDOT_SOURCE}) [{VDOT_URL}]"


def compose_card(conn, seg_row, seg_geom) -> dict:
    """Cited lines from provenance for the segment's top-3 drivers, plus an /ask narrative slot."""
    top3 = json.loads(seg_row["top3"])
    cited = []
    for driver in top3:
        field = driver["component"]
        if field == "traffic_aadt":
            cited.append(traffic_line(driver["value"]))  # real VDOT AADT, cited to VDOT
        else:
            # everything else (incl. the housing-density proxy) must come from a provenance row
            line = provenance_line(conn, int(seg_row["segment_id"]), field)
            if line:  # hard rule: skip any claim without a provenance row
                cited.append(line)
    mid = seg_geom.interpolate(0.5, normalized=True)
    return {
        "segment_id": int(seg_row["segment_id"]),
        "route_name": seg_row["route_name"],
        "score": float(seg_row["score"]),
        "grade": seg_row["grade"],
        "lat": round(mid.y, 5),
        "lng": round(mid.x, 5),
        "cited_lines": cited,
        "narrative": None,  # filled by /ask, clearly a supplement
    }


def ask_narrative(client: httpx.Client, lat: float, lng: float) -> str | None:
    try:
        resp = client.post(ASK_URL, json={
            "lat": lat, "lng": lng,
            "question": "What ground and climate conditions here most affect road deterioration risk?",
        }, timeout=30.0)
        resp.raise_for_status()
        return resp.json().get("answer")
    except (httpx.HTTPError, ValueError):
        return None


def render(card: dict) -> str:
    lines = [
        f"WHY-CARD  segment {card['segment_id']} — {card['route_name'] or 'unnamed road'}",
        f"Score {card['score']} (confidence {card['grade']})",
        "Drivers (each line cited to a federal source):",
    ]
    lines += [f"  - {c}" for c in card["cited_lines"]]
    if card["narrative"]:
        lines.append("Narrative (Mireye /ask — quoted supplement, not a source of claims):")
        lines.append(f"  \"{card['narrative'].splitlines()[0]}\"")
    return "\n".join(lines)


def main() -> int:
    segs = gpd.read_parquet(SEGMENTS).set_index("segment_id")
    df = pd.read_parquet(SCORES).sort_values("score", ascending=False).head(TOP_N)
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("PRAGMA busy_timeout=10000")
    token = probe.load_token()
    cards = []
    with httpx.Client(headers={"Authorization": f"Bearer {token}"}) as client:
        for _, seg_row in df.iterrows():
            geom = segs.loc[int(seg_row["segment_id"])].geometry
            card = compose_card(conn, seg_row, geom)
            card["narrative"] = ask_narrative(client, card["lat"], card["lng"])
            cards.append(card)
    conn.close()
    CARDS_OUT.write_text(json.dumps(cards, indent=2))
    print(render(cards[0]))
    print(f"\nWrote {len(cards)} why-cards to {CARDS_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
