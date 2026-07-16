"""Session 6 county copilot (PRD agentic layer). An Anthropic agent with exactly two tools —
query_scores (the scored segments + provenance store) and mireye_lookup (live Mireye data). Hard
rule mirrored from the why-card agent: every factual claim must come from a tool result; if the
data does not answer, say so — never speculate, never predict the future.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import anthropic
import httpx
import pandas as pd

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))
import probe  # noqa: E402

REPO = SRC.parent
DATA = REPO / "data"
SCORES = DATA / "scores.parquet"
CACHE_DB = DATA / "cache.sqlite"
MODEL = "claude-sonnet-5"
ASK_URL = "https://api.mireye.com/v1/ask"

SYSTEM = (
    "You are the Subgrade county copilot for a public-works engineer. Subgrade ranks road segments "
    "by ground-driven deterioration RISK (not current condition, not safety). Answer ONLY from the "
    "two tools: query_scores (scored segments + cited provenance + a remaining-service-life estimate) "
    "and mireye_lookup (live Mireye ground/climate data at a coordinate). Every factual claim must "
    "come from a tool result, with its source. "
    "WHEN a road will reach poor condition: give the estimated YEAR RANGE from query_scores "
    "(rsl_year_low-rsl_year_high) and its basis (rsl_basis: hpms/vdot/prior; 'prior' = treatment "
    "year unknown, screening-grade). NEVER give a single exact year or date — if pushed for one, "
    "refuse and explain it is a transparent screening estimate, not a prediction. For other future "
    "questions the data cannot cover, say so plainly. Never invent soil types, scores, or forecasts. "
    "Be concise."
)

TOOLS = [
    {
        "name": "query_scores",
        "description": ("Query the scored Subgrade segments and their provenance. Use for segment "
                        "risk scores, grades, rankings, drivers, and comparisons."),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["top", "segment", "route"],
                           "description": "top N by score, one segment by id, or segments on a route"},
                "segment_id": {"type": "integer"},
                "n": {"type": "integer", "description": "how many for action=top (default 5)"},
                "route_name": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "mireye_lookup",
        "description": ("Fetch live, cited ground/climate data from Mireye at a coordinate (via "
                        "/v1/ask). Use to re-check a segment fresh or get data not in the scores."),
        "input_schema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number"}, "lng": {"type": "number"},
                "question": {"type": "string"},
            },
            "required": ["lat", "lng"],
        },
    },
]


def _seg_summary(r) -> dict:
    out = {"segment_id": int(r["segment_id"]), "route_name": r["route_name"],
           "score": float(r["score"]), "grade": r["grade"],
           "traffic_source": r["traffic_source"], "top_drivers": json.loads(r["top3"])}
    if "rsl_year_low" in r and pd.notna(r.get("rsl_year_low")):
        out["reach_poor_condition_year_range"] = [int(r["rsl_year_low"]), int(r["rsl_year_high"])]
        out["rsl_basis"] = r["rsl_basis"]  # hpms | vdot | prior (prior = treatment year unknown)
        if pd.notna(r.get("rsl_last_treated")):
            out["last_treated_year"] = int(r["rsl_last_treated"])
    return out


def _provenance_for(conn, segment_id: int, fields: list[str]) -> dict:
    pids = [f"{segment_id}_{k}" for k in range(3)]
    out = {}
    for f in fields:
        row = conn.execute(
            "SELECT value, source, source_url FROM provenance WHERE field=? AND status='present' "
            f"AND point_id IN ({','.join('?' * len(pids))}) LIMIT 1", (f, *pids),
        ).fetchone()
        if row:
            out[f] = {"value": json.loads(row[0]), "source": row[1], "source_url": row[2]}
    return out


def query_scores(action: str, segment_id: int | None = None, n: int = 5,
                 route_name: str | None = None) -> object:
    scores = pd.read_parquet(SCORES)
    if action == "top":
        rows = scores.nlargest(n or 5, "score")
        return [_seg_summary(r) for _, r in rows.iterrows()]
    if action == "segment":
        r = scores[scores["segment_id"] == segment_id]
        if r.empty:
            return {"error": f"segment {segment_id} is not in the scored dataset"}
        summ = _seg_summary(r.iloc[0])
        conn = sqlite3.connect(CACHE_DB)
        conn.execute("PRAGMA busy_timeout=8000")
        driver_fields = [d["component"] for d in summ["top_drivers"] if d["component"] != "traffic_aadt"]
        summ["provenance"] = _provenance_for(conn, segment_id, driver_fields)
        conn.close()
        return summ
    if action == "route":
        rows = scores[scores["route_name"].str.contains(route_name or "", case=False, na=False)]
        return [_seg_summary(r) for _, r in rows.head(20).iterrows()]
    return {"error": f"unknown action {action}"}


def mireye_lookup(lat: float, lng: float, question: str | None = None) -> dict:
    q = question or "What ground and climate conditions here most affect road deterioration risk?"
    try:
        token = probe.load_token()
        resp = httpx.post(ASK_URL, headers={"Authorization": f"Bearer {token}"},
                          json={"lat": lat, "lng": lng, "question": q}, timeout=30.0)
        resp.raise_for_status()
        d = resp.json()
        return {"lat": lat, "lng": lng, "answer": d.get("answer"), "source": "Mireye /v1/ask",
                "answered_at": d.get("answered_at")}
    except (httpx.HTTPError, ValueError) as exc:
        return {"error": f"Mireye lookup failed: {exc}"}


def _dispatch(name: str, args: dict) -> object:
    if name == "query_scores":
        return query_scores(**args)
    if name == "mireye_lookup":
        return mireye_lookup(**args)
    return {"error": f"unknown tool {name}"}


def anthropic_client() -> anthropic.Anthropic:
    """Anthropic client keyed from ANTHROPIC_API_KEY in the environment or .env."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key and (REPO / ".env").exists():
        for line in (REPO / ".env").read_text().splitlines():
            if line.strip().startswith("ANTHROPIC_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY not set (environment or .env)")
    return anthropic.Anthropic(api_key=key)


def run(question: str) -> tuple[str, list]:
    """Run the tool-use loop. Returns (final_answer, transcript_of_tool_calls)."""
    client = anthropic_client()
    messages = [{"role": "user", "content": question}]
    transcript = []
    for _ in range(8):  # bounded tool-use loop
        # extended thinking is on by default for this model; budget for thinking + the answer.
        resp = client.messages.create(model=MODEL, max_tokens=3000, system=SYSTEM,
                                       tools=TOOLS, messages=messages)
        if resp.stop_reason != "tool_use":
            text = "".join(b.text for b in resp.content if b.type == "text")
            return text, transcript
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for block in resp.content:
            if block.type == "tool_use":
                result = _dispatch(block.name, dict(block.input))
                transcript.append({"tool": block.name, "input": block.input, "result": result})
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(result, default=str)})
        messages.append({"role": "user", "content": results})
    return "(stopped: tool-use loop exceeded)", transcript


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python src/agents/copilot.py \"<question>\"")
        return 1
    answer, transcript = run(sys.argv[1])
    print(f"Q: {sys.argv[1]}\n")
    for t in transcript:
        print(f"  [tool] {t['tool']}({t['input']})")
    print(f"\nA: {answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
