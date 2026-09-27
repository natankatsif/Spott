import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** A number with its label; `hint` under it, `aside` on the right (an icon, a mini chart). */
export function StatCard({
  label,
  value,
  hint,
  aside,
  className,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  aside?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-end justify-between gap-3 rounded-2xl border bg-card p-4", className)}>
      <div className="min-w-0">
        <p className="text-muted-foreground text-xs">{label}</p>
        <p className="mt-1 font-semibold text-2xl tabular-nums tracking-tight">{value}</p>
        {hint && <p className="mt-0.5 text-muted-foreground text-xs">{hint}</p>}
      </div>
      {aside}
    </div>
  );
}
