import { useEffect, useRef } from "react";

const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5];
const smooth = (a: number, b: number, x: number) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };

// A pixelated red glow along the bottom and sides, built with ordered dithering.
// Fixed behind the page; its opacity follows --dark, so it only shows in the dark scene.
export function Night() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    const S = 4; // css px per pixel
    const paint = () => {
      const w = Math.ceil(innerWidth / S), h = Math.ceil(innerHeight / S);
      canvas.width = w; canvas.height = h;
      const img = ctx.createImageData(w, h);
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        const u = x / w, v = y / h;
        const bottom = Math.pow(smooth(0.5, 1, v), 1.4) * 0.95;
        const edge = Math.max(1 - smooth(0, 0.3, u), smooth(0.7, 1, u));
        const side = edge * smooth(0.25, 1, v) * 0.85;
        const I = Math.max(bottom, side);
        if (I > (BAYER[(x & 3) + ((y & 3) << 2)] + 0.5) / 16) {
          const i = (y * w + x) * 4;
          img.data[i] = 207; img.data[i + 1] = 53; img.data[i + 2] = 34; img.data[i + 3] = 255;
        }
      }
      ctx.putImageData(img, 0, 0);
    };
    paint();
    addEventListener("resize", paint);
    return () => removeEventListener("resize", paint);
  }, []);

  return <canvas ref={ref} className="night-art" aria-hidden />;
}
