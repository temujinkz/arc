import { useEffect, useState, type ReactNode } from "react";
import { people, type GLink, type GNode } from "./data";
import type { LivePerson, LivePrompt } from "./live";
import type { Tone } from "./profileData";

// One card layout for everything you can click: a person, a company, a role area or someone found live.
// Same order every time: header and status, facts, about, evidence, connections, footer.

type Actions = {
  retry: (id: string, name: string) => void;
  answer: (p: LivePerson, prompt: LivePrompt, labels: string[], note?: string) => void;
  stop: (p: LivePerson) => void;
};
type Row = { id: string; label: string; note: string };
type Card = {
  eyebrow: string;
  title: string;
  headline: string;
  badge: { label: string; tone: Tone | "live" };
  facts: [string, ReactNode][];
  about: string;
  claims?: { text: string; label: string; tone: Tone; why: string }[];
  readings?: { title: string; text: string; limits: string; thin: boolean }[];
  sources: { label: string; url?: string; note?: string }[];
  rows: Row[];
  rowsTitle: string;
  live?: LivePerson;
};

function Swatch({ node }: { node?: GNode }) {
  if (!node) return null;
  return node.kind === "company" ? <i className="dot sq" /> : <i className="dot" style={{ background: node.hue ?? "var(--faint)" }} />;
}

function Clamp({ text }: { text: string }) {
  const [more, setMore] = useState(false);
  useEffect(() => setMore(false), [text]);
  if (!text) return <p className="about muted">Nothing written about them yet.</p>;
  const long = text.length > 220;
  return (
    <>
      <p className={`about${long && !more ? " clamp" : ""}`}>{text}</p>
      {long && <button className="more" onClick={() => setMore(!more)}>{more ? "Show less" : "Read more"}</button>}
    </>
  );
}

// "Which of these are you?" A checklist where one, some or all can be chosen.
function Ask({ p, prompt, onAnswer }: { p: LivePerson; prompt: LivePrompt; onAnswer: Actions["answer"] }) {
  const labels = prompt.candidates.map((c) => c.label);
  const [picked, setPicked] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const all = picked.length === labels.length;
  const toggle = (l: string) => setPicked((s) => (s.includes(l) ? s.filter((x) => x !== l) : [...s, l]));
  return (
    <fieldset className="ask">
      <legend>{prompt.question}</legend>
      <label className="check all"><input type="checkbox" checked={all} onChange={() => setPicked(all ? [] : labels)} /><span>Select all ({labels.length})</span></label>
      {prompt.candidates.map((c) => (
        <label className="check" key={c.label}>
          <input type="checkbox" checked={picked.includes(c.label)} onChange={() => toggle(c.label)} />
          <span><b>{c.label}</b>{c.description && <small>{c.description}</small>}</span>
        </label>
      ))}
      {!picked.length && <input className="note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="None of these? Say who you mean" aria-label="Describe who you mean" />}
      <button className="go" disabled={!picked.length && !note.trim()} onClick={() => onAnswer(p, prompt, picked, note)}>
        {picked.length > 1 ? `Continue with ${picked.length}` : "Continue"}
      </button>
    </fieldset>
  );
}

function build(id: string, nodes: GNode[], links: GLink[], live: LivePerson[]): Card | null {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const node = byId.get(id);
  const lp = live.find((x) => x.id === id);
  if (!node && !lp) return null;
  const nbrs = (nid: string) => links.filter((l) => l.source === nid || l.target === nid).map((l) => ({ id: l.source === nid ? l.target : l.source, rel: l.rel }));
  const direct = nbrs(id).filter((n, i, a) => a.findIndex((x) => x.id === n.id) === i);
  const rows: Row[] = direct.filter((d) => byId.has(d.id)).map((d) => ({ id: d.id, label: byId.get(d.id)!.label, note: d.rel }));

  if (lp) {
    const pr = lp.profile;
    const working = lp.status === "starting" || lp.status === "running";
    const coworkers = direct.map((d) => byId.get(d.id)).filter((n): n is GNode => n?.kind === "company")
      .flatMap((c) => nbrs(c.id).filter((x) => x.id !== id && byId.get(x.id)?.kind === "person").map((x) => ({ id: x.id, label: byId.get(x.id)!.label, note: `also at ${c.label}` })));
    return {
      eyebrow: working ? "Looking them up" : lp.status === "complete" ? "Added by search" : "Not added",
      title: lp.name,
      headline: working ? lp.note : lp.status === "complete" ? (pr?.headline ?? "") : lp.error || "The search was stopped.",
      badge: working ? { label: "Researching", tone: "live" } : lp.status === "complete" ? (pr?.verdict ?? { label: "Done", tone: "muted" }) : { label: "Not added", tone: "bad" },
      facts: [
        ["Status", working ? "Researching now" : lp.status === "complete" ? "Added to the record" : "Stopped"],
        ["Sources", pr ? String(pr.sources.length) : lp.signals ? String(lp.signals) : "None yet"],
        ["Claims checked", pr ? String(pr.claims.length) : "None yet"],
      ],
      about: pr?.about ?? "",
      claims: pr?.claims, readings: pr?.readings,
      sources: (pr?.sources ?? []).map((s) => ({ label: s.label, url: s.url, note: s.kind })),
      rows: [...rows, ...coworkers].filter((r, i, a) => a.findIndex((x) => x.id === r.id) === i),
      rowsTitle: "Connected to",
      live: lp,
    };
  }

  const n = node!;
  if (n.kind === "person") {
    const p = people.find((x) => x.id === n.id);
    const company = direct.map((d) => byId.get(d.id)).find((x) => x?.kind === "company");
    const colleagues = company ? nbrs(company.id).filter((x) => x.id !== id && byId.get(x.id)?.kind === "person").map((x) => ({ id: x.id, label: byId.get(x.id)!.label, note: `also at ${company.label}` })) : [];
    return {
      eyebrow: company?.label ?? "Person",
      title: n.label,
      headline: p ? `${p.role} at ${p.company}` : (n.sub ?? ""),
      badge: { label: "From one source", tone: "mid" },
      facts: [["Role", p?.role ?? "Not known"], ["Company", p?.company ?? "Not known"], ["Area", p?.area ?? "Not known"]],
      about: p ? `${p.role} at ${p.company}. One of the first round judges.` : "",
      sources: [{ label: "First round judges slide", note: "the only source so far" }],
      rows: [...rows, ...colleagues],
      rowsTitle: "Connected to",
    };
  }

  const members = direct.map((d) => byId.get(d.id)).filter((x): x is GNode => x?.kind === "person");
  const side = direct.map((d) => byId.get(d.id)).filter((x): x is GNode => !!x && x.kind !== "person");
  const isCompany = n.kind === "company";
  return {
    eyebrow: isCompany ? "Company" : "Role area",
    title: n.label,
    headline: `${members.length} ${members.length === 1 ? "person" : "people"} ${isCompany ? "work here" : "in this area"}`,
    badge: { label: "Summary of the record", tone: "muted" },
    facts: [
      ["People", String(members.length)],
      [isCompany ? "Areas" : "Companies", String(new Set(members.flatMap((m) => nbrs(m.id).map((x) => byId.get(x.id)).filter((x) => x && (isCompany ? x.kind === "theme" : x.kind === "company")).map((x) => x!.id))).size || side.length)],
      ["Source", "First round judges slide"],
    ],
    about: `${n.label} appears in the record through ${members.length} ${members.length === 1 ? "person" : "people"}. Click a name to read their profile.`,
    sources: [{ label: "First round judges slide", note: "the only source so far" }],
    rows: members.map((m) => ({ id: m.id, label: m.label, note: m.sub?.split(",")[0] ?? "" })),
    rowsTitle: "People",
  };
}

export function Profile({ id, nodes, links, live, actions, onSelect, onClose }: {
  id: string | null; nodes: GNode[]; links: GLink[]; live: LivePerson[]; actions: Actions; onSelect: (id: string) => void; onClose: () => void;
}) {
  const card = id ? build(id, nodes, links, live) : null;
  const byId = new Map(nodes.map((n) => [n.id, n]));

  if (!card) {
    return (
      <aside className="pcard empty" aria-live="polite">
        <p className="eyebrow">Profile</p>
        <h3>Pick someone</h3>
        <p className="about muted">Click a name on the graph or in the list, or search above. Their profile appears here.</p>
        <p className="fine">Press Esc to close a profile.</p>
      </aside>
    );
  }

  const lp = card.live;
  const working = lp && (lp.status === "starting" || lp.status === "running");
  return (
    <aside className="pcard" key={id} aria-live="polite">
      <header>
        <div className="top"><p className="eyebrow">{card.eyebrow}</p><button className="x" onClick={onClose} aria-label="Close profile">×</button></div>
        <h3>{card.title}</h3>
        <p className={`headline${working ? " live" : ""}`}>{working && <i className="spin" />}{card.headline}</p>
        <span className={`badge ${card.badge.tone}`}>{card.badge.tone === "live" && <i className="pulse" />}{card.badge.label}</span>
      </header>

      <dl className="facts">{card.facts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>

      {lp?.prompts.map((q) => <Ask key={q.request_id} p={lp} prompt={q} onAnswer={actions.answer} />)}
      {lp?.error && lp.status !== "failed" && <p className="warnline">{lp.error}</p>}

      <section><h4>About</h4><Clamp text={card.about} /></section>

      {card.claims && card.claims.length > 0 && (
        <section><h4>What we checked</h4>
          <ul className="claims">{card.claims.map((c, i) => (
            <li key={i}><p>{c.text}</p><span className={`badge ${c.tone}`}>{c.label}</span>{c.why && <small>{c.why}</small>}</li>
          ))}</ul>
        </section>
      )}

      {card.readings && card.readings.length > 0 && (
        <section><h4>Our reading</h4><p className="fine">Interpretation, not fact. Each one lists its limits.</p>
          {card.readings.map((r, i) => (
            <details key={i} className={r.thin ? "thin" : ""}><summary>{r.title}</summary><p>{r.text}</p>{r.limits && <p className="limits">Limits: {r.limits}</p>}</details>
          ))}
        </section>
      )}

      <section><h4>Evidence</h4>
        {card.sources.length ? (
          <ul className="srcs">{card.sources.map((s, i) => (
            <li key={i}>{s.url ? <a href={s.url} target="_blank" rel="noreferrer noopener">{s.label}</a> : <span>{s.label}</span>}{s.note && <small>{s.note}</small>}</li>
          ))}</ul>
        ) : <p className="about muted">{working ? "Sources show up here as they are found." : "No sources yet."}</p>}
      </section>

      {card.rows.length > 0 && (
        <section><h4>{card.rowsTitle}</h4>
          <ul className="conns">{card.rows.map((r) => (
            <li key={r.id}><button onClick={() => onSelect(r.id)}><span><Swatch node={byId.get(r.id)} />{r.label}</span><small>{r.note}</small></button></li>
          ))}</ul>
        </section>
      )}

      <footer>
        {lp && working && lp.sessionId && <button className="ghost" onClick={() => actions.stop(lp)}>Stop</button>}
        {lp && (lp.status === "failed" || lp.status === "cancelled") && <button className="ghost" onClick={() => actions.retry(lp.id, lp.name)}>Try again</button>}
        <p className="fine">Some of this may not be verified.</p>
        <a className="dispute" href="#principles">Dispute or ask for a change</a>
      </footer>
    </aside>
  );
}
