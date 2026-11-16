"use client";

import { CalendarClock, Pause, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { type Report, ReportForm, schedule } from "@/components/Insights";
import SortableTable, { type Column } from "@/components/SortableTable";
import { Button, EmptyState, Page, PageHeader, Panel, Skeleton, Status, useUI } from "@/components/ui";
import { api, apiJson } from "@/lib/api";
import { ago } from "@/lib/time";

export default function Reports() {
  const { confirm, toast } = useUI();
  const [reports, setReports] = useState<Report[] | null>(null);
  const [editing, setEditing] = useState<Report | "new" | null>(null);

  const load = useCallback(() => apiJson<Report[]>("/api/reports").then(setReports).catch((e) => toast(e.message, "error")), [toast]);
  useEffect(() => {
    load();
  }, [load]);

  async function call(path: string, init: RequestInit, done: string) {
    const res = await api(path, init);
    if (res.ok) toast(done);
    else toast((await res.json()).detail, "error");
    load();
  }
  async function remove(r: Report) {
    if (await confirm({ title: `Delete “${r.title}”?`, action: "Delete", danger: true, body: "It will stop running. Reports already sent are kept." }))
      call(`/api/reports/${r.id}`, { method: "DELETE" }, "Report deleted.");
  }

  const cols: Column<Report>[] = [
    { key: "title", label: "Report", sort: (r) => r.title.toLowerCase(), render: (r) => (
      <span className="block min-w-48">
        <span className="block font-medium">{r.title}</span>
        <span className="line-clamp-1 block text-xs text-muted">{r.question ?? `${r.tile_ids?.length ?? 0} dashboard charts`}</span>
      </span>
    ) },
    { key: "schedule", label: "Schedule", render: (r) => <span className="text-text2">{schedule(r)}</span> },
    { key: "recipients", label: "Recipients", render: (r) => (
      <span className="text-text2" title={r.recipient_emails.join(", ")}>{r.recipient_emails.length ? `You and ${r.recipient_emails.length} more` : "Only you"}</span>
    ) },
    { key: "status", label: "Status", sort: (r) => (r.enabled ? 1 : 0), render: (r) => !r.enabled ? <Status kind="neutral">Paused</Status>
      : r.last_status === "error" ? <Status kind="error" title={r.last_error ?? ""}>Last run had problems</Status>
      : <Status kind="success">Active</Status> },
    { key: "last", label: "Last run", sort: (r) => r.last_run_at ?? "", render: (r) => <span className="text-text2">{r.last_run_at ? ago(r.last_run_at) : "Never"}</span> },
    { key: "next", label: "Next run", sort: (r) => r.next_run_at ?? "", render: (r) => (
      <span className="text-text2">{r.enabled && r.next_run_at ? new Date(r.next_run_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "None"}</span>
    ) },
    { key: "actions", label: "", className: "w-1", render: (r) => (
      <span className="flex justify-end gap-1">
        <Button size="sm" icon={<Play className="h-3.5 w-3.5" />} onClick={() => call(`/api/reports/${r.id}/run`, { method: "POST" }, "Running now. You'll get a notification when it's ready.")}>Run now</Button>
        <Button size="sm" variant="ghost" icon={r.enabled ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
          onClick={() => call(`/api/reports/${r.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !r.enabled }) }, r.enabled ? "Report paused." : "Report resumed.")}>
          {r.enabled ? "Pause" : "Resume"}
        </Button>
        <Button size="sm" variant="ghost" icon={<Pencil className="h-3.5 w-3.5" />} onClick={() => setEditing(r)}>Edit</Button>
        <Button size="sm" variant="ghost" aria-label={`Delete ${r.title}`} title="Delete" icon={<Trash2 className="h-3.5 w-3.5" />} onClick={() => remove(r)} />
      </span>
    ) },
  ];

  return (
    <>
      <PageHeader title="Reports" description="Questions and dashboards delivered on a schedule. Each recipient's copy uses only the data they can access."
        actions={<Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setEditing("new")}>New report</Button>} />
      <Page>
        <Panel flush>
          {!reports ? <div className="p-4"><Skeleton className="h-24" /></div> : (
            <SortableTable columns={cols} rows={reports} rowKey={(r) => r.id}
              empty={<EmptyState icon={<CalendarClock className="h-6 w-6" />} title="No scheduled reports"
                body={<>Create one here, or select <b>Schedule this</b> on any answer in Ask.</>} />} />
          )}
        </Panel>
      </Page>
      {editing && <ReportForm report={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />}
    </>
  );
}
