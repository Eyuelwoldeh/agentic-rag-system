"use client";

import { Bell, ChevronDown, LogOut, Menu, PanelLeftClose, PanelLeftOpen, Plus, Search, Settings, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import CommandPalette from "@/components/CommandPalette";
import { NotificationsPanel, useNotifications } from "@/components/Notifications";
import { api } from "@/lib/api";
import { useMe } from "@/lib/me";
import { isActive, SECTIONS, SETTINGS, type NavItem } from "@/lib/nav";
import { usePref } from "@/lib/prefs";

/**
 * App shell (Carbon UI shell pattern): a global header for things that apply everywhere (search, new question,
 * notifications, account) and a left menu for moving between areas, grouped by job.
 */
export default function Shell({ children }: { children: React.ReactNode }) {
  const me = useMe();
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = usePref("nav-collapsed", false);
  const [bellOpen, setBellOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const notifications = useNotifications(Boolean(me));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!me) return <>{children}</>; // sign-in page renders without the shell
  const admin = me.role === "admin";
  const mini = collapsed && !open; // icon-only rail on desktop; the mobile drawer always shows labels
  const unread = notifications.data.unread;

  const link = ({ href, label, Icon }: NavItem) => {
    const active = isActive(href, path);
    return (
      <Link key={href} href={href} onClick={() => setOpen(false)} aria-current={active ? "page" : undefined} title={mini ? label : undefined}
        className={`relative flex h-8 items-center gap-2.5 rounded text-sm ${mini ? "justify-center px-0" : "px-2.5"} ${active ? "bg-accent-weak font-medium text-accent" : "text-text2 hover:bg-hover hover:text-foreground"}`}>
        {active && <span className="absolute top-1 bottom-1 left-0 w-[3px] rounded-r bg-accent" aria-hidden />}
        <Icon className="h-4 w-4 shrink-0" aria-hidden />
        <span className={mini ? "sr-only" : "truncate"}>{label}</span>
      </Link>
    );
  };

  const nav = (
    <nav className={`flex h-full flex-col py-3 ${mini ? "px-2" : "px-3"}`} aria-label="Main">
      <div className="flex-1 space-y-5 overflow-y-auto">
        {SECTIONS.filter((s) => admin || !s.admin).map((s) => (
          <div key={s.label ?? "main"} className="space-y-0.5">
            {s.label && (mini ? <div className="mx-2 mb-2 border-t border-line" aria-hidden /> : <p className="px-2.5 pb-1 text-xs font-medium text-muted">{s.label}</p>)}
            {s.items.map(link)}
          </div>
        ))}
      </div>
      <div className="space-y-0.5 border-t border-line pt-3">
        {link(SETTINGS)}
        <button onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? "Expand menu" : "Collapse menu"} title={collapsed ? "Expand menu" : "Collapse menu"}
          className={`hidden h-8 w-full items-center gap-2.5 rounded text-sm text-muted hover:bg-hover hover:text-foreground md:flex ${mini ? "justify-center" : "px-2.5"}`}>
          {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
          {!mini && "Collapse menu"}
        </button>
      </div>
    </nav>
  );

  const headerBtn = "grid h-8 w-8 place-items-center rounded text-shell-muted hover:bg-shell-hover hover:text-shell-fg";

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-30 flex h-12 shrink-0 items-center gap-2 bg-shell px-3 text-shell-fg">
        <button onClick={() => setOpen(true)} aria-label="Open menu" className={`${headerBtn} md:hidden`}><Menu className="h-5 w-5" /></button>
        <Link href="/home" className="flex items-center gap-2 rounded px-1.5 py-1 hover:bg-shell-hover">
          <Mark />
          <span className="hidden text-sm font-semibold tracking-[0.01em] sm:inline">Agentic RAG</span>
        </Link>
        <button onClick={() => setPaletteOpen(true)}
          className="mx-auto flex h-8 w-full max-w-md items-center gap-2 rounded border border-white/10 bg-white/[0.06] px-3 text-sm text-shell-muted hover:border-white/20 hover:bg-white/10">
          <Search className="h-4 w-4 shrink-0" aria-hidden />
          <span className="flex-1 truncate text-left">Search or jump to…</span>
          <kbd className="hidden rounded border border-white/15 px-1.5 font-sans text-[11px] sm:inline">⌘K</kbd>
        </button>
        <Link href="/" onClick={(e) => { if (path === "/") { e.preventDefault(); location.href = "/"; } }}
          className="hidden h-8 items-center gap-1.5 rounded bg-accent px-3 text-sm font-medium text-white hover:bg-accent-hover md:inline-flex">
          <Plus className="h-4 w-4" aria-hidden />New question
        </Link>
        <button onClick={() => setBellOpen(true)} aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"} title="Notifications" className={`${headerBtn} relative`}>
          <Bell className="h-[18px] w-[18px]" aria-hidden />
          {unread > 0 && (
            <span className="absolute top-0.5 right-0 grid h-4 min-w-4 place-items-center rounded-full bg-bad px-1 text-[10px] font-semibold text-white ring-2 ring-shell" aria-hidden>
              {unread > 99 ? "99+" : unread}
            </span>
          )}
        </button>
        <AccountMenu email={me.email} admin={admin} />
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className={`sticky top-12 hidden h-[calc(100dvh-3rem)] shrink-0 border-r border-line bg-nav transition-[width] md:block ${collapsed ? "w-14" : "w-56"}`}>{nav}</aside>
        {open && (
          <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
            <div className="absolute inset-0 bg-[rgb(9_30_66/0.54)]" onClick={() => setOpen(false)} />
            <aside className="absolute inset-y-0 left-0 flex w-64 flex-col border-r border-line bg-nav">
              <div className="flex h-12 items-center justify-between bg-shell px-4 text-shell-fg">
                <span className="flex items-center gap-2 text-sm font-semibold"><Mark />Agentic RAG</span>
                <button onClick={() => setOpen(false)} aria-label="Close menu" className="text-shell-muted"><X className="h-5 w-5" /></button>
              </div>
              <div className="min-h-0 flex-1">{nav}</div>
            </aside>
          </div>
        )}
        <main className="min-w-0 flex-1">{children}</main>
      </div>
      {bellOpen && <NotificationsPanel data={notifications.data} reload={notifications.reload} onClose={() => setBellOpen(false)} />}
      {paletteOpen && <CommandPalette admin={admin} onClose={() => setPaletteOpen(false)} />}
    </div>
  );
}

/** Product mark: three stacked bars (sources) feeding one answer. */
function Mark() {
  return (
    <svg viewBox="0 0 24 24" className="h-6 w-6 shrink-0" aria-hidden>
      <rect width="24" height="24" rx="5" fill="var(--accent)" />
      <rect x="5" y="6" width="8" height="2.4" rx="1.2" fill="#fff" opacity="0.65" />
      <rect x="5" y="10.8" width="11" height="2.4" rx="1.2" fill="#fff" opacity="0.8" />
      <rect x="5" y="15.6" width="14" height="2.4" rx="1.2" fill="#fff" />
    </svg>
  );
}

function AccountMenu({ email, admin }: { email: string; admin: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);

  async function signOut() {
    await api("/api/auth/logout", { method: "POST" });
    location.href = "/login";
  }

  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(!open)} aria-haspopup="menu" aria-expanded={open} aria-label="Account"
        className="flex h-8 items-center gap-1 rounded pr-1 pl-1 text-shell-muted hover:bg-shell-hover hover:text-shell-fg">
        <span className="grid h-6 w-6 place-items-center rounded-full bg-[#4c6a92] text-xs font-semibold text-white" aria-hidden>{email[0].toUpperCase()}</span>
        <ChevronDown className="h-3.5 w-3.5" aria-hidden />
      </button>
      {open && (
        <div role="menu" className="absolute right-0 mt-1.5 w-64 rounded-md border border-line bg-panel py-1 text-foreground shadow-overlay">
          <div className="border-b border-line px-3 py-2.5">
            <p className="truncate text-sm font-medium">{email}</p>
            <p className="text-xs text-muted">{admin ? "Administrator" : "Member"}</p>
          </div>
          <Link role="menuitem" href="/settings" onClick={() => setOpen(false)} className="flex items-center gap-2.5 px-3 py-2 text-sm hover:bg-hover">
            <Settings className="h-4 w-4 text-muted" aria-hidden />Settings
          </Link>
          <button role="menuitem" onClick={signOut} className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm hover:bg-hover">
            <LogOut className="h-4 w-4 text-muted" aria-hidden />Sign out
          </button>
        </div>
      )}
    </div>
  );
}
