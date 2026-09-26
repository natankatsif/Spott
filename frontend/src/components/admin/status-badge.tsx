import { Badge } from "@/components/spell/badge";
import { Spinner } from "@/components/spell/spinner";
import type { JobStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

const VARIANT = {
  queued: "slate",
  running: "blue",
  done: "green",
  failed: "red",
  cancelled: "outline",
} as const satisfies Record<JobStatus, string>;

/** Job status chip: colour by status; a spinner while running, a dot otherwise. */
export function StatusBadge({ status, label, className }: { status: JobStatus; label: string; className?: string }) {
  return (
    <Badge className={cn("gap-1.5 rounded-full px-2 py-1 font-medium", className)} variant={VARIANT[status]}>
      {status === "running" ? (
        <Spinner className="size-3" speed="fast" />
      ) : (
        <span aria-hidden className={cn("size-1.5 rounded-full bg-current", status === "queued" && "animate-pulse")} />
      )}
      {label}
    </Badge>
  );
}
