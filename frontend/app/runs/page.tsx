"use client";

import { ChevronRight, History } from "lucide-react";
import { useEffect, useState } from "react";
import { EmptyState, Page, PageHeader, Skeleton, Status, inputCls, useUI } from "@/components/ui";
import { apiJson } from "@/lib/api";
import { useMe } from "@/lib/me";
import { ago } from "@/lib/time";

type Run = {
  id: number; question: string; answer: string;
  events: { type: string; name?: string; input?: unknown; summary?: string; message?: string; feedback?: string; passed?: boolean }[];
  input_tokens: number; output_tokens: number; latency_ms: number; attempts: number;
  critic: { grounded: boolean; complete: boolean; hallucination_risk: string; feedback: string } | null;
  created_at: string; user: string | null; feedback: number | null;
};

const verified = (r: Run) => !!r.critic && r.critic.grounded && r.critic.complete && r.critic.hallucination_risk !== "high";

export default function Activity() {
  const me = useMe()!;
  const { toast } = useUI();
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => {
    apiJson<Run[]>("/api/runs?limit=200").then(setRuns).catch((e) => toast(e.message, "error"));
  }, [toast]);

  const shown = (runs ?? []).filter((r) => !q || r.question.toLowerCase().includes(q.toLowerCase()) || (r.user ?? "").includes(q.toLowerCase()));
  const status = (r: Run) => r.events.some((e) => e.type === "error") ? <Status kind="error">Failed</Status>
    : verified(r) ? <Status kind="success">Verified</Status>
    : r.critic ? <Status kind="warning">Not verified</Status>
    : <Status kind="neutral">Unchecked</Status>;

  return (
    <>
      <PageHeader title="Activity" description={me.role === "admin" ? "Every question asked in your organization, with how each answer was produced." : "Questions you've asked, with how each answer was produced."} />
      <Page wide>
        <div className="rounded border border-line bg-panel">
          <div className="border-b border-line p-3">
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={me.role === "admin" ? "Search questions or people" : "Search questions"} aria-label="Search activity" className={`${inputCls} max-w-sm`} />
          </div>
          {!runs ? <div className="space-y-2 p-4"><Skeleton className="h-8" /><Skeleton className="h-8" /></div>
            : shown.length === 0 ? <EmptyState icon={<History className="h-6 w-6" />} title={q ? "No matching questions" : "No activity yet"} body={q ? "Try a different search." : "Questions asked in Ask appear here."} /> : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th className="w-8" aria-label="Expand" />
                    <th>Question</th>
                    {me.role === "admin" && <th>Asked by</th>}
                    <th>Status</th>
                    <th>Rating</th>
                    <th className="num">Time</th>
                    <th className="num">Tokens</th>
                    <th>When</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r) => [
                    <tr key={r.id} className="cursor-pointer" onClick={() => setOpen(open === r.id ? null : r.id)}>
                      <td><ChevronRight className={`h-4 w-4 text-muted transition-transform ${open === r.id ? "rotate-90" : ""}`} aria-hidden /></td>
                      <td className="max-w-md"><button className="line-clamp-1 text-left" aria-expanded={open === r.id}>{r.question}</button></td>
                      {me.role === "admin" && <td className="text-text2">{r.user ?? "Unknown"}</td>}
                      <td>{status(r)}</td>
                      <td className="text-text2">{r.feedback === 1 ? "Helpful" : r.feedback === -1 ? "Not helpful" : ""}</td>
                      <td className="num">{(r.latency_ms / 1000).toFixed(1)} s</td>
                      <td className="num">{(r.input_tokens + r.output_tokens).toLocaleString()}</td>
                      <td className="text-text2 whitespace-nowrap">{ago(r.created_at)}</td>
                    </tr>,
                    open === r.id && (
                      <tr key={`${r.id}-detail`}>
                        <td />
                        <td colSpan={me.role === "admin" ? 7 : 6} className="bg-nav">
                          <div className="space-y-3 py-2">
                            {r.critic && !verified(r) && <p className="text-sm"><span className="font-medium">Fact-check notes: </span><span className="text-text2">{r.critic.feedback}</span></p>}
                            <ol className="space-y-1 font-mono text-xs">
                              {r.events.map((e, i) => (
                                <li key={i} className={e.type === "error" ? "text-bad" : "text-text2"}>
                                  <span className="text-foreground">{e.type === "tool" ? e.name : e.type === "tool_result" ? `result of ${e.name}` : e.type}</span>{" "}
                                  {e.summary ?? e.message ?? e.feedback ?? (e.input ? JSON.stringify(e.input) : "")}
                                </li>
                              ))}
                            </ol>
                          </div>
                        </td>
                      </tr>
                    ),
                  ])}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Page>
    </>
  );
}
