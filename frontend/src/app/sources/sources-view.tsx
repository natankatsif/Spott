"use client";

import { ArrowLeftIcon } from "lucide-react";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Marquee } from "@/components/ui/marquee";
import { UI, type UILang } from "@/lib/i18n";
import { setUILang, UI_LANGS, useUILang } from "@/lib/lang";
import { cn } from "@/lib/utils";

type Source = {
  name: string;
  url: string;
  body: Record<UILang, string>;
  logo: { src: string; width: number; height: number };
};

const TEXT = {
  ro: {
    back: "Înapoi la asistent",
    title: "Sursele noastre",
    subtitle: "Asistentul răspunde doar din documentele publice ale acestor instituții. Apăsați pe o sursă pentru a deschide site-ul.",
  },
  ru: {
    back: "Назад к ассистенту",
    title: "Наши источники",
    subtitle: "Ассистент отвечает только по публичным документам этих учреждений. Нажмите на источник, чтобы открыть сайт.",
  },
  en: {
    back: "Back to the assistant",
    title: "Our sources",
    subtitle: "The assistant answers only from the public documents of these institutions. Click a source to open its website.",
  },
} satisfies Record<UILang, { back: string; title: string; subtitle: string }>;

// Sites whose public documents are in the index (domains match the crawled URLs).
const sources: Source[] = [
  {
    name: "Primăria Municipiului Chișinău",
    url: "https://www.chisinau.md/",
    body: {
      ro: "Decizii ale Consiliului municipal, dispoziții ale primarului, anunțuri și servicii publice ale capitalei.",
      ru: "Решения Муниципального совета, распоряжения примара, объявления и публичные услуги столицы.",
      en: "Municipal Council decisions, mayor's orders, announcements and public services of the capital.",
    },
    logo: { src: "/logos/primaria-chisinau.webp", width: 335, height: 80 },
  },
  {
    name: "Direcția Generală Arhitectură, Urbanism și Relații Funciare",
    url: "https://dgaurf.md/",
    body: {
      ro: "Planul urbanistic general, planuri urbanistice zonale, certificate de urbanism și autorizații de construire.",
      ru: "Генеральный градостроительный план, зональные планы, градостроительные сертификаты и разрешения на строительство.",
      en: "The General Urban Plan, zonal urban plans, urban planning certificates and building permits.",
    },
    logo: { src: "/logos/dgaurf.webp", width: 142, height: 80 },
  },
  {
    name: "Direcția Generală Mobilitate Urbană",
    url: "https://mobilitatechisinau.md/",
    body: {
      ro: "Transport public, rute și orare, parcări, piste de biciclete și proiecte de mobilitate în oraș.",
      ru: "Общественный транспорт, маршруты и расписания, парковки, велодорожки и проекты городской мобильности.",
      en: "Public transport, routes and timetables, parking, bike lanes and urban mobility projects.",
    },
    logo: { src: "/logos/dgmu.webp", width: 264, height: 80 },
  },
  {
    name: "Regia Transport Electric Chișinău",
    url: "https://rtec.md/",
    body: {
      ro: "Troleibuzele capitalei: rute, orare, tarife, abonamente și noutăți despre rețeaua de transport electric.",
      ru: "Троллейбусы столицы: маршруты, расписания, тарифы, проездные и новости сети электротранспорта.",
      en: "The capital's trolleybuses: routes, timetables, fares, passes and news about the electric transport network.",
    },
    logo: { src: "/logos/rtec.webp", width: 282, height: 80 },
  },
  {
    name: "Parcul Urban de Autobuze",
    url: "https://www.autourban.md/",
    body: {
      ro: "Autobuzele municipale: rute, orare, tarife și informații pentru pasageri.",
      ru: "Муниципальные автобусы: маршруты, расписания, тарифы и информация для пассажиров.",
      en: "Municipal buses: routes, timetables, fares and passenger information.",
    },
    logo: { src: "/logos/parcul-urban-autobuze.webp", width: 89, height: 80 },
  },
  {
    name: "Exploatarea Drumurilor și Podurilor",
    url: "https://exdrupo.md/",
    body: {
      ro: "Întreținerea și repararea drumurilor, podurilor și a infrastructurii rutiere din municipiu.",
      ru: "Содержание и ремонт дорог, мостов и дорожной инфраструктуры муниципия.",
      en: "Maintenance and repair of the city's roads, bridges and road infrastructure.",
    },
    logo: { src: "/logos/exdrupo.webp", width: 226, height: 80 },
  },
  {
    name: "Direcția Generală Locativ-Comunală și Amenajare",
    url: "https://dglca.md/",
    body: {
      ro: "Fondul locativ, servicii comunale, amenajarea curților și a spațiilor publice.",
      ru: "Жилищный фонд, коммунальные услуги, благоустройство дворов и общественных пространств.",
      en: "Housing stock, utilities, and the upkeep of courtyards and public spaces.",
    },
    logo: { src: "/logos/dglca.webp", width: 449, height: 80 },
  },
  {
    name: "Regia Autosalubritate",
    url: "https://autosalubritate.md/",
    body: {
      ro: "Colectarea și evacuarea deșeurilor, graficele de salubrizare și tarifele pentru servicii.",
      ru: "Сбор и вывоз отходов, графики уборки и тарифы на услуги.",
      en: "Waste collection and removal, sanitation schedules and service fees.",
    },
    logo: { src: "/logos/autosalubritate.webp", width: 166, height: 80 },
  },
  {
    name: "S.A. „Apă-Canal Chișinău”",
    url: "https://www.acc.md/",
    body: {
      ro: "Alimentarea cu apă și canalizarea: contracte, tarife, deconectări planificate și avarii.",
      ru: "Водоснабжение и канализация: договоры, тарифы, плановые отключения и аварии.",
      en: "Water supply and sewerage: contracts, tariffs, planned outages and emergencies.",
    },
    logo: { src: "/logos/apa-canal.webp", width: 82, height: 80 },
  },
  {
    name: "Î.M. Asociația de Gospodărie a Spațiilor Verzi",
    url: "https://agsv.md/",
    body: {
      ro: "Parcuri, scuaruri și spații verzi: întreținere, plantări și toaletarea arborilor.",
      ru: "Парки, скверы и зелёные зоны: уход, посадки и обрезка деревьев.",
      en: "Parks, squares and green spaces: maintenance, planting and tree pruning.",
    },
    logo: { src: "/logos/agsv.webp", width: 251, height: 80 },
  },
  {
    name: "IMSP Asociația Medicală Teritorială Centru",
    url: "https://amt-centru.md/",
    body: {
      ro: "Asistență medicală primară în sectorul Centru: centre de sănătate, program și servicii.",
      ru: "Первичная медицинская помощь в секторе Центр: центры здоровья, график работы и услуги.",
      en: "Primary health care in the Centru district: health centres, opening hours and services.",
    },
    logo: { src: "/logos/amt-centru.webp", width: 343, height: 80 },
  },
];

const hostOf = (url: string) => new URL(url).hostname.replace(/^www\./, "");

/** Resolves once every logo has loaded (or failed): the marquee starts moving only then. */
function useLogosReady(timeoutMs = 5000): boolean {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let left = sources.length;
    const done = () => --left <= 0 && setReady(true);
    for (const s of sources) {
      const img = new window.Image();
      img.onload = img.onerror = done;
      img.src = s.logo.src;
    }
    const timer = setTimeout(() => setReady(true), timeoutMs); // a slow logo must not freeze the page
    return () => clearTimeout(timer);
  }, [timeoutMs]);
  return ready;
}

const SourceCard = ({ name, url, body, logo, lang, ready }: Source & { lang: UILang; ready: boolean }) => {
  return (
    <a className="block w-full max-w-sm" href={url} rel="noopener noreferrer" target="_blank">
      <Card className="relative w-full max-w-sm cursor-pointer overflow-hidden border border-border bg-card shadow-none p-4 transition-colors hover:border-ring/40">
        <CardContent className="p-0 flex flex-col gap-2">
          {ready ? (
            // eslint-disable-next-line @next/next/no-img-element -- pre-sized WebP, already preloaded by useLogosReady
            <img
              alt={name}
              className="h-14 w-auto max-w-full self-start object-contain object-left sm:h-10 animate-in fade-in duration-300"
              height={logo.height}
              src={logo.src}
              width={logo.width}
            />
          ) : (
            <div
              aria-hidden
              className="h-14 w-full animate-pulse rounded-md bg-muted sm:h-10"
            />
          )}
          <div className="flex flex-col">
            <p className="text-sm font-medium text-foreground">{name}</p>
            <p className="text-xs font-medium text-muted-foreground">{hostOf(url)}</p>
          </div>
          <p className="text-sm text-foreground leading-relaxed">{body[lang]}</p>
        </CardContent>
      </Card>
    </a>
  );
};

export function SourcesView() {
  const lang = useUILang();
  const ready = useLogosReady();
  const tx = TEXT[lang];

  // phones: the document doesn't scroll or bounce here (globals.css html.page-locked), only the columns move
  useEffect(() => {
    document.documentElement.classList.add("page-locked");
    return () => document.documentElement.classList.remove("page-locked");
  }, []);
  const cards = (keep: (i: number) => boolean) =>
    sources.filter((_, i) => keep(i)).map((source) => <SourceCard key={source.url} lang={lang} ready={ready} {...source} />);

  return (
    <main className="mx-auto flex h-dvh w-full max-w-5xl flex-col gap-4 overflow-hidden px-4 py-6 md:h-auto md:min-h-dvh md:gap-6 md:overflow-visible md:py-8">
      <header className="flex flex-col gap-2">
        <Link className="flex items-center gap-1 text-muted-foreground text-sm hover:text-foreground" href="/">
          <ArrowLeftIcon className="size-3.5" /> {tx.back}
        </Link>
        <h1 className="font-semibold text-2xl">{tx.title}</h1>
        <p className="text-muted-foreground text-sm">{tx.subtitle}</p>
      </header>

      {/* the scrolling columns sit centred in the space between the header and the language footer */}
      {/* phones: the columns fill the screen between the header and the languages (the page itself never scrolls) */}
      <div className="flex min-h-0 flex-1 items-center">
        <div className="relative flex h-full w-full md:h-125 flex-row items-center justify-center overflow-hidden">
          <div className="flex flex-row items-center justify-center w-full gap-4 px-4 h-full">
            <Marquee pauseOnHover vertical className={cn("[--duration:20s] h-full sm:flex hidden flex-1", !ready && "[&>div]:[animation-play-state:paused]")}>
              {cards((i) => i % 3 === 0)}
            </Marquee>
            <Marquee reverse pauseOnHover vertical className={cn("[--duration:20s] h-full hidden sm:flex flex-1", !ready && "[&>div]:[animation-play-state:paused]")}>
              {cards((i) => i % 3 === 1)}
            </Marquee>
            <Marquee pauseOnHover vertical className={cn("[--duration:20s] h-full hidden lg:flex flex-1", !ready && "[&>div]:[animation-play-state:paused]")}>
              {cards((i) => i % 3 === 2)}
            </Marquee>
            <Marquee pauseOnHover vertical className={cn("[--duration:20s] h-full sm:hidden flex flex-1", !ready && "[&>div]:[animation-play-state:paused]")}>
              {cards(() => true)}
            </Marquee>
          </div>
          <div className="pointer-events-none absolute inset-x-0 top-0 h-1/3 bg-linear-to-b from-background"></div>
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-1/3 bg-linear-to-t from-background"></div>
        </div>
      </div>

      {/* Same line as the chat's "powered by…" footer, holding the language switch; synced site-wide. */}
      <footer className="mt-auto flex items-center justify-center gap-3 text-foreground/80 text-sm">
        {UI_LANGS.map((l) => (
          <button
            aria-pressed={lang === l}
            className={cn(
              "underline-offset-2 transition-colors hover:text-foreground",
              lang === l ? "font-medium text-foreground underline" : "text-foreground/60",
            )}
            key={l}
            lang={l}
            onClick={() => setUILang(l)}
            type="button"
          >
            {UI[l].langName}
          </button>
        ))}
      </footer>
    </main>
  );
}
