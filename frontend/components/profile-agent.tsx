"use client";
import { useState } from "react";
import { LoaderCircle, Send } from "lucide-react";
import { profileRequest } from "@/lib/profile-api";
import { safeSourceUrl } from "@/lib/investigation";

type Reply = { mode: string; message: string; boundary?: string; facts: { id: string; text: string; provenance: string; sources: string[] }[] };
export function ProfileAgent({ profileId, name }: { profileId: string; name: string }) {
  const [question, setQuestion] = useState(""); const [kind, setKind] = useState("question");
  const [reply, setReply] = useState<Reply | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  return <section aria-label="Local agent representative" className="mx-5 my-6 border-t border-white/10 pt-6 lg:mx-7">
    <h2 className="text-lg font-medium">Ask {name}’s agent</h2>
    <p className="mt-2 text-sm leading-6 text-zinc-400">Explore a collaboration using their public dossier and context they approved for sharing. Questions are saved in their local inbox. The agent cannot make commitments for them.</p>
    <form className="mt-4" onSubmit={async (e) => { e.preventDefault(); if (busy) return; setBusy(true); setError(""); setReply(null); try { setReply(await profileRequest(`/api/profiles/${encodeURIComponent(profileId)}/agent`, {request_id: crypto.randomUUID(), kind, question})); } catch (e) { setError(String(e)); } finally { setBusy(false); } }}>
      <label className="text-xs text-zinc-400">Intent <select value={kind} onChange={(e) => setKind(e.target.value)} className="ml-2 rounded border border-white/10 bg-zinc-900 p-2"><option value="question">Ask a question</option><option value="collaboration">Propose collaboration</option></select></label>
      <textarea aria-label="Question or collaboration pitch" required minLength={3} maxLength={3000} rows={3} value={question} onChange={(e) => setQuestion(e.target.value)} className="my-3 w-full rounded-lg border border-white/10 bg-black/20 p-3 text-sm" placeholder="What would you like to build together?" />
      <button disabled={busy || question.trim().length < 3} className="primary-button flex items-center gap-2">{busy ? <LoaderCircle size={14} className="activity-spin" /> : <Send size={14} />}{busy ? "Consulting local agent…" : "Send to agent"}</button>
    </form>
    {error && <p role="alert" className="mt-3 text-sm text-rose-300">{error}</p>}
    {reply && <div role="status" className="mt-5 space-y-4"><p className="text-xs text-emerald-300">{reply.message}</p>{reply.facts.map((fact) => <article key={fact.id}><p className="mb-1 text-[10px] uppercase tracking-wide text-zinc-500">{fact.provenance.replaceAll("_", " ")}</p><p className="whitespace-pre-wrap text-sm leading-6 text-zinc-200">{fact.text}</p>{fact.sources.map((url) => safeSourceUrl(url) && <a key={url} href={safeSourceUrl(url)} target="_blank" rel="noopener noreferrer" className="mt-1 block text-xs text-blue-300">Public source ↗</a>)}</article>)}{reply.boundary && <p className="text-xs text-zinc-500">{reply.boundary}</p>}</div>}
  </section>;
}
