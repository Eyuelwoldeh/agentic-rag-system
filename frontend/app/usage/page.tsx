"use client";

import { CircleCheck } from "lucide-react";
import { useEffect, useState } from "react";
import Chart, { fmt } from "@/components/Chart";
import SortableTable, { type Column } from "@/components/SortableTable";
import { EmptyState, Page, PageHeader, Panel, Skeleton, Status, useUI } from "@/components/ui";
import { apiJson } from "@/lib/api";
import { ago } from "@/lib/time";

type Gap = { id: number; question: string; created_at: string; user: string | null; reason: string; note: string | null };
type Usage = {
  days: number; questions: number; active_users: number; verified_rate: number | null; thumbs_up: number; thumbs_down: number;
  median_latency_ms: number; estimated_cost_usd: number; daily: { day: string; questions: number }[]; gaps: Gap[];
};

const REASON: Record<string, { label: string; help: string }> = {
  "thumbs down": { label: "Marked not helpful", help: "Someone rated the answer as not helpful" },
  "nothing found": { label: "Nothing found", help: "No document matched; the information may be missing" },
  "not verified": { label: "Not verified", help: "The fact-check couldn't confirm every claim" },
};

export default function UsagePage() {
  const { toast } = useUI();
  const [days, setDays] = useState(30);
  const [d, setD] = useState<Usage | null>(null);
  useEffect(() => {
    setD(null);
    apiJson<Usage>(`/api/insights?days=${days}`).then(setD).catch((e) => toast(e.message, "error"));
  }, [days, toast]);

  const stats: [string, string, string?][] = d ? [
    ["Questions", fmt(d.questions)],
    ["Active people", fmt(d.active_users)],
    ["Verified answers", d.verified_rate == null ? "No data" : `${Math.round(d.verified_rate * 100)}%`, "Share of answers the fact-check fully confirmed"],
    ["Rated helpful", `${d.thumbs_up} of ${d.thumbs_up + d.thumbs_down}`],
    ["Median response time", `${(d.median_latency_ms / 1000).toFixed(1)} s`],
    ["Estimated AI cost", `$${d.estimated_cost_usd.toFixed(2)}`, "Tokens × list price for models with known pricing. Dashboard refreshes cost nothing."],
  ] : [];

  const gapCols: Column<Gap>[] = [
    { key: "q", label: "Question", sort: (g) => g.question.toLowerCase(), render: (g) => (
      <span className="block max-w-xl">
        <span className="block">{g.question}</span>
        {g.note && <span className="block text-xs text-muted">Feedback: {g.note}</span>}
      </span>
    ) },
    { key: "reason", label: "Reason", sort: (g) => g.reason, render: (g) => <Status kind="warning" title={REASON[g.reason]?.help}>{REASON[g.reason]?.label ?? g.reason}</Status> },
    { key: "user", label: "Asked by", sort: (g) => g.user ?? "", render: (g) => <span className="text-text2">{g.user ?? "Unknown"}</span> },
    { key: "when", label: "When", sort: (g) => g.created_at, render: (g) => <span className="text-text2">{ago(g.created_at)}</span> },
  ];

  return (
    <>
      <PageHeader title="Usage" description="How your organization uses the assistant, and which questions it couldn't answer well."
        actions={
          <label className="flex items-center gap-2 text-xs text-text2">
            Period
            <select value={days} onChange={(e) => setDays(+e.target.value)} className="h-8 rounded border border-line bg-panel px-2 text-sm text-foreground">
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
            </select>
          </label>
        } />
      <Page>
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded border border-line bg-line sm:grid-cols-3 lg:grid-cols-6">
          {(d ? stats : Array.from({ length: 6 }, () => ["", ""] as [string, string])).map(([label, value, help], i) => (
            <div key={label || i} className="bg-panel p-4" title={help}>
              {d ? <>
                <div className="text-xs text-text2">{label}</div>
                <div className="mt-1 text-2xl leading-8 tabular-nums">{value}</div>
              </> : <Skeleton className="h-12" />}
            </div>
          ))}
        </div>

        {d && d.daily.length > 1 && (
          <Chart data={{ title: "Questions per day", spec: { type: "bar", x: "day", y: ["questions"] }, columns: ["day", "questions"], rows: d.daily.map((r) => [r.day.slice(5), r.questions]) }} />
        )}

        <Panel title="Knowledge gaps" description="Questions the assistant couldn't answer well. Adding or updating the right document usually resolves them." flush>
          {!d ? <div className="p-4"><Skeleton className="h-8" /></div> : (
            <SortableTable columns={gapCols} rows={d.gaps} rowKey={(g) => g.id} initialSort={{ key: "when", dir: -1 }}
              empty={<EmptyState icon={<CircleCheck className="h-6 w-6" />} title="No gaps in this period" body="Every question was answered and verified, and nothing was rated unhelpful." />} />
          )}
        </Panel>
      </Page>
    </>
  );
}
