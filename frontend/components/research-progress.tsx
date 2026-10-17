"use client";

import { ChevronRight, LoaderCircle, Check, CircleAlert } from "lucide-react";
import type { ArcEvent } from "@/lib/investigation";

const readable = (text?: string) => (text || "Pending").replaceAll("_", " ").toLowerCase();
export function researchEvents(logs: ArcEvent[]) {
  const seen = new Set<string>();
  return logs.filter((event) => {
    if (!/^(research\.|task\.|lead\.|hypothesis\.|critic\.|tool\.recovery)/.test(event.type)) return false;
    const key = event.sequence === undefined ? undefined : `${event.session_id}:${event.sequence}`;
    if (key && seen.has(key)) return false;
    if (key) seen.add(key);
    return true;
  });
}

export function ResearchProgress({ logs }: { logs: ArcEvent[] }) {
  const events = researchEvents(logs);
  if (!events.length) return null;
  const tasks = new Map<string, ArcEvent>();
  for (const event of events) {
    if (event.task_id && event.type.startsWith("task.")) {
      const previous = tasks.get(event.task_id) || { type: "task.queued" };
      tasks.set(event.task_id, { ...previous, ...event,
        status: event.type === "task.started" ? "RUNNING" : event.status || previous.status || "QUEUED" });
    }
  }
  const stopped = events.findLast((event) => event.type === "research.stopped");
  const wave = events.findLast((event) => event.type === "research.wave");
  const decisions = events.filter((event) => ["lead.discovered", "critic.reviewed", "tool.recovery", "task.rejected"].includes(event.type));
  const running = [...tasks.values()].filter((task) => task.status === "RUNNING").length;
  return <section aria-label="Recursive research progress" className="py-4 text-sm">
    <div className="flex flex-wrap items-center gap-2 text-ink" role="status">
      {!stopped && running ? <LoaderCircle size={15} className="activity-spin text-accent" /> : <Check size={15} className="text-muted" />}
      <span>Wave {wave?.wave ?? 0} of {wave?.max_waves ?? 3}</span>
      <span className="text-muted">· {tasks.size} tasks{running && !stopped ? ` · ${running} running` : ""}</span>
    </div>
    {stopped && <p className="mt-2 text-xs text-warning">Stopped: {readable(stopped.reason)}. Unresolved claims remain visible below.</p>}
    <details className="group mt-3 border-y border-line py-3">
      <summary className="flex cursor-pointer items-center gap-2 text-xs text-muted"><ChevronRight size={13} className="transition-transform group-open:rotate-90" />Research steps and recovery decisions</summary>
      <ol className="mt-4 space-y-4" aria-label="Research task ledger">
        {[...tasks.values()].map((task) => <li key={task.task_id} style={{ paddingLeft: `${Math.min(task.depth || 0, 2) * 14}px` }}>
          <p className="flex items-start gap-2 text-xs text-ink">
            {task.status === "RUNNING" && !stopped ? <LoaderCircle size={13} className="activity-spin mt-0.5 shrink-0" /> : task.status === "BLOCKED" ? <CircleAlert size={13} className="mt-0.5 shrink-0 text-warning" /> : <Check size={13} className="mt-0.5 shrink-0 text-muted" />}
            {task.goal || task.role || "Research task"}</p>
          <p className="mt-1 text-[11px] text-muted">Wave {task.wave} · depth {task.depth} · {readable(task.status)}{task.parent_id ? ` · child of ${task.parent_id.slice(-6)}` : " · root"}</p>
          {task.token_upper_bound !== undefined && <p className="mt-1 text-[11px] text-muted">{task.token_upper_bound.toLocaleString()} packet bytes · {task.included} passages included · {task.excluded} excluded</p>}
          {task.unresolved?.map((reason) => <p key={reason} className="mt-1 break-words text-[11px] text-warning">{reason}</p>)}
        </li>)}
      </ol>
      <div className="mt-5 space-y-3 border-t border-line pt-4">
        {decisions.map((event, index) => <p key={event.sequence ?? index} className="break-words text-xs leading-relaxed text-muted">
          <span className="text-ink">{event.type === "tool.recovery" ? `${event.simulated ? "Simulated outage · " : ""}${readable(event.strategy)} · ${event.state}` : readable(event.type.replaceAll(".", " "))}</span>
          {event.reason && <> — {event.reason}</>}
        </p>)}
      </div>
    </details>
  </section>;
}

export function ResearchCoverage({ logs, onSpans }: { logs: ArcEvent[]; onSpans?: (ids: string[]) => void }) {
  const event = logs.findLast((event) => event.type === "research.coverage");
  if (!event?.coverage) return null;
  return <section aria-label="Claim coverage" className="space-y-4 py-4">
    <h3 className="text-sm font-medium text-ink">What the evidence supports</h3>
    {event.coverage.map((claim) => <div key={claim.hypothesis_id} className="border-l border-line pl-3">
      {onSpans && [...(claim.supporting || []), ...(claim.refuting || [])].length ? <button onClick={() => onSpans([...(claim.supporting || []), ...(claim.refuting || [])])} className="text-left text-sm text-ink hover:text-accent hover:underline">{claim.statement}</button> : <p className="text-sm text-ink">{claim.statement}</p>}
      <p className="mt-1 text-xs text-warning">{readable(claim.support)}{claim.insufficient_evidence ? " · insufficient independent evidence" : ""}</p>
      <p className="mt-1 text-xs leading-relaxed text-muted">{claim.rationale}</p>
    </div>)}
    {!!event.missing_portrait_sections?.length && <p className="text-xs leading-relaxed text-muted">Sections without a complete cited assessment: {event.missing_portrait_sections.map(readable).join(", ")}.</p>}
  </section>;
}
