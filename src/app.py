"""Session 6 UI (PRD): one Streamlit page — left, the Folium risk map; right, the cited why-card
for the selected segment; bottom, the county-copilot chat. Deliberately thin; Streamlit defaults.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
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


def build_map(scored: gpd.GeoDataFrame, watched_ids: set | None = None) -> folium.Map:
    """Render the whole network as ONE GeoJson layer with a data-driven style. One folium layer
    per segment ships a ~5 MB, 2,600-layer map on every rerun and lags the browser; a single layer
    is ~10x faster to serialize. Static mode colors by risk score; live ("Right now") mode colors
    watch-listed segments red and greys the rest, so current stress pops against fragility."""
    g = scored[["segment_id", "route_name", "score", "grade", "geometry"]].copy()
    if watched_ids is not None:
        on = g["segment_id"].astype(int).isin(watched_ids)
        g["color"], g["weight"] = on.map({True: "#d7191c", False: "#cccccc"}), on.map({True: 5, False: 2})
    else:
        g["color"], g["weight"] = g["score"].map(score._color), 4
    # The tooltip string is what st_folium returns as last_object_clicked_tooltip (parsed for seg id).
    g["label"] = [f"seg {r.segment_id} — {r.route_name or 'unnamed'}: score {r.score} (grade {r.grade})"
                  for r in g.itertuples()]

    minx, miny, maxx, maxy = g.total_bounds
    m = folium.Map(location=[(miny + maxy) / 2, (minx + maxx) / 2], zoom_start=12)
    folium.GeoJson(
        g[["color", "weight", "label", "geometry"]],
        style_function=lambda f: {"color": f["properties"]["color"], "weight": f["properties"]["weight"]},
        tooltip=folium.GeoJsonTooltip(fields=["label"], labels=False),
        name="segments",
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


def demo_mode() -> bool:
    return "--demo" in sys.argv or bool(os.environ.get("SUBGRADE_DEMO"))


def load_watchlist() -> tuple[pd.DataFrame | None, dict]:
    """Live watch list; in --demo mode fall back to data/demo_snapshot/ so it works offline."""
    wl_path, meta_path = DATA / "watchlist.parquet", DATA / "watchlist_meta.json"
    if not wl_path.exists() and demo_mode():
        wl_path, meta_path = DATA / "demo_snapshot" / "watchlist.parquet", \
            DATA / "demo_snapshot" / "watchlist_meta.json"
    if not wl_path.exists():
        return None, {}
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    return pd.read_parquet(wl_path), meta


def data_age(iso: str | None) -> str:
    if not iso:
        return "unknown age"
    try:
        secs = (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
        mins = int(secs // 60)
        return f"{mins} min ago" if mins < 120 else f"{mins // 60} h ago"
    except ValueError:
        return "unknown age"


def trigger_lines(triggers_json: str) -> list[str]:
    """Live why-card lines: triggering condition + its source + timestamp + age (provenance)."""
    out = []
    for t in json.loads(triggers_json or "[]"):
        label = {"alert": "🌧️ Alert", "gage": "🌊 Gauge", "wet_week": "💧 Wet week"}.get(
            t["type"], t["type"])
        src = f" [source]({t['source_url']})" if t.get("source_url") else f" ({t.get('source')})"
        out.append(f"- **{label}:** {t['detail']} — {data_age(t.get('at'))}{src}")
    return out


def main() -> None:
    st.set_page_config(page_title="Subgrade — road deterioration risk", layout="wide")
    st.title("Subgrade — cited road deterioration risk")
    if demo_mode():
        st.caption("🎬 DEMO MODE — live layer served from an offline snapshot (airplane-mode safe).")
    scored = load_scored()

    # Live "Right now" layer: fragility (static score) vs current stress.
    watchlist, meta = load_watchlist()
    ctrl1, ctrl2, _ = st.columns([1, 1, 3])
    live_mode = ctrl1.toggle("Right now (live stress)", value=False,
                             help="Recolor the map to segments under active stress")
    if ctrl2.button("↻ Refresh live", help="Re-pull NWS + USGS (button, not polling)"):
        try:
            import live
            df, m = live.build_watchlist()
            df.to_parquet(live.WATCHLIST)
            live.WATCH_META.write_text(json.dumps(m, indent=2))
            watchlist, meta = df, m
        except Exception as exc:  # noqa: BLE001 - keep the last snapshot if live pull fails
            # watchlist/meta still hold the snapshot loaded above (only overwritten on success).
            st.warning(f"Live refresh failed ({exc}); showing the last snapshot.")

    watched_ids = None
    if live_mode:
        if watchlist is None:
            st.info("No live watch list yet — run `python src/live.py` (or click Refresh live).")
        else:
            watched_ids = set(watchlist.loc[watchlist["watched"], "segment_id"].astype(int))
            gen_age = data_age(meta.get("generated_at"))
            if meta.get("calm", True):
                st.success(f"✅ No active stress right now (as of {gen_age}). "
                           "Map shows fragility only; nothing is escalated.")
            else:
                st.error(f"⚠️ {meta.get('watched_segments', 0)} segments under current stress "
                         f"(as of {gen_age}) — alerts: {meta.get('active_alerts') or 'none'}; "
                         f"elevated gauges: {meta.get('elevated_gages') or 'none'}; "
                         f"wet week: {meta.get('wet_week', {}).get('wet')}.")
    else:
        st.caption(f"{len(scored)} scored segments · click a road to see its cited why-card")

    left, right = st.columns([3, 2])
    with left:
        fmap = build_map(scored, watched_ids=watched_ids)
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
        if "rsl_year_low" in scored.columns and pd.notna(r.get("rsl_year_low")):
            import service_life
            st.markdown("**⏳ " + service_life.render_rsl({
                "rsl_year_low": int(r["rsl_year_low"]), "rsl_year_high": int(r["rsl_year_high"]),
                "rsl_basis": r["rsl_basis"], "last_treated_year": r.get("rsl_last_treated"),
                "grade": r["rsl_grade"]}) + "**")
        if live_mode and watchlist is not None:
            wrow = watchlist[watchlist["segment_id"] == seg_id]
            trigs = trigger_lines(wrow.iloc[0]["triggers"]) if not wrow.empty else []
            if trigs:
                st.markdown("**⚡ Under stress right now:**")
                for line in trigs:
                    st.markdown(line)
            else:
                st.caption("No active stress on this segment right now.")
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
