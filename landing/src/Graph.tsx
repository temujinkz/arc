import { useEffect, useMemo, useRef } from "react";
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationNodeDatum } from "d3-force";
import type { GLink, GNode } from "./data";

type N = SimulationNodeDatum & GNode & { w: number; h: number };
type Cam = { ready: boolean; touched: boolean; k: number; tx: number; ty: number };
type L = { source: N | string; target: N | string; rel: string };

type Props = {
  nodes: GNode[];
  links: GLink[];
  selected: string | null;
  onSelect: (id: string | null) => void;
  distance?: number;
  charge?: number;
};

const INK = "#15140f", LINE = "#d6d4cc", FAINT = "#b3b1a5", PAPER = "#f6f6f4";
const TAU = Math.PI * 2;

// Shapes say what a node is, colour says which group it belongs to.
function shape(ctx: CanvasRenderingContext2D, n: N, r: number, dim: boolean, on: boolean, k: number) {
  const x = n.x!, y = n.y!;
  const c = dim ? FAINT : n.hue ?? INK;
  ctx.setLineDash([]);
  ctx.lineWidth = 1.5 / k;
  ctx.strokeStyle = c;
  ctx.beginPath();
  if (n.draft) {
    // Still being researched: a dashed ring that breathes.
    const t = performance.now() / 600;
    ctx.setLineDash([3 / k, 2.5 / k]);
    ctx.arc(x, y, r, 0, TAU); ctx.fillStyle = PAPER; ctx.fill(); ctx.stroke(); ctx.setLineDash([]);
    ctx.beginPath(); ctx.arc(x, y, r + 5 + Math.sin(t) * 2.5, 0, TAU);
    ctx.globalAlpha = 0.25 + 0.15 * Math.sin(t); ctx.lineWidth = 1 / k; ctx.stroke(); ctx.globalAlpha = 1;
    return;
  }
  if (n.kind === "person") {
    ctx.arc(x, y, r, 0, TAU); ctx.fillStyle = c; ctx.fill();
  } else if (n.kind === "company") {
    ctx.roundRect(x - r * 0.95, y - r * 0.95, r * 1.9, r * 1.9, 2 / k); ctx.fillStyle = dim ? FAINT : INK; ctx.fill();
  } else {
    if (n.kind === "theme") ctx.arc(x, y, r, 0, TAU);
    else if (n.kind === "artifact") ctx.rect(x - r * 0.85, y - r * 0.85, r * 1.7, r * 1.7);
    else if (n.kind === "claim") { ctx.moveTo(x, y - r * 1.2); ctx.lineTo(x + r * 1.2, y); ctx.lineTo(x, y + r * 1.2); ctx.lineTo(x - r * 1.2, y); ctx.closePath(); }
    else { ctx.arc(x, y, r, 0, TAU); ctx.setLineDash([3 / k, 2.5 / k]); }
    ctx.fillStyle = PAPER; ctx.fill();
    ctx.globalAlpha = dim ? 0.1 : 0.18; ctx.fillStyle = c; ctx.fill(); ctx.globalAlpha = 1;
    ctx.stroke(); ctx.setLineDash([]);
  }
  if (on) { ctx.beginPath(); ctx.arc(x, y, r + 5, 0, TAU); ctx.lineWidth = 1 / k; ctx.globalAlpha = 0.35; ctx.stroke(); ctx.globalAlpha = 1; }
}

export function Graph({ nodes: inNodes, links: inLinks, selected, onSelect, distance = 120, charge = -300 }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const selRef = useRef(selected);
  selRef.current = selected;
  const focus = useRef<(id: string) => void>(() => {});
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  // The simulation is rebuilt only when nodes or links are added or removed. Positions and the
  // camera carry over, so a new person appears without the whole graph jumping.
  const cam = useRef<Cam>({ ready: false, touched: false, k: 1, tx: 0, ty: 0 });
  const pos = useRef(new Map<string, { x: number; y: number }>());
  const meta = useRef(new Map<string, GNode>());
  meta.current = new Map(inNodes.map((n) => [n.id, n]));
  const shapeKey = useMemo(() => inNodes.map((n) => n.id).join() + "|" + inLinks.map((l) => l.source + ">" + l.target).join(), [inNodes, inLinks]);

  useEffect(() => { if (selected) focus.current(selected); }, [selected]);

  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    const font = (n: { kind: string }) => `${n.kind === "company" ? 600 : n.kind === "person" ? 450 : 400} 12px "Geist Variable", system-ui, sans-serif`;
    const known = pos.current;
    const nodes: N[] = inNodes.map((n) => {
      ctx.font = font(n);
      const p = known.get(n.id);
      return { ...n, w: ctx.measureText(n.label).width + 34, h: 50, ...(p ? { x: p.x, y: p.y } : {}) };
    });
    // A new node starts beside the first node it is linked to, so it settles in place.
    for (const n of nodes) {
      if (known.has(n.id)) continue;
      const l = inLinks.find((x) => (x.source === n.id && known.has(x.target)) || (x.target === n.id && known.has(x.source)));
      const near = l ? known.get(l.source === n.id ? l.target : l.source) : null;
      if (near) { n.x = near.x + (Math.random() - 0.5) * 40; n.y = near.y + (Math.random() - 0.5) * 40; }
    }
    const rebuilding = known.size > 0;
    const links: L[] = inLinks.map((l) => ({ ...l }));
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const adj = new Map<string, Set<string>>();
    for (const l of inLinks) {
      (adj.get(l.source) ?? adj.set(l.source, new Set()).get(l.source)!).add(l.target);
      (adj.get(l.target) ?? adj.set(l.target, new Set()).get(l.target)!).add(l.source);
    }

    // Each node takes up a box the size of its dot plus its label, and boxes push each
    // other apart along the shorter overlap, so names never sit on top of one another.
    const boxes = (alpha: number) => {
      const s = 0.5 * Math.min(1, alpha * 4 + 0.25);
      for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        const dx = b.x! - a.x!, dy = b.y! - a.y!;
        const ox = (a.w + b.w) / 2 - Math.abs(dx), oy = (a.h + b.h) / 2 - Math.abs(dy);
        if (ox <= 0 || oy <= 0) continue;
        if (ox < oy) { const p = (dx === 0 ? Math.random() - 0.5 : Math.sign(dx)) * ox * s * 0.5; a.vx! -= p; b.vx! += p; }
        else { const p = (dy === 0 ? Math.random() - 0.5 : Math.sign(dy)) * oy * s * 0.5; a.vy! -= p; b.vy! += p; }
      }
    };

    const sim = forceSimulation(nodes)
      .force("link", forceLink<N, L>(links).id((d) => d.id).distance(distance).strength(0.4))
      .force("charge", forceManyBody().strength(charge).distanceMax(520))
      .force("round", forceCollide<N>(28).strength(0.9))
      .force("boxes", boxes)
      // The card is wider than it is tall, so the layout is pulled less sideways than up and down.
      .force("x", forceX(0).strength(0.012))
      .force("y", forceY(0).strength(0.06))
      .velocityDecay(0.42)
      .alphaDecay(0.02)
      .alpha(rebuilding ? 0.35 : 1);

    let { touched, k, tx, ty } = cam.current;
    let w = 0, h = 0, hover: N | null = null, drag: N | null = null, pan = false, moved = false;

    const resize = () => {
      const dpr = devicePixelRatio;
      w = canvas.clientWidth; h = canvas.clientHeight;
      canvas.width = w * dpr; canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (!cam.current.ready) {
        cam.current.ready = true;
        tx = w / 2; ty = h / 2;
        k = Math.min(1, Math.max(0.6, w / 640));
        touched = false;
      }
      queueMicrotask(fit);
    };

    // Ease the camera so the whole graph sits inside the card, under the search box.
    const fit = () => {
      if (touched || !w || !h) return;
      let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
      for (const n of nodes) { x0 = Math.min(x0, n.x! - n.w / 2); x1 = Math.max(x1, n.x! + n.w / 2); y0 = Math.min(y0, n.y!); y1 = Math.max(y1, n.y! + 24); }
      const nk = Math.min(1.5, Math.max(0.4, Math.min(w / (x1 - x0 + 120), (h - 120) / (y1 - y0 + 80))));
      const nx = w / 2 - ((x0 + x1) / 2) * nk, ny = (h + 56) / 2 - ((y0 + y1) / 2) * nk;
      const k0 = k, x = tx, y = ty, t0 = performance.now();
      const step = (now: number) => {
        if (touched) return;
        const e = Math.min(1, (now - t0) / 600), s = 1 - Math.pow(1 - e, 5);
        k = k0 + (nk - k0) * s; tx = x + (nx - x) * s; ty = y + (ny - y) * s;
        if (e < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    };

    const toWorld = (x: number, y: number) => [(x - tx) / k, (y - ty) / k];
    const pick = (x: number, y: number) => {
      const [wx, wy] = toWorld(x, y);
      return nodes.find((n) => Math.hypot((n.x ?? 0) - wx, (n.y ?? 0) - wy) < 16) ?? null;
    };

    focus.current = (id) => {
      const n = byId.get(id);
      if (!n) return;
      touched = true;
      const sx = w / 2 - (n.x ?? 0) * k, sy = h / 2 - (n.y ?? 0) * k;
      const x0 = tx, y0 = ty, t0 = performance.now();
      const step = (now: number) => {
        const e = Math.min(1, (now - t0) / 500), s = 1 - Math.pow(1 - e, 3);
        tx = x0 + (sx - x0) * s; ty = y0 + (sy - y0) * s;
        if (e < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    };

    const curve = (a: N, b: N, i: number) => {
      const mx = (a.x! + b.x!) / 2, my = (a.y! + b.y!) / 2;
      const dx = b.x! - a.x!, dy = b.y! - a.y!;
      const off = 0.12 * (i % 2 ? 1 : -1);
      return { cx: mx - dy * off, cy: my + dx * off };
    };

    let raf = 0;
    const draw = () => {
      ctx.clearRect(0, 0, w, h);
      ctx.save();
      ctx.translate(tx, ty); ctx.scale(k, k);
      const sel = selRef.current;
      const focusId = sel ?? hover?.id ?? null;
      const active = new Set<string>();
      if (focusId) { active.add(focusId); adj.get(focusId)?.forEach((x) => active.add(x)); }

      links.forEach((l, i) => {
        const a = l.source as N, b = l.target as N;
        const on = focusId && (a.id === focusId || b.id === focusId);
        const { cx, cy } = curve(a, b, i);
        ctx.strokeStyle = on ? INK : focusId ? "#e1e1da" : LINE;
        ctx.lineWidth = (on ? 1.25 : 1) / k;
        ctx.beginPath(); ctx.moveTo(a.x!, a.y!); ctx.quadraticCurveTo(cx, cy, b.x!, b.y!); ctx.stroke();
        if (on) {
          const lx = 0.25 * a.x! + 0.5 * cx + 0.25 * b.x!, ly = 0.25 * a.y! + 0.5 * cy + 0.25 * b.y!;
          ctx.font = `500 10px "Geist Variable", system-ui, sans-serif`;
          const tw = ctx.measureText(l.rel).width;
          ctx.fillStyle = PAPER; ctx.fillRect(lx - tw / 2 - 4, ly - 8, tw + 8, 16);
          ctx.fillStyle = "#54524a"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
          ctx.fillText(l.rel, lx, ly + 0.5);
        }
      });

      ctx.textAlign = "center"; ctx.textBaseline = "top";
      for (const n of nodes) {
        const m = meta.current.get(n.id);
        if (m) { n.draft = m.draft; n.hue = m.hue; n.label = m.label; }
        const dim = !!focusId && !active.has(n.id);
        const on = n.id === sel || n === hover;
        const r = n.kind === "person" ? 5.5 : 5;
        shape(ctx, n, r + (on ? 1 : 0), dim, on, k);
        ctx.font = font(n);
        ctx.fillStyle = dim ? FAINT : INK;
        const t = n.label.length > 28 ? n.label.slice(0, 27) + "…" : n.label;
        ctx.fillText(t, n.x!, n.y! + r + 9);
      }
      ctx.restore();
      raf = requestAnimationFrame(draw);
    };

    const pt = (e: PointerEvent) => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    const down = (e: PointerEvent) => {
      const [x, y] = pt(e);
      canvas.setPointerCapture(e.pointerId);
      touched = true;
      moved = false;
      drag = pick(x, y);
      pan = !drag;
      if (drag) { const [wx, wy] = toWorld(x, y); drag.fx = wx; drag.fy = wy; sim.alphaTarget(0.25).restart(); }
    };
    const move = (e: PointerEvent) => {
      const [x, y] = pt(e);
      if (drag) { const [wx, wy] = toWorld(x, y); drag.fx = wx; drag.fy = wy; moved = true; }
      else if (pan) { tx += e.movementX; ty += e.movementY; moved = true; canvas.style.cursor = "grabbing"; }
      else { hover = pick(x, y); canvas.style.cursor = hover ? "pointer" : "grab"; }
    };
    const up = (e: PointerEvent) => {
      const [x, y] = pt(e);
      if (drag) { drag.fx = null; drag.fy = null; sim.alphaTarget(0); }
      if (!moved) onSelectRef.current(pick(x, y)?.id ?? null);
      drag = null; pan = false;
    };
    const leave = () => { hover = null; };

    // Wheel zooms only with ctrl/cmd so the page itself keeps scrolling.
    const wheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      touched = true;
      const nk = Math.min(2.5, Math.max(0.4, k * Math.exp(-e.deltaY * 0.01)));
      tx = e.offsetX - ((e.offsetX - tx) / k) * nk; ty = e.offsetY - ((e.offsetY - ty) / k) * nk; k = nk;
    };

    sim.on("end", fit);
    const fitTimer = setTimeout(fit, 900);
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);
    resize();
    canvas.addEventListener("pointerdown", down);
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
    canvas.addEventListener("pointerleave", leave);
    canvas.addEventListener("wheel", wheel, { passive: false });
    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf); clearTimeout(fitTimer); sim.stop(); ro.disconnect();
      for (const n of nodes) pos.current.set(n.id, { x: n.x ?? 0, y: n.y ?? 0 });
      Object.assign(cam.current, { touched, k, tx, ty });
      canvas.removeEventListener("pointerdown", down);
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up);
      canvas.removeEventListener("pointerleave", leave);
      canvas.removeEventListener("wheel", wheel);
    };
  }, [shapeKey, distance, charge]); // eslint-disable-line react-hooks/exhaustive-deps

  return <canvas ref={ref} className="graph" />;
}
