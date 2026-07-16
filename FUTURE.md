# FUTURE — deferred, out of scope for this build

One-liners only. Each was consciously left out to keep the prototype honest and shippable.

- **Bridge exclusion.** PRD §5 wants bridge spans flagged/excluded via `nearest_bridge_name`; not
  implemented (a robust filter needs a bridge-*distance* field we don't fetch). `nearest_bridge_name`
  is already fetched as the intended input. Today bridge segments are scored like any other road.
- **HPMS spot check.** PRD §6 stage-4 item 2 — rank-correlate Subgrade scores against published IRI
  on the county's federal-aid roads. The LTPP calibration test (the stronger evidence) is done; HPMS
  is the lighter follow-up.
- **NOAA precipitation + freeze-thaw integration.** The C (climate) factor uses only Mireye's
  snow/hot-days/temp; the PRD's NOAA precip + freeze-thaw gap-fill is not wired in (and climate is
  near-constant within one county anyway).
- **Truck-share uplift in the T factor.** Dropped in Session 2 as a scoring input; T is AADT
  log-normalized with a housing-density fallback. Re-join VDOT truck-percent fields to add it.
- **Weight calibration.** PRD stretch — fit the five factor weights by regression against LTPP
  deterioration instead of literature weights. V1 ships with literature weights.
- **Scale beyond one corridor.** This build is scoped to the Leesburg+Ashburn corridor; the point-API
  cost (≈1 call/point, §6 of the README) is the barrier to county- and state-scale coverage — the
  reason a Mireye polyline endpoint is the headline ask.
- **Copilot via Mireye MCP.** The county copilot uses Mireye's `/v1/ask` directly; wiring it to the
  hosted MCP server (`uvx mireye-mcp`) would exercise Mireye's third surface.
