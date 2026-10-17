import { network, people } from "./data";
import type { LivePerson } from "./live";

const byId = new Map(network.nodes.map((n) => [n.id, n]));
const neighbours = (id: string) =>
  network.links.filter((l) => l.source === id || l.target === id).map((l) => (l.source === id ? l.target : l.source));

type Row = { id: string; label: string; note: string };

function Swatch({ id }: { id: string }) {
  const n = byId.get(id);
  if (!n) return null;
  return n.kind === "company" ? <i className="dot sq" /> : <i className="dot" style={{ background: n.hue }} />;
}

function Rows({ rows, onSelect }: { rows: Row[]; onSelect: (id: string) => void }) {
  return (
    <ul className="conns">
      {rows.map((r) => (
        <li key={r.id}><button onClick={() => onSelect(r.id)}><span><Swatch id={r.id} />{r.label}</span><small>{r.note}</small></button></li>
      ))}
    </ul>
  );
}

type LiveActions = { retry: (id: string, name: string) => void; answer: (p: LivePerson, requestId: string, selected: string[]) => void; stop: (p: LivePerson) => void };

function LiveProfile({ p, actions }: { p: LivePerson; actions: LiveActions }) {
  const working = p.status === "starting" || p.status === "running";
  return (
    <aside className="profile" key={p.id}>
      <p className="eyebrow">{working ? "Looking them up" : p.status === "complete" ? "Added to the record" : "Not added"}</p>
      <h3>{p.name}</h3>
      {working && <p className="summary live"><i className="spin" />{p.note}</p>}
      {p.status === "complete" && <p className="summary">{p.summary || "The search finished. There was not enough public information to write a summary."}</p>}
      {(p.status === "failed" || p.status === "cancelled") && <p className="summary">{p.error || "The search was stopped."}</p>}

      {p.prompts.map((q) => (
        <div className="ask" key={q.request_id}>
          <p>{q.question}</p>
          <ul className="conns">
            {q.candidates.map((c) => (
              <li key={c.label}><button onClick={() => actions.answer(p, q.request_id, [c.label])}><span>{c.label}</span><small>{c.description}</small></button></li>
            ))}
          </ul>
        </div>
      ))}

      {p.themes.length > 0 && (<><p className="eyebrow">Themes</p><ul className="chips">{p.themes.map((t) => <li key={t}>{t}</li>)}</ul></>)}
      {p.signals > 0 && <p className="eyebrow">{p.signals} {p.signals === 1 ? "signal" : "signals"} found</p>}

      <div className="actions">
        {working && p.sessionId && <button className="ghost" onClick={() => actions.stop(p)}>Stop</button>}
        {(p.status === "failed" || p.status === "cancelled") && <button className="ghost" onClick={() => actions.retry(p.id, p.name)}>Try again</button>}
      </div>
    </aside>
  );
}

export function Profile({ id, onSelect, live, actions }: { id: string | null; onSelect: (id: string) => void; live: LivePerson[]; actions: LiveActions }) {
  const lp = live.find((x) => x.id === id);
  if (lp) return <LiveProfile p={lp} actions={actions} />;
  const node = id ? byId.get(id) : undefined;
  if (!node) {
    return (
      <aside className="profile empty">
        <p className="summary">Pick someone on the graph, or search a name, to see what we know about them.</p>
      </aside>
    );
  }

  const nb = neighbours(node.id).map((x) => byId.get(x)!).filter(Boolean);

  if (node.kind === "person") {
    const p = people.find((x) => x.id === node.id)!;
    const company = nb.find((n) => n.kind === "company");
    const area = nb.find((n) => n.kind === "theme");
    const colleagues = company ? neighbours(company.id).filter((x) => x !== node.id).map((x) => byId.get(x)!).filter((n) => n.kind === "person") : [];
    return (
      <aside className="profile" key={node.id}>
        <p className="eyebrow">{p.company}</p>
        <h3>{p.name}</h3>
        <p className="summary">{p.role} at {p.company}. One of the first round judges.</p>
        <p className="eyebrow">Where it comes from</p>
        <ul className="chips"><li>First round judges slide</li></ul>
        <p className="eyebrow">Connected to</p>
        <Rows onSelect={onSelect} rows={[
          ...(company ? [{ id: company.id, label: company.label, note: "works at" }] : []),
          ...(area ? [{ id: area.id, label: area.label, note: "role" }] : []),
          ...colleagues.map((c) => ({ id: c.id, label: c.label, note: `also at ${p.company}` })),
        ]} />
        <a className="remove" href="#principles">Ask to remove this profile</a>
      </aside>
    );
  }

  const members = nb.filter((n) => n.kind === "person");
  return (
    <aside className="profile" key={node.id}>
      <p className="eyebrow">{node.kind === "company" ? "Company" : "Role area"}</p>
      <h3>{node.label}</h3>
      <p className="summary">{members.length} {members.length === 1 ? "person" : "people"} on the graph {node.kind === "company" ? "work here" : "are in this area"}.</p>
      <p className="eyebrow">People</p>
      <Rows onSelect={onSelect} rows={members.map((m) => ({ id: m.id, label: m.label, note: m.sub?.split(",")[0] ?? "" }))} />
    </aside>
  );
}
