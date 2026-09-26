"use client";

import { ChevronDownIcon, EyeIcon, EyeOffIcon, MessageCircleQuestionIcon, PlusIcon, RefreshCwIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/admin/empty-state";
import { Badge } from "@/components/spell/badge";
import { Spinner } from "@/components/spell/spinner";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Skeleton } from "@/components/ui/skeleton";
import { admin, useAdminQuery } from "@/lib/admin";
import { errorText } from "@/lib/admin-errors";
import { timeAgo } from "@/lib/admin-format";
import type { AdminText } from "@/lib/admin-i18n";
import type { Gap } from "@/lib/api";
import type { UILang } from "@/lib/i18n";
import { useApiMode } from "@/lib/mode";
import { cn } from "@/lib/utils";

/**
 * Questions the bot couldn't (fully) answer, grouped, biggest first (docs/API.md → Gaps). The demo loop:
 * a gap → add the missing document's link above → wait until it's indexed → Recheck → the card leaves.
 */
export function GapsSection({ t, lang, onAddSource }: { t: AdminText; lang: UILang; onAddSource: () => void }) {
  const mode = useApiMode();
  const [hidden, setHidden] = useState(false);
  const query = useAdminQuery(`gaps-${mode}-${hidden}`, () => admin.gaps(hidden));
  const g = t.gaps;

  return (
    <section className="mb-8">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 font-semibold text-lg tracking-tight">
            {g.title}
            {query.data && query.data.totals.groups > 0 && (
              <span className="rounded-full bg-destructive/10 px-2 py-0.5 font-medium text-destructive text-xs tabular-nums">
                {query.data.totals.groups}
              </span>
            )}
          </h2>
          <p className="mt-0.5 max-w-2xl text-muted-foreground text-sm">{g.subtitle}</p>
          {query.data && (
            <p className="mt-1 text-muted-foreground text-xs">{g.totals(query.data.totals.not_found, query.data.totals.partial)}</p>
          )}
        </div>
        <div className="flex rounded-full bg-muted/80 p-0.5 text-xs" role="radiogroup">
          {[false, true].map((h) => (
            <button
              aria-checked={hidden === h}
              className={cn(
                "rounded-full px-3 py-1 font-medium transition-colors",
                hidden === h ? "bg-card text-foreground shadow-sm" : "text-foreground/50 hover:text-foreground/80",
              )}
              key={String(h)}
              onClick={() => setHidden(h)}
              role="radio"
              type="button"
            >
              {h ? g.showHidden : g.showActive}
            </button>
          ))}
        </div>
      </div>

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
      ) : query.data?.items.length === 0 ? (
        <p className="rounded-2xl border border-dashed bg-card/50 px-4 py-6 text-center text-muted-foreground text-sm">
          {hidden ? g.emptyHidden : g.empty}
        </p>
      ) : (
        <ul className="grid gap-2 md:grid-cols-2">
          {query.data?.items.map((gap) => (
            <GapCard gap={gap} key={gap.id} lang={lang} onAddSource={onAddSource} onChanged={query.reload} t={t} />
          ))}
        </ul>
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
    <li className={cn("flex flex-col gap-3 rounded-2xl border bg-card p-4", gap.status === "not_found" ? "border-l-4 border-l-destructive/60" : "border-l-4 border-l-amber-400")}>
      <div className="flex items-start gap-3">
        <p className="min-w-0 flex-1 font-medium text-sm leading-snug">{gap.example}</p>
        <Badge className="shrink-0 rounded-full px-2 py-1" variant={gap.status === "not_found" ? "red" : "amber"}>
          {g.status[gap.status]}
        </Badge>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground text-xs">
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
    </li>
  );
}
