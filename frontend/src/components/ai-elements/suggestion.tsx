"use client";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ComponentProps } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

// project: a plain horizontal scroller instead of ScrollArea, so that
//  - a mouse wheel scrolls the row sideways (ScrollArea only moved with a trackpad's horizontal swipe); at either end
//    the wheel is passed on, so the chat still scrolls when the pointer happens to be over the chips;
//  - the edges fade out (mask) only on the side where more chips are hidden, instead of a hard cut.
export type SuggestionsProps = ComponentProps<"div">;

const FADE = 32; // px

export const Suggestions = ({ className, children, ...props }: SuggestionsProps) => {
  const ref = useRef<HTMLDivElement>(null);
  const [fade, setFade] = useState({ left: false, right: false });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => {
      const max = el.scrollWidth - el.clientWidth;
      setFade({ left: el.scrollLeft > 1, right: el.scrollLeft < max - 1 });
    };
    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey || Math.abs(e.deltaX) >= Math.abs(e.deltaY)) return; // pinch zoom / a trackpad's own sideways swipe
      const max = el.scrollWidth - el.clientWidth;
      if (max <= 0) return;
      const atStart = el.scrollLeft <= 0 && e.deltaY < 0;
      const atEnd = el.scrollLeft >= max - 1 && e.deltaY > 0;
      if (atStart || atEnd) return; // nothing more this way: let the page scroll
      e.preventDefault();
      el.scrollLeft += e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY; // lines → px for mice that report lines
    };
    update();
    el.addEventListener("scroll", update, { passive: true });
    el.addEventListener("wheel", onWheel, { passive: false });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => {
      el.removeEventListener("scroll", update);
      el.removeEventListener("wheel", onWheel);
      ro.disconnect();
    };
  }, []);

  const mask = `linear-gradient(to right, transparent 0, #000 ${fade.left ? FADE : 0}px, #000 calc(100% - ${fade.right ? FADE : 0}px), transparent 100%)`;

  return (
    <div
      className="w-full overflow-x-auto overscroll-x-contain whitespace-nowrap [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      ref={ref}
      style={{ maskImage: mask, WebkitMaskImage: mask }}
      {...props}
    >
      {/* py-1: room for the chips' shadow inside the scroller */}
      <div className={cn("flex w-max flex-nowrap items-center gap-2 py-1", className)}>{children}</div>
    </div>
  );
};

export type SuggestionProps = Omit<ComponentProps<typeof Button>, "onClick"> & {
  suggestion: string;
  onClick?: (suggestion: string) => void;
};

export const Suggestion = ({
  suggestion,
  onClick,
  className,
  variant = "outline",
  size = "sm",
  children,
  ...props
}: SuggestionProps) => {
  const handleClick = useCallback(() => {
    onClick?.(suggestion);
  }, [onClick, suggestion]);

  return (
    <Button
      className={cn("cursor-pointer rounded-full px-4", className)}
      onClick={handleClick}
      size={size}
      type="button"
      variant={variant}
      {...props}
    >
      {children || suggestion}
    </Button>
  );
};
