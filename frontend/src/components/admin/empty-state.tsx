import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function EmptyState({ icon: Icon, title, hint, action }: { icon: LucideIcon; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-2xl border border-dashed bg-card/50 px-6 py-14 text-center">
      <div className="mb-1 flex size-10 items-center justify-center rounded-full bg-accent text-brand">
        <Icon className="size-5" />
      </div>
      <p className="font-medium text-sm">{title}</p>
      {hint && <p className="max-w-sm text-muted-foreground text-sm">{hint}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
