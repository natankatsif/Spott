"use client";

import { MonitorIcon, MoonIcon, SunIcon } from "lucide-react";
import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";
import type { UILang } from "@/lib/i18n";
import { useUILang } from "@/lib/lang";
import { cn } from "@/lib/utils";

const LABELS: Record<UILang, Record<"system" | "light" | "dark", string>> = {
  ro: { system: "Ca în sistem", light: "Temă deschisă", dark: "Temă întunecată" },
  ru: { system: "Как в системе", light: "Светлая тема", dark: "Тёмная тема" },
  en: { system: "System theme", light: "Light theme", dark: "Dark theme" },
};
const OPTIONS = [
  ["system", MonitorIcon],
  ["light", SunIcon],
  ["dark", MoonIcon],
] as const;

/** System · light · dark, remembered in this browser (next-themes); the same pill look as the language switch. */
export function ThemeSwitch({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme();
  const lang = useUILang();
  // the saved choice is only known in the browser: render the pill without a pick until then (no hydration flash)
  const mounted = useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
  const current = mounted ? (theme ?? "system") : null;
  return (
    <div className={cn("flex w-fit items-center rounded-full bg-muted/80 p-0.5", className)} role="radiogroup">
      {OPTIONS.map(([value, Icon]) => (
        <button
          aria-checked={current === value}
          aria-label={LABELS[lang][value]}
          className={cn(
            "flex h-6 w-8 items-center justify-center rounded-full transition-colors",
            current === value ? "bg-card text-foreground shadow-sm" : "text-foreground/50 hover:text-foreground/80",
          )}
          key={value}
          onClick={() => setTheme(value)}
          role="radio"
          title={LABELS[lang][value]}
          type="button"
        >
          <Icon className="size-3.5" />
        </button>
      ))}
    </div>
  );
}
