"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import Chart, { type ChartSpec } from "@/components/Chart";
import { api, apiJson } from "@/lib/api";
import { useMe } from "@/lib/me";
import { ago } from "@/lib/time";

type Tile = { id: number; title: string; spec: ChartSpec; source: string; shared: boolean; mine: boolean; owner: string };
type TileData = { columns: string[]; rows: unknown[][]; truncated?: boolean; refreshed_at?: string; error?: string | null; stale?: boolean; no_access?: boolean };

export default function Dashboard() {
  const [tiles, setTiles] = useState<Tile[] | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => apiJson<Tile[]>("/api/dashboard").then(setTiles).catch((e) => setError(e.message)), []);
  useEffect(() => {
    load();
  }, [load]);

  if (error) return <p className="p-6 text-bad">{error}</p>;
  if (!tiles) return <p className="p-6 text-sm text-muted">Loading…</p>;

  const mine = tiles.filter((t) => t.mine);
  const shared = tiles.filter((t) => !t.mine);
  return (
    <main className="mx-auto max-w-6xl space-y-8 px-4 py-6">
      <div>
        <h1 className="text-lg font-semibold">Dashboard</h1>
        <p className="mt-1 text-sm text-muted">Pinned charts re-run their query when you open them (cached for 15 minutes), so the numbers stay current at no AI cost.</p>
      </div>
      {tiles.length === 0 && (
        <section className="rounded-lg border border-dashed border-line p-6 text-sm">
          <h2 className="font-medium">Build your first dashboard in three steps</h2>
          <ol className="mt-3 list-decimal space-y-1.5 pl-5 text-muted">
            <li>Make sure there&apos;s data to ask about: upload a CSV in <Link href="/" className="text-accent underline">Chat</Link>, or have an admin connect BigQuery, Snowflake or Postgres.</li>
            <li>Ask a numbers question, like <em>&ldquo;Show revenue by month for this year&rdquo;</em>.</li>
            <li>Click <b>Pin to dashboard</b> on the chart. Share it with your team from here.</li>
          </ol>
        </section>
      )}
      {mine.length > 0 && <Grid title="My charts" tiles={mine} onChange={load} />}
      {shared.length > 0 && <Grid title="Shared with everyone" tiles={shared} onChange={load} />}
    </main>
  );
}

function Grid({ title, tiles, onChange }: { title: string; tiles: Tile[]; onChange: () => void }) {
  return (
    <section>
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">{title}</h2>
      <div className="grid gap-4 lg:grid-cols-2">
        {tiles.map((t) => <TileCard key={t.id} tile={t} onChange={onChange} />)}
      </div>
    </section>
  );
}

function TileCard({ tile, onChange }: { tile: Tile; onChange: () => void }) {
  const me = useMe()!;
  const [data, setData] = useState<TileData | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async (refresh = false) => {
    setLoading(true);
    try {
      setData(await apiJson<TileData>(`/api/dashboard/tiles/${tile.id}/data${refresh ? "?refresh=true" : ""}`));
    } catch (e) {
      setData({ columns: [], rows: [], error: (e as Error).message });
    } finally {
      setLoading(false);
    }
  }, [tile.id]);
  useEffect(() => {
    fetchData();
  }, [fetchData]);

  async function patch(body: object) {
    await api(`/api/dashboard/tiles/${tile.id}`, { method: "PATCH", body: JSON.stringify(body) });
    onChange();
  }
  async function remove() {
    if (!confirm(`Remove "${tile.title}" from the dashboard?`)) return;
    await api(`/api/dashboard/tiles/${tile.id}`, { method: "DELETE" });
    onChange();
  }

  const canEdit = tile.mine || me.role === "admin";
  return (
    <div className="space-y-1.5">
      {data?.no_access ? (
        <div className="rounded-lg border border-line bg-panel p-4 text-sm">
          <p className="font-medium">{tile.title}</p>
          <p className="mt-1 text-muted">You don&apos;t have access to the data source behind this chart. Ask an admin if you need it.</p>
        </div>
      ) : data ? (
        <Chart compactHeader data={{ title: tile.title, spec: tile.spec, columns: data.columns, rows: data.rows, truncated: data.truncated }}
          action={<button onClick={() => fetchData(true)} disabled={loading} className="rounded border border-line px-2 py-0.5 text-muted hover:text-foreground disabled:opacity-50">{loading ? "…" : "Refresh"}</button>} />
      ) : (
        <div className="h-64 animate-pulse rounded-lg border border-line bg-panel" />
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-xs text-muted">
        {data?.refreshed_at && <span>Updated {ago(data.refreshed_at)}</span>}
        {data?.error && <span className="text-bad" title={data.error}>{data.stale ? "Couldn't refresh: showing last result" : "Query failed"} ⓘ</span>}
        {!tile.mine && <span>by {tile.owner}</span>}
        <span className="ml-auto flex gap-3">
          {tile.mine && (
            <button onClick={() => patch({ shared: !tile.shared })} className="hover:text-foreground" title="Shared charts are visible to everyone, but each person only sees data they have access to">
              {tile.shared ? "Shared ✓" : "Share"}
            </button>
          )}
          {canEdit && <button onClick={() => { const t = prompt("Rename chart", tile.title); if (t?.trim()) patch({ title: t.trim() }); }} className="hover:text-foreground">Rename</button>}
          {canEdit && <button onClick={remove} className="hover:text-bad">Remove</button>}
        </span>
      </div>
    </div>
  );
}
