"use client";

import { useRef, useState } from "react";
import { ArrowUp, LoaderCircle } from "lucide-react";

export function ChatComposer({ hasSession, busy, waiting, canFollowUp, onSubmit }: {
  hasSession: boolean; busy: boolean; waiting: boolean; canFollowUp: boolean;
  onSubmit: (text: string, refreshSources: boolean) => Promise<boolean>;
}) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [refreshSources, setRefreshSources] = useState(false);
  const form = useRef<HTMLFormElement>(null);
  const disabled = busy || sending || (hasSession && !canFollowUp);
  return <div className="shrink-0 px-4 pt-2 pb-4 sm:px-8 sm:pb-5"><div className="mx-auto max-w-4xl">
    <form ref={form} onSubmit={async (event) => { event.preventDefault(); if (disabled || !text.trim()) return; setSending(true); try { if (await onSubmit(text, refreshSources)) setText(""); } finally { setSending(false); } }}
      className="rounded-2xl border border-white/10 bg-[#1a1d22] p-3 shadow-xl shadow-black/10 focus-within:border-white/20">
      <label htmlFor="chat-input" className="sr-only">{hasSession ? "Ask about collected evidence" : "Person or public profile to investigate"}</label>
      <textarea id="chat-input" value={text} disabled={disabled} maxLength={hasSession ? 4000 : 1000} rows={2}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); form.current?.requestSubmit(); } }}
        placeholder={waiting ? "Answer the identity question above to continue" : busy ? "ARC is working on your request…" : hasSession ? canFollowUp ? "Ask about the evidence, claims or findings…" : "Start a new investigation to continue" : "A name with context, or a public profile URL…"}
        className="max-h-36 min-h-12 w-full resize-y bg-transparent px-1 py-1 text-sm leading-relaxed text-zinc-200 outline-none placeholder:text-zinc-500 disabled:opacity-60" />
      <div className="flex items-center justify-between gap-3 pt-1"><span className="pl-1 text-[10px] text-zinc-600">{hasSession ? "Collected evidence" : "Public professional research"}</span>
        <button type="submit" disabled={disabled || !text.trim()} aria-label={hasSession ? "Send follow-up" : "Start investigation"} className="grid size-8 shrink-0 place-items-center rounded-full bg-zinc-100 text-zinc-950 transition hover:bg-white disabled:bg-zinc-700 disabled:text-zinc-500">{sending ? <LoaderCircle size={16} className="activity-spin" /> : <ArrowUp size={17} />}</button></div>
    </form>
    {!hasSession && <label className="mt-3 flex items-center justify-center gap-2 text-[11px] text-zinc-400"><input type="checkbox" checked={refreshSources} disabled={disabled} onChange={(event) => setRefreshSources(event.target.checked)} className="accent-emerald-400" />Fetch fresh sources instead of reusing saved results (uses provider credits)</label>}
    <p className="mt-2.5 text-center text-[10px] text-zinc-600">{hasSession ? "Follow-ups use collected evidence and the remaining Claude budget." : "Starting research uses your configured provider credits."}</p>
  </div></div>;
}
