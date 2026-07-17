"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import type { SegmentProps, Live, Trigger, Summary } from "./types";

const MapView = dynamic(() => import("./MapView"), { ssr: false });

type Msg = { role: "user" | "bot"; text: string; tools?: { tool: string; input: unknown }[] };

function ageOf(iso?: string | null): string {
  if (!iso) return "unknown";
  const secs = (Date.now() - new Date(iso).getTime()) / 1000;
  const mins = Math.floor(secs / 60);
  if (mins < 1) return "just now";
  if (mins < 120) return `${mins} min ago`;
  return `${Math.floor(mins / 60)} h ago`;
}

export default function Page() {
  const [data, setData] = useState<GeoJSON.FeatureCollection | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [liveMode, setLiveMode] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  useEffect(() => {
    fetch("/data/segments.geojson")
      .then((r) => {
        if (!r.ok) throw new Error(`could not load segments.geojson (HTTP ${r.status})`);
        return r.json();
      })
      .then(setData)
      .catch((e) => setLoadError(String(e.message || e)));
    fetch("/data/live.json").then((r) => r.json()).then(setLive).catch(() => setLive({ available: false }));
    fetch("/data/summary.json").then((r) => r.json()).then(setSummary).catch(() => setSummary(null));
  }, []);

  // lookup with the intact (non-stringified) properties, keyed by segment id
  const byId = useMemo(() => {
    const m = new Map<number, SegmentProps>();
    data?.features.forEach((f) => m.set(f.properties!.segment_id as number, f.properties as SegmentProps));
    return m;
  }, [data]);

  // default the panel to the highest-risk segment once data lands
  useEffect(() => {
    if (data && selectedId == null) {
      let top: SegmentProps | null = null;
      byId.forEach((p) => {
        if (!top || p.score > top.score) top = p;
      });
      if (top) setSelectedId((top as SegmentProps).segment_id);
    }
  }, [data, byId, selectedId]);

  const watched = live?.watched ?? [];
  const selected = selectedId != null ? byId.get(selectedId) ?? null : null;
  const selTriggers: Trigger[] = selectedId != null ? live?.triggers?.[String(selectedId)] ?? [] : [];

  return (
    <div className="app">
      <header>
        <h1>Subgrade</h1>
        <span className="sub">cited road-deterioration risk · Loudoun County, VA · {byId.size || "…"} local-road segments</span>
        <label className="toggle">
          <input type="checkbox" checked={liveMode} onChange={(e) => setLiveMode(e.target.checked)} />
          Right now (live stress)
        </label>
        <LiveStatus live={live} liveMode={liveMode} />
        {!liveMode && (
          <div className="legend" title="Relative ranking among local roads — not an absolute good/bad claim">
            <span>lower risk</span>
            <span className="ramp" aria-hidden />
            <span>higher risk</span>
            <span className="ramp-note">relative rank</span>
          </div>
        )}
      </header>

      <div className="main">
        {data ? (
          <MapView data={data} liveMode={liveMode} watched={watched} selectedId={selectedId} onSelect={setSelectedId} />
        ) : (
          <div id="map" style={{ display: "grid", placeItems: "center", color: loadError ? "#ff9ba0" : "#93a1b0", padding: 20, textAlign: "center" }}>
            {loadError ? `Failed to load the map data: ${loadError}. Re-run src/export_web.py.` : "loading the network…"}
          </div>
        )}

        <aside className="panel">
          {selected ? (
            <WhyCard seg={selected} liveMode={liveMode} triggers={selTriggers} />
          ) : (
            <p className="empty">Click a road to see its cited why-card.</p>
          )}
          {summary && <CountywideSummary s={summary} />}
          <Copilot />
        </aside>
      </div>
    </div>
  );
}

function LiveStatus({ live, liveMode }: { live: Live | null; liveMode: boolean }) {
  if (!liveMode) return null;
  if (!live?.available) return <span className="pill">live layer unavailable</span>;
  const checked = ageOf(live.generated_at);
  if (live.calm) {
    return (
      <span className="pill calm">
        ✅ Calm: {live.active_alerts} active VA alerts, 0 segments under alert; {live.elevated_gages} gauges elevated;
        wet week: {live.wet_week ? "yes" : "no"}. Checked {checked}.
      </span>
    );
  }
  return (
    <span className="pill stress">
      ⚠️ Live stress: {live.active_alerts} active VA alerts, {live.watched_segments} segments flagged;{" "}
      {live.elevated_gages} gauges elevated ({(live.gage_ids ?? []).join(", ") || "none"}); wet week:{" "}
      {live.wet_week ? "yes" : "no"}. Checked {checked}.
    </span>
  );
}

const GROUP_COLOR: Record<string, string> = {
  Mireye: "#4aa3ff",
  "VDOT traffic": "#6b7885",
  "Local records": "#d9a441",
  "Live stress": "#d7191c",
};
const LIVE_WEIGHT = 0.15; // matches RSL/LIVE weighting in src/attribution.py

function MireyeSplit({ seg, liveStress }: { seg: SegmentProps; liveStress: boolean }) {
  const raw: Record<string, number> = { ...seg.decision_weights };
  if (liveStress) raw["Live stress"] = (raw["Live stress"] ?? 0) + LIVE_WEIGHT;
  const total = Object.values(raw).reduce((a, b) => a + b, 0) || 1;
  const groups = Object.entries(raw)
    .map(([k, v]) => ({ k, pct: Math.round((v / total) * 100) }))
    .filter((g) => g.pct > 0)
    .sort((a, b) => b.pct - a.pct);
  return (
    <div
      className="mireye"
      title="Share of this decision's inputs by actual contribution (factor weight × normalized value) — attribution, not a data-quality claim"
    >
      <div className="section-title">Share of this decision&apos;s inputs</div>
      <div className="bar">
        {groups.map((g) => (
          <span key={g.k} className="fill" style={{ width: `${g.pct}%`, background: GROUP_COLOR[g.k] }} />
        ))}
      </div>
      <div className="bar-labels">
        {groups.map((g) => (
          <span key={g.k}>
            <span className="dot" style={{ background: GROUP_COLOR[g.k] }} /> {g.k} <strong>{g.pct}%</strong>
            {g.k === "Mireye" ? ` · ${seg.mireye_field_count} fields` : ""}
          </span>
        ))}
      </div>
    </div>
  );
}

function CountywideSummary({ s }: { s: Summary }) {
  const m = s.mireye_contribution;
  return (
    <div className="summary">
      <div className="section-title">How much of the decision does Mireye power?</div>
      <p>
        Across {s.segments.toLocaleString()} segments, Mireye-served data drives a median{" "}
        <strong>{Math.round(m.median * 100)}%</strong> of each risk decision (range {Math.round(m.min * 100)}–
        {Math.round(m.max * 100)}%), <em>weighted by actual contribution</em>.
      </p>
      <p className="muted">
        A naive field-count would report {Math.round(s.mireye_by_fieldcount_median * 100)}% — it overstates
        Mireye by counting fields we chose, not how much each moved the score.
      </p>
      <p>
        Most influential Mireye fields countywide:{" "}
        <strong>{s.top_mireye_fields.map((f) => f.field).join(", ")}</strong>. The only input Mireye doesn&apos;t
        carry — VDOT traffic — is a median {Math.round(s.non_mireye_median.vdot_traffic * 100)}%.
      </p>
    </div>
  );
}

function WhyCard({ seg, liveMode, triggers }: { seg: SegmentProps; liveMode: boolean; triggers: Trigger[] }) {
  return (
    <div className="card">
      <h2>{seg.route_name}</h2>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, margin: "4px 0 10px" }}>
        <span className="score" style={{ color: seg.color }}>
          {seg.score.toFixed(1)}
        </span>
        <span className="badge">grade {seg.grade}</span>
        <span className="badge">{seg.bucket}</span>
      </div>

      <MireyeSplit seg={seg} liveStress={liveMode && triggers.length > 0} />

      <div className={"rsl" + (seg.rsl.estimated ? "" : " none")}>⏳ {seg.rsl.text}</div>

      {liveMode && triggers.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div className="section-title">Under stress right now</div>
          {triggers.map((t, i) => (
            <div className="trigger" key={i}>
              {t.detail} · {ageOf(t.at)}{" "}
              {t.source_url ? (
                <a href={t.source_url} target="_blank" rel="noreferrer" style={{ color: "#ff9ba0" }}>
                  [{t.source}]
                </a>
              ) : (
                `(${t.source ?? ""})`
              )}
            </div>
          ))}
        </div>
      )}

      <div style={{ marginTop: 12 }}>
        <div className="section-title">Top drivers of this score — each cited to a federal source</div>
        <ul className="drivers">
          {seg.drivers.map((d, i) => (
            <li key={i}>
              <div>
                <strong>{d.label}</strong> <span className="val">= {String(d.value)}</span>
              </div>
              <a href={d.source_url} target="_blank" rel="noreferrer">
                {d.source}
                {d.fetched_at ? ` · ${d.fetched_at}` : ""} ↗
              </a>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function Copilot() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  // Block body (not a concise arrow): a concise arrow returns scrollIntoView()'s value, which React
  // then treats as this effect's cleanup — throwing "destroy is not a function" on the next run.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs, busy]);

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    const question = q.trim();
    if (!question || busy) return;
    setQ("");
    setMsgs((m) => [...m, { role: "user", text: question }]);
    setBusy(true);
    try {
      const res = await fetch("/api/copilot", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question }),
      });
      const j = await res.json();
      if (!res.ok) throw new Error(j.error || "copilot error");
      setMsgs((m) => [...m, { role: "bot", text: j.answer, tools: j.transcript }]);
    } catch (err) {
      setMsgs((m) => [...m, { role: "bot", text: `Copilot unavailable: ${(err as Error).message}` }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chat">
      <div className="section-title">County copilot — answers only from the scored data, cited</div>
      {msgs.map((m, i) => (
        <div key={i} className={"msg " + (m.role === "user" ? "user" : "bot")}>
          {m.tools?.map((t, j) => (
            <div className="tool" key={j}>
              tool: {t.tool}({JSON.stringify(t.input)})
            </div>
          ))}
          {m.text}
        </div>
      ))}
      {busy && <div className="msg bot">Thinking (querying the scored data)…</div>}
      <div ref={endRef} />
      <form onSubmit={ask}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="e.g. why is the top segment ranked first?"
        />
        <button disabled={busy}>Ask</button>
      </form>
    </div>
  );
}
