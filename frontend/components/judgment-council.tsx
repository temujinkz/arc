"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowRight, Check, ChevronDown, Copy, Database, LoaderCircle, RefreshCw } from "lucide-react";
import { API_BASE } from "@/lib/use-investigation";
import type { ArcEvent, InvestigationState, PersonaTake } from "@/lib/investigation";
import "./judgment-council.css";

type Persona = { id: string; name: string; role: string; initials: string; color: string; provocation: string; description: string };
type CouncilData = { personas: Persona[]; evidence_count: number; snapshot: string; ready: boolean; reason: string; remaining_budget_usd: number | null; readings: ArcEvent[] };
const stanceLabel = { impressed: "Won over", intrigued: "Leaning in", skeptical: "Not buying it", withholding: "Holding judgment" };

function Take({ take, persona, onEvidence }: { take: PersonaTake; persona: Persona; onEvidence: (id: string) => void }) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return <article className={`council-take tone-${persona.color}`}>
    <header className="take-byline"><span className="take-initials" aria-hidden="true">{persona.initials}</span><div><h3>{persona.name}</h3><p>{persona.role}</p></div><span className="take-stance">{stanceLabel[take.stance]}</span></header>
    <h4>{take.headline}</h4><p className="take-verdict">{take.verdict}</p>
    <dl className="take-details"><div><dt>The upside</dt><dd>{take.upside}</dd></div><div><dt>The friction</dt><dd>{take.tension}</dd></div></dl>
    <div className="take-question"><div><span>A question worth asking</span><button aria-label={`Copy ${persona.name}'s question`} onClick={async () => {
      try { await navigator.clipboard.writeText(take.question); setCopied(true); setCopyError(false); if (timer.current) clearTimeout(timer.current); timer.current = setTimeout(() => setCopied(false), 2000); }
      catch { setCopyError(true); }
    }}>{copied ? <Check size={15} /> : <Copy size={15} />}{copied ? "Copied" : "Copy"}</button></div><p>{take.question}</p>{copyError && <small role="status">Select the question to copy it manually.</small>}</div>
    <details className="take-receipts"><summary>Show the receipts <span>{take.evidence_ids.length} {take.evidence_ids.length === 1 ? "source record" : "source records"}<ChevronDown size={14} /></span></summary>
      <p>{take.limitations}</p><div>{take.evidence_ids.map((id, index) => <button key={id} onClick={() => onEvidence(id)}><Database size={12} />Open evidence {index + 1}<ArrowRight size={12} /></button>)}</div>
    </details>
  </article>;
}

export function JudgmentCouncil({ state, onJudge, onEvidence, onCollect }: {
  state: InvestigationState; onJudge: (personaIds: string[]) => Promise<boolean>; onEvidence: (id: string) => void; onCollect: () => void;
}) {
  const [data, setData] = useState<CouncilData | null>(null);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [chosen, setChosen] = useState(["jung", "diogenes", "nietzsche", "builder"]);
  const [focus, setFocus] = useState("jung");
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [readingId, setReadingId] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const busy = state.status === "running" || state.status === "starting" || submitted;
  const deliberating = busy && state.phase === "judgment";
  useEffect(() => {
    const controller = new AbortController();
    const path = state.sessionId ? `/api/investigate/${encodeURIComponent(state.sessionId)}/judgments` : "/api/judgments/personas";
    fetch(`${API_BASE}${path}`, { signal: controller.signal }).then(async response => {
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "The council is unavailable.");
      setPersonas(result.personas); setData(state.sessionId ? result : null); setError("");
    }).catch(error => { if (error.name !== "AbortError") setError("Could not load the council. Check the backend connection and try again."); });
    return () => controller.abort();
  }, [state.sessionId, state.status, state.cost, retry]);
  const readings = [...(data?.readings || [])];
  for (const event of state.logs.filter(event => event.type === "judgment.complete")) {
    if (!readings.some(item => item.request_id === event.request_id)) readings.push(event);
  }
  const latest = readings.at(-1);
  const reading = readings.find(item => item.request_id === readingId) || latest;
  const activePersona = personas.find(item => item.id === focus) || personas[0];
  const stale = !!reading && !!data && reading.snapshot !== data.snapshot;
  return <div className="council">
    <div className="council-intro"><div><h2>One person.<br /><em>Very different takes.</em></h2><p>Meet the Council. Four strong opinions. The same evidence.<br className="council-desktop-break" /> Pick your minds and see what survives the conversation.</p></div><span className="council-stamp">THE<br />COUNCIL<span>Agree to disagree.</span></span></div>
    {error && <div role="alert" className="council-error">{error}<button onClick={() => setRetry(value => value + 1)}><RefreshCw size={14} />Try again</button></div>}
    <fieldset className="council-cast"><legend>Choose your judges <span>{chosen.length} selected</span></legend><div className="council-members">{personas.map(persona => {
      const selected = chosen.includes(persona.id);
      return <button key={persona.id} className={`council-member tone-${persona.color} ${selected ? "is-selected" : ""}`} aria-pressed={selected} disabled={busy} onClick={() => {
        setFocus(persona.id); setChosen(items => selected ? items.filter(id => id !== persona.id) : [...items, persona.id]);
      }}><span className="member-top"><span className="member-initials" aria-hidden="true">{persona.initials}</span><span className="member-check" aria-hidden="true">{selected && <Check size={13} />}</span></span><strong>{persona.name}</strong><span className="member-role">{persona.role}</span><span className="member-line">“{persona.provocation}”</span></button>;
    })}</div>{!personas.length && !error && <p role="status" className="council-loading">Taking our seats…</p>}</fieldset>
    <div className="council-run"><div><span><Database size={14} />{data ? `${data.evidence_count} saved ${data.evidence_count === 1 ? "record" : "records"}` : "Start with public evidence"}</span><p>{data?.remaining_budget_usd != null ? `Uses your configured model · $${data.remaining_budget_usd.toFixed(4)} remaining threshold` : "Readings use your configured model and remaining budget."}</p></div>
      <button className="council-submit" disabled={busy || !data?.ready || chosen.length === 0} onClick={async () => {
        setSubmitted(true); setReadingId(""); try { await onJudge(personas.filter(item => chosen.includes(item.id)).map(item => item.id)); } finally { setSubmitted(false); }
      }}>{deliberating ? <><LoaderCircle size={16} className="activity-spin" />Deliberating…</> : <>{reading ? "Call the council again" : "Let them judge"}<ArrowRight size={16} /></>}</button>
    </div>
    {!busy && (!state.sessionId || (data && !data.ready)) && <p className="council-hint">{data?.reason || "Collect a few public sources, then give the council something to disagree about."}{!data?.evidence_count && <button onClick={onCollect}>Open evidence workbench <ArrowRight size={13} /></button>}</p>}
    {chosen.length === 0 && <p className="council-hint" role="status">A quiet room. Pick at least one judge to get a take.</p>}
    <p className="council-disclosure">Starting a reading authorizes a model call. Historical voices are inspired lenses. All takes are AI interpretations of public work.</p>
    {deliberating && <div className="council-deliberating" role="status"><LoaderCircle size={21} className="activity-spin" /><div><strong>Strong opinions are forming.</strong><p>The council is reading the saved evidence. Your previous takes stay below.</p></div></div>}
    {reading?.reading ? <div className="council-results"><div className="council-results-heading"><h3>The verdicts are in.</h3>{readings.length > 1 && <label>Reading<select aria-label="Saved council reading" value={reading.request_id} onChange={event => setReadingId(event.target.value)}>{readings.map((item, index) => <option key={item.request_id} value={item.request_id}>Reading {index + 1}{item.timestamp ? ` · ${new Date(item.timestamp * 1000).toLocaleString()}` : ""}</option>)}</select></label>}</div>
      <p className="council-results-note">{reading.timestamp && new Date(reading.timestamp * 1000).toLocaleString()} · Saved interpretation{stale ? " · Evidence has changed since this reading." : ""}</p>
      <div className="council-takes">{reading.reading.takes.map(take => { const persona = personas.find(item => item.id === take.persona_id); return persona && <Take key={`${reading.request_id}-${take.persona_id}`} take={take} persona={persona} onEvidence={onEvidence} />; })}</div>
    </div> : activePersona && <div className={`council-preview tone-${activePersona.color}`}><div className="preview-word" aria-hidden="true">{activePersona.initials}<span>Perspective changes everything.</span></div><div><span className="preview-role">{activePersona.role}</span><blockquote>“{activePersona.provocation}”</blockquote><p>{activePersona.description}</p><span className="preview-note"><ArrowDown size={14} />The evidence comes first. The opinion comes next.</span></div></div>}
  </div>;
}
