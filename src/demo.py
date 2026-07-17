"""Demo prep: pre-warm every cache and snapshot the live layer to data/demo_snapshot/ so the
walkthrough survives airplane mode. Run this once (with network) before the demo; then launch the
app with `--demo` and it reads the snapshot, auto-falling back if a live API fails mid-demo.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))
import live  # noqa: E402

REPO = SRC.parent
DATA = REPO / "data"
SNAP = DATA / "demo_snapshot"
# Artifacts the offline app actually reads (verified here so a missing one fails loud, not at demo time).
STATIC_REQUIRED = ["scores.parquet", "segments.parquet", "cache.sqlite", "why_cards.json",
                   "audit.json"]
# The network-dependent live layer — these get snapshotted.
LIVE_ARTIFACTS = [live.WATCHLIST, live.WATCH_META]


def main() -> int:
    SNAP.mkdir(parents=True, exist_ok=True)

    # 1. Refresh the live layer with network; if it fails, keep whatever watchlist already exists.
    try:
        df, meta = live.build_watchlist()
        df.to_parquet(live.WATCHLIST)
        live.WATCH_META.write_text(json.dumps(meta, indent=2))
        print(f"live layer refreshed: {'calm (no active stress)' if meta['calm'] else str(meta['watched_segments']) + ' watched'}")
    except Exception as exc:  # noqa: BLE001
        print(f"live refresh failed ({exc}); snapshotting the existing watchlist instead.")

    # 2. Snapshot the live-layer artifacts (the only network-dependent pieces).
    snapped = []
    for f in LIVE_ARTIFACTS:
        if f.exists():
            shutil.copy(f, SNAP / f.name)
            snapped.append(f.name)

    # 3. Verify the offline-capable static artifacts are present.
    missing = [s for s in STATIC_REQUIRED if not (DATA / s).exists()]
    print(f"snapshotted live artifacts: {snapped or 'none'} -> {SNAP}")
    if missing:
        print(f"WARNING: missing static artifacts (run the pipeline first): {missing}")
        return 1
    print("Demo ready. Offline-safe launch:  streamlit run src/app.py -- --demo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
