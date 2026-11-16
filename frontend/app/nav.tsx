"use client";

import { BarChart3, History, LayoutDashboard, LibraryBig, LogOut, Menu, MessageSquareText, PanelLeftClose, PanelLeftOpen, Plug, Settings, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { BellButton, NotificationsPanel, useNotifications } from "@/components/Notifications";
import { api } from "@/lib/api";
import { useMe } from "@/lib/me";
import { usePref } from "@/lib/prefs";

type Item = { href: string; label: string; Icon: typeof Menu };
const WORKSPACE: Item[] = [
  { href: "/", label: "Ask", Icon: MessageSquareText },
  { href: "/dashboard", label: "Dashboards", Icon: LayoutDashboard },
  { href: "/library", label: "Library", Icon: LibraryBig },
  { href: "/runs", label: "Activity", Icon: History },
];
const ADMIN: Item[] = [
  { href: "/connectors", label: "Connectors", Icon: Plug },
  { href: "/usage", label: "Usage", Icon: BarChart3 },
];

/** App shell: fixed left navigation (Carbon / Fluent pattern) with the page to the right. */
export default function Shell({ children }: { children: React.ReactNode }) {
  const me = useMe();
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = usePref("nav-collapsed", false);
  const [bellOpen, setBellOpen] = useState(false);
  const notifications = useNotifications(Boolean(me));
  if (!me) return <>{children}</>; // sign-in page renders without the shell

  async function signOut() {
    await api("/api/auth/logout", { method: "POST" });
    location.href = "/login";
  }

  const mini = collapsed && !open; // icon-only rail on desktop; the mobile drawer always shows labels

  const link = ({ href, label, Icon }: Item) => {
    const active = href === "/" ? path === "/" : path.startsWith(href);
    return (
      <Link key={href} href={href} onClick={() => setOpen(false)} aria-current={active ? "page" : undefined} title={mini ? label : undefined}
        className={`relative flex h-8 items-center gap-2.5 rounded text-sm ${mini ? "justify-center px-0" : "px-2.5"} ${active ? "bg-accent-weak font-medium text-accent" : "text-text2 hover:bg-hover hover:text-foreground"}`}>
        {active && <span className="absolute top-1 bottom-1 left-0 w-[3px] rounded-r bg-accent" aria-hidden />}
        <Icon className="h-4 w-4 shrink-0" aria-hidden />
        <span className={mini ? "sr-only" : ""}>{label}</span>
      </Link>
    );
  };

  const nav = (
    <nav className={`flex h-full flex-col gap-6 py-4 ${mini ? "px-2" : "px-3"}`} aria-label="Main">
      <Link href="/" className={`flex items-center gap-2 ${mini ? "justify-center" : "px-2.5"}`} onClick={() => setOpen(false)} title="Agentic RAG">
        <span className="grid h-6 w-6 shrink-0 place-items-center rounded bg-accent text-[11px] font-semibold text-white" aria-hidden>AR</span>
        <span className={mini ? "sr-only" : "text-sm font-semibold"}>Agentic RAG</span>
      </Link>
      <div className="space-y-0.5">
        {!mini && <p className="px-2.5 pb-1 text-xs font-medium text-muted">Workspace</p>}
        {WORKSPACE.map(link)}
      </div>
      {me.role === "admin" && (
        <div className="space-y-0.5">
          {mini ? <div className="mx-2 mb-1 border-t border-line" aria-hidden /> : <p className="px-2.5 pb-1 text-xs font-medium text-muted">Administration</p>}
          {ADMIN.map(link)}
        </div>
      )}
      <div className="mt-auto space-y-0.5 border-t border-line pt-3">
        <BellButton unread={notifications.data.unread} mini={mini} onClick={() => { setOpen(false); setBellOpen(true); }} />
        {link({ href: "/settings", label: "Settings", Icon: Settings })}
        <div className={`flex items-center gap-2 pt-2 ${mini ? "flex-col" : "px-2.5"}`}>
          <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-hover text-xs font-medium text-text2" title={mini ? me.email : undefined} aria-hidden>
            {me.email[0].toUpperCase()}
          </span>
          {!mini && (
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs font-medium">{me.email}</span>
              <span className="block text-xs text-muted">{me.role === "admin" ? "Administrator" : "Member"}</span>
            </span>
          )}
          <button onClick={signOut} title="Sign out" aria-label="Sign out" className="rounded p-1 text-muted hover:bg-hover hover:text-foreground">
            <LogOut className="h-4 w-4" />
          </button>
        </div>
        <button onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? "Expand menu" : "Collapse menu"} title={collapsed ? "Expand menu" : "Collapse menu"}
          className={`mt-1 hidden h-8 w-full items-center gap-2.5 rounded text-xs text-muted hover:bg-hover hover:text-foreground md:flex ${mini ? "justify-center" : "px-2.5"}`}>
          {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
          {!mini && "Collapse menu"}
        </button>
      </div>
    </nav>
  );

  return (
    <div className="flex min-h-dvh">
      <aside className={`sticky top-0 hidden h-dvh shrink-0 border-r border-line bg-nav transition-[width] md:block ${collapsed ? "w-14" : "w-60"}`}>{nav}</aside>
      {open && (
        <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-[rgb(9_30_66/0.54)]" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 border-r border-line bg-nav">
            <button onClick={() => setOpen(false)} aria-label="Close menu" className="absolute top-4 right-3 text-muted"><X className="h-5 w-5" /></button>
            {nav}
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-12 items-center gap-3 border-b border-line bg-panel px-4 md:hidden">
          <button onClick={() => setOpen(true)} aria-label="Open menu" className="text-text2"><Menu className="h-5 w-5" /></button>
          <span className="flex-1 text-sm font-semibold">Agentic RAG</span>
          <BellButton unread={notifications.data.unread} label={false} onClick={() => setBellOpen(true)} />
        </div>
        <main className="min-w-0 flex-1">{children}</main>
      </div>
      {bellOpen && <NotificationsPanel data={notifications.data} reload={notifications.reload} onClose={() => setBellOpen(false)} />}
    </div>
  );
}
