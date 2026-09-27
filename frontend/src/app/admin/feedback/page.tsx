"use client";

import { ChevronDownIcon, ThumbsDownIcon, ThumbsUpIcon } from "lucide-react";
import { useState } from "react";
import { EmptyState } from "@/components/admin/empty-state";
import { PageHeader } from "@/components/admin/page-header";
import { StatCard } from "@/components/admin/stat-card";
import { likeShare, likesOf, Vote } from "@/components/admin/votes";
import { Badge } from "@/components/spell/badge";
import { Chart } from "@/components/spell/chart";
import { CopyButton } from "@/components/spell/copy-button";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Skeleton } from "@/components/ui/skeleton";
import { admin, useAdminQuery } from "@/lib/admin";
import { errorText } from "@/lib/admin-errors";
import { number, shortDay, timeAgo } from "@/lib/admin-format";
import { ADMIN_UI, type AdminText } from "@/lib/admin-i18n";
import type { FeedbackItem } from "@/lib/api";
import type { UILang } from "@/lib/i18n";
import { useUILang } from "@/lib/lang";
import { useApiMode } from "@/lib/mode";
import { cn } from "@/lib/utils";

export default function FeedbackPage() {
  const lang = useUILang();
  const t = ADMIN_UI[lang];
  const mode = useApiMode();
  const [maxRating, setMaxRating] = useState(2); // 2: dislikes only, 5: all
  const stats = useAdminQuery(`fb-stats-${mode}`, admin.feedbackStats, () => 30000);
  const items = useAdminQuery(`fb-items-${mode}-${maxRating}`, () => admin.feedback(maxRating));

  const s = stats.data;
  const { likes, dislikes } = s ? likesOf(s.per_star) : { likes: 0, dislikes: 0 };
  const share = (n: number) => `${Math.round((n / (likes + dislikes || 1)) * 100)}%`;

  return (
    <>
      <PageHeader subtitle={t.feedback.subtitle} title={t.feedback.title} />

      {stats.error && !s ? (
        <EmptyState
          action={<Button onClick={stats.reload} variant="outline">{t.common.retry}</Button>}
          hint={errorText(stats.error, lang)}
          icon={ThumbsUpIcon}
          title={t.common.loadError}
        />
      ) : s?.count === 0 ? (
        <EmptyState icon={ThumbsUpIcon} title={t.feedback.noData} />
      ) : (
        <>
          {/* the cards and their labels are there at once; only the numbers wait for the data */}
          <div className="grid grid-cols-3 gap-2 sm:gap-3">
            <StatCard label={t.feedback.count} value={s && number(s.count, lang)} />
            <StatCard
              aside={<ThumbsUpIcon className="text-success" size={18} />}
              hint={s && share(likes)}
              label={t.feedback.likes}
              value={s && number(likes, lang)}
            />
            <StatCard
              aside={<ThumbsDownIcon className="text-destructive" size={18} />}
              className={dislikes > 0 ? "border-destructive/25" : undefined}
              hint={s && share(dislikes)}
              label={t.feedback.dislikes}
              value={s && number(dislikes, lang)}
            />
          </div>

          <div className="mt-3 grid gap-3 lg:grid-cols-5">
            <section className="rounded-2xl border bg-card p-4 lg:col-span-3">
              <h2 className="mb-1 font-medium text-sm">{t.feedback.trend}</h2>
              {!s ? (
                <Skeleton className="mt-3 h-52 w-full rounded-xl" />
              ) : s.by_day.length > 1 ? (
                <Chart
                  className="-mx-1"
                  color="#1d5fae"
                  data={s.by_day.map((d) => Math.round(likeShare(d.average) * 100))}
                  formatValue={(v, i) => `${v}% · ${s.by_day[i].count} ${t.feedback.trendCount(s.by_day[i].count)}`}
                  labels={s.by_day.map((d) => shortDay(d.day, lang))}
                  name={t.feedback.likes}
                  tickCount={Math.min(6, s.by_day.length)}
                />
              ) : (
                <p className="py-10 text-center text-muted-foreground text-sm">{t.feedback.trendEmpty}</p>
              )}
            </section>
            {/* likes and dislikes are the cards above: no second chart of the same two numbers */}
            <div className="flex flex-col gap-3 lg:col-span-2">
              <section className="rounded-2xl border bg-card p-4">
                <h2 className="mb-3 font-medium text-sm">{t.feedback.tags}</h2>
                <div className="flex flex-wrap gap-1.5">
                  {!s && <Skeleton className="h-6 w-40 rounded-full" />}
                  {s && s.top_tags.length === 0 && <p className="text-muted-foreground text-sm">{t.feedback.tagsEmpty}</p>}
                  {s?.top_tags.map((tag) => (
                    <Badge className="gap-1.5 rounded-full px-2.5 py-1" key={tag.tag} variant={tag.tag === "helpful" ? "emerald" : "slate"}>
                      {t.tags[tag.tag] ?? tag.tag}
                      <span className="tabular-nums opacity-60">{tag.count}</span>
                    </Badge>
                  ))}
                </div>
              </section>
            </div>
          </div>
        </>
      )}

      <section className="mt-8">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-semibold text-lg tracking-tight">{t.feedback.list}</h2>
          <div className="flex rounded-full bg-muted/80 p-0.5 text-xs" role="radiogroup">
            {([2, 5] as const).map((n) => (
              <button
                aria-checked={maxRating === n}
                className={cn(
                  "rounded-full px-3 py-1 font-medium transition-colors",
                  maxRating === n ? "bg-card text-foreground shadow-sm" : "text-foreground/50 hover:text-foreground/80",
                )}
                key={n}
                onClick={() => setMaxRating(n)}
                role="radio"
                type="button"
              >
                {n === 2 ? t.feedback.filter.down : t.feedback.filter.all}
              </button>
            ))}
          </div>
        </div>
        {items.loading ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton className="h-16 rounded-2xl" key={i} />
            ))}
          </div>
        ) : items.data?.length === 0 ? (
          <EmptyState icon={ThumbsUpIcon} title={t.feedback.empty} />
        ) : (
          <ul className="flex flex-col gap-2">
            {items.data?.map((item) => (
              <FeedbackRow item={item} key={`${item.answer_id}-${item.updated_at}`} lang={lang} t={t} />
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

function FeedbackRow({ item, t, lang }: { item: FeedbackItem; t: AdminText; lang: UILang }) {
  return (
    <li>
      <Collapsible className="group/fb rounded-2xl border bg-card">
        <CollapsibleTrigger className="flex w-full items-start gap-3 p-4 text-left">
          <Vote className="mt-0.5 shrink-0" rating={item.rating} />
          <div className="min-w-0 flex-1">
            <p className="line-clamp-2 font-medium text-sm">{item.question ?? "—"}</p>
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              {item.status && <Badge variant={item.status === "answered" ? "blue" : "amber"}>{t.answerStatus[item.status] ?? item.status}</Badge>}
              {item.tags.map((tag) => (
                <Badge key={tag} variant="slate">
                  {t.tags[tag] ?? tag}
                </Badge>
              ))}
              <span className="text-muted-foreground text-xs">{timeAgo(item.updated_at, lang)}</span>
            </div>
          </div>
          <ChevronDownIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform group-data-[state=open]/fb:rotate-180" />
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="flex flex-col gap-4 border-t px-4 py-4 text-sm">
            {item.comment && (
              <div className="rounded-xl bg-warning-bg px-3 py-2 text-warning">
                <p className="font-medium text-xs">{t.feedback.comment}</p>
                <p className="mt-0.5">“{item.comment}”</p>
              </div>
            )}
            <div>
              <p className="text-muted-foreground text-xs">{t.feedback.answer}</p>
              <p className="mt-1 whitespace-pre-wrap leading-relaxed">{item.answer ?? "—"}</p>
            </div>
            {item.doc_ids.length > 0 && (
              <div>
                <p className="text-muted-foreground text-xs">{t.feedback.sources}</p>
                <ul className="mt-1 flex flex-col gap-1">
                  {item.doc_ids.map((id) => (
                    <li className="flex items-center gap-1 font-mono text-xs" key={id}>
                      <span className="truncate">{id}</span>
                      <CopyButton className="size-6 shrink-0" size="sm" value={id} />
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <p className="flex items-center gap-1 text-muted-foreground text-xs">
              {item.answer_id}
              <CopyButton className="size-6" size="sm" value={item.answer_id} />
              {item.path && <span>· {t.feedback.path}: {item.path}</span>}
            </p>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}
