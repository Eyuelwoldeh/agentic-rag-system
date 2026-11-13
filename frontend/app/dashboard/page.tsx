"use client";

import { LayoutDashboard, Pencil, RefreshCw, Share2, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import Chart, { type ChartSpec } from "@/components/Chart";
import { Button, EmptyState, Page, PageHeader, Skeleton, Status, useUI } from "@/components/ui";
import { api, apiJson } from "@/lib/api";
import { useMe } from "@/lib/me";
import { ago } from "@/lib/time";

type Tile = { id: number; title: string; spec: ChartSpec; source: string; shared: boolean; mine: boolean; owner: string };
type TileData = { columns: string[]; rows: unknown[][]; truncated?: boolean; refreshed_at?: string; error?: string | null; stale?: boolean; no_access?: boolean };

export default function Dashboard() {
  const { toast } = useUI();
  const [tiles, setTiles] = useState<Tile[] | null>(null);
  const [tab, setTab] = useState<"mine" | "shared">("mine");
  const load = useCallback(() => apiJson<Tile[]>("/api/dashboard").then(setTiles).catch((e) => toast(e.message, "error")), [toast]);
  useEffect(() => {
    load();
  }, [load]);

  const mine = tiles?.filter((t) => t.mine) ?? [];
  const shared = tiles?.filter((t) => !t.mine) ?? [];
  const shown = tab === "mine" ? mine : shared;

  return (
    <>
      <PageHeader title="Dashboards" description="Pinned charts re-run their query when opened (cached for 15 minutes), so figures stay current without AI cost." />
      <div className="border-b border-line bg-panel px-6">
        <div role="tablist" className="flex gap-6">
          {(["mine", "shared"] as const).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
              className={`-mb-px h-10 border-b-2 text-sm ${tab === t ? "border-accent font-medium text-foreground" : "border-transparent text-text2 hover:text-foreground"}`}>
              {t === "mine" ? `My charts (${mine.length})` : `Shared with me (${shared.length})`}
            </button>
          ))}
        </div>
      </div>
      <Page wide>
        {!tiles ? (
          <div className="grid gap-4 xl:grid-cols-2"><Skeleton className="h-72" /><Skeleton className="h-72" /></div>
        ) : shown.length === 0 ? (
          <div className="rounded border border-line bg-panel">
            {tab === "mine" ? (
              <EmptyState icon={<LayoutDashboard className="h-6 w-6" />} title="No charts pinned yet"
                body={<>Ask a numbers question, such as “Show revenue by month for this year”, then select <b>Pin to dashboard</b> on the chart. You can also select <b>Analyze</b> on a spreadsheet in the Library.</>}
                action={<Link href="/"><Button variant="primary">Ask a question</Button></Link>} />
            ) : (
              <EmptyState icon={<Share2 className="h-6 w-6" />} title="Nothing shared with you yet" body="Charts your colleagues share appear here." />
            )}
          </div>
        ) : (
          <div className="grid gap-4 xl:grid-cols-2">
            {shown.map((t) => <TileCard key={t.id} tile={t} onChange={load} />)}
          </div>
        )}
      </Page>
    </>
  );
}

function TileCard({ tile, onChange }: { tile: Tile; onChange: () => void }) {
  const me = useMe()!;
  const { confirm, prompt, toast } = useUI();
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

  async function patch(body: object, done: string) {
    const res = await api(`/api/dashboard/tiles/${tile.id}`, { method: "PATCH", body: JSON.stringify(body) });
    if (res.ok) toast(done);
    else toast((await res.json()).detail, "error");
    onChange();
  }
  async function rename() {
    const title = await prompt({ title: "Rename chart", action: "Rename", input: { label: "Chart name", value: tile.title } });
    if (title && title !== tile.title) patch({ title }, "Chart renamed.");
  }
  async function remove() {
    if (await confirm({ title: `Remove “${tile.title}”?`, action: "Remove", danger: true, body: tile.shared ? "It will also disappear for everyone it's shared with." : undefined })) {
      await api(`/api/dashboard/tiles/${tile.id}`, { method: "DELETE" });
      toast("Chart removed.");
      onChange();
    }
  }

  const canEdit = tile.mine || me.role === "admin";
  return (
    <div className="flex flex-col">
      {data?.no_access ? (
        <div className="rounded border border-line bg-panel">
          <p className="border-b border-line px-3 py-2 text-sm font-semibold">{tile.title}</p>
          <div className="flex items-center gap-2 p-4 text-sm text-text2">
            <Status kind="restricted">No access</Status> You don&apos;t have access to the data behind this chart. Ask an administrator if you need it.
          </div>
        </div>
      ) : data ? (
        <Chart compactHeader data={{ title: tile.title, spec: tile.spec, columns: data.columns, rows: data.rows, truncated: data.truncated }}
          action={<Button size="sm" variant="ghost" icon={<RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />} onClick={() => fetchData(true)} disabled={loading} title="Re-run the query now">Refresh</Button>} />
      ) : (
        <Skeleton className="h-72" />
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 pt-1.5 text-xs text-muted">
        {data?.refreshed_at && <span>Updated {ago(data.refreshed_at)}</span>}
        {data?.error && <Status kind="error" title={data.error}>{data.stale ? "Refresh failed, showing last result" : "Query failed"}</Status>}
        {!tile.mine && <span>Shared by {tile.owner}</span>}
        {tile.mine && tile.shared && <Status kind="info">Shared</Status>}
        <span className="ml-auto flex gap-0.5">
          {tile.mine && (
            <Button size="sm" variant="ghost" icon={<Share2 className="h-3.5 w-3.5" />} onClick={() => patch({ shared: !tile.shared }, tile.shared ? "Stopped sharing." : "Shared with everyone.")}
              title="Shared charts are visible to everyone, but each person only sees data they have access to">
              {tile.shared ? "Stop sharing" : "Share"}
            </Button>
          )}
          {canEdit && <Button size="sm" variant="ghost" icon={<Pencil className="h-3.5 w-3.5" />} onClick={rename}>Rename</Button>}
          {canEdit && <Button size="sm" variant="ghost" icon={<Trash2 className="h-3.5 w-3.5" />} onClick={remove}>Remove</Button>}
        </span>
      </div>
    </div>
  );
}
