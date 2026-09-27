"use client";

import { ArrowLeftIcon } from "lucide-react";
import Link from "next/link";
import { SpottWidget } from "@/components/widget/spott-widget";
import { UI } from "@/lib/i18n";
import { useUILang } from "@/lib/lang";

// the site the demo puts the widget on: it allows being shown in a frame (no X-Frame-Options / frame-ancestors)
const SITE = "https://educatieonline.md/";

/** How the assistant would look on a public site: the live site in a full-screen frame, the widget over it. */
export default function DemoPage() {
  const t = UI[useUILang()];
  return (
    <div className="fixed inset-0 bg-white">
      <iframe className="absolute inset-0 size-full border-0" src={SITE} title="educatieonline.md" />
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
