"use client";

import { animate, motion, useMotionValue } from "framer-motion";
import { XIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { LogoMark } from "@/components/logo-mark";
import { UI } from "@/lib/i18n";
import { useUILang } from "@/lib/lang";
import { cn } from "@/lib/utils";

/**
 * The assistant as a site widget: a round button with the white mark on the flowing brand gradient, which can be
 * dragged anywhere and settles against the nearer side of the screen; opened, the chat in a panel on that side (the
 * whole screen on a phone). The chat is the real one (`/?embed=1`) in an iframe, mounted on the first open and kept
 * while closed, so closing never loses the conversation.
 *
 * The motion is MagneticSelect's (components/MagneticSelect.tsx): springs set by a damping ratio rather than a raw
 * damping value, and the disc's skin leaning a few pixels toward a cursor that comes near, on its own element so
 * it never fights the drag's transform.
 */

const SIZE = 64;
const MARGIN = 24;
/* MagneticSelect's springs: Bounce 55 → a damping ratio of 0.64, so the settle arrives with one small rebound */
const ZETA = 0.9 - 0.48 * 0.55;
const swing = (k: number, mass = 1) => ({ type: "spring" as const, stiffness: k, damping: 2 * Math.sqrt(k * mass) * ZETA, mass });
/* how far past the disc's edge the lean still reaches, and its ceiling (MagneticSelect: Give 50 → 4.5 px) */
const FADE = 44;
const LEAN = 4.5;
const KEY = "spottWidget.pos";

type Side = "left" | "right";

function readPos(): { side: Side; y: number } | null {
  try {
    const v = JSON.parse(window.localStorage.getItem(KEY) || "null");
    return v && (v.side === "left" || v.side === "right") && typeof v.y === "number" ? v : null;
  } catch {
    return null;
  }
}

export function SpottWidget({ src = "/?embed=1" }: { src?: string }) {
  const lang = useUILang();
  const w = UI[lang].widget;
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [hint, setHint] = useState(false);
  const [side, setSide] = useState<Side>("right");
  const [lean, setLean] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  // from press to release a clear layer covers the page: over a page's iframe the moves and the release would go to
  // the frame, and the drag would stop half way
  const [pressed, setPressed] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const moved = useRef(false);
  // the disc's place: an offset from its resting corner (bottom right), driven by the drag and by the springs
  const x = useMotionValue(0);
  const y = useMotionValue(0);

  // where it was left last time (this browser only), and back inside the screen when the window changes size
  useEffect(() => {
    const place = (animated: boolean) => {
      const saved = readPos();
      const s = saved?.side ?? "right";
      const tx = s === "left" ? -(window.innerWidth - SIZE - 2 * MARGIN) : 0;
      const ty = Math.max(-(window.innerHeight - SIZE - 2 * MARGIN), Math.min(0, saved?.y ?? 0));
      setSide(s);
      if (animated) {
        animate(x, tx, swing(420));
        animate(y, ty, swing(420));
      } else {
        x.set(tx);
        y.set(ty);
      }
    };
    place(false);
    const onResize = () => place(true);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [x, y]);

  // the greeting bubble: once, a moment after the page settles, until the widget is opened or it is closed
  useEffect(() => {
    const show = setTimeout(() => setHint(true), 1800);
    return () => clearTimeout(show);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  /* the lean, read off the pointer: nothing at the centre, full at the disc's edge, gone FADE past it — the same
     ramp as MagneticSelect, so the direction is never asked for where it is undefined */
  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    let raf = 0;
    let next = { x: 0, y: 0 };
    const publish = () => {
      raf = 0;
      setLean(next);
    };
    const read = (e: PointerEvent) => {
      const el = btn.current;
      if (!el) return;
      const b = el.getBoundingClientRect();
      const dx = e.clientX - (b.left + b.width / 2);
      const dy = e.clientY - (b.top + b.height / 2);
      const d = Math.hypot(dx, dy);
      const r = b.width / 2;
      const rise = Math.min(1, d / r);
      const away = d <= r ? 1 : Math.max(0, 1 - (d - r) / FADE);
      const drawn = rise * away * LEAN;
      next = drawn > 0 ? { x: (dx / (d || 1)) * drawn, y: (dy / (d || 1)) * drawn } : { x: 0, y: 0 };
      if (!raf) raf = requestAnimationFrame(publish);
    };
    document.addEventListener("pointermove", read, { passive: true });
    return () => {
      document.removeEventListener("pointermove", read);
      cancelAnimationFrame(raf);
    };
  }, []);

  const toggle = () => {
    setMounted(true);
    setHint(false);
    setOpen((o) => !o);
  };

  // let go: to the nearer side, kept inside the screen top to bottom, remembered
  const settle = () => {
    const el = btn.current;
    if (!el) return;
    const b = el.getBoundingClientRect();
    const s: Side = b.left + b.width / 2 < window.innerWidth / 2 ? "left" : "right";
    const tx = s === "left" ? -(window.innerWidth - SIZE - 2 * MARGIN) : 0;
    const ty = Math.max(-(window.innerHeight - SIZE - 2 * MARGIN), Math.min(0, y.get()));
    setSide(s);
    animate(x, tx, swing(380, 0.9));
    animate(y, ty, swing(380, 0.9));
    try {
      window.localStorage.setItem(KEY, JSON.stringify({ side: s, y: ty }));
    } catch {
      /* storage blocked: it stays put for this visit */
    }
  };

  return (
    <div className="pointer-events-none fixed inset-0 z-50">
      {/* the panel: on the disc's side on larger screens, the whole screen on a phone */}
      <section
        aria-hidden={!open}
        aria-label={w.title}
        className={cn(
          "pointer-events-auto absolute flex flex-col overflow-hidden bg-background shadow-[0_24px_60px_-12px_rgb(15_23_42/0.35)] transition-[opacity,transform] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]",
          "inset-0 sm:inset-auto sm:bottom-6 sm:h-[min(680px,calc(100dvh-3rem))] sm:w-[400px] sm:rounded-3xl sm:border",
          side === "right" ? "origin-bottom-right sm:right-[6.5rem]" : "origin-bottom-left sm:left-[6.5rem]",
          open ? "translate-y-0 scale-100 opacity-100" : "pointer-events-none translate-y-4 scale-95 opacity-0",
        )}
        role="dialog"
      >
        <header className="spott-flow relative flex shrink-0 items-center gap-3 px-4 py-3 text-white">
          <span className="flex size-9 items-center justify-center rounded-full bg-white/15 ring-1 ring-white/25">
            <LogoMark className="h-5 w-auto text-white drop-shadow-none" />
          </span>
          <div className="min-w-0 flex-1 leading-tight">
            <p className="font-semibold text-[15px]">{w.title}</p>
            <p className="truncate text-white/80 text-xs">{w.subtitle}</p>
          </div>
          <button
            aria-label={w.close}
            className="flex size-9 items-center justify-center rounded-full text-white/90 transition-colors hover:bg-white/20 hover:text-white active:scale-95"
            onClick={() => setOpen(false)}
            title={w.close}
            type="button"
          >
            <XIcon className="size-5" />
          </button>
        </header>
        <div className="relative min-h-0 flex-1 bg-background">
          {mounted && <iframe className="absolute inset-0 size-full border-0" src={src} title={w.title} />}
        </div>
      </section>

      {pressed && (
        <div
          aria-hidden
          className="pointer-events-auto fixed inset-0"
          onPointerCancel={() => setPressed(false)}
          onPointerUp={() => setPressed(false)}
          style={{ cursor: dragging ? "grabbing" : undefined }}
        />
      )}

      {/* the disc, and the greeting that rides along with it */}
      <motion.div
        className={cn("pointer-events-auto absolute right-6 bottom-6", open && "max-sm:hidden")}
        drag
        dragElastic={0.12}
        dragMomentum={false}
        onDragEnd={() => {
          setDragging(false);
          setPressed(false);
          settle();
        }}
        onDragStart={() => {
          moved.current = true;
          setDragging(true);
          setHint(false);
        }}
        onPointerDown={() => {
          moved.current = false;
          setPressed(true);
        }}
        style={{ x, y, width: SIZE, height: SIZE }}
      >
        <div
          className={cn(
            "absolute bottom-[calc(100%+12px)] flex w-max max-w-[240px] items-start gap-2 rounded-2xl border bg-card py-2.5 pr-2 pl-3.5 text-sm shadow-lg transition-[opacity,transform] duration-300",
            side === "right" ? "right-0 rounded-br-md" : "left-0 rounded-bl-md",
            hint && !open && !dragging ? "translate-y-0 opacity-100" : "pointer-events-none translate-y-2 opacity-0",
          )}
        >
          <button className="text-left" onClick={toggle} type="button">
            {w.hello}
          </button>
          <button aria-label={w.close} className="rounded-full p-1 text-muted-foreground hover:bg-muted" onClick={() => setHint(false)} type="button">
            <XIcon className="size-3.5" />
          </button>
        </div>

        <motion.button
          animate={{ scale: dragging ? 1.08 : 1 }}
          aria-expanded={open}
          aria-label={open ? w.close : w.open}
          className={cn(
            "relative size-full cursor-grab touch-none rounded-full outline-none focus-visible:ring-4 focus-visible:ring-[#4a8fd9]/40",
            dragging && "cursor-grabbing",
          )}
          onClick={() => {
            // a drag ends with a click too: only a press that stayed put opens it
            setPressed(false);
            if (!moved.current) toggle();
          }}
          ref={btn}
          transition={swing(520, 0.8)}
          type="button"
          whileTap={{ scale: 0.94 }}
        >
          {!open && <span aria-hidden className="spott-halo absolute -inset-1 rounded-full" />}
          {/* the lean lives on the skin, not on the button: the button's transform is the drag's and the springs' */}
          <span
            className="spott-flow absolute inset-0 flex items-center justify-center overflow-hidden rounded-full shadow-[0_10px_30px_-6px_rgb(42_59_135/0.6)] ring-1 ring-white/30"
            style={{
              transform: `translate(${lean.x.toFixed(2)}px, ${lean.y.toFixed(2)}px)`,
              transition: "transform 260ms cubic-bezier(0.22, 0.9, 0.28, 1)",
            }}
          >
            <LogoMark
              className={cn(
                "absolute h-7 w-auto text-white drop-shadow-[0_2px_4px_rgb(0_0_0/0.2)] transition-all duration-300",
                open ? "rotate-90 scale-50 opacity-0" : "rotate-0 scale-100 opacity-100",
              )}
            />
            <XIcon className={cn("absolute size-6 text-white transition-all duration-300", open ? "rotate-0 scale-100 opacity-100" : "-rotate-90 scale-50 opacity-0")} />
          </span>
        </motion.button>
      </motion.div>
    </div>
  );
}
