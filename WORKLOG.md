# Subgrade work log

Running record of what each build session did, decided, and left for the next.
Workflow: **one continuous conversation** (no `/clear` between sessions). At the start of a
session, read this file + `ERRORS.md`; at the end, append the session's entry here.

Legend: ✅ done · ⏸ deferred/blocked · ⚠️ kill-criterion note

---

## Session 0 — Repo, constitution, subagents, hooks
**Date:** 2026-07-15 · **Commit:** `40a7127`

**Done**
- ✅ `CLAUDE.md` — standing rules (PRD authority, lean code, field-name validation vs live
  catalog, `null_meaning`-driven null handling, provenance-or-no-value, 3-part definition of
  done, ERRORS.md protocol).
- ✅ `.claude/agents/code-reviewer.md` + `data-qa.md` — the two judgment subagents.
- ✅ `.claude/settings.json` — PostToolUse hook runs `.venv/bin/ruff check --fix` on edited/written
  `.py` files. Verified end-to-end (stripped unused imports from a test file).
- ✅ `.gitignore` (`data/`, `.env`, `__pycache__`, `.venv/`), empty `ERRORS.md`, git init.
- ✅ Python 3.11 `.venv` via uv with the 11 required packages only; all import clean, `ruff` +
  `pytest` run.
- ✅ `PRD-subgrade.md` in repo root; `.env` with a valid `MIREYE_TOKEN` (gitignored) +
  `.env.example`.

**Learned / decided**
- Mireye catalog `https://api.mireye.com/v1/meta/fields` is **public** (HTTP 200, no token) and
  returns per-field `null_meaning`, `layer`, `interpretation_hints`.
- Token is a 90-day `api_token` JWT (`iss: mireye-earth`, exp **2026-10-14**); `Bearer` header
  accepted on catalog.
- `/v1/fetch` request contract (auth shape, params, rate limits) **not** explored — that is
  Session 1's job by design.

**Left for next session (Session 1 — Probe)**
- ⏸ Discover the `/v1/fetch` contract; validate all 17 core + supporting field names (PRD §7)
  against `data/field_catalog.json`.
- ⏸ ~100-call probe across Loudoun County: latency, 429 behavior, per-field null rates,
  road-miles-per-hour extrapolation at 3 points/segment.
- ⚠️ Kill criterion: if sustainable rate can't cover the county in ~1 hour, stop and report the
  three VDOT districts to scope down to.
