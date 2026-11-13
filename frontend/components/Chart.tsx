"use client";

import { Download } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "./ui";

export type ChartSpec = { type: "line" | "bar" | "number" | "table"; x?: string | null; y: string[] };
export type ChartData = { title: string; spec: ChartSpec; columns: string[]; rows: unknown[][]; truncated?: boolean };

const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"];
const H = 240;
const M = { top: 12, right: 16, bottom: 28, left: 48 };

const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const full = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** "2026-03-01" → "Mar 2026" (month starts), "2026-03-15" → "Mar 15", anything else unchanged. */
export function fmtLabel(v: string) {
  const m = /^(\d{4})-(\d{2})(?:-(\d{2}))?/.exec(v);
  if (!m) return v;
  const mon = MONTHS[+m[2] - 1];
  return !m[3] || m[3] === "01" ? `${mon} ${m[1]}` : `${mon} ${+m[3]}`;
}

export const fmt = (v: unknown) => (typeof v === "number" ? (Math.abs(v) >= 10000 ? compact.format(v) : full.format(v)) : String(v ?? "–"));

function niceTicks(min: number, max: number, count = 4): number[] {
  if (min === max) max = min + 1;
  const step0 = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0)!;
  const lo = Math.floor(min / step) * step;
  const out: number[] = [];
  for (let v = lo; ; v += step) {
    out.push(+v.toFixed(10));
    if (v >= max - 1e-9) break; // the top tick must reach the largest value, or bars overflow the plot
  }
  return out;
}

export function toCSV(columns: string[], rows: unknown[][]) {
  const cell = (v: unknown) => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns, ...rows].map((r) => r.map(cell).join(",")).join("\n");
}

export function downloadCSV(title: string, columns: string[], rows: unknown[][]) {
  const url = URL.createObjectURL(new Blob([toCSV(columns, rows)], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: `${title.replace(/[^\w-]+/g, "_").slice(0, 60) || "data"}.csv` });
  a.click();
  URL.revokeObjectURL(url);
}

/** A chart with its own toolbar: table view (always available), CSV download, and an optional action (e.g. Pin). */
export default function Chart({ data, action, compactHeader }: { data: ChartData; action?: React.ReactNode; compactHeader?: boolean }) {
  const [asTable, setAsTable] = useState(data.spec.type === "table");
  const canChart = data.spec.type !== "table";
  return (
    <figure className="rounded border border-line bg-panel">
      <figcaption className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <span className={`min-w-0 flex-1 text-sm font-semibold ${compactHeader ? "truncate" : ""}`}>{data.title}</span>
        <span className="flex items-center gap-1">
          {canChart && <Button size="sm" variant="ghost" onClick={() => setAsTable(!asTable)}>{asTable ? "Chart view" : "Table view"}</Button>}
          <Button size="sm" variant="ghost" icon={<Download className="h-3.5 w-3.5" />} onClick={() => downloadCSV(data.title, data.columns, data.rows)}
            title="Download as CSV (opens in Excel or Google Sheets)">CSV</Button>
          {action}
        </span>
      </figcaption>
      <div className="p-3">
        {asTable || !canChart ? <DataTable columns={data.columns} rows={data.rows} /> : data.spec.type === "number" ? <Stat data={data} /> : <Plot data={data} />}
        {data.truncated && <p className="mt-1 text-xs text-muted">Showing the first {data.rows.length.toLocaleString()} rows.</p>}
      </div>
    </figure>
  );
}

function DataTable({ columns, rows }: { columns: string[]; rows: unknown[][] }) {
  return (
    <div className="max-h-72 overflow-auto">
      <table className="data-table">
        <thead className="sticky top-0">
          <tr>{columns.map((c, j) => <th key={c} className={typeof rows[0]?.[j] === "number" ? "num" : ""}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.slice(0, 500).map((r, i) => (
            <tr key={i}>{r.map((v, j) => <td key={j} className={typeof v === "number" ? "num" : ""}>{fmt(v)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Stat({ data }: { data: ChartData }) {
  const idx = data.columns.indexOf(data.spec.y[0]);
  const values = data.rows.map((r) => r[idx]).filter((v): v is number => typeof v === "number");
  const value = values.length > 1 ? values[values.length - 1] : values[0];
  return (
    <div className="py-2">
      <div className="text-[42px] leading-[50px] font-light tabular-nums">{fmt(value)}</div>
      <div className="mt-1 text-xs text-text2">{data.spec.y[0].replace(/_/g, " ")}</div>
    </div>
  );
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(600);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function Plot({ data }: { data: ChartData }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const { spec } = data;
  const xi = data.columns.indexOf(spec.x ?? "");
  const ys = spec.y.map((y) => data.columns.indexOf(y));
  const labels = data.rows.map((r) => fmtLabel(String(r[xi] ?? "")));
  const series = ys.map((i) => data.rows.map((r) => (typeof r[i] === "number" ? (r[i] as number) : null)));
  const all = series.flat().filter((v): v is number => v != null);
  const ticks = niceTicks(Math.min(0, ...all), Math.max(0, ...all));
  const lo = ticks[0], hi = ticks[ticks.length - 1];
  const iw = width - M.left - M.right, ih = H - M.top - M.bottom;
  const yScale = (v: number) => M.top + ih - ((v - lo) / (hi - lo || 1)) * ih;
  const n = labels.length;
  const band = iw / Math.max(n, 1);
  const xCenter = (i: number) => M.left + (spec.type === "line" ? (n === 1 ? iw / 2 : (i / (n - 1)) * iw) : band * i + band / 2);
  const everyNth = Math.ceil(n / Math.max(1, Math.floor(iw / 72)));
  const maxChars = Math.max(6, Math.floor((band * everyNth) / 6.5)); // fit labels to the space they actually get
  const zero = yScale(Math.max(lo, Math.min(0, hi)));

  const nearest = (clientX: number, rect: DOMRect) => {
    const x = clientX - rect.left - M.left;
    const i = spec.type === "line" ? Math.round((x / iw) * (n - 1)) : Math.floor(x / band);
    return Math.max(0, Math.min(n - 1, i));
  };

  // bars: grouped, ≤24px thick, 2px surface gap between neighbours, 4px rounded data end
  const groupW = Math.min(band * 0.8, 24 * ys.length + 2 * (ys.length - 1));
  const barW = Math.max(2, (groupW - 2 * (ys.length - 1)) / ys.length);
  const barPath = (x: number, v: number) => {
    const y0 = zero, y1 = yScale(v), r = Math.min(4, barW / 2, Math.abs(y0 - y1));
    const up = v >= 0, top = up ? y1 : y0, bottom = up ? y0 : y1;
    return up
      ? `M${x},${bottom}V${top + r}Q${x},${top} ${x + r},${top}H${x + barW - r}Q${x + barW},${top} ${x + barW},${top + r}V${bottom}Z`
      : `M${x},${top}V${bottom - r}Q${x},${bottom} ${x + r},${bottom}H${x + barW - r}Q${x + barW},${bottom} ${x + barW},${bottom - r}V${top}Z`;
  };

  return (
    <div ref={ref} className="relative">
      {ys.length > 1 && (
        <ul className="mb-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted" aria-label="Legend">
          {spec.y.map((name, k) => (
            <li key={name} className="flex items-center gap-1.5">
              {spec.type === "line" ? <span className="h-0.5 w-3 rounded" style={{ background: SERIES[k] }} /> : <span className="h-2.5 w-2.5 rounded-sm" style={{ background: SERIES[k] }} />}
              {name}
            </li>
          ))}
        </ul>
      )}
      <svg
        width={width} height={H} role="img" tabIndex={0}
        aria-label={`${spec.type} chart: ${data.title}. Use the Table button for exact values.`}
        className="block outline-none focus-visible:ring-2 focus-visible:ring-accent"
        onPointerMove={(e) => setHover(nearest(e.clientX, e.currentTarget.getBoundingClientRect()))}
        onPointerLeave={() => setHover(null)}
        onBlur={() => setHover(null)}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight") setHover((h) => Math.min(n - 1, (h ?? -1) + 1));
          if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? n) - 1));
        }}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={width - M.right} y1={yScale(t)} y2={yScale(t)} stroke="var(--grid)" strokeWidth={1} />
            <text x={M.left - 6} y={yScale(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--muted)">{fmt(t)}</text>
          </g>
        ))}
        {labels.map((l, i) => i % everyNth === 0 && (
          <text key={i} x={xCenter(i)} y={H - 8} textAnchor="middle" fontSize={11} fill="var(--muted)">{l.length > maxChars ? l.slice(0, maxChars - 1) + "…" : l}</text>
        ))}

        {spec.type === "line" && hover != null && <line x1={xCenter(hover)} x2={xCenter(hover)} y1={M.top} y2={M.top + ih} stroke="var(--muted)" strokeWidth={1} />}

        {spec.type === "line" && series.map((vals, k) => {
          const pts = vals.map((v, i) => (v == null ? null : [xCenter(i), yScale(v)] as const));
          const d = pts.reduce((acc, p, i) => (p ? acc + `${acc && pts[i - 1] ? "L" : "M"}${p[0]},${p[1]}` : acc), "");
          const lastI = vals.map((v, i) => (v == null ? -1 : i)).reduce((a, b) => Math.max(a, b), -1);
          return (
            <g key={k}>
              <path d={d} fill="none" stroke={SERIES[k]} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              {lastI >= 0 && <circle cx={xCenter(lastI)} cy={yScale(vals[lastI]!)} r={4} fill={SERIES[k]} stroke="var(--panel)" strokeWidth={2} />}
              {hover != null && vals[hover] != null && <circle cx={xCenter(hover)} cy={yScale(vals[hover]!)} r={4} fill={SERIES[k]} stroke="var(--panel)" strokeWidth={2} />}
            </g>
          );
        })}

        {spec.type === "bar" && series.map((vals, k) => vals.map((v, i) => v == null ? null : (
          <path key={`${k}-${i}`} d={barPath(xCenter(i) - groupW / 2 + k * (barW + 2), v)} fill={SERIES[k]}
            opacity={hover == null || hover === i ? 1 : 0.55} />
        )))}
        {spec.type === "bar" && <line x1={M.left} x2={width - M.right} y1={zero} y2={zero} stroke="var(--muted)" strokeWidth={1} />}
      </svg>

      {hover != null && (
        <div role="status" className="pointer-events-none absolute top-6 z-10 min-w-32 rounded-md border border-line bg-panel px-2.5 py-1.5 text-xs shadow-md"
          style={{ left: Math.min(Math.max(0, xCenter(hover) + 12), width - 150) }}>
          <div className="mb-1 text-muted">{labels[hover]}</div>
          {series.map((vals, k) => (
            <div key={k} className="flex items-center gap-2">
              <span className="h-0.5 w-3 rounded" style={{ background: SERIES[k] }} />
              <span className="font-semibold tabular-nums">{fmt(vals[hover])}</span>
              {ys.length > 1 && <span className="text-muted">{spec.y[k]}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
