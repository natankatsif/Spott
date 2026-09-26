"use client";

import { DatabaseIcon, FileTextIcon, GlobeIcon, MoreHorizontalIcon, PencilIcon, PlayIcon, PlusIcon, RefreshCwIcon, SearchIcon, ShieldBanIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/admin/empty-state";
import { PageHeader } from "@/components/admin/page-header";
import { StageProgress } from "@/components/admin/stage-progress";
import { StatusBadge } from "@/components/admin/status-badge";
import { AnimatedCheckbox } from "@/components/spell/animated-checkbox";
import { Badge } from "@/components/spell/badge";
import { CopyButton } from "@/components/spell/copy-button";
import { LabelInput } from "@/components/spell/label-input";
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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { admin, isActive, useAdminQuery } from "@/lib/admin";
import { number, timeAgo } from "@/lib/admin-format";
import { ADMIN_UI, type AdminText, CATEGORIES } from "@/lib/admin-i18n";
import type { SourceRow } from "@/lib/api";
import type { UILang } from "@/lib/i18n";
import { errorText } from "@/lib/admin-errors";
import { useUILang } from "@/lib/lang";
import { useApiMode } from "@/lib/mode";
import { cn } from "@/lib/utils";

/** A site shows its domain; a document its file name (the domain is in the URL line under it). */
const titleOf = (row: SourceRow) =>
  row.kind === "document" ? decodeURIComponent(row.url.split("/").filter(Boolean).at(-1) ?? row.site_id) : row.site_id;

type Editing = { mode: "add" } | { mode: "edit"; row: SourceRow } | null;

export default function SourcesPage() {
  const lang = useUILang();
  const t = ADMIN_UI[lang];
  const mode = useApiMode();
  const router = useRouter();
  const query = useAdminQuery(`sources-${mode}`, admin.sources, (rows) => (rows.some((r) => isActive(r.last_job)) ? 2000 : 15000));
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<Editing>(null);
  const [deleting, setDeleting] = useState<SourceRow | null>(null);

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (query.data ?? [])
      .filter((r) => !needle || `${r.url} ${r.site_id} ${r.category ?? ""}`.toLowerCase().includes(needle))
      // sites robots.txt forbids to crawl can't be processed: keep them at the end (stable, server order otherwise)
      .toSorted((a, b) => Number(a.robots === "blocked") - Number(b.robots === "blocked"));
  }, [query.data, q]);

  const run = async (row: SourceRow, kind: "crawl" | "refresh") => {
    try {
      await admin.startJob(row.id, kind);
      toast.success(t.sources.jobStarted, {
        action: { label: t.nav.jobs, onClick: () => router.push("/admin/jobs") },
      });
      query.reload();
    } catch (e) {
      toast.error(errorText(e, lang));
    }
  };

  const toggle = async (row: SourceRow, enabled: boolean) => {
    try {
      await admin.patchSource(row.id, { enabled });
      query.reload();
    } catch (e) {
      toast.error(errorText(e, lang));
    }
  };

  return (
    <>
      <PageHeader
        actions={
          <Button className="rounded-xl" onClick={() => setEditing({ mode: "add" })}>
            <PlusIcon /> {t.sources.add}
          </Button>
        }
        subtitle={t.sources.subtitle}
        title={t.sources.title}
      />

      <div className="relative mb-4 max-w-sm">
        <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input className="h-9 rounded-xl bg-card pl-9" onChange={(e) => setQ(e.target.value)} placeholder={t.common.search} value={q} />
      </div>

      {query.error && !query.data ? (
        <EmptyState
          action={<Button onClick={query.reload} variant="outline">{t.common.retry}</Button>}
          hint={errorText(query.error, lang)}
          icon={DatabaseIcon}
          title={t.common.loadError}
        />
      ) : query.data && query.data.length === 0 ? (
        <EmptyState
          action={<Button onClick={() => setEditing({ mode: "add" })}><PlusIcon /> {t.sources.add}</Button>}
          hint={t.sources.emptyHint}
          icon={DatabaseIcon}
          title={t.sources.empty}
        />
      ) : (
        <div className="overflow-hidden rounded-2xl border bg-card">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="pl-4">{t.sources.col.source}</TableHead>
                <TableHead className="hidden lg:table-cell">{t.sources.col.category}</TableHead>
                <TableHead className="hidden md:table-cell">{t.sources.col.robots}</TableHead>
                <TableHead className="hidden text-right sm:table-cell">{t.sources.col.chunks}</TableHead>
                <TableHead className="hidden w-56 sm:table-cell">{t.sources.col.lastJob}</TableHead>
                <TableHead className="w-14 text-center">{t.sources.col.enabled}</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.loading &&
                Array.from({ length: 4 }, (_, i) => (
                  <TableRow key={i}>
                    <TableCell className="pl-4" colSpan={7}>
                      <Skeleton className="h-9 w-full rounded-lg" />
                    </TableCell>
                  </TableRow>
                ))}
              {rows.map((row) => (
                <SourceTableRow key={row.id} lang={lang} onDelete={setDeleting} onEdit={(r) => setEditing({ mode: "edit", row: r })} onRun={run} onToggle={toggle} row={row} t={t} />
              ))}
              {query.data && query.data.length > 0 && rows.length === 0 && (
                <TableRow>
                  <TableCell className="py-10 text-center text-muted-foreground" colSpan={7}>
                    {t.sources.noMatch}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      )}

      <SourceSheet editing={editing} lang={lang} onClose={() => setEditing(null)} onSaved={query.reload} t={t} />
      <DeleteDialog lang={lang} onClose={() => setDeleting(null)} onDeleted={query.reload} row={deleting} t={t} />
    </>
  );
}

function SourceTableRow({
  row,
  t,
  lang,
  onRun,
  onToggle,
  onEdit,
  onDelete,
}: {
  row: SourceRow;
  t: AdminText;
  lang: UILang;
  onRun: (row: SourceRow, kind: "crawl" | "refresh") => void;
  onToggle: (row: SourceRow, enabled: boolean) => void;
  onEdit: (row: SourceRow) => void;
  onDelete: (row: SourceRow) => void;
}) {
  const job = row.last_job;
  const blocked = row.robots === "blocked";
  const busy = isActive(job);
  const KindIcon = row.kind === "site" ? GlobeIcon : FileTextIcon;
  return (
    <TableRow className={cn(!row.enabled && "opacity-60")}>
      <TableCell className="max-w-[16rem] pl-4 sm:max-w-[20rem]">
        <div className="flex items-center gap-3">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-accent text-brand" title={t.sources.kind[row.kind]}>
            <KindIcon className="size-4" />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-1">
              <span className="truncate font-medium">{titleOf(row)}</span>
              <CopyButton className="size-6 shrink-0 opacity-0 transition-opacity group-hover/row:opacity-100 [tr:hover_&]:opacity-100" size="sm" value={row.url} />
            </div>
            <a className="block truncate text-muted-foreground text-xs hover:underline" href={row.url} rel="noreferrer" target="_blank">
              {row.url.replace(/^https?:\/\//, "")}
            </a>
            {job && (
              <div className="mt-1 sm:hidden">
                <StatusBadge label={busy ? `${t.status[job.status]} · ${Math.round(job.percent)}%` : t.status[job.status]} status={job.status} />
              </div>
            )}
          </div>
        </div>
      </TableCell>
      <TableCell className="hidden lg:table-cell">
        {row.category ? <Badge variant="slate">{row.category}</Badge> : <span className="text-muted-foreground">—</span>}
      </TableCell>
      <TableCell className="hidden md:table-cell">
        {row.kind === "site" ? (
          <Badge className="gap-1" variant={blocked ? "red" : "emerald"}>
            {blocked && <ShieldBanIcon className="size-3" />}
            {t.sources.robots[row.robots]}
          </Badge>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell className="hidden text-right tabular-nums sm:table-cell">{number(row.chunks, lang)}</TableCell>
      <TableCell className="hidden sm:table-cell">
        {job ? (
          <Link className="block rounded-lg" href={`/admin/jobs?id=${job.id}`}>
            <div className="flex items-center justify-between gap-2">
              <StatusBadge label={t.status[job.status]} status={job.status} />
              <span className="text-muted-foreground text-xs tabular-nums">
                {busy ? `${Math.round(job.percent)}%` : timeAgo(job.finished_at ?? job.started_at, lang)}
              </span>
            </div>
            {busy && <StageProgress className="mt-2" job={job} />}
          </Link>
        ) : (
          <span className="text-muted-foreground text-xs">{t.common.never}</span>
        )}
      </TableCell>
      <TableCell className="text-center">
        <Switch aria-label={t.sources.col.enabled} checked={row.enabled} onCheckedChange={(v) => onToggle(row, v)} />
      </TableCell>
      <TableCell className="pr-3">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button aria-label="…" className="size-8" size="icon" variant="ghost">
              <MoreHorizontalIcon />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <DropdownMenuItem disabled={blocked || busy || !row.enabled} onClick={() => onRun(row, "crawl")}>
              <PlayIcon /> {t.sources.crawl}
            </DropdownMenuItem>
            <DropdownMenuItem disabled={blocked || busy || !row.enabled} onClick={() => onRun(row, "refresh")}>
              <RefreshCwIcon /> {t.sources.refresh}
            </DropdownMenuItem>
            {blocked && <p className="px-2 py-1 text-muted-foreground text-xs">{t.sources.robotsBlocked}</p>}
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => onEdit(row)}>
              <PencilIcon /> {t.sources.edit}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => onDelete(row)} variant="destructive">
              <Trash2Icon /> {t.common.delete}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </TableCell>
    </TableRow>
  );
}

function SourceSheet({ editing, t, lang, onClose, onSaved }: { editing: Editing; t: AdminText; lang: UILang; onClose: () => void; onSaved: () => void }) {
  const row = editing?.mode === "edit" ? editing.row : null;
  const [kind, setKind] = useState<"site" | "document">("site");
  const [start, setStart] = useState(true);
  const [busy, setBusy] = useState(false);
  const [openedFor, setOpenedFor] = useState<Editing>(null);
  // reset the form each time the sheet opens for another source (state adjusted during render, no effect)
  if (editing !== openedFor) {
    setOpenedFor(editing);
    setKind(row?.kind ?? "site");
    setStart(true);
  }

  const submit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const num = (k: string) => (f.get(k) ? Number(f.get(k)) : undefined);
    const category = String(f.get("category") ?? "") || undefined;
    setBusy(true);
    try {
      if (row) {
        await admin.patchSource(row.id, { category, max_depth: num("max_depth"), max_pages: num("max_pages") });
        toast.success(t.sources.saved);
      } else {
        await admin.addSource({
          kind,
          url: String(f.get("url") ?? "").trim(),
          category,
          ...(kind === "site" ? { max_depth: num("max_depth"), max_pages: num("max_pages") } : {}),
          start,
        });
        toast.success(t.sources.added);
      }
      onSaved();
      onClose();
    } catch (err) {
      toast.error(errorText(err, lang));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet onOpenChange={(open) => !open && onClose()} open={editing !== null}>
      <SheetContent className="w-full gap-0 sm:max-w-md">
        <form className="flex h-full flex-col" onSubmit={submit}>
          <SheetHeader className="border-b">
            <SheetTitle>{row ? t.sources.edit : t.sources.add}</SheetTitle>
            <SheetDescription>{row ? row.url : t.sources.form.urlHint}</SheetDescription>
          </SheetHeader>
          <div className="flex flex-1 flex-col gap-5 overflow-y-auto p-4">
            {!row && (
              <div className="flex flex-col gap-2">
                <Label>{t.sources.form.kind}</Label>
                <div className="grid grid-cols-2 gap-2">
                  {(["site", "document"] as const).map((k) => {
                    const Icon = k === "site" ? GlobeIcon : FileTextIcon;
                    return (
                      <button
                        className={cn(
                          "flex items-center gap-2 rounded-xl border px-3 py-2.5 text-left text-sm transition-colors",
                          kind === k ? "border-brand bg-accent font-medium text-accent-foreground" : "hover:bg-muted/50",
                        )}
                        key={k}
                        onClick={() => setKind(k)}
                        type="button"
                      >
                        <Icon className={cn("size-4", kind === k ? "text-brand" : "text-muted-foreground")} />
                        {t.sources.kind[k]}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
            {!row && (
              <LabelInput
                label={t.sources.form.url}
                name="url"
                pattern="https?://.+"
                required
                ringColor="blue"
                type="url"
              />
            )}
            <div className="flex flex-col gap-2">
              <Label htmlFor="category">{t.sources.form.category}</Label>
              <select
                className="h-10 rounded-lg border bg-card px-3 text-sm outline-none focus:ring-2 focus:ring-blue-600"
                defaultValue={row?.category ?? ""}
                id="category"
                name="category"
              >
                <option value="">{t.sources.form.noCategory}</option>
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
            {(row?.kind ?? kind) === "site" && (
              <div className="grid grid-cols-2 gap-3">
                <LabelInput defaultValue={row?.max_depth ?? 4} label={t.sources.form.maxDepth} max={10} min={0} name="max_depth" ringColor="blue" type="number" />
                <LabelInput defaultValue={row?.max_pages ?? 2000} label={t.sources.form.maxPages} max={100000} min={1} name="max_pages" ringColor="blue" type="number" />
              </div>
            )}
            {!row && (
              <AnimatedCheckbox className="text-sm" defaultChecked={start} strike={false} key={String(editing !== null)} onCheckedChange={setStart} title={t.sources.form.start} />
            )}
          </div>
          <SheetFooter className="flex-row justify-end border-t">
            <Button onClick={onClose} type="button" variant="ghost">
              {t.common.cancel}
            </Button>
            <Button className="rounded-xl" disabled={busy} type="submit">
              {busy && <Spinner className="size-4" />}
              {row ? t.common.save : t.sources.add}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

function DeleteDialog({ row, t, lang, onClose, onDeleted }: { row: SourceRow | null; t: AdminText; lang: UILang; onClose: () => void; onDeleted: () => void }) {
  const [purge, setPurge] = useState(false);
  const [busy, setBusy] = useState(false);

  const confirm = async () => {
    if (!row) return;
    setBusy(true);
    try {
      await admin.deleteSource(row.id, purge);
      toast.success(t.sources.deleted);
      onDeleted();
      onClose();
    } catch (e) {
      toast.error(errorText(e, lang));
    } finally {
      setBusy(false);
      setPurge(false);
    }
  };

  return (
    <AlertDialog onOpenChange={(open) => !open && onClose()} open={row !== null}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t.sources.deleteTitle}</AlertDialogTitle>
          <AlertDialogDescription>{row && t.sources.deleteText(row.site_id)}</AlertDialogDescription>
        </AlertDialogHeader>
        <AnimatedCheckbox className="text-sm" key={row?.id} strike={false} onCheckedChange={setPurge} title={t.sources.purge} />
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
            {busy && <Spinner className="size-4" />}
            {t.common.delete}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
