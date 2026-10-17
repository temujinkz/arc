"use client";

import { FileText, GitBranch, MessageSquare, Plus, RefreshCw, Users, PanelLeftClose, Database } from "lucide-react";
import type { InvestigationSummary } from "@/lib/use-investigation";

export const views = [
  { id: "conversation", title: "Conversation", icon: MessageSquare },
  { id: "evidence", title: "Evidence", icon: Database },
  { id: "dossier", title: "Dossier", icon: FileText },
  { id: "graph", title: "Graph", icon: GitBranch },
  { id: "peers", title: "Peer claims", icon: Users },
] as const;
export type WorkspaceView = typeof views[number]["id"];

export function InvestigationSidebar({ view, onView, sessionId, history, error, onOpen, onNew, onRefresh, onClose, pending }: {
  view: WorkspaceView; onView: (view: WorkspaceView) => void; sessionId: string;
  history: InvestigationSummary[]; error: string; onOpen: (item: InvestigationSummary) => void;
  onNew: () => void; onRefresh: () => void; onClose: () => void; pending: boolean;
}) {
  return <div className="flex h-full min-h-0 w-64 flex-col bg-[#0b0d10] px-3 text-zinc-300">
    <div className="flex h-16 shrink-0 items-center justify-between px-2"><span className="text-lg font-semibold tracking-[0.18em] text-zinc-100">ARC<span className="ml-2 text-emerald-400">·</span></span>
      <button onClick={onClose} aria-label="Close sidebar" className="rounded-lg p-2 text-zinc-500 hover:bg-white/5 hover:text-zinc-200"><PanelLeftClose size={17} /></button></div>
    <button onClick={onNew} disabled={pending} className="mb-6 flex items-center gap-3 rounded-lg border border-white/8 bg-white/3 px-3 py-3 text-sm text-zinc-200 transition hover:bg-white/6 disabled:opacity-40"><Plus size={16} />New investigation</button>
    <nav aria-label="Investigation navigation" className="space-y-1">{views.map((item) => <button key={item.id} onClick={() => onView(item.id)} aria-current={view === item.id ? "page" : undefined}
      className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-[13px] transition ${view === item.id ? "bg-white/7 text-white" : "text-zinc-500 hover:bg-white/3 hover:text-zinc-300"}`}><item.icon size={16} />{item.title}</button>)}</nav>
    <div className="mt-8 mb-3 flex shrink-0 items-center justify-between px-3"><h2 className="text-[10px] font-medium uppercase tracking-widest text-zinc-600">Investigations</h2><button onClick={onRefresh} aria-label="Refresh investigation history" className="text-zinc-600 hover:text-zinc-300"><RefreshCw size={12} /></button></div>
    <div className="min-h-0 flex-1 space-y-1 overflow-y-auto pb-5">
      {error ? <div className="px-3 text-xs leading-relaxed text-amber-300/80">{error}<button onClick={onRefresh} className="mt-2 block text-zinc-300 underline">Try again</button></div>
        : !history.length && <p className="px-3 text-xs leading-relaxed text-zinc-600">Your investigations will appear here.</p>}
      {history.map((item) => <button key={item.session_id} onClick={() => onOpen(item)} disabled={pending} aria-current={sessionId === item.session_id ? "true" : undefined}
        className={`flex w-full items-center gap-2 rounded-lg px-3 py-3 text-left text-xs transition disabled:opacity-40 ${sessionId === item.session_id ? "bg-white/7 text-zinc-200" : "text-zinc-500 hover:bg-white/3 hover:text-zinc-300"}`}>
        <span className="min-w-0 flex-1 truncate">{item.target}</span>{item.active && <span className="size-1.5 shrink-0 rounded-full bg-emerald-400" aria-label="In progress" />}</button>)}
    </div>
    <p className="border-t border-white/5 px-3 py-4 text-[10px] leading-relaxed text-zinc-600">Investigations and collected sources are saved on this device.</p>
  </div>;
}
