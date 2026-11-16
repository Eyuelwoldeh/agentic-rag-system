"use client";

import { BellRing, History, Pause, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { type Catalog, PeoplePicker } from "@/components/Insights";
import SortableTable, { type Column } from "@/components/SortableTable";
import { Button, EmptyState, Field, inputCls, Page, PageHeader, Panel, SidePanel, Skeleton, Status, useUI } from "@/components/ui";
import { api, apiJson } from "@/lib/api";
import { ago } from "@/lib/time";

type Condition = "drop_vs_last_year" | "above" | "below";
type Alert = { id: number; name: string; metric_id: number | null; metric_name: string | null; source: string | null; table_name: string | null;
  expression: string | null; time_column: string | null; condition: Condition; threshold: number; group_by: string | null;
  cadence: "hourly" | "daily"; recipients: number[]; recipient_emails: string[]; enabled: boolean; last_state: "ok" | "triggered" | "error" | null;
  last_checked_at: string | null; last_triggered_at: string | null };
type Check = { triggered: boolean; message: string; rows: { group: string; value: number; baseline?: number; change_pct?: number | null; hit: boolean }[] };
type Event = { id: number; at: string; state: string; message: string };
type MetricRef = { id: number; name: string; source: string; table_name: string; time_column: string | null };

const describe = (a: Pick<Alert, "condition" | "threshold">) =>
  a.condition === "drop_vs_last_year" ? `Drops more than ${a.threshold}% vs last year` : `${a.condition === "above" ? "Above" : "Below"} ${a.threshold.toLocaleString()}`;

function StateBadge({ a }: { a: Alert }) {
  if (!a.enabled) return <Status kind="neutral">Paused</Status>;
  if (a.last_state === "triggered") return <Status kind="warning">Triggered</Status>;
  if (a.last_state === "error") return <Status kind="error">Check failed</Status>;
  if (a.last_state === "ok") return <Status kind="success">Normal</Status>;
  return <Status kind="info">Not checked yet</Status>;
}

export default function Alerts() {
  const { confirm, toast } = useUI();
  const [alerts, setAlerts] = useState<Alert[] | null>(null);
  const [editing, setEditing] = useState<Alert | "new" | null>(null);
  const [checking, setChecking] = useState<{ a: Alert; result?: Check; error?: string } | null>(null);
  const [history, setHistory] = useState<{ a: Alert; events?: Event[] } | null>(null);

  const load = useCallback(() => apiJson<Alert[]>("/api/alerts").then(setAlerts).catch((e) => toast(e.message, "error")), [toast]);
  useEffect(() => {
    load();
  }, [load]);

  async function test(a: Alert) {
    setChecking({ a });
    try {
      setChecking({ a, result: await apiJson<Check>(`/api/alerts/${a.id}/test`, { method: "POST" }) });
    } catch (e) {
      setChecking({ a, error: (e as Error).message });
    }
  }
  async function showHistory(a: Alert) {
    setHistory({ a });
    setHistory({ a, events: await apiJson<Event[]>(`/api/alerts/${a.id}/events`).catch(() => []) });
  }
  async function call(path: string, init: RequestInit, done: string) {
    const res = await api(path, init);
    if (res.ok) toast(done);
    else toast((await res.json()).detail, "error");
    load();
  }
  async function remove(a: Alert) {
    if (await confirm({ title: `Delete “${a.name}”?`, action: "Delete", danger: true, body: "Its trigger history is deleted too." }))
      call(`/api/alerts/${a.id}`, { method: "DELETE" }, "Alert deleted.");
  }

  const cols: Column<Alert>[] = [
    { key: "name", label: "Alert", sort: (a) => a.name.toLowerCase(), render: (a) => (
      <span className="block min-w-48">
        <span className="block font-medium">{a.name}</span>
        <span className="block text-xs text-muted">{a.metric_name ?? `${a.expression} from ${a.table_name}`}{a.group_by ? `, per ${a.group_by}` : ""}</span>
      </span>
    ) },
    { key: "condition", label: "Condition", render: (a) => <span className="text-text2">{describe(a)}</span> },
    { key: "cadence", label: "Checked", sort: (a) => a.cadence, render: (a) => <span className="text-text2">{a.cadence === "hourly" ? "Hourly" : "Daily"}</span> },
    { key: "state", label: "Status", render: (a) => <StateBadge a={a} /> },
    { key: "last", label: "Last triggered", sort: (a) => a.last_triggered_at ?? "", render: (a) => <span className="text-text2">{a.last_triggered_at ? ago(a.last_triggered_at) : "Never"}</span> },
    { key: "actions", label: "", className: "w-1", render: (a) => (
      <span className="flex justify-end gap-1">
        <Button size="sm" icon={<Play className="h-3.5 w-3.5" />} onClick={() => test(a)} title="Check now without notifying anyone">Test now</Button>
        <Button size="sm" variant="ghost" icon={<History className="h-3.5 w-3.5" />} onClick={() => showHistory(a)}>History</Button>
        <Button size="sm" variant="ghost" icon={a.enabled ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
          onClick={() => call(`/api/alerts/${a.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !a.enabled }) }, a.enabled ? "Alert paused." : "Alert resumed.")}>
          {a.enabled ? "Pause" : "Resume"}
        </Button>
        <Button size="sm" variant="ghost" icon={<Pencil className="h-3.5 w-3.5" />} onClick={() => setEditing(a)}>Edit</Button>
        <Button size="sm" variant="ghost" aria-label={`Delete ${a.name}`} title="Delete" icon={<Trash2 className="h-3.5 w-3.5" />} onClick={() => remove(a)} />
      </span>
    ) },
  ];

  return (
    <>
      <PageHeader title="Alerts" description="Get notified when a number crosses a line, such as sales falling behind last year. You're told once when it starts, not on every check."
        actions={<Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setEditing("new")}>New alert</Button>} />
      <Page>
        <Panel flush>
          {!alerts ? <div className="p-4"><Skeleton className="h-24" /></div> : (
            <SortableTable columns={cols} rows={alerts} rowKey={(a) => a.id}
              empty={<EmptyState icon={<BellRing className="h-6 w-6" />} title="No alerts yet" body="Watch a metric, such as revenue per location, and get notified when it changes." />} />
          )}
        </Panel>
      </Page>
      {editing && <AlertForm alert={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />}
      {checking && (
        <SidePanel title={`Check: ${checking.a.name}`} description={describe(checking.a)} onClose={() => setChecking(null)}>
          {checking.error ? <Status kind="error">{checking.error}</Status> : !checking.result ? <Skeleton className="h-24" /> : (
            <div className="space-y-3">
              <div className="flex items-start gap-2 text-sm">
                {checking.result.triggered ? <Status kind="warning">Would trigger</Status> : <Status kind="success">Normal</Status>}
                <span>{checking.result.message}</span>
              </div>
              <SortableTable rowKey={(r) => r.group} rows={checking.result.rows} columns={[
                { key: "group", label: "Group", render: (r) => r.group },
                { key: "value", label: "Current", num: true, render: (r) => r.value.toLocaleString(undefined, { maximumFractionDigits: 2 }) },
                ...(checking.a.condition === "drop_vs_last_year" ? [
                  { key: "baseline", label: "Last year", num: true, render: (r: Check["rows"][number]) => (r.baseline ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 }) },
                  { key: "change", label: "Change", num: true, render: (r: Check["rows"][number]) => r.change_pct == null ? "No data" : `${r.change_pct > 0 ? "+" : ""}${r.change_pct}%` },
                ] : []),
                { key: "hit", label: "Condition", render: (r) => r.hit ? <Status kind="warning">Met</Status> : <Status kind="neutral">Not met</Status> },
              ]} empty={<p className="text-sm text-muted">No data for this period.</p>} />
            </div>
          )}
        </SidePanel>
      )}
      {history && (
        <SidePanel title={`History: ${history.a.name}`} onClose={() => setHistory(null)}>
          {!history.events ? <Skeleton className="h-24" /> : history.events.length === 0 ? <p className="text-sm text-muted">No changes yet.</p> : (
            <ol className="space-y-3">
              {history.events.map((e) => (
                <li key={e.id} className="text-sm">
                  <div className="flex items-center gap-2">
                    {e.state === "triggered" ? <Status kind="warning">Triggered</Status> : e.state === "error" ? <Status kind="error">Check failed</Status> : <Status kind="success">Back to normal</Status>}
                    <span className="text-xs text-muted">{new Date(e.at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</span>
                  </div>
                  <p className="mt-1 text-text2">{e.message}</p>
                </li>
              ))}
            </ol>
          )}
        </SidePanel>
      )}
    </>
  );
}

function AlertForm({ alert, onClose, onSaved }: { alert: Alert | null; onClose: () => void; onSaved: () => void }) {
  const { toast } = useUI();
  const [metrics, setMetrics] = useState<MetricRef[]>([]);
  const [catalog, setCatalog] = useState<Catalog>([]);
  const [f, setF] = useState({
    name: alert?.name ?? "", mode: alert && !alert.metric_id ? "custom" : "metric", metric_id: alert?.metric_id ?? 0,
    source: alert?.source ?? "uploads", table_name: alert?.table_name ?? "", expression: alert?.expression ?? "", time_column: alert?.time_column ?? "",
    condition: alert?.condition ?? ("drop_vs_last_year" as Condition), threshold: String(alert?.threshold ?? 20), group_by: alert?.group_by ?? "",
    cadence: alert?.cadence ?? "daily", recipients: alert?.recipients ?? [],
  });
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    apiJson<MetricRef[]>("/api/metrics").then((m) => {
      setMetrics(m);
      setF((x) => (x.metric_id || !m.length ? x : { ...x, metric_id: m[0].id }));
    }).catch(() => {});
    apiJson<Catalog>("/api/metrics/catalog").then(setCatalog).catch(() => {});
  }, []);

  const metric = metrics.find((m) => m.id === f.metric_id);
  const src = f.mode === "metric" ? metric?.source : f.source;
  const table = f.mode === "metric" ? metric?.table_name : f.table_name;
  const columns = catalog.find((s) => s.id === src)?.tables[table ?? ""] ?? [];
  const tables = catalog.find((s) => s.id === f.source)?.tables ?? {};

  async function save() {
    setSaving(true);
    setError("");
    const body = { name: f.name, condition: f.condition, threshold: Number(f.threshold), group_by: f.group_by || null, cadence: f.cadence,
      recipients: f.recipients, enabled: alert?.enabled ?? true,
      ...(f.mode === "metric" ? { metric_id: f.metric_id } : { source: f.source, table_name: f.table_name, expression: f.expression, time_column: f.time_column || null }) };
    const res = await api(alert ? `/api/alerts/${alert.id}` : "/api/alerts", { method: alert ? "PUT" : "POST", body: JSON.stringify(body) });
    setSaving(false);
    if (res.ok) {
      toast(alert ? "Alert updated." : "Alert created.");
      onSaved();
    } else setError((await res.json()).detail ?? "Couldn't save the alert.");
  }

  const ready = f.name.trim() && f.threshold !== "" && !isNaN(Number(f.threshold)) && (f.mode === "metric" ? f.metric_id : f.table_name && f.expression);
  return (
    <SidePanel title={alert ? "Edit alert" : "New alert"} onClose={onClose} description="Checked with your access. Recipients are only told if they can see the data too."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={saving} disabled={!ready} onClick={save}>Save</Button></>}>
      <div className="space-y-4">
        {error && <p className="rounded border border-bad bg-bad-weak px-3 py-2 text-sm text-bad">{error}</p>}
        <Field label="Name" required><input className={inputCls} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Sales behind last year" /></Field>
        <fieldset>
          <legend className="mb-1 text-xs font-medium text-text2">What to watch</legend>
          {(["metric", "custom"] as const).map((k) => (
            <label key={k} className="mr-4 inline-flex items-center gap-2 text-sm">
              <input type="radio" name="mode" checked={f.mode === k} onChange={() => setF({ ...f, mode: k })} />
              {k === "metric" ? "A defined metric" : "A custom calculation"}
            </label>
          ))}
        </fieldset>
        {f.mode === "metric" ? (
          <Field label="Metric" required helper={metrics.length ? undefined : "No metrics are defined yet. An administrator can add them on the Metrics page."}>
            <select className={inputCls} value={f.metric_id} onChange={(e) => setF({ ...f, metric_id: +e.target.value, group_by: "" })}>
              {metrics.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </Field>
        ) : (
          <>
            <Field label="Data source">
              <select className={inputCls} value={f.source} onChange={(e) => setF({ ...f, source: e.target.value, table_name: "", time_column: "", group_by: "" })}>
                {catalog.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </Field>
            <Field label="Table" required>
              <select className={inputCls} value={f.table_name} onChange={(e) => setF({ ...f, table_name: e.target.value, time_column: "", group_by: "" })}>
                <option value="">Choose a table</option>
                {Object.keys(tables).map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </Field>
            <Field label="Expression" required helper={<>For example <code>sum(revenue)</code>.</>}>
              <input className={`${inputCls} font-mono`} value={f.expression} onChange={(e) => setF({ ...f, expression: e.target.value })} />
            </Field>
            <Field label="Time column" helper="Needed to compare with last year, or to look at the current month only.">
              <select className={inputCls} value={f.time_column} onChange={(e) => setF({ ...f, time_column: e.target.value })}>
                <option value="">None</option>
                {columns.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </Field>
          </>
        )}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Condition">
            <select className={inputCls} value={f.condition} onChange={(e) => setF({ ...f, condition: e.target.value as Condition })}>
              <option value="drop_vs_last_year">Drops vs last year</option>
              <option value="above">Goes above</option>
              <option value="below">Goes below</option>
            </select>
          </Field>
          <Field label={f.condition === "drop_vs_last_year" ? "By more than (%)" : "Value"} required>
            <input type="number" className={inputCls} value={f.threshold} onChange={(e) => setF({ ...f, threshold: e.target.value })} />
          </Field>
        </div>
        <p className="text-xs text-muted">
          {f.condition === "drop_vs_last_year" ? "Compares this month so far with the same days last year." : "Uses this month so far when there is a time column, otherwise all rows."}
        </p>
        <Field label="Check separately for each" helper="Optional, for example each location.">
          <select className={inputCls} value={f.group_by} onChange={(e) => setF({ ...f, group_by: e.target.value })}>
            <option value="">Overall total only</option>
            {columns.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </Field>
        <Field label="Check">
          <select className={inputCls} value={f.cadence} onChange={(e) => setF({ ...f, cadence: e.target.value as Alert["cadence"] })}>
            <option value="hourly">Hourly</option><option value="daily">Daily</option>
          </select>
        </Field>
        <Field label="Also notify" helper="You're always notified about your own alerts.">
          <PeoplePicker value={f.recipients} onChange={(recipients) => setF({ ...f, recipients })} />
        </Field>
      </div>
    </SidePanel>
  );
}
