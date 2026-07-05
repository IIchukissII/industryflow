// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useRef } from 'react';
import './Starfield.css';

// The raw material: sensor telemetry as a field of faint points — the unresolved data out of
// which the mandala's ordered core emerges. Points twinkle and fade toward the centre so the
// mark reads clean over them. Canvas (not SVG) since it's a few hundred animated dots.
// Honors prefers-reduced-motion: one static frame, no loop.
export default function Starfield({ className = '' }) {
  const ref = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext('2d');
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let stars = [];
    let W = 0;
    let H = 0;
    let raf = 0;

    const seed = () => {
      stars = [];
      const n = Math.round((W * H) / 2400);
      for (let i = 0; i < n; i += 1) {
        // colour drawn from the palette — the raw data as kinds of signal: mostly warm-white,
        // some gold (the Self), the odd azure (reason), a rare cinnabar (the critical).
        const roll = Math.random();
        const rgb = roll < 0.70 ? '224,222,214'
          : roll < 0.84 ? '207,169,78'
            : roll < 0.94 ? '74,144,217'
              : '224,71,47';
        stars.push({
          x: Math.random() * W,
          y: Math.random() * H,
          r: Math.random() * 1.3 + 0.3,
          base: Math.random() * 0.4 + 0.14,
          ph: Math.random() * 6.283,
          sp: Math.random() * 0.6 + 0.3, // slow: ~8–26s blink period
          rgb,
        });
      }
    };
    const size = () => {
      const b = canvas.getBoundingClientRect();
      const d = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = b.width * d;
      canvas.height = b.height * d;
      ctx.setTransform(d, 0, 0, d, 0, 0);
      W = b.width;
      H = b.height;
      seed();
    };
    const draw = (t) => {
      ctx.clearRect(0, 0, W, H);
      const cx = W * 0.5;
      const cy = H * 0.44;
      for (let i = 0; i < stars.length; i += 1) {
        const s = stars[i];
        const tw = reduce ? 0.75 : 0.5 + 0.5 * Math.sin(t * 0.0008 * s.sp + s.ph);
        const dx = (s.x - cx) / W;
        const dy = (s.y - cy) / H;
        const fade = Math.min(1, Math.sqrt(dx * dx + dy * dy) * 2.5);
        const a = s.base * tw * fade;
        ctx.beginPath();
        ctx.arc(s.x, s.y, s.r, 0, 6.2832);
        ctx.fillStyle = `rgba(${s.rgb},${a})`;
        ctx.fill();
      }
      if (!reduce) raf = requestAnimationFrame(draw);
    };
    const onResize = () => {
      cancelAnimationFrame(raf);
      size();
      if (!reduce) raf = requestAnimationFrame(draw);
    };

    size();
    window.addEventListener('resize', onResize);
    if (reduce) draw(0);
    else raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
    };
  }, []);

  return <canvas ref={ref} className={`starfield ${className}`.trim()} aria-hidden="true" />;
}
