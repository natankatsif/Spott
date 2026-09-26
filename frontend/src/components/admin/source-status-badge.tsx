import { Badge } from "@/components/spell/badge";
import { Spinner } from "@/components/spell/spinner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { SourceStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

// docs/FRONTEND-11.md §3: indexed green · running blue + spinner · queued blue outline · pending grey ·
// failed red (+ last_error tooltip) · blocked amber · disabled grey outline
const VARIANT = {
  indexed: "green",
  running: "blue",
  queued: "outline",
  pending: "slate",
  failed: "red",
  blocked: "amber",
  disabled: "outline",
} as const satisfies Record<SourceStatus, string>;

export function SourceStatusBadge({ status, label, error }: { status: SourceStatus; label: string; error?: string | null }) {
  const badge = (
    <Badge
      className={cn(
        "gap-1.5 whitespace-nowrap rounded-full px-2 py-1 font-medium",
        status === "queued" && "border-blue-300 text-blue-700",
        status === "disabled" && "text-muted-foreground",
      )}
      variant={VARIANT[status]}
    >
      {status === "running" ? (
        <Spinner className="size-3" speed="fast" />
      ) : (
        <span aria-hidden className={cn("size-1.5 rounded-full bg-current", status === "queued" && "animate-pulse")} />
      )}
      {label}
    </Badge>
  );
  if (!(status === "failed" && error)) return badge;
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="cursor-help">{badge}</span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs break-words">{error}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
