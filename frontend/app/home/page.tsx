"use client";

import { ArrowUp, BellRing, TrendingDown, TrendingUp, CalendarClock, ChevronRight, LayoutDashboard, MessageSquareText, Plug, Sigma, TriangleAlert, Upload } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Skeleton, Status } from "@/components/ui";
import { apiJson } from "@/lib/api";
import { useMe } from "@/lib/me";
import { ago } from "@/lib/time";

type Home = { checklist: { id: string; label: string; done: boolean }[]; suggestions: string[]; ai: string[] };
type Conv = { id: number; title: string; updated_at: string; questions: number };
type Metric = { id: number; name: string; description: string; format: "currency" | "number" | "percent" };
type Alert = { id: number; name: string; enabled: boolean; last_state: "ok" | "triggered" | "error" | null; last_triggered_at: string | null };
type Report = { id: number; title: string; enabled: boolean; next_run_at: string | null; last_status: "ok" | "error" | null; last_error: string | null };
type Connection = { id: number; name: string; status: string; error: string | null };
type Tile = { id: number; title: string };

const askUrl = (q: string) => `/?q=${encodeURIComponent(q)}`;

type Point = { period: string; value: number | null };
type Kpi = { value: number | null; label: string; change?: number | null; vs?: string; spark?: number[] };

const monthName = (p: string) => new Date(`${p}-15`).toLocaleDateString(undefined, { month: "short", year: "numeric" });

/** Latest month, compared with the same month a year earlier (like for like), else the month before. */
function kpi(points: Point[]): Kpi {
  const last = points[points.length - 1];
  const yearAgo = points.find((p) => p.period === `${+last.period.slice(0, 4) - 1}${last.period.slice(4)}`);
  const base = yearAgo ?? points[points.length - 2];
  const change = base?.value && last.value !== null ? (last.value - base.value) / Math.abs(base.value) : null;
  return { value: last.value, label: monthName(last.period), change, vs: base ? (yearAgo ? "vs last year" : "vs prior month") : undefined,
    spark: points.slice(-13).map((p) => p.value ?? 0) };
}

function Spark({ data }: { data: number[] }) {
  if (data.length < 2) return null;
  const min = Math.min(...data), max = Math.max(...data), span = max - min || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * 100},${28 - ((v - min) / span) * 24}`).join(" ");
  return (
    <svg viewBox="0 0 100 30" preserveAspectRatio="none" className="h-8 w-24" aria-hidden>
      <polyline points={pts} fill="none" stroke="var(--series-1)" strokeWidth="1.75" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      <circle cx="100" cy={28 - ((data[data.length - 1] - min) / span) * 24} r="2.5" fill="var(--series-1)" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function fmt(v: number | null, format: Metric["format"]) {
  if (v === null || Number.isNaN(v)) return "—";
  const compact = Math.abs(v) >= 10000;
  if (format === "currency") return v.toLocaleString(undefined, { style: "currency", currency: "USD", notation: compact ? "compact" : "standard", maximumFractionDigits: compact ? 1 : 0 });
  if (format === "percent") return `${(Math.abs(v) <= 1 ? v * 100 : v).toFixed(1)}%`;
  return v.toLocaleString(undefined, { notation: compact ? "compact" : "standard", maximumFractionDigits: 1 });
}

/** Landing page: the numbers that matter, what needs attention, and a way back into recent work. */
export default function HomePage() {
  const me = useMe();
  const [home, setHome] = useState<Home | null>(null);
  const [convs, setConvs] = useState<Conv[] | null>(null);
  const [metrics, setMetrics] = useState<Metric[] | null>(null);
  const [values, setValues] = useState<Record<number, Kpi>>({});
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [tiles, setTiles] = useState<Tile[]>([]);
  const [q, setQ] = useState("");

  useEffect(() => {
    apiJson<Home>("/api/home").then(setHome).catch(() => {});
    apiJson<Conv[]>("/api/conversations").then(setConvs).catch(() => setConvs([]));
    apiJson<Alert[]>("/api/alerts").then(setAlerts).catch(() => {});
    apiJson<Report[]>("/api/reports").then(setReports).catch(() => {});
    apiJson<Tile[]>("/api/dashboard").then(setTiles).catch(() => {});
    apiJson<Metric[]>("/api/metrics").then((ms) => {
      const top = ms.slice(0, 4);
      setMetrics(top);
      for (const m of top) {
        apiJson<{ points: Point[] }>(`/api/metrics/${m.id}/trend`)
          .then(async ({ points }) => points.length ? kpi(points)
            : { value: (await apiJson<{ value: number | null }>(`/api/metrics/${m.id}/test`, { method: "POST" })).value, label: "All time" })
          .then((k) => setValues((v) => ({ ...v, [m.id]: k })))
          .catch(() => setValues((v) => ({ ...v, [m.id]: { value: null, label: "Not available" } })));
      }
    }).catch(() => setMetrics([]));
  }, []);

  useEffect(() => {
    if (me?.role === "admin") apiJson<{ connections: Connection[] }>("/api/connectors").then((d) => setConnections(d.connections)).catch(() => {});
  }, [me?.role]);

  if (!me) return null;
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const name = me.email.split("@")[0].split(/[._-]/)[0];

  const attention = [
    ...alerts.filter((a) => a.enabled && a.last_state === "triggered").map((a) => ({ key: `a${a.id}`, kind: "warning" as const, label: "Alert", text: a.name,
      meta: a.last_triggered_at ? `Triggered ${ago(a.last_triggered_at)}` : "Triggered", href: "/alerts" })),
    ...alerts.filter((a) => a.enabled && a.last_state === "error").map((a) => ({ key: `ae${a.id}`, kind: "error" as const, label: "Alert", text: a.name, meta: "Couldn't check", href: "/alerts" })),
    ...reports.filter((r) => r.enabled && r.last_status === "error").map((r) => ({ key: `r${r.id}`, kind: "error" as const, label: "Report", text: r.title,
      meta: r.last_error ?? "Last run failed", href: "/reports" })),
    ...connections.filter((c) => c.status === "error").map((c) => ({ key: `c${c.id}`, kind: "error" as const, label: "Connector", text: c.name,
      meta: c.error ?? "Sync failed", href: "/connectors" })),
  ];
  const upcoming = reports.filter((r) => r.enabled && r.next_run_at).sort((a, b) => a.next_run_at!.localeCompare(b.next_run_at!)).slice(0, 4);
  const setup = home?.checklist.filter((c) => !c.done) ?? [];

  return (
    <div>
      {/* hero band: greeting + the primary action, on the panel surface so it reads as the top of the page */}
      <section className="border-b border-line bg-panel">
        <div className="mx-auto max-w-6xl px-6 pt-7 pb-6">
          <p className="text-sm text-text2">{new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}</p>
          <h1 className="mt-0.5 text-[28px] leading-9 font-semibold tracking-[-0.015em]">{greeting}, <span className="capitalize">{name}</span></h1>
          <form onSubmit={(e) => { e.preventDefault(); if (q.trim()) location.href = askUrl(q.trim()); }}
            className="mt-5 flex items-center gap-2 rounded-md border border-line bg-background p-1.5 pl-4 focus-within:border-accent focus-within:ring-1 focus-within:ring-accent">
            <MessageSquareText className="h-5 w-5 shrink-0 text-muted" aria-hidden />
            <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Ask a question"
              placeholder="Ask about your business, e.g. “How did revenue compare with last year?”"
              className="h-9 flex-1 bg-transparent text-[15px] outline-none placeholder:text-muted" />
            <button type="submit" disabled={!q.trim()} aria-label="Ask"
              className="grid h-9 w-9 place-items-center rounded bg-accent text-white hover:bg-accent-hover disabled:opacity-40"><ArrowUp className="h-4 w-4" /></button>
          </form>
          {home && home.suggestions.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {home.suggestions.slice(0, 4).map((s) => (
                <a key={s} href={askUrl(s)} className="rounded-full border border-line bg-panel px-3 py-1 text-xs text-text2 hover:border-accent hover:text-accent">{s}</a>
              ))}
            </div>
          )}
        </div>
      </section>

      <div className="mx-auto max-w-6xl space-y-6 px-6 py-6">
        {/* key numbers */}
        <section aria-labelledby="kpi">
          <div className="mb-2 flex items-baseline justify-between">
            <h2 id="kpi" className="text-sm font-semibold">Key numbers</h2>
            <Link href="/metrics" className="text-xs text-accent hover:underline">{metrics?.length ? "All metrics" : "Define metrics"}</Link>
          </div>
          {metrics === null ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-[104px]" />)}</div>
          ) : metrics.length === 0 ? (
            <div className="flex flex-wrap items-center gap-3 rounded-md border border-dashed border-line bg-panel px-4 py-4 text-sm text-text2">
              <Sigma className="h-5 w-5 text-muted" aria-hidden />
              <span className="flex-1">Define revenue, margin and other key numbers once, and they show up here for everyone, calculated the same way.</span>
              {me.role === "admin" && <Link href="/metrics" className="font-medium text-accent hover:underline">Create a metric</Link>}
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {metrics.map((m) => (
                <a key={m.id} href={askUrl(`How has ${m.name} changed compared with the same period last year, and what's driving it?`)}
                  className="group rounded-md border border-line bg-panel p-4 shadow-raised hover:border-accent">
                  <div className="flex items-baseline justify-between gap-2">
                    <p className="truncate text-xs font-medium text-text2" title={m.description || m.name}>{m.name}</p>
                    {values[m.id] && <p className="shrink-0 text-xs text-muted">{values[m.id].label}</p>}
                  </div>
                  <div className="mt-2 flex items-end justify-between gap-2">
                    <div className="text-[26px] leading-8 font-semibold tabular-nums">
                      {m.id in values ? fmt(values[m.id].value, m.format) : <Skeleton className="h-8 w-24" />}
                    </div>
                    {values[m.id]?.spark && <Spark data={values[m.id].spark!} />}
                  </div>
                  <div className="mt-2 flex items-center gap-1.5 text-xs">
                    {values[m.id]?.change != null ? (
                      <>
                        <span className={`inline-flex items-center gap-0.5 font-medium tabular-nums ${values[m.id].change! >= 0 ? "text-ok" : "text-bad"}`}>
                          {values[m.id].change! >= 0 ? <TrendingUp className="h-3.5 w-3.5" aria-hidden /> : <TrendingDown className="h-3.5 w-3.5" aria-hidden />}
                          {values[m.id].change! >= 0 ? "+" : "−"}{Math.abs(values[m.id].change! * 100).toFixed(1)}%
                        </span>
                        <span className="text-muted">{values[m.id].vs}</span>
                      </>
                    ) : <span className="text-muted">&nbsp;</span>}
                    <span className="ml-auto flex items-center text-muted group-hover:text-accent">Explain<ChevronRight className="h-3 w-3" aria-hidden /></span>
                  </div>
                </a>
              ))}
            </div>
          )}
        </section>

        <div className="grid gap-6 lg:grid-cols-3">
          <div className="space-y-6 lg:col-span-2">
            {attention.length > 0 && (
              <Card title="Needs attention" icon={<TriangleAlert className="h-4 w-4 text-warn" />}>
                <ul className="divide-y divide-line">
                  {attention.map((a) => (
                    <li key={a.key}>
                      <Link href={a.href} className="flex items-center gap-3 px-4 py-2.5 hover:bg-hover">
                        <Status kind={a.kind}>{a.label}</Status>
                        <span className="min-w-0 flex-1 truncate text-sm font-medium">{a.text}</span>
                        <span className="max-w-[45%] truncate text-xs text-muted">{a.meta}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            <Card title="Continue where you left off" icon={<MessageSquareText className="h-4 w-4 text-muted" />} action={<Link href="/" className="text-xs text-accent hover:underline">New question</Link>}>
              {convs === null ? <div className="space-y-2 p-4"><Skeleton className="h-5" /><Skeleton className="h-5" /><Skeleton className="h-5" /></div>
                : convs.length === 0 ? <p className="px-4 py-4 text-sm text-text2">Your conversations will appear here. Ask a question above to start.</p>
                : (
                  <ul className="divide-y divide-line">
                    {convs.slice(0, 6).map((c) => (
                      <li key={c.id}>
                        <Link href={`/?c=${c.id}`} className="flex items-center gap-3 px-4 py-2.5 hover:bg-hover">
                          <span className="min-w-0 flex-1 truncate text-sm">{c.title}</span>
                          <span className="shrink-0 text-xs text-muted tabular-nums">{c.questions} {c.questions === 1 ? "question" : "questions"} · {ago(c.updated_at)}</span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
            </Card>
          </div>

          <div className="space-y-6">
            {setup.length > 0 && home && (
              <Card title="Finish setting up" action={<span className="text-xs text-muted tabular-nums">{home.checklist.length - setup.length} of {home.checklist.length} done</span>}>
                <div className="px-4 pt-3"><div className="h-1.5 overflow-hidden rounded-full bg-hover">
                  <div className="h-full rounded-full bg-accent" style={{ width: `${((home.checklist.length - setup.length) / home.checklist.length) * 100}%` }} />
                </div></div>
                <ul className="space-y-1 px-2 py-2">
                  {setup.map((c) => (
                    <li key={c.id}>
                      <Link href={c.id === "knowledge" ? "/library" : c.id === "team" ? "/settings" : c.id === "pin" ? "/dashboard" : "/"}
                        className="flex items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-hover">
                        <span className="h-3.5 w-3.5 shrink-0 rounded-full border-2 border-line" aria-hidden />
                        <span className="flex-1">{c.label}</span>
                        <ChevronRight className="h-3.5 w-3.5 text-muted" aria-hidden />
                      </Link>
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            <Card title="Coming up" icon={<CalendarClock className="h-4 w-4 text-muted" />} action={<Link href="/reports" className="text-xs text-accent hover:underline">Reports</Link>}>
              {upcoming.length === 0 ? (
                <p className="px-4 py-4 text-sm text-text2">No scheduled reports. Use the calendar button on any answer to get it again every week.</p>
              ) : (
                <ul className="divide-y divide-line">
                  {upcoming.map((r) => (
                    <li key={r.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                      <span className="min-w-0 flex-1 truncate">{r.title}</span>
                      <span className="shrink-0 text-xs text-muted">{new Date(r.next_run_at!).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card title="Shortcuts">
              <ul className="grid grid-cols-2 gap-px bg-line">
                {[
                  { href: "/library", label: "Upload a file", Icon: Upload },
                  { href: "/dashboard", label: `Dashboard${tiles.length ? ` (${tiles.length})` : ""}`, Icon: LayoutDashboard },
                  { href: "/alerts", label: `Alerts${alerts.length ? ` (${alerts.length})` : ""}`, Icon: BellRing },
                  me.role === "admin" ? { href: "/connectors", label: "Connect an app", Icon: Plug } : { href: "/metrics", label: "Metrics", Icon: Sigma },
                ].map(({ href, label, Icon }) => (
                  <li key={href} className="bg-panel">
                    <Link href={href} className="flex items-center gap-2 px-4 py-3 text-sm hover:bg-hover"><Icon className="h-4 w-4 text-accent" aria-hidden />{label}</Link>
                  </li>
                ))}
              </ul>
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}

function Card({ title, icon, action, children }: { title: string; icon?: React.ReactNode; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="overflow-hidden rounded-md border border-line bg-panel shadow-raised">
      <header className="flex items-center gap-2 border-b border-line px-4 py-2.5">
        {icon}
        <h2 className="flex-1 text-sm font-semibold">{title}</h2>
        {action}
      </header>
      {children}
    </section>
  );
}
