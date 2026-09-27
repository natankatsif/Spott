"use client";

import { EyeOffIcon, MessageSquareQuoteIcon, PinIcon, PinOffIcon, ThumbsUpIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/admin/empty-state";
import { PageHeader } from "@/components/admin/page-header";
import { likeShare } from "@/components/admin/votes";
import { Badge } from "@/components/spell/badge";
import { Spinner } from "@/components/spell/spinner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { admin, useAdminQuery } from "@/lib/admin";
import { errorText } from "@/lib/admin-errors";
import { ADMIN_UI } from "@/lib/admin-i18n";
import type { Lang, Suggestion } from "@/lib/api";
import { useUILang } from "@/lib/lang";
import { useApiMode } from "@/lib/mode";
import { cn } from "@/lib/utils";

// answers (and so quick questions) exist in Romanian and Russian only
const QUESTION_LANGS: Lang[] = ["ro", "ru"];

export default function SuggestionsPage() {
  const uiLang = useUILang();
  const t = ADMIN_UI[uiLang];
  const mode = useApiMode();
  const [lang, setLang] = useState<Lang>(uiLang === "ru" ? "ru" : "ro");
  const list = useAdminQuery(`sugg-${mode}-${lang}`, () => admin.suggestions(lang));
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [hiding, setHiding] = useState<number | null>(null);
  const [pinning, setPinning] = useState<number | null>(null);
  const pinnedCount = list.data?.filter((s) => s.pinned).length ?? 0;

  const togglePin = async (s: Suggestion) => {
    setPinning(s.id);
    try {
      await admin.pinSuggestion(s.question, s.lang, !s.pinned);
      toast.success(s.pinned ? t.suggestions.unpinnedOk : t.suggestions.pinnedBoth);
      list.reload();
    } catch (err) {
      toast.error(errorText(err, uiLang));
    } finally {
      setPinning(null);
    }
  };

  const valid = text.trim().length >= 10 && text.trim().length <= 120;

  const pin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    try {
      await admin.pinSuggestion(text.trim(), lang);
      toast.success(t.suggestions.pinnedBoth);
      setText("");
      list.reload();
    } catch (err) {
      toast.error(errorText(err, uiLang));
    } finally {
      setBusy(false);
    }
  };

  const hide = async (id: number) => {
    setHiding(id);
    try {
      await admin.hideSuggestion(id);
      toast.success(t.suggestions.hidden);
      list.reload();
    } catch (err) {
      toast.error(errorText(err, uiLang));
    } finally {
      setHiding(null);
    }
  };

  return (
    <>
      <PageHeader
        actions={
          <ToggleGroup onValueChange={(v) => v && setLang(v as Lang)} size="sm" type="single" value={lang} variant="outline">
            {QUESTION_LANGS.map((l) => (
              <ToggleGroupItem className="px-3 font-semibold uppercase" key={l} value={l}>
                {l}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        }
        subtitle={t.suggestions.subtitle}
        title={t.suggestions.title}
      />

      <form className="mb-6 rounded-2xl border bg-card p-4" onSubmit={pin}>
        <label className="mb-2 flex items-center gap-1.5 font-medium text-sm" htmlFor="pin-q">
          <PinIcon className="size-4 text-brand" /> {t.suggestions.add}
        </label>
        <Textarea
          className="min-h-20 resize-none rounded-xl"
          id="pin-q"
          lang={lang}
          maxLength={120}
          onChange={(e) => setText(e.target.value)}
          placeholder={t.suggestions.placeholder}
          value={text}
        />
        <div className="mt-3 flex items-center justify-between gap-3">
          <span className={cn("text-xs tabular-nums", text && !valid ? "text-destructive" : "text-muted-foreground")}>
            {text && !valid ? t.suggestions.tooShort : `${text.trim().length}/120`}
          </span>
          <Button className="rounded-xl" disabled={!valid || busy} type="submit">
            {busy ? <Spinner className="size-4" /> : <PinIcon />}
            {t.suggestions.add}
          </Button>
        </div>
      </form>

      {list.loading ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton className="h-16 rounded-2xl" key={i} />
          ))}
        </div>
      ) : list.error && !list.data ? (
        <EmptyState
          action={<Button onClick={list.reload} variant="outline">{t.common.retry}</Button>}
          hint={errorText(list.error, uiLang)}
          icon={MessageSquareQuoteIcon}
          title={t.common.loadError}
        />
      ) : list.data?.length === 0 ? (
        <EmptyState hint={t.suggestions.noneOnHome} icon={MessageSquareQuoteIcon} title={t.suggestions.empty} />
      ) : (
        <>
        {pinnedCount === 0 && <p className="mb-3 rounded-xl bg-amber-50 px-3 py-2 text-amber-900 text-sm">{t.suggestions.noneOnHome}</p>}
        <ul className="flex flex-col gap-2">
          {list.data?.map((s) => (
            <li key={s.id}>
              <Card className={cn("rounded-2xl py-4 shadow-none", s.pinned && "border-brand/30 bg-accent/40")}>
                <CardContent className="flex items-center gap-3 px-4">
                  <div className="min-w-0 flex-1">
                    <p className="font-medium text-sm" lang={s.lang}>
                      {s.question}
                    </p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-2 text-muted-foreground text-xs">
                      {s.pinned ? (
                        <Badge className="gap-1" variant="blue">
                          <PinIcon className="size-3" /> {t.suggestions.onHome}
                        </Badge>
                      ) : (
                        <Badge variant="slate">{t.suggestions.proposal}</Badge>
                      )}
                      {s.check === "pending" && <Badge variant="amber">{t.suggestions.pending}</Badge>}
                      {s.check === "failed" && <Badge variant="red">{t.suggestions.failed}</Badge>}
                      <span>{t.suggestions.asked(s.asked_count)}</span>
                      {s.rating_avg != null && (
                        <span className="flex items-center gap-1">
                          <ThumbsUpIcon className="size-3 text-emerald-600" /> {Math.round(likeShare(s.rating_avg) * 100)}%
                        </span>
                      )}
                    </div>
                  </div>
                  <Button
                    className="shrink-0 rounded-xl"
                    disabled={pinning === s.id}
                    onClick={() => togglePin(s)}
                    size="sm"
                    variant={s.pinned ? "ghost" : "outline"}
                  >
                    {pinning === s.id ? <Spinner className="size-4" /> : s.pinned ? <PinOffIcon /> : <PinIcon />}
                    <span className="hidden sm:inline">{s.pinned ? t.suggestions.unpin : t.suggestions.pin}</span>
                  </Button>
                  <Button className="shrink-0 rounded-xl" disabled={hiding === s.id} onClick={() => hide(s.id)} size="sm" variant="ghost">
                    {hiding === s.id ? <Spinner className="size-4" /> : <EyeOffIcon />}
                    <span className="hidden sm:inline">{t.suggestions.hide}</span>
                  </Button>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
        </>
      )}
    </>
  );
}
