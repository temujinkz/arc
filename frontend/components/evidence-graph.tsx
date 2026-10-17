"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";
import { GitBranch, LoaderCircle, Maximize, Minus, Plus } from "lucide-react";
import type { ForceGraphMethods } from "react-force-graph-2d";
import type { GraphData, GraphNode } from "@/lib/investigation";

const ForceGraph = dynamic(() => import("react-force-graph-2d"), { ssr: false,
  loading: () => <p role="status" className="flex items-center gap-2 p-6 text-sm text-zinc-400"><LoaderCircle size={15} className="activity-spin" />Loading graph…</p> });
const colors: Record<string, string> = {
  Person: "#60a5fa", DomainPillar: "#a78bfa", Artifact: "#34d399", Claim: "#f472b6",
  AIJudgment: "#fbbf24", EvidenceNode: "#22d3ee", IdentityAnchor: "#67e8f9",
};
const filters = { All: null, Identities: ["Person", "IdentityAnchor"], Themes: ["Person", "DomainPillar"], Evidence: ["Person", "Claim", "Artifact", "EvidenceNode"], Judgments: ["Person", "AIJudgment"] };
type Filter = keyof typeof filters;

export function EvidenceGraph({ graph, committed, selected, onSelect }: {
  graph: GraphData; committed: boolean; selected: string | null; onSelect: (id: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const engine = useRef<ForceGraphMethods | undefined>(undefined);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [filter, setFilter] = useState<Filter>("All");
  const fitPending = useRef(true);
  const simulation = useMemo(() => {
    const labels = filters[filter];
    const nodes = graph.nodes.filter((node) => !labels || labels.includes(node.label)).map((node) => ({ ...node }));
    const ids = new Set(nodes.map((node) => node.id));
    return { nodes, links: graph.links.filter((link) => ids.has(link.source) && ids.has(link.target)).map((link) => ({ ...link })) };
  }, [graph, filter]);
  useEffect(() => { fitPending.current = true; }, [simulation, size]);
  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setSize({ width: Math.floor(entry.contentRect.width), height: Math.floor(entry.contentRect.height) }));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return <section className="flex min-h-[460px] flex-1 flex-col lg:min-h-0" aria-label="Evidence graph">
    <div ref={container} className="relative min-h-[390px] flex-1 overflow-hidden bg-[radial-gradient(#ffffff08_1px,transparent_1px)] bg-size-[20px_20px] lg:min-h-0">
      <div className="absolute top-4 right-4 left-4 z-10 flex flex-wrap items-start justify-between gap-2">
        <div className="glass-card flex max-w-full flex-wrap items-center gap-2 px-3 py-2"><GitBranch size={13} className="text-emerald-400" /><span className="mr-1 text-[11px] text-zinc-300">Evidence map</span>
          <label className="sr-only" htmlFor="graph-filter">Filter graph nodes</label><select id="graph-filter" value={filter} onChange={(event) => setFilter(event.target.value as Filter)} className="rounded border border-white/10 bg-zinc-900 px-2 py-1 text-[10px] text-zinc-400">{Object.keys(filters).map((name) => <option key={name}>{name}</option>)}</select></div>
        <span className="glass-card px-3 py-2.5 text-[10px] text-zinc-500">{simulation.nodes.length} nodes · {committed ? "Committed graph" : `${graph.nodes.filter((node) => node.isDraft).length} draft signals`}</span>
      </div>
      {simulation.nodes.length === 0 ? <div className="absolute inset-0 grid place-items-center p-8 text-center"><div><GitBranch size={32} className="mx-auto mb-4 text-zinc-700" /><p className="text-sm text-zinc-400">{graph.nodes.length ? "No nodes match this filter." : "Follow the evidence as it connects."}</p><p className="mt-2 text-xs text-zinc-600">Dashed rings are draft sources. Solid nodes belong to the committed graph.</p></div></div>
        : size.width > 0 && size.height > 0 && <ForceGraph ref={engine}
          width={size.width} height={size.height} graphData={simulation} backgroundColor="#00000000"
          nodeLabel={() => ""} linkLabel={() => ""} nodeRelSize={7} linkColor={() => "#38434e"}
          linkLineDash={() => committed ? null : [3, 3]} linkDirectionalArrowLength={3} linkDirectionalArrowRelPos={1}
          d3VelocityDecay={0.35} cooldownTicks={100}
          onEngineStop={() => { if (fitPending.current) { engine.current?.zoomToFit(300, 85); fitPending.current = false; } }}
          onNodeClick={(node) => onSelect(String(node.id))}
          nodeCanvasObject={(object, ctx, scale) => {
            const node = object as GraphNode & { x: number; y: number };
            const color = colors[node.label] || "#94a3b8";
            const radius = node.label === "Person" ? 9 : node.label === "DomainPillar" ? 7 : 5;
            ctx.save();
            ctx.beginPath(); ctx.arc(node.x, node.y, radius + 3, 0, 2 * Math.PI);
            ctx.strokeStyle = selected === node.id ? "#ffffff" : color;
            ctx.lineWidth = (selected === node.id ? 2 : 0.7) / scale;
            ctx.globalAlpha = node.isDraft ? 0.5 : selected === node.id ? 1 : 0.35;
            ctx.setLineDash(node.isDraft ? [3 / scale, 3 / scale] : []); ctx.stroke();
            ctx.globalAlpha = node.isDraft ? 0.25 : 1;
            ctx.beginPath(); ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI);
            ctx.fillStyle = color; ctx.shadowColor = color; ctx.shadowBlur = node.isDraft ? 0 : 10; ctx.fill();
            ctx.shadowBlur = 0; ctx.globalAlpha = 1;
            const fontSize = Math.min(12, 10 / scale);
            ctx.font = `500 ${fontSize}px Arial`; ctx.textAlign = "center"; ctx.textBaseline = "top";
            const title = node.title.length > 30 ? `${node.title.slice(0, 30)}…` : node.title;
            const width = ctx.measureText(title).width;
            ctx.fillStyle = "rgba(9,11,14,.85)"; ctx.fillRect(node.x - width / 2 - 3, node.y + radius + 5, width + 6, fontSize + 4);
            ctx.fillStyle = node.isDraft ? "#71717a" : "#d4d4d8"; ctx.fillText(title, node.x, node.y + radius + 7);
            ctx.restore();
          }}
          nodePointerAreaPaint={(object, color, ctx) => { const node = object as GraphNode & { x: number; y: number }; ctx.fillStyle = color; ctx.beginPath(); ctx.arc(node.x, node.y, 13, 0, 2 * Math.PI); ctx.fill(); }}
        />}
      {simulation.nodes.length > 0 && <div className="glass-card absolute bottom-4 left-4 flex items-center gap-1 p-1">
        <button aria-label="Zoom in" onClick={() => engine.current?.zoom((engine.current?.zoom() || 1) * 1.3, 200)} className="rounded p-2 text-zinc-400 hover:bg-white/5"><Plus size={14} /></button>
        <button aria-label="Zoom out" onClick={() => engine.current?.zoom((engine.current?.zoom() || 1) / 1.3, 200)} className="rounded p-2 text-zinc-400 hover:bg-white/5"><Minus size={14} /></button>
        <button aria-label="Fit graph" onClick={() => engine.current?.zoomToFit(300, 85)} className="rounded p-2 text-zinc-400 hover:bg-white/5"><Maximize size={14} /></button></div>}
    </div>
    <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-t border-white/5 px-4 py-3 text-[9px] text-zinc-500">
      {Object.entries({ Person: colors.Person, Theme: colors.DomainPillar, Artifact: colors.Artifact, Claim: colors.Claim, Judgment: colors.AIJudgment }).map(([label, color]) => <span key={label} className="flex items-center gap-1.5"><span className="size-1.5 rounded-full" style={{ backgroundColor: color }} />{label}</span>)}
      <span className="flex items-center gap-1.5"><span className="size-2 rounded-full border border-dashed border-zinc-500" />Draft</span>
    </div>
    {graph.nodes.length > 0 && <div className="flex shrink-0 items-center gap-3 border-t border-white/5 px-4 py-2">
      <label htmlFor="node-picker" className="shrink-0 text-[10px] text-zinc-500">Inspect node</label><select id="node-picker" value={selected || ""} onChange={(event) => onSelect(event.target.value)} className="min-w-0 flex-1 rounded border border-white/10 bg-zinc-900 p-1 text-xs text-zinc-300"><option value="" disabled>Choose a node…</option>{graph.nodes.map((node) => <option key={node.id} value={node.id}>{node.label}: {node.title}</option>)}</select>
    </div>}
  </section>;
}
