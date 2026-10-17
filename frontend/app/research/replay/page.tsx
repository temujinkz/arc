"use client";

import Link from "next/link";
import { useState } from "react";
import { applyEvent, initialState } from "@/lib/investigation";
import { ResearchCoverage, ResearchProgress } from "@/components/research-progress";
import { EvidenceGraph } from "@/components/evidence-graph";
import { parseReplayBundle, type ReplayBundle } from "@/lib/replay";

export default function ReplayPage() {
  const [bundle, setBundle] = useState<ReplayBundle | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const state = bundle?.events.reduce(applyEvent, initialState) || initialState;
  const selectedNode = state.graph.nodes.find((node) => node.id === selected);
  return <main className="mx-auto max-w-6xl px-5 py-8 text-zinc-200 sm:px-8">
    <Link href="/" className="text-xs text-zinc-500 hover:text-white">← ARC workspace</Link>
    <h1 className="mt-6 text-2xl font-medium tracking-tight">Inspect a cached investigation</h1>
    <p className="mt-3 max-w-2xl text-sm leading-relaxed text-zinc-400">This viewer reads the replay file on your device. It makes no research calls and does not update the public graph. Findings come from reviewed fixtures; outages are simulated.</p>
    <label className="mt-6 block text-xs text-zinc-400">Open replay.json
      <input type="file" accept=".json,application/json" className="mt-2 block w-full text-xs file:mr-4 file:rounded-lg file:border-0 file:bg-white/10 file:px-4 file:py-2 file:text-zinc-200" onChange={async (event) => {
        const file = event.target.files?.[0]; if (!file) return;
        setError("");
        try {
          if (file.size > 5_000_000) throw new Error("Replay exceeds the viewer's 5 MB file limit.");
          const parsed = parseReplayBundle(JSON.parse(await file.text()));
          setBundle(parsed); setSelected(null);
        } catch (error) { setError(error instanceof Error ? error.message : "Could not read replay."); }
      }} />
    </label>
    {error && <p role="alert" className="mt-3 text-sm text-rose-300">{error}</p>}
    {bundle && <>
      <div className="mt-8 border-y border-white/10 py-4 text-xs text-zinc-400">
        <p>Offline preview · 0 model calls · 0 source calls · graph not committed</p>
        <p className="mt-2">Largest packet: {bundle.token_report.max_packet_bytes.toLocaleString()} bytes · saved source payloads: {bundle.token_report.raw_cached_data_bytes.toLocaleString()} bytes</p>
        <p className="mt-2 text-zinc-500">{bundle.token_report.measurement}</p>
      </div>
      <ResearchProgress logs={state.logs} />
      <ResearchCoverage logs={state.logs} />
      <div className="mt-6 flex h-[480px] min-w-0 flex-col overflow-hidden rounded-xl border border-white/10">
        <EvidenceGraph graph={state.graph} committed={false} selected={selected} onSelect={setSelected} />
      </div>
      {selectedNode && <section className="mt-5 space-y-2 pb-10" aria-label="Replay passage inspector">
        <p className="text-xs text-zinc-500">{selectedNode.label} · {selectedNode.evidence_state}</p>
        <h2 className="text-sm font-medium">{selectedNode.title}</h2>
        <pre className="whitespace-pre-wrap break-words text-xs leading-relaxed text-zinc-400">{selectedNode.summary}</pre>
      </section>}
    </>}
  </main>;
}
