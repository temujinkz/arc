import { useEffect, useRef } from "react";
import { scene } from "./scene";

type P = { x: number; y: number; t: number };

const LIFE = 750;   // ms a point stays in the stroke
const MAX_W = 40;   // widest part of the stroke, at the cursor
const ALPHA = 0.06;

// A soft ink stroke that trails the cursor and dries out, like a wet brush.
export function Brush() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (!matchMedia("(hover: hover)").matches || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    let pts: P[] = [], raf = 0, w = 0, h = 0, speed = 0; // speed in px per ms, smoothed

    const resize = () => {
      const dpr = Math.min(devicePixelRatio, 2);
      w = innerWidth; h = innerHeight;
      canvas.width = w * dpr; canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const draw = (now: number) => {
      pts = pts.filter((p) => now - p.t < LIFE);
      speed *= 0.95;
      ctx.clearRect(0, 0, w, h);
      if (pts.length > 2) {
        const left: [number, number][] = [], right: [number, number][] = [];
        pts.forEach((p, i) => {
          const a = pts[Math.max(0, i - 1)], b = pts[Math.min(pts.length - 1, i + 1)];
          let dx = b.x - a.x, dy = b.y - a.y;
          const len = Math.hypot(dx, dy) || 1;
          dx /= len; dy /= len;
          const age = (now - p.t) / LIFE;
          const fast = Math.min(1, len / 60);
          // Thick at the head, tapering to the tail; faster strokes run thinner.
          const wd = MAX_W * Math.pow(1 - age, 1.6) * (1 - 0.45 * fast);
          left.push([p.x - dy * wd / 2, p.y + dx * wd / 2]);
          right.push([p.x + dy * wd / 2, p.y - dx * wd / 2]);
        });
        ctx.beginPath();
        ctx.moveTo(left[0][0], left[0][1]);
        for (let i = 1; i < left.length; i++) ctx.lineTo(left[i][0], left[i][1]);
        for (let i = right.length - 1; i >= 0; i--) ctx.lineTo(right[i][0], right[i][1]);
        ctx.closePath();
        const tone = Math.round(21 + (255 - 21) * scene.dark);
        ctx.fillStyle = `rgba(${tone}, ${tone}, ${tone}, ${(ALPHA + 0.02 * scene.dark) * (0.3 + 0.7 * Math.min(1, speed / 1.3))})`;
        ctx.fill();
      }
      raf = pts.length ? requestAnimationFrame(draw) : 0;
    };

    const move = (e: PointerEvent) => {
      const last = pts[pts.length - 1];
      const now = performance.now();
      if (last) {
        const dist = Math.hypot(e.clientX - last.x, e.clientY - last.y);
        if (dist < 3) return;
        speed = speed * 0.6 + (dist / Math.max(1, now - last.t)) * 0.4;
      }
      pts.push({ x: e.clientX, y: e.clientY, t: now });
      if (!raf) raf = requestAnimationFrame(draw);
    };

    resize();
    addEventListener("resize", resize);
    addEventListener("pointermove", move);
    return () => {
      cancelAnimationFrame(raf);
      removeEventListener("resize", resize);
      removeEventListener("pointermove", move);
    };
  }, []);

  return <canvas ref={ref} className="brush" aria-hidden />;
}
