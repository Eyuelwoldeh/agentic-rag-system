"use client";

import { useEffect, useState } from "react";
import { api, apiJson } from "@/lib/api";
import { useMe } from "@/lib/me";
import { Button, Field, inputCls, SidePanel, useUI } from "./ui";

export type Catalog = { id: string; name: string; tables: Record<string, string[]> }[];
export type Person = { id: number; email: string };
export type Report = { id: number; title: string; question: string | null; tile_ids: number[] | null; cadence: "daily" | "weekly" | "monthly";
  weekday: number | null; day: number | null; hour: number; recipients: number[]; recipient_emails: string[]; enabled: boolean;
  last_run_at: string | null; last_status: "ok" | "error" | null; last_error: string | null; next_run_at: string | null };

export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const hourLabel = (h: number) => new Date(2000, 0, 1, h).toLocaleTimeString(undefined, { hour: "numeric" });

export function schedule(r: Pick<Report, "cadence" | "weekday" | "day" | "hour">) {
  const at = hourLabel(r.hour);
  if (r.cadence === "weekly") return `Every ${WEEKDAYS[r.weekday ?? 0]} at ${at}`;
  if (r.cadence === "monthly") return `Monthly on day ${r.day ?? 1} at ${at}`;
  return `Daily at ${at}`;
}

/** Checkbox list of workspace people (yourself excluded: owners always receive their own reports and alerts). */
export function PeoplePicker({ value, onChange }: { value: number[]; onChange: (ids: number[]) => void }) {
  const me = useMe()!;
  const [people, setPeople] = useState<Person[]>([]);
  useEffect(() => {
    apiJson<Person[]>("/api/people").then(setPeople).catch(() => {});
  }, []);
  const others = people.filter((p) => p.id !== me.id);
  if (!others.length) return <p className="text-xs text-muted">No one else is in this workspace yet.</p>;
  return (
    <div className="max-h-40 space-y-1 overflow-y-auto rounded border border-line p-2">
      {others.map((p) => (
        <label key={p.id} className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={value.includes(p.id)} onChange={(e) => onChange(e.target.checked ? [...value, p.id] : value.filter((i) => i !== p.id))} />
          {p.email}
        </label>
      ))}
    </div>
  );
}

type Tile = { id: number; title: string };

/** Create or edit a scheduled report: a question, or a set of dashboard charts. */
export function ReportForm({ report, question, onClose, onSaved }: { report?: Report | null; question?: string; onClose: () => void; onSaved: () => void }) {
  const { toast } = useUI();
  const [f, setF] = useState({
    title: report?.title ?? (question ? question.slice(0, 80) : ""), kind: report?.tile_ids ? "charts" : "question",
    question: report?.question ?? question ?? "", tile_ids: report?.tile_ids ?? [], cadence: report?.cadence ?? "weekly",
    weekday: report?.weekday ?? 0, day: report?.day ?? 1, hour: report?.hour ?? 8, recipients: report?.recipients ?? [],
  });
  const [tiles, setTiles] = useState<Tile[]>([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    apiJson<Tile[]>("/api/dashboard").then(setTiles).catch(() => {});
  }, []);

  async function save() {
    setSaving(true);
    setError("");
    const body = { title: f.title, cadence: f.cadence, weekday: f.weekday, day: f.day, hour: f.hour, recipients: f.recipients,
      enabled: report?.enabled ?? true, ...(f.kind === "question" ? { question: f.question } : { tile_ids: f.tile_ids }) };
    const res = await api(report ? `/api/reports/${report.id}` : "/api/reports", { method: report ? "PUT" : "POST", body: JSON.stringify(body) });
    setSaving(false);
    if (res.ok) {
      toast(report ? "Report updated." : "Report scheduled.");
      onSaved();
    } else setError((await res.json()).detail ?? "Couldn't save the report.");
  }

  const ready = f.title.trim() && (f.kind === "question" ? f.question.trim() : f.tile_ids.length > 0);
  return (
    <SidePanel title={report ? "Edit report" : "Schedule a report"} onClose={onClose}
      description="Each recipient gets their own copy, produced with their own access."
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={saving} disabled={!ready} onClick={save}>{report ? "Save" : "Schedule"}</Button></>}>
      <div className="space-y-4">
        {error && <p className="rounded border border-bad bg-bad-weak px-3 py-2 text-sm text-bad">{error}</p>}
        <Field label="Title" required><input className={inputCls} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
        <fieldset className="space-y-1">
          <legend className="mb-1 text-xs font-medium text-text2">Content</legend>
          {(["question", "charts"] as const).map((k) => (
            <label key={k} className="mr-4 inline-flex items-center gap-2 text-sm">
              <input type="radio" name="kind" checked={f.kind === k} onChange={() => setF({ ...f, kind: k })} />
              {k === "question" ? "Answer a question" : "Refresh dashboard charts"}
            </label>
          ))}
        </fieldset>
        {f.kind === "question" ? (
          <Field label="Question" required>
            <textarea rows={3} className={`${inputCls} h-auto py-1.5`} value={f.question} onChange={(e) => setF({ ...f, question: e.target.value })} />
          </Field>
        ) : (
          <Field label="Charts" required>
            <div className="max-h-48 space-y-1 overflow-y-auto rounded border border-line p-2">
              {tiles.length === 0 && <p className="text-xs text-muted">Pin charts to your dashboard first.</p>}
              {tiles.map((t) => (
                <label key={t.id} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={f.tile_ids.includes(t.id)}
                    onChange={(e) => setF({ ...f, tile_ids: e.target.checked ? [...f.tile_ids, t.id] : f.tile_ids.filter((i) => i !== t.id) })} />
                  {t.title}
                </label>
              ))}
            </div>
          </Field>
        )}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Frequency">
            <select className={inputCls} value={f.cadence} onChange={(e) => setF({ ...f, cadence: e.target.value as Report["cadence"] })}>
              <option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option>
            </select>
          </Field>
          {f.cadence === "weekly" && (
            <Field label="Day">
              <select className={inputCls} value={f.weekday} onChange={(e) => setF({ ...f, weekday: +e.target.value })}>
                {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
              </select>
            </Field>
          )}
          {f.cadence === "monthly" && (
            <Field label="Day of month">
              <select className={inputCls} value={f.day} onChange={(e) => setF({ ...f, day: +e.target.value })}>
                {Array.from({ length: 28 }, (_, i) => <option key={i} value={i + 1}>{i + 1}</option>)}
              </select>
            </Field>
          )}
          <Field label="Time" helper="Server time">
            <select className={inputCls} value={f.hour} onChange={(e) => setF({ ...f, hour: +e.target.value })}>
              {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{hourLabel(h)}</option>)}
            </select>
          </Field>
        </div>
        <Field label="Also send to" helper="You always receive your own reports.">
          <PeoplePicker value={f.recipients} onChange={(recipients) => setF({ ...f, recipients })} />
        </Field>
      </div>
    </SidePanel>
  );
}
