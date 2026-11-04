"use client";

import { useEffect, useState } from "react";
import Chart, { fmt } from "@/components/Chart";
import { apiJson } from "@/lib/api";
import { ago } from "@/lib/time";

type Insights = {
  days: number; questions: number; active_users: number; verified_rate: number | null; thumbs_up: number; thumbs_down: number;
  median_latency_ms: number; estimated_cost_usd: number; daily: { day: string; questions: number }[];
  gaps: { id: number; question: string; created_at: string; user: string | null; reason: string; note: string | null }[];
};

const REASON_HELP: Record<string, string> = {
  "thumbs down": "Someone marked the answer as bad",
  "nothing found": "No document matched; the knowledge may be missing",
  "not verified": "The fact-checker couldn't confirm every claim",
};

export default function InsightsPage() {
  const [days, setDays] = useState(30);
  const [d, setD] = useState<Insights | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    apiJson<Insights>(`/api/insights?days=${days}`).then(setD).catch((e) => setError(e.message));
  }, [days]);

  if (error) return <p className="p-6 text-bad">{error}</p>;
  if (!d) return <p className="p-6 text-sm text-muted">Loading…</p>;

  const stats: [string, string, string?][] = [
    ["Questions asked", fmt(d.questions)],
    ["Active people", fmt(d.active_users)],
    ["Verified answers", d.verified_rate == null ? "–" : `${Math.round(d.verified_rate * 100)}%`, "Share of answers the fact-checker fully confirmed"],
    ["Helpful / not helpful", `${d.thumbs_up} / ${d.thumbs_down}`],
    ["Typical answer time", `${(d.median_latency_ms / 1000).toFixed(1)}s`, "Median"],
    ["Estimated AI cost", `$${d.estimated_cost_usd.toFixed(2)}`, "Token usage × list price of the main model. Dashboards cost nothing to refresh."],
  ];

  return (
    <main className="mx-auto max-w-5xl space-y-8 px-4 py-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">Insights</h1>
          <p className="mt-1 text-sm text-muted">How your team uses the assistant, and where your knowledge base has gaps.</p>
        </div>
        <select value={days} onChange={(e) => setDays(+e.target.value)} className="rounded-md border border-line bg-panel px-2 py-1 text-sm" aria-label="Time range">
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </select>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {stats.map(([label, value, help]) => (
          <div key={label} className="rounded-lg border border-line bg-panel p-3" title={help}>
            <div className="text-xs text-muted">{label}</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
          </div>
        ))}
      </div>

      {d.daily.length > 1 && (
        <Chart data={{ title: "Questions per day", spec: { type: "bar", x: "day", y: ["questions"] }, columns: ["day", "questions"], rows: d.daily.map((r) => [r.day.slice(5), r.questions]) }} />
      )}

      <section>
        <h2 className="font-semibold">Knowledge gaps</h2>
        <p className="mt-1 text-sm text-muted">Questions the assistant couldn&apos;t answer well. Adding or updating the right document usually fixes them.</p>
        {d.gaps.length === 0 ? (
          <p className="mt-3 rounded-lg border border-line bg-panel p-4 text-sm text-muted">No gaps in this period. 🎉</p>
        ) : (
          <ul className="mt-3 divide-y divide-line rounded-lg border border-line bg-panel text-sm">
            {d.gaps.map((g) => (
              <li key={g.id} className="flex flex-wrap items-start gap-x-3 gap-y-1 px-3 py-2">
                <span className="min-w-0 flex-1">
                  {g.question}
                  {g.note && <span className="block text-xs text-muted">“{g.note}”</span>}
                </span>
                <span className="rounded-full bg-warn/15 px-2 py-0.5 text-xs text-warn" title={REASON_HELP[g.reason]}>{g.reason}</span>
                <span className="text-xs text-muted">{g.user ?? "unknown"} · {ago(g.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
