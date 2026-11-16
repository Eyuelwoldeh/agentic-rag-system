"use client";

import { Bell } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Button, EmptyState, SidePanel } from "@/components/ui";
import { api, apiJson } from "@/lib/api";
import { ago } from "@/lib/time";

type Item = { id: number; kind: string; title: string; body: string | null; link: string | null; read_at: string | null; created_at: string };
type Data = { unread: number; items: Item[] };

/** Polls /api/notifications every 30 seconds while signed in. */
export function useNotifications(enabled: boolean) {
  const [data, setData] = useState<Data>({ unread: 0, items: [] });
  const reload = useCallback(() => apiJson<Data>("/api/notifications").then(setData).catch(() => {}), []);
  useEffect(() => {
    if (!enabled) return;
    reload();
    const t = setInterval(reload, 30_000);
    return () => clearInterval(t);
  }, [enabled, reload]);
  return { data, reload };
}

export function BellButton({ unread, onClick, mini, label = true }: { unread: number; onClick: () => void; mini?: boolean; label?: boolean }) {
  const name = unread ? `Notifications, ${unread} unread` : "Notifications";
  return (
    <button onClick={onClick} aria-label={name} title={mini || !label ? name : undefined}
      className={`relative flex h-8 items-center gap-2.5 rounded text-sm text-text2 hover:bg-hover hover:text-foreground ${mini || !label ? "justify-center px-1.5" : "w-full px-2.5"}`}>
      <span className="relative">
        <Bell className="h-4 w-4 shrink-0" aria-hidden />
        {unread > 0 && (mini || !label) && (
          <span className="absolute -top-1.5 -right-2 grid h-4 min-w-4 place-items-center rounded-full bg-bad px-1 text-[10px] font-semibold text-white" aria-hidden>
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </span>
      {!mini && label && <span className="flex-1 text-left">Notifications</span>}
      {!mini && label && unread > 0 && <span className="rounded-full bg-bad px-1.5 text-xs font-semibold text-white" aria-hidden>{unread > 99 ? "99+" : unread}</span>}
    </button>
  );
}

export function NotificationsPanel({ data, reload, onClose }: { data: Data; reload: () => void; onClose: () => void }) {
  const router = useRouter();
  async function open(n: Item) {
    if (!n.read_at) await api(`/api/notifications/${n.id}/read`, { method: "POST" });
    reload();
    onClose();
    if (!n.link) return;
    // same page with a different query (e.g. another conversation): reload so the page picks it up
    if (new URL(n.link, location.href).pathname === location.pathname) location.assign(n.link);
    else router.push(n.link);
  }
  async function markAll() {
    await api("/api/notifications/read", { method: "POST" });
    reload();
  }
  return (
    <SidePanel title="Notifications" description={data.unread ? `${data.unread} unread` : "You're all caught up."} onClose={onClose}
      footer={<Button onClick={markAll} disabled={!data.unread}>Mark all as read</Button>}>
      {data.items.length === 0 ? (
        <EmptyState icon={<Bell className="h-6 w-6" />} title="No notifications" body="Shares, mentions and document updates will appear here." />
      ) : (
        <ul className="-mx-5 divide-y divide-line">
          {data.items.map((n) => (
            <li key={n.id}>
              <button onClick={() => open(n)} className="flex w-full items-start gap-3 px-5 py-3 text-left hover:bg-hover">
                <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${n.read_at ? "bg-transparent" : "bg-accent"}`} aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className={`block text-sm ${n.read_at ? "text-text2" : "font-medium"}`}>{n.title}{!n.read_at && <span className="sr-only"> (unread)</span>}</span>
                  {n.body && <span className="mt-0.5 line-clamp-3 block text-xs whitespace-pre-line text-text2">{n.body}</span>}
                  <span className="mt-0.5 block text-xs text-muted">{ago(n.created_at)}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </SidePanel>
  );
}
