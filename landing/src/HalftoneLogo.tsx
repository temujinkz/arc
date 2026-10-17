import { useEffect, useRef } from "react";

const CELL = 9;                     // css px between dot centres
const RGB = "207, 53, 34";          // brand red
const FONT = '900 100px "Hanken Grotesk Variable", system-ui, sans-serif';

const clamp = (v: number, a = 0, b = 1) => Math.min(b, Math.max(a, v));

// "ARC" rasterised to a grid, then drawn as halftone dots: dot size follows how much
// of the letter covers each cell, with a soft halo of small dots around the edges.
export function HalftoneLogo() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    let cols = 0, rows = 0, cw = 0, chh = 0, ox = 0, oy = 0;
    let field = new Float32Array(0);
    let raf = 0, start = 0, alive = true, visible = true;
    let tx = -9999, ty = -9999, sx = -9999, sy = -9999; // pointer target and smoothed position

    const build = async () => {
      try { await document.fonts.load(FONT, "ARC"); } catch { /* fall back to system font */ }
      if (!alive) return;
      const rect = canvas.getBoundingClientRect();
      cw = rect.width; chh = rect.height;
      cols = Math.floor(cw / CELL); rows = Math.floor(chh / CELL);
      ox = (cw - cols * CELL) / 2; oy = (chh - rows * CELL) / 2;
      const dpr = Math.min(devicePixelRatio, 2);
      canvas.width = cw * dpr; canvas.height = chh * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      // One pixel per dot. Edge pixels come out partially covered, which gives the halftone fade.
      const off = document.createElement("canvas");
      off.width = cols; off.height = rows;
      const o = off.getContext("2d", { willReadFrequently: true })!;
      let size = (rows * 0.74) / 0.7;
      o.font = FONT.replace("100px", `${size}px`);
      const w = o.measureText("ARC").width;
      if (w > cols * 0.94) { size *= (cols * 0.94) / w; o.font = FONT.replace("100px", `${size}px`); }
      o.textAlign = "center"; o.fillStyle = "#000";
      o.fillText("ARC", cols / 2, rows / 2 + (size * 0.7) / 2);
      const px = o.getImageData(0, 0, cols, rows).data;

      const core = new Float32Array(cols * rows);
      for (let i = 0; i < core.length; i++) core[i] = px[i * 4 + 3] / 255;
      // Two box-blur passes make the halo, shown as small dots around each letter.
      const blur = (src: Float32Array) => {
        const dst = new Float32Array(src.length);
        for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
          let s = 0, n = 0;
          for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) {
            const rr = r + dr, cc = c + dc;
            if (rr >= 0 && rr < rows && cc >= 0 && cc < cols) { s += src[rr * cols + cc]; n++; }
          }
          dst[r * cols + c] = s / n;
        }
        return dst;
      };
      const halo = blur(blur(core));
      field = new Float32Array(core.length);
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
        const i = r * cols + c;
        const shade = 0.72 + 0.28 * (r / rows); // dots grow slightly toward the bottom
        field[i] = Math.max(core[i] * shade, halo[i] * 0.55);
      }
      start = performance.now();
      kick();
    };

    const draw = (now: number) => {
      ctx.clearRect(0, 0, cw, chh);
      sx += (tx - sx) * 0.16; sy += (ty - sy) * 0.16;
      let busy = false;
      // A bright band sweeps across every five seconds.
      const bx = ((now / 5000) % 1) * (cols + 30) - 15;
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
        const v = field[r * cols + c];
        if (v < 0.06) continue;
        let x = ox + (c + 0.5) * CELL, y = oy + (r + 0.5) * CELL;
        // Entrance: a soft wave from the left. Dots start at 60% size, never from nothing.
        const p = still ? 1 : clamp((now - start - c * 9 - r * 5) / 560);
        if (p <= 0) { busy = true; continue; }
        if (p < 1) busy = true;
        const e = 1 - Math.pow(1 - p, 4);
        // Near the pointer dots swell and lean away.
        const dx = x - sx, dy = y - sy, d2 = dx * dx + dy * dy;
        const g = d2 < 160 * 160 ? Math.exp(-d2 / (90 * 90)) : 0;
        if (g > 0.01) { const d = Math.sqrt(d2) || 1; x += (dx / d) * g * 5; y += (dy / d) * g * 5; busy = true; }
        // Idle motion: two slow ripples crossing the letters, plus the sweeping sheen.
        const amb = still ? 0 : 0.26 * Math.sin(now * 0.0022 + c * 0.26 - r * 0.18) + 0.14 * Math.sin(now * 0.0013 - c * 0.12 + r * 0.34) + 0.08 * Math.sin(now * 0.0035 + (c + r) * 0.5);
        const sheen = still ? 0 : 0.5 * Math.exp(-(((c - bx) / 6) ** 2));
        // The dots also drift a little, so the whole word seems to float.
        const fx = still ? 0 : Math.sin(now * 0.0014 + r * 0.4) * 1.7, fy = still ? 0 : Math.cos(now * 0.0017 + c * 0.3) * 1.7;
        const rad = Math.min(CELL * 0.68, v * CELL * 0.52 * (0.6 + 0.4 * e) * (1 + 0.5 * g + amb + sheen));
        ctx.fillStyle = `rgba(${RGB}, ${e})`;
        ctx.beginPath(); ctx.arc(x + fx, y + fy, rad, 0, Math.PI * 2); ctx.fill();
      }
      if (Math.abs(tx - sx) > 0.5 || Math.abs(ty - sy) > 0.5) busy = true;
      if (!still && visible) busy = true;
      raf = busy ? requestAnimationFrame(draw) : 0;
    };
    const kick = () => { if (!raf && alive && field.length) raf = requestAnimationFrame(draw); };

    const move = (e: PointerEvent) => {
      const b = canvas.getBoundingClientRect();
      tx = e.clientX - b.left; ty = e.clientY - b.top;
      if (sx < -9000) { sx = tx; sy = ty; }
      kick();
    };

    const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible) kick(); });
    io.observe(canvas);
    build();
    const ro = new ResizeObserver(() => { cancelAnimationFrame(raf); raf = 0; build(); });
    ro.observe(canvas);
    if (!still) addEventListener("pointermove", move);
    return () => { alive = false; cancelAnimationFrame(raf); ro.disconnect(); io.disconnect(); removeEventListener("pointermove", move); };
  }, []);

  return <canvas ref={ref} className="halftone" role="img" aria-label="Arc" />;
}
