# Walkthrough — 30-minute demo script

1. **Problem (2 min).** More failing road-miles than budget; the decision that matters is *which
   segments to treat next*, which needs *predicting* deterioration from ground cause — data nobody
   assembles for local roads. (README §1–2.)
2. **Run the app.** `.venv/bin/streamlit run src/app.py` — one page: risk map, why-card, copilot.
3. **Map (left).** Leesburg + Ashburn corridor, segments colored by deterioration risk, worst in red;
   grade in the tooltip. This is the ranked output a public-works engineer acts on.
4. **Click the top segment → why-card (right).** Every driver line carries a **federal source URL**
   and fetch date (USDA soil, USGS landslide, FEMA flood, VDOT traffic). This is the anti-LLM point:
   cited, not invented.
5. **Copilot — "why is the top segment ranked first?"** → it calls `query_scores`, names Sycolin Rd,
   and cites the drivers (traffic + landslide susceptibility + soil erodibility) with sources.
6. **Copilot — "compare segment 3620 and 1315"** → cited side-by-side; volunteers that neither tool
   gives a failure *date*.
7. **Copilot — "which segment will fail in March 2027?"** → **refuses**, zero tool calls: "scores
   reflect relative risk, not a failure timeline… neither source produces forecasted dates." The
   agent is honest by construction.
8. **What worked — LTPP chart** (`output/ltpp_validation.png`). Measured deterioration vs Subgrade's
   ground score across 51 federal sections: right direction (top quartile ~17% faster), not
   significant (p≈0.26), no leakage. Reported honestly, not tuned. (README §4.)
9. **Where the data fell short** (`docs/shortfalls.md`): no precipitation, no freeze-thaw count,
   reconnaissance-scale soil (→ no A grades) — each backed by the audit's real null rates.
10. **The ask (headline):** Mireye has **no corridor primitive**. We sprayed thousands of point
    queries to fake a corridor (see the call counts in `data/audit.json`). A polyline endpoint opens
    the entire linear-asset market — pipes, rail, transmission, roads. The build *is* the evidence.
