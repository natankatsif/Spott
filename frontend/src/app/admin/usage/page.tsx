"use client";

import { CheckIcon, CircleAlertIcon, CoinsIcon, PlusIcon } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/admin/empty-state";
import { PageHeader } from "@/components/admin/page-header";
import { StatCard } from "@/components/admin/stat-card";
import { Badge } from "@/components/spell/badge";
import { Spinner } from "@/components/spell/spinner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { admin, useAdminQuery } from "@/lib/admin";
import { errorText } from "@/lib/admin-errors";
import { number, shortDay } from "@/lib/admin-format";
import { ADMIN_UI, type AdminText } from "@/lib/admin-i18n";
import type { Currency, ModelPrice, Pricing, UsageDay, UsageKind } from "@/lib/api";
import type { UILang } from "@/lib/i18n";
import { useUILang } from "@/lib/lang";
import { useApiMode } from "@/lib/mode";
import { cn } from "@/lib/utils";

const RANGES = [7, 30, 90] as const;
const CURRENCIES: Currency[] = ["USD", "EUR", "MDL"];
const LOCALE: Record<UILang, string> = { ro: "ro-RO", ru: "ru-RU", en: "en-GB" };

type PriceDraft = { input: string; output: string };
type Draft = {
  currency?: Currency;
  rates?: Partial<Record<Currency, string>>;
  budget?: string; // in the display currency
  prices?: Record<string, PriceDraft>;
  added?: string[];
};

/** Money in the chosen currency: more decimals for the small sums a question costs. */
function money(usd: number | null | undefined, currency: Currency, rate: number, lang: UILang): string {
  if (usd == null) return "—";
  const v = usd * rate;
  const abs = Math.abs(v);
  const digits = abs === 0 ? 2 : abs < 0.01 ? 4 : abs < 1 ? 3 : 2;
  return new Intl.NumberFormat(LOCALE[lang], {
    style: "currency",
    currency,
    currencyDisplay: currency === "MDL" ? "code" : "narrowSymbol",
    minimumFractionDigits: Math.min(2, digits),
    maximumFractionDigits: digits,
  }).format(v);
}

const compact = (n: number, lang: UILang) =>
  new Intl.NumberFormat(LOCALE[lang], { notation: "compact", maximumFractionDigits: 1 }).format(n);

const parse = (s: string | undefined) => {
  if (s === undefined || s.trim() === "") return null;
  const n = Number(s.replace(",", "."));
  return Number.isFinite(n) && n >= 0 ? n : Number.NaN;
};

export default function UsagePage() {
  const lang = useUILang();
  const t = ADMIN_UI[lang];
  const tu = t.usage;
  const mode = useApiMode();
  const [days, setDays] = useState<(typeof RANGES)[number]>(30);
  const query = useAdminQuery(`usage-${mode}-${days}`, () => admin.usage(days));
  const report = query.data;
  const [draft, setDraft] = useState<Draft>({});
  const [saving, setSaving] = useState(false);
  const [metric, setMetric] = useState<"money" | "tokens">("money");
  const [newModel, setNewModel] = useState("");

  const header = (actions?: ReactNode) => <PageHeader actions={actions} subtitle={tu.subtitle} title={tu.title} />;

  if (!report && !query.error) {
    return (
      <>
        {header()}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton className="h-24 rounded-2xl" key={i} />
          ))}
        </div>
        <Skeleton className="mt-4 h-72 rounded-2xl" />
      </>
    );
  }
  if (!report) {
    return (
      <>
        {header()}
        <EmptyState
          action={<Button onClick={query.reload} variant="outline">{t.common.retry}</Button>}
          hint={errorText(query.error, lang)}
          icon={CoinsIcon}
          title={t.common.loadError}
        />
      </>
    );
  }

  const saved = report.pricing;
  const currency = draft.currency ?? saved.currency;
  const rateText = (c: Currency) => draft.rates?.[c] ?? String(saved.rates[c] ?? 1);
  const rate = currency === "USD" ? 1 : parse(rateText(currency)) || saved.rates[currency] || 1;
  const $ = (usd: number | null | undefined) => money(usd, currency, rate, lang);
  const priceText = (m: string): PriceDraft =>
    draft.prices?.[m] ?? { input: saved.prices[m] ? String(saved.prices[m].input) : "", output: saved.prices[m] ? String(saved.prices[m].output) : "" };
  const budgetText = draft.budget ?? (saved.budget_usd != null ? String(+(saved.budget_usd * rate).toFixed(2)) : "");
  const dirty = Object.keys(draft).some((k) => k !== "added") || (draft.added?.length ?? 0) > 0;

  // every model to price: the ones used in the range, then the rest seen or set in admin → Models, then added here
  const used = new Set(report.models.map((m) => m.model));
  const pricingRows = [
    ...report.models.map((m) => m.model),
    ...report.known_models.filter((m) => !used.has(m)),
    ...(draft.added ?? []).filter((m) => !used.has(m) && !report.known_models.includes(m)),
  ];

  const setPrice = (m: string, side: keyof PriceDraft, v: string) =>
    setDraft((d) => ({ ...d, prices: { ...d.prices, [m]: { ...priceText(m), ...d.prices?.[m], [side]: v } } }));

  const save = async () => {
    const prices: Record<string, ModelPrice> = { ...saved.prices };
    for (const m of pricingRows) {
      const p = priceText(m);
      const i = parse(p.input);
      const o = parse(p.output);
      if (Number.isNaN(i) || Number.isNaN(o)) return toast.error(`${m}: ${t.common.loadError}`);
      if (i === null && o === null) delete prices[m];
      else prices[m] = { input: i ?? 0, output: o ?? 0 };
    }
    const rates = { ...saved.rates };
    for (const c of CURRENCIES) {
      if (c === "USD") continue;
      const r = parse(rateText(c));
      if (!r) return toast.error(`${tu.rate(c)}: > 0`);
      rates[c] = r;
    }
    const b = parse(budgetText);
    if (Number.isNaN(b)) return toast.error(tu.budgetInput(currency));
    const next: Pricing = {
      currency,
      rates,
      prices,
      budget_usd: b === null ? null : b / (currency === "USD" ? 1 : rates[currency]),
    };
    setSaving(true);
    try {
      await admin.savePricing(next);
      toast.success(tu.saved);
      setDraft({});
      query.reload();
    } catch (err) {
      toast.error(errorText(err, lang));
    } finally {
      setSaving(false);
    }
  };

  const budget = saved.budget_usd;
  const spentShare = budget ? report.month.cost_usd / budget : null;
  const unpriced = report.range.unpriced_calls;
  const empty = report.all_time.calls === 0;

  return (
    <>
      {header(
        <>
          <RangeSwitch days={days} onChange={setDays} t={tu} />
          <Button className="rounded-xl" disabled={!dirty || saving} onClick={save}>
            {saving ? <Spinner className="size-4" /> : <CheckIcon />}
            {tu.save}
          </Button>
        </>,
      )}
      {dirty && <p className="-mt-3 mb-4 text-warning text-xs">{tu.unsaved}</p>}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatCard
          hint={tu.calls(number(report.today.calls, lang))}
          label={tu.today}
          value={$(report.today.cost_usd)}
        />
        <StatCard hint={tu.forecast($(report.month_forecast_usd))} label={tu.month} value={$(report.month.cost_usd)} />
        <div className="flex min-w-0 flex-col justify-end rounded-2xl border bg-card p-3 sm:p-4">
          <p className="truncate text-muted-foreground text-xs">{tu.budget}</p>
          {budget ? (
            <>
              <p className="mt-1 font-semibold text-xl tabular-nums tracking-tight sm:text-2xl">{$(budget)}</p>
              <div
                aria-label={`${Math.round((spentShare ?? 0) * 100)}%`}
                className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted"
                role="img"
              >
                <div
                  className={cn("h-full rounded-full transition-[width]", (spentShare ?? 0) > 1 ? "bg-destructive" : (spentShare ?? 0) > 0.8 ? "bg-warning" : "bg-brand")}
                  style={{ width: `${Math.min(100, (spentShare ?? 0) * 100)}%` }}
                />
              </div>
              <p className={cn("mt-1 text-xs", (spentShare ?? 0) > 1 ? "text-destructive" : "text-muted-foreground")}>
                {(spentShare ?? 0) > 1 ? tu.budgetOver($(report.month.cost_usd - budget)) : tu.budgetLeft($(budget - report.month.cost_usd))}
              </p>
            </>
          ) : (
            <p className="mt-1 font-semibold text-muted-foreground text-xl sm:text-2xl">{tu.noBudget}</p>
          )}
        </div>
        <StatCard
          hint={tu.questions(number(report.questions, lang))}
          label={tu.perQuestion}
          value={$(report.cost_per_question_usd)}
        />
        <StatCard
          className="col-span-2 lg:col-span-1"
          hint={tu.tokensShort(compact(report.all_time.input_tokens, lang), compact(report.all_time.output_tokens, lang))}
          label={tu.allTime}
          value={$(report.all_time.cost_usd)}
        />
      </div>

      {unpriced > 0 && (
        <p className="mt-3 flex items-start gap-1.5 rounded-xl bg-warning-bg px-3 py-2 text-warning text-xs">
          <CircleAlertIcon className="mt-px size-3.5 shrink-0" /> {tu.unpriced(number(unpriced, lang))}
        </p>
      )}

      {empty ? (
        <div className="mt-4">
          <EmptyState hint={tu.emptyHint} icon={CoinsIcon} title={tu.empty} />
        </div>
      ) : (
        <>
          <section className="mt-4 rounded-2xl border bg-card p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="font-medium text-sm">{tu.daily}</h2>
                <p className="text-muted-foreground text-xs">
                  {metric === "money"
                    ? $(report.range.cost_usd)
                    : tu.tokensShort(compact(report.range.input_tokens, lang), compact(report.range.output_tokens, lang))}
                  {" · "}
                  {tu.calls(number(report.range.calls, lang))}
                </p>
              </div>
              <Segmented
                onChange={setMetric}
                options={[
                  ["money", tu.money],
                  ["tokens", tu.tokens],
                ]}
                value={metric}
              />
            </div>
            <DailyBars days={report.daily} format={$} lang={lang} metric={metric} t={tu} />
          </section>

          <section className="mt-4 rounded-2xl border bg-card p-4">
            <h2 className="mb-3 font-medium text-sm">{tu.byKind}</h2>
            <KindBars format={$} kinds={report.kinds} lang={lang} t={tu} />
          </section>
        </>
      )}

      <section className="mt-4 rounded-2xl border bg-card">
        <div className="flex flex-wrap items-end justify-between gap-3 p-4 pb-3">
          <div className="min-w-0">
            <h2 className="font-medium text-sm">{tu.byModel}</h2>
            <p className="mt-0.5 max-w-2xl text-muted-foreground text-xs">{tu.settingsHint}</p>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead>
              <tr className="border-y bg-muted/40 text-left text-muted-foreground text-xs">
                <th className="px-4 py-2 font-medium">{tu.model}</th>
                <th className="px-3 py-2 text-right font-medium">{tu.callsCol}</th>
                <th className="px-3 py-2 text-right font-medium">{tu.inputCol}</th>
                <th className="px-3 py-2 text-right font-medium">{tu.outputCol}</th>
                <th className="px-3 py-2 font-medium">{tu.priceCol}</th>
                <th className="px-4 py-2 text-right font-medium">{tu.costCol}</th>
              </tr>
            </thead>
            <tbody>
              {pricingRows.map((m) => {
                const row = report.models.find((r) => r.model === m);
                const p = priceText(m);
                return (
                  <tr className="border-b last:border-b-0" key={m}>
                    <td className="px-4 py-2">
                      <span className="font-mono text-[13px]">{m}</span>
                      {row && <span className="ml-1.5 text-muted-foreground text-xs">{row.provider}</span>}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{row ? number(row.calls, lang) : "—"}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{row ? compact(row.input_tokens, lang) : "—"}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{row ? compact(row.output_tokens, lang) : "—"}</td>
                    <td className="px-3 py-1.5">
                      <div className="flex items-center gap-1.5">
                        <PriceInput label={`${m} ${tu.priceIn}`} onChange={(v) => setPrice(m, "input", v)} placeholder={tu.priceIn} value={p.input} />
                        <PriceInput label={`${m} ${tu.priceOut}`} onChange={(v) => setPrice(m, "output", v)} placeholder={tu.priceOut} value={p.output} />
                      </div>
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums">
                      {row?.cost_usd != null ? $(row.cost_usd) : row ? <Badge variant="amber">{tu.noPrice}</Badge> : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <form
          className="flex gap-2 border-t p-4"
          onSubmit={(e) => {
            e.preventDefault();
            const m = newModel.trim();
            if (!m || pricingRows.includes(m)) return;
            setDraft((d) => ({ ...d, added: [...(d.added ?? []), m] }));
            setNewModel("");
          }}
        >
          <Input
            aria-label={tu.addModel}
            className="max-w-xs rounded-xl font-mono text-sm"
            onChange={(e) => setNewModel(e.target.value)}
            placeholder={tu.addModelPlaceholder}
            spellCheck={false}
            value={newModel}
          />
          <Button className="rounded-xl" disabled={!newModel.trim()} type="submit" variant="outline">
            <PlusIcon /> {tu.addModel}
          </Button>
        </form>
      </section>

      <section className="mt-4 mb-8 rounded-2xl border bg-card p-4">
        <h2 className="font-medium text-sm">{tu.settingsTitle}</h2>
        <div className="mt-3 grid gap-4 sm:grid-cols-[auto_1fr_1fr_1fr] sm:items-end">
          <div>
            <p className="mb-1.5 text-muted-foreground text-xs">{tu.currency}</p>
            <Segmented
              onChange={(c) => setDraft((d) => ({ ...d, currency: c, budget: undefined }))}
              options={CURRENCIES.map((c) => [c, c] as const)}
              value={currency}
            />
          </div>
          {CURRENCIES.filter((c) => c !== "USD").map((c) => (
            <label className="block" key={c}>
              <span className="mb-1.5 block text-muted-foreground text-xs">{tu.rate(c)}</span>
              <Input
                className="rounded-xl tabular-nums"
                inputMode="decimal"
                onChange={(e) => setDraft((d) => ({ ...d, rates: { ...d.rates, [c]: e.target.value } }))}
                value={rateText(c)}
              />
            </label>
          ))}
          <label className="block">
            <span className="mb-1.5 block text-muted-foreground text-xs">{tu.budgetInput(currency)}</span>
            <Input
              className="rounded-xl tabular-nums"
              inputMode="decimal"
              onChange={(e) => setDraft((d) => ({ ...d, budget: e.target.value }))}
              placeholder="—"
              value={budgetText}
            />
          </label>
        </div>
        <p className="mt-2 text-muted-foreground text-xs">{tu.rateHint}</p>
      </section>
    </>
  );
}

function PriceInput({ value, onChange, placeholder, label }: { value: string; onChange: (v: string) => void; placeholder: string; label: string }) {
  const bad = Number.isNaN(parse(value));
  return (
    <Input
      aria-invalid={bad || undefined}
      aria-label={label}
      className={cn("h-8 w-24 rounded-lg px-2 text-right text-sm tabular-nums", bad && "border-destructive")}
      inputMode="decimal"
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      value={value}
    />
  );
}

function Segmented<T extends string>({ value, options, onChange }: { value: T; options: readonly (readonly [T, string])[]; onChange: (v: T) => void }) {
  return (
    <div className="flex w-fit rounded-full bg-muted/80 p-0.5 text-xs" role="radiogroup">
      {options.map(([v, label]) => (
        <button
          aria-checked={v === value}
          className={cn(
            "rounded-full px-3 py-1 font-medium transition-colors",
            v === value ? "bg-card text-foreground shadow-sm" : "text-foreground/60 hover:text-foreground",
          )}
          key={v}
          onClick={() => onChange(v)}
          role="radio"
          type="button"
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function RangeSwitch({ days, onChange, t }: { days: number; onChange: (d: (typeof RANGES)[number]) => void; t: AdminText["usage"] }) {
  return (
    <Segmented
      onChange={(v) => onChange(Number(v) as (typeof RANGES)[number])}
      options={RANGES.map((d) => [String(d), t.range(d)] as const)}
      value={String(days)}
    />
  );
}

/** Bars per day: money (one series) or tokens (input and output stacked), a tooltip on hover or tap. */
function DailyBars({
  days,
  metric,
  format,
  lang,
  t,
}: {
  days: UsageDay[];
  metric: "money" | "tokens";
  format: (usd: number) => string;
  lang: UILang;
  t: AdminText["usage"];
}) {
  const [active, setActive] = useState<number | null>(null);
  const values = days.map((d) => (metric === "money" ? d.cost_usd : d.input_tokens + d.output_tokens));
  const max = Math.max(...values, 0);
  const ticks = useMemo(() => niceTicks(max), [max]);
  const top = ticks.at(-1) || 1;
  const H = 180;
  const n = days.length;
  const label = (v: number) => (metric === "money" ? format(v) : compact(v, lang));
  const every = Math.ceil(n / 6);
  const shown = active ?? n - 1;
  const d = days[shown];

  return (
    <div>
      {metric === "tokens" && (
        <div className="mb-2 flex gap-4 text-muted-foreground text-xs">
          <span className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm bg-series-1" /> {t.input}
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm bg-series-2" /> {t.output}
          </span>
        </div>
      )}
      <div className="flex gap-2">
        <div className="relative w-14 shrink-0 text-right text-[11px] text-muted-foreground tabular-nums" style={{ height: H }}>
          {ticks.map((v) => (
            <span className="absolute right-0 -translate-y-1/2" key={v} style={{ top: H - (v / top) * H }}>
              {label(v)}
            </span>
          ))}
        </div>
        <div className="relative min-w-0 flex-1">
          <div className="relative" onMouseLeave={() => setActive(null)} style={{ height: H }}>
            {ticks.map((v) => (
              <div className="absolute inset-x-0 border-border border-t border-dashed" key={v} style={{ top: H - (v / top) * H }} />
            ))}
            <div className="absolute inset-0 flex items-end gap-[2px]">
              {days.map((day, i) => {
                const inH = metric === "money" ? (day.cost_usd / top) * H : (day.input_tokens / top) * H;
                const outH = metric === "money" ? 0 : (day.output_tokens / top) * H;
                return (
                  <button
                    aria-label={`${shortDay(day.day, lang)}: ${label(values[i])}`}
                    className="group relative flex h-full min-w-0 flex-1 flex-col justify-end outline-none"
                    key={day.day}
                    onClick={() => setActive(i)}
                    onFocus={() => setActive(i)}
                    onMouseEnter={() => setActive(i)}
                    type="button"
                  >
                    <span className={cn("absolute inset-x-0 inset-y-0 rounded-md transition-colors", i === active && "bg-muted/60")} />
                    {outH > 0 && (
                      <span className="relative mb-[2px] rounded-t-[4px] bg-series-2" style={{ height: Math.max(outH - 2, 1) }} />
                    )}
                    <span
                      className={cn("relative bg-series-1", outH > 0 ? "" : "rounded-t-[4px]", values[i] === 0 && "opacity-0")}
                      style={{ height: Math.max(inH, values[i] > 0 ? 2 : 0) }}
                    />
                  </button>
                );
              })}
            </div>
          </div>
          <div className="mt-1.5 flex gap-[2px] text-[11px] text-muted-foreground">
            {days.map((day, i) => (
              <span
                className={cn(
                  "min-w-0 flex-1 overflow-visible whitespace-nowrap",
                  i === n - 1 ? "flex justify-end" : "text-center",
                  // phones: every other label, so they don't run into each other
                  ((n - 1 - i) / every) % 2 === 1 && "max-sm:invisible",
                )}
                key={day.day}
              >
                {(n - 1 - i) % every === 0 ? shortDay(day.day, lang) : ""}
              </span>
            ))}
          </div>
        </div>
      </div>
      {d && (
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 rounded-xl bg-muted/50 px-3 py-2 text-xs tabular-nums">
          <span className="font-medium">{shortDay(d.day, lang)}</span>
          <span>{format(d.cost_usd)}</span>
          <span className="flex items-center gap-1">
            <span className="size-2 rounded-sm bg-series-1" /> {t.input}: {compact(d.input_tokens, lang)}
          </span>
          <span className="flex items-center gap-1">
            <span className="size-2 rounded-sm bg-series-2" /> {t.output}: {compact(d.output_tokens, lang)}
          </span>
          <span className="text-muted-foreground">{t.calls(number(d.calls, lang))}</span>
        </div>
      )}
    </div>
  );
}

/** Where the money goes: one bar per kind of call, its share of the period's cost. */
function KindBars({ kinds, format, lang, t }: { kinds: UsageKind[]; format: (usd: number) => string; lang: UILang; t: AdminText["usage"] }) {
  const total = kinds.reduce((s, k) => s + k.cost_usd, 0);
  const tokens = kinds.reduce((s, k) => s + k.input_tokens + k.output_tokens, 0);
  const share = (k: UsageKind) => (total > 0 ? k.cost_usd / total : tokens > 0 ? (k.input_tokens + k.output_tokens) / tokens : 0);
  const max = Math.max(...kinds.map(share), 0.0001);
  return (
    <ul className="flex flex-col gap-3">
      {kinds.map((k) => (
        <li key={k.kind}>
          <div className="mb-1 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-sm">
            <span className="min-w-0 max-sm:w-full">{t.kinds[k.kind] ?? k.kind}</span>
            <span className="text-muted-foreground text-xs tabular-nums">
              {t.calls(number(k.calls, lang))} · {t.tokensShort(compact(k.input_tokens, lang), compact(k.output_tokens, lang))} ·{" "}
              <span className="font-medium text-foreground">{format(k.cost_usd)}</span>
            </span>
          </div>
          <div className="h-2 rounded-full bg-muted" title={t.share(`${Math.round(share(k) * 100)}%`)}>
            <div className="h-full rounded-full bg-series-1" style={{ width: `${(share(k) / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

/** 0 and three round steps up to at least `max`. */
function niceTicks(max: number): number[] {
  if (max <= 0) return [0];
  const raw = max / 3;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? raw;
  return [0, step, step * 2, step * 3];
}
