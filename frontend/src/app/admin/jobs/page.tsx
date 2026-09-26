"use client";

import { CircleAlertIcon, SquareIcon, TerminalIcon, WorkflowIcon } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/admin/empty-state";
import { LogView } from "@/components/admin/log-view";
import { PageHeader } from "@/components/admin/page-header";
import { StageProgress } from "@/components/admin/stage-progress";
import { StatCard } from "@/components/admin/stat-card";
import { StatusBadge } from "@/components/admin/status-badge";
import { Spinner } from "@/components/spell/spinner";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { admin, isActive, useAdminQuery } from "@/lib/admin";
import { errorText } from "@/lib/admin-errors";
import { dateTime, duration, number, timeAgo } from "@/lib/admin-format";
import { ADMIN_UI, type AdminText } from "@/lib/admin-i18n";
import type { Job, JobStatus, SourceRow } from "@/lib/api";
import type { UILang } from "@/lib/i18n";
import { useUILang } from "@/lib/lang";
import { useApiMode } from "@/lib/mode";

const FILTERS: (JobStatus | "all")[] = ["all", "running", "queued", "done", "failed", "cancelled"];

export default function JobsPage() {
  // useSearchParams needs a Suspense boundary in the app router
  return (
    <Suspense>
      <Jobs />
    </Suspense>
  );
}

function Jobs() {
  const lang = useUILang();
  const t = ADMIN_UI[lang];
  const mode = useApiMode();
  const router = useRouter();
  const params = useSearchParams();
  const openId = params.get("id") ? Number(params.get("id")) : null;
  const [filter, setFilter] = useState<JobStatus | "all">("all");

  const jobs = useAdminQuery(`jobs-${mode}-${filter}`, () => admin.jobs(filter === "all" ? undefined : filter), (list) =>
    list.some(isActive) ? 1500 : 10000,
  );
  const sources = useAdminQuery(`sources-${mode}`, admin.sources);
  const siteOf = (job: Job) =>
    job.source_id == null ? t.jobs.allSources : (sources.data?.find((s) => s.id === job.source_id)?.site_id ?? `#${job.source_id}`);

  const open = (id: number | null) => router.replace(id == null ? "/admin/jobs" : `/admin/jobs?id=${id}`, { scroll: false });

  return (
    <>
      <PageHeader subtitle={t.jobs.subtitle} title={t.jobs.title} />

      <Tabs className="mb-4" onValueChange={(v) => setFilter(v as JobStatus | "all")} value={filter}>
        <TabsList className="h-auto flex-wrap rounded-xl">
          {FILTERS.map((f) => (
            <TabsTrigger className="rounded-lg" key={f} value={f}>
              {f === "all" ? t.common.all : t.status[f]}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {jobs.error && !jobs.data ? (
        <EmptyState
          action={<Button onClick={jobs.reload} variant="outline">{t.common.retry}</Button>}
          hint={errorText(jobs.error, lang)}
          icon={WorkflowIcon}
          title={t.common.loadError}
        />
      ) : jobs.loading ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton className="h-20 rounded-2xl" key={i} />
          ))}
        </div>
      ) : jobs.data?.length === 0 ? (
        <EmptyState hint={t.jobs.workerHint} icon={WorkflowIcon} title={t.jobs.empty} />
      ) : (
        <ul className="flex flex-col gap-2">
          {jobs.data?.map((job) => (
            <li key={job.id}>
              <button
                className="w-full rounded-2xl border bg-card p-4 text-left transition-colors hover:border-ring/40"
                onClick={() => open(job.id)}
                type="button"
              >
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="font-medium">{siteOf(job)}</span>
                  <span className="text-muted-foreground text-xs">
                    #{job.id} · {t.jobs.kind[job.kind]}
                  </span>
                  <span className="ml-auto flex items-center gap-3">
                    <span className="text-muted-foreground text-xs tabular-nums">
                      {isActive(job)
                        ? `${Math.round(job.percent)}% · ${t.jobs.eta} ${duration(job.eta_s)}`
                        : timeAgo(job.finished_at ?? job.started_at, lang)}
                    </span>
                    <StatusBadge label={t.status[job.status]} status={job.status} />
                  </span>
                </div>
                <StageProgress className="mt-3" job={job} />
              </button>
            </li>
          ))}
        </ul>
      )}

      <JobSheet id={openId} lang={lang} onClose={() => open(null)} sources={sources.data} t={t} />
    </>
  );
}

function JobSheet({ id, t, lang, sources, onClose }: { id: number | null; t: AdminText; lang: UILang; sources?: SourceRow[]; onClose: () => void }) {
  const mode = useApiMode();
  const query = useAdminQuery(`job-${mode}-${id}`, () => (id == null ? Promise.resolve(null) : admin.job(id)), (job) =>
    isActive(job) ? 1500 : null,
  );
  const job = query.data;
  const [stopping, setStopping] = useState(false);
  const site = job ? (job.source_id == null ? t.jobs.allSources : (sources?.find((s) => s.id === job.source_id)?.site_id ?? `#${job.source_id}`)) : "";

  const cancel = async () => {
    if (!job) return;
    setStopping(true);
    try {
      await admin.cancelJob(job.id);
      toast.success(t.jobs.cancelled);
      query.reload();
    } catch (e) {
      toast.error(errorText(e, lang));
    } finally {
      setStopping(false);
    }
  };

  // while running the ETA says how long is left; the duration is shown once the job is over
  const took = job?.started_at && job.finished_at ? (Date.parse(job.finished_at) - Date.parse(job.started_at)) / 1000 : null;
  const stats = Object.entries(job?.stats ?? {});

  return (
    <Sheet onOpenChange={(o) => !o && onClose()} open={id != null}>
      <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-xl">
        <SheetHeader className="border-b">
          <SheetTitle className="flex items-center gap-3">
            {site || <Skeleton className="h-5 w-32" />}
            {job && <StatusBadge label={t.status[job.status]} status={job.status} />}
          </SheetTitle>
          <SheetDescription>{job ? `#${job.id} · ${t.jobs.kind[job.kind]}` : "…"}</SheetDescription>
        </SheetHeader>

        {!job ? (
          <div className="flex flex-col gap-3 p-4">
            <Skeleton className="h-24 rounded-2xl" />
            <Skeleton className="h-40 rounded-2xl" />
          </div>
        ) : (
          <div className="flex flex-col gap-5 p-4">
            <section className="rounded-2xl border bg-card p-4">
              <div className="mb-4 flex items-end justify-between gap-3">
                <div>
                  <p className="font-semibold text-4xl tabular-nums tracking-tight">
                    {Math.round(job.percent)}
                    <span className="text-muted-foreground text-xl">%</span>
                  </p>
                  {job.stage && isActive(job) && job.stage_total > 0 && (
                    <p className="mt-1 text-muted-foreground text-sm">
                      {t.jobs.stage[job.stage]}: {t.jobs.stepOf(job.stage_done, job.stage_total)}
                    </p>
                  )}
                </div>
                {isActive(job) && (
                  <div className="text-right">
                    <p className="text-muted-foreground text-xs">{t.jobs.eta}</p>
                    <p className="font-medium tabular-nums">{duration(job.eta_s)}</p>
                  </div>
                )}
              </div>
              <StageProgress job={job} labels={t.jobs.stage} />
              <dl className="mt-4 grid grid-cols-3 gap-2 border-t pt-3 text-xs">
                <div>
                  <dt className="text-muted-foreground">{t.jobs.started}</dt>
                  <dd className="mt-0.5 font-medium">{dateTime(job.started_at, lang)}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">{t.jobs.finished}</dt>
                  <dd className="mt-0.5 font-medium">{dateTime(job.finished_at, lang)}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">{t.jobs.duration}</dt>
                  <dd className="mt-0.5 font-medium tabular-nums">{duration(took)}</dd>
                </div>
              </dl>
            </section>

            {job.error && (
              <div className="flex gap-2 rounded-2xl border border-destructive/30 bg-destructive/5 p-3 text-destructive text-sm">
                <CircleAlertIcon className="mt-0.5 size-4 shrink-0" />
                <div>
                  <p className="font-medium">{t.jobs.error}</p>
                  <p className="mt-0.5 break-words">{job.error}</p>
                </div>
              </div>
            )}

            {stats.length > 0 && (
              <section>
                <h3 className="mb-2 font-medium text-sm">{t.jobs.stats}</h3>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                  {stats.map(([k, v]) => (
                    <StatCard
                      className={k === "errors" && v > 0 ? "border-destructive/30" : undefined}
                      key={k}
                      label={t.jobs.stat[k] ?? k}
                      value={<span className="text-xl">{number(v, lang)}</span>}
                    />
                  ))}
                </div>
              </section>
            )}

            <section>
              <h3 className="mb-2 flex items-center gap-1.5 font-medium text-sm">
                <TerminalIcon className="size-4 text-muted-foreground" /> {t.jobs.log}
              </h3>
              <LogView lines={job.log_tail} title={`job #${job.id}`} />
            </section>

            {isActive(job) && (
              <Button className="self-start rounded-xl" disabled={stopping} onClick={cancel} variant="outline">
                {stopping ? <Spinner className="size-4" /> : <SquareIcon className="fill-current" />}
                {t.jobs.cancel}
              </Button>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
