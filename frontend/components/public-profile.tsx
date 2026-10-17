"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { profileRequest } from "@/lib/profile-api";
import { applyEvent, initialState, safeSourceUrl, type GraphPlan } from "@/lib/investigation";
import { ReviewPanel, NodeInspector } from "./review-panel";
import { PeerJudgments } from "./peer-judgments";
import { ClaimJournal } from "./claim-journal";
import { ProfileAgent } from "./profile-agent";
import { EvidenceGraph } from "./evidence-graph";

type Profile = { name: string; summary: string; graph: GraphPlan & { detected_archetype: string; signal_to_hype_score: number | null }; claimed: boolean };
export function PublicProfile({ profileId }: { profileId: string }) {
  const [profile, setProfile] = useState<Profile | null>(null); const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null); const [evidence, setEvidence] = useState<string | null>(null);
  const [view, setView] = useState("dossier");
  useEffect(() => { let active = true; profileRequest(`/api/profiles/${encodeURIComponent(profileId)}`).then((data) => { if (active) setProfile(data); }).catch((e) => { if (active) setError(String(e)); }); return () => { active = false; }; }, [profileId]);
  if (!profile) return <main className="mx-auto max-w-4xl p-8"><Link href="/" className="text-emerald-300">← ARC</Link><p role={error ? "alert" : "status"} className="mt-8 text-zinc-400">{error || "Loading public dossier…"}</p></main>;
  const state = applyEvent({...initialState, target: profile.name, sessionId: profileId, status: "complete", judgment: {archetype: profile.graph.detected_archetype, score: profile.graph.signal_to_hype_score, summary: profile.summary}}, {type: "graph.final_commit", graph: profile.graph});
  const node = state.graph.nodes.find((item) => item.id === selected);
  const sources = state.graph.nodes.filter((item) => item.evidence_ids?.includes(evidence || ""));
  return <main className="min-h-dvh bg-[#101215] text-zinc-100">
    <header className="sticky top-0 z-30 flex flex-wrap items-center gap-5 border-b border-white/5 bg-[#101215]/95 px-6 py-4 backdrop-blur"><Link href="/" className="font-semibold tracking-widest">ARC <span className="text-emerald-400">·</span></Link><span className="mr-auto text-sm text-zinc-400">{profile.name} · public profile</span>{["dossier", "graph", "peers", "agent"].map((item) => <button key={item} onClick={() => setView(item)} className={`text-xs capitalize ${view === item ? "text-emerald-300" : "text-zinc-500"}`}>{item === "peers" ? "Peer / Friend Judgments" : item}</button>)}</header>
    <div className="mx-auto max-w-6xl pb-10">
      {view === "dossier" && <><ReviewPanel state={state} onSelect={setSelected} onEvidence={setEvidence} /><ClaimJournal profileId={profileId} /></>}
      {view === "graph" && <div className="relative h-[70dvh]"><EvidenceGraph graph={state.graph} committed selected={selected} onSelect={setSelected} /></div>}
      {view === "peers" && <PeerJudgments profileId={profileId} />}
      {view === "agent" && <><ProfileAgent profileId={profileId} name={profile.name} /><ClaimJournal profileId={profileId} /></>}
      {node && (view === "graph" || view === "dossier") && <div className="sticky bottom-4 z-20 mx-5 max-w-xl"><NodeInspector node={node} onEvidence={setEvidence} onClose={() => setSelected(null)} /></div>}
      {evidence && <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-5" onClick={() => setEvidence(null)}><section role="dialog" aria-modal="true" aria-label="Public evidence sources" className="glass-card max-h-[70vh] w-full max-w-xl overflow-auto p-6" onClick={(e) => e.stopPropagation()}><div className="flex justify-between"><h2 className="text-sm font-medium">Public source links</h2><button autoFocus onClick={() => setEvidence(null)} className="text-xs text-zinc-400">Close</button></div><p className="my-3 text-xs text-zinc-500">These graph statements cite the selected captured evidence. Links open the original public sources.</p>{sources.map((item) => <div key={item.id} className="mt-3"><p className="text-sm">{item.title}</p>{safeSourceUrl(item.url) && <a href={safeSourceUrl(item.url)} target="_blank" rel="noopener noreferrer" className="break-all text-xs text-blue-300">{item.url} ↗</a>}</div>)}</section></div>}
    </div>
  </main>;
}
