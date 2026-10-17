"use client";

import { useEffect, useRef, useState } from "react";
import { LoaderCircle, Users } from "lucide-react";
import { profileRequest } from "@/lib/profile-api";
import { safeSourceUrl } from "@/lib/investigation";

type PeerClaim = { claim_id: string; author_name: string; author_url: string; relationship: string; statement: string; created_at?: string };

export function PeerJudgments({ profileId }: { profileId: string }) {
  const [claims, setClaims] = useState<PeerClaim[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ author_name: "", author_url: "", relationship: "", statement: "" });
  const attempt = useRef<{ body: string; id: string } | null>(null);
  useEffect(() => {
    let active = true;
    profileRequest(`/api/profiles/${encodeURIComponent(profileId)}/peers`).then(data => { if (active) setClaims(data.claims); })
      .catch(error => { if (active) setError(String(error)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [profileId]);
  return <section aria-label="Peer / Friend Judgments" className="mx-auto max-w-[800px] space-y-6 p-5 lg:p-7">
    <div><h2 className="flex items-center gap-2 text-xl font-medium"><Users size={20} className="text-inference" />Peer / Friend Judgments</h2>
      <p className="mt-3 text-sm leading-relaxed text-muted">Attributed statements from people who know this person. These never become OSINT evidence or ARC inferences. Attribution and relationships are self-reported, not independently verified.</p></div>
    {loading ? <p className="text-xs text-muted">Loading peer judgments…</p> : !claims.length && <p className="text-sm text-muted">No peer judgments yet. Share a specific example from working together.</p>}
    <div className="space-y-4">{claims.map(claim => <article key={claim.claim_id} className="rounded-xl border border-indigo-400/15 p-5">
      <a href={safeSourceUrl(claim.author_url)} target="_blank" rel="noopener noreferrer" className="font-medium text-inference hover:underline">{claim.author_name}</a>
      <p className="mt-1 text-xs text-muted">{claim.relationship} · Self-attributed</p><p className="mt-4 whitespace-pre-wrap text-sm leading-7 text-ink">{claim.statement}</p>
    </article>)}</div>
    <form className="space-y-4 border-t border-line pt-6" onSubmit={async event => {
      event.preventDefault(); if (busy) return; setBusy(true); setError("");
      const body = JSON.stringify(form); if (attempt.current?.body !== body) attempt.current = { body, id: crypto.randomUUID() };
      try { const claim = await profileRequest(`/api/profiles/${encodeURIComponent(profileId)}/peers`, { ...form, claim_id: attempt.current.id }); setClaims(items => [claim, ...items.filter(item => item.claim_id !== claim.claim_id)]); setForm({ author_name: "", author_url: "", relationship: "", statement: "" }); attempt.current = null; }
      catch (error) { setError(String(error)); } finally { setBusy(false); }
    }}><h3 className="text-sm font-medium">Leave a peer judgment</h3>
      <div className="grid gap-3 sm:grid-cols-2">{([{ key: "author_name", label: "Your name", max: 100 }, { key: "author_url", label: "Your public profile URL", max: 2048 }, { key: "relationship", label: "How you know this person", max: 200 }] as const).map(field => <label key={field.key} className="block text-xs text-muted">{field.label}<input required type={field.key === "author_url" ? "url" : "text"} maxLength={field.max} value={form[field.key]} onChange={e => setForm({ ...form, [field.key]: e.target.value })} className="mt-2 w-full rounded-lg border border-line bg-wash p-3 text-ink" /></label>)}</div>
      <label className="block text-xs text-muted">Your experience working with them<textarea required minLength={10} maxLength={3000} rows={4} value={form.statement} onChange={e => setForm({ ...form, statement: e.target.value })} className="mt-2 w-full rounded-lg border border-line bg-wash p-3 text-ink" /></label>
      <p className="text-[11px] text-muted">Your name, profile link, relationship and statement will be public.</p><button disabled={busy} className="primary-button">{busy && <LoaderCircle size={14} className="activity-spin mr-2 inline" />}Publish peer judgment</button>
    </form>{error && <p role="alert" className="text-xs text-danger">{error}</p>}
  </section>;
}
