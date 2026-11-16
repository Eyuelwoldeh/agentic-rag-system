"use client";

import { Pencil, Play, Plus, Sigma, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import SortableTable, { type Column } from "@/components/SortableTable";
import { Button, EmptyState, Field, inputCls, Page, PageHeader, Panel, SidePanel, Skeleton, useUI } from "@/components/ui";
import { api, apiJson } from "@/lib/api";
import { type Catalog } from "@/components/Insights";
import { useMe } from "@/lib/me";
import { ago } from "@/lib/time";

type Metric = { id: number; name: string; description: string; source: string; table_name: string; expression: string;
  time_column: string | null; filters: string | null; format: "currency" | "number" | "percent"; updated_at: string; owner: string | null };


const FORMAT = { currency: "Currency", number: "Number", percent: "Percent" };

function fmtValue(v: unknown, format: Metric["format"]) {
  if (typeof v !== "number") return v == null ? "No data" : String(v);
  if (format === "currency") return v.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  if (format === "percent") return `${v.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export default function Metrics() {
  const me = useMe()!;
  const { confirm, toast } = useUI();
  const [metrics, setMetrics] = useState<Metric[] | null>(null);
  const [editing, setEditing] = useState<Metric | "new" | null>(null);
  const [values, setValues] = useState<Record<number, { value?: unknown; error?: string; loading?: boolean }>>({});
  const admin = me.role === "admin";

  const load = useCallback(() => apiJson<Metric[]>("/api/metrics").then(setMetrics).catch((e) => toast(e.message, "error")), [toast]);
  useEffect(() => {
    load();
  }, [load]);

  async function test(m: Metric) {
    setValues((v) => ({ ...v, [m.id]: { loading: true } }));
    try {
      const r = await apiJson<{ value: unknown }>(`/api/metrics/${m.id}/test`, { method: "POST" });
      setValues((v) => ({ ...v, [m.id]: { value: r.value } }));
    } catch (e) {
      setValues((v) => ({ ...v, [m.id]: { error: (e as Error).message } }));
    }
  }
  async function remove(m: Metric) {
    if (!(await confirm({ title: `Delete “${m.name}”?`, action: "Delete", danger: true, body: "Alerts that watch this metric are deleted too." }))) return;
    const res = await api(`/api/metrics/${m.id}`, { method: "DELETE" });
    if (res.ok) toast("Metric deleted.");
    else toast((await res.json()).detail, "error");
    load();
  }

  const cols: Column<Metric>[] = [
    { key: "name", label: "Name", sort: (m) => m.name.toLowerCase(), render: (m) => (
      <span className="block min-w-48">
        <span className="block font-medium">{m.name}</span>
        {m.description && <span className="block text-xs text-muted">{m.description}</span>}
      </span>
    ) },
    { key: "definition", label: "Definition", render: (m) => (
      <span className="block font-mono text-xs text-text2">
        {m.expression}
        <span className="block text-muted">from {m.table_name}{m.filters ? ` where ${m.filters}` : ""}</span>
      </span>
    ) },
    { key: "format", label: "Format", sort: (m) => m.format, render: (m) => FORMAT[m.format] },
    { key: "updated", label: "Updated", sort: (m) => m.updated_at, render: (m) => <span className="text-text2">{ago(m.updated_at)}</span> },
    { key: "value", label: "Current value", num: true, render: (m) => {
      const v = values[m.id];
      if (!v) return <span className="text-muted">Not tested</span>;
      if (v.loading) return <span className="text-muted">Calculating…</span>;
      if (v.error) return <span className="text-xs text-bad" title={v.error}>Couldn&apos;t calculate</span>;
      return <span className="font-medium tabular-nums">{fmtValue(v.value, m.format)}</span>;
    } },
    { key: "actions", label: "", className: "w-1", render: (m) => (
      <span className="flex justify-end gap-1">
        <Button size="sm" icon={<Play className="h-3.5 w-3.5" />} onClick={() => test(m)} title="Calculate the current value with your access">Test</Button>
        {admin && <>
          <Button size="sm" variant="ghost" icon={<Pencil className="h-3.5 w-3.5" />} onClick={() => setEditing(m)}>Edit</Button>
          <Button size="sm" variant="ghost" aria-label={`Delete ${m.name}`} title="Delete" icon={<Trash2 className="h-3.5 w-3.5" />} onClick={() => remove(m)} />
        </>}
      </span>
    ) },
  ];

  return (
    <>
      <PageHeader title="Metrics" description="Official definitions of key numbers. The assistant, alerts and reports use them, so everyone gets the same answer."
        actions={admin && <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setEditing("new")}>New metric</Button>} />
      <Page>
        <Panel flush>
          {!metrics ? <div className="p-4"><Skeleton className="h-24" /></div> : (
            <SortableTable columns={cols} rows={metrics} rowKey={(m) => m.id} initialSort={{ key: "name", dir: 1 }}
              empty={<EmptyState icon={<Sigma className="h-6 w-6" />} title="No metrics defined yet"
                body={admin ? "Define revenue, margin or any other key number once, and the assistant will use that definition." : "An administrator can define the company's key metrics here."} />} />
          )}
        </Panel>
      </Page>
      {editing && <MetricForm metric={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />}
    </>
  );
}

function MetricForm({ metric, onClose, onSaved }: { metric: Metric | null; onClose: () => void; onSaved: () => void }) {
  const { toast } = useUI();
  const [catalog, setCatalog] = useState<Catalog>([]);
  const [f, setF] = useState({ name: metric?.name ?? "", description: metric?.description ?? "", source: metric?.source ?? "uploads",
    table_name: metric?.table_name ?? "", expression: metric?.expression ?? "", time_column: metric?.time_column ?? "",
    filters: metric?.filters ?? "", format: metric?.format ?? "number" });
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    apiJson<Catalog>("/api/metrics/catalog").then(setCatalog).catch(() => {});
  }, []);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const tables = catalog.find((s) => s.id === f.source)?.tables ?? {};
  const columns = tables[f.table_name] ?? [];

  async function save() {
    setSaving(true);
    setError("");
    const res = await api(metric ? `/api/metrics/${metric.id}` : "/api/metrics", { method: metric ? "PUT" : "POST",
      body: JSON.stringify({ ...f, time_column: f.time_column || null, filters: f.filters || null }) });
    setSaving(false);
    if (res.ok) {
      toast(metric ? "Metric updated." : "Metric created.");
      onSaved();
    } else setError((await res.json()).detail ?? "Couldn't save the metric.");
  }

  return (
    <SidePanel title={metric ? "Edit metric" : "New metric"} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={saving} onClick={save} disabled={!f.name || !f.table_name || !f.expression}>Save</Button></>}>
      <div className="space-y-4">
        {error && <p className="rounded border border-bad bg-bad-weak px-3 py-2 text-sm text-bad">{error}</p>}
        <Field label="Name" required><input className={inputCls} value={f.name} onChange={set("name")} placeholder="Gross margin" /></Field>
        <Field label="Description"><input className={inputCls} value={f.description} onChange={set("description")} placeholder="Revenue minus cost of goods sold" /></Field>
        <Field label="Data source" required>
          <select className={inputCls} value={f.source} onChange={(e) => setF({ ...f, source: e.target.value, table_name: "", time_column: "" })}>
            {catalog.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </Field>
        <Field label="Table" required>
          <select className={inputCls} value={f.table_name} onChange={(e) => setF({ ...f, table_name: e.target.value, time_column: "" })}>
            <option value="">Choose a table</option>
            {Object.keys(tables).map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </Field>
        <Field label="Expression" required helper={<>An aggregate of this table&apos;s columns, such as <code>sum(revenue) - sum(cost_of_goods)</code>. Allowed: sum, avg, count, min, max, round, coalesce, nullif and arithmetic.</>}>
          <input className={`${inputCls} font-mono`} value={f.expression} onChange={set("expression")} />
        </Field>
        {columns.length > 0 && <p className="text-xs text-muted">Columns: {columns.join(", ")}</p>}
        <Field label="Time column" helper="Lets the metric be broken down by day, week, month, quarter or year.">
          <select className={inputCls} value={f.time_column} onChange={set("time_column")}>
            <option value="">None</option>
            {columns.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </Field>
        <Field label="Default filter" helper={<>Optional condition, such as <code>status = &apos;completed&apos;</code>.</>}>
          <input className={`${inputCls} font-mono`} value={f.filters} onChange={set("filters")} />
        </Field>
        <Field label="Format">
          <select className={inputCls} value={f.format} onChange={set("format")}>
            {Object.entries(FORMAT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
      </div>
    </SidePanel>
  );
}
