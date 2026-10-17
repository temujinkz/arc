import type { PortraitSection } from "@/lib/investigation";

export function ProfessionalPortrait({ sections, onEvidence }: { sections: PortraitSection[]; onEvidence: (id: string) => void }) {
  if (!sections.length) return null;
  return <section aria-label="ARC Inferred Judgments" className="space-y-5">
    <div><h3 className="text-lg font-medium text-zinc-100">ARC Inferred Judgments</h3><p className="mt-2 text-xs leading-relaxed text-zinc-500">A professional portrait inferred from public sources. Interpretations and limitations are explicit; friend judgments are separate.</p></div>
    <div className="grid gap-4 xl:grid-cols-2">{sections.map((item, index) => <article key={item.section} className={`rounded-xl border border-white/7 bg-white/2 p-5 ${index === 0 ? "xl:col-span-2" : ""}`}>
      <span className="text-[10px] uppercase tracking-wider text-violet-300">ARC inference</span><h4 className="mt-2 text-base font-medium text-zinc-100">{item.title}</h4>
      <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-zinc-300">{item.analysis}</p>
      <p className="mt-4 border-l border-amber-400/25 pl-3 text-xs leading-6 text-zinc-500">{item.limitations}</p>
      <div className="mt-4 flex flex-wrap gap-3">{item.evidence_ids.map((id, i) => <button key={id} onClick={() => onEvidence(id)} className="text-[11px] text-cyan-300 hover:underline">Source {i + 1}</button>)}</div>
    </article>)}</div>
  </section>;
}
