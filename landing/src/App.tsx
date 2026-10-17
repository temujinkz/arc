import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Graph } from "./Graph";
import { Search } from "./Search";
import { Profile } from "./Profile";
import { Brush } from "./Brush";
import { useLive, liveId, slug, type LivePerson } from "./live";
import { HalftoneLogo } from "./HalftoneLogo";
import { Pipeline } from "./Pipeline";
import { Night } from "./Night";
import { scene } from "./scene";
import { areaHue, dossier, kindHue, network, type GLink, type GNode, type Kind } from "./data";

const legend: { kind: Kind; name: string; hint: string }[] = [
  { kind: "person", name: "Person", hint: "Who the profile is about" },
  { kind: "company", name: "Company", hint: "Where they work" },
  { kind: "theme", name: "Theme", hint: "What they keep coming back to" },
  { kind: "artifact", name: "Artifact", hint: "A repo, post, talk or page" },
  { kind: "claim", name: "Claim", hint: "Something said about them, with sources" },
  { kind: "judgment", name: "Inference", hint: "Our own reading, clearly marked" },
];

const glyph: Record<Kind, string> = { person: "●", company: "■", theme: "○", artifact: "□", claim: "◇", judgment: "◌" };

function Mark({ kind, color }: { kind: Kind; color?: string }) {
  return <i className="glyph-mark" style={{ color: color ?? kindHue[kind] }} aria-hidden>{glyph[kind]}</i>;
}

// Each card describes one stage of the real pipeline in arc-engine. Checked against the backend code.
const tech = [
  ["Collect", "An AI agent searches the web, LinkedIn and GitHub. If several people fit a name, it asks which ones are you. Every run has a spending limit.", "FastAPI, Claude Agent SDK, MCP"],
  ["Save the evidence", "Every source is saved with its time and a SHA-256 hash. Claims can only quote exact passages from the saved copies.", "SQLite ledger, SHA-256"],
  ["Check the claims", "A model picks up to three claims. Workers read the saved passages and propose findings, and a critic accepts or rejects each one. A claim counts as independently backed only if a source outside the person's own accounts says so.", "workers, critic, support levels"],
  ["Draw the graph", "The graph is built only from accepted findings. Every node and link cites a passage. It is schema-checked, then saved to Neo4j.", "schema-checked, Neo4j"],
  ["Keep opinions apart", "Opinions, like the optional four-lens council, stay separate from the facts and run only when you authorize them.", "council, off by default"],
  ["Private by default", "Claimed profiles keep private notes in a local vault, apart from the public graph.", "separate vault"],
];

const RED = "#cf3522";
const REL: Record<string, string> = { WORKED_AT: "worked at", CONTRIBUTED_TO: "contributed to", COLLABORATED_ON: "collaborated on" };

// The record plus everyone found by search. A finished search brings its companies and works into the graph,
// and a company that is already in the record is reused, so a new person lands next to their colleagues.
function withLive(live: LivePerson[]): { nodes: GNode[]; links: GLink[] } {
  const nodes: GNode[] = [...network.nodes], links: GLink[] = [...network.links];
  const companies = new Map(nodes.filter((n) => n.kind === "company").map((n) => [n.label.toLowerCase(), n.id]));
  const inRecord = new Map(nodes.map((n, i) => [n.label.toLowerCase(), i]));
  for (const p of live) {
    if (p.status === "failed" || p.status === "cancelled") continue;
    const draft = p.status !== "complete";
    // Someone already in the record keeps their node and colour. Their lookup results attach to it.
    const at = inRecord.get(p.name.toLowerCase());
    let pid = p.id;
    if (at !== undefined && nodes[at].kind === "person") { pid = nodes[at].id; nodes[at] = { ...nodes[at], draft }; }
    else nodes.push({ id: p.id, label: p.name, kind: "person", hue: RED, draft, sub: "Added by search" });
    let extra = 0;
    for (const n of p.commit?.nodes ?? []) {
      if (extra >= 8) break;
      const title = n.title ?? "";
      if (!title || !["Organization", "Artifact", "DomainPillar"].includes(n.label)) continue;
      const edge = p.commit?.edges?.find((e) => e.source_id === n.node_id || e.target_id === n.node_id);
      if (n.label === "Organization") {
        const have = companies.get(title.toLowerCase());
        const id = have ?? `${p.id}:${n.node_id}`;
        if (!have) { nodes.push({ id, label: title, kind: "company", sub: "Company" }); companies.set(title.toLowerCase(), id); }
        if (!links.some((l) => l.source === pid && l.target === id)) links.push({ source: pid, target: id, rel: REL[edge?.relationship ?? ""] ?? "linked to" });
      } else {
        const id = `${p.id}:${n.node_id}`;
        nodes.push({ id, label: title, kind: n.label === "Artifact" ? "artifact" : "theme", hue: RED, sub: n.label === "Artifact" ? "Work" : "Theme" });
        links.push({ source: pid, target: id, rel: n.label === "Artifact" ? "built" : "works on" });
      }
      extra++;
    }
  }
  return { nodes, links };
}

export function App() {
  // Links like /?person=george-gulabyan&view=list open straight to that profile or view.
  const params = new URLSearchParams(location.search);
  const [selected, setSelected] = useState<string | null>(params.get("person"));
  const [node, setNode] = useState<string | null>("p");
  const [view, setView] = useState<"graph" | "list">(params.get("view") === "list" || (params.get("view") !== "graph" && innerWidth < 700) ? "list" : "graph");
  const [filter, setFilter] = useState<string | null>(null);
  const select = useCallback((id: string | null) => setSelected(id), []);
  const pickNode = useCallback((id: string | null) => setNode(id), []);
  const searchRef = useRef<HTMLInputElement>(null);

  // Anyone found by search. They are loaded back from the backend on every visit.
  const live = useLive();
  const add = useCallback(async (name: string) => {
    const known = network.nodes.find((n) => n.kind === "person" && n.label.toLowerCase() === name.toLowerCase());
    setSelected(known?.id ?? liveId(name));
    await live.start(name);
  }, [live]);
  const actions = useMemo(() => ({
    lookup: (name: string) => { void add(name); },
    retry: (_id: string, name: string) => { void live.start(name); },
    answer: (p: LivePerson, prompt: Parameters<typeof live.answer>[1], labels: string[], note?: string) => { void live.answer(p, prompt, labels, note); },
    stop: (p: LivePerson) => { void live.stop(p); },
  }), [live, add]);

  const graph = useMemo(() => withLive(live.people), [live.people]);
  // What gets drawn: companies and people. Role areas are shown by colour and the filter chips instead of extra nodes,
  // which keeps the lines from crossing everywhere.
  const drawn = useMemo(() => {
    const areaIds = new Set(graph.nodes.filter((n) => n.id.startsWith("area-")).map((n) => n.id));
    return { nodes: graph.nodes.filter((n) => !areaIds.has(n.id)), links: graph.links.filter((l) => !areaIds.has(l.target) && !areaIds.has(l.source)) };
  }, [graph]);
  const byId = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph]);

  // Role filter: people in the chosen area, plus that area and their companies. Anyone added by search always stays lit.
  const highlight = useMemo(() => {
    if (!filter) return null;
    const people = new Set(graph.links.filter((l) => l.target === filter).map((l) => l.source));
    const lit = new Set<string>([filter, ...people, ...live.people.map((p) => p.id)]);
    for (const l of graph.links) if (people.has(l.source) && byId.get(l.target)?.kind === "company") lit.add(l.target);
    return lit;
  }, [filter, graph, byId, live.people]);

  // List view: the same people grouped by company, so the record can be read without the canvas.
  const groups = useMemo(() => {
    const company = new Map<string, string>(), area = new Map<string, string>();
    for (const l of graph.links) {
      const t = byId.get(l.target);
      if (t?.kind === "company") company.set(l.source, l.target);
      if (t?.kind === "theme" && l.target.startsWith("area-")) area.set(l.source, l.target);
    }
    const out = new Map<string, GNode[]>();
    for (const n of graph.nodes) {
      if (n.kind !== "person") continue;
      const isLive = n.id.startsWith("live-");
      if (filter && !isLive && area.get(n.id) !== filter) continue;
      const key = company.get(n.id) ?? "added";
      out.set(key, [...(out.get(key) ?? []), n]);
    }
    return [...out.entries()]
      .map(([key, people]) => ({ key, name: key === "added" ? "Added by search" : byId.get(key)?.label ?? key, people: people.sort((a, b) => a.label.localeCompare(b.label)) }))
      .sort((a, b) => (a.key === "added" ? -1 : b.key === "added" ? 1 : b.people.length - a.people.length || a.name.localeCompare(b.name)));
  }, [graph, byId, filter]);

  const counts = useMemo(() => ({
    people: graph.nodes.filter((n) => n.kind === "person").length,
    companies: graph.nodes.filter((n) => n.kind === "company").length,
  }), [graph]);

  const focusSearch = () => {
    document.getElementById("record")?.scrollIntoView({ behavior: "smooth", block: "start" });
    setTimeout(() => searchRef.current?.focus({ preventScroll: true }), 450);
  };

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const typing = document.activeElement?.tagName === "INPUT";
      if (e.key === "/" && !typing) { e.preventDefault(); searchRef.current?.focus(); }
      // Esc closes the open profile. While typing in search it only leaves the box.
      if (e.key === "Escape" && !typing) setSelected(null);
    };
    addEventListener("keydown", key);
    return () => removeEventListener("keydown", key);
  }, []);

  // Scroll effects: a light motion blur while scrolling, and the page fades to black
  // while the "under the hood" section crosses the screen, then back to white.
  useEffect(() => {
    const root = document.documentElement;
    const main = document.querySelector("main") as HTMLElement;
    const hood = document.getElementById("hood")!;
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const clamp = (v: number) => Math.min(1, Math.max(0, v));
    let dark = 0, blur = 0, lastY = scrollY, raf = 0;
    const frame = () => {
      const vh = innerHeight, r = hood.getBoundingClientRect();
      const target = clamp((vh * 0.8 - r.top) / (vh * 0.5)) * (1 - clamp((vh * 0.75 - r.bottom) / (vh * 0.5)));
      const dy = Math.abs(scrollY - lastY); lastY = scrollY;
      dark = still ? target : dark + (target - dark) * 0.1;
      blur = still ? 0 : blur + (Math.min(3.4, Math.pow(dy, 1.35) * 0.035) - blur) * 0.12;
      scene.dark = dark;
      root.style.setProperty("--dark", dark.toFixed(3));
      main.style.filter = blur > 0.05 ? `blur(${blur.toFixed(2)}px)` : "";
      const settled = Math.abs(target - dark) < 0.002 && blur < 0.05 && dy === 0;
      if (settled) { dark = target; root.style.setProperty("--dark", String(target)); }
      raf = settled ? 0 : requestAnimationFrame(frame);
    };
    const kick = () => { if (!raf) raf = requestAnimationFrame(frame); };
    addEventListener("scroll", kick, { passive: true });
    addEventListener("resize", kick);
    kick();
    return () => { cancelAnimationFrame(raf); removeEventListener("scroll", kick); removeEventListener("resize", kick); main.style.filter = ""; };
  }, []);

  // Fade sections in as they scroll into view.
  useEffect(() => {
    const els = document.querySelectorAll("[data-reveal], [data-lines]");
    const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }), { threshold: 0.12 });
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);

  const picked = dossier.nodes.find((n) => n.id === node);
  const areas = Object.entries(areaHue).map(([name, hue]) => ({ id: "area-" + slug(name), name, hue }));

  return (
    <>
      <Night />
      <Brush />

      <header className="nav">
        <div className="nav-in">
          <a className="brand" href="#top"><span className="mk" />Arc</a>
          <nav>
            <a href="#record">Record</a>
            <a href="#profile">Profiles</a>
            <a href="#hood">Under the hood</a>
            <a href="#principles">Principles</a>
          </nav>
        </div>
      </header>

      <main id="top">
        <section className="wrap hero">
          <HalftoneLogo />
          <h1 data-reveal>A public encyclopedia for <span className="grad">everyone</span>, not only the famous</h1>
        </section>

        <section id="record" className="wrap block">
          <div className="head" data-reveal>
            <h2>People in the record</h2>
            <p className="lede">Click a name to see their profile. To add someone new, type their full name and press Find.</p>
          </div>
          <div className="card split" data-reveal>
            <div className="stage">
              <Search ref={searchRef} stage nodes={graph.nodes} onPick={select} onAdd={(n) => void add(n)} />

              <div className="viewbar" role="group" aria-label="Choose a view">
                <button aria-pressed={view === "graph"} onClick={() => setView("graph")}>Graph</button>
                <button aria-pressed={view === "list"} onClick={() => setView("list")}>List</button>
              </div>

              <div className="chips-row" role="group" aria-label="Filter by role">
                <button aria-pressed={!filter} onClick={() => setFilter(null)}>Everyone</button>
                {areas.map((a) => (
                  <button key={a.id} aria-pressed={filter === a.id} onClick={() => setFilter(filter === a.id ? null : a.id)}>
                    <i style={{ background: a.hue }} />{a.name}
                  </button>
                ))}
              </div>

              <div className={`graph-wrap${view === "list" ? " off" : ""}`} aria-hidden={view === "list"}>
                <Graph nodes={drawn.nodes} links={drawn.links} selected={selected} onSelect={select} distance={82} charge={-420} hubs highlight={highlight} />
                <p className="tip">Drag to explore, click a person, Esc to close</p>
                <ul className="mini-legend" aria-hidden>{Object.entries(areaHue).map(([name, hue]) => <li key={name}><Mark kind="person" color={hue} />{name}</li>)}<li><Mark kind="company" color="#15140f" />Company</li></ul>
              </div>

              {view === "list" && (
                <div className="list" aria-label="People in the record">
                  <p className="count">{counts.people} people, {counts.companies} companies{filter ? `, showing ${areas.find((a) => a.id === filter)?.name}` : ""}</p>
                  {groups.length === 0 && <p className="about muted">Nobody matches this filter yet.</p>}
                  {groups.map((g) => (
                    <section key={g.key}>
                      <h4>{g.name}<span>{g.people.length}</span></h4>
                      <ul>
                        {g.people.map((p) => (
                          <li key={p.id}>
                            <button className={selected === p.id ? "on" : ""} onClick={() => select(p.id)}>
                              <i className="dot" style={{ background: p.hue ?? "var(--faint)" }} />
                              <span>{p.label}</span>
                              <small>{p.sub?.split(",")[0]}</small>
                            </button>
                          </li>
                        ))}
                      </ul>
                    </section>
                  ))}
                </div>
              )}
            </div>
            <Profile id={selected} nodes={graph.nodes} links={graph.links} live={live.people} actions={actions} onSelect={select} onClose={() => select(null)} />
          </div>
        </section>

        <section id="profile" className="wrap block">
          <div className="head" data-reveal>
            <h2>Every claim points back to where it came from</h2>
            <p className="lede">Each person gets a small graph. Facts sit next to their source, and our own guesses are dashed. This example only holds what the team told us.</p>
          </div>
          <div className="card" data-reveal>
            <div className="stage tall">
              <Graph nodes={dossier.nodes} links={dossier.links} selected={node} onSelect={pickNode} distance={120} charge={-520} />
            </div>
            <div className="note">
              {picked ? <><span className="eyebrow">{legend.find((l) => l.kind === picked.kind)?.name}</span><strong>{picked.label}</strong><span>{picked.note}</span></> : <span>Select a node to see its source note.</span>}
            </div>
            <ul className="legend">
              {legend.map((l) => <li key={l.kind}><Mark kind={l.kind} /><b>{l.name}</b><span>{l.hint}</span></li>)}
            </ul>
          </div>
        </section>

        <section id="hood" className="wrap block">
          <div className="head" data-reveal>
            <h2>How a name becomes a graph</h2>
            <p className="lede">What happens after you press Find.</p>
          </div>
          <div className="pipe-card" data-reveal>
            <Pipeline />
          </div>
          <ul className="tech">
            {tech.map(([t, d, spec]) => (
              <li key={t} data-reveal><h3>{t}</h3><p>{d}</p><code>{spec}</code></li>
            ))}
          </ul>
        </section>

        <section id="principles" className="wrap block">
          <div className="head" data-reveal>
            <h2>A record people can check and correct</h2>
          </div>
          <div className="cols">
            {[
              ["Only what is public", "Arc reads the open web. Private accounts and gated pages are off limits."],
              ["Guesses look like guesses", "Anything Arc infers is drawn differently, so it never passes as fact."],
              ["You can dispute it", "Some of this may not be verified. Anyone can dispute a line, ask for a change, or ask for a profile to come down."],
            ].map(([t, d]) => <div key={t} data-reveal><h3>{t}</h3><p>{d}</p></div>)}
          </div>
        </section>

        <section className="wrap closer" data-reveal>
          <h2>Find someone</h2>
          <button className="btn" onClick={focusSearch}>Search a name</button>
        </section>
      </main>

      <footer className="wrap foot"><span className="brand"><span className="mk" />Arc</span><span>A public record for everyone.</span></footer>
    </>
  );
}
