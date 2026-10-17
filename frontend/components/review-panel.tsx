"use client";

import { useState } from "react";
import { AlertTriangle, ExternalLink, ShieldCheck, Fingerprint, ArrowUpRight, Layers, LoaderCircle, X } from "lucide-react";
import { safeSourceUrl, type GraphNode, type HumanPrompt, type InvestigationState } from "@/lib/investigation";
import { ProfessionalPortrait } from "./professional-portrait";

function HumanQuestion({ prompt, answer }: {
  prompt: HumanPrompt; answer: (id: string, selected: string[], text: string) => Promise<void>;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return <form className="rounded-lg border border-amber-800/70 bg-amber-950/10 p-4"
    onSubmit={async (event) => {
      event.preventDefault(); if (busy) return;
      setBusy(true); setError("");
      try { await answer(prompt.request_id, selected, text); }
      catch (error) { setError(String(error)); setBusy(false); }
    }}>
    <h3 className="mb-3 flex items-center gap-2 text-sm font-medium text-warning"><AlertTriangle size={16} />Confirm the identity</h3>
    <fieldset disabled={busy}>
      <legend className="mb-3 text-sm text-ink">{prompt.question}</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {prompt.candidates.map((candidate) => <label key={candidate.label} className="flex cursor-pointer items-start gap-2 rounded border border-line bg-surface p-3 has-checked:border-emerald-500">
          <input type={prompt.multi_select ? "checkbox" : "radio"} name={prompt.request_id} value={candidate.label}
            checked={selected.includes(candidate.label)} className="mt-1 accent-emerald-500"
            onChange={(event) => { setText(""); setSelected(prompt.multi_select
              ? event.target.checked ? [...selected, candidate.label] : selected.filter((s) => s !== candidate.label)
              : [candidate.label]); }} />
          <span><span className="block break-words text-xs font-medium">{candidate.label}</span>
            <span className="mt-1 block break-words text-[11px] leading-relaxed text-muted">{candidate.description}</span></span>
        </label>)}
      </div>
      <label className="mt-3 block text-xs text-muted">Or give a different answer
        <textarea value={text} maxLength={2000} rows={2} className="mt-2 w-full rounded border border-line bg-surface p-2 text-ink"
          placeholder="None of these — here is the correct profile…"
          onChange={(event) => { setSelected([]); setText(event.target.value); }} />
      </label>
      <button type="submit" disabled={busy || (!selected.length && !text.trim())} className="primary-button mt-3">
        {busy && <LoaderCircle size={13} className="activity-spin mr-2 inline" />}{busy ? "Submitting…" : "Send answer"}
      </button>
    </fieldset>
    {error && <p role="alert" className="mt-2 text-xs text-danger">{error}</p>}
  </form>;
}

export function HumanReview({ state, answer }: {
  state: InvestigationState; answer: (id: string, selected: string[], text: string) => Promise<void>;
}) {
  if (!state.prompts.length) return null;
  return <section aria-label="Identity confirmation" className="max-h-[65vh] shrink-0 space-y-3 overflow-y-auto border-b border-amber-500/20 p-4">
    {state.prompts.map((prompt) => <HumanQuestion key={prompt.request_id} prompt={prompt} answer={answer} />)}
  </section>;
}

export function TrustBadge({ node }: { node: GraphNode }) {
  const value = node.isDraft ? "Draft source" : node.evidence_state || "Unassessed";
  const color = node.evidence_state === "supported" ? "text-accent border-emerald-500/20 bg-emerald-500/5"
    : node.evidence_state === "disputed" ? "text-danger border-rose-500/20 bg-rose-500/5"
    : node.evidence_state === "interpretation" ? "text-inference border-violet-500/20 bg-violet-500/5"
    : "text-muted border-line bg-wash";
  return <span className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] capitalize ${color}`}>{value}</span>;
}

export function NodeInspector({ node, onEvidence, onClose }: {
  node: GraphNode; onEvidence: (id: string) => void; onClose?: () => void;
}) {
  const source = safeSourceUrl(node.url);
  return <aside className="glass-card space-y-3 p-4" aria-label="Selected node details">
    <div className="flex items-center justify-between gap-2"><span className="eyebrow">{node.label}</span>{onClose && <button onClick={onClose} aria-label="Close node inspector" className="text-muted hover:text-ink"><X size={15} /></button>}</div>
    <h3 className="text-sm font-medium text-ink">{node.title}</h3><TrustBadge node={node} />
    <p className="text-xs leading-relaxed text-muted">{node.summary || "Source collected into working memory; awaiting assessment."}</p>
    {node.confidence != null && <p className="text-[10px] text-muted">Model confidence · {Math.round(node.confidence * 100)}%</p>}
    {source && <a href={source} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-accent hover:underline">Open source <ExternalLink size={12} /></a>}
    <div className="flex flex-wrap gap-3">{node.evidence_ids?.map((id, index) => <button key={id} onClick={() => onEvidence(id)} className="text-[11px] text-accent hover:underline">Captured evidence {index + 1}</button>)}</div>
  </aside>;
}

export function ReviewPanel({ state, onSelect, onEvidence }: {
  state: InvestigationState; onSelect: (id: string) => void; onEvidence: (id: string) => void;
}) {
  const nodes = state.graph.nodes.filter((node) => !node.isDraft);
  const claims = nodes.filter((node) => node.label === "Claim");
  const artifacts = nodes.filter((node) => node.label === "Artifact");
  const anchors = nodes.filter((node) => node.label === "IdentityAnchor");
  const person = nodes.find((node) => node.label === "Person");
  const pillars = nodes.filter((node) => node.label === "DomainPillar");
  const evidenceCount = new Set(nodes.flatMap((node) => node.evidence_ids || [])).size;
  const score = state.judgment?.score;
  return <section aria-label="AI research dossier" className="space-y-8 p-5 lg:px-12 lg:py-10">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="eyebrow mb-2">Public professional dossier</p>
      <h2 className="font-editorial text-4xl sm:text-5xl tracking-tight text-ink">{person?.title || state.target || "A person. A trail of evidence."}</h2>
      <p className="mt-2 max-w-xl text-xs leading-relaxed text-muted">{state.target ? "Collected sources, attributed claims and an assessment you can inspect." : "Start with a name and context, or a public profile. Follow each finding back to its source."}</p></div>
      {state.judgment && <span className="rounded-full border border-violet-400/20 bg-violet-500/5 px-3 py-1 text-[10px] text-inference">{state.judgment.archetype.replaceAll("_", " / ")}</span>}
    </div>
    <ProfessionalPortrait sections={state.portrait} onEvidence={onEvidence} />
    <div className="grid grid-cols-3 gap-3">{[{ value: evidenceCount, title: "Captured outputs" }, { value: anchors.length, title: "Identity anchors" }, { value: artifacts.length, title: "Public artifacts" }].map((stat) =>
      <div key={stat.title} className="border-t border-line py-4"><p className="font-mono text-2xl text-ink">{String(stat.value).padStart(2, "0")}</p><p className="mt-1 text-[10px] text-muted">{stat.title}</p></div>)}</div>
    <div className="grid gap-4 xl:grid-cols-[1.35fr_1fr]">
      <article className="border-t border-line py-5">
        <div className="mb-5 flex items-center gap-2 text-xs text-inference"><ShieldCheck size={15} /><h3>AI assessment</h3><span className="ml-auto text-[10px] text-muted">Interpretation</span></div>
        <div className="flex items-end justify-between gap-3"><div><p className="eyebrow">Signal-to-hype estimate</p><p className="mt-2 font-mono text-4xl tracking-tight text-ink">{score ?? "—"}<span className="ml-1 text-sm text-muted">/ 100</span></p></div>
          <span className="max-w-28 text-right text-[10px] leading-relaxed text-muted">{score == null ? "Not enough assessed evidence" : "A model estimate, not a credibility rating"}</span></div>
        <div className="my-5 h-1 overflow-hidden rounded-full bg-wash"><div className="h-full rounded-full bg-accent transition-all duration-700" style={{ width: `${Math.min(100, Math.max(0, score ?? 0))}%` }} /></div>
        {state.judgment ? <details className="group"><summary className="text-xs text-ink">Read assessment summary</summary><p className="mt-3 whitespace-pre-wrap text-xs leading-6 text-muted">{state.judgment.summary}</p></details>
          : <p className="text-xs leading-relaxed text-muted">The assessment appears after source collection. Missing evidence stays visible.</p>}
        {state.judgment && !state.committed && <p className="mt-3 text-[10px] text-warning">Proposed assessment · not yet committed</p>}
      </article>
      <article className="border-t border-line py-5"><h3 className="mb-5 flex items-center gap-2 text-xs text-accent"><Fingerprint size={15} />Identity provenance</h3>
        {anchors.length ? <div className="space-y-3">{anchors.map((anchor) => <button key={anchor.id} onClick={() => onSelect(anchor.id)} className="block w-full rounded-lg border border-line bg-wash p-3 text-left hover:border-cyan-500/30"><span className="mb-2 flex items-center justify-between gap-2 text-xs text-ink">{anchor.title}<ArrowUpRight size={12} /></span><TrustBadge node={anchor} /></button>)}</div>
          : <p className="text-xs leading-relaxed text-muted">Public profiles will appear with their evidence status. Selecting an identity does not verify its claims.</p>}
      </article>
    </div>
    {pillars.length > 0 && <div className="flex flex-wrap items-center gap-2"><span className="eyebrow mr-2">Research themes</span>{pillars.map((pillar) => <button key={pillar.id} onClick={() => onSelect(pillar.id)} className="rounded-full border border-violet-400/15 bg-violet-500/5 px-3 py-1.5 text-[11px] text-inference hover:border-violet-400/50">{pillar.title}</button>)}</div>}
    <article className="overflow-hidden border-y border-line"><div className="flex items-center justify-between border-b border-line p-4"><h3 className="flex items-center gap-2 text-xs text-ink"><Layers size={14} className="text-accent" />Claims & supporting sources</h3><span className="font-mono text-[10px] text-muted">{claims.length} claims</span></div>
      {!claims.length ? <p className="p-6 text-xs leading-relaxed text-muted">Claims and their linked evidence will appear after the graph assessment.</p> : <div className="divide-y divide-line">{claims.map((claim) => {
        const links = state.graph.links.filter((link) => link.source === claim.id && ["PROVEN_BY", "CONTRADICTED_BY"].includes(link.relationship));
        return <div key={claim.id} className="grid gap-4 p-4 sm:grid-cols-2"><div><p className="eyebrow mb-2">Claim</p><button onClick={() => onSelect(claim.id)} className="text-left text-sm text-ink hover:text-accent">{claim.title}</button><p className="my-2 text-xs leading-relaxed text-muted">{claim.summary}</p><TrustBadge node={claim} /></div>
          <div className="rounded-lg border border-line bg-wash p-3"><p className="eyebrow mb-2">Linked evidence</p>{links.length ? links.map((link) => {
            const evidence = nodes.find((node) => node.id === link.target);
            return <div key={`${link.target}-${link.relationship}`} className="mb-3 last:mb-0"><p className={`mb-1 text-[10px] ${link.relationship === "CONTRADICTED_BY" ? "text-danger" : "text-accent"}`}>{link.relationship === "CONTRADICTED_BY" ? "Contradicting source" : "Supporting source"}</p><button onClick={() => onSelect(link.target)} className="text-left text-xs text-ink hover:underline">{evidence?.title || link.target} ↗</button><p className="mt-1 text-[11px] leading-relaxed text-muted">{link.reason}</p></div>;
          }) : <p className="text-xs text-muted">No supporting or contradicting edge recorded.</p>}
          {claim.confidence != null && <p className="mt-3 text-[10px] text-muted">Model confidence: {Math.round(claim.confidence * 100)}%</p>}
          {claim.evidence_ids?.map((id, index) => <button key={id} onClick={() => onEvidence(id)} className="mr-3 mt-2 text-[10px] text-accent hover:underline">Captured output {index + 1}</button>)}
          </div></div>;
      })}</div>}
    </article>
    {artifacts.length > 0 && <div><p className="eyebrow mb-3">Public artifacts</p><div className="grid gap-3 sm:grid-cols-2">{artifacts.map((artifact) => <button key={artifact.id} onClick={() => onSelect(artifact.id)} className="border-t border-line py-5 text-left transition hover:border-emerald-400/30"><span className="mb-3 flex items-center justify-between"><TrustBadge node={artifact} /><ArrowUpRight size={14} className="text-muted" /></span><span className="block text-sm text-ink">{artifact.title}</span><span className="mt-2 line-clamp-3 block text-xs leading-relaxed text-muted">{artifact.summary}</span></button>)}</div></div>}
  </section>;
}
