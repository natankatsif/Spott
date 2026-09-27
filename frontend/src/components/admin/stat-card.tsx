import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/** A number with its label; `hint` under it, `aside` on the right (an icon, a mini chart). While the number loads
 * (`value` undefined) the card and its label are already there and only the number and hint are skeletons. */
export function StatCard({
  label,
  value,
  hint,
  aside,
  className,
}: {
  label: string;
  value?: ReactNode;
  hint?: ReactNode;
  aside?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-end justify-between gap-3 rounded-2xl border bg-card p-4", className)}>
      <div className="min-w-0">
        <p className="text-muted-foreground text-xs">{label}</p>
        {value === undefined ? (
          <>
            <Skeleton className="mt-2 h-7 w-16 rounded-md" />
            <Skeleton className="mt-1.5 h-3 w-10 rounded" />
          </>
        ) : (
          <>
            <p className="mt-1 font-semibold text-2xl tabular-nums tracking-tight">{value}</p>
            {hint && <p className="mt-0.5 text-muted-foreground text-xs">{hint}</p>}
          </>
        )}
      </div>
      {aside}
    </div>
  );
}
