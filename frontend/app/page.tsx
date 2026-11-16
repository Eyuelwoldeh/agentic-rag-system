"use client";

import { CalendarClock, ChevronDown, ChevronRight, Copy, Download, ExternalLink, FileText, History, MessageSquarePlus, PanelLeft, Pencil, Search, ThumbsDown, ThumbsUp, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import Chart, { type ChartData } from "@/components/Chart";
import { ReportForm } from "@/components/Insights";
import { DocumentPreview } from "@/components/Preview";
import { Button, SidePanel, Status, useUI } from "@/components/ui";
import { API, api, apiJson, streamChat, type AgentEvent, type RawChart, type Source } from "@/lib/api";
import { useMe } from "@/lib/me";
import { usePref } from "@/lib/prefs";
import { ago } from "@/lib/time";

type ChartEvent = ChartData & { id: string; source: string; sql: string };
type Step = Exclude<AgentEvent, { type: "text" | "sources" | "done" | "chart" | "restart" | "conversation" }>;
type Done = Extract<AgentEvent, { type: "done" }>;
type Assistant = { role: "assistant"; content: string; steps: Step[]; sources: Source[]; charts: ChartEvent[]; done?: Done; pending: boolean; feedback?: number | null };
type Msg = { role: "user"; content: string } | Assistant;
type Home = { checklist: { id: string; label: string; done: boolean }[]; suggestions: string[]; ai: string[] };
type Conv = { id: number; title: string; updated_at: string; questions: number };
type Turn = { run_id: number; question: string; answer: string; sources: Source[]; charts: RawChart[]; steps: Step[]; memory: string;
  latency_ms: number; attempts: number; verified: boolean; feedback: number | null; input_tokens: number; output_tokens: number };

const toChart = (c: RawChart): ChartEvent => ({ ...c, spec: { type: c.type, x: c.x, y: c.y } });
/** Internal query/chart ids ("[Rd7ea5b]", "[C1]") mean nothing to people; drop them while the answer streams. */
const stripIds = (t: string) => t.replace(/\s?\[(?:R[0-9a-f]{6}|C\d+)(?:\s*,\s*(?:R[0-9a-f]{6}|C\d+))*\]/g, "");

/** A saved conversation → the messages the page draws (same shapes as a live answer). */
function turnsToMsgs(turns: Turn[]): Msg[] {
  return turns.flatMap((t) => [
    { role: "user" as const, content: t.question },
    { role: "assistant" as const, content: t.answer, steps: t.steps, sources: t.sources, charts: t.charts.map(toChart), pending: false, feedback: t.feedback,
      done: { type: "done" as const, run_id: t.run_id, answer: t.answer, latency_ms: t.latency_ms, attempts: t.attempts, verified: t.verified,
        input_tokens: t.input_tokens, output_tokens: t.output_tokens, memory: t.memory } },
  ]);
}

export default function Ask() {
  const { confirm, prompt, toast } = useUI();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [home, setHome] = useState<Home | null>(null);
  const [convs, setConvs] = useState<Conv[]>([]);
  const [convId, setConvId] = useState<number | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [listShown, setListShown] = usePref("conversations-shown", true);
  const convRef = useRef<number | null>(null); // current conversation, readable inside the streaming loop
  const bottom = useRef<HTMLDivElement>(null);
  const started = useRef(false);

  const loadHome = useCallback(() => apiJson<Home>("/api/home").then(setHome).catch(() => {}), []);
  const loadConvs = useCallback(() => apiJson<Conv[]>("/api/conversations").then(setConvs).catch(() => {}), []);

  const select = useCallback((id: number | null) => {
    convRef.current = id;
    setConvId(id);
    history.replaceState(null, "", id ? `/?c=${id}` : "/"); // the address identifies the conversation, so reload restores it
  }, []);

  const open = useCallback(async (id: number) => {
    try {
      const c = await apiJson<{ turns: Turn[] }>(`/api/conversations/${id}`);
      setMsgs(turnsToMsgs(c.turns));
      select(id);
    } catch {
      select(null);
      setMsgs([]);
      toast("That conversation isn't available.", "error");
    }
    setHistoryOpen(false);
  }, [select, toast]);

  function newConversation() {
    select(null);
    setMsgs([]);
    setHistoryOpen(false);
  }

  function patchLast(fn: (m: Assistant) => Partial<Assistant>) {
    setMsgs((prev) => {
      const last = prev[prev.length - 1];
      if (last?.role !== "assistant") return prev;
      return [...prev.slice(0, -1), { ...last, ...fn(last) }];
    });
  }

  const ask = useCallback(async (question: string, analyze?: string) => {
    question = question.trim();
    if (!question) return;
    setInput("");
    setBusy(true);
    setMsgs((p) => [...p, { role: "user", content: question }, { role: "assistant", content: "", steps: [], sources: [], charts: [], pending: true }]);
    try {
      // the server rebuilds context from the saved conversation, including the queries behind earlier answers
      for await (const ev of streamChat(question, [], analyze, convRef.current ?? undefined)) {
        if (ev.type === "conversation") { if (!convRef.current) select(ev.id); }
        else if (ev.type === "text") patchLast((m) => ({ content: m.content + ev.text }));
        else if (ev.type === "sources") patchLast(() => ({ sources: ev.items }));
        else if (ev.type === "done") patchLast(() => ({ done: ev, content: ev.answer || "", pending: false }));
        else if (ev.type === "restart") patchLast((m) => ({ content: "", charts: [], steps: [...m.steps, { type: "thinking", text: ev.reason }] }));
        else if (ev.type === "chart") patchLast((m) => ({ charts: [...m.charts, toChart(ev.chart)] }));
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
      loadConvs();
    }
  }, [loadHome, loadConvs, select]);

  useEffect(() => {
    loadHome();
    loadConvs();
    if (started.current) return;
    started.current = true;
    const q = new URLSearchParams(location.search);
    const table = q.get("analyze"); // "Analyze" from the Library arrives as ?analyze=<table>&title=<name>
    const c = Number(q.get("c"));
    if (table) {
      history.replaceState(null, "", "/");
      ask(`Analyze “${q.get("title") ?? table}” and give me the key business insights.`, table);
    } else if (c) open(c);
  }, [loadHome, loadConvs, ask, open]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" }); // braces: newer Chrome returns a Promise here
  }, [msgs]);

  async function rename(c: Conv) {
    const title = await prompt({ title: "Rename conversation", action: "Rename", input: { label: "Name", value: c.title } });
    if (title && title !== c.title) {
      await api(`/api/conversations/${c.id}`, { method: "PATCH", body: JSON.stringify({ title }) });
      loadConvs();
    }
  }
  async function remove(c: Conv) {
    if (!(await confirm({ title: `Delete “${c.title}”?`, action: "Delete", danger: true, body: "It will be removed from your conversations." }))) return;
    await api(`/api/conversations/${c.id}`, { method: "DELETE" });
    if (convRef.current === c.id) newConversation();
    toast("Conversation deleted.");
    loadConvs();
  }

  const submit = () => !busy && ask(input);
  const list = <ConversationList convs={convs} active={convId} busy={busy} onOpen={open} onNew={newConversation} onRename={rename} onDelete={remove} />;
  const title = convs.find((c) => c.id === convId)?.title;

  return (
    <div className="flex h-dvh">
      {listShown && <aside className="hidden w-60 shrink-0 flex-col border-r border-line bg-nav lg:flex">{list}</aside>}
      {historyOpen && <SidePanel title="Conversations" onClose={() => setHistoryOpen(false)}>{list}</SidePanel>}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-4 border-b border-line bg-panel px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-2">
            <button onClick={() => setListShown(!listShown)} className="hidden rounded p-1.5 text-muted hover:bg-hover hover:text-foreground lg:block"
              aria-label={listShown ? "Hide conversations" : "Show conversations"} title={listShown ? "Hide conversations" : "Show conversations"}>
              <PanelLeft className="h-4 w-4" />
            </button>
            <div className="min-w-0">
              <h1 className="truncate text-sm font-semibold">{title ?? "Ask"}</h1>
              {!convId && <p className="text-xs text-muted">Answers use only the documents, data and apps you have access to.</p>}
            </div>
          </div>
          <div className="flex gap-2">
            {convId && !busy && <ExportMenu id={convId} />}
            <Button className="lg:hidden" icon={<History className="h-4 w-4" />} onClick={() => setHistoryOpen(true)}>Conversations</Button>
            {msgs.length > 0 && <Button className={listShown ? "lg:hidden" : ""} icon={<MessageSquarePlus className="h-4 w-4" />} onClick={newConversation} disabled={busy}>New</Button>}
          </div>
        </header>

        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl space-y-6 px-6 py-6">
            {home && home.ai.length === 0 && <NoAINotice />}
            {msgs.length === 0 && <Start home={home} onAsk={(q) => ask(q)} />}
            {msgs.map((m, i) => (m.role === "user" ? <Question key={i} text={m.content} /> : <AnswerView key={i} m={m} question={msgs[i - 1]?.content ?? ""} />))}
            <div ref={bottom} />
          </div>
        </div>

        <form onSubmit={(e) => { e.preventDefault(); submit(); }} className="border-t border-line bg-panel px-6 py-3">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <label className="sr-only" htmlFor="question">Question</label>
            <textarea id="question" value={input} rows={2} onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
              placeholder={convId ? "Ask a follow-up question" : "Ask a question about your documents, data or connected apps"}
              className="max-h-40 min-h-[52px] flex-1 resize-none rounded border border-line bg-panel px-3 py-2 text-sm outline-none placeholder:text-muted focus:border-accent focus:ring-1 focus:ring-accent" />
            <Button type="submit" variant="primary" loading={busy} disabled={!input.trim()} className="h-[52px] px-5">Ask</Button>
          </div>
          <p className="mx-auto mt-1 max-w-3xl text-xs text-muted">Press Enter to send, Shift+Enter for a new line. Conversations are saved automatically.</p>
        </form>
      </div>
    </div>
  );
}

function ConversationList({ convs, active, busy, onOpen, onNew, onRename, onDelete }: {
  convs: Conv[]; active: number | null; busy: boolean; onOpen: (id: number) => void; onNew: () => void; onRename: (c: Conv) => void; onDelete: (c: Conv) => void;
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-line p-3">
        <Button className="w-full" icon={<MessageSquarePlus className="h-4 w-4" />} onClick={onNew} disabled={busy}>New conversation</Button>
      </div>
      <nav className="flex-1 overflow-y-auto p-2" aria-label="Conversations">
        {convs.length === 0 && <p className="px-2 py-3 text-xs text-muted">Your conversations will appear here.</p>}
        <ul className="space-y-0.5">
          {convs.map((c) => (
            <li key={c.id} className="group relative">
              <button onClick={() => !busy && onOpen(c.id)} aria-current={active === c.id ? "page" : undefined}
                className={`block w-full rounded px-2.5 py-2 pr-14 text-left ${active === c.id ? "bg-accent-weak" : "hover:bg-hover"}`}>
                <span className={`block truncate text-sm ${active === c.id ? "font-medium text-accent" : ""}`}>{c.title}</span>
                <span className="block text-xs text-muted">{ago(c.updated_at)}</span>
              </button>
              <span className="absolute top-2 right-1.5 hidden gap-0.5 group-focus-within:flex group-hover:flex">
                <button onClick={() => onRename(c)} aria-label={`Rename ${c.title}`} title="Rename" className="rounded p-1 text-muted hover:bg-panel hover:text-foreground"><Pencil className="h-3.5 w-3.5" /></button>
                <button onClick={() => onDelete(c)} aria-label={`Delete ${c.title}`} title="Delete" className="rounded p-1 text-muted hover:bg-panel hover:text-bad"><Trash2 className="h-3.5 w-3.5" /></button>
              </span>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}

function NoAINotice() {
  const me = useMe()!;
  return (
    <div className="flex items-start gap-3 rounded border border-line border-l-4 border-l-warn bg-panel p-3 text-sm">
      <Status kind="warning">No AI model</Status>
      <p className="text-text2">
        Answers will be search results and calculated spreadsheet findings, without written explanations.{" "}
        {me.role === "admin" ? <Link href="/settings" className="text-accent underline">Connect Claude or ChatGPT in Settings</Link> : "Ask an administrator to connect an AI model."}
      </p>
    </div>
  );
}

function Start({ home, onAsk }: { home: Home | null; onAsk: (q: string) => void }) {
  const remaining = home?.checklist.filter((c) => !c.done) ?? [];
  return (
    <div className="space-y-6">
      <section className="rounded border border-line bg-panel">
        <h2 className="border-b border-line px-4 py-3 text-sm font-semibold">Suggested questions</h2>
        {home && home.suggestions.length > 0 ? (
          <ul>
            {home.suggestions.map((s) => (
              <li key={s} className="border-b border-line last:border-0">
                <button onClick={() => onAsk(s)} className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm hover:bg-hover">
                  <Search className="h-4 w-4 shrink-0 text-muted" aria-hidden />
                  <span className="flex-1">{s}</span>
                  <ChevronRight className="h-4 w-4 text-muted" aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="px-4 py-3 text-sm text-text2">
            Add documents or spreadsheets in the <Link href="/library" className="text-accent underline">Library</Link> to get suggestions based on your data.
          </p>
        )}
      </section>

      {remaining.length > 0 && home && (
        <section className="rounded border border-line bg-panel">
          <h2 className="border-b border-line px-4 py-3 text-sm font-semibold">Set up your workspace</h2>
          <ul className="divide-y divide-line">
            {home.checklist.map((c) => (
              <li key={c.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                {c.done ? <Status kind="success">Done</Status> : <Status kind="neutral">To do</Status>}
                <span className={c.done ? "text-muted" : ""}>{c.label}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function Question({ text }: { text: string }) {
  return (
    <div className="border-l-2 border-line pl-4">
      <p className="text-xs font-medium text-muted">Question</p>
      <p className="mt-0.5 whitespace-pre-wrap text-sm font-medium">{text}</p>
    </div>
  );
}

function ExportMenu({ id }: { id: number }) {
  const link = (format: "pdf" | "md", label: string) => (
    <a href={`${API}/api/conversations/${id}/export?format=${format}`} download className="block px-3 py-2 text-sm hover:bg-hover">{label}</a>
  );
  return (
    <details className="relative">
      <summary className="inline-flex h-8 cursor-pointer list-none items-center gap-1.5 rounded border border-line bg-panel px-3 text-sm font-medium hover:bg-hover">
        <Download className="h-4 w-4" aria-hidden />Export<ChevronDown className="h-3.5 w-3.5" aria-hidden />
      </summary>
      <div className="absolute right-0 z-20 mt-1 w-40 rounded border border-line bg-panel py-1 shadow-lg">
        {link("pdf", "PDF document")}
        {link("md", "Markdown")}
      </div>
    </details>
  );
}

function ScheduleButton({ question }: { question: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button aria-label="Schedule this" title="Schedule this question as a recurring report" onClick={() => setOpen(true)}
        className="rounded p-1.5 text-muted hover:bg-hover hover:text-foreground"><CalendarClock className="h-4 w-4" /></button>
      {open && <ReportForm question={question} onClose={() => setOpen(false)} onSaved={() => setOpen(false)} />}
    </>
  );
}

function AnswerView({ m, question }: { m: Assistant; question: string }) {
  const critic = [...m.steps].reverse().find((s) => s.type === "critic");
  return (
    <article className="rounded border border-line bg-panel">
      <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
        <h2 className="text-sm font-semibold">Answer</h2>
        {m.pending ? <Status kind="running">Working</Status>
          : m.done?.verified ? <Status kind="success" title="A second AI checked every claim against the sources">Verified</Status>
          : critic && critic.type === "critic" ? <Status kind="warning" title={critic.feedback}>Not fully verified</Status> : null}
        {m.done && (
          <div className="ml-auto flex items-center gap-1">
            <span className="mr-2 text-xs text-muted tabular-nums">{(m.done.latency_ms / 1000).toFixed(1)} s</span>
            <CopyButton text={m.content} />
            {question && <ScheduleButton question={question} />}
            <Feedback runId={m.done.run_id} initial={m.feedback ?? null} />
          </div>
        )}
      </header>

      <div className="space-y-4 px-4 py-4">
        {m.content ? <div className="md text-sm leading-6"><ReactMarkdown remarkPlugins={[remarkGfm]}>{stripIds(m.content)}</ReactMarkdown></div>
          : m.pending && <p className="text-sm text-muted">Searching sources and preparing the answer…</p>}
        {m.charts.map((c) => <Chart key={c.id} data={c} action={<PinButton chart={c} />} />)}
      </div>

      {m.sources.length > 0 && (
        <details className="group border-t border-line">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 px-4 py-2.5 text-xs text-text2 hover:bg-hover">
            <ChevronRight className="h-3.5 w-3.5 shrink-0 transition-transform group-open:rotate-90" aria-hidden />
            <span className="font-medium">Sources ({m.sources.length})</span>
            <span className="truncate text-muted group-open:hidden">
              {[...new Set(m.sources.map((s) => (s.kind === "web" ? s.title : s.document) ?? ""))].slice(0, 3).join(", ")}
              {new Set(m.sources.map((s) => s.document ?? s.title)).size > 3 ? ", …" : ""}
            </span>
          </summary>
          <ol className="space-y-1 px-4 pb-3">
            {m.sources.map((s) => <SourceRow key={s.label} s={s} />)}
          </ol>
        </details>
      )}
      {m.steps.length > 0 && <Trace steps={m.steps} pending={m.pending} />}
    </article>
  );
}

function PinButton({ chart }: { chart: ChartEvent }) {
  const { toast } = useUI();
  const [state, setState] = useState<"idle" | "saving" | "pinned">("idle");
  async function pin() {
    setState("saving");
    const res = await api("/api/dashboard/tiles", { method: "POST", body: JSON.stringify({ title: chart.title, ...chart.spec, source: chart.source, sql: chart.sql }) });
    if (res.ok) {
      setState("pinned");
      toast(`Pinned “${chart.title}” to your dashboard.`);
    } else {
      setState("idle");
      toast((await res.json()).detail ?? "Couldn't pin the chart.", "error");
    }
  }
  return (
    <Button size="sm" variant={state === "pinned" ? "secondary" : "primary"} onClick={pin} loading={state === "saving"} disabled={state === "pinned"}
      title="Add to your dashboard. It re-runs the query each time, so it stays current.">
      {state === "pinned" ? "Pinned" : "Pin to dashboard"}
    </Button>
  );
}

function CopyButton({ text }: { text: string }) {
  const { toast } = useUI();
  return (
    <button aria-label="Copy answer" title="Copy answer" className="rounded p-1.5 text-muted hover:bg-hover hover:text-foreground"
      onClick={() => navigator.clipboard.writeText(text).then(() => toast("Answer copied."))}>
      <Copy className="h-4 w-4" />
    </button>
  );
}

function Feedback({ runId, initial }: { runId: number; initial: number | null }) {
  const { prompt, toast } = useUI();
  const [value, setValue] = useState<number | null>(initial);
  async function send(v: number) {
    let note = "";
    if (v === -1) {
      const n = await prompt({ title: "What was wrong with this answer?", action: "Send feedback", input: { label: "Details (optional)", value: "" },
        body: "Your feedback appears in the Usage page so administrators can fix missing or outdated information." });
      note = n ?? "";
    }
    setValue(v);
    await api(`/api/runs/${runId}/feedback`, { method: "POST", body: JSON.stringify({ value: v, note }) });
    toast("Thanks. Your feedback was recorded.");
  }
  return (
    <>
      <button aria-label="Helpful" aria-pressed={value === 1} title="Helpful" onClick={() => send(1)}
        className={`rounded p-1.5 hover:bg-hover ${value === 1 ? "text-ok" : "text-muted hover:text-foreground"}`}><ThumbsUp className="h-4 w-4" /></button>
      <button aria-label="Not helpful" aria-pressed={value === -1} title="Not helpful" onClick={() => send(-1)}
        className={`rounded p-1.5 hover:bg-hover ${value === -1 ? "text-bad" : "text-muted hover:text-foreground"}`}><ThumbsDown className="h-4 w-4" /></button>
    </>
  );
}

const SOURCE_LABEL: Record<string, string> = { slack: "Slack", gdrive: "Google Drive", notion: "Notion", github: "GitHub", web: "Website", upload: "Uploaded file" };

function SourceRow({ s }: { s: Source }) {
  const [open, setOpen] = useState(false);
  const name = s.kind === "web" ? s.title : s.document;
  const where = s.kind === "web" ? "Web" : s.source && s.source !== "upload" ? SOURCE_LABEL[s.source] ?? s.source : `Uploaded file, page ${s.page}`;
  return (
    <li className="flex items-start gap-2 text-sm">
      <span className="mt-0.5 w-7 shrink-0 font-mono text-xs text-accent">{s.label}</span>
      <span className="min-w-0 flex-1">
        {s.url ? (
          <a href={s.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">
            {name}<ExternalLink className="h-3 w-3" aria-hidden />
          </a>
        ) : s.document_id ? (
          <button onClick={() => setOpen(true)} className="inline-flex items-center gap-1 text-left text-accent hover:underline" title="Preview with the cited passage highlighted">
            <FileText className="h-3.5 w-3.5" aria-hidden />{name}
          </button>
        ) : <span className="inline-flex items-center gap-1"><FileText className="h-3.5 w-3.5 text-muted" aria-hidden />{name}</span>}
        <span className="ml-2 text-xs text-muted">{where}</span>
      </span>
      {open && s.document_id && <DocumentPreview id={s.document_id} highlight={s.content} onClose={() => setOpen(false)} />}
    </li>
  );
}

const TOOL_LABEL: Record<string, string> = {
  search_documents: "Searched documents",
  web_search: "Searched the web",
  list_data_sources: "Listed data sources",
  analyze_spreadsheet: "Analyzed spreadsheet",
  describe_tables: "Read table columns",
  run_sql: "Queried data",
  create_chart: "Created chart",
  code_execution: "Ran a calculation",
  list_metrics: "Listed metrics",
  query_metric: "Calculated a metric",
  forecast: "Forecast",
  what_if: "Calculated a scenario",
};

function Trace({ steps, pending }: { steps: Step[]; pending: boolean }) {
  const n = steps.filter((s) => s.type === "tool").length;
  return (
    <details open={pending} className="group border-t border-line">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 px-4 py-2.5 text-xs font-medium text-text2 hover:bg-hover">
        <ChevronRight className="h-3.5 w-3.5 transition-transform group-open:rotate-90" aria-hidden />
        How this answer was produced ({n} {n === 1 ? "step" : "steps"})
      </summary>
      <ol className="space-y-2 px-4 pt-1 pb-3 text-xs">
        {steps.map((s, i) => <li key={i}><StepView s={s} /></li>)}
      </ol>
    </details>
  );
}

function StepView({ s }: { s: Step }) {
  switch (s.type) {
    case "thinking":
      return <p className="whitespace-pre-wrap text-muted">{s.text}</p>;
    case "tool":
      return (
        <p className="flex flex-wrap items-baseline gap-2">
          <span className="font-medium text-foreground">{s.name.startsWith("mcp:") ? `Used app tool ${s.name.slice(4)}` : TOOL_LABEL[s.name] ?? s.name}</span>
          <code className="font-mono text-muted">{fmtInput(s.input)}</code>
        </p>
      );
    case "tool_result":
      return <p className={`line-clamp-2 pl-3 font-mono ${s.error ? "text-bad" : "text-muted"}`}>{s.summary}</p>;
    case "critic":
      return (
        <div className="flex flex-wrap items-center gap-2">
          <Status kind={s.passed ? "success" : "warning"}>Fact-check {s.attempt}: {s.passed ? "passed" : "issues found"}</Status>
          {!s.passed && <span className="text-text2">{s.feedback}</span>}
        </div>
      );
    case "revising":
      return <Status kind="info">Rewriting answer (attempt {s.attempt})</Status>;
    case "error":
      return <Status kind="error">{s.message}</Status>;
  }
}

function fmtInput(input: unknown) {
  if (input && typeof input === "object") {
    const o = input as Record<string, unknown>;
    return String(o.query ?? o.name ?? o.title ?? o.table ?? o.command ?? o.code ?? (Array.isArray(o.tables) ? o.tables.join(", ") : "") ?? "").slice(0, 200);
  }
  return String(input ?? "");
}
