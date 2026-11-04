"use client";

import { useEffect, useState } from "react";
import { apiJson } from "@/lib/api";

type Run = {
  id: number;
  question: string;
  answer: string;
  events: { type: string; name?: string; input?: unknown; summary?: string; message?: string; feedback?: string; passed?: boolean }[];
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  attempts: number;
  critic: { grounded: boolean; complete: boolean; hallucination_risk: string; feedback: string } | null;
  created_at: string;
  user: string | null;
};

const passed = (r: Run) => !!r.critic && r.critic.grounded && r.critic.complete && r.critic.hallucination_risk !== "high";

export default function Runs() {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiJson<Run[]>("/api/runs")
      .then(setRuns)
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="p-6 text-bad">{error}</p>;
  if (!runs) return <p className="p-6 text-muted">Loading…</p>;

  const n = runs.length || 1;
  const stats = [
    ["Runs", runs.length.toString()],
    ["Verified", `${Math.round((runs.filter(passed).length / n) * 100)}%`],
    ["Avg latency", `${(runs.reduce((a, r) => a + r.latency_ms, 0) / n / 1000).toFixed(1)}s`],
    ["Avg tokens", Math.round(runs.reduce((a, r) => a + r.input_tokens + r.output_tokens, 0) / n).toLocaleString()],
    ["Replanned", runs.filter((r) => r.attempts > 1).length.toString()],
  ];

  return (
    <main className="mx-auto max-w-5xl px-4 py-6">
      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-5">
        {stats.map(([k, v]) => (
          <div key={k} className="rounded-lg border border-line bg-panel p-3">
            <div className="text-xs text-muted">{k}</div>
            <div className="mt-1 text-xl font-semibold tabular-nums">{v}</div>
          </div>
        ))}
      </div>
      <ul className="space-y-2">
        {runs.map((r) => (
          <li key={r.id}>
            <details className="rounded-lg border border-line bg-panel">
              <summary className="flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-sm">
                <span className="font-mono text-xs text-muted">#{r.id}</span>
                <span className="min-w-0 flex-1 truncate">{r.question}</span>
                {r.user && <span className="text-xs text-muted">{r.user}</span>}
                <span className={passed(r) ? "text-ok" : "text-warn"}>{passed(r) ? "verified" : "unverified"}</span>
                <span className="tabular-nums text-muted">{(r.latency_ms / 1000).toFixed(1)}s</span>
                <span className="tabular-nums text-muted">{(r.input_tokens + r.output_tokens).toLocaleString()} tok</span>
              </summary>
              <div className="space-y-1 border-t border-line px-3 py-2 font-mono text-xs">
                {r.events.map((e, i) => (
                  <p key={i} className={e.type === "error" ? "text-bad" : "text-muted"}>
                    <span className="text-foreground">{e.type === "tool" ? `→ ${e.name}` : e.type === "tool_result" ? `↳ ${e.name}` : e.type}</span>{" "}
                    {e.summary ?? e.message ?? e.feedback ?? (e.input ? JSON.stringify(e.input) : "")}
                  </p>
                ))}
              </div>
            </details>
          </li>
        ))}
      </ul>
    </main>
  );
}
