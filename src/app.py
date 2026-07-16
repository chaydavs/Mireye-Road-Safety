"""Session 6 UI (PRD): one Streamlit page — left, the Folium risk map; right, the cited why-card
for the selected segment; bottom, the county-copilot chat. Deliberately thin; Streamlit defaults.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "agents"))
import score  # noqa: E402  (reuse the map colour ramp)
import why_card  # noqa: E402  (reuse the VDOT AADT source citation)

REPO = SRC.parent
DATA = REPO / "data"
SCORES = DATA / "scores.parquet"
SEGMENTS = DATA / "segments.parquet"
CACHE_DB = DATA / "cache.sqlite"


@st.cache_data
def load_scored() -> gpd.GeoDataFrame:
    scores = pd.read_parquet(SCORES)
    geoms = gpd.read_parquet(SEGMENTS)[["segment_id", "geometry"]]
    return gpd.GeoDataFrame(scores.merge(geoms, on="segment_id"), geometry="geometry",
                            crs=geoms.crs)


def build_map(scored: gpd.GeoDataFrame) -> folium.Map:
    minx, miny, maxx, maxy = scored.total_bounds
    m = folium.Map(location=[(miny + maxy) / 2, (minx + maxx) / 2], zoom_start=12)
    for _, r in scored.iterrows():
        folium.GeoJson(
            r.geometry.__geo_interface__,
            style_function=lambda _f, c=score._color(r["score"]): {"color": c, "weight": 4},
            tooltip=f"seg {r['segment_id']} — {r['route_name'] or 'unnamed'}: "
                    f"score {r['score']} (grade {r['grade']})",
            name=str(r["segment_id"]),
        ).add_to(m)
    return m


def provenance_lines(segment_id: int, drivers: list) -> list[str]:
    """Cited driver lines for the why-card, each traced to a provenance row (hard rule)."""
    pids = [f"{segment_id}_{k}" for k in range(3)]
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("PRAGMA busy_timeout=8000")
    lines = []
    for d in drivers:
        f = d["component"]
        if f == "traffic_aadt":
            # AADT is a VDOT join (not a Mireye provenance row), so cite it to its VDOT source + URL.
            lines.append(f"- **traffic** AADT {d['value']} veh/day — {why_card.VDOT_SOURCE} "
                         f"[source]({why_card.VDOT_URL})")
            continue
        row = conn.execute(
            "SELECT value, source, source_url, fetched_at FROM provenance WHERE field=? "
            f"AND status='present' AND point_id IN ({','.join('?' * len(pids))}) LIMIT 1",
            (f, *pids),
        ).fetchone()
        if row and row[1] and row[2]:
            lines.append(f"- **{f}** = {json.loads(row[0])} — {row[1]} ({(row[3] or '')[:10]}) "
                         f"[source]({row[2]})")
    conn.close()
    return lines


def main() -> None:
    st.set_page_config(page_title="Subgrade — road deterioration risk", layout="wide")
    st.title("Subgrade — cited road deterioration risk")
    scored = load_scored()
    st.caption(f"{len(scored)} scored segments · click a road to see its cited why-card")

    left, right = st.columns([3, 2])
    with left:
        fmap = build_map(scored)
        clicked = st_folium(fmap, height=520, use_container_width=True,
                            returned_objects=["last_object_clicked_tooltip"])
    with right:
        tip = (clicked or {}).get("last_object_clicked_tooltip")
        seg_id = int(tip.split("seg ")[1].split(" —")[0]) if tip and "seg " in tip else \
            int(scored.sort_values("score", ascending=False).iloc[0]["segment_id"])
        r = scored[scored["segment_id"] == seg_id].iloc[0]
        st.subheader(f"Why-card — segment {seg_id}: {r['route_name'] or 'unnamed road'}")
        st.metric("Risk score", r["score"], help=f"confidence grade {r['grade']}")
        st.write(f"**Grade {r['grade']}** · traffic source: {r['traffic_source']}")
        st.markdown("**Drivers (each cited to a federal source):**")
        for line in provenance_lines(seg_id, json.loads(r["top3"])):
            st.markdown(line)

    st.divider()
    st.subheader("County copilot")
    st.caption("Answers only from the scored data + live Mireye; refuses what the data can't answer.")
    q = st.chat_input("Ask about the segments (e.g. 'why is the top segment ranked first?')")
    if q:
        st.chat_message("user").write(q)
        with st.chat_message("assistant"):
            try:
                import copilot
                answer, transcript = copilot.run(q)
                for t in transcript:
                    st.caption(f"tool: {t['tool']}({t['input']})")
                st.write(answer)
            except Exception as exc:  # noqa: BLE001 - surface any copilot/config error in the UI
                st.error(f"Copilot unavailable ({exc}). Set ANTHROPIC_API_KEY to enable chat.")


def _in_streamlit() -> bool:
    try:
        from streamlit.runtime import exists
        return exists()
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__" or _in_streamlit():
    main()
