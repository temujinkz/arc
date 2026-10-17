"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Terminal, Check, AlertTriangle, LoaderCircle, Pause, ChevronRight, ArrowUpRight, ArrowDown } from "lucide-react";
import type { ArcEvent, InvestigationState } from "@/lib/investigation";
import { activityLabel, provider, toolLabel, toolResults } from "@/lib/presentation";
import { AgentResponse } from "./agent-response";
import { ResearchProgress } from "./research-progress";
import { HumanReview } from "./review-panel";

function ToolRow({ event, result, state, onEvidence }: {
  event: ArcEvent; result?: ArcEvent; state: InvestigationState; onEvidence: (id: string) => void;
}) {
  const pending = !result && ["starting", "running"].includes(state.status);
  const paused = pending && (state.prompts.length > 0 || state.connection === "reconnecting");
  const failed = result?.type === "tool.failed" || result?.evidence_status === "failed";
  const duration = result?.timestamp && event.timestamp ? Math.max(0, result.timestamp - event.timestamp) : null;
  const label = failed ? "Failed" : result?.source_cache?.hit ? "Reused" : result?.evidence_status === "captured" ? "Captured"
    : result?.evidence_status === "skipped" ? "Not captured" : result ? "Finished" : paused ? "Waiting" : pending ? "Running" : "Interrupted";
  const neutral = !result || result.evidence_status === "skipped";
  const statusColor = failed ? "text-danger" : paused ? "text-warning" : neutral ? "text-muted" : "text-accent";
  const agent = event.subagent === "operator" ? "Operator lookup" : event.subagent === "orchestrator" ? "Lead agent" : event.subagent ? "Harvester" : "Agent";
  return <div className="tool-activity">
    <details className="tool-disclosure group">
      <summary className="flex min-h-11 items-center gap-2 rounded-md px-1 py-2 text-[13px] hover:bg-wash/60 sm:gap-3">
        <ChevronRight size={13} className="shrink-0 text-muted transition-transform group-open:rotate-90" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate text-ink" title={toolLabel(event.tool)}>{toolLabel(event.tool)}</span>
        <span className={`flex shrink-0 items-center gap-1.5 text-xs ${statusColor}`}>
          {failed ? <AlertTriangle size={13} aria-hidden="true" /> : paused || neutral && !pending ? <Pause size={13} aria-hidden="true" /> : pending ? <LoaderCircle size={13} className="activity-spin" aria-hidden="true" /> : <Check size={13} aria-hidden="true" />}
          {label}
        </span>
        {duration !== null && <span className="hidden w-12 shrink-0 text-right text-[11px] tabular-nums text-muted sm:inline">{duration.toFixed(1)}s</span>}
      </summary>
      <div className="tool-details ml-2 space-y-4 border-l border-line py-3 pl-5 pr-1 text-xs sm:ml-2.5">
        <p className="text-muted">{provider(event.tool)} · {agent}{duration !== null && <span className="sm:hidden"> · {duration.toFixed(1)}s</span>}</p>
        <p className="break-all font-mono text-[11px] text-muted">{event.tool}{event.subagent ? ` · ${event.subagent}` : ""}</p>
        <div><p className="mb-2 font-medium text-ink">Arguments</p><pre tabIndex={0} className="max-h-52 overflow-auto rounded-md bg-wash/50 p-3 text-[12px] leading-6 text-muted">{JSON.stringify(event.args, null, 2)}</pre></div>
        {result && <div><p className="mb-2 font-medium text-ink">{failed ? "Error details" : "Output preview"}</p>
          {result.source_cache && <p className="mb-2 text-xs text-muted">{result.source_cache.hit ? "Reused saved source" : "New source saved"} · Collected {result.source_cache.observed_at}</p>}
          <p className="max-h-48 overflow-auto whitespace-pre-wrap break-words text-xs leading-6 text-muted">{result.error || result.preview || "Tool completed."}</p></div>}
        {result?.evidence_id && <button type="button" onClick={() => onEvidence(result.evidence_id!)} className="inline-flex items-center gap-1 text-xs text-accent underline decoration-accent/30 underline-offset-4 hover:decoration-accent">Inspect captured output <ArrowUpRight size={12} /></button>}
      </div>
    </details>
    {failed && <p className="tool-failure-note ml-6 pb-2 text-xs leading-6 text-danger">{result?.error || "This lookup failed. Expand the tool for details."}</p>}
  </div>;

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
  const failures = logs.filter((event) => event.type === "investigation.failed");
  const latestFailure = failures.at(-1);
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
    {logs.length > 0 && <div className="mx-auto flex w-full max-w-[800px] shrink-0 items-center gap-2 px-5 py-2 text-[11px] text-muted sm:px-8"><span>{state.phase === "conversation" ? "Evidence conversation" : "Research activity"}</span><span className="ml-auto">{calls.length} tool calls · {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}</span></div>}
    <div ref={panel} onScroll={() => { const el = panel.current; if (el && visible) { readingPosition.current = el.scrollTop; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48; setFollowing(follow.current); } }}
      className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-[800px] space-y-3 px-5 py-4 sm:px-8 sm:py-6">
      {!logs.length && <div className="py-20 text-center"><Terminal size={28} className="mx-auto mb-4 text-muted" />
        <p className="font-editorial text-4xl sm:text-5xl tracking-tight text-ink">Who would you like to understand?</p>
        <p className="mx-auto mt-4 max-w-sm text-sm leading-relaxed text-muted">Start with a name and context, or a public profile. We’ll follow the sources together.</p></div>}
      <ResearchProgress logs={logs} />
      {logs.map((event, index) => {
        if (event.type === "case.ready" || event.type === "source.started" || event.type === "source.complete") return <p key={index} className="py-2 text-sm text-muted">{event.text}</p>;
        if (event.type === "source.failed" || event.type === "source.cancelled") return <p key={index} role="alert" className="py-2 text-sm text-warning">{event.error}</p>;
        if (event.type === "investigation.started" || event.type === "user.message") return <div key={index} className="flex justify-end py-4"><p className="max-w-[85%] whitespace-pre-wrap break-words rounded-xl rounded-tr-sm bg-wash px-4 py-3 text-sm leading-relaxed text-ink">{event.text || event.target}</p></div>;
        if (event.type === "conversation.failed" || event.type === "conversation.cancelled") return <p key={index} role="alert" className="py-2 text-xs text-warning">{event.error || event.text}</p>;
        if (event.type === "tool.started") return <ToolRow key={event.tool_use_id || index} event={event} result={results.get(event.tool_use_id || "")} state={state} onEvidence={onEvidence} />;
        if (event.type === "investigation.failed") {
          if (event !== latestFailure) return null;
          return <div key={index} aria-label="Investigation stopped" className="rounded-lg border border-danger/25 bg-danger/5 p-3 text-xs text-danger">
            <p className="flex items-center gap-2"><AlertTriangle size={13} />Investigation stopped</p>
            <p className="mt-2 leading-relaxed">{event.text || event.error || event.reason}</p>
            {failures.length > 1 && <details className="mt-3 text-muted">
              <summary className="cursor-pointer">Earlier error details ({failures.length - 1})</summary>
              {failures.slice(0, -1).map((failure, i) => <p key={i} className="mt-2 break-words">{failure.phase ? `${failure.phase}: ` : ""}{failure.text || failure.error || failure.reason}</p>)}
            </details>}
          </div>;
        }
        if (event.type === "evidence.skipped") return <div key={index} className="rounded-lg border border-danger/25 bg-danger/5 p-3 text-xs text-danger"><p className="flex items-center gap-2"><AlertTriangle size={13} />{toolLabel(event.tool) || "Evidence"}</p><p className="mt-2 leading-relaxed">{event.text || event.error || event.reason}</p></div>;
        if (event.type === "agent.text") return <AgentResponse key={index} text={event.text || ""}
          label={event.subagent && event.subagent !== "orchestrator" ? "Source harvester" : "ARC"}
          streaming={active && !paused && index === logs.length - 1} />;
        if (event.type === "agent.architect_decision") return <AgentResponse key={index} text={event.summary || ""} label="Graph assessment" />;
        if (event.type === "human.interrupt") return <p key={index} className="border-l border-amber-500/40 py-1 pl-3 text-xs leading-relaxed text-warning">Identity clarification requested</p>;
        if (event.type === "human.resolved") return <p key={index} className="pl-3 text-[11px] text-muted">Operator answer received</p>;
        if (event.type === "agent.status") return <p key={index} className="py-2 text-xs text-inference">{event.text}</p>;
        if (event.type === "investigation.complete") return <p key={index} className="flex items-center gap-2 py-3 text-xs text-accent"><Check size={14} />Investigation complete</p>;
        if (event.type === "investigation.cancelled") return <p key={index} className="text-xs text-warning">Investigation stopped. Remote provider jobs may still finish.</p>;
        return null;
      })}
      <HumanReview state={state} answer={answer} />
      </div>
    </div>
    {!following && <div className="flex shrink-0 justify-center py-2"><button type="button"
      onClick={() => { follow.current = true; setFollowing(true); if (panel.current) panel.current.scrollTop = panel.current.scrollHeight; }}
      className="flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1.5 text-[11px] text-ink hover:text-ink"><ArrowDown size={12} />Jump to latest</button></div>}
    {active && <div role="status" className="flex shrink-0 items-center justify-center gap-2 px-4 py-2 text-xs text-muted">
      {paused ? <Pause size={15} className="shrink-0 text-warning" /> : <LoaderCircle size={15} className="activity-spin shrink-0 text-accent" />}
      <span>{activityLabel(state)}</span>
    </div>}
  </section>;
}
