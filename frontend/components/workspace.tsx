"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { PanelLeft, Square, X, LoaderCircle, Check, Pause, SlidersHorizontal, ArrowLeft } from "lucide-react";
import { API_BASE, useInvestigation, type InvestigationSummary } from "@/lib/use-investigation";
import { provider, toolResults } from "@/lib/presentation";
import { EvidenceGraph } from "./evidence-graph";
import { ToolStream } from "./tool-stream";
import { ReviewPanel, NodeInspector } from "./review-panel";
import { ChatComposer } from "./chat-composer";
import { InvestigationSidebar, views, type WorkspaceView } from "./investigation-sidebar";
import { PeerJudgments } from "./peer-judgments";
import { ClaimJournal } from "./claim-journal";
import { ProfileAgent } from "./profile-agent";
import { EvidenceWorkbench } from "./evidence-workbench";

export function Workspace() {
  const { state, start, send, answer, cancel, evidence, history, historyError, refreshHistory, open, newInvestigation, createCase, collectSource } = useInvestigation();
  const [view, setView] = useState<WorkspaceView>("conversation");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [raw, setRaw] = useState("");
  const [loadingEvidence, setLoadingEvidence] = useState(false);
  const [toolsStatus, setToolsStatus] = useState<Record<string, { installed: boolean }> | null>(null);
  const [toolsError, setToolsError] = useState("");
  const [dorks, setDorks] = useState<{ query: string; url: string }[]>([]);
  const evidenceDialog = useRef<HTMLDialogElement>(null);
  const detailsDialog = useRef<HTMLDialogElement>(null);
  const mobileSidebar = useRef<HTMLDialogElement>(null);
  const evidenceRequest = useRef(0);
  const busy = state.status === "starting" || state.status === "running";
  const paused = state.prompts.length > 0 || state.connection === "reconnecting";
  const node = state.graph.nodes.find((node) => node.id === selected);
  const results = toolResults(state.logs);
  const hasSession = !!state.sessionId;
  const canFollowUp = state.logs.some((event) => event.type === "investigation.complete");
  const status = state.connection === "reconnecting" && busy ? "Reconnecting"
    : state.prompts.length ? "Your answer needed" : busy ? state.phase === "conversation" ? "Replying" : state.phase === "source" ? "Collecting source" : "Researching"
    : state.status === "complete" ? state.collectionMode === "sources" ? "Evidence case" : "Complete" : state.status === "idle" ? "" : state.status;
  const showView = (value: WorkspaceView) => { setView(value); mobileSidebar.current?.close(); };
  const sidebarProps = { view, onView: showView, sessionId: state.sessionId, history, error: historyError,
    onRefresh: () => { void refreshHistory(); }, pending: state.status === "starting",
    onNew: () => { newInvestigation(); setSelected(null); showView("conversation"); },
    onOpen: (item: InvestigationSummary) => { open(item); setSelected(null); showView("conversation"); } };
  const openEvidence = async (id: string) => {
    const requestId = ++evidenceRequest.current;
    setRaw(""); setLoadingEvidence(true); evidenceDialog.current?.showModal();
    try { const result = await evidence(id); if (requestId === evidenceRequest.current) setRaw(JSON.stringify(result, null, 2)); }
    catch (error) { if (requestId === evidenceRequest.current) setRaw(String(error)); }
    finally { if (requestId === evidenceRequest.current) setLoadingEvidence(false); }
  };
  const openDetails = async () => {
    detailsDialog.current?.showModal();
    setToolsStatus(null); setToolsError(""); setDorks([]);
    try {
      const response = await fetch(`${API_BASE}/api/tools`);
      if (!response.ok) throw new Error("Could not load tool availability.");
      setToolsStatus((await response.json()).local_tools);
    } catch { setToolsError("Could not load tool availability."); }
    if (state.target) {
      try {
        const response = await fetch(`${API_BASE}/api/tools/google-dorks?subject=${encodeURIComponent(state.target.replaceAll('"', ""))}`);
        if (response.ok) setDorks((await response.json()).queries);
      } catch { /* Search links are optional; tool availability has its own error. */ }
    }
  };

  return <main className="flex h-dvh min-h-0 overflow-hidden bg-paper text-ink">
    <aside className={sidebarOpen ? "hidden shrink-0 border-r border-line md:flex" : "hidden"} aria-label="Sidebar"><InvestigationSidebar {...sidebarProps} onClose={() => setSidebarOpen(false)} /></aside>
    <dialog ref={mobileSidebar} aria-label="Navigation menu" className="m-0 h-dvh max-h-none max-w-none border-0 bg-sidebar p-0 text-ink backdrop:bg-ink/20"><InvestigationSidebar {...sidebarProps} onClose={() => mobileSidebar.current?.close()} /></dialog>
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="flex h-16 shrink-0 items-center gap-3 px-4 sm:px-6">
        <button aria-label="Toggle sidebar" onClick={() => { if (window.matchMedia("(min-width: 768px)").matches) setSidebarOpen(!sidebarOpen); else mobileSidebar.current?.showModal(); }} className="rounded-lg p-2 text-muted hover:bg-wash hover:text-ink"><PanelLeft size={18} /></button>
        <div className="min-w-0 flex-1"><h1 className="truncate text-sm font-medium text-ink">{state.target || "New investigation"}</h1>{view !== "conversation" && <p className="mt-0.5 text-[10px] text-muted">{views.find((item) => item.id === view)?.title}</p>}</div>
        {status && <span role="status" className={`flex shrink-0 items-center gap-1.5 text-[11px] ${paused ? "text-warning" : state.status === "failed" ? "text-danger" : "text-muted"}`}>
          {busy ? paused ? <Pause size={12} /> : <LoaderCircle size={12} className="activity-spin" /> : state.status === "complete" ? <Check size={12} /> : null}<span className="hidden sm:inline">{status}</span></span>}
        {busy && hasSession && <button onClick={() => void cancel()} aria-label="Stop current operation" className="rounded-lg p-2 text-muted hover:bg-wash"><Square size={14} /></button>}
        <button aria-label="Investigation details" onClick={() => void openDetails()} className="rounded-lg p-2 text-muted hover:bg-wash hover:text-ink"><SlidersHorizontal size={17} /></button>
      </header>
      {state.error && <p role="alert" className="mx-4 mb-2 rounded-lg border border-rose-500/15 bg-rose-500/5 px-4 py-3 text-xs text-danger">{state.error}</p>}
      {state.prompts.length > 0 && view !== "conversation" && <button onClick={() => showView("conversation")} className="mx-4 mb-3 flex items-center gap-2 rounded-lg border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-left text-xs text-warning"><ArrowLeft size={14} />ARC needs your answer. Return to the conversation.</button>}
      <section key={`conversation-${state.sessionId || "new"}`} aria-label="Conversation" className={view === "conversation" ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
        <ToolStream state={state} visible={view === "conversation"} answer={answer} onEvidence={(id) => void openEvidence(id)} />
        {!hasSession && <button onClick={() => showView("evidence")} className="mx-auto mb-4 rounded-lg px-4 py-2 text-sm text-accent hover:bg-wash">Or start with free source lookups</button>}
        {hasSession && state.collectionMode === "sources" && <p className="mx-5 mb-4 text-center text-sm text-muted">This is an evidence case. <button onClick={() => showView("evidence")} className="text-accent hover:underline">Open the evidence workbench</button> to collect and inspect sources.</p>}
        {state.collectionMode !== "sources" && <ChatComposer hasSession={hasSession} canFollowUp={canFollowUp} busy={busy} waiting={!!state.prompts.length}
          onSubmit={async (text, refreshSources) => { setSelected(null); return hasSession ? send(text) : start(text, refreshSources); }} />}
      </section>
      <section aria-label="Evidence workbench" className={view === "evidence" ? "min-h-0 flex-1 overflow-y-auto" : "hidden"}>
        {view === "evidence" && <EvidenceWorkbench key={state.sessionId || "new-evidence-case"} state={state} onCreate={createCase} onCollect={collectSource}
          onEvidence={(id) => void openEvidence(id)} onSelectNode={(id) => { setSelected(id); showView("graph"); }} />}
      </section>
      <section aria-label="Dossier" className={view === "dossier" ? "relative min-h-0 flex-1 overflow-y-auto" : "hidden"}><div className="mx-auto max-w-5xl">
        <ReviewPanel state={state} onSelect={setSelected} onEvidence={(id) => void openEvidence(id)} />
        {view === "dossier" && state.committed && <div key={state.sessionId}>
          <Link href={`/u/${state.sessionId}`} className="mx-7 text-sm text-accent hover:underline">Open public profile ↗</Link>
          <ClaimJournal profileId={state.sessionId} />
          <ProfileAgent profileId={state.sessionId} name={state.graph.nodes.find((item) => item.label === "Person")?.title || state.target} />
        </div>}
        {node && <div className="sticky bottom-3 z-20 mx-5 mb-4"><NodeInspector node={node} onEvidence={(id) => void openEvidence(id)} onClose={() => setSelected(null)} /></div>}
      </div></section>
      <section aria-label="Graph" className={view === "graph" ? "relative flex min-h-0 flex-1 flex-col overflow-y-auto" : "hidden"}>
        {view === "graph" && <EvidenceGraph graph={state.graph} committed={state.committed} selected={selected} onSelect={setSelected} />}
        {node && <div className="absolute right-3 bottom-14 z-20 max-h-[55%] w-[min(340px,calc(100%-24px))] overflow-y-auto"><NodeInspector node={node} onEvidence={(id) => void openEvidence(id)} onClose={() => setSelected(null)} /></div>}
      </section>
      <section aria-label="Peer claims" className={view === "peers" ? "min-h-0 flex-1 overflow-y-auto" : "hidden"}>{view === "peers" && (state.committed ? <PeerJudgments key={state.sessionId} profileId={state.sessionId} /> : <p className="p-7 text-sm text-muted">Complete an investigation to open its peer judgments.</p>)}</section>
    </div>
    <dialog ref={detailsDialog} aria-labelledby="details-heading" className="my-0 mr-0 ml-auto h-dvh max-h-none w-[min(90vw,380px)] max-w-none border-l border-line bg-surface p-6 text-ink backdrop:bg-ink/20">
      <div className="mb-8 flex items-center justify-between"><h2 id="details-heading" className="text-sm font-medium">Investigation details</h2><button onClick={() => detailsDialog.current?.close()} aria-label="Close details" className="text-muted"><X size={18} /></button></div>
      <h3 className="eyebrow mb-4">Source activity</h3><div className="space-y-4">{["Free search", "Sherlock", "Maigret", "theHarvester", "Crossref", "Public DNS", "Internet Archive", "Robin", "Exa", "Apify", "GitHub", "Firecrawl", "Neo4j"].map((name) => {
        const calls = state.logs.filter((event) => event.type === "tool.started" && provider(event.tool) === name);
        const running = busy && calls.some((event) => !results.has(event.tool_use_id || ""));
        const failed = calls.some((event) => results.get(event.tool_use_id || "")?.type === "tool.failed");
        const used = calls.some((event) => results.get(event.tool_use_id || "")?.type === "tool.finished");
        const committed = name === "Neo4j" && state.committed;
        return <div key={name} className="flex items-center justify-between text-xs"><span className="text-muted">{name}</span><span className={running ? "text-warning" : failed ? "text-danger" : used || committed ? "text-accent" : "text-muted"}>{committed ? "Committed" : running ? paused ? "Waiting" : "Active" : failed ? "Error" : used ? "Used" : "Not used"}</span></div>;
      })}</div>
      <div className="mt-8 space-y-3 border-t border-line pt-6 text-xs">
        <h3 className="eyebrow">Free local tools</h3>
        {toolsError ? <p role="alert" className="text-danger">{toolsError}</p> : toolsStatus ? Object.entries(toolsStatus).map(([name, tool]) => <p key={name} className="flex justify-between text-muted"><span>{name}</span><span>{tool.installed ? "Installed" : "Not installed"}</span></p>) : <p className="text-muted">Checking availability…</p>}
        {state.committed && <a href={`${API_BASE}/api/investigate/${encodeURIComponent(state.sessionId)}/exports/maltego.csv`} className="block text-accent hover:underline">Download Maltego CSV</a>}
        {dorks.length > 0 && <div className="space-y-2 pt-3"><h3 className="eyebrow">Google search links</h3><p className="text-muted">Open a query to search manually.</p>{dorks.map((item) => <a key={item.query} href={item.url} target="_blank" rel="noreferrer" className="block break-words text-accent hover:underline">{item.query} ↗</a>)}</div>}
      </div>
      <div className="mt-8 space-y-4 border-t border-line pt-6 text-xs"><p className="flex justify-between text-muted"><span>Reported Claude cost</span><span>${state.cost.toFixed(4)}</span></p><p className="flex justify-between text-muted"><span>Cache read</span><span>{state.cacheRead.toLocaleString()} tokens</span></p><p className="text-muted">Provider charges are separate. Follow-ups reuse collected evidence and spend from the remaining investigation budget.</p>{state.model && <p className="text-muted">{state.model}</p>}{hasSession && <p className="break-all text-[10px] text-muted">Session {state.sessionId}</p>}</div>
    </dialog>
    <dialog ref={evidenceDialog} aria-labelledby="evidence-heading" className="m-auto max-h-[85dvh] w-[min(90vw,900px)] rounded-xl border border-line bg-surface p-0 text-ink backdrop:bg-ink/20">
      <div className="flex items-center justify-between border-b border-line p-4"><h2 id="evidence-heading" className="text-sm font-medium">Captured source output</h2><form method="dialog"><button aria-label="Close evidence" className="rounded p-1 text-muted hover:text-ink"><X size={18} /></button></form></div>
      {loadingEvidence ? <p role="status" className="flex items-center gap-2 p-6 text-xs text-muted"><LoaderCircle size={15} className="activity-spin" />Loading captured output…</p> : <pre className="max-h-[70dvh] overflow-auto p-4 text-xs leading-relaxed text-ink">{raw}</pre>}
    </dialog>
  </main>;
}
