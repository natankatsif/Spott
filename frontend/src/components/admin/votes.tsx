import { MinusIcon, ThumbsDownIcon, ThumbsUpIcon } from "lucide-react";
import { cn } from "@/lib/utils";

// The chat rates an answer with a like or a dislike, stored as 5 and 1 (older 1–5 ratings: 4–5 like, 1–2 dislike).
export const isLike = (rating: number) => rating >= 4;
export const isDislike = (rating: number) => rating <= 2;

/** Share of likes, 0–1, from an average of 1 (dislike) and 5 (like) ratings. */
export const likeShare = (average: number) => Math.min(1, Math.max(0, (average - 1) / 4));

export function likesOf(perStar: Record<string, number>) {
  const n = (k: number) => perStar[String(k)] ?? 0;
  return { likes: n(4) + n(5), dislikes: n(1) + n(2) };
}

/** A like, a dislike, or (an old middle rating) neither. */
export function Vote({ rating, className, size = 16 }: { rating: number; className?: string; size?: number }) {
  if (isLike(rating)) return <ThumbsUpIcon className={cn("text-emerald-600", className)} size={size} />;
  if (isDislike(rating)) return <ThumbsDownIcon className={cn("text-destructive", className)} size={size} />;
  return <MinusIcon className={cn("text-muted-foreground", className)} size={size} />;
}

/** Two bars: likes and dislikes, with count and share. */
export function VoteBars({ perStar }: { perStar: Record<string, number> }) {
  const { likes, dislikes } = likesOf(perStar);
  const total = likes + dislikes || 1;
  return (
    <div className="flex flex-col gap-2">
      {[
        { key: "up", count: likes, icon: <ThumbsUpIcon className="text-emerald-600" size={14} />, bar: "bg-emerald-500" },
        { key: "down", count: dislikes, icon: <ThumbsDownIcon className="text-destructive" size={14} />, bar: "bg-destructive/70" },
      ].map((row) => (
        <div className="flex items-center gap-3 text-sm" key={row.key}>
          <span className="flex w-5 shrink-0 items-center">{row.icon}</span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
            <div className={cn("h-full rounded-full transition-[width] duration-700", row.bar)} style={{ width: `${(row.count / total) * 100}%` }} />
          </div>
          <span className="w-16 shrink-0 text-right text-muted-foreground tabular-nums">
            {row.count} <span className="text-xs">({Math.round((row.count / total) * 100)}%)</span>
          </span>
        </div>
      ))}
    </div>
  );
}
