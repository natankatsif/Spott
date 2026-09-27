"use client";

import { ChevronDownIcon, EyeIcon, EyeOffIcon, MessageCircleQuestionIcon, MessageSquareTextIcon, PlusIcon, RefreshCwIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/admin/empty-state";
import { Badge } from "@/components/spell/badge";
import { Spinner } from "@/components/spell/spinner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
import { admin, useAdminQuery } from "@/lib/admin";
import { errorText } from "@/lib/admin-errors";
import { timeAgo } from "@/lib/admin-format";
import type { AdminText } from "@/lib/admin-i18n";
import type { Gap, GapTopic } from "@/lib/api";
import type { UILang } from "@/lib/i18n";
import { useApiMode } from "@/lib/mode";

const PAGE_SIZE = 8;
type StatusFilter = "all" | Gap["status"];
type Sort = "count" | "recent" | "partial" | "not_found";

const byCount = (a: Gap, b: Gap) => b.count - a.count || b.last_asked.localeCompare(a.last_asked);
const SORTS: Record<Sort, (a: Gap, b: Gap) => number> = {
  count: byCount,
  recent: (a, b) => b.last_asked.localeCompare(a.last_asked),
  partial: (a, b) => Number(b.status === "partial") - Number(a.status === "partial") || byCount(a, b),
  not_found: (a, b) => Number(b.status === "not_found") - Number(a.status === "not_found") || byCount(a, b),
};

/** Page numbers with gaps: 1 … 4 5 6 … 12. */
function pageList(page: number, pages: number): (number | "gap")[] {
  const wanted = new Set([1, pages, page - 1, page, page + 1].filter((n) => n >= 1 && n <= pages));
  const sorted = [...wanted].sort((a, b) => a - b);
  return sorted.flatMap((n, i) => (i > 0 && n - sorted[i - 1] > 1 ? (["gap", n] as const) : [n]));
}

/**
 * Questions the bot couldn't (fully) answer, grouped, by topic (docs/API.md → Gaps). The demo loop:
 * a gap → add the missing document's link above → wait until it's indexed → Recheck → the card leaves.
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
  const filtered = useMemo(
    () =>
      (all ?? [])
        .filter((gap) => (status === "all" || gap.status === status) && (topic === "all" || gap.topic === topic))
        .sort(SORTS[sort]),
    [all, status, topic, sort],
  );
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
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 font-semibold text-lg tracking-tight">
            {g.title}
            {query.data && query.data.totals.groups > 0 && (
              <Badge className="rounded-full tabular-nums" variant="red">
                {query.data.totals.groups}
              </Badge>
            )}
          </h2>
          <p className="mt-0.5 max-w-2xl text-muted-foreground text-sm">{g.subtitle}</p>
          {query.data && (
            <p className="mt-1 text-muted-foreground text-xs">{g.totals(query.data.totals.not_found, query.data.totals.partial)}</p>
          )}
        </div>
        <ToggleGroup
          onValueChange={(v) => v && reset(setHidden)(v === "hidden")}
          size="sm"
          type="single"
          value={hidden ? "hidden" : "active"}
          variant="outline"
        >
          <ToggleGroupItem className="px-3" value="active">{g.showActive}</ToggleGroupItem>
          <ToggleGroupItem className="px-3" value="hidden">{g.showHidden}</ToggleGroupItem>
        </ToggleGroup>
      </div>

      {query.data && query.data.items.length > 0 && (
        <div className="mb-3 flex flex-col gap-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <ToggleGroup onValueChange={(v) => v && reset(setStatus)(v as StatusFilter)} size="sm" type="single" value={status} variant="outline">
              <ToggleGroupItem className="px-3" value="all">{g.all}</ToggleGroupItem>
              <ToggleGroupItem className="px-3" value="not_found">{g.status.not_found}</ToggleGroupItem>
              <ToggleGroupItem className="px-3" value="partial">{g.status.partial}</ToggleGroupItem>
            </ToggleGroup>
            <Select onValueChange={(v) => reset(setSort)(v as Sort)} value={sort}>
              <SelectTrigger aria-label={g.sortBy} className="w-52" size="sm">
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
          {query.data.topics.length > 1 && (
            <ToggleGroup
              className="flex-wrap"
              onValueChange={(v) => v && reset(setTopic)(v as GapTopic | "all")}
              size="sm"
              spacing={1}
              type="single"
              value={topic}
            >
              <ToggleGroupItem className="rounded-full px-3 data-[state=on]:bg-foreground data-[state=on]:text-background" value="all">
                {g.allTopics}
              </ToggleGroupItem>
              {query.data.topics.map((tp) => (
                <ToggleGroupItem
                  className="gap-1.5 rounded-full px-3 data-[state=on]:bg-foreground data-[state=on]:text-background"
                  key={tp.topic}
                  value={tp.topic}
                >
                  {g.topic[tp.topic]}
                  <span className="tabular-nums opacity-60">{tp.groups}</span>
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          )}
        </div>
      )}

      {query.loading ? (
        <div className="grid gap-2 md:grid-cols-2">
          {Array.from({ length: 2 }, (_, i) => (
            <Skeleton className="h-36 rounded-2xl" key={i} />
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
        <p className="rounded-2xl border border-dashed bg-card/50 px-4 py-6 text-center text-muted-foreground text-sm">
          {hidden ? g.emptyHidden : g.empty}
        </p>
      ) : (
        <>
          <ul className="grid gap-2 md:grid-cols-2">
            {shown.map((gap) => (
              <GapCard gap={gap} key={gap.id} lang={lang} onAddSource={onAddSource} onChanged={query.reload} t={t} />
            ))}
          </ul>
          {pages > 1 && (
            <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
              <span className="text-muted-foreground text-xs tabular-nums">
                {g.shown((current - 1) * PAGE_SIZE + 1, (current - 1) * PAGE_SIZE + shown.length, filtered.length)}
              </span>
              <Pagination className="mx-0 w-auto">
                <PaginationContent>
                  <PaginationItem>
                    <PaginationPrevious aria-disabled={current === 1} className={current === 1 ? "pointer-events-none opacity-50" : undefined} href="#" label={g.prev} onClick={go(current - 1)} />
                  </PaginationItem>
                  {pageList(current, pages).map((n, i) =>
                    n === "gap" ? (
                      <PaginationItem key={`gap-${i}`}>
                        <PaginationEllipsis />
                      </PaginationItem>
                    ) : (
                      <PaginationItem key={n}>
                        <PaginationLink href="#" isActive={n === current} onClick={go(n)}>
                          {n}
                        </PaginationLink>
                      </PaginationItem>
                    ),
                  )}
                  <PaginationItem>
                    <PaginationNext aria-disabled={current === pages} className={current === pages ? "pointer-events-none opacity-50" : undefined} href="#" label={g.next} onClick={go(current + 1)} />
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

function GapCard({ gap, t, lang, onAddSource, onChanged }: { gap: Gap; t: AdminText; lang: UILang; onAddSource: () => void; onChanged: () => void }) {
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
      <Card className={`h-full gap-3 rounded-2xl py-4 shadow-none border-l-4 ${gap.status === "not_found" ? "border-l-destructive/60" : "border-l-amber-400"}`}>
        <CardContent className="flex h-full flex-col gap-3 px-4">
          <div className="flex items-start gap-3">
            <p className="min-w-0 flex-1 font-medium text-sm leading-snug">{gap.example}</p>
            <Badge className="shrink-0 rounded-full px-2 py-1" variant={gap.status === "not_found" ? "red" : "amber"}>
              {g.status[gap.status]}
            </Badge>
          </div>

          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground text-xs">
            <Badge variant="slate">{g.topic[gap.topic]}</Badge>
            <span className="font-medium text-foreground tabular-nums">{g.asked(gap.count)}</span>
            <span>
              {g.last} {timeAgo(gap.last_asked, lang)}
            </span>
            <span className="flex gap-1">
              {gap.langs.map((l) => (
                <span className="rounded bg-muted px-1.5 py-0.5 font-semibold uppercase" key={l}>
                  {l}
                </span>
              ))}
            </span>
          </div>

          {gap.missing.length > 0 && (
            <p className="text-sm">
              <span className="text-muted-foreground">{g.missing}: </span>
              {gap.missing.join("; ")}
            </p>
          )}
          {gap.hint_sites.length > 0 && (
            <p className="text-sm">
              <span className="text-muted-foreground">{g.hint}: </span>
              {gap.hint_sites.map((h, i) => (
                <span key={h.site}>
                  {i > 0 && ", "}
                  <span className="font-medium">{h.site}</span> <span className="text-muted-foreground tabular-nums">({h.hits})</span>
                </span>
              ))}
            </p>
          )}

          {gap.last_answer && (
            <Collapsible className="group/a rounded-xl bg-muted/50">
              <CollapsibleTrigger className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-muted-foreground text-xs hover:text-foreground">
                <MessageSquareTextIcon className="size-3.5" />
                <span className="flex-1">{g.lastAnswer}</span>
                <ChevronDownIcon className="size-3.5 transition-transform group-data-[state=open]/a:rotate-180" />
              </CollapsibleTrigger>
              <CollapsibleContent>
                <p className="whitespace-pre-wrap px-3 pb-3 text-sm leading-relaxed">{gap.last_answer}</p>
              </CollapsibleContent>
            </Collapsible>
          )}

          {others.length > 0 && (
            <Collapsible className="group/q">
              <CollapsibleTrigger className="flex items-center gap-1 text-muted-foreground text-xs hover:text-foreground">
                {g.more(others.length)}
                <ChevronDownIcon className="size-3.5 transition-transform group-data-[state=open]/q:rotate-180" />
              </CollapsibleTrigger>
              <CollapsibleContent>
                <ul className="mt-2 flex flex-col gap-1 border-l-2 pl-3 text-sm">
                  {others.map((q) => (
                    <li className="flex items-baseline gap-2" key={q.answer_id} lang={q.lang}>
                      <span className="min-w-0 flex-1">{q.question}</span>
                      <span className="shrink-0 text-muted-foreground text-xs">{timeAgo(q.ts, lang)}</span>
                    </li>
                  ))}
                </ul>
              </CollapsibleContent>
            </Collapsible>
          )}

          <div className="mt-auto flex flex-wrap items-center gap-2 border-t pt-3">
            <Button className="rounded-xl" onClick={onAddSource} size="sm" variant="outline">
              <PlusIcon /> {g.addSource}
            </Button>
            <Button className="rounded-xl" disabled={busy !== null} onClick={recheck} size="sm" title={g.recheckCost} variant="outline">
              {busy === "recheck" ? <Spinner className="size-4" /> : <RefreshCwIcon />}
              {g.recheck}
            </Button>
            <Button className="ml-auto rounded-xl" disabled={busy !== null} onClick={toggleHidden} size="sm" variant="ghost">
              {busy === "hide" ? <Spinner className="size-4" /> : gap.hidden ? <EyeIcon /> : <EyeOffIcon />}
              <span className="hidden sm:inline">{gap.hidden ? g.unhide : g.hide}</span>
            </Button>
          </div>
          {gap.rechecked && (
            <p className="-mt-1 text-muted-foreground text-xs">
              {g.recheck}: {t.answerStatus[gap.rechecked.status] ?? gap.rechecked.status} · {timeAgo(gap.rechecked.ts, lang)}
            </p>
          )}
        </CardContent>
      </Card>
    </li>
  );
}
