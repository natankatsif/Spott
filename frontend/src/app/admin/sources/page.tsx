"use client";

import {
  CheckIcon,
  DatabaseIcon,
  FileTextIcon,
  GlobeIcon,
  LinkIcon,
  MoreHorizontalIcon,
  PowerIcon,
  RefreshCwIcon,
  SearchIcon,
  SquareIcon,
  TagIcon,
  Trash2Icon,
  WorkflowIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/admin/empty-state";
import { GapsSection } from "@/components/admin/gaps-section";
import { PageHeader } from "@/components/admin/page-header";
import { SourceStatusBadge } from "@/components/admin/source-status-badge";
import { StageProgress } from "@/components/admin/stage-progress";
import { StatCard } from "@/components/admin/stat-card";
import { CopyButton } from "@/components/spell/copy-button";
import { Spinner } from "@/components/spell/spinner";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { admin, useAdminQuery } from "@/lib/admin";
import { errorText } from "@/lib/admin-errors";
import { duration, number, timeAgo } from "@/lib/admin-format";
import { ADMIN_UI, type AdminText, CATEGORIES } from "@/lib/admin-i18n";
import { ApiRequestError, type SourceRow, type SourceStatus } from "@/lib/api";
import type { UILang } from "@/lib/i18n";
import { useUILang } from "@/lib/lang";
import { useApiMode } from "@/lib/mode";
import { cn } from "@/lib/utils";

type Filter = "all" | "indexed" | "active" | "pending" | "problems";
const FILTERS: Filter[] = ["all", "indexed", "active", "pending", "problems"];
const IN_FILTER: Record<Filter, (s: SourceStatus) => boolean> = {
  all: () => true,
  indexed: (s) => s === "indexed",
  active: (s) => s === "running" || s === "queued",
  pending: (s) => s === "pending",
  problems: (s) => s === "failed" || s === "blocked" || s === "disabled",
};
// running first, then what needs attention; sources that can't be processed at the end
const ORDER: Record<SourceStatus, number> = { running: 0, queued: 1, failed: 2, indexed: 3, pending: 4, disabled: 5, blocked: 6 };

const isBusy = (r: SourceRow) => r.status === "running" || r.status === "queued";
/** A site shows its domain (or title); a document its title or file name. */
const titleOf = (r: SourceRow) =>
  r.title ?? (r.kind === "document" ? decodeURIComponent(r.url.split("/").filter(Boolean).at(-1) ?? r.site_id) : r.site_id);

export default function SourcesPage() {
  const lang = useUILang();
  const t = ADMIN_UI[lang];
  const mode = useApiMode();
  // docs/API.md: poll every 2 s while any row is running/queued, stop when none is (an add or a refresh reloads)
  const query = useAdminQuery(`sources-${mode}`, admin.sources, (d) => (d.sources.some(isBusy) ? 2000 : null));
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [deleting, setDeleting] = useState<SourceRow | null>(null);
  const [flash, setFlash] = useState<number | null>(null);
  const addRef = useRef<HTMLInputElement>(null);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (query.data?.sources ?? [])
      .filter((r) => IN_FILTER[filter](r.status))
      .filter((r) => !needle || `${r.url} ${r.site_id} ${r.title ?? ""} ${r.category ?? ""}`.toLowerCase().includes(needle))
      .toSorted((a, b) => ORDER[a.status] - ORDER[b.status]);
  }, [query.data, q, filter]);

  const counts = useMemo(() => {
    const all = query.data?.sources ?? [];
    return Object.fromEntries(FILTERS.map((f) => [f, all.filter((r) => IN_FILTER[f](r.status)).length])) as Record<Filter, number>;
  }, [query.data]);

  const highlight = (id: number) => {
    setFlash(id);
    setTimeout(() => setFlash((f) => (f === id ? null : f)), 2500);
  };

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      toast.success(ok);
      query.reload();
    } catch (e) {
      toast.error(errorText(e, lang));
    }
  };

  const totals = query.data?.totals;

  return (
    <>
      <PageHeader subtitle={t.sources.subtitle} title={t.sources.title} />

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          hint={totals && `/ ${number(totals.sites_total, lang)}`}
          label={t.sources.totals.sites}
          value={totals && number(totals.sites_indexed, lang)}
        />
        <StatCard label={t.sources.totals.pages} value={totals && number(totals.pages, lang)} />
        <StatCard
          hint={totals && `/ ${number(totals.documents_found, lang)}`}
          label={t.sources.totals.documents}
          value={totals && number(totals.documents_downloaded, lang)}
        />
        <StatCard label={t.sources.totals.chunks} value={totals && number(totals.chunks, lang)} />
      </div>

      <AddSource inputRef={addRef} lang={lang} onAdded={(id) => { query.reload(); if (id) highlight(id); }} t={t} />

      <GapsSection lang={lang} onAddSource={() => { addRef.current?.focus(); addRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }); }} t={t} />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap rounded-full bg-muted/80 p-0.5 text-xs" role="radiogroup">
          {FILTERS.map((f) => (
            <button
              aria-checked={filter === f}
              className={cn(
                "flex items-center gap-1.5 rounded-full px-3 py-1 font-medium transition-colors",
                filter === f ? "bg-card text-foreground shadow-sm" : "text-foreground/50 hover:text-foreground/80",
              )}
              key={f}
              onClick={() => setFilter(f)}
              role="radio"
              type="button"
            >
              {t.sources.filter[f]}
              {query.data && <span className="tabular-nums opacity-60">{counts[f]}</span>}
            </button>
          ))}
        </div>
        <div className="relative ml-auto w-full max-w-xs">
          <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input className="h-9 rounded-xl bg-card pl-9" onChange={(e) => setQ(e.target.value)} placeholder={t.sources.search} value={q} />
        </div>
      </div>

      {query.error && !query.data ? (
        <EmptyState
          action={<Button onClick={query.reload} variant="outline">{t.common.retry}</Button>}
          hint={errorText(query.error, lang)}
          icon={DatabaseIcon}
          title={t.common.loadError}
        />
      ) : query.data && query.data.sources.length === 0 ? (
        <EmptyState icon={DatabaseIcon} title={t.sources.empty} />
      ) : (
        <div className="overflow-hidden rounded-2xl border bg-card">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="pl-4">{t.sources.col.source}</TableHead>
                <TableHead className="hidden xl:table-cell">{t.sources.col.category}</TableHead>
                <TableHead className="w-60">{t.sources.col.status}</TableHead>
                <TableHead className="hidden text-right lg:table-cell">{t.sources.col.pages}</TableHead>
                <TableHead className="hidden text-right lg:table-cell">{t.sources.col.documents}</TableHead>
                <TableHead className="hidden text-right md:table-cell">{t.sources.col.chunks}</TableHead>
                <TableHead className="hidden md:table-cell">{t.sources.col.lastCrawled}</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.loading &&
                Array.from({ length: 6 }, (_, i) => (
                  <TableRow key={i}>
                    <TableCell className="pl-4" colSpan={8}>
                      <Skeleton className="h-9 w-full rounded-lg" />
                    </TableCell>
                  </TableRow>
                ))}
              {rows.map((row) => (
                <SourceTableRow
                  flash={flash === row.id}
                  key={row.id}
                  lang={lang}
                  onAct={act}
                  onDelete={setDeleting}
                  row={row}
                  t={t}
                />
              ))}
              {query.data && rows.length === 0 && (
                <TableRow>
                  <TableCell className="py-10 text-center text-muted-foreground" colSpan={8}>
                    {t.sources.noMatch}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      )}

      <DeleteDialog lang={lang} onClose={() => setDeleting(null)} onDeleted={query.reload} row={deleting} t={t} />
    </>
  );
}

/** One input + one button: POST {url}; the server decides kind, category and crawl settings. */
function AddSource({ t, lang, inputRef, onAdded }: { t: AdminText; lang: UILang; inputRef: React.RefObject<HTMLInputElement | null>; onAdded: (id: number | null) => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const link = url.trim();
    if (!link) return;
    setBusy(true);
    try {
      const r = await admin.addSource(link);
      const d = r.detected;
      // docs/FRONTEND-11.md §3: 201 new · 200 merged · robots blocked (saved, no crawl)
      if (r.robots === "blocked") toast.warning(t.sources.blocked, { description: d.reason });
      else if (r.merged_into != null) toast.success(t.sources.merged(r.site_id), { description: d.reason });
      else toast.success(t.sources.added(d.kind, d.category, d.crawl_depth), { description: d.reason });
      setUrl("");
      onAdded(r.merged_into ?? r.id);
    } catch (err) {
      const code = err instanceof ApiRequestError ? err.body.error : null;
      if (code === "conflict") toast.error(t.sources.exists, { description: (err as ApiRequestError).body.message });
      else if (code === "validation_error") toast.error(t.sources.unreachable, { description: (err as ApiRequestError).body.message });
      else toast.error(errorText(err, lang));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="mb-8 rounded-2xl border bg-card p-3 shadow-[0_10px_30px_-24px_rgb(15_42_74/0.5)]" onSubmit={submit}>
      <div className="flex gap-2">
        <div className="relative min-w-0 flex-1">
          <LinkIcon className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label={t.sources.addPlaceholder}
            className="h-11 rounded-xl border-0 bg-muted/50 pl-10 text-[15px] focus-visible:ring-2 focus-visible:ring-ring/40"
            inputMode="url"
            onChange={(e) => setUrl(e.target.value)}
            placeholder={t.sources.addPlaceholder}
            ref={inputRef}
            type="url"
            value={url}
          />
        </div>
        <Button className="h-11 rounded-xl px-5" disabled={busy || !url.trim()} type="submit">
          {busy && <Spinner className="size-4" />}
          {t.sources.add}
        </Button>
      </div>
      <p className="mt-2 px-1 text-muted-foreground text-xs">{t.sources.addHint}</p>
    </form>
  );
}

function SourceTableRow({
  row,
  t,
  lang,
  flash,
  onAct,
  onDelete,
}: {
  row: SourceRow;
  t: AdminText;
  lang: UILang;
  flash: boolean;
  onAct: (fn: () => Promise<unknown>, ok: string) => void;
  onDelete: (row: SourceRow) => void;
}) {
  const busy = isBusy(row);
  const canRefresh = !busy && row.status !== "blocked" && row.status !== "disabled";
  const KindIcon = row.kind === "site" ? GlobeIcon : FileTextIcon;
  const p = row.progress;

  return (
    <TableRow className={cn("transition-colors duration-700", flash && "bg-accent", row.status === "disabled" && "opacity-60")}>
      <TableCell className="max-w-[18rem] pl-4">
        <div className="flex items-center gap-3">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-accent text-brand" title={t.sources.kind[row.kind]}>
            <KindIcon className="size-4" />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-1">
              <span className="truncate font-medium" title={titleOf(row)}>
                {titleOf(row)}
              </span>
              <CopyButton className="size-6 shrink-0 opacity-0 transition-opacity [tr:hover_&]:opacity-100" size="sm" value={row.url} />
            </div>
            <a className="block truncate text-muted-foreground text-xs hover:underline" href={row.url} rel="noreferrer" target="_blank">
              {row.url.replace(/^https?:\/\//, "")}
            </a>
          </div>
        </div>
      </TableCell>
      <TableCell className="hidden xl:table-cell">
        {row.category ? (
          <span
            className="rounded-md bg-muted px-1.5 py-0.5 text-xs"
            title={row.category_source ? (t.sources.categorySource[row.category_source] ?? row.category_source) : undefined}
          >
            {row.category}
          </span>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell>
        <div className="flex items-center justify-between gap-2">
          <SourceStatusBadge error={row.last_error} label={t.sources.status[row.status]} status={row.status} />
          {p && (
            <Link className="text-muted-foreground text-xs tabular-nums hover:text-foreground" href={`/admin/jobs?id=${p.job_id}`} title={t.sources.details}>
              {Math.round(p.percent)}%{p.eta_s != null && ` · ${t.sources.eta(duration(p.eta_s))}`}
            </Link>
          )}
        </div>
        {p && (
          <StageProgress className="mt-2" job={{ status: row.status === "running" ? "running" : "queued", stage: p.stage, percent: p.percent }} />
        )}
        {p?.stage && <p className="mt-1 text-[11px] text-muted-foreground">{t.jobs.stage[p.stage]}</p>}
      </TableCell>
      <TableCell className="hidden text-right tabular-nums lg:table-cell">{number(row.pages, lang)}</TableCell>
      <TableCell className="hidden text-right tabular-nums lg:table-cell">
        {number(row.documents_downloaded, lang)}
        <span className="text-muted-foreground">/{number(row.documents_found, lang)}</span>
      </TableCell>
      <TableCell className="hidden text-right tabular-nums md:table-cell">{number(row.chunks, lang)}</TableCell>
      <TableCell className="hidden whitespace-nowrap text-muted-foreground text-xs md:table-cell">
        {row.last_crawled ? timeAgo(row.last_crawled, lang) : t.common.never}
      </TableCell>
      <TableCell className="pr-3">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button aria-label="…" className="size-8" size="icon" variant="ghost">
              <MoreHorizontalIcon />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuItem disabled={!canRefresh} onClick={() => onAct(() => admin.startJob(row.id, "refresh"), t.sources.jobStarted)}>
              <RefreshCwIcon /> {t.sources.refresh}
            </DropdownMenuItem>
            {p && (
              <DropdownMenuItem onClick={() => onAct(() => admin.cancelJob(p.job_id), t.sources.jobCancelled)}>
                <SquareIcon /> {t.sources.cancelJob}
              </DropdownMenuItem>
            )}
            {row.last_job && (
              <DropdownMenuItem asChild>
                <Link href={`/admin/jobs?id=${p?.job_id ?? row.last_job.id}`}>
                  <WorkflowIcon /> {t.sources.details}
                </Link>
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => onAct(() => admin.patchSource(row.id, { enabled: !row.enabled }), t.sources.saved)}>
              <PowerIcon /> {row.enabled ? t.sources.disable : t.sources.enable}
            </DropdownMenuItem>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>
                <TagIcon className="size-4 text-muted-foreground" /> {t.sources.category}
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="max-h-72 overflow-y-auto">
                <DropdownMenuRadioGroup
                  onValueChange={(category) => onAct(() => admin.patchSource(row.id, { category }), t.sources.saved)}
                  value={row.category ?? ""}
                >
                  {CATEGORIES.map((c) => (
                    <DropdownMenuRadioItem key={c} value={c}>
                      {c}
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => onDelete(row)} variant="destructive">
              <Trash2Icon /> {t.common.delete}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </TableCell>
    </TableRow>
  );
}

/** DELETE ?purge=true: the documents leave the index too, so it is confirmed first. */
function DeleteDialog({ row, t, lang, onClose, onDeleted }: { row: SourceRow | null; t: AdminText; lang: UILang; onClose: () => void; onDeleted: () => void }) {
  const [busy, setBusy] = useState(false);

  const confirm = async () => {
    if (!row) return;
    setBusy(true);
    try {
      await admin.deleteSource(row.id, true);
      toast.success(t.sources.deleted);
      onDeleted();
      onClose();
    } catch (e) {
      toast.error(errorText(e, lang));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AlertDialog onOpenChange={(open) => !open && onClose()} open={row !== null}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t.sources.deleteTitle}</AlertDialogTitle>
          <AlertDialogDescription>{row && t.sources.deleteText(titleOf(row))}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{t.common.cancel}</AlertDialogCancel>
          <AlertDialogAction
            className="bg-destructive text-white hover:bg-destructive/90"
            disabled={busy}
            onClick={(e) => {
              e.preventDefault();
              void confirm();
            }}
          >
            {busy ? <Spinner className="size-4" /> : <CheckIcon />}
            {t.common.delete}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
