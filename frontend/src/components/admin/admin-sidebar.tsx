"use client";

import { ArrowUpRightIcon, DatabaseIcon, LogOutIcon, MessageSquareQuoteIcon, StarIcon, WorkflowIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LangSwitch } from "@/components/admin/lang-switch";
import { LogoMark } from "@/components/logo-mark";
import FallbackAvatar from "@/components/spell/fallback-avatar";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";
import { admin, signOut, useAdminQuery, useAdminSession } from "@/lib/admin";
import { ADMIN_UI } from "@/lib/admin-i18n";
import { useUILang } from "@/lib/lang";
import { setApiMode, useApiMode } from "@/lib/mode";
import { cn } from "@/lib/utils";

export function AdminSidebar() {
  const lang = useUILang();
  const t = ADMIN_UI[lang];
  const pathname = usePathname();
  const session = useAdminSession();
  const mode = useApiMode();
  const running = useAdminQuery(`nav-running-${mode}`, () => admin.jobs("running"), () => 5000);
  const activeJobs = running.data?.length ?? 0;

  const items = [
    { href: "/admin/sources", label: t.nav.sources, icon: DatabaseIcon },
    { href: "/admin/jobs", label: t.nav.jobs, icon: WorkflowIcon, badge: activeJobs || null },
    { href: "/admin/feedback", label: t.nav.feedback, icon: StarIcon },
    { href: "/admin/suggestions", label: t.nav.suggestions, icon: MessageSquareQuoteIcon },
  ];

  return (
    <Sidebar collapsible="icon" variant="inset">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild className="h-12" size="lg">
              <Link href="/admin/sources">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-accent">
                  <LogoMark className="h-5 w-auto drop-shadow-none" />
                </span>
                <span className="flex min-w-0 flex-col leading-tight">
                  <span className="truncate font-semibold">Asistent</span>
                  <span className="truncate text-muted-foreground text-xs">{t.title}</span>
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu className="gap-1">
              {items.map((item) => (
                <SidebarMenuItem key={item.href}>
                  <SidebarMenuButton asChild isActive={pathname.startsWith(item.href)} tooltip={item.label}>
                    <Link href={item.href}>
                      <item.icon />
                      <span>{item.label}</span>
                    </Link>
                  </SidebarMenuButton>
                  {item.badge && (
                    <SidebarMenuBadge className="rounded-full bg-brand/10 text-brand tabular-nums">{item.badge}</SidebarMenuBadge>
                  )}
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter className="gap-3">
        <div className="flex flex-col gap-2 px-1 group-data-[collapsible=icon]:hidden">
          <LangSwitch />
          <div className="flex w-fit rounded-full bg-muted/80 p-0.5 text-xs">
            {(["mock", "live"] as const).map((m) => (
              <button
                className={cn(
                  "rounded-full px-3 py-0.5 font-medium transition-colors",
                  mode === m ? "bg-card text-foreground shadow-sm" : "text-foreground/50 hover:text-foreground/80",
                )}
                key={m}
                onClick={() => setApiMode(m)}
                type="button"
              >
                {m === "mock" ? "Mock" : "Live"}
              </button>
            ))}
          </div>
        </div>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild tooltip={t.openAssistant}>
              <Link href="/" target="_blank">
                <ArrowUpRightIcon />
                <span>{t.openAssistant}</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <div className="flex items-center gap-2 rounded-lg p-1.5 group-data-[collapsible=icon]:p-0">
              <FallbackAvatar className="shrink-0 rounded-full" name={session?.login ?? "admin"} size={28} />
              <span className="min-w-0 flex-1 truncate font-medium text-sm group-data-[collapsible=icon]:hidden">{session?.login}</span>
              <button
                aria-label={t.logout}
                className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-sidebar-accent hover:text-foreground group-data-[collapsible=icon]:hidden"
                onClick={signOut}
                title={t.logout}
                type="button"
              >
                <LogOutIcon className="size-4" />
              </button>
            </div>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
