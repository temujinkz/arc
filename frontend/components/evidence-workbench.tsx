"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, Download, Search, RefreshCw, Plus, Database, LoaderCircle } from "lucide-react";
import { API_BASE } from "@/lib/use-investigation";
import { safeSourceUrl, type InvestigationState } from "@/lib/investigation";
import { toolLabel } from "@/lib/presentation";
import type { EvidenceLedger, SourceOption } from "@/lib/evidence-ledger";

const SITES = ["GitHub", "GitLab", "BitBucket", "Docker Hub", "Hugging Face", "Kaggle", "PyPi", "npm"];
const inputClass = "w-full rounded-lg border border-zinc-700 bg-[#101215] px-3 py-2.5 text-base text-zinc-100 placeholder:text-zinc-500 focus:border-emerald-400";
function date(value: string | null) {
  if (!value) return "Not recorded";
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(time);
}

export function EvidenceWorkbench({ state, onCreate, onCollect, onEvidence, onSelectNode }: {
  state: InvestigationState; onCreate: (target: string) => Promise<boolean>;
  onCollect: (tool: string, args: Record<string, unknown>, fresh: boolean) => Promise<boolean>;
  onEvidence: (id: string) => void; onSelectNode: (id: string) => void;
}) {
  const [sources, setSources] = useState<SourceOption[]>([]);
  const [ledger, setLedger] = useState<EvidenceLedger | null>(null);
  const [problem, setProblem] = useState({ sessionId: "", message: "" });
  const [catalogError, setCatalogError] = useState("");
  const [revision, setRevision] = useState(0);
  const [target, setTarget] = useState("");
  const [source, setSource] = useState("search_public_web");
  const [value, setValue] = useState("");
  const [backend, setBackend] = useState("auto");
  const [recordType, setRecordType] = useState("A");
  const [limit, setLimit] = useState("");
  const [sites, setSites] = useState(["GitHub", "GitLab"]);
  const [fresh, setFresh] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [filter, setFilter] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const valueField = useRef<HTMLInputElement>(null);
  const busy = submitting || state.status === "starting" || state.status === "running";
  const saved = ledger?.session_id === state.sessionId ? ledger : null;
  const error = problem.sessionId === state.sessionId ? problem.message : "";
  const option = sources.find((item) => item.tool === source);
  const kind = option?.input_kind || "query";
  const sourceRevision = state.logs.filter((event) => ["tool.finished", "tool.failed", "graph.draft_node", "graph.final_commit", "source.complete", "source.failed", "source.cancelled"].includes(event.type)).length;

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE}/api/sources/catalog`, { signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error();
      setSources((await response.json()).sources); setCatalogError("");
    }).catch(() => { if (!controller.signal.aborted) setCatalogError("Source availability could not be loaded. Check the API connection."); });
    return () => controller.abort();
  }, [revision]);

  useEffect(() => {
    if (!state.sessionId) return;
    const sessionId = state.sessionId;
    const controller = new AbortController();
    fetch(`${API_BASE}/api/investigate/${encodeURIComponent(sessionId)}/ledger`, { signal: controller.signal, cache: "no-store" }).then(async (response) => {
      if (!response.ok) throw new Error();
      setLedger(await response.json()); setProblem({ sessionId, message: "" });
    }).catch(() => { if (!controller.signal.aborted) setProblem({ sessionId, message: "Saved evidence could not be loaded. Reload records to try again; this will not repeat a source lookup." }); });
    return () => controller.abort();
  }, [state.sessionId, sourceRevision, state.status, revision]);

  const records = (saved?.records || []).filter((record) =>
    [record.source, record.evidence_id, JSON.stringify(record.inputs), ...record.links.map((link) => link.url)].join(" ").toLowerCase().includes(filter.toLowerCase()));
  const selected = records.find((item) => item.evidence_id === selectedId) || records[0];
  const label = kind === "username" ? "Sourced username" : kind === "dns" || kind === "domain_limit" ? "Public project or company domain" : "Public research query";
  const chooseDomain = (url: string) => {
    const safe = safeSourceUrl(url);
    if (!safe) return;
    setSource("inspect_domain_dns"); setValue(new URL(safe).hostname); setFresh(false);
    valueField.current?.focus();
  };

  return <div className="mx-auto w-full max-w-[1500px] px-5 py-6 sm:px-8 sm:py-8">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div><h2 className="text-2xl font-semibold tracking-tight text-zinc-100">Evidence workbench</h2>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-zinc-400">Follow public sources, inspect what was captured, and keep every finding attached to its origin.</p></div>
      {state.sessionId && <div className="flex flex-wrap gap-3 text-sm">
        <a href="#free-source-collection" className="inline-flex items-center gap-2 rounded-lg border border-zinc-700 px-3 py-2 text-emerald-300 hover:bg-white/5 xl:hidden"><Plus size={14} />Collect a source</a>
        <button onClick={() => setRevision((n) => n + 1)} className="inline-flex items-center gap-2 rounded-lg px-2 py-2 text-zinc-300 hover:bg-white/5"><RefreshCw size={14} />Reload records</button>
        <a href={`${API_BASE}/api/investigate/${encodeURIComponent(state.sessionId)}/exports/report.json`} className="inline-flex items-center gap-2 rounded-lg border border-zinc-700 px-3 py-2 text-zinc-200 hover:bg-white/5"><Download size={14} />Evidence JSON</a>
        <a href={`${API_BASE}/api/investigate/${encodeURIComponent(state.sessionId)}/exports/report.md`} className="inline-flex items-center gap-2 rounded-lg border border-zinc-700 px-3 py-2 text-zinc-200 hover:bg-white/5"><Download size={14} />Report</a>
      </div>}
    </div>

    {!state.sessionId ? <div className="mt-10 max-w-xl border-t border-zinc-800 pt-7">
      <Database size={24} className="mb-4 text-emerald-400" /><h3 className="text-lg font-medium">Start with an evidence case</h3>
      <p className="mt-3 text-sm leading-relaxed text-zinc-400">Create a saved workspace for a person, project or professional question. Then choose each free lookup yourself. Creating a case does not call a model or collect a source.</p>
      <form className="mt-6 space-y-4" onSubmit={async (event) => { event.preventDefault(); if (busy || !target.trim()) return; setSubmitting(true); try { await onCreate(target); } finally { setSubmitting(false); } }}>
        <label className="block text-sm text-zinc-300">Case name or research question<input value={target} onChange={(event) => setTarget(event.target.value)} maxLength={1000} required disabled={busy} className={`${inputClass} mt-2`} placeholder="A public professional identity or project" /></label>
        <button disabled={busy || !target.trim()} className="primary-button inline-flex items-center gap-2 py-3 text-sm">{busy ? <LoaderCircle size={15} className="activity-spin" /> : <Plus size={15} />}Create evidence case</button>
      </form>
    </div> : <>
      {saved && <p className="mt-6 border-y border-zinc-800 py-4 text-sm leading-relaxed text-zinc-300">
        Records: {saved.summary.evidence_records} · Source tools: {saved.summary.source_tools_with_evidence} · Distinct URLs: {saved.summary.unique_source_urls} · Reused snapshots: {saved.summary.reused_records}.
        <span className="mt-1 block text-zinc-400">{saved.assessment.present ? `${saved.summary.added_after_assessment} records added after the last assessment. New evidence does not automatically revise the profile.` : "No model assessment has been committed. Collected leads have not been independently attributed to a person."}</span>
      </p>}
      {error && <p role="alert" className="mt-5 text-sm text-amber-300">{error}</p>}
      <div className="mt-7 grid min-w-0 gap-8 xl:grid-cols-[minmax(0,1fr)_310px]">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="text-base font-medium text-zinc-200">Source records</h3>
            <label className="flex min-w-0 items-center gap-2 text-zinc-400"><Search size={15} /><input aria-label="Filter source records" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter sources, queries or URLs" className="w-full max-w-72 border-b border-zinc-700 bg-transparent py-2 text-sm text-zinc-200 placeholder:text-zinc-500" /></label></div>
          {!saved && !error ? <p role="status" className="mt-6 text-sm text-zinc-400">Loading saved source records…</p> : !records.length ? <div className="border-b border-zinc-800 py-10 text-sm leading-relaxed text-zinc-400">{filter ? "No saved records match this filter." : "No source evidence yet. Choose a free source and run a lookup. A failed or unchecked source is never evidence of absence."}</div> : <>
            <div className="mt-4 overflow-x-auto"><table className="w-full text-left text-sm"><caption className="sr-only">Captured source evidence and original collection dates</caption>
              <thead className="border-b border-zinc-700 text-zinc-400"><tr><th className="py-3 pr-4 font-normal">Source</th><th className="py-3 pr-4 font-normal">Collected</th><th className="py-3 font-normal">Assessment</th></tr></thead>
              <tbody>{records.map((record) => <tr key={record.evidence_id} className={`border-b border-zinc-800 ${record.evidence_id === selected?.evidence_id ? "bg-white/5" : "hover:bg-white/3"}`}>
                <td className="py-3 pr-4"><button onClick={() => setSelectedId(record.evidence_id)} aria-pressed={record.evidence_id === selected?.evidence_id} className="text-left font-medium text-emerald-300 hover:underline">{toolLabel(record.source)}</button><p className="mt-1 text-xs text-zinc-400">{record.links.length} source URLs · {record.reused ? "Reused snapshot" : "New collection"}</p></td>
                <td className="py-3 pr-4 text-xs text-zinc-300">{date(record.observed_at)}</td><td className="py-3 text-xs text-zinc-300">{record.added_after_assessment ? "Added after assessment" : record.included_in_assessment ? "Cited by assessment" : "Unassessed source"}</td>
              </tr>)}</tbody></table></div>
            {selected && <section aria-label="Evidence provenance" className="mt-7 min-w-0 border-b border-zinc-800 pb-7">
              <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="text-base font-medium">{toolLabel(selected.source)}</h3><button onClick={() => onEvidence(selected.evidence_id)} className="inline-flex items-center gap-2 text-sm text-emerald-300 hover:underline">Inspect captured output<ArrowUpRight size={14} /></button></div>
              <p className="mt-2 break-all text-xs text-zinc-400">Evidence {selected.evidence_id}</p>
              {selected.integrity_matches === false && <p role="alert" className="mt-4 text-sm leading-relaxed text-rose-300">The captured payload no longer matches its recorded digest. It is excluded from future model evidence; inspect the original before relying on these fields.</p>}
              <dl className="mt-5 grid gap-4 text-sm sm:grid-cols-2"><div><dt className="text-zinc-400">Originally collected</dt><dd className="mt-1 text-zinc-200">{date(selected.observed_at)}</dd></div><div><dt className="text-zinc-400">Added to this case</dt><dd className="mt-1 text-zinc-200">{date(selected.retrieved_at)}</dd></div></dl>
              {selected.observations.length > 0 && <details className="mt-5" open><summary className="py-2 text-sm font-medium text-zinc-200">Reported fields</summary><p className="mt-1 text-xs leading-relaxed text-zinc-400">Values as returned by the source, with paths into the captured payload. These are observations, not verified identity claims.</p><dl className="mt-3 divide-y divide-zinc-800">{selected.observations.map((item) => <div key={item.field_path} className="py-3"><dt className="text-xs text-zinc-400">{item.label}</dt><dd className="mt-1 whitespace-pre-wrap break-words text-sm leading-relaxed text-zinc-200">{String(item.value)}<code className="mt-1 block break-all text-[11px] text-zinc-400">{item.field_path}</code></dd></div>)}</dl></details>}
              <details className="mt-5 text-sm"><summary className="py-2 text-zinc-300 hover:text-white">Query, payload integrity and identity links</summary>
                <pre className="mt-3 max-h-48 overflow-auto rounded-lg bg-black/25 p-3 text-xs text-zinc-300">{JSON.stringify(selected.inputs, null, 2)}</pre>
                <p className={`mt-4 ${selected.integrity_matches === false ? "text-rose-300" : "text-zinc-300"}`}>{selected.digest_recorded ? selected.integrity_matches ? "Captured payload digest matches." : "Captured payload digest does not match. Review before use." : "Legacy record: digest computed for this view; no capture-time digest was recorded."}</p>
                <code className="mt-2 block break-all text-xs text-zinc-400">SHA-256 {selected.sha256}</code><p className="mt-2 text-xs leading-relaxed text-zinc-400">A payload checksum preserves provenance; it does not verify the source’s claims.</p>
                {selected.graph_references.length ? <ul className="mt-4 space-y-2">{selected.graph_references.map((node) => <li key={node.node_id}><button onClick={() => onSelectNode(node.node_id)} className="text-left text-emerald-300 hover:underline">{node.title}</button><span className="ml-2 text-xs text-zinc-400">{node.state}</span></li>)}</ul> : <p className="mt-4 text-zinc-400">No committed identity or claim cites this record.</p>}
              </details>
              {selected.comparison && <div className="mt-5 text-sm text-zinc-300"><p>{selected.comparison.changed ? "Payload changed since the previous matching lookup." : "Payload unchanged from the previous matching lookup."}</p><p className="mt-1 text-xs text-zinc-400">{selected.comparison.added_urls.length} URLs added · {selected.comparison.removed_urls.length} removed. Changes describe captured records, not identity certainty.</p><button onClick={() => onEvidence(selected.comparison!.previous_evidence_id)} className="mt-2 text-emerald-300 hover:underline">Inspect previous snapshot</button></div>}
              {selected.links.length > 0 && <details className="mt-5" open><summary className="py-2 text-sm text-zinc-300">Source URLs and available pivots</summary><ul className="mt-2 space-y-4">{selected.links.map((link) => <li key={link.url} className="min-w-0"><a href={safeSourceUrl(link.url)} target="_blank" rel="noreferrer" className="break-all text-sm text-cyan-300 hover:underline">{link.url}</a><p className="mt-1 break-all font-mono text-[11px] text-zinc-400">{link.field_paths.join(", ")}</p>{link.field_paths.some((path) => path !== '$["source_url"]') && <button onClick={() => chooseDomain(link.url)} className="mt-1 text-xs text-zinc-300 hover:text-white hover:underline">Use this returned domain for a DNS lookup</button>}</li>)}</ul></details>}
            </section>}
          </>}
          <section className="mt-8"><h3 className="text-base font-medium">Coverage and source health</h3><p className="mt-2 text-sm leading-relaxed text-zinc-400">Counts describe this case’s attempted tools. They do not measure all available websites or prove a person’s identity.</p>
            <div className="mt-4 divide-y divide-zinc-800">{sources.map((item) => {
              const activity = saved?.source_activity.find((group) => group.tool === `mcp__osint__${item.tool}`);
              return <div key={item.tool} className="flex flex-wrap justify-between gap-2 py-3 text-sm"><span className="text-zinc-300">{item.name}</span><span className={activity?.failed ? "text-amber-300" : "text-zinc-400"}>{activity ? `Lookups: ${activity.attempts} · Captured: ${activity.captured} · Failed: ${activity.failed} · Not captured: ${activity.not_captured} · Unfinished: ${activity.unfinished}` : item.available ? "Not checked" : "Not installed"}</span></div>;
            })}{saved?.source_activity.filter((group) => !sources.some((item) => group.tool === `mcp__osint__${item.tool}`)).map((group) => <div key={group.tool} className="flex flex-wrap justify-between gap-2 py-3 text-sm"><span className="text-zinc-300">{toolLabel(group.tool)}</span><span className="text-zinc-400">{group.captured} captured · {group.failed} failed · {group.unfinished} unfinished</span></div>)}</div>
          </section>
        </div>
        <aside id="free-source-collection" aria-label="Collect a free source" className="min-w-0 border-t border-zinc-800 pt-6 xl:border-t-0 xl:pt-0"><h3 className="text-base font-medium">Collect another source</h3><p className="mt-2 text-sm leading-relaxed text-zinc-400">One explicit lookup at a time. These controls call free sources and preserve their receipts; they do not start a model session.</p>
          {catalogError && <p role="alert" className="mt-4 text-sm text-amber-300">{catalogError}</p>}
          <form className="mt-5 space-y-5" onSubmit={async (event) => { event.preventDefault(); if (busy || !option?.available || !value.trim()) return; setSubmitting(true); const args: Record<string, unknown> = kind === "query" ? { query: value.trim() } : kind === "username" ? { username: value.trim(), sites } : { domain: value.trim() }; if (source === "search_public_web") args.backend = backend; if (kind === "domain_limit") args.result_limit = Number(limit); if (kind === "dns") args.record_type = recordType; try { await onCollect(source, args, fresh); } finally { setSubmitting(false); } }}>
            <fieldset disabled={busy || !sources.length} className="space-y-5">
              <label className="block text-sm text-zinc-300">Source<select value={source} onChange={(event) => { setSource(event.target.value); setValue(""); }} className={`${inputClass} mt-2`}>{sources.map((item) => <option key={item.tool} value={item.tool} disabled={!item.available}>{item.name}{!item.available ? " — not installed" : ""}</option>)}</select></label>
              <label className="block text-sm text-zinc-300">{label}<input ref={valueField} value={value} onChange={(event) => setValue(event.target.value)} required maxLength={1000} placeholder={kind === "query" ? "Name, project or publication with context" : kind === "username" ? "Use a handle supplied by a person or source" : "example.org"} className={`${inputClass} mt-2`} /></label>
              {source === "search_public_web" && <label className="block text-sm text-zinc-300">Search engine<select value={backend} onChange={(event) => setBackend(event.target.value)} className={`${inputClass} mt-2`}><option value="auto">Free metasearch</option><option value="google">Google dork query</option><option value="brave">Brave</option><option value="yahoo">Yahoo</option><option value="duckduckgo">DuckDuckGo</option></select></label>}
              {kind === "username" && <fieldset><legend className="text-sm text-zinc-300">Professional sites</legend><div className="mt-3 grid grid-cols-2 gap-3">{SITES.map((site) => <label key={site} className="flex items-center gap-2 text-xs text-zinc-300"><input type="checkbox" checked={sites.includes(site)} onChange={(event) => setSites(event.target.checked ? [...sites, site] : sites.filter((s) => s !== site))} className="accent-emerald-400" />{site}</label>)}</div><p className="mt-3 text-xs leading-relaxed text-zinc-400">A matching handle is an unverified lead, not an identity match.</p></fieldset>}
              {kind === "domain_limit" && <label className="block text-sm text-zinc-300">Requested result limit<input type="number" min={1} step={1} value={limit} required onChange={(event) => setLimit(event.target.value)} className={`${inputClass} mt-2`} /><span className="mt-2 block text-xs text-zinc-400">A requested sample bound; provider behavior can differ.</span></label>}
              {kind === "dns" && <label className="block text-sm text-zinc-300">Record type<select value={recordType} onChange={(event) => setRecordType(event.target.value)} className={`${inputClass} mt-2`}>{["A", "AAAA", "MX", "NS", "CNAME", "TXT"].map((type) => <option key={type}>{type}</option>)}</select></label>}
              <label className="flex items-start gap-2 text-xs leading-relaxed text-zinc-300"><input type="checkbox" checked={fresh} onChange={(event) => setFresh(event.target.checked)} className="mt-0.5 accent-emerald-400" />Request a fresh snapshot instead of reusing saved evidence</label>
              <button disabled={busy || !option?.available || !value.trim() || kind === "username" && !sites.length} className="primary-button inline-flex w-full items-center justify-center gap-2 py-3 text-sm">{busy ? <LoaderCircle size={15} className="activity-spin" /> : <Search size={15} />}{busy ? "An operation is running…" : "Collect free source"}</button>
            </fieldset>
          </form>
          <div className="mt-7 border-t border-zinc-800 pt-5 text-xs leading-relaxed text-zinc-400">Failed or interrupted lookups are never silently retried. Review the status before requesting fresh sources. Model assessments and paid-source collection use the separate investigation flow.</div>
        </aside>
      </div>
    </>}
  </div>;
}
