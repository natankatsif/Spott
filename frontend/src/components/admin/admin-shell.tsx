"use client";

import { useEffect, type ReactNode } from "react";
import { AdminSidebar } from "@/components/admin/admin-sidebar";
import { LoginScreen } from "@/components/admin/login-screen";
import { Spinner } from "@/components/spell/spinner";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { Toaster } from "@/components/ui/sonner";
import { admin, useAdminSession, useHydrated } from "@/lib/admin";
import { useApiMode } from "@/lib/mode";

/** Login gate + sidebar layout for every /admin page. */
export function AdminShell({ children }: { children: ReactNode }) {
  const hydrated = useHydrated();
  const session = useAdminSession();
  const mode = useApiMode();

  // the stored token may have been revoked (new password, server restart with another secret): a 401 signs out
  useEffect(() => {
    if (session) void admin.me().catch(() => {});
  }, [session, mode]);

  if (!hydrated) {
    return (
      <div className="flex min-h-dvh items-center justify-center text-muted-foreground">
        <Spinner className="size-6" />
      </div>
    );
  }
  if (!session) return <LoginScreen />;

  return (
    <SidebarProvider>
      <AdminSidebar />
      <SidebarInset className="min-w-0">
        <div className="sticky top-0 z-10 flex h-12 items-center px-3 md:hidden">
          <SidebarTrigger />
        </div>
        <div className="mx-auto w-full max-w-6xl px-4 pt-4 pb-16 md:px-8 md:pt-8">{children}</div>
      </SidebarInset>
      <Toaster position="bottom-right" richColors />
    </SidebarProvider>
  );
}
