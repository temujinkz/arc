import { useState, type ReactNode } from "react";
import { people, type GLink, type GNode } from "./data";
import type { LivePerson, LivePrompt } from "./live";
import type { Tone } from "./profileData";
import { dirKey, type Directory, type Who } from "./directory";

// One card layout for everything you can click: a person, a company, a role area or someone found by search.
// Same order every time: header and status, facts, claims, sources, connections, footer. Data only, no prose.

type Actions = {
  lookup: (name: string) => void;
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
  claims: { text: string; label: string; tone: Tone }[];
  sources: { label: string; url?: string; note?: string }[];
  rows: Row[];
  live?: LivePerson;
  lookup?: string; // name to look up, when this person has not been researched yet
  person?: Who;    // who to ask the people directory about
};

function Swatch({ node }: { node?: GNode }) {
  if (!node) return null;
  return node.kind === "company" ? <i className="dot sq" /> : <i className="dot" style={{ background: node.hue ?? "var(--faint)" }} />;
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

const money = (n: number) => `$${n.toFixed(3)}`;
const when = (s: string | null) => (s ? s.slice(0, 7) : "");

// Apollo through the backend. Matching is free. Anything with a price needs a second, confirming click.
function ApolloSection({ person, dir }: { person: Who; dir: Directory }) {
  const key = dirKey(person);
  const v = dir.views[key], busy = dir.busy[key], err = dir.errors[key];
  const [ask, setAsk] = useState<null | "email" | "phone">(null);
  if (!dir.status?.configured) return null;
  const price = dir.status.price_usd;
  const emailPrice = price.profile_and_email, phonePrice = price.profile_and_email + price.phone_extra;
  const revealed = !!v?.contact;
  const phones = v?.contact?.phones ?? [];

  const buy = (kind: "email" | "phone") => (
    ask === kind ? (
      <span className="confirm">
        <button className="go" disabled={!!busy} onClick={() => { setAsk(null); void dir.reveal(person, kind === "phone"); }}>Confirm {money(kind === "phone" ? phonePrice : emailPrice)}</button>
        <button className="ghost" onClick={() => setAsk(null)}>Cancel</button>
      </span>
    ) : (
      <button className="ghost" disabled={!!busy} onClick={() => setAsk(kind)}>
        {kind === "email" ? "Get profile and work email" : "Get phone number"} <span className="price">{money(kind === "phone" ? phonePrice : emailPrice)}</span>
      </button>
    )
  );

  return (
    <section className="apollo"><h4>Apollo</h4>
      {!v ? <p className="fine"><i className="spin" /> Checking Apollo</p>
        : !v.found ? <p className="fine">Not in Apollo.</p>
        : (
          <>
            {v.preview && <p className="match"><b>{v.preview.title}</b> at {v.preview.company}</p>}
            {!revealed && (
              <>
                <p className="fine">{v.preview?.has_email ? "Email on file." : "No email on file."} {v.preview?.has_phone ? "A phone number may be available." : ""}</p>
                {buy("email")}
              </>
            )}
            {revealed && (
              <ul className="contact">
                {(v.contact!.emails.length ? v.contact!.emails : []).map((e) => <li key={e.value}><span>Email</span><a href={`mailto:${e.value}`}>{e.value}</a>{e.status && <small>{e.status}</small>}<button className="copy" onClick={() => void navigator.clipboard?.writeText(e.value)}>Copy</button></li>)}
                {!v.contact!.emails.length && <li><span>Email</span><em>None on file</em></li>}
                {v.contact!.personal_emails.map((e) => <li key={e}><span>Personal</span><a href={`mailto:${e}`}>{e}</a></li>)}
                {phones.map((ph) => <li key={ph.value}><span>Phone</span><a href={`tel:${ph.value}`}>{ph.value}</a>{ph.type && <small>{ph.type}</small>}<button className="copy" onClick={() => void navigator.clipboard?.writeText(ph.value)}>Copy</button></li>)}
                {!phones.length && v.contact!.phone_pending && <li><span>Phone</span><em><i className="spin" /> Apollo is finding one. This takes a few minutes.</em></li>}
              </ul>
            )}
            {revealed && !phones.length && !v.contact!.phone_pending && <div className="row">{buy("phone")}</div>}
          </>
        )}
      {busy && <p className="fine"><i className="spin" /> {busy === "phone" ? "Asking Apollo for a phone number" : "Getting the profile"}</p>}
      {err && <p className="warnline">{err}</p>}
      {v?.profile && (
        <div className="profile-data">
          {(v.profile.headline || v.profile.location) && <p className="fine">{[v.profile.headline, v.profile.location].filter(Boolean).join(". ")}</p>}
          <ul className="chips">
            {v.profile.linkedin_url && <li><a href={v.profile.linkedin_url.replace(/^http:/, "https:")} target="_blank" rel="noreferrer noopener">LinkedIn</a></li>}
            {v.profile.github_url && <li><a href={v.profile.github_url} target="_blank" rel="noreferrer noopener">GitHub</a></li>}
            {v.profile.twitter_url && <li><a href={v.profile.twitter_url} target="_blank" rel="noreferrer noopener">X</a></li>}
          </ul>
          {v.profile.history.length > 0 && (
            <ul className="exp">{v.profile.history.map((h, i) => (
              <li key={i}><b>{h.title || "Role"}</b><span>{h.company}</span><small>{when(h.start)}{h.start ? " to " : ""}{h.current ? "now" : when(h.end)}</small></li>
            ))}</ul>
          )}
          {v.profile.company && <p className="fine">{[v.profile.company.industry, v.profile.company.employees ? `${v.profile.company.employees} people` : null, v.profile.company.stage].filter(Boolean).join(", ")}</p>}
        </div>
      )}
      {dir.status && <p className="fine">Spent {money(dir.status.spent_usd)} of the {money(dir.status.cap_usd)} limit.</p>}
    </section>
  );
}

function build(id: string, nodes: GNode[], links: GLink[], live: LivePerson[]): Card | null {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const node = byId.get(id);
  // Someone in the record who has been looked up is matched by name, so their card gains the results.
  const lp = live.find((x) => x.id === id) ?? (node ? live.find((x) => x.name.toLowerCase() === node.label.toLowerCase()) : undefined);
  if (!node && !lp) return null;
  const nbrs = (nid: string) => links.filter((l) => l.source === nid || l.target === nid).map((l) => ({ id: l.source === nid ? l.target : l.source, rel: l.rel }));
  const direct = nbrs(id).filter((n, i, a) => a.findIndex((x) => x.id === n.id) === i);
  const rows: Row[] = direct.filter((d) => byId.has(d.id)).map((d) => ({ id: d.id, label: byId.get(d.id)!.label, note: d.rel }));
  const working = !!lp && (lp.status === "starting" || lp.status === "running");
  const pr = lp?.profile;

  const liveBadge = (): Card["badge"] | null => !lp ? null : working ? { label: "Researching", tone: "live" } : lp.status === "complete" ? (pr?.verdict ?? { label: "Done", tone: "muted" }) : { label: "Not added", tone: "bad" };
  const found = (pr?.sources ?? []).map((s) => ({ label: s.label, url: s.url, note: s.kind }));

  // Someone who only exists because of a search.
  if (!node || (lp && lp.id === id && !people.some((x) => x.id === id))) {
    const l = lp!;
    const company = pr?.orgs.map((o) => o.label).join(", ");
    return {
      eyebrow: "Added by search",
      title: l.name,
      headline: working ? l.note : l.status === "complete" ? (company || `${pr?.claims.length ?? 0} claims, ${pr?.sources.length ?? 0} sources`) : l.error || "The search was stopped.",
      person: pr?.orgs[0] ? { name: l.name, company: pr.orgs[0].label } : undefined,
      badge: liveBadge()!,
      facts: [["Company", company || "Not found"], ["Sources", String(pr?.sources.length ?? l.signals)], ["Claims checked", String(pr?.claims.length ?? 0)]],
      claims: pr?.claims ?? [],
      sources: found,
      rows,
      live: l,
    };
  }

  const n = node;
  if (n.kind === "person") {
    const p = people.find((x) => x.id === n.id);
    const company = direct.map((d) => byId.get(d.id)).find((x) => x?.kind === "company");
    const colleagues = company ? nbrs(company.id).filter((x) => x.id !== id && byId.get(x.id)?.kind === "person").map((x) => ({ id: x.id, label: byId.get(x.id)!.label, note: `also at ${company.label}` })) : [];
    return {
      eyebrow: company?.label ?? "Person",
      title: n.label,
      headline: p ? `${p.role}, ${p.company}` : (n.sub ?? ""),
      person: p ? { name: n.label, company: p.company } : undefined,
      badge: liveBadge() ?? { label: "From one source", tone: "mid" },
      facts: [["Role", p?.role ?? "Not known"], ["Company", p?.company ?? "Not known"], ["Area", p?.area ?? "Not known"]],
      claims: pr?.claims ?? [],
      sources: [{ label: "First round judges slide", note: "event slide" }, ...found],
      rows: [...rows, ...colleagues],
      live: lp,
      lookup: lp ? undefined : n.label,
    };
  }

  const members = direct.map((d) => byId.get(d.id)).filter((x): x is GNode => x?.kind === "person");
  const isCompany = n.kind === "company";
  return {
    eyebrow: isCompany ? "Company" : "Role area",
    title: n.label,
    headline: `${members.length} ${members.length === 1 ? "person" : "people"}`,
    badge: { label: "From the record", tone: "muted" },
    facts: [["People", String(members.length)], ["Source", "Judges slide"]],
    claims: [],
    sources: [],
    rows: members.map((m) => ({ id: m.id, label: m.label, note: m.sub?.split(",")[0] ?? "" })),
  };
}

export function Profile({ id, nodes, links, live, actions, dir, onSelect, onClose }: {
  id: string | null; nodes: GNode[]; links: GLink[]; live: LivePerson[]; actions: Actions; dir: Directory; onSelect: (id: string) => void; onClose: () => void;
}) {
  const card = id ? build(id, nodes, links, live) : null;
  const byId = new Map(nodes.map((n) => [n.id, n]));

  if (!card) {
    return (
      <aside className="pcard empty" aria-live="polite">
        <p className="eyebrow">Profile</p>
        <h3>Pick someone</h3>
        <p className="fine">Click a name on the graph or in the list. Esc closes a profile.</p>
      </aside>
    );
  }

  const lp = card.live;
  const working = !!lp && (lp.status === "starting" || lp.status === "running");
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

      {card.person && <ApolloSection person={card.person} dir={dir} />}

      {card.claims.length > 0 && (
        <section><h4>Claims checked</h4>
          <ul className="claims">{card.claims.map((c, i) => (
            <li key={i}><p>{c.text}</p><span className={`badge ${c.tone}`}>{c.label}</span></li>
          ))}</ul>
        </section>
      )}

      {card.sources.length > 0 && (
        <section><h4>Sources</h4>
          <ul className="srcs">{card.sources.map((s, i) => (
            <li key={i}>{s.url ? <a href={s.url} target="_blank" rel="noreferrer noopener">{s.label}</a> : <span>{s.label}</span>}{s.note && <small>{s.note}</small>}</li>
          ))}</ul>
        </section>
      )}

      {card.rows.length > 0 && (
        <section><h4>Connected to</h4>
          <ul className="conns">{card.rows.map((r) => (
            <li key={r.id}><button onClick={() => onSelect(r.id)}><span><Swatch node={byId.get(r.id)} />{r.label}</span><small>{r.note}</small></button></li>
          ))}</ul>
        </section>
      )}

      <footer>
        {card.lookup && <button className="ghost" onClick={() => actions.lookup(card.lookup!)}>Look up public sources</button>}
        {lp && working && lp.sessionId && <button className="ghost" onClick={() => actions.stop(lp)}>Stop</button>}
        {lp && (lp.status === "failed" || lp.status === "cancelled") && <button className="ghost" onClick={() => actions.retry(lp.id, lp.name)}>Try again</button>}
        <a className="dispute" href="#principles">Dispute or ask for a change</a>
        <p className="fine">Some of this may not be verified.</p>
      </footer>
    </aside>
  );
}
