"""Session 6/7 audit-narrator logic test — the compact_audit filter (no LLM, no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "agents"))

import audit_narrator  # noqa: E402


def test_compact_audit_drops_status_dump_keeps_ltpp():
    audit = {
        "scope": "town",
        "total_coordinate_fetches": 7870,
        "status_distribution_per_field": {"soil_drainage_class": {"present": 1}},  # bulky -> dropped
        "null_rate_per_field": {"soil_drainage_class": 0.1},
        "ltpp_validation": {"n_sections": 51, "permutation_p_value": 0.26},
    }
    compact = audit_narrator.compact_audit(audit)
    assert "status_distribution_per_field" not in compact   # bulky dump removed
    assert compact["ltpp_validation"]["n_sections"] == 51   # LTPP result preserved
    assert compact["null_rate_per_field"] == audit["null_rate_per_field"]
    assert compact["total_coordinate_fetches"] == 7870
