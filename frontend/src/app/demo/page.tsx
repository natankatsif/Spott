"use client";

import { ArrowLeftIcon } from "lucide-react";
import Link from "next/link";
import { SpottWidget } from "@/components/widget/spott-widget";
import { UI } from "@/lib/i18n";
import { useUILang } from "@/lib/lang";

/**
 * How the assistant would look on the City Hall's site. chisinau.md forbids being shown in a frame
 * (X-Frame-Options: SAMEORIGIN), so the site behind the widget is a full-page snapshot of it (public/demo, taken from
 * the Wayback Machine copy of 25.09.2026): one for wide screens, one for phones, scrolled like the page itself.
 */
export default function DemoPage() {
  const t = UI[useUILang()];
  return (
    <div className="min-h-dvh bg-white">
      <picture>
        <source media="(max-width: 639px)" srcSet="/demo/chisinau-phone.jpg" />
        <img alt="chisinau.md" className="block h-auto w-full select-none" draggable={false} src="/demo/chisinau-desktop.jpg" />
      </picture>
      <Link
        className="fixed top-3 left-3 z-40 flex items-center gap-1.5 rounded-full bg-black/70 px-3 py-1.5 text-white text-xs backdrop-blur transition-colors hover:bg-black/80"
        href="/"
        title={t.widget.demoNote}
      >
        <ArrowLeftIcon className="size-3.5" /> Spott
      </Link>
      <SpottWidget />
    </div>
  );
}
