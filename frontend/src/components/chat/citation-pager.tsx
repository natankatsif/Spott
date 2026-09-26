"use client";

// Phones: an answer's citations as one card at a time (swipe or ← →) instead of a long list of chips.
// A card opens the source preview full screen, or the original when there is no line to show (canPreview).

import { BookOpenIcon, ChevronLeftIcon, ChevronRightIcon, ExternalLinkIcon, ScanSearchIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { canPreview, useSourcePreview } from "@/components/chat/source-preview";
import { Carousel, type CarouselApi, CarouselContent, CarouselItem } from "@/components/ui/carousel";
import type { Citation } from "@/lib/api";
import type { UIText } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export function CitationPager({ citations, t, className }: { citations: Citation[]; t: UIText; className?: string }) {
  const preview = useSourcePreview();
  const [api, setApi] = useState<CarouselApi>();
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (!api) return;
    const onSelect = () => setIndex(api.selectedScrollSnap());
    api.on("select", onSelect);
    api.on("reInit", onSelect);
    return () => {
      api.off("select", onSelect);
      api.off("reInit", onSelect);
    };
  }, [api]);

  if (!citations.length) return null;
  const shown = preview.state?.citations[preview.state.index];
  const many = citations.length > 1;

  return (
    <div className={cn("rounded-2xl border bg-card p-2 shadow-sm", className)}>
      <div className="flex items-center gap-1 px-1 pb-1.5">
        <BookOpenIcon className="size-3.5 text-muted-foreground" />
        <span className="flex-1 text-muted-foreground text-xs">{t.sourcesUsed(citations.length)}</span>
        {many && (
          <>
            <button
              aria-label={t.preview.prev}
              className="flex size-8 items-center justify-center rounded-full text-foreground/70 transition-colors hover:bg-accent disabled:opacity-30"
              disabled={index === 0}
              onClick={() => api?.scrollPrev()}
              type="button"
            >
              <ChevronLeftIcon className="size-4" />
            </button>
            <span className="min-w-10 text-center text-xs tabular-nums" aria-live="polite">
              {index + 1} / {citations.length}
            </span>
            <button
              aria-label={t.preview.next}
              className="flex size-8 items-center justify-center rounded-full text-foreground/70 transition-colors hover:bg-accent disabled:opacity-30"
              disabled={index === citations.length - 1}
              onClick={() => api?.scrollNext()}
              type="button"
            >
              <ChevronRightIcon className="size-4" />
            </button>
          </>
        )}
      </div>

      <Carousel opts={{ align: "start" }} setApi={setApi}>
        <CarouselContent className="-ml-2">
          {citations.map((c) => {
            const previewable = canPreview(c);
            const body = (
              <>
                <span className="flex items-start gap-2">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium text-sm">{c.document_title}</span>
                    <span className="block truncate text-muted-foreground text-xs">
                      {[c.site ?? new URL(c.url).hostname, c.location, c.page ? `${t.page} ${c.page}` : null].filter(Boolean).join(" · ")}
                    </span>
                  </span>
                  {previewable ? (
                    <ScanSearchIcon className="mt-0.5 size-4 shrink-0 text-brand" />
                  ) : (
                    <ExternalLinkIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  )}
                </span>
                {/* many citations come from the same document: the quote is what tells them apart */}
                <span className="mt-1.5 line-clamp-3 border-border border-l-2 pl-2 text-foreground/75 text-xs italic">{c.quote}</span>
                {previewable && <span className="mt-1.5 block font-medium text-brand text-xs">{t.preview.show}</span>}
              </>
            );
            const cardClass = cn(
              "block h-full w-full rounded-xl border bg-background/60 p-3 text-left transition-colors active:scale-[0.99]",
              shown?.id === c.id && "border-brand/40 bg-accent/60",
            );
            return (
              <CarouselItem className="pl-2" key={c.id}>
                {previewable ? (
                  <button className={cardClass} onClick={() => preview.open(citations, c.id)} type="button">
                    {body}
                  </button>
                ) : (
                  <a className={cardClass} href={c.deep_link} rel="noreferrer" target="_blank">
                    {body}
                  </a>
                )}
              </CarouselItem>
            );
          })}
        </CarouselContent>
      </Carousel>
    </div>
  );
}
