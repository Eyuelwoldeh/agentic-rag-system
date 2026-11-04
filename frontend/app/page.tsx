"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import Chart, { type ChartData } from "@/components/Chart";
import { api, apiJson, streamChat, type AgentEvent, type Source } from "@/lib/api";
import { useMe } from "@/lib/me";

type ChartEvent = ChartData & { id: string; source: string; sql: string };
type Step = Exclude<AgentEvent, { type: "text" | "sources" | "done" | "chart" }>;
type Done = Extract<AgentEvent, { type: "done" }>;
type Assistant = { role: "assistant"; content: string; steps: Step[]; sources: Source[]; charts: ChartEvent[]; done?: Done; pending: boolean };
type Msg = { role: "user"; content: string } | Assistant;
type Home = { checklist: { id: string; label: string; done: boolean }[]; suggestions: string[] };
type Library = { documents: { id: number; name: string; chunks: number; mine: boolean; private: boolean }[]; tables: Record<string, Record<string, string>> };

export default function Chat() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [home, setHome] = useState<Home | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const loadHome = useCallback(() => apiJson<Home>("/api/home").then(setHome).catch(() => {}), []);
  useEffect(() => {
    loadHome();
  }, [loadHome]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" }); // braces: newer Chrome returns a Promise here
  }, [msgs]);

  function patchLast(fn: (m: Assistant) => Partial<Assistant>) {
    setMsgs((prev) => {
      const last = prev[prev.length - 1];
      if (last?.role !== "assistant") return prev;
      return [...prev.slice(0, -1), { ...last, ...fn(last) }];
    });
  }

  async function ask(question: string) {
    question = question.trim();
    if (!question || busy) return;
    const history = msgs.filter((m) => m.content).map((m) => ({ role: m.role, content: m.content })).slice(-20);
    setInput("");
    setBusy(true);
    setMsgs((p) => [...p, { role: "user", content: question }, { role: "assistant", content: "", steps: [], sources: [], charts: [], pending: true }]);
    try {
      for await (const ev of streamChat(question, history)) {
        if (ev.type === "text") patchLast((m) => ({ content: m.content + ev.text }));
        else if (ev.type === "sources") patchLast(() => ({ sources: ev.items }));
        else if (ev.type === "done") patchLast(() => ({ done: ev, content: ev.answer || "", pending: false }));
        else if (ev.type === "chart") {
          const c = ev.chart;
          patchLast((m) => ({ charts: [...m.charts, { ...c, spec: { type: c.type, x: c.x, y: c.y } }] }));
        }
        else if (ev.type === "thinking")
          patchLast((m) => {
            const last = m.steps[m.steps.length - 1];
            if (last?.type === "thinking") return { steps: [...m.steps.slice(0, -1), { ...last, text: last.text + ev.text }] };
            return { steps: [...m.steps, ev] };
          });
        else patchLast((m) => ({ steps: [...m.steps, ev], ...(ev.type === "revising" ? { content: "", charts: [] } : {}) }));
      }
    } catch (err) {
      patchLast((m) => ({ steps: [...m.steps, { type: "error", message: String(err) }] }));
    } finally {
      patchLast(() => ({ pending: false }));
      setBusy(false);
      loadHome();
    }
  }

  return (
    <div className="flex h-[calc(100dvh-3rem)] flex-col md:flex-row">
      <Sidebar onChange={loadHome} />
      <main className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto px-4 py-6">
          <div className="mx-auto max-w-3xl space-y-6">
            {msgs.length === 0 && <Welcome home={home} onAsk={ask} />}
            {msgs.map((m, i) => (m.role === "user" ? <UserBubble key={i} text={m.content} /> : <AssistantMsg key={i} m={m} />))}
            <div ref={bottom} />
          </div>
        </div>
        <form onSubmit={(e) => { e.preventDefault(); ask(input); }} className="border-t border-line p-4">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <textarea
              value={input}
              rows={1}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  ask(input);
                }
              }}
              placeholder="Ask about your docs, your data, or the web…"
              aria-label="Question"
              className="max-h-40 flex-1 resize-none rounded-lg border border-line bg-panel px-3 py-2 outline-none focus:border-accent"
            />
            <button disabled={busy || !input.trim()} className="rounded-lg bg-accent px-4 py-2 font-medium text-background disabled:opacity-40">
              {busy ? "Working…" : "Ask"}
            </button>
          </div>
          <p className="mx-auto mt-1.5 max-w-3xl text-xs text-muted">Enter to send · Shift+Enter for a new line · answers only use sources you have access to</p>
        </form>
      </main>
    </div>
  );
}

function Welcome({ home, onAsk }: { home: Home | null; onAsk: (q: string) => void }) {
  const [hidden, setHidden] = useState(() => {
    try {
      return localStorage.getItem("hide-checklist") === "1";
    } catch {
      return false;
    }
  });
  const remaining = home?.checklist.filter((c) => !c.done).length ?? 0;
  return (
    <div className="space-y-6 pt-10">
      <div className="text-center">
        <h1 className="text-xl font-semibold">What do you want to know?</h1>
        <p className="mt-1 text-sm text-muted">I search your documents and connected apps, query your data, and check every answer before marking it verified.</p>
      </div>

      {home && home.suggestions.length > 0 && (
        <div className="grid gap-2 sm:grid-cols-2">
          {home.suggestions.map((s) => (
            <button key={s} onClick={() => onAsk(s)} className="rounded-lg border border-line bg-panel px-3 py-2 text-left text-sm hover:border-accent">
              {s}
            </button>
          ))}
        </div>
      )}

      {home && remaining > 0 && !hidden && (
        <section className="rounded-lg border border-line bg-panel p-4 text-sm">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="font-medium">Getting started</h2>
            <button onClick={() => { setHidden(true); try { localStorage.setItem("hide-checklist", "1"); } catch {} }} className="text-xs text-muted hover:text-foreground">
              Hide
            </button>
          </div>
          <ol className="space-y-1.5">
            {home.checklist.map((c) => (
              <li key={c.id} className={`flex items-center gap-2 ${c.done ? "text-muted line-through" : ""}`}>
                <span className={`grid h-4 w-4 shrink-0 place-items-center rounded-full border text-[10px] ${c.done ? "border-ok bg-ok text-background" : "border-line"}`}>{c.done ? "✓" : ""}</span>
                {c.label}
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-accent/10 px-4 py-2">{text}</div>
    </div>
  );
}

function AssistantMsg({ m }: { m: Assistant }) {
  const critic = [...m.steps].reverse().find((s) => s.type === "critic");
  return (
    <div className="space-y-3">
      {m.steps.length > 0 && <Trace steps={m.steps} pending={m.pending} />}
      {m.charts.map((c) => <Chart key={c.id} data={c} action={<PinButton chart={c} />} />)}
      {m.content && <div className="md leading-relaxed"><ReactMarkdown>{m.content}</ReactMarkdown></div>}
      {m.pending && !m.content && <p className="animate-pulse text-sm text-muted">Working on it…</p>}
      {m.sources.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {m.sources.map((s) => <SourceChip key={s.label} s={s} />)}
        </div>
      )}
      {m.done && (
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
          {m.done.verified ? (
            <span className="rounded-full bg-ok/15 px-2 py-0.5 font-medium text-ok" title="A second AI checked every claim against the sources">✓ Verified</span>
          ) : critic && critic.type === "critic" ? (
            <span className="rounded-full bg-warn/15 px-2 py-0.5 font-medium text-warn" title={critic.feedback}>⚠ Not fully verified: hover for why</span>
          ) : null}
          <span>{(m.done.latency_ms / 1000).toFixed(1)}s</span>
          <CopyButton text={m.content} />
          <Feedback runId={m.done.run_id} />
        </div>
      )}
    </div>
  );
}

function PinButton({ chart }: { chart: ChartEvent }) {
  const [state, setState] = useState<"idle" | "saving" | "pinned" | "error">("idle");
  async function pin() {
    setState("saving");
    const res = await api("/api/dashboard/tiles", {
      method: "POST",
      body: JSON.stringify({ title: chart.title, ...chart.spec, source: chart.source, sql: chart.sql }),
    });
    setState(res.ok ? "pinned" : "error");
  }
  return (
    <button onClick={pin} disabled={state === "saving" || state === "pinned"} className="rounded bg-accent px-2 py-0.5 font-medium text-background disabled:opacity-60" title="Add to your dashboard. It re-runs the query each time, so it stays current.">
      {state === "pinned" ? "Pinned ✓" : state === "error" ? "Retry pin" : "Pin to dashboard"}
    </button>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button onClick={() => navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); })} className="hover:text-foreground">
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function Feedback({ runId }: { runId: number }) {
  const [value, setValue] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [sent, setSent] = useState(false);
  async function send(v: number, n = "") {
    setValue(v);
    await api(`/api/runs/${runId}/feedback`, { method: "POST", body: JSON.stringify({ value: v, note: n }) });
    if (n || v === 1) setSent(true);
  }
  if (sent) return <span>Thanks for the feedback</span>;
  return (
    <span className="flex items-center gap-1">
      <button aria-label="Good answer" onClick={() => send(1)} className={`rounded px-1 ${value === 1 ? "text-ok" : "hover:text-foreground"}`}>👍</button>
      <button aria-label="Bad answer" onClick={() => send(-1)} className={`rounded px-1 ${value === -1 ? "text-bad" : "hover:text-foreground"}`}>👎</button>
      {value === -1 && (
        <form onSubmit={(e) => { e.preventDefault(); send(-1, note); }} className="flex gap-1">
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="What was wrong? (optional)" className="w-48 rounded border border-line bg-background px-1.5 py-0.5" />
          <button className="hover:text-foreground">Send</button>
        </form>
      )}
    </span>
  );
}

const SOURCE_LABEL: Record<string, string> = { slack: "Slack", gdrive: "Drive", notion: "Notion", github: "GitHub", web: "Site" };

function SourceChip({ s }: { s: Source }) {
  const label = s.kind === "web" ? s.title : s.source && s.source !== "upload" ? `${SOURCE_LABEL[s.source] ?? s.source} · ${s.document}` : `${s.document} p.${s.page}`;
  const body = (
    <span className="inline-flex max-w-xs items-center gap-1.5 truncate rounded-md border border-line bg-panel px-2 py-1 text-xs" title={s.content ?? s.url}>
      <span className="font-mono text-accent">{s.label}</span>
      <span className="truncate">{label}</span>
    </span>
  );
  return s.url ? <a href={s.url} target="_blank" rel="noreferrer">{body}</a> : body;
}

const TOOL_LABEL: Record<string, string> = {
  search_documents: "Searching docs",
  web_search: "Searching the web",
  list_data_sources: "Finding data",
  describe_tables: "Reading table columns",
  run_sql: "Querying data",
  create_chart: "Making a chart",
  code_execution: "Running a calculation",
};

function Trace({ steps, pending }: { steps: Step[]; pending: boolean }) {
  const tools = steps.filter((s) => s.type === "tool").length;
  return (
    <details open={pending} className="rounded-lg border border-line bg-panel text-sm">
      <summary className="cursor-pointer select-none px-3 py-2 text-muted">
        How I got this answer · {tools} step{tools === 1 ? "" : "s"}
      </summary>
      <ol className="space-y-2 border-t border-line px-3 py-2">
        {steps.map((s, i) => (
          <li key={i} className="flex gap-2">
            <StepView s={s} />
          </li>
        ))}
      </ol>
    </details>
  );
}

function StepView({ s }: { s: Step }) {
  switch (s.type) {
    case "thinking":
      return <p className="whitespace-pre-wrap italic text-muted">{s.text}</p>;
    case "tool":
      return (
        <p>
          <span className="mr-2 rounded bg-accent/15 px-1.5 py-0.5 text-xs font-medium text-accent">
            {s.name.startsWith("mcp:") ? `App · ${s.name.slice(4)}` : TOOL_LABEL[s.name] ?? s.name}
          </span>
          <code className="font-mono text-xs text-muted">{fmtInput(s.input)}</code>
        </p>
      );
    case "tool_result":
      return <p className={`line-clamp-2 font-mono text-xs ${s.error ? "text-bad" : "text-muted"}`}>↳ {s.summary}</p>;
    case "critic":
      return (
        <p className={s.passed ? "text-ok" : "text-warn"}>
          Fact-check (attempt {s.attempt}): {s.passed ? "passed" : "found problems"}
          {!s.passed && <span className="block text-muted">{s.feedback}</span>}
        </p>
      );
    case "revising":
      return <p className="font-medium text-warn">↻ Rechecking and rewriting (attempt {s.attempt})</p>;
    case "error":
      return <p className="text-bad">Error: {s.message}</p>;
  }
}

function fmtInput(input: unknown) {
  if (input && typeof input === "object") {
    const o = input as Record<string, unknown>;
    const text = String(o.query ?? o.title ?? o.command ?? o.code ?? (Array.isArray(o.tables) ? o.tables.join(", ") : "") ?? "").slice(0, 200);
    return o.source && typeof o.source === "string" && !o.source.startsWith("conn-") && o.source !== "uploads"
      ? `${text} · in ${SOURCE_LABEL[o.source] ?? o.source}` : text;
  }
  return String(input ?? "");
}

function Sidebar({ onChange }: { onChange: () => void }) {
  const me = useMe()!;
  const [lib, setLib] = useState<Library>({ documents: [], tables: {} });
  const [status, setStatus] = useState("");
  const [visibility, setVisibility] = useState<"everyone" | "private">("everyone");

  const refresh = useCallback(
    () => api("/api/sources").then((r) => r.json()).then(setLib).catch(() => setStatus("Backend not reachable")),
    [],
  );
  useEffect(() => {
    refresh();
  }, [refresh]);

  async function upload(files: FileList | null) {
    for (const file of Array.from(files ?? [])) {
      setStatus(`Reading ${file.name}…`);
      const body = new FormData();
      body.append("file", file);
      body.append("visibility", visibility);
      const res = await api("/api/upload", { method: "POST", body });
      const json = await res.json();
      setStatus(res.ok ? `Added ${file.name}${visibility === "private" ? " (only you)" : ""}` : `${file.name}: ${json.detail}`);
    }
    refresh();
    onChange();
  }

  async function remove(id: number, name: string) {
    if (!confirm(`Delete ${name}?`)) return;
    const res = await api(`/api/documents/${id}`, { method: "DELETE" });
    if (!res.ok) setStatus((await res.json()).detail);
    refresh();
    onChange();
  }

  return (
    <aside className="max-h-64 shrink-0 overflow-y-auto border-b border-line p-4 text-sm md:max-h-none md:w-72 md:border-b-0 md:border-r">
      <label className="block cursor-pointer rounded-lg border border-dashed border-line px-3 py-4 text-center text-muted hover:border-accent hover:text-foreground">
        <span className="block font-medium text-foreground">Add files</span>
        <span className="text-xs">PDF, Word-exported text, Markdown, or CSV spreadsheets</span>
        <input type="file" multiple accept=".pdf,.txt,.md,.csv" className="sr-only" onChange={(e) => upload(e.target.files)} />
      </label>
      <div className="mt-2 flex items-center gap-2 text-xs text-muted">
        <span>Who can see uploads:</span>
        <select value={visibility} onChange={(e) => setVisibility(e.target.value as "everyone" | "private")} className="rounded border border-line bg-background px-1 py-0.5" aria-label="Upload visibility">
          <option value="everyone">Everyone</option>
          <option value="private">Only me</option>
        </select>
      </div>
      {status && <p className="mt-2 text-xs text-muted" role="status">{status}</p>}

      <h2 className="mt-5 mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Files</h2>
      {lib.documents.length === 0 && <p className="text-xs text-muted">Nothing yet. Add a policy, handbook or report to try it out.</p>}
      <ul className="space-y-1">
        {lib.documents.map((d) => (
          <li key={d.id} className="group flex items-center justify-between gap-2">
            <span className="truncate" title={d.name}>{d.private && <span title="Only you can see this" className="mr-1">🔒</span>}{d.name}</span>
            {(d.mine || me.role === "admin") && (
              <button onClick={() => remove(d.id, d.name)} aria-label={`Delete ${d.name}`} className="shrink-0 text-xs text-muted opacity-0 hover:text-bad group-hover:opacity-100 focus:opacity-100">✕</button>
            )}
          </li>
        ))}
      </ul>

      <h2 className="mt-5 mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Spreadsheets</h2>
      {Object.keys(lib.tables).length === 0 && <p className="text-xs text-muted">Upload a CSV to ask questions about numbers and get charts.</p>}
      <ul className="space-y-2">
        {Object.entries(lib.tables).map(([t, cols]) => (
          <li key={t}>
            <span className="font-mono">{t}</span>
            <p className="truncate text-xs text-muted" title={Object.keys(cols).join(", ")}>{Object.keys(cols).join(", ")}</p>
          </li>
        ))}
      </ul>
    </aside>
  );
}
