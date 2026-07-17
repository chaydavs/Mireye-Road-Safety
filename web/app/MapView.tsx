"use client";

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

// Free, no-API-key raster basemap (CARTO Positron). MapLibre needs a style object.
const BASE_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    carto: {
      type: "raster",
      tiles: [
        "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
        "https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
      ],
      tileSize: 256,
      attribution: "© OpenStreetMap © CARTO",
    },
  },
  layers: [{ id: "carto", type: "raster", source: "carto" }],
};

type Props = {
  data: GeoJSON.FeatureCollection;
  liveMode: boolean;
  watched: number[];
  selectedId: number | null;
  onSelect: (id: number) => void;
};

export default function MapView({ data, liveMode, watched, selectedId, onSelect }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const ready = useRef(false);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const selectedIdRef = useRef(selectedId);
  selectedIdRef.current = selectedId;
  const prevSel = useRef<number | null>(null);

  // static risk color (relative percentile ramp, precomputed per feature)
  const staticColor: maplibregl.ExpressionSpecification = ["get", "color"];

  useEffect(() => {
    if (!ref.current || map.current) return;
    const m = new maplibregl.Map({ container: ref.current, style: BASE_STYLE, center: [-77.5, 39.05], zoom: 10 });
    map.current = m;
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");

    m.on("load", () => {
      m.addSource("segments", { type: "geojson", data, promoteId: "segment_id" });
      m.addLayer({
        id: "segments-line",
        type: "line",
        source: "segments",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": staticColor,
          "line-width": ["case", ["boolean", ["feature-state", "sel"], false], 6, 3.2],
          "line-opacity": 0.9,
        },
      });

      // fit to data bounds
      const b = new maplibregl.LngLatBounds();
      for (const f of data.features) {
        const g = f.geometry;
        const coords = g.type === "LineString" ? g.coordinates : g.type === "MultiLineString" ? g.coordinates.flat() : [];
        for (const c of coords as [number, number][]) b.extend(c);
      }
      if (!b.isEmpty()) m.fitBounds(b, { padding: 30, duration: 0 });

      m.on("click", "segments-line", (e) => {
        const f = e.features?.[0];
        if (f) onSelectRef.current(f.properties!.segment_id as number);
      });
      m.on("mouseenter", "segments-line", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "segments-line", () => (m.getCanvas().style.cursor = ""));
      ready.current = true;
      // a default selection set before 'load' fires won't have been highlighted yet — apply it now
      if (selectedIdRef.current != null) {
        m.setFeatureState({ source: "segments", id: selectedIdRef.current }, { sel: true });
        prevSel.current = selectedIdRef.current;
      }
    });

    return () => {
      m.remove();
      map.current = null;
      ready.current = false;
    };
  }, [data]);

  // recolor on live-mode / watched changes
  useEffect(() => {
    const m = map.current;
    if (!m || !ready.current || !m.getLayer("segments-line")) return;
    if (liveMode) {
      const set = watched.length ? watched : [-1];
      m.setPaintProperty("segments-line", "line-color", [
        "case",
        ["in", ["get", "segment_id"], ["literal", set]],
        "#d7191c",
        "#c7d0d8",
      ] as maplibregl.ExpressionSpecification);
      m.setPaintProperty("segments-line", "line-opacity", [
        "case",
        ["in", ["get", "segment_id"], ["literal", set]],
        0.95,
        0.35,
      ] as maplibregl.ExpressionSpecification);
    } else {
      m.setPaintProperty("segments-line", "line-color", staticColor);
      m.setPaintProperty("segments-line", "line-opacity", 0.9);
    }
  }, [liveMode, watched]);

  // highlight selection via feature-state
  useEffect(() => {
    const m = map.current;
    if (!m || !ready.current) return;
    if (prevSel.current != null) m.setFeatureState({ source: "segments", id: prevSel.current }, { sel: false });
    if (selectedId != null) m.setFeatureState({ source: "segments", id: selectedId }, { sel: true });
    prevSel.current = selectedId;
  }, [selectedId]);

  return <div id="map" ref={ref} />;
}
