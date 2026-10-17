"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Terminal, Check, AlertTriangle, LoaderCircle, Pause, ChevronRight, ArrowUpRight, ArrowDown } from "lucide-react";
import type { ArcEvent, InvestigationState } from "@/lib/investigation";
import { activityLabel, provider, toolLabel, toolResults } from "@/lib/presentation";
import { AgentResponse } from "./agent-response";
import { HumanReview } from "./review-panel";

function ToolCard({ event, result, state, onEvidence }: {
  event: ArcEvent; result?: ArcEvent; state: InvestigationState; onEvidence: (id: string) => void;
}) {
  const pending = !result && ["starting", "running"].includes(state.status);
  const paused = pending && (state.prompts.length > 0 || state.connection === "reconnecting");
  const failed = result?.type === "tool.failed" || result?.evidence_status === "failed";
  const duration = result?.timestamp && event.timestamp ? Math.max(0, result.timestamp - event.timestamp) : null;
  const label = failed ? "Failed" : result?.source_cache?.hit ? "Reused" : result?.evidence_status === "captured" ? "Captured"
    : result?.evidence_status === "skipped" ? "Not captured" : result ? "Finished" : paused ? "Waiting" : pending ? "Running" : "Interrupted";
  return <details className={`tool-card group ${pending && !paused ? "tool-active" : ""}`}>
    <summary className="flex items-center gap-2.5 p-3.5">
      <ChevronRight size={12} className="shrink-0 text-zinc-500 transition-transform group-open:rotate-90" />
      <span className={`shrink-0 ${failed ? "text-rose-400" : paused ? "text-amber-400" : "text-emerald-400"}`}>
        {failed ? <AlertTriangle size={14} /> : paused ? <Pause size={14} /> : pending ? <LoaderCircle size={14} className="activity-spin" /> : result ? <Check size={14} /> : <Pause size={14} />}
      </span>
      <span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium text-zinc-200">{toolLabel(event.tool)}</span>
        <span className="mt-1 block truncate text-[10px] text-zinc-500">{provider(event.tool)} · {event.subagent === "operator" ? "Operator lookup" : event.subagent === "orchestrator" ? "Lead agent" : event.subagent ? "Harvester" : "Agent"}</span></span>
      <span className="shrink-0 text-right text-[10px] text-zinc-500"><span className={failed ? "text-rose-400" : pending ? "text-amber-300" : "text-zinc-400"}>{label}</span>
        {duration !== null && <span className="mt-1 block font-mono">{duration.toFixed(1)}s</span>}</span>
    </summary>
    <div className="space-y-3 border-t border-white/5 p-3.5 text-xs">
      <p className="break-all font-mono text-[10px] text-zinc-500">{event.tool} · {event.subagent}</p>
      <div><p className="eyebrow mb-2">Arguments</p><pre className="max-h-52 overflow-auto rounded-lg bg-black/30 p-3 text-[11px] text-zinc-400">{JSON.stringify(event.args, null, 2)}</pre></div>
      {result && <div><p className="eyebrow mb-2">{failed ? "Error" : "Output preview"}</p>
        {result.source_cache && <p className="mb-2 text-[11px] text-cyan-300">{result.source_cache.hit ? "Reused saved source" : "New source saved"} · Collected {result.source_cache.observed_at}</p>}
        <p className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-relaxed text-zinc-400">{result.error || result.preview || "Tool completed."}</p></div>}
      {result?.evidence_id && <button type="button" onClick={() => onEvidence(result.evidence_id!)} className="inline-flex items-center gap-1 text-xs text-emerald-300 hover:underline">Inspect captured output <ArrowUpRight size={12} /></button>}
    </div>
  </details>;
}

export function ToolStream({ state, visible, onEvidence, answer }: { state: InvestigationState; visible: boolean; onEvidence: (id: string) => void; answer: (id: string, selected: string[], text: string) => Promise<void> }) {
  const { logs } = state;
  const panel = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const readingPosition = useRef(0);
  const [following, setFollowing] = useState(true);
  const [now, setNow] = useState(0);
  const active = state.status === "starting" || state.status === "running";
  const paused = state.prompts.length > 0 || state.connection === "reconnecting";
  const results = useMemo(() => toolResults(logs), [logs]);
  const calls = logs.filter((event) => event.type === "tool.started");
  const start = logs.find((event) => event.type === "investigation.started")?.timestamp;
  const end = [...logs].reverse().find((event) => ["investigation.complete", "investigation.failed", "investigation.cancelled"].includes(event.type))?.timestamp;
  const elapsed = start ? Math.max(0, Math.floor((end ?? (active && now ? now : start)) - start)) : 0;
  useEffect(() => {
    if (!active || !visible) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [active, visible]);
  useEffect(() => {
    if (!visible || !panel.current) return;
    panel.current.scrollTop = follow.current ? panel.current.scrollHeight : readingPosition.current;
  }, [logs, visible]);
  return <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Live agent activity">
    {logs.length > 0 && <div className="mx-auto flex w-full max-w-4xl shrink-0 items-center gap-2 px-5 py-2 text-[10px] text-zinc-600 sm:px-8"><span>{state.phase === "conversation" ? "Evidence conversation" : "Research activity"}</span><span className="ml-auto">{calls.length} tool calls · {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}</span></div>}
    <div ref={panel} onScroll={() => { const el = panel.current; if (el && visible) { readingPosition.current = el.scrollTop; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48; setFollowing(follow.current); } }}
      className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl space-y-3 px-5 py-4 sm:px-8 sm:py-6">
      {!logs.length && <div className="py-20 text-center"><Terminal size={28} className="mx-auto mb-4 text-zinc-700" />
        <p className="text-2xl font-medium tracking-tight text-zinc-200">Who would you like to understand?</p>
        <p className="mx-auto mt-4 max-w-sm text-sm leading-relaxed text-zinc-500">Start with a name and context, or a public profile. We’ll follow the sources together.</p></div>}
      {logs.map((event, index) => {
        if (event.type === "case.ready" || event.type === "source.started" || event.type === "source.complete") return <p key={index} className="py-2 text-sm text-zinc-400">{event.text}</p>;
        if (event.type === "source.failed" || event.type === "source.cancelled") return <p key={index} role="alert" className="py-2 text-sm text-amber-300">{event.error}</p>;
        if (event.type === "investigation.started" || event.type === "user.message") return <div key={index} className="flex justify-end py-4"><p className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-tr-sm bg-white/6 px-4 py-3 text-sm leading-relaxed text-zinc-200">{event.text || event.target}</p></div>;
        if (event.type === "conversation.failed" || event.type === "conversation.cancelled") return <p key={index} role="alert" className="py-2 text-xs text-amber-300">{event.error || event.text}</p>;
        if (event.type === "tool.started") return <ToolCard key={event.tool_use_id || index} event={event} result={results.get(event.tool_use_id || "")} state={state} onEvidence={onEvidence} />;
        if (["evidence.skipped", "investigation.failed"].includes(event.type)) return <div key={index} className="rounded-lg border border-rose-900/60 bg-rose-950/15 p-3 text-xs text-rose-300"><p className="flex items-center gap-2"><AlertTriangle size={13} />{toolLabel(event.tool) || "Investigation"}</p><p className="mt-2 leading-relaxed">{event.error || event.reason}</p></div>;
        if (event.type === "agent.text") return <AgentResponse key={index} text={event.text || ""}
          label={event.subagent && event.subagent !== "orchestrator" ? "Source harvester" : "ARC"}
          streaming={active && !paused && index === logs.length - 1} />;
        if (event.type === "agent.architect_decision") return <AgentResponse key={index} text={event.summary || ""} label="Graph assessment" />;
        if (event.type === "human.interrupt") return <p key={index} className="border-l border-amber-500/40 py-1 pl-3 text-xs leading-relaxed text-amber-300">Identity clarification requested</p>;
        if (event.type === "human.resolved") return <p key={index} className="pl-3 text-[11px] text-zinc-500">Operator answer received</p>;
        if (event.type === "agent.status") return <p key={index} className="py-2 text-xs text-violet-300">{event.text}</p>;
        if (event.type === "investigation.complete") return <p key={index} className="flex items-center gap-2 py-3 text-xs text-emerald-300"><Check size={14} />Investigation complete</p>;
        if (event.type === "investigation.cancelled") return <p key={index} className="text-xs text-amber-300">Investigation stopped. Remote provider jobs may still finish.</p>;
        return null;
      })}
      <HumanReview state={state} answer={answer} />
      </div>
    </div>
    {!following && <div className="flex shrink-0 justify-center py-2"><button type="button"
      onClick={() => { follow.current = true; setFollowing(true); if (panel.current) panel.current.scrollTop = panel.current.scrollHeight; }}
      className="flex items-center gap-2 rounded-full border border-white/10 bg-zinc-900 px-3 py-1.5 text-[11px] text-zinc-300 hover:text-white"><ArrowDown size={12} />Jump to latest</button></div>}
    {active && <div role="status" className="flex shrink-0 items-center justify-center gap-2 px-4 py-2 text-xs text-zinc-500">
      {paused ? <Pause size={15} className="shrink-0 text-amber-400" /> : <LoaderCircle size={15} className="activity-spin shrink-0 text-emerald-400" />}
      <span>{activityLabel(state)}</span>
    </div>}
  </section>;
}
