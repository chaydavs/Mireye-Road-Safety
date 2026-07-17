"""Honest attribution of each why-card DECISION to its data-origin groups.

We weight by ACTUAL CONTRIBUTION (factor_weight × normalized_component_value — the same quantity that
ranks the drivers), never by counting fields. Field-counting is circular: it just re-measures which
fields we chose to fetch, so a source looks important merely because we pulled many of its fields.
Contribution measures how much each input actually moved the score.

Groups (shares sum to 1.0):
  - "Mireye"        every field served through Mireye /v1/fetch (soil, bedrock, flood, landslide,
                    climate, the census housing proxy, …) — plus the Mireye-derived rate inside RSL.
  - "VDOT traffic"  traffic_aadt (the one score input fetched outside Mireye).
  - "Local records" HPMS/VDOT treatment year, ONLY when it fed this segment's RSL (an estimated basis).
  - "Live stress"   NWS/USGS, ONLY when the Right-now view is active and the segment is watched.

This is "share of this decision's inputs" — attribution, not a data-quality or accuracy claim.

The risk score is the primary decision (weight 1.0) and its Mireye-vs-traffic split is EXACT by
contribution. The RSL timing and the live-stress overlay are secondary decision dimensions with no
contribution in score units, so each is credited a stated presentation weight (RSL_WEIGHT / LIVE_WEIGHT)
relative to the score's 1.0; within the RSL dimension, the record-vs-Mireye split is the real
consumed-life fraction (age / predicted service life). These two constants are the only chosen weights;
everything else is measured.
"""

from __future__ import annotations

RSL_WEIGHT = 0.15   # RSL timing is a secondary dimension, credited 0.15 against the score's 1.0
LIVE_WEIGHT = 0.15  # a watched segment's live-stress overlay, same footing as the RSL dimension


def static_weights(mireye_share: float, rsl_estimated: bool, last_treated: float | None,
                   rsl_mid: float | None, current_year: int) -> dict:
    """Un-normalized group weights that do NOT depend on the live toggle (Mireye / VDOT traffic /
    Local records). The caller normalizes (optionally after adding the live dimension)."""
    w = {"Mireye": mireye_share, "VDOT traffic": max(0.0, 1.0 - mireye_share)}
    if rsl_estimated and last_treated and rsl_mid:
        age = max(0.0, current_year - last_treated)
        predicted_life = max(1.0, rsl_mid - last_treated)   # treated -> predicted poor-condition year
        records = min(1.0, age / predicted_life)            # how much of the life the RECORD says is spent
        w["Local records"] = records * RSL_WEIGHT
        w["Mireye"] += (1.0 - records) * RSL_WEIGHT         # the RSL rate is Mireye-derived (the score)
    return w


def normalize(weights: dict) -> dict:
    total = sum(weights.values()) or 1.0
    return {k: round(v / total, 3) for k, v in weights.items() if v > 0}
