"use client";

import type { UILang } from "@/lib/i18n";
import { setUILang, UI_LANGS, useUILang } from "@/lib/lang";
import { cn } from "@/lib/utils";

/** RO · RU · EN pill with a sliding thumb, the same switch as in the chat input; synced site-wide. */
export function LangSwitch({ className }: { className?: string }) {
  const lang = useUILang();
  return (
    <div className={cn("relative flex w-fit items-center rounded-full bg-muted/80 p-0.5", className)} role="radiogroup">
      <span
        aria-hidden
        className="absolute top-0.5 bottom-0.5 left-0.5 w-9 rounded-full bg-card shadow-sm transition-transform duration-400 ease-[cubic-bezier(0.175,0.885,0.32,1.275)] motion-reduce:transition-none"
        style={{ transform: `translateX(${UI_LANGS.indexOf(lang) * 100}%)` }}
      />
      {UI_LANGS.map((l: UILang) => (
        <button
          aria-checked={lang === l}
          className={cn(
            "relative z-[1] w-9 rounded-full py-0.5 text-center font-semibold text-xs uppercase transition-colors",
            lang === l ? "text-foreground" : "text-foreground/50 hover:text-foreground/80",
          )}
          key={l}
          onClick={() => setUILang(l)}
          role="radio"
          type="button"
        >
          {l}
        </button>
      ))}
    </div>
  );
}
