"use client";

import { BarChart3, FileSpreadsheet, FileText, RefreshCw, Trash2, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { DocumentPreview, SheetPreview } from "@/components/Preview";
import SortableTable, { type Column } from "@/components/SortableTable";
import { Button, EmptyState, Page, PageHeader, Panel, Skeleton, Status, useUI } from "@/components/ui";
import { api, apiJson } from "@/lib/api";
import { useMe } from "@/lib/me";
import { ago } from "@/lib/time";

type Doc = { id: number; name: string; chunks: number; created_at: string; mine: boolean; private: boolean; replaced_at: string | null };
type Sheet = { name: string; title: string; source: string; private: boolean; mine: boolean; columns: Record<string, string>; rows: number | null; updated_at: string | null; replaced_at: string | null };
type Library = { documents: Doc[]; tables: Sheet[] };

const SOURCE: Record<string, string> = { upload: "Upload", gdrive: "Google Drive" };

export default function LibraryPage() {
  const me = useMe()!;
  const router = useRouter();
  const { confirm, toast } = useUI();
  const [lib, setLib] = useState<Library | null>(null);
  const [visibility, setVisibility] = useState<"everyone" | "private">("everyone");
  const [uploading, setUploading] = useState<"sheets" | "docs" | null>(null);
  const [preview, setPreview] = useState<{ kind: "sheet"; name: string } | { kind: "doc"; id: number } | null>(null);
  const sheetInput = useRef<HTMLInputElement>(null);
  const docInput = useRef<HTMLInputElement>(null);
  const replaceInput = useRef<HTMLInputElement>(null);
  const [replacing, setReplacing] = useState<{ kind: "sheet" | "doc"; id: string | number; label: string } | null>(null);

  const load = useCallback(() => apiJson<Library>("/api/sources").then(setLib).catch((e) => toast(e.message, "error")), [toast]);
  useEffect(() => {
    load();
  }, [load]);

  async function upload(files: FileList | null, kind: "sheets" | "docs") {
    setUploading(kind);
    for (const file of Array.from(files ?? [])) {
      const body = new FormData();
      body.append("file", file);
      body.append("visibility", visibility);
      const res = await api("/api/upload", { method: "POST", body });
      const json = await res.json();
      if (res.ok) {
        const tabs = json.kind === "tables" ? json.tables.length : 0;
        toast(`Uploaded ${file.name}${tabs > 1 ? ` (${tabs} sheets)` : ""}.`);
      } else toast(`${file.name}: ${json.detail}`, "error");
    }
    setUploading(null);
    for (const input of [sheetInput.current, docInput.current]) if (input) input.value = "";
    load();
  }

  function startReplace(target: { kind: "sheet" | "doc"; id: string | number; label: string }) {
    setReplacing(target);
    if (replaceInput.current) {
      replaceInput.current.accept = target.kind === "sheet" ? ".xlsx,.xlsm,.csv" : ".pdf,.txt,.md";
      replaceInput.current.click();
    }
  }

  async function replace(files: FileList | null) {
    const file = files?.[0];
    if (!file || !replacing) return;
    const body = new FormData();
    body.append("file", file);
    const path = replacing.kind === "sheet" ? `/api/tables/${replacing.id}/replace` : `/api/documents/${replacing.id}/replace`;
    const res = await api(path, { method: "POST", body });
    const json = await res.json();
    if (res.ok) {
      const first = String(json.change_note ?? "").split("\n")[0];
      toast(`Replaced ${replacing.label}.${first ? ` ${first}` : ""}`);
    } else toast(json.detail ?? "Couldn't replace the file.", "error");
    setReplacing(null);
    if (replaceInput.current) replaceInput.current.value = "";
    load();
  }

  async function remove(kind: "document" | "sheet", id: string | number, name: string) {
    const ok = await confirm({ title: `Delete ${name}?`, action: "Delete", danger: true,
      body: "It will no longer be searchable, and answers will stop citing it. This can't be undone." });
    if (!ok) return;
    const res = await api(kind === "document" ? `/api/documents/${id}` : `/api/tables/${id}`, { method: "DELETE" });
    if (res.ok) toast(`Deleted ${name}.`);
    else toast((await res.json()).detail, "error");
    load();
  }

  const access = (priv: boolean, mine: boolean) => priv ? <Status kind="restricted">{mine ? "Only you" : "Restricted"}</Status> : <span className="text-text2">Everyone</span>;

  const sheetCols: Column<Sheet>[] = [
    { key: "name", label: "Name", sort: (s) => s.title.toLowerCase(), render: (s) => {
      const [file, tab] = s.title.includes(" › ") ? s.title.split(" › ") : [s.title, null];
      return (
        <button onClick={() => setPreview({ kind: "sheet", name: s.name })} className="group flex items-center gap-2 text-left" title="Preview">
          <FileSpreadsheet className="h-4 w-4 shrink-0 text-ok" aria-hidden />
          <span className="min-w-0">
            <span className="block truncate font-medium text-accent group-hover:underline">{tab ?? file}</span>
            {tab && <span className="block truncate text-xs text-muted">{file}</span>}
          </span>
        </button>
      );
    } },
    { key: "columns", label: "Columns", render: (s) => <span className="line-clamp-1 max-w-72 text-xs text-text2" title={Object.keys(s.columns).join(", ")}>{Object.keys(s.columns).join(", ")}</span> },
    { key: "rows", label: "Rows", num: true, sort: (s) => s.rows ?? 0, render: (s) => (s.rows ?? 0).toLocaleString() },
    { key: "source", label: "Source", sort: (s) => s.source, render: (s) => SOURCE[s.source] ?? s.source },
    { key: "access", label: "Access", render: (s) => access(s.private, s.mine) },
    { key: "updated", label: "Updated", sort: (s) => s.updated_at ?? "", render: (s) => (
      <span className="text-text2">{s.replaced_at ? `Replaced ${ago(s.replaced_at)}` : ago(s.updated_at)}</span>
    ) },
    { key: "actions", label: "", className: "w-1", render: (s) => (
      <span className="flex justify-end gap-1">
        <Button size="sm" icon={<BarChart3 className="h-3.5 w-3.5" />} onClick={() => router.push(`/?analyze=${encodeURIComponent(s.name)}&title=${encodeURIComponent(s.title)}`)}>Analyze</Button>
        {(s.mine || me.role === "admin") && s.source === "upload" && <>
          <Button size="sm" variant="ghost" title="Upload a new version of this file. Charts and access settings are kept."
            onClick={() => startReplace({ kind: "sheet", id: s.name, label: s.title.split(" › ")[0] })} icon={<RefreshCw className="h-3.5 w-3.5" />}>Replace</Button>
          <Button size="sm" variant="ghost" aria-label={`Delete ${s.title}`} title="Delete" onClick={() => remove("sheet", s.name, s.title)} icon={<Trash2 className="h-3.5 w-3.5" />} />
        </>}
      </span>
    ) },
  ];

  const docCols: Column<Doc>[] = [
    { key: "name", label: "Name", sort: (d) => d.name.toLowerCase(), render: (d) => (
      <button onClick={() => setPreview({ kind: "doc", id: d.id })} className="group flex items-center gap-2 text-left" title="Preview">
        <FileText className="h-4 w-4 shrink-0 text-accent" aria-hidden /><span className="truncate font-medium text-accent group-hover:underline">{d.name}</span>
      </button>
    ) },
    { key: "chunks", label: "Passages", num: true, sort: (d) => d.chunks, render: (d) => d.chunks.toLocaleString() },
    { key: "access", label: "Access", render: (d) => access(d.private, d.mine) },
    { key: "added", label: "Updated", sort: (d) => d.replaced_at ?? d.created_at, render: (d) => (
      <span className="text-text2">{d.replaced_at ? `Replaced ${ago(d.replaced_at)}` : `Added ${ago(d.created_at)}`}</span>
    ) },
    { key: "actions", label: "", className: "w-1", render: (d) => (d.mine || me.role === "admin") && (
      <span className="flex justify-end gap-1">
        <Button size="sm" variant="ghost" title="Upload a new version of this document. Access settings are kept."
          onClick={() => startReplace({ kind: "doc", id: d.id, label: d.name })} icon={<RefreshCw className="h-3.5 w-3.5" />}>Replace</Button>
        <Button size="sm" variant="ghost" aria-label={`Delete ${d.name}`} title="Delete" onClick={() => remove("document", d.id, d.name)} icon={<Trash2 className="h-3.5 w-3.5" />} />
      </span>
    ) },
  ];

  return (
    <>
      <PageHeader title="Library" description="Files and spreadsheets you've added. Spreadsheets can be analyzed and charted; documents are searchable in Ask."
        actions={
          <label className="flex items-center gap-2 text-xs text-text2">
            New uploads visible to
            <select value={visibility} onChange={(e) => setVisibility(e.target.value as "everyone" | "private")} className="h-8 rounded border border-line bg-panel px-2 text-sm text-foreground">
              <option value="everyone">Everyone</option>
              <option value="private">Only me</option>
            </select>
          </label>
        } />
      <input ref={sheetInput} type="file" multiple accept=".xlsx,.xlsm,.csv" className="sr-only" tabIndex={-1} aria-hidden
        onChange={(e) => upload(e.target.files, "sheets")} />
      <input ref={docInput} type="file" multiple accept=".pdf,.txt,.md" className="sr-only" tabIndex={-1} aria-hidden
        onChange={(e) => upload(e.target.files, "docs")} />
      <input ref={replaceInput} type="file" className="sr-only" tabIndex={-1} aria-hidden onChange={(e) => replace(e.target.files)} />
      <Page>
        <Panel title="Spreadsheets" description="Excel (.xlsx) and CSV files, up to 25 MB. Each sheet becomes its own table; Google Sheets from a connected Drive appear here too." flush
          actions={<Button variant="primary" icon={<Upload className="h-4 w-4" />} loading={uploading === "sheets"} disabled={!!uploading} onClick={() => sheetInput.current?.click()}>Upload spreadsheets</Button>}>
          {!lib ? <div className="space-y-2 p-4"><Skeleton className="h-8" /><Skeleton className="h-8" /></div> : (
            <SortableTable columns={sheetCols} rows={lib.tables} rowKey={(s) => s.name} initialSort={{ key: "updated", dir: -1 }}
              empty={<EmptyState icon={<FileSpreadsheet className="h-6 w-6" />} title="No spreadsheets yet"
                body="Upload an Excel or CSV file to get trends, top performers and charts with one click."
                action={<Button onClick={() => sheetInput.current?.click()} icon={<Upload className="h-4 w-4" />}>Upload a spreadsheet</Button>} />} />
          )}
        </Panel>
        <Panel title="Documents" description="PDF, text (.txt) and Markdown (.md) files, up to 25 MB. Slack, Notion and other connected sources are managed under Connectors." flush
          actions={<Button variant="primary" icon={<Upload className="h-4 w-4" />} loading={uploading === "docs"} disabled={!!uploading} onClick={() => docInput.current?.click()}>Upload documents</Button>}>
          {!lib ? <div className="space-y-2 p-4"><Skeleton className="h-8" /></div> : (
            <SortableTable columns={docCols} rows={lib.documents} rowKey={(d) => d.id} initialSort={{ key: "added", dir: -1 }}
              empty={<EmptyState icon={<FileText className="h-6 w-6" />} title="No documents yet" body="Upload policies, handbooks or reports so Ask can answer questions about them."
                action={<Button onClick={() => docInput.current?.click()} icon={<Upload className="h-4 w-4" />}>Upload a document</Button>} />} />
          )}
        </Panel>
      </Page>
      {preview?.kind === "sheet" && <SheetPreview name={preview.name} onClose={() => setPreview(null)} />}
      {preview?.kind === "doc" && <DocumentPreview id={preview.id} onClose={() => setPreview(null)} />}
    </>
  );
}
