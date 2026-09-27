"use client";

import { MinusIcon, PlusIcon, XIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { LogoMark } from "@/components/logo-mark";
import { UI } from "@/lib/i18n";
import { useUILang } from "@/lib/lang";
import { cn } from "@/lib/utils";

/**
 * The assistant as a site widget: a round button in the corner with the white mark on the flowing brand gradient,
 * and, opened, the chat in a panel above it (a full screen on a phone). The chat is the real one (`/?embed=1`) in an
 * iframe; it is mounted on the first open and kept while closed, so minimizing never loses the conversation.
 * The panel's own header has the two things a small window needs: a new chat (+) and minimize (−).
 */
export function SpottWidget({ src = "/?embed=1" }: { src?: string }) {
  const lang = useUILang();
  const w = UI[lang].widget;
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [hint, setHint] = useState(false);
  const frame = useRef<HTMLIFrameElement>(null);

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

  const toggle = () => {
    setMounted(true);
    setHint(false);
    setOpen((o) => !o);
  };
  const newChat = () => frame.current?.contentWindow?.postMessage({ type: "spott:new-chat" }, window.location.origin);

  return (
    <div className="pointer-events-none fixed inset-0 z-50">
      {/* the panel: above the button on larger screens, the whole screen on a phone */}
      <section
        aria-hidden={!open}
        aria-label={w.title}
        className={cn(
          "pointer-events-auto absolute flex flex-col overflow-hidden bg-background shadow-[0_24px_60px_-12px_rgb(15_23_42/0.35)] transition-[opacity,transform] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]",
          "inset-0 sm:inset-auto sm:right-6 sm:bottom-24 sm:h-[min(680px,calc(100dvh-8rem))] sm:w-[400px] sm:rounded-3xl sm:border",
          "origin-bottom-right",
          open ? "translate-y-0 scale-100 opacity-100" : "pointer-events-none translate-y-4 scale-95 opacity-0",
        )}
        role="dialog"
      >
        <header className="spott-flow relative flex shrink-0 items-center gap-3 px-4 py-3 text-white">
          <span className="flex size-9 items-center justify-center rounded-xl bg-white/15 ring-1 ring-white/25">
            <LogoMark className="h-5 w-auto text-white drop-shadow-none" />
          </span>
          <div className="min-w-0 flex-1 leading-tight">
            <p className="font-semibold text-[15px]">{w.title}</p>
            <p className="truncate text-white/80 text-xs">{w.subtitle}</p>
          </div>
          {/* the small menu: new chat and minimize, nothing else */}
          <div className="flex items-center gap-0.5 rounded-full bg-white/15 p-0.5 ring-1 ring-white/20">
            <button
              aria-label={w.newChat}
              className="flex size-8 items-center justify-center rounded-full transition-colors hover:bg-white/20 active:scale-95"
              onClick={newChat}
              title={w.newChat}
              type="button"
            >
              <PlusIcon className="size-4" />
            </button>
            <button
              aria-label={w.minimize}
              className="flex size-8 items-center justify-center rounded-full transition-colors hover:bg-white/20 active:scale-95"
              onClick={() => setOpen(false)}
              title={w.minimize}
              type="button"
            >
              <MinusIcon className="size-4" />
            </button>
          </div>
        </header>
        <div className="relative min-h-0 flex-1 bg-background">
          {mounted && <iframe className="absolute inset-0 size-full border-0" ref={frame} src={src} title={w.title} />}
        </div>
      </section>

      {/* the greeting bubble */}
      <div
        className={cn(
          "pointer-events-auto absolute right-6 bottom-[6.25rem] flex max-w-[260px] items-start gap-2 rounded-2xl rounded-br-md border bg-card py-2.5 pr-2 pl-3.5 text-sm shadow-lg transition-[opacity,transform] duration-300",
          hint && !open ? "translate-y-0 opacity-100" : "pointer-events-none translate-y-2 opacity-0",
        )}
      >
        <button className="text-left" onClick={toggle} type="button">
          {w.hello}
        </button>
        <button aria-label={w.minimize} className="rounded-full p-1 text-muted-foreground hover:bg-muted" onClick={() => setHint(false)} type="button">
          <XIcon className="size-3.5" />
        </button>
      </div>

      {/* the button: hidden on a phone while the panel covers the screen */}
      <button
        aria-expanded={open}
        aria-label={open ? w.minimize : w.open}
        className={cn(
          "group pointer-events-auto absolute right-6 bottom-6 size-16 rounded-full outline-none transition-transform duration-300 ease-[cubic-bezier(0.175,0.885,0.32,1.275)] hover:scale-105 focus-visible:ring-4 focus-visible:ring-[#4a8fd9]/40 active:scale-95",
          open && "max-sm:scale-0",
        )}
        onClick={toggle}
        type="button"
      >
        {!open && <span aria-hidden className="spott-halo absolute -inset-1 rounded-full" />}
        <span className="spott-flow relative flex size-full items-center justify-center overflow-hidden rounded-full shadow-[0_10px_30px_-6px_rgb(42_59_135/0.6)] ring-1 ring-white/30">
          <LogoMark className={cn("absolute h-7 w-auto text-white drop-shadow-[0_2px_4px_rgb(0_0_0/0.2)] transition-all duration-300", open ? "rotate-90 scale-50 opacity-0" : "rotate-0 scale-100 opacity-100")} />
          <XIcon className={cn("absolute size-6 text-white transition-all duration-300", open ? "rotate-0 scale-100 opacity-100" : "-rotate-90 scale-50 opacity-0")} />
        </span>
      </button>
    </div>
  );
}
