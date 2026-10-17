"use client";
import { useEffect, useState } from "react";
import { LockKeyhole, LoaderCircle } from "lucide-react";
import { profileRequest } from "@/lib/profile-api";

type Journal = { revision: number; approved_revision: number | null; approved_context: string[];
  entries: { entry_id: string; kind: string; text: string; created_at: string }[];
  inbox: { request_id: string; kind: string; question: string; reply: string }[] };
export function ClaimJournal({ profileId }: { profileId: string }) {
  const [journal, setJournal] = useState<Journal | null>(null);
  const [text, setText] = useState(""); const [kind, setKind] = useState("note");
  const [brief, setBrief] = useState(""); const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(""); const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const base = `/api/vault/${encodeURIComponent(profileId)}`;
  const refresh = async () => { const data: Journal = await profileRequest(base); setJournal(data); setBrief(data.approved_context.join("\n")); };
  useEffect(() => { let active = true; profileRequest(base).then((data: Journal) => {
    if (active) { setJournal(data); setBrief(data.approved_context.join("\n")); }
  }).catch(() => {}).finally(() => { if (active) setLoading(false); }); return () => { active = false; }; }, [base]);
  const act = async (operation: () => Promise<void>) => {
    if (busy) return; setBusy(true); setError(""); setMessage("");
    try { await operation(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const input = "mt-2 w-full rounded-lg border border-white/10 bg-black/20 p-3 text-sm text-zinc-200";
  return <section className="mx-5 my-6 rounded-xl border border-emerald-400/15 p-5 lg:mx-7" aria-label="Claim and private journal">
    <h2 className="flex items-center gap-2 text-lg font-medium"><LockKeyhole size={18} className="text-emerald-300" />{journal ? "Private Local Journal" : "Make this profile yours"}</h2>
    {loading ? <p className="mt-3 text-sm text-zinc-500">Checking local ownership…</p> : !journal ? <>
      <p className="my-3 max-w-2xl text-sm leading-6 text-zinc-400">If this is your profile, activate a journal and local representative on this device. This is a self-claim, not verified identity. The browser that claims it controls the private vault.</p>
      <button disabled={busy} className="primary-button" onClick={() => void act(async () => { await profileRequest(`${base}/claim`, {}); await refresh(); })}>Claim This Profile</button>
    </> : <div className="mt-4 space-y-5">
      <p className="text-xs leading-5 text-zinc-500">Stored in a separate private SQLite vault on the ARC server device. Raw notes never enter the public graph. Keep this browser’s owner cookie to retain access.</p>
      <form onSubmit={(e) => { e.preventDefault(); void act(async () => { await profileRequest(`${base}/entries`, {entry_id: crypto.randomUUID(), kind, text}); setText(""); await refresh(); setMessage("Saved privately. Review your shareable context before the agent represents you again."); }); }}>
        <label className="text-xs text-zinc-400">Entry type <select value={kind} onChange={(e) => setKind(e.target.value)} className="ml-3 rounded border border-white/10 bg-zinc-900 p-2"><option value="note">Raw note</option><option value="goal">Current goal</option><option value="context">Private context</option></select></label>
        <textarea aria-label="Private journal entry" className={input} value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={12000} required placeholder="What are you working toward? What should only your local agent know?" />
        <button disabled={busy || !text.trim()} className="primary-button mt-2">Save private entry</button>
      </form>
      <div className="space-y-3">{journal.entries.map((entry) => <details key={entry.entry_id} className="border-b border-white/5 pb-3"><summary className="text-xs text-zinc-400">{entry.kind} · {new Date(entry.created_at).toLocaleString()} · Open private note</summary><p className="my-3 whitespace-pre-wrap text-sm leading-6 text-zinc-200">{entry.text}</p><button disabled={busy} className="text-xs text-rose-300" onClick={() => void act(async () => { await profileRequest(`${base}/entries/${entry.entry_id}`, undefined, "DELETE"); await refresh(); })}>Delete entry</button></details>)}</div>
      <div className="border-t border-white/10 pt-5">
        <h3 className="text-sm font-medium">What your agent may share</h3>
        <p className="my-2 text-xs leading-5 text-zinc-400">Draft privately with your local model, or write your own brief. Review every line: approving makes these statements available to visitors. Editing your journal pauses representation until you approve again.</p>
        <button disabled={busy} className="rounded border border-white/15 px-3 py-2 text-xs text-zinc-300" onClick={() => void act(async () => { const result = await profileRequest(`${base}/prepare`, {}); if (result.available) setBrief(result.statements.join("\n")); setMessage(result.message); })}>Draft from my journal locally</button>
        <textarea aria-label="Approved shareable context" className={input} rows={4} value={brief} onChange={(e) => setBrief(e.target.value)} placeholder="One shareable statement per line. Leave out anything private." />
        <button disabled={busy} className="primary-button mt-2" onClick={() => void act(async () => { await profileRequest(`${base}/representation`, {revision: journal.revision, statements: brief.split("\n").map((s) => s.trim()).filter(Boolean)}); await refresh(); setMessage("Approved. Visitors can now ask your representative using public evidence and these statements."); })}>Approve these statements for visitors</button>
        <p className="mt-2 text-xs text-zinc-500">{journal.approved_revision === journal.revision ? "Representation active · owner-approved context" : "Representation paused · approval required"}</p>
      </div>
      <details><summary className="text-sm text-zinc-400">Agent inbox · {journal.inbox.length} requests</summary>{journal.inbox.map((item) => <div key={item.request_id} className="mt-3 border-t border-white/5 pt-3"><p className="text-xs text-emerald-300">{item.kind}</p><p className="mt-1 whitespace-pre-wrap text-sm">{item.question}</p></div>)}<button className="mt-3 text-xs text-zinc-400 underline" onClick={() => void act(refresh)}>Refresh inbox</button></details>
    </div>}
    {busy && <p role="status" className="mt-3 flex items-center gap-2 text-xs text-zinc-400"><LoaderCircle size={14} className="activity-spin" />Working locally…</p>}
    {message && <p role="status" className="mt-3 text-sm text-emerald-300">{message}</p>}{error && <p role="alert" className="mt-3 text-sm text-rose-300">{error}</p>}
  </section>;
}
