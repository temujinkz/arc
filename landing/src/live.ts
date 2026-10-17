import { useCallback, useEffect, useRef, useState } from "react";

// Talks to the arc-engine backend: POST /api/investigations starts a run, and
// GET /api/investigate/stream/{id} streams its events (server-sent events).

export type LivePrompt = { request_id: string; question: string; candidates: { label: string; description?: string }[]; multi_select: boolean };
export type LiveStatus = "starting" | "running" | "complete" | "failed" | "cancelled";
export type LivePerson = {
  id: string;
  name: string;
  status: LiveStatus;
  note: string;
  signals: number;
  prompts: LivePrompt[];
  summary: string;
  themes: string[];
  error: string;
  sessionId: string;
};

const API = ((import.meta.env.VITE_API_URL as string | undefined) ?? "").replace(/\/$/, "");
const DOWN = "Could not reach the Arc backend. Start it on port 8010 and try again.";

async function post(path: string, body: unknown) {
  let res: Response;
  try {
    res = await fetch(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch { throw new Error(DOWN); }
  let data: { detail?: unknown } | null = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error(!data ? DOWN : typeof data.detail === "string" ? data.detail : `The backend answered ${res.status}.`);
  return data as Record<string, unknown>;
}

// Full names only, so a search never turns into just a surname. "connor  guerrero" becomes "Connor Guerrero".
export function fullName(raw: string): string | null {
  const words = raw.trim().split(/\s+/).filter(Boolean);
  if (words.length < 2) return null;
  const fix = (w: string) => (w === w.toLowerCase() || w === w.toUpperCase() ? w[0].toUpperCase() + w.slice(1).toLowerCase() : w);
  return words.map(fix).join(" ");
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

export function useLive() {
  const [people, setPeople] = useState<LivePerson[]>([]);
  const streams = useRef(new Map<string, EventSource>());

  const patch = useCallback((id: string, f: (p: LivePerson) => Partial<LivePerson>) =>
    setPeople((ps) => ps.map((p) => (p.id === id ? { ...p, ...f(p) } : p))), []);

  useEffect(() => { const s = streams.current; return () => s.forEach((e) => e.close()); }, []);

  const connect = useCallback((id: string, sessionId: string) => {
    const es = new EventSource(`${API}/api/investigate/stream/${encodeURIComponent(sessionId)}`);
    streams.current.set(id, es);
    es.onmessage = (m) => {
      let e: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
      try { e = JSON.parse(m.data); } catch { return; }
      switch (e.type) {
        case "stream.end": es.close(); streams.current.delete(id); break;
        case "investigation.started": patch(id, () => ({ status: "running", note: "Starting the search" })); break;
        case "investigation.collecting": patch(id, () => ({ status: "running", note: "Looking through public sources" })); break;
        case "tool.started": patch(id, () => ({ note: e.tool ? `Checking ${String(e.tool).split("__").pop()?.replace(/_/g, " ")}` : "Checking a source" })); break;
        case "graph.draft_node": patch(id, (p) => ({ signals: p.signals + 1, note: `Found ${p.signals + 1} ${p.signals === 0 ? "signal" : "signals"} so far` })); break;
        case "human.interrupt":
          if (e.request_id && e.question) patch(id, (p) => ({ note: "Needs a quick answer", prompts: [...p.prompts.filter((x) => x.request_id !== e.request_id), { request_id: e.request_id, question: e.question, candidates: e.candidates ?? [], multi_select: !!e.multi_select }] }));
          break;
        case "human.resolved": patch(id, (p) => ({ note: "Back to work", prompts: p.prompts.filter((x) => x.request_id !== e.request_id) })); break;
        case "graph.final_commit": {
          const g = e.graph;
          if (!g) break;
          const person = g.nodes?.find((n: { label: string }) => n.label === "Person");
          const themes = (g.nodes ?? []).filter((n: { label: string }) => n.label === "DomainPillar").map((n: { title: string }) => n.title).slice(0, 5);
          patch(id, () => ({ summary: g.summary || person?.summary || "", themes, note: "Saving the graph" }));
          break;
        }
        case "investigation.complete": patch(id, () => ({ status: "complete", note: "Done", prompts: [] })); break;
        case "investigation.failed": patch(id, () => ({ status: "failed", prompts: [], error: `The search failed (${e.error || "unknown error"}).` })); break;
        case "investigation.cancelled": patch(id, () => ({ status: "cancelled", prompts: [], note: "Stopped" })); break;
      }
    };
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) patch(id, (p) => (p.status === "complete" || p.status === "cancelled" ? {} : { status: "failed", error: "Lost the connection to the backend." }));
    };
  }, [patch]);

  // Starts a real research run. Each run spends model and source credits on the backend.
  const start = useCallback(async (name: string, existingId?: string) => {
    const id = existingId ?? "live-" + slug(name);
    setPeople((ps) => ps.some((p) => p.id === id)
      ? ps.map((p) => (p.id === id ? { ...p, status: "starting", note: "Waking up the backend", error: "", signals: 0, prompts: [] } : p))
      : [...ps, { id, name, status: "starting", note: "Waking up the backend", signals: 0, prompts: [], summary: "", themes: [], error: "", sessionId: "" }]);
    try {
      const res = await post("/api/investigations", { target: name, request_id: crypto.randomUUID() });
      const sessionId = String(res.session_id);
      patch(id, () => ({ sessionId, status: "running", note: "Starting the search" }));
      connect(id, sessionId);
    } catch (error) {
      patch(id, () => ({ status: "failed", error: error instanceof Error ? error.message : String(error) }));
    }
    return id;
  }, [connect, patch]);

  const answer = useCallback(async (id: string, sessionId: string, requestId: string, selected: string[]) => {
    try { await post(`/api/investigate/${encodeURIComponent(sessionId)}/human-reply`, { request_id: requestId, selected, text: "" }); }
    catch (error) { patch(id, () => ({ error: error instanceof Error ? error.message : String(error) })); }
  }, [patch]);

  const stop = useCallback(async (id: string, sessionId: string) => {
    try { await post(`/api/investigate/${encodeURIComponent(sessionId)}/cancel`, {}); } catch { /* the stream reports the outcome */ }
    patch(id, () => ({ note: "Stopping" }));
  }, [patch]);

  return { people, start, answer, stop };
}
