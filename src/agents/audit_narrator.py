"""Session 6 audit narrator (PRD agentic layer). Reads data/audit.json + ERRORS.md and drafts
docs/shortfalls.md — the "where Mireye falls short" report — as a DRAFT for human editing, clearly
marked as agent-drafted. It reports the measured numbers; it does not invent shortfalls.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC / "agents"))
import copilot  # noqa: E402  (reuse the .env-aware Anthropic client)

REPO = SRC.parent
AUDIT = REPO / "data" / "audit.json"
ERRORS = REPO / "ERRORS.md"
OUT = REPO / "docs" / "shortfalls.md"
MODEL = "claude-sonnet-5"

SYSTEM = (
    "You draft a candid engineering report titled 'Where Mireye falls short for linear-asset "
    "risk scoring', based ONLY on the supplied audit JSON and error log. Ground every claim in "
    "the supplied numbers — quote the actual null rates, call counts, and the LTPP result. Do not "
    "invent shortfalls or numbers. The PRD pre-registered these findings to confirm from the "
    "build: (1) NO corridor/polyline primitive — Mireye is a point API, so corridor coverage is "
    "simulated by spraying thousands of point queries (costly, lossy); (2) no precipitation field; "
    "(3) no freeze-thaw cycle count (snow-cover days is only a proxy); (4) SSURGO/STATSGO soil is "
    "reconnaissance-scale, so scores are screening-grade and confidence is capped (few/no A grades). "
    "Weave the audit numbers into these. Keep it tight, honest, and useful."
)


def main() -> int:
    if not AUDIT.exists():
        print(f"No {AUDIT} yet; run the fetch/validate stages first.")
        return 1
    audit = json.loads(AUDIT.read_text())
    errors = ERRORS.read_text() if ERRORS.exists() else "(none)"

    # Compact view: drop the bulky per-field status dump so the LTPP validation result and null
    # rates always fit (untruncated) — otherwise the narrator can't see the LTPP test.
    compact = {k: v for k, v in audit.items() if k != "status_distribution_per_field"}

    prompt = (
        "Draft the shortfalls report. Audit JSON:\n```json\n"
        + json.dumps(compact, indent=2)
        + "\n```\n\nERRORS.md (AI error log):\n```\n" + errors[:3000] + "\n```\n"
        "Structure: a 2-3 sentence summary, then one short section per pre-registered finding with "
        "the supporting number, then a 'measured from this build' section (null rates, calls vs "
        "cache, and the LTPP validation result from the `ltpp_validation` key: n, ground coef, "
        "permutation p-value, and quartile deterioration medians), then 'headline product "
        "feedback' (the corridor/polyline endpoint). Do NOT confuse the fetch snap-QA (`qa` key: "
        "keep/keep_flag/discard) with the LTPP validation (`ltpp_validation` key)."
    )
    client = copilot.anthropic_client()
    # generous budget: claude-sonnet-5 uses extended thinking by default, so leave room for the
    # report text after the thinking tokens.
    resp = client.messages.create(model=MODEL, max_tokens=8000, system=SYSTEM,
                                  messages=[{"role": "user", "content": prompt}])
    body = "".join(b.text for b in resp.content if b.type == "text")

    header = ("<!-- AGENT-DRAFTED by src/agents/audit_narrator.py from data/audit.json + ERRORS.md. "
              "DRAFT for human editing — verify every number before sharing. -->\n\n")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(header + body + "\n")
    print(f"Wrote agent-drafted {OUT} ({len(body)} chars). Review before sharing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
