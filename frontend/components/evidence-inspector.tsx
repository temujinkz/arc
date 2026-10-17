import { ExternalLink } from "lucide-react";
import { safeSourceUrl, type EvidenceSpanView } from "@/lib/investigation";

function readablePassage(span: EvidenceSpanView["span"]) {
  // Decode a text field within a captured JSON record without rewriting its words.
  // The parent record and exact parent pointer remain available below.
  if (span.start == null) {
    try {
      const record = JSON.parse(span.text);
      for (const key of ["description", "text", "markdown"]) {
        if (record && typeof record[key] === "string" && record[key].trim()) {
          return { text: record[key] as string, pointer: `${span.pointer}/${key}`, field: key };
        }
      }
    } catch { /* The captured passage is already plain text. */ }
  }
  return { text: span.text, pointer: span.pointer, field: null };
}

export function EvidenceInspector({ passages }: { passages: EvidenceSpanView[] }) {
  return <div className="space-y-8 p-5" aria-label="Exact source passages">
    {passages.map(({ span, claims, unresolved, stop_reason }) => {
      const url = safeSourceUrl(span.url);
      const quote = readablePassage(span);
      return <article key={span.id} className="space-y-4 border-b border-line pb-7 last:border-0">
        <div className="flex flex-wrap gap-2">{[...new Set(claims.map(claim => claim.support))].map(support => <span key={support} className="rounded-full border border-amber-400/25 px-2 py-1 font-mono text-[10px] text-warning">{support}</span>)}
          {!claims.length && <span className="text-xs text-inference">Source for interpretation · {span.source_kind}</span>}
        </div>
        <p className="text-xs text-muted">{quote.field ? `Verbatim ${quote.field} field within captured record` : "Verbatim captured passage"}</p>
        <blockquote className="whitespace-pre-wrap break-words border-l-2 border-emerald-400/40 pl-4 text-sm leading-7 text-ink">{quote.text}</blockquote>
        <dl className="grid grid-cols-[100px_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs">
          <dt className="text-muted">Collected</dt><dd>{span.observed_at || "Collection time unavailable"}</dd>
          <dt className="text-muted">JSON pointer</dt><dd className="break-all font-mono text-ink">{quote.pointer || "/ (root)"}</dd>
          <dt className="text-muted">Text offsets</dt><dd>{span.start == null ? "Whole JSON field / record" : `${span.start}–${span.end} (end exclusive, characters)`}</dd>
          <dt className="text-muted">Origin</dt><dd className="break-all">{span.origin_group || "Not classified"}</dd>
        </dl>
        {quote.field && <details className="text-xs text-muted"><summary className="cursor-pointer">Full captured record · {span.pointer || "/ (root)"}</summary><pre className="mt-3 whitespace-pre-wrap break-all text-[11px] leading-5">{span.text}</pre></details>}
        {url && <a href={url} target="_blank" rel="noopener noreferrer" className="inline-flex max-w-full items-center gap-2 break-all text-xs text-accent hover:underline"><ExternalLink size={13} className="shrink-0" />{url}</a>}
        <details className="text-[11px] text-muted"><summary className="cursor-pointer">Snapshot identity</summary><p className="mt-2 break-all font-mono">SHA-256: {span.snapshot_hash}</p><p className="mt-2 break-all">Passage: {span.id}</p></details>
        {!!claims.length && <div className="space-y-3"><p className="text-xs font-medium text-warning">What remains unresolved</p>{claims.map(claim => <p key={claim.statement} className="text-xs leading-6 text-muted">{claim.rationale}</p>)}</div>}
        {!!unresolved.length && <ul className="list-disc space-y-2 pl-4 text-xs leading-6 text-muted">{unresolved.map(gap => <li key={gap}>{gap}</li>)}</ul>}
        {stop_reason && <p className="text-xs text-muted">Research stopped: {stop_reason.replaceAll("_", " ").toLowerCase()}.</p>}
      </article>;
    })}
  </div>;
}
