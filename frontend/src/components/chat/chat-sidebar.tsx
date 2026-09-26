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
import { cn } from "@/lib/utils";
import type { UIText } from "@/lib/i18n";

// ─────────────── open / collapsed, remembered in this browser ───────────────

const OPEN_KEY = "chatSidebarOpen";
const openListeners = new Set<() => void>();
const readOpen = () => {
  try {
    return window.localStorage.getItem(OPEN_KEY) === "1"; // collapsed until the user opens it
  } catch {
    return false;
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
    () => false,
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
  // phones: bigger text and taller rows, like Gemini's menu
  const navClass = isMobile ? "h-12 gap-3 rounded-xl px-3 text-base [&>svg]:size-5" : undefined;
  // phones: the menu is a slide-over, close it after picking something
  const done = () => isMobile && setOpenMobile(false);

  const startSearch = () => {
    if (state === "collapsed") setOpen(true);
    setSearching((s) => !s);
    setQ("");
  };

  return (
    <>
      {/* phones: a wide slide-over like Gemini's, the chat stays visible (no dimming; a tap on it closes the menu) */}
      <Sidebar
        collapsible="icon"
        mobileClassName="w-[85vw] max-w-[22rem] border-r-0 shadow-2xl sm:max-w-[22rem]"
        mobileOverlayClassName="bg-transparent"
      >
        <SidebarHeader className="gap-3 p-3">
          <div className="flex items-center justify-between gap-2 group-data-[collapsible=icon]:justify-center">
            <Link className="flex min-w-0 items-center gap-2.5 group-data-[collapsible=icon]:hidden" href="/" onClick={onNew}>
              <LogoMark className={cn("w-auto shrink-0 drop-shadow-none", isMobile ? "h-8" : "h-7")} />
              <span className={cn("truncate font-semibold tracking-tight", isMobile ? "text-xl" : "text-[17px]")}>Spott</span>
            </Link>
            {isMobile ? (
              <button
                aria-label={t.preview.close}
                className="flex size-10 shrink-0 items-center justify-center rounded-full text-foreground/80 hover:bg-sidebar-accent"
                onClick={() => setOpenMobile(false)}
                type="button"
              >
                <XIcon className="size-6" />
              </button>
            ) : (
              <SidebarTrigger aria-label={h.toggle} className="shrink-0" />
            )}
          </div>
        </SidebarHeader>

        <SidebarContent>
          <SidebarGroup className="pt-0">
            <SidebarMenu className="gap-0.5">
              <SidebarMenuItem>
                <SidebarMenuButton
                  className={navClass}
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
                <SidebarMenuButton className={navClass} isActive={searching} onClick={startSearch} tooltip={h.search}>
                  <SearchIcon /> <span>{h.search}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton asChild className={navClass} tooltip={h.sources}>
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
                  className={cn("rounded-lg pr-8", isMobile ? "h-11 text-base" : "h-9")}
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
            <SidebarGroupLabel className={cn(isMobile && "h-10 px-3 text-sm")}>{h.recent}</SidebarGroupLabel>
            <SidebarGroupContent>
              {shown.length === 0 ? (
                <p className={cn("py-3 text-muted-foreground", isMobile ? "px-3 text-base" : "px-2 text-sm")}>{needle ? h.noMatch : h.empty}</p>
              ) : (
                <SidebarMenu className="gap-0.5">
                  {shown.map((c: ChatMeta) => (
                    <SidebarMenuItem key={c.id}>
                      <SidebarMenuButton
                        className={cn(isMobile ? "h-12 rounded-xl px-3 pr-12 text-base" : "h-9")}
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
                        className={cn("hover:text-destructive", isMobile && "top-1/2! right-2 size-9 -translate-y-1/2 [&>svg]:size-[18px]")}
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
          <p className={cn("flex items-center gap-1.5 px-1 text-muted-foreground", isMobile ? "text-sm" : "text-xs")}>
            <LockIcon className="size-3 shrink-0" /> {h.local}
          </p>
          {chats.length > 0 && (
            <button
              className={cn(
                "flex items-center gap-1.5 rounded-md px-1 py-1 text-left text-muted-foreground transition-colors hover:text-destructive",
                isMobile ? "text-sm" : "text-xs",
              )}
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
