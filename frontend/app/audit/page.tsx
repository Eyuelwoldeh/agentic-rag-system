"use client";

import { ChevronLeft, ChevronRight, Download, ScrollText } from "lucide-react";
import { useEffect, useState } from "react";
import SortableTable, { type Column } from "@/components/SortableTable";
import { Button, EmptyState, Field, inputCls, Page, PageHeader, Panel, Skeleton, Status, useUI } from "@/components/ui";
import { API, apiJson } from "@/lib/api";

type Entry = { id: number; at: string; actor_email: string | null; action: string; target: string | null; detail: Record<string, unknown> | null; ip: string | null };
type Result = { total: number; page: number; size: number; actions: string[]; items: Entry[] };

const LABELS: Record<string, string> = {
  "user.login": "Signed in", "user.login_failed": "Sign-in failed", "user.logout": "Signed out", "user.sso_login": "Signed in with SSO",
  "user.sso_login_failed": "SSO sign-in failed", "user.create": "Person added", "user.delete": "Person removed", "user.role_change": "Role changed",
  "group.create": "Group created", "group.delete": "Group deleted", "group.member_add": "Added to group", "group.member_remove": "Removed from group",
  "connection.create": "Connection added", "connection.delete": "Connection removed", "connection.access_change": "Connection access changed",
  "document.upload": "Document uploaded", "document.replace": "Document replaced", "document.delete": "Document deleted",
  "spreadsheet.upload": "Spreadsheet uploaded", "spreadsheet.replace": "Spreadsheet replaced", "spreadsheet.delete": "Spreadsheet deleted",
  "ai.settings_change": "AI settings changed", "oauth_app.save": "Sign-in app saved", "oauth_app.delete": "Sign-in app removed",
  "sso.settings_change": "SSO settings changed", "scim.token_create": "Provisioning token created", "scim.token_revoke": "Provisioning token revoked",
  "scim.user_create": "Person provisioned", "scim.user_update": "Person updated by provisioning", "scim.user_deactivate": "Person deactivated",
  "scim.user_activate": "Person reactivated", "scim.user_delete": "Person deprovisioned", "scim.group_create": "Group provisioned",
  "scim.group_update": "Group updated by provisioning", "scim.group_delete": "Group deprovisioned",
  "dashboard.share": "Dashboard tile shared", "dashboard.unshare": "Dashboard tile unshared",
};
const label = (a: string) => LABELS[a] ?? a;
const failed = (a: string) => a.endsWith("_failed");

export default function AuditLog() {
  const { toast } = useUI();
  const [filters, setFilters] = useState({ action: "", person: "", start: "", end: "" });
  const [page, setPage] = useState(1);
  const [r, setR] = useState<Result | null>(null);
  const query = new URLSearchParams(Object.entries(filters).filter(([, v]) => v)).toString();

  useEffect(() => {
    const t = setTimeout(() => {
      apiJson<Result>(`/api/audit?${query}${query ? "&" : ""}page=${page}&size=50`).then(setR).catch((e) => toast(e.message, "error"));
    }, 250); // debounce typing in the person filter
    return () => clearTimeout(t);
  }, [query, page, toast]);

  const set = (k: keyof typeof filters) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setFilters({ ...filters, [k]: e.target.value });
    setPage(1);
  };
  const pages = r ? Math.max(1, Math.ceil(r.total / r.size)) : 1;

  const cols: Column<Entry>[] = [
    { key: "at", label: "Time", render: (e) => <span className="whitespace-nowrap text-text2">{new Date(e.at).toLocaleString()}</span> },
    { key: "who", label: "Person", render: (e) => e.actor_email === "scim" ? <span className="text-text2">Identity provider</span> : e.actor_email ?? <span className="text-muted">Unknown</span> },
    { key: "action", label: "Action", render: (e) => failed(e.action) ? <Status kind="error">{label(e.action)}</Status> : label(e.action) },
    { key: "target", label: "Target", render: (e) => <span className="font-mono text-xs text-text2">{e.target}</span> },
    { key: "detail", label: "Details", render: (e) => e.detail && (
      <span className="block max-w-md truncate text-xs text-text2" title={JSON.stringify(e.detail, null, 1)}>
        {Object.entries(e.detail).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ")}
      </span>
    ) },
    { key: "ip", label: "IP address", render: (e) => <span className="font-mono text-xs text-text2">{e.ip}</span> },
  ];

  return (
    <>
      <PageHeader title="Audit log" description="Sign-ins, changes to people, groups, access and settings, and file activity. Entries can't be edited or deleted."
        actions={<Button icon={<Download className="h-4 w-4" />} onClick={() => { location.href = `${API}/api/audit/export?${query}`; }}>Export CSV</Button>} />
      <Page wide>
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-56">
            <Field label="Action">
              <select value={filters.action} onChange={set("action")} className={inputCls}>
                <option value="">All actions</option>
                {(r?.actions ?? []).map((a) => <option key={a} value={a}>{label(a)}</option>)}
              </select>
            </Field>
          </div>
          <div className="w-64"><Field label="Person"><input value={filters.person} onChange={set("person")} placeholder="Email contains" className={inputCls} /></Field></div>
          <div className="w-40"><Field label="From"><input type="date" value={filters.start} onChange={set("start")} className={inputCls} /></Field></div>
          <div className="w-40"><Field label="To"><input type="date" value={filters.end} onChange={set("end")} className={inputCls} /></Field></div>
          {query && <Button variant="ghost" onClick={() => { setFilters({ action: "", person: "", start: "", end: "" }); setPage(1); }}>Clear filters</Button>}
        </div>
        <Panel flush title={r ? `${r.total.toLocaleString()} ${r.total === 1 ? "entry" : "entries"}` : "Entries"}
          actions={r && r.total > r.size && (
            <div className="flex items-center gap-2 text-xs text-text2">
              Page {page} of {pages}
              <Button size="sm" variant="ghost" aria-label="Previous page" disabled={page <= 1} onClick={() => setPage(page - 1)} icon={<ChevronLeft className="h-4 w-4" />} />
              <Button size="sm" variant="ghost" aria-label="Next page" disabled={page >= pages} onClick={() => setPage(page + 1)} icon={<ChevronRight className="h-4 w-4" />} />
            </div>
          )}>
          {!r ? <div className="space-y-2 p-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-8" />)}</div> : (
            <SortableTable columns={cols} rows={r.items} rowKey={(e) => e.id}
              empty={<EmptyState icon={<ScrollText className="h-6 w-6" />} title="No matching entries" body={query ? "Try different filters." : "Activity will appear here as people sign in and make changes."} />} />
          )}
        </Panel>
      </Page>
    </>
  );
}
