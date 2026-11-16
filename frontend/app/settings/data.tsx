"use client";

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import SortableTable, { type Column } from "@/components/SortableTable";
import { Button, Field, inputCls, Panel, Status, useUI } from "@/components/ui";
import { apiJson } from "@/lib/api";
import { ago } from "@/lib/time";

/* ---------- Data and retention tab: retention, limits, background jobs ---------- */

export default function DataRetention() {
  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-2">
        <RetentionPanel />
        <LimitsPanel />
      </div>
      <JobsPanel />
    </div>
  );
}

type Retention = { questions_days: number; notifications_days: number; audit_days: number };

function DaysField({ label, helper, value, onChange }: { label: string; helper: string; value: number; onChange: (n: number) => void }) {
  return (
    <Field label={label} helper={helper}>
      <input type="number" min={0} max={36500} value={value} onChange={(e) => onChange(Math.max(0, Number(e.target.value) || 0))} className={`${inputCls} w-40`} />
    </Field>
  );
}

function RetentionPanel() {
  const { toast } = useUI();
  const [r, setR] = useState<Retention | null>(null);
  useEffect(() => {
    apiJson<Retention>("/api/settings/retention").then(setR);
  }, []);
  if (!r) return null;
  async function save(e: React.FormEvent) {
    e.preventDefault();
    try {
      setR(await apiJson<Retention>("/api/settings/retention", { method: "PUT", body: JSON.stringify(r) }));
      toast("Retention settings saved. Older records are removed once a day.");
    } catch (err) {
      toast((err as Error).message, "error");
    }
  }
  return (
    <Panel title="Retention" description="How long records are kept, in days. 0 keeps them forever.">
      <form onSubmit={save} className="space-y-4">
        <DaysField label="Questions and answers" helper="Activity history and saved conversations." value={r.questions_days} onChange={(n) => setR({ ...r, questions_days: n })} />
        <DaysField label="Notifications" helper="Read and unread notifications." value={r.notifications_days} onChange={(n) => setR({ ...r, notifications_days: n })} />
        <DaysField label="Audit log" helper="Each cleanup is itself recorded in the audit log." value={r.audit_days} onChange={(n) => setR({ ...r, audit_days: n })} />
        <Button variant="primary" type="submit">Save retention</Button>
      </form>
    </Panel>
  );
}

type Limits = { daily_tokens: number; per_minute: number; allowed_models: Record<"anthropic" | "openai", string[]> };
const PROVIDER_LABELS = { anthropic: "Claude", openai: "ChatGPT" } as const;

function LimitsPanel() {
  const { toast } = useUI();
  const [l, setL] = useState<Limits | null>(null);
  const [models, setModels] = useState({ anthropic: "", openai: "" });
  const [err, setErr] = useState("");
  const apply = useCallback((v: Limits) => {
    setL(v);
    setModels({ anthropic: v.allowed_models.anthropic.join("\n"), openai: v.allowed_models.openai.join("\n") });
  }, []);
  useEffect(() => {
    apiJson<Limits>("/api/settings/limits").then(apply);
  }, [apply]);
  if (!l) return null;
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    const split = (s: string) => s.split(/[\n,]/).map((m) => m.trim()).filter(Boolean);
    try {
      apply(await apiJson<Limits>("/api/settings/limits", { method: "PUT", body: JSON.stringify({
        daily_tokens: l!.daily_tokens, per_minute: l!.per_minute,
        allowed_models: { anthropic: split(models.anthropic), openai: split(models.openai) },
      }) }));
      toast("Limits saved.");
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  return (
    <Panel title="Limits" description="Applied to each person. 0 or an empty list means no limit.">
      <form onSubmit={save} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Daily AI tokens per person" helper="Resets at midnight UTC.">
            <input type="number" min={0} value={l.daily_tokens} onChange={(e) => setL({ ...l, daily_tokens: Math.max(0, Number(e.target.value) || 0) })} className={inputCls} />
          </Field>
          <Field label="Questions per minute per person">
            <input type="number" min={0} max={1000} value={l.per_minute} onChange={(e) => setL({ ...l, per_minute: Math.max(0, Number(e.target.value) || 0) })} className={inputCls} />
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          {(["anthropic", "openai"] as const).map((p) => (
            <Field key={p} label={`Allowed ${PROVIDER_LABELS[p]} models`} helper="One model name per line.">
              <textarea rows={3} value={models[p]} onChange={(e) => setModels({ ...models, [p]: e.target.value })}
                placeholder="Any model" className={`${inputCls} h-auto py-1.5 font-mono`} />
            </Field>
          ))}
        </div>
        {err && <p className="text-sm text-bad">{err}</p>}
        <Button variant="primary" type="submit">Save limits</Button>
      </form>
    </Panel>
  );
}

type Job = { id: number; kind: string; status: "queued" | "running" | "done" | "failed"; attempts: number; max_attempts: number; error: string | null; created_at: string; finished_at: string | null };
type Worker = { mode: string; heartbeat_age_seconds: number | null; healthy: boolean };
const JOB_STATUS = { queued: ["neutral", "Queued"], running: ["running", "Running"], done: ["success", "Done"], failed: ["error", "Failed"] } as const;

function JobsPanel() {
  const [data, setData] = useState<{ worker: Worker; jobs: Job[] } | null>(null);
  const load = useCallback(() => apiJson<{ worker: Worker; jobs: Job[] }>("/api/jobs").then(setData), []);
  useEffect(() => {
    load();
  }, [load]);
  if (!data) return null;
  const w = data.worker;
  const cols: Column<Job>[] = [
    { key: "kind", label: "Job", sort: (j) => j.kind, render: (j) => <span className="font-mono text-xs">{j.kind}</span> },
    { key: "status", label: "Status", sort: (j) => j.status, render: (j) => <Status kind={JOB_STATUS[j.status][0]}>{JOB_STATUS[j.status][1]}</Status> },
    { key: "attempts", label: "Attempts", sort: (j) => j.attempts, render: (j) => `${j.attempts} of ${j.max_attempts}` },
    { key: "created", label: "Queued", sort: (j) => j.created_at, render: (j) => <span className="text-text2">{ago(j.created_at)}</span> },
    { key: "error", label: "Error", render: (j) => j.error && <span className="line-clamp-2 text-xs text-bad" title={j.error}>{j.error}</span> },
  ];
  return (
    <Panel title="Background jobs" flush
      description={w.mode === "inline" ? "Processed inside the API server." : "Processed by separate worker processes."}
      actions={<>
        {w.healthy ? <Status kind="success">Worker running</Status>
          : <Status kind="warning">{w.heartbeat_age_seconds === null ? "No worker has started" : `Worker last seen ${ago(new Date(Date.now() - w.heartbeat_age_seconds * 1000).toISOString())}`}</Status>}
        <Button size="sm" icon={<RefreshCw className="h-3.5 w-3.5" />} onClick={load}>Refresh</Button>
      </>}>
      <SortableTable columns={cols} rows={data.jobs} rowKey={(j) => j.id} initialSort={{ key: "created", dir: -1 }} />
    </Panel>
  );
}
