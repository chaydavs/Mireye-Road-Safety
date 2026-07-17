#!/usr/bin/env bash
# One-command demo launch: snapshot the live layer, then open the app (offline-safe) in under a minute.
# Assumes a clean checkout PLUS the cached data/ directory (scores, cache, map, why-cards).
set -euo pipefail
cd "$(dirname "$0")"

VENV=.venv
if [ ! -x "$VENV/bin/streamlit" ]; then
  echo "No usable .venv. Create it once with:"
  echo "  uv venv --python 3.11 .venv && uv pip install geopandas shapely pyproj pandas pyarrow httpx folium streamlit streamlit-folium anthropic matplotlib"
  exit 1
fi

if [ ! -f data/scores.parquet ]; then
  echo "data/scores.parquet missing — this launch expects the cached data/ directory to be present."
  echo "Regenerate with: .venv/bin/python src/network.py && src/fetch.py && src/score.py && src/paving.py && src/service_life.py"
  exit 1
fi

# 1. Snapshot the live layer (needs network; degrades to the existing snapshot if offline).
"$VENV/bin/python" src/demo.py || echo "demo prep had warnings; continuing with existing snapshot."

# 2. Launch the app in demo mode (reads the offline snapshot; survives airplane mode).
echo "Opening the app at http://localhost:8501 …"
exec "$VENV/bin/streamlit" run src/app.py -- --demo
