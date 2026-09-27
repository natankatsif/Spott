"use client";

import { ChevronDownIcon, EyeIcon, EyeOffIcon, MessageCircleQuestionIcon, PlusIcon, RefreshCwIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/admin/empty-state";
import { Badge } from "@/components/spell/badge";
import { Spinner } from "@/components/spell/spinner";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Pagination,
  PaginationContent,
  PaginationEllipsis,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { admin, useAdminQuery } from "@/lib/admin";
import { errorText } from "@/lib/admin-errors";
import { timeAgo } from "@/lib/admin-format";
import type { AdminText } from "@/lib/admin-i18n";
import type { Gap, GapTopic } from "@/lib/api";
import type { UILang } from "@/lib/i18n";
import { useApiMode } from "@/lib/mode";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 10;
type StatusFilter = "all" | Gap["status"];
type Sort = "count" | "recent" | "partial" | "not_found";

const byCount = (a: Gap, b: Gap) => b.count - a.count || b.last_asked.localeCompare(a.last_asked);
const SORTS: Record<Sort, (a: Gap, b: Gap) => number> = {
  count: byCount,
  recent: (a, b) => b.last_asked.localeCompare(a.last_asked),
  partial: (a, b) => Number(b.status === "partial") - Number(a.status === "partial") || byCount(a, b),
  not_found: (a, b) => Number(b.status === "not_found") - Number(a.status === "not_found") || byCount(a, b),
};

// the segmented pill look of the other admin filters, on shadcn's toggle group
const SEGMENTS = "rounded-full bg-muted/80 p-0.5";
const SEGMENT =
  "h-7 gap-1.5 !rounded-full px-3 text-xs font-medium text-foreground/55 hover:bg-transparent hover:text-foreground/80 data-[state=on]:bg-card data-[state=on]:text-foreground data-[state=on]:shadow-sm";
const DOT: Record<Gap["status"], string> = { not_found: "bg-destructive", partial: "bg-amber-400" };

/** Page numbers with gaps: 1 … 4 5 6 … 12. */
function pageList(page: number, pages: number): (number | "gap")[] {
  const sorted = [...new Set([1, pages, page - 1, page, page + 1])].filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b);
  return sorted.flatMap((n, i) => (i > 0 && n - sorted[i - 1] > 1 ? (["gap", n] as const) : [n]));
}

/**
 * Questions the bot couldn't (fully) answer, grouped, by topic (docs/API.md → Gaps). One line per group; the details
 * and actions open under it. The demo loop: add the missing document's link above → wait until it's indexed →
 * Recheck → the group leaves the list.
 */
export function GapsSection({ t, lang, onAddSource }: { t: AdminText; lang: UILang; onAddSource: () => void }) {
  const mode = useApiMode();
  const [hidden, setHidden] = useState(false);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [topic, setTopic] = useState<GapTopic | "all">("all");
  const [sort, setSort] = useState<Sort>("count");
  const [page, setPage] = useState(1);
  const query = useAdminQuery(`gaps-${mode}-${hidden}`, () => admin.gaps(hidden));
  const g = t.gaps;

  const all = query.data?.items;
  const inTopic = useMemo(() => (all ?? []).filter((gap) => topic === "all" || gap.topic === topic), [all, topic]);
  const filtered = useMemo(
    () => inTopic.filter((gap) => status === "all" || gap.status === status).sort(SORTS[sort]),
    [inTopic, status, sort],
  );
  const counts = { all: inTopic.length, not_found: 0, partial: 0 };
  for (const gap of inTopic) counts[gap.status]++;
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const current = Math.min(page, pages);
  const shown = filtered.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);

  // a filter change starts from the first page
  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    setPage(1);
  };
  const go = (n: number) => (e: React.MouseEvent) => {
    e.preventDefault();
    if (n >= 1 && n <= pages) setPage(n);
  };

  return (
    <section className="mb-8">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="font-semibold text-lg tracking-tight">{g.title}</h2>
        {query.data && query.data.totals.groups > 0 && (
          <Badge className="rounded-full tabular-nums" variant="red">
            {query.data.totals.groups}
          </Badge>
        )}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              aria-pressed={hidden}
              className={cn("ml-auto size-8 rounded-full text-muted-foreground", hidden && "bg-muted text-foreground")}
              onClick={() => reset(setHidden)(!hidden)}
              size="icon"
              variant="ghost"
            >
              {hidden ? <EyeIcon /> : <EyeOffIcon />}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{hidden ? g.showActive : g.showHidden}</TooltipContent>
        </Tooltip>
      </div>

      {all && all.length > 0 && (
        <div className="mb-3 flex flex-col gap-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <ToggleGroup className={SEGMENTS} onValueChange={(v) => v && reset(setStatus)(v as StatusFilter)} type="single" value={status}>
              {(["all", "not_found", "partial"] as const).map((s) => (
                <ToggleGroupItem className={SEGMENT} key={s} value={s}>
                  {s !== "all" && <span className={cn("size-1.5 rounded-full", DOT[s])} />}
                  {g.tabs[s]}
                  <span className="tabular-nums opacity-50">{counts[s]}</span>
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
            <Select onValueChange={(v) => reset(setSort)(v as Sort)} value={sort}>
              <SelectTrigger aria-label={g.sortBy} className="h-8 rounded-full border-0 bg-muted/80 text-xs shadow-none" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="end">
                {(Object.keys(SORTS) as Sort[]).map((k) => (
                  <SelectItem key={k} value={k}>
                    {g.sort[k]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {query.data && query.data.topics.length > 1 && (
            <ToggleGroup
              className="-mx-1 w-full flex-nowrap overflow-x-auto px-1 pb-1 [scrollbar-width:none]"
              onValueChange={(v) => v && reset(setTopic)(v as GapTopic | "all")}
              spacing={1}
              type="single"
              value={topic}
            >
              {[{ topic: "all" as const, groups: all.length }, ...query.data.topics].map((tp) => (
                <ToggleGroupItem
                  className="h-7 shrink-0 gap-1.5 !rounded-full border px-3 text-xs data-[state=on]:border-foreground data-[state=on]:bg-foreground data-[state=on]:text-background"
                  key={tp.topic}
                  value={tp.topic}
                >
                  {tp.topic === "all" ? g.allTopics : g.topic[tp.topic]}
                  <span className="tabular-nums opacity-50">{tp.groups}</span>
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          )}
        </div>
      )}

      {query.loading ? (
        <div className="flex flex-col gap-1.5">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton className="h-14 rounded-xl" key={i} />
          ))}
        </div>
      ) : query.error && !query.data ? (
        <EmptyState
          action={<Button onClick={query.reload} variant="outline">{t.common.retry}</Button>}
          hint={errorText(query.error, lang)}
          icon={MessageCircleQuestionIcon}
          title={t.common.loadError}
        />
      ) : filtered.length === 0 ? (
        <p className="rounded-xl border border-dashed px-4 py-6 text-center text-muted-foreground text-sm">
          {hidden ? g.emptyHidden : g.empty}
        </p>
      ) : (
        <>
          <ul className="flex flex-col gap-1.5">
            {shown.map((gap) => (
              <GapRow gap={gap} key={gap.id} lang={lang} onAddSource={onAddSource} onChanged={query.reload} t={t} />
            ))}
          </ul>
          {pages > 1 && (
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
              <span className="text-muted-foreground text-xs tabular-nums">
                {g.shown((current - 1) * PAGE_SIZE + 1, (current - 1) * PAGE_SIZE + shown.length, filtered.length)}
              </span>
              <Pagination className="mx-0 w-auto">
                <PaginationContent>
                  <PaginationItem>
                    <PaginationPrevious
                      aria-disabled={current === 1}
                      className={cn("h-8", current === 1 && "pointer-events-none opacity-40")}
                      href="#"
                      label={g.prev}
                      onClick={go(current - 1)}
                    />
                  </PaginationItem>
                  {pageList(current, pages).map((n, i) =>
                    n === "gap" ? (
                      <PaginationItem key={`gap-${i}`}>
                        <PaginationEllipsis />
                      </PaginationItem>
                    ) : (
                      <PaginationItem key={n}>
                        <PaginationLink className="size-8 rounded-full" href="#" isActive={n === current} onClick={go(n)}>
                          {n}
                        </PaginationLink>
                      </PaginationItem>
                    ),
                  )}
                  <PaginationItem>
                    <PaginationNext
                      aria-disabled={current === pages}
                      className={cn("h-8", current === pages && "pointer-events-none opacity-40")}
                      href="#"
                      label={g.next}
                      onClick={go(current + 1)}
                    />
                  </PaginationItem>
                </PaginationContent>
              </Pagination>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function GapRow({ gap, t, lang, onAddSource, onChanged }: { gap: Gap; t: AdminText; lang: UILang; onAddSource: () => void; onChanged: () => void }) {
  const g = t.gaps;
  const [busy, setBusy] = useState<"recheck" | "hide" | null>(null);
  const others = gap.questions.filter((q) => q.question !== gap.example);

  const recheck = async () => {
    setBusy("recheck");
    try {
      const r = await admin.recheckGap(gap.id);
      if (r.status === "answered") toast.success(g.solved);
      else toast.info(r.status === "partial" ? g.stillPartial : g.stillMissing);
      onChanged();
    } catch (e) {
      toast.error(errorText(e, lang));
    } finally {
      setBusy(null);
    }
  };

  const toggleHidden = async () => {
    setBusy("hide");
    try {
      await admin.hideGap(gap.id, !gap.hidden);
      toast.success(gap.hidden ? g.unhidden : g.hidden);
      onChanged();
    } catch (e) {
      toast.error(errorText(e, lang));
    } finally {
      setBusy(null);
    }
  };

  return (
    <li>
      <Collapsible className="group/gap rounded-xl border bg-card transition-shadow data-[state=open]:shadow-sm">
        <CollapsibleTrigger className="flex w-full items-center gap-3 rounded-xl px-4 py-3 text-left hover:bg-muted/40">
          <span className={cn("size-2 shrink-0 rounded-full", DOT[gap.status])} title={g.status[gap.status]} />
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium text-sm group-data-[state=open]/gap:whitespace-normal">{gap.example}</p>
            <p className="mt-0.5 truncate text-muted-foreground text-xs">
              {g.topic[gap.topic]} · {g.status[gap.status]} · {timeAgo(gap.last_asked, lang)}
              {gap.langs.length > 0 && ` · ${gap.langs.map((l) => l.toUpperCase()).join(" ")}`}
            </p>
          </div>
          <span className="shrink-0 font-semibold text-sm tabular-nums" title={g.asked(gap.count)}>
            {gap.count}×
          </span>
          <ChevronDownIcon className="size-4 shrink-0 text-muted-foreground transition-transform group-data-[state=open]/gap:rotate-180" />
        </CollapsibleTrigger>

        <CollapsibleContent>
          <div className="flex flex-col gap-3 border-t px-4 py-3 text-sm">
            {gap.last_answer && (
              <figure className="rounded-lg bg-muted/60 px-3 py-2">
                <figcaption className="text-muted-foreground text-xs">{g.lastAnswer}</figcaption>
                <p className="mt-1 whitespace-pre-wrap leading-relaxed">{gap.last_answer}</p>
              </figure>
            )}
            {gap.missing.length > 0 && (
              <p>
                <span className="text-muted-foreground">{g.missing}: </span>
                {gap.missing.join("; ")}
              </p>
            )}
            {gap.hint_sites.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-muted-foreground">{g.hint}:</span>
                {gap.hint_sites.map((h) => (
                  <Badge className="gap-1 font-normal" key={h.site} variant="secondary">
                    {h.site} <span className="tabular-nums opacity-50">{h.hits}</span>
                  </Badge>
                ))}
              </div>
            )}
            {others.length > 0 && (
              <ul className="flex flex-col gap-1 border-l-2 pl-3 text-muted-foreground">
                {others.map((q) => (
                  <li className="flex items-baseline gap-2" key={q.answer_id} lang={q.lang}>
                    <span className="min-w-0 flex-1 text-foreground/80">{q.question}</span>
                    <span className="shrink-0 text-xs">{timeAgo(q.ts, lang)}</span>
                  </li>
                ))}
              </ul>
            )}

            <div className="flex flex-wrap items-center gap-1.5 pt-1">
              <Button className="h-8 rounded-full" onClick={onAddSource} size="sm" variant="outline">
                <PlusIcon /> {g.addSource}
              </Button>
              <Button className="h-8 rounded-full" disabled={busy !== null} onClick={recheck} size="sm" title={g.recheckCost} variant="ghost">
                {busy === "recheck" ? <Spinner className="size-4" /> : <RefreshCwIcon />}
                {g.recheck}
              </Button>
              {gap.rechecked && (
                <span className="text-muted-foreground text-xs">
                  {t.answerStatus[gap.rechecked.status] ?? gap.rechecked.status} · {timeAgo(gap.rechecked.ts, lang)}
                </span>
              )}
              <Button className="ml-auto h-8 rounded-full text-muted-foreground" disabled={busy !== null} onClick={toggleHidden} size="sm" variant="ghost">
                {busy === "hide" ? <Spinner className="size-4" /> : gap.hidden ? <EyeIcon /> : <EyeOffIcon />}
                {gap.hidden ? g.unhide : g.hide}
              </Button>
            </div>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}
