import type { ReactNode } from "react";
import { SidebarTrigger } from "@/components/ui/sidebar";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="flex min-w-0 items-start gap-2">
        <SidebarTrigger className="-ml-2 mt-0.5 hidden md:inline-flex" />
        <div className="min-w-0">
          <h1 className="font-semibold text-2xl tracking-tight">{title}</h1>
          {subtitle && <p className="mt-1 text-muted-foreground text-sm">{subtitle}</p>}
        </div>
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
