"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { api } from "@/lib/api";
import { useMe } from "@/lib/me";

export default function Nav() {
  const me = useMe();
  const path = usePathname();
  if (!me) return null;
  const admin = me.role === "admin";
  const links = [["/", "Chat"], ["/dashboard", "Dashboard"], ...(admin ? [["/connectors", "Connectors"], ["/usage", "Usage"]] : []),
    ["/runs", "Activity"], ["/settings", "Settings"]];

  async function signOut() {
    await api("/api/auth/logout", { method: "POST" });
    location.href = "/login";
  }

  return (
    <header className="flex h-12 items-center gap-6 border-b border-line px-4 text-sm">
      <span className="font-semibold tracking-tight">Agentic RAG</span>
      <nav className="flex gap-4 overflow-x-auto text-muted">
        {links.map(([href, label]) => (
          <Link key={href} href={href} className={path === href ? "text-foreground" : "hover:text-foreground"}>{label}</Link>
        ))}
      </nav>
      <span className="ml-auto flex items-center gap-3 text-xs text-muted">
        <span className="hidden sm:inline">{me.email}{me.role === "admin" && " · admin"}</span>
        <button onClick={signOut} className="hover:text-foreground">Sign out</button>
      </span>
    </header>
  );
}
