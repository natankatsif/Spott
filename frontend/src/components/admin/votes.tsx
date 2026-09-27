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
  if (isLike(rating)) return <ThumbsUpIcon className={cn("text-success", className)} size={size} />;
  if (isDislike(rating)) return <ThumbsDownIcon className={cn("text-destructive", className)} size={size} />;
  return <MinusIcon className={cn("text-muted-foreground", className)} size={size} />;
}
