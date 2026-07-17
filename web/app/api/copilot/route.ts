import Anthropic from "@anthropic-ai/sdk";
import type { SegmentProps } from "../../types";

export const runtime = "nodejs";
export const maxDuration = 60;

const MODEL = "claude-sonnet-5";

const SYSTEM =
  "You are the Subgrade county copilot for a public-works engineer. Subgrade ranks road segments " +
  "by ground-driven deterioration RISK (not current condition, not safety). Answer ONLY from the " +
  "query_scores tool (scored segments + cited provenance + a remaining-service-life estimate). Every " +
  "factual claim must come from a tool result, with its source. " +
  "WHEN a road will reach poor condition: give the estimated YEAR RANGE from query_scores (rsl.low-" +
  "rsl.high) and its basis (rsl.basis: hpms/vdot/prior). If rsl.estimated is false (prior basis, no " +
  "treatment year), say there is no treatment-year data so RSL is not estimated for that segment — do " +
  "NOT invent a range. NEVER give a single exact year or date; if pushed, refuse and explain it is a " +
  "transparent screening estimate, not a prediction. Never invent soil types, scores, or forecasts. Be concise.";

const TOOLS: Anthropic.Tool[] = [
  {
    name: "query_scores",
    description:
      "Query the scored Subgrade segments and their cited provenance. Use for segment risk scores, " +
      "grades, rankings, drivers, RSL, and comparisons.",
    input_schema: {
      type: "object",
      properties: {
        action: { type: "string", enum: ["top", "segment", "route"], description: "top N by score, one segment by id, or segments on a route" },
        segment_id: { type: "integer" },
        n: { type: "integer", description: "how many for action=top (default 5)" },
        route_name: { type: "string" },
      },
      required: ["action"],
    },
  },
];

// Cache the slim scored records across warm invocations.
let cache: SegmentProps[] | null = null;
async function loadScores(origin: string): Promise<SegmentProps[]> {
  if (cache) return cache;
  const res = await fetch(`${origin}/data/scores.json`);
  if (!res.ok) throw new Error(`could not load scores.json (${res.status})`);
  cache = (await res.json()) as SegmentProps[];
  return cache;
}

function summary(p: SegmentProps) {
  return {
    segment_id: p.segment_id,
    route_name: p.route_name,
    score: p.score,
    grade: p.grade,
    top_drivers: p.drivers.map((d) => ({ factor: d.label, value: d.value, contribution: d.contribution, source: d.source })),
    rsl: p.rsl, // estimated, basis, low, high, last_treated, text
  };
}

function queryScores(scores: SegmentProps[], input: Record<string, unknown>): unknown {
  const action = input.action as string;
  if (action === "top") {
    const n = (input.n as number) || 5;
    return [...scores].sort((a, b) => b.score - a.score).slice(0, n).map(summary);
  }
  if (action === "segment") {
    const p = scores.find((s) => s.segment_id === input.segment_id);
    return p ? summary(p) : { error: `segment ${input.segment_id} is not in the scored dataset` };
  }
  if (action === "route") {
    const q = String(input.route_name ?? "").toLowerCase();
    return scores.filter((s) => (s.route_name ?? "").toLowerCase().includes(q)).slice(0, 20).map(summary);
  }
  return { error: `unknown action ${action}` };
}

export async function POST(req: Request) {
  let question: string;
  try {
    ({ question } = await req.json());
  } catch {
    return Response.json({ error: "bad request body" }, { status: 400 });
  }
  if (!question?.trim()) return Response.json({ error: "empty question" }, { status: 400 });

  const key = process.env.ANTHROPIC_API_KEY;
  if (!key) return Response.json({ error: "ANTHROPIC_API_KEY is not set on the server" }, { status: 500 });

  let scores: SegmentProps[];
  try {
    scores = await loadScores(new URL(req.url).origin);
  } catch (e) {
    return Response.json({ error: (e as Error).message }, { status: 500 });
  }

  const client = new Anthropic({ apiKey: key });
  const messages: Anthropic.MessageParam[] = [{ role: "user", content: question }];
  const transcript: { tool: string; input: unknown }[] = [];

  try {
    for (let i = 0; i < 6; i++) {
      const resp = await client.messages.create({ model: MODEL, max_tokens: 2000, system: SYSTEM, tools: TOOLS, messages });
      if (resp.stop_reason !== "tool_use") {
        const text = resp.content.filter((b) => b.type === "text").map((b) => (b as Anthropic.TextBlock).text).join("");
        return Response.json({ answer: text, transcript });
      }
      messages.push({ role: "assistant", content: resp.content });
      const results: Anthropic.ToolResultBlockParam[] = [];
      for (const block of resp.content) {
        if (block.type === "tool_use") {
          const result = block.name === "query_scores" ? queryScores(scores, block.input as Record<string, unknown>) : { error: "unknown tool" };
          transcript.push({ tool: block.name, input: block.input });
          results.push({ type: "tool_result", tool_use_id: block.id, content: JSON.stringify(result) });
        }
      }
      messages.push({ role: "user", content: results });
    }
    return Response.json({ answer: "(stopped: tool-use loop exceeded)", transcript });
  } catch (e) {
    return Response.json({ error: `copilot call failed: ${(e as Error).message}` }, { status: 502 });
  }
}
