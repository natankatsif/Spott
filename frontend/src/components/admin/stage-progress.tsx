"use client";

import { ShimmerText } from "@/components/spell/shimmer-text";
import type { Job } from "@/lib/api";
import { STAGE_WEIGHTS, STAGES } from "@/lib/admin-i18n";
import { cn } from "@/lib/utils";

type Props = {
  job: Pick<Job, "status" | "stage" | "percent">;
  /** stage names under the bar; omit for the compact table variant */
  labels?: Record<NonNullable<Job["stage"]>, string>;
  className?: string;
};

/**
 * One bar split into the four pipeline stages, each as wide as its share of `percent`
 * (crawl 20, download 20, parse 40, index 20). Done stages are solid, the running one fills and shimmers.
 */
export function StageProgress({ job, labels, className }: Props) {
  const failed = job.status === "failed";
  const stopped = job.status === "cancelled";
  const running = job.status === "running";
  const starts = STAGES.map((_, i) => STAGES.slice(0, i).reduce((a, s) => a + STAGE_WEIGHTS[s], 0));
  return (
    <div className={cn("w-full", className)}>
      <div className="flex w-full gap-1" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(job.percent)}>
        {STAGES.map((stage, i) => {
          const width = STAGE_WEIGHTS[stage];
          const fill = Math.min(1, Math.max(0, (job.percent - starts[i]) / width));
          const active = job.stage === stage && fill < 1 && job.status !== "done";
          return (
            <div className="relative h-2 overflow-hidden rounded-full bg-muted" key={stage} style={{ flexGrow: width, flexBasis: 0 }}>
              <div
                className={cn(
                  "absolute inset-y-0 left-0 rounded-full transition-[width] duration-700 ease-out",
                  failed && active ? "bg-destructive" : stopped ? "bg-muted-foreground/40" : fill === 1 ? "bg-brand" : "bg-brand/70",
                )}
                style={{ width: `${fill * 100}%` }}
              />
              {active && running && (
                <div className="absolute inset-0 animate-[stage-sheen_1.6s_linear_infinite] bg-linear-to-r from-transparent via-white/60 to-transparent" />
              )}
            </div>
          );
        })}
      </div>
      {labels && (
        <div className="mt-2 flex w-full gap-1 text-xs">
          {STAGES.map((stage, i) => {
            const passed = job.percent >= STAGES.slice(0, i + 1).reduce((a, s) => a + STAGE_WEIGHTS[s], 0);
            const active = job.stage === stage && !passed;
            return (
              <div className="min-w-0 truncate" key={stage} style={{ flexGrow: STAGE_WEIGHTS[stage], flexBasis: 0 }}>
                {active && running ? (
                  <ShimmerText className="font-medium">{labels[stage]}</ShimmerText>
                ) : (
                  <span className={cn(passed || active ? "font-medium text-foreground" : "text-muted-foreground")}>{labels[stage]}</span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
