"use client";

import { BarChart3, Download, ExternalLink } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { downloadCSV } from "./Chart";
import { Button, SidePanel, Skeleton, Status, useUI } from "./ui";
import { api, apiJson } from "@/lib/api";
import { ago } from "@/lib/time";

const SOURCE: Record<string, string> = { upload: "Uploaded file", slack: "Slack", gdrive: "Google Drive", notion: "Notion", github: "GitHub", web: "Website" };

async function download(path: string, fallbackName: string, onError: (m: string) => void) {
  const res = await api(path);
  if (!res.ok) return onError((await res.json().catch(() => ({}))).detail ?? "Download failed");
  const name = decodeURIComponent(/filename\*=UTF-8''([^;]+)/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? fallbackName);
  const url = URL.createObjectURL(await res.blob());
  Object.assign(document.createElement("a"), { href: url, download: name }).click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

type Version = { filename: string; change_note: string | null; replaced_at: string; by: string | null };
type DocPreview = { id: number; name: string; source: string; url: string | null; kind: "pdf" | "markdown" | "text"; text: string; has_file: boolean; updated_at: string; versions: Version[] };

/** Quiet version info: one line, with what changed and earlier replacements behind a disclosure. */
function VersionNote({ versions }: { versions: Version[] }) {
  if (!versions?.length) return null;
  const [latest, ...older] = versions;
  return (
    <details className="group rounded border border-line text-sm">
      <summary className="cursor-pointer list-none px-3 py-2 text-xs text-text2 hover:bg-hover">
        Replaced {ago(latest.replaced_at)}{latest.by ? ` by ${latest.by}` : ""}. <span className="text-accent group-open:hidden">Show what changed</span>
      </summary>
      <div className="space-y-3 border-t border-line px-3 py-2">
        <pre className="font-sans text-xs leading-5 whitespace-pre-wrap text-text2">{latest.change_note}</pre>
        {older.length > 0 && (
          <div>
            <h4 className="mb-1 text-xs font-semibold text-text2">Earlier versions</h4>
            <ul className="space-y-1 text-xs text-muted">
              {older.map((v, i) => <li key={i}>{v.filename}, replaced {ago(v.replaced_at)}{v.by ? ` by ${v.by}` : ""}</li>)}
            </ul>
          </div>
        )}
      </div>
    </details>
  );
}

/** A document: the original PDF when stored, otherwise its text. `highlight` shows the passage an answer cited. */
export function DocumentPreview({ id, highlight, onClose }: { id: number; highlight?: string; onClose: () => void }) {
  const { toast } = useUI();
  const [doc, setDoc] = useState<DocPreview | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let url: string | null = null;
    apiJson<DocPreview>(`/api/documents/${id}/preview`).then(async (d) => {
      setDoc(d);
      if (d.kind === "pdf" && d.has_file) {
        const res = await api(`/api/documents/${id}/file`);
        if (res.ok) {
          url = URL.createObjectURL(new Blob([await res.blob()], { type: "application/pdf" }));
          setPdfUrl(url);
        }
      }
    }).catch((e) => setError(e.message));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [id]);

  return (
    <SidePanel wide onClose={onClose} title={doc?.name ?? "Document"}
      description={doc && <span className="flex flex-wrap items-center gap-2">{SOURCE[doc.source] ?? doc.source}<span className="text-muted">Updated {ago(doc.updated_at)}</span></span>}
      footer={doc && <>
        {doc.url && <a href={doc.url} target="_blank" rel="noreferrer"><Button icon={<ExternalLink className="h-4 w-4" />}>Open in {SOURCE[doc.source] ?? "source"}</Button></a>}
        {doc.has_file && <Button variant="primary" icon={<Download className="h-4 w-4" />} onClick={() => download(`/api/documents/${id}/file`, doc.name, (m) => toast(m, "error"))}>Download original</Button>}
      </>}>
      {error ? <Status kind="error">{error}</Status> : !doc ? <div className="space-y-2"><Skeleton className="h-6" /><Skeleton className="h-64" /></div> : (
        <div className="flex h-full flex-col gap-4">
          <VersionNote versions={doc.versions} />
          {highlight && (
            <section className="rounded border border-line border-l-4 border-l-accent bg-accent-weak/40 p-3">
              <h3 className="mb-1 text-xs font-semibold text-text2">Passage cited in the answer</h3>
              {doc.kind === "markdown" ? <div className="md text-sm leading-6"><ReactMarkdown remarkPlugins={[remarkGfm]}>{highlight}</ReactMarkdown></div>
                : <p className="text-sm whitespace-pre-wrap">{highlight}</p>}
            </section>
          )}
          {pdfUrl ? (
            <iframe src={pdfUrl} title={doc.name} className="min-h-[70vh] w-full flex-1 rounded border border-line" />
          ) : doc.kind === "markdown" ? (
            <div className="md text-sm leading-6"><ReactMarkdown remarkPlugins={[remarkGfm]}>{doc.text}</ReactMarkdown></div>
          ) : (
            <>
              {doc.kind === "pdf" && !doc.has_file && <p className="text-xs text-muted">Showing extracted text. The original PDF wasn&apos;t stored because it was added before previews existed; re-upload it to view the original.</p>}
              <pre className="font-sans text-sm leading-6 whitespace-pre-wrap">{doc.text}</pre>
            </>
          )}
        </div>
      )}
    </SidePanel>
  );
}

type SheetPreviewData = { name: string; title: string; source: string; columns: string[]; types: Record<string, string>; rows: unknown[][]; total_rows: number | null; shown: number; has_file: boolean; versions: Version[] };

const exact = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });

const TYPE_LABEL: Record<string, string> = { bigint: "Whole number", "double precision": "Number", text: "Text", date: "Date", "timestamp without time zone": "Date and time" };

/** A spreadsheet tab: real rows (as the viewer's own database role), column types, and downloads. */
export function SheetPreview({ name, onClose }: { name: string; onClose: () => void }) {
  const router = useRouter();
  const { toast } = useUI();
  const [p, setP] = useState<SheetPreviewData | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    apiJson<SheetPreviewData>(`/api/tables/${name}/preview`).then(setP).catch((e) => setError(e.message));
  }, [name]);
  const [file, tab] = p?.title.includes(" › ") ? p.title.split(" › ") : [p?.title, null];

  return (
    <SidePanel wide onClose={onClose} title={tab ?? file ?? "Spreadsheet"}
      description={p && <span className="flex flex-wrap items-center gap-2">{tab && <span>{file}</span>}<span className="text-muted">{(p.total_rows ?? p.shown).toLocaleString()} rows, {p.columns.length} columns</span></span>}
      footer={p && <>
        <Button icon={<Download className="h-4 w-4" />} onClick={() => downloadCSV(p.title, p.columns, p.rows)}>Download preview as CSV</Button>
        {p.has_file && <Button icon={<Download className="h-4 w-4" />} onClick={() => download(`/api/tables/${name}/file`, file ?? "spreadsheet", (m) => toast(m, "error"))}>Download original</Button>}
        <Button variant="primary" icon={<BarChart3 className="h-4 w-4" />} onClick={() => router.push(`/?analyze=${encodeURIComponent(name)}&title=${encodeURIComponent(p.title)}`)}>Analyze</Button>
      </>}>
      {error ? <Status kind="error">{error}</Status> : !p ? <div className="space-y-2"><Skeleton className="h-8" /><Skeleton className="h-8" /><Skeleton className="h-8" /></div> : (
        <div className="space-y-2">
          <VersionNote versions={p.versions} />
          {p.total_rows != null && p.total_rows > p.shown && <p className="text-xs text-muted">Showing the first {p.shown.toLocaleString()} of {p.total_rows.toLocaleString()} rows.</p>}
          <div className="overflow-auto rounded border border-line">
            <table className="data-table">
              <thead className="sticky top-0">
                <tr>
                  {p.columns.map((c) => {
                    const num = ["bigint", "double precision"].includes(p.types[c]);
                    return (
                      <th key={c} className={num ? "num" : ""}>
                        <span className="block">{c}</span>
                        <span className="block text-[11px] font-normal text-muted">{TYPE_LABEL[p.types[c]] ?? p.types[c]}</span>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {p.rows.map((r, i) => (
                  <tr key={i}>{r.map((v, j) => <td key={j} className={typeof v === "number" ? "num" : ""}>{v == null ? <span className="text-muted">Empty</span> : typeof v === "number" ? exact.format(v) : String(v)}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </SidePanel>
  );
}

