'use client';

import { useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import './landing.css';

export default function LandingPage() {
  const canvasRef  = useRef<HTMLCanvasElement>(null);
  const stageRef   = useRef<HTMLDivElement>(null);
  const heroRef    = useRef<HTMLDivElement>(null);
  const auroraRef  = useRef<HTMLDivElement>(null);
  const launchingRef = useRef(false);
  const router = useRouter();

  /* ── Reveal animations ── */
  useEffect(() => {
    requestAnimationFrame(() => {
      document.querySelectorAll<HTMLElement>('.sp-reveal').forEach(el =>
        el.classList.add('sp-show')
      );
    });
  }, []);

  /* ── Warp starfield canvas ── */
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    let w = 0, h = 0, raf = 0;

    const resize = () => {
      w = canvas.width  = canvas.offsetWidth  * dpr;
      h = canvas.height = canvas.offsetHeight * dpr;
    };
    resize();
    window.addEventListener('resize', resize);

    const STARS = 320;
    const stars = Array.from({ length: STARS }, () => ({
      x: (Math.random() - 0.5) * (canvas.offsetWidth * dpr),
      y: (Math.random() - 0.5) * (canvas.offsetHeight * dpr),
      z: Math.random() * (canvas.offsetWidth * dpr),
      pz: 0,
    }));

    const tick = () => {
      const speed = launchingRef.current ? 48 : 2.2;
      ctx.fillStyle = 'rgba(8,10,22,0.35)';
      ctx.fillRect(0, 0, w, h);
      const cx = w / 2, cy = h / 2;

      for (const s of stars) {
        s.pz = s.z;
        s.z -= speed;
        if (s.z < 1) {
          s.x = (Math.random() - 0.5) * w;
          s.y = (Math.random() - 0.5) * h;
          s.z = w; s.pz = s.z;
        }
        const k = 220 / s.z, pk = 220 / s.pz;
        const px  = s.x * k  + cx, py  = s.y * k  + cy;
        const ppx = s.x * pk + cx, ppy = s.y * pk + cy;
        const size  = Math.max(0.5, (1 - s.z / w) * 2.2) * dpr;
        const alpha = Math.min(1, (1 - s.z / w) * 1.4);
        const g = ctx.createLinearGradient(ppx, ppy, px, py);
        g.addColorStop(0, 'rgba(120,140,255,0)');
        g.addColorStop(1, `rgba(170,200,255,${alpha})`);
        ctx.strokeStyle = g;
        ctx.lineWidth   = size;
        ctx.beginPath();
        ctx.moveTo(ppx, ppy);
        ctx.lineTo(px, py);
        ctx.stroke();
      }
      raf = requestAnimationFrame(tick);
    };
    tick();

    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', resize); };
  }, []);

  /* ── Parallax tilt + aurora follow ── */
  useEffect(() => {
    const stage  = stageRef.current;
    const hero   = heroRef.current;
    const aurora = auroraRef.current;
    if (!stage || !hero || !aurora) return;

    const onMove = (e: MouseEvent) => {
      const r = stage.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width;
      const y = (e.clientY - r.top)  / r.height;
      aurora.style.setProperty('--mx', x * 100 + '%');
      aurora.style.setProperty('--my', y * 100 + '%');
      hero.style.transform = `perspective(1200px) rotateX(${(y - 0.5) * -6}deg) rotateY(${(x - 0.5) * 8}deg)`;
    };

    stage.addEventListener('mousemove', onMove);
    return () => stage.removeEventListener('mousemove', onMove);
  }, []);

  /* ── Enter button — smooth warp then navigate ── */
  const handleEnter = () => {
    if (launchingRef.current) return;
    launchingRef.current = true;
    stageRef.current?.classList.add('sp-launching');
    // Wait for CSS fade-out (0.6s) then push
    setTimeout(() => router.push('/app'), 650);
  };

  return (
    <div className="sp-stage" ref={stageRef}>
      <canvas className="sp-canvas" ref={canvasRef} />
      <div className="sp-aurora" ref={auroraRef} />
      <div className="sp-grid" />
      <div className="sp-flash" />

      {/* ── Header ── */}
      <header className="sp-header">
        <div className="sp-brand">
          <svg width="28" height="28" viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg" aria-label="Recovera">
            <defs>
              <linearGradient id="sp-rg" x1="0" y1="0" x2="40" y2="40" gradientUnits="userSpaceOnUse">
                <stop offset="0%"   stopColor="#818cf8" />
                <stop offset="100%" stopColor="#67e8f9" />
              </linearGradient>
              <radialGradient id="sp-rgl" cx="50%" cy="50%" r="50%">
                <stop offset="0%"   stopColor="#818cf8" stopOpacity=".7" />
                <stop offset="100%" stopColor="#818cf8" stopOpacity="0"  />
              </radialGradient>
            </defs>
            <path d="M20 2.5 L34.5 11 V29 L20 37.5 L5.5 29 V11 Z" stroke="url(#sp-rg)" strokeWidth="1.6" fill="url(#sp-rgl)" />
            <ellipse cx="20" cy="20" rx="9.5" ry="4" stroke="url(#sp-rg)" strokeWidth="1.2" opacity=".7" />
            <path d="M20 12 L26 20 L20 28 L14 20 Z" fill="url(#sp-rg)" />
            <circle cx="20" cy="20" r="2" fill="#fff" opacity=".95" />
          </svg>
          <span className="sp-brand-name">RECOVERA</span>
        </div>
        <div className="sp-pill">
          <span className="sp-dot" />
          SYSTEM ONLINE · v2.6
        </div>
      </header>

      {/* ── Main — centred in viewport ── */}
      <main className="sp-main">
        <div className="sp-hero" ref={heroRef}>

          {/* Orb */}
          <div className="sp-orb sp-reveal">
            <div className="sp-ring sp-ring-r1" />
            <div className="sp-ring sp-ring-r2" />
            <div className="sp-ring sp-ring-dash" />
            <div className="sp-glow" />
            <svg width="68" height="68" viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M20 2.5 L34.5 11 V29 L20 37.5 L5.5 29 V11 Z" stroke="url(#sp-rg)" strokeWidth="1.6" fill="url(#sp-rgl)" />
              <ellipse cx="20" cy="20" rx="9.5" ry="4" stroke="url(#sp-rg)" strokeWidth="1.2" opacity=".7" />
              <path d="M20 12 L26 20 L20 28 L14 20 Z" fill="url(#sp-rg)" />
              <circle cx="20" cy="20" r="2" fill="#fff" opacity=".95" />
            </svg>
            <div className="sp-moon" />
            <div className="sp-moon2" />
          </div>

          {/* Badge */}
          <div className="sp-badge sp-reveal">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#67e8f9" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z" />
            </svg>
            REVENUE INTELLIGENCE OS
          </div>

          {/* Headline */}
          <h1 className="sp-h1 sp-reveal sp-delay-150">
            Step into the future<br />of revenue intelligence
          </h1>

          {/* Sub-copy */}
          <p className="sp-lead sp-reveal sp-delay-300">
            A live, AI-powered command center for leakage recovery, seller risk, vector retrieval and marketing attribution. Cross the threshold to enter the cockpit.
          </p>

          {/* CTA */}
          <div className="sp-enter-wrap sp-reveal sp-delay-500">
            <button className="sp-enter-btn" onClick={handleEnter} type="button">
              <span className="sp-sweep" />
              <span className="sp-label">Enter Recovera</span>
              <span className="sp-arrow">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M5 12h14" /><path d="m12 5 7 7-7 7" />
                </svg>
              </span>
            </button>
            <div className="sp-hint">PRESS TO INITIATE LAUNCH SEQUENCE</div>
          </div>

        </div>
      </main>

      {/* ── Footer ── */}
      <footer className="sp-footer">
        <span>LAT 37.7749° N · LON 122.4194° W</span>
        <span className="sp-footer-mid">ENCRYPTED CHANNEL · TLS 1.3</span>
        <span suppressHydrationWarning>
          {typeof window !== 'undefined' ? new Date().getUTCFullYear() + ' · RECOVERA LABS' : ''}
        </span>
      </footer>
    </div>
  );
}
