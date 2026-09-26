import { StarIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/** Read-only 1–5 stars; fractional values fill the last star partly. */
export function Stars({ value, className, size = 14 }: { value: number; className?: string; size?: number }) {
  return (
    <span aria-label={`${value.toFixed(1)} / 5`} className={cn("inline-flex items-center gap-0.5", className)} role="img">
      {[1, 2, 3, 4, 5].map((n) => {
        const fill = Math.min(1, Math.max(0, value - (n - 1)));
        return (
          <span className="relative inline-block" key={n} style={{ width: size, height: size }}>
            <StarIcon className="absolute inset-0 text-muted-foreground/30" fill="currentColor" size={size} strokeWidth={0} />
            <span className="absolute inset-0 overflow-hidden" style={{ width: `${fill * 100}%` }}>
              <StarIcon className="text-amber-400" fill="currentColor" size={size} strokeWidth={0} />
            </span>
          </span>
        );
      })}
    </span>
  );
}

/** Horizontal bars 5★ → 1★ with count and share. */
export function StarBars({ perStar }: { perStar: Record<string, number> }) {
  const total = Object.values(perStar).reduce((a, b) => a + b, 0) || 1;
  return (
    <div className="flex flex-col gap-2">
      {[5, 4, 3, 2, 1].map((n) => {
        const count = perStar[String(n)] ?? 0;
        const share = count / total;
        return (
          <div className="flex items-center gap-3 text-sm" key={n}>
            <span className="flex w-8 shrink-0 items-center gap-1 text-muted-foreground tabular-nums">
              {n}
              <StarIcon className="text-amber-400" fill="currentColor" size={12} strokeWidth={0} />
            </span>
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
              <div
                className={cn("h-full rounded-full transition-[width] duration-700", n <= 2 ? "bg-destructive/70" : n === 3 ? "bg-amber-400" : "bg-brand")}
                style={{ width: `${share * 100}%` }}
              />
            </div>
            <span className="w-14 shrink-0 text-right text-muted-foreground tabular-nums">
              {count} <span className="text-xs">({Math.round(share * 100)}%)</span>
            </span>
          </div>
        );
      })}
    </div>
  );
}
