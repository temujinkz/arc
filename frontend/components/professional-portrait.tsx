import type { PortraitSection } from "@/lib/investigation";

export function ProfessionalPortrait({ sections, onEvidence }: { sections: PortraitSection[]; onEvidence: (id: string) => void }) {
  if (!sections.length) return null;
  return <section aria-label="ARC Inferred Judgments" className="space-y-5">
    <div><h3 className="font-editorial text-3xl text-ink">ARC Inferred Judgments</h3><p className="mt-2 text-xs leading-relaxed text-muted">A professional portrait inferred from public sources. Interpretations and limitations are explicit; friend judgments are separate.</p></div>
    <div className="divide-y divide-line">{sections.map((item) => <article key={item.section} className="py-8 first:pt-5">
      <span className="text-xs font-medium text-inference">ARC inference</span><h4 className="mt-2 font-editorial text-2xl sm:text-3xl text-ink">{item.title}</h4>
      <p className="mt-3 max-w-[72ch] whitespace-pre-wrap text-[15px] leading-7 text-ink">{item.analysis}</p>
      <p className="mt-4 max-w-[80ch] border-l border-amber-400/25 pl-3 text-xs leading-6 text-muted">{item.limitations}</p>
      <div className="mt-4 flex flex-wrap gap-3">{item.evidence_ids.map((id, i) => <button key={id} onClick={() => onEvidence(id)} className="text-xs text-accent hover:underline">Source {i + 1}</button>)}</div>
    </article>)}</div>
  </section>;
}
