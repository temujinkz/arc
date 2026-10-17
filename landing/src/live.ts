import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { profileFromGraph, type ProfileData, type RawGraph } from "./profileData";

// Talks to the arc-engine backend: POST /api/investigations starts a run, and
// GET /api/investigate/stream/{id} streams its events (server-sent events).

export type LivePrompt = { request_id: string; question: string; candidates: { label: string; description?: string }[]; multi_select: boolean };
export type LiveStatus = "starting" | "running" | "complete" | "failed" | "cancelled";
export type LivePerson = {
  key: string;      // one per run
  id: string;       // one per name
  fresh: boolean;   // started in this visit, so a failure is worth showing
  name: string;
  status: LiveStatus;
  note: string;
  signals: number;
  prompts: LivePrompt[];
  profile: ProfileData | null;
  commit: RawGraph | null;
  error: string;
  sessionId: string;
};

const API = ((import.meta.env.VITE_API_URL as string | undefined) ?? "").replace(/\/$/, "");
const DOWN = "Could not reach the Arc backend. Start it on port 8010 and try again.";

async function call(path: string, body?: unknown) {
  let res: Response;
  try {
    res = await fetch(API + path, body === undefined ? undefined : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
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

export const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
export const liveId = (name: string) => "live-" + slug(name);

// Several choices go as a list only when the backend asked a multi-choice question (it accepts at most four).
// Otherwise they go as a plain written answer, which the backend always accepts.
export function answerBody(prompt: Pick<LivePrompt, "request_id" | "multi_select">, labels: string[], note = "") {
  const asList = labels.length === 1 || (prompt.multi_select && labels.length <= 4);
  if (labels.length && asList) return { request_id: prompt.request_id, selected: labels, text: "" };
  return { request_id: prompt.request_id, selected: [] as string[], text: labels.length ? `All of these are the same person: ${labels.join("; ")}.` : note.trim() };
}

const blank = (id: string, name: string, sessionId = "", fresh = true): LivePerson => ({ key: crypto.randomUUID(), fresh, id, name, status: "starting", note: "Waking up the backend", signals: 0, prompts: [], profile: null, commit: null, error: "", sessionId });

export function useLive() {
  const [runs, setRuns] = useState<LivePerson[]>([]);
  const streams = useRef(new Map<string, EventSource>());

  const patch = useCallback((key: string, f: (p: LivePerson) => Partial<LivePerson>) =>
    setRuns((rs) => rs.map((p) => (p.key === key ? { ...p, ...f(p) } : p))), []);

  const connect = useCallback((key: string, sessionId: string) => {
    streams.current.get(key)?.close();
    const es = new EventSource(`${API}/api/investigate/stream/${encodeURIComponent(sessionId)}`);
    streams.current.set(key, es);
    es.onmessage = (m) => {
      let e: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
      try { e = JSON.parse(m.data); } catch { return; }
      const type = String(e.type);
      switch (type) {
        case "stream.end":
          es.close(); streams.current.delete(key);
          patch(key, (p) => (p.status === "running" || p.status === "starting" ? { status: "failed", prompts: [], error: p.error || "This search was interrupted before it finished." } : { prompts: [] }));
          break;
        case "investigation.started": patch(key, () => ({ status: "running", note: "Starting the search" })); break;
        case "investigation.collecting": patch(key, () => ({ status: "running", note: "Looking through public sources" })); break;
        case "tool.started": patch(key, () => ({ note: e.tool ? `Checking ${String(e.tool).split("__").pop()?.replace(/_/g, " ")}` : "Checking a source" })); break;
        case "graph.draft_node": patch(key, (p) => ({ signals: p.signals + 1, note: `Found ${p.signals + 1} ${p.signals === 0 ? "signal" : "signals"} so far` })); break;
        case "agent.status": if (e.text) patch(key, () => ({ note: String(e.text).slice(0, 140) })); break;
        case "task.context": patch(key, () => ({ note: "A worker is reading the saved sources" })); break;
        case "hypothesis.updated": patch(key, () => ({ note: e.statement ? `Checked a claim: ${String(e.statement).slice(0, 80)}` : "Checked a claim" })); break;
        case "critic.reviewed": patch(key, () => ({ note: "Reviewing what the workers found" })); break;
        case "graph.research_preview": patch(key, () => ({ note: "Drafting the graph" })); break;
        case "human.interrupt":
          if (e.request_id && e.question) patch(key, (p) => ({ note: "Needs a quick answer from you", prompts: [...p.prompts.filter((x) => x.request_id !== e.request_id), { request_id: e.request_id, question: e.question, candidates: e.candidates ?? [], multi_select: !!e.multi_select }] }));
          break;
        case "human.resolved": patch(key, (p) => ({ note: "Back to work", prompts: p.prompts.filter((x) => x.request_id !== e.request_id) })); break;
        case "graph.final_commit": if (e.graph) patch(key, () => ({ commit: e.graph as RawGraph, profile: profileFromGraph(e.graph as RawGraph), note: "Saving the graph" })); break;
        case "investigation.complete": patch(key, () => ({ status: "complete", note: "Done", prompts: [] })); break;
        case "investigation.failed": patch(key, () => ({ status: "failed", prompts: [], error: String(e.text || `The search failed (${e.error || "unknown error"}).`) })); break;
        case "investigation.cancelled": patch(key, () => ({ status: "cancelled", prompts: [], note: "Stopped" })); break;
      }
    };
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) patch(key, (p) => (p.status === "complete" || p.status === "cancelled" ? {} : { status: "failed", error: "Lost the connection to the backend." }));
    };
  }, [patch]);

  // On load, bring back every run the backend remembers (newest 20). The backend keeps them all, so a reload loses nothing.
  useEffect(() => {
    let cancelled = false;
    const open = streams.current;
    (async () => {
      try {
        const data = (await call("/api/investigations")) as { investigations?: { session_id: string; target: string; created_at?: number }[] };
        if (cancelled) return;
        const list = [...(data.investigations ?? [])].sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0)).slice(0, 20);
        const made = list.map((r) => ({ ...blank(liveId(r.target), r.target, r.session_id, false), status: "running" as LiveStatus, note: "Loading" }));
        setRuns((rs) => [...rs, ...made]);
        made.forEach((m) => connect(m.key, m.sessionId));
      } catch { /* the backend may be off; the record still works */ }
    })();
    return () => { cancelled = true; open.forEach((s) => s.close()); };
  }, [connect]);

  // One entry per name. A search that is running wins; then a failure from this visit (so you see why);
  // then a finished run, so an old good result is never hidden by a newer failed attempt after a reload.
  const people = useMemo(() => {
    const best = new Map<string, LivePerson>();
    const rank = (p: LivePerson) => (p.status === "starting" || p.status === "running" ? 3 : p.fresh ? 2 : p.status === "complete" ? 1 : 0);
    for (const p of runs) {
      const have = best.get(p.id);
      if (!have || rank(p) > rank(have) || (rank(p) === rank(have) && p.status === "complete" && have.status !== "complete")) best.set(p.id, p);
    }
    return [...best.values()];
  }, [runs]);

  // Starts a real research run. Each run spends model and source credits on the backend.
  const start = useCallback(async (name: string) => {
    const id = liveId(name);
    const run = blank(id, name);
    setRuns((rs) => [...rs, run]);
    try {
      const res = await call("/api/investigations", { target: name, request_id: crypto.randomUUID() });
      const sessionId = String(res.session_id);
      patch(run.key, () => ({ sessionId, status: "running", note: "Starting the search" }));
      connect(run.key, sessionId);
    } catch (error) {
      patch(run.key, () => ({ status: "failed", error: error instanceof Error ? error.message : String(error) }));
    }
    return id;
  }, [connect, patch]);

  const answer = useCallback(async (p: LivePerson, prompt: LivePrompt, labels: string[], note = "") => {
    const body = answerBody(prompt, labels, note);
    try { await call(`/api/investigate/${encodeURIComponent(p.sessionId)}/human-reply`, body); }
    catch (error) { patch(p.key, () => ({ error: error instanceof Error ? error.message : String(error) })); }
  }, [patch]);

  const stop = useCallback(async (p: LivePerson) => {
    try { await call(`/api/investigate/${encodeURIComponent(p.sessionId)}/cancel`, {}); } catch { /* the stream reports the outcome */ }
    patch(p.key, () => ({ note: "Stopping" }));
  }, [patch]);

  return { people, start, answer, stop };
}
