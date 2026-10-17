import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Graph } from "./Graph";
import { Search } from "./Search";
import { Profile } from "./Profile";
import { Brush } from "./Brush";
import { useLive, type LivePerson } from "./live";
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

const tech = [
  ["Collect", "Searching a name starts an AI agent built on the Claude Agent SDK. It calls research tools over MCP: web and publication search, identity lookups, and LinkedIn and GitHub profiles. It can stop to ask a person a question, and every run has a spending limit.", "FastAPI, Claude Agent SDK, MCP"],
  ["Keep the evidence", "Every result is saved with the time it was collected and a SHA-256 hash. Matching usernames do not prove two accounts are one person, and ten snapshots of the same page still count as one source.", "SQLite cache, SHA-256"],
  ["Build the graph", "A second step turns the evidence into a graph, and the graph is checked before it is saved. Every node and link has to cite evidence, there is exactly one central person, and anything the AI infers has to be labelled as a guess.", "schema-checked, Neo4j"],
  ["Draft, then commit", "While a search is running, new findings stream in as dashed drafts. Only the saved graph becomes a profile.", "server-sent events"],
  ["Guesses stay separate", "Evidence is marked observed, unverified, supported, disputed or interpretation. Our own readings, like how fast someone builds, live in their own nodes with the limits written beside them.", "evidence states"],
  ["Private by default", "People can claim their own profile. Their private notes stay in a separate local vault, and what friends say about them is stored apart from the research.", "separate vault, separate peer claims"],
];

export function App() {
  const [selected, setSelected] = useState<string | null>(null);
  const [node, setNode] = useState<string | null>("p");
  const select = useCallback((id: string | null) => setSelected(id), []);
  const pickNode = useCallback((id: string | null) => setNode(id), []);
  const searchRef = useRef<HTMLInputElement>(null);

  // People added from the search box. Each one is a live backend run until it finishes.
  const live = useLive();
  const add = useCallback(async (name: string) => {
    const id = "live-" + name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    setSelected(id);
    await live.start(name, live.people.some((p) => p.id === id) ? id : undefined);
  }, [live]);
  const actions = useMemo(() => ({
    retry: (id: string, name: string) => { void live.start(name, id); },
    answer: (p: LivePerson, requestId: string, selected: string[]) => { void live.answer(p.id, p.sessionId, requestId, selected); },
    stop: (p: LivePerson) => { void live.stop(p.id, p.sessionId); },
  }), [live]);

  // The record plus anyone being added. A failed search leaves no node behind to clutter the graph.
  const graph = useMemo(() => {
    const nodes: GNode[] = [...network.nodes], links: GLink[] = [...network.links];
    for (const p of live.people) {
      if (p.status === "failed" || p.status === "cancelled") continue;
      nodes.push({ id: p.id, label: p.name, kind: "person", hue: "#cf3522", draft: p.status !== "complete", sub: "Added by search" });
      if (p.status === "complete") p.themes.forEach((t, i) => {
        const tid = `${p.id}-t${i}`;
        nodes.push({ id: tid, label: t, kind: "theme", hue: "#cf3522", sub: "Theme" });
        links.push({ source: p.id, target: tid, rel: "works on" });
      });
    }
    return { nodes, links };
  }, [live.people]);
  const focusSearch = () => {
    document.getElementById("record")?.scrollIntoView({ behavior: "smooth", block: "start" });
    setTimeout(() => searchRef.current?.focus({ preventScroll: true }), 450);
  };

  // "/" jumps to the graph search.
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
            <h2>Who works with who</h2>
          </div>
          <div className="card split" data-reveal>
            <div className="stage">
              <Search ref={searchRef} stage onPick={select} onAdd={(n) => void add(n)} />
              <Graph nodes={graph.nodes} links={graph.links} selected={selected} onSelect={select} distance={130} charge={-620} />
              <p className="tip">Drag to explore, click a person, Esc to close</p>
              <ul className="mini-legend" aria-hidden>{Object.entries(areaHue).map(([name, hue]) => <li key={name}><Mark kind="person" color={hue} />{name}</li>)}<li><Mark kind="company" color="#15140f" />Company</li></ul>
            </div>
            <Profile id={selected} onSelect={select} live={live.people} actions={actions} />
          </div>
        </section>

        <section id="profile" className="wrap block">
          <div className="head" data-reveal>
            <h2>Every claim points back to where it came from</h2>
            <p className="lede">Every person gets their own small graph. Things we found sit next to the page they came from, and anything we worked out ourselves is drawn dashed so you can tell the difference. This example only holds what the team has told us so far.</p>
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
            <p className="lede">When you search a name, Arc goes looking, writes down what it finds, and only then draws the graph. Here is the route it takes.</p>
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
              ["Only what is already public", "Arc reads what people have put on the open web. Private accounts and gated pages are off limits."],
              ["Guesses look like guesses", "Anything Arc works out for itself is drawn differently and comes with its limits, so it never passes as fact."],
              ["You can ask to be removed", "Anyone can claim their profile, fix it, or ask for it to come down."],
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
