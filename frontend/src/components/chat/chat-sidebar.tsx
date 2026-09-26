"use client";

// The chat's left menu, laid out like Gemini's: docked next to the chat on desktop (collapses to an icon rail),
// a slide-over on phones. Our logo, new chat, search over chats, our sources, and the local chat history
// (lib/chat-history.ts: this browser only, nothing is sent to the backend).

import { LibraryBigIcon, LockIcon, SearchIcon, SquarePenIcon, Trash2Icon, XIcon } from "lucide-react";
import Link from "next/link";
import { type CSSProperties, useState, useSyncExternalStore } from "react";
import { LogoMark } from "@/components/logo-mark";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInput,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarTrigger,
  useSidebar,
} from "@/components/ui/sidebar";
import { type ChatMeta, clearChats, deleteChat, useChatHistory } from "@/lib/chat-history";
import type { UIText } from "@/lib/i18n";

// ─────────────── open / collapsed, remembered in this browser ───────────────

const OPEN_KEY = "chatSidebarOpen";
const openListeners = new Set<() => void>();
const readOpen = () => {
  try {
    return window.localStorage.getItem(OPEN_KEY) !== "0";
  } catch {
    return true;
  }
};
const writeOpen = (open: boolean) => {
  try {
    window.localStorage.setItem(OPEN_KEY, open ? "1" : "0");
  } catch {
    /* storage blocked */
  }
  openListeners.forEach((l) => l());
};

/** Wraps the chat page: the menu on the left, `children` (chat + preview) take the rest. */
export function ChatSidebarProvider({ children }: { children: React.ReactNode }) {
  const open = useSyncExternalStore(
    (l) => {
      openListeners.add(l);
      return () => openListeners.delete(l);
    },
    readOpen,
    () => true,
  );
  return (
    <SidebarProvider
      className="h-full min-h-0"
      onOpenChange={writeOpen}
      open={open}
      style={{ "--sidebar-width": "17rem" } as CSSProperties}
    >
      {children}
    </SidebarProvider>
  );
}

// ─────────────── the menu ───────────────

export function ChatSidebar({
  activeId,
  onSelect,
  onNew,
  onDeleted,
  t,
}: {
  activeId: string;
  onSelect: (id: string) => void;
  onNew: () => void;
  /** a deleted chat was the one on screen (or everything was cleared) */
  onDeleted: (id: string | null) => void;
  t: UIText;
}) {
  const chats = useChatHistory();
  const { isMobile, setOpenMobile, setOpen, state } = useSidebar();
  const [searching, setSearching] = useState(false);
  const [q, setQ] = useState("");
  const [confirmClear, setConfirmClear] = useState(false);
  const h = t.history;

  const needle = q.trim().toLowerCase();
  const shown = needle ? chats.filter((c) => c.title.toLowerCase().includes(needle)) : chats;
  // phones: the menu is a slide-over, close it after picking something
  const done = () => isMobile && setOpenMobile(false);

  const startSearch = () => {
    if (state === "collapsed") setOpen(true);
    setSearching((s) => !s);
    setQ("");
  };

  return (
    <>
      <Sidebar collapsible="icon">
        <SidebarHeader className="gap-3 p-3">
          <div className="flex items-center justify-between gap-2 group-data-[collapsible=icon]:justify-center">
            <Link className="flex min-w-0 items-center gap-2.5 group-data-[collapsible=icon]:hidden" href="/" onClick={onNew}>
              <LogoMark className="h-7 w-auto shrink-0 drop-shadow-none" />
              <span className="truncate font-semibold text-[17px] tracking-tight">Asistent</span>
            </Link>
            <SidebarTrigger aria-label={h.toggle} className="shrink-0" />
          </div>
        </SidebarHeader>

        <SidebarContent>
          <SidebarGroup className="pt-0">
            <SidebarMenu className="gap-0.5">
              <SidebarMenuItem>
                <SidebarMenuButton
                  onClick={() => {
                    onNew();
                    done();
                  }}
                  tooltip={t.newChat}
                >
                  <SquarePenIcon /> <span>{t.newChat}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton isActive={searching} onClick={startSearch} tooltip={h.search}>
                  <SearchIcon /> <span>{h.search}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild tooltip={h.sources}>
                  <Link href="/sources">
                    <LibraryBigIcon /> <span>{h.sources}</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
            {searching && (
              <div className="relative mt-2 group-data-[collapsible=icon]:hidden">
                <SidebarInput
                  aria-label={h.search}
                  autoFocus
                  className="h-9 rounded-lg pr-8"
                  onChange={(e) => setQ(e.target.value)}
                  placeholder={h.searchPlaceholder}
                  value={q}
                />
                <button
                  aria-label={t.preview.close}
                  className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  onClick={() => {
                    setSearching(false);
                    setQ("");
                  }}
                  type="button"
                >
                  <XIcon className="size-4" />
                </button>
              </div>
            )}
          </SidebarGroup>

          <SidebarGroup className="min-h-0 flex-1 group-data-[collapsible=icon]:hidden">
            <SidebarGroupLabel>{h.recent}</SidebarGroupLabel>
            <SidebarGroupContent>
              {shown.length === 0 ? (
                <p className="px-2 py-3 text-muted-foreground text-sm">{needle ? h.noMatch : h.empty}</p>
              ) : (
                <SidebarMenu className="gap-0.5">
                  {shown.map((c: ChatMeta) => (
                    <SidebarMenuItem key={c.id}>
                      <SidebarMenuButton
                        className="h-9"
                        isActive={c.id === activeId}
                        onClick={() => {
                          onSelect(c.id);
                          done();
                        }}
                        title={c.title || h.untitled}
                      >
                        <span className="truncate">{c.title || h.untitled}</span>
                      </SidebarMenuButton>
                      <SidebarMenuAction
                        aria-label={h.delete}
                        className="hover:text-destructive"
                        onClick={() => {
                          deleteChat(c.id);
                          if (c.id === activeId) onDeleted(c.id);
                        }}
                        showOnHover={!isMobile}
                        title={h.delete}
                      >
                        <Trash2Icon />
                      </SidebarMenuAction>
                    </SidebarMenuItem>
                  ))}
                </SidebarMenu>
              )}
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>

        <SidebarFooter className="gap-1 border-t p-3 group-data-[collapsible=icon]:hidden">
          <p className="flex items-center gap-1.5 px-1 text-muted-foreground text-xs">
            <LockIcon className="size-3 shrink-0" /> {h.local}
          </p>
          {chats.length > 0 && (
            <button
              className="flex items-center gap-1.5 rounded-md px-1 py-1 text-left text-muted-foreground text-xs transition-colors hover:text-destructive"
              onClick={() => setConfirmClear(true)}
              type="button"
            >
              <Trash2Icon className="size-3" /> {h.clear}
            </button>
          )}
        </SidebarFooter>
      </Sidebar>

      <AlertDialog onOpenChange={setConfirmClear} open={confirmClear}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{h.clearConfirm}</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{h.cancel}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={() => {
                clearChats();
                onDeleted(null);
              }}
            >
              {h.clear}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
