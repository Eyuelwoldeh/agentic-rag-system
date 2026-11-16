"use client";

import { CornerDownLeft, FileText, MessageSquareText, Search, Sheet, Sigma } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { apiJson } from "@/lib/api";
import { SECTIONS, SETTINGS } from "@/lib/nav";

type Entry = { group: string; label: string; hint?: string; Icon: typeof Search; go: string };

/** Full navigation: the Ask page reads ?q= / ?analyze= only on load, so reload it even when it's already open. */
const askUrl = (q: string) => `/?q=${encodeURIComponent(q)}`;

/** ⌘K / Ctrl+K: jump to any page, conversation, metric or file, or ask what you typed. */
export default function CommandPalette({ admin, onClose }: { admin: boolean; onClose: () => void }) {
  const router = useRouter();
  const ref = useRef<HTMLDialogElement>(null);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [data, setData] = useState<Entry[]>([]);

  useEffect(() => {
    ref.current?.showModal();
    Promise.allSettled([
      apiJson<{ id: number; title: string }[]>("/api/conversations"),
      apiJson<{ id: number; name: string; description: string }[]>("/api/metrics"),
      apiJson<{ documents: { id: number; name: string }[]; tables: { name: string; title: string }[] }>("/api/sources"),
    ]).then(([convs, metrics, sources]) => {
      const out: Entry[] = [];
      if (convs.status === "fulfilled") out.push(...convs.value.slice(0, 30).map((c) => ({ group: "Conversations", label: c.title, Icon: MessageSquareText, go: `/?c=${c.id}` })));
      if (metrics.status === "fulfilled") out.push(...metrics.value.map((m) => ({ group: "Metrics", label: m.name, hint: "Ask about this metric", Icon: Sigma,
        go: askUrl(`How has ${m.name} trended over the last 12 months?`) })));
      if (sources.status === "fulfilled") {
        out.push(...sources.value.tables.map((t) => ({ group: "Files", label: t.title, hint: "Analyze", Icon: Sheet,
          go: `/?analyze=${encodeURIComponent(t.name)}&title=${encodeURIComponent(t.title)}` })));
        out.push(...sources.value.documents.map((d) => ({ group: "Files", label: d.name, hint: "Summarize", Icon: FileText, go: askUrl(`Summarize ${d.name}`) })));
      }
      setData(out);
    });
  }, []);

  const pages: Entry[] = useMemo(() => [...SECTIONS.filter((s) => admin || !s.admin).flatMap((s) => s.items), SETTINGS]
    .map((i) => ({ group: "Go to", label: i.label, hint: i.hint, Icon: i.Icon, go: i.href })), [admin]);

  const results = useMemo(() => {
    const t = q.trim().toLowerCase();
    const match = (e: Entry) => !t || e.label.toLowerCase().includes(t) || e.hint?.toLowerCase().includes(t);
    const ask: Entry[] = t ? [{ group: "Ask", label: q.trim(), hint: "Ask this question", Icon: Search, go: askUrl(q.trim()) }] : [];
    // ponytail: substring match and 8 per group; add fuzzy ranking if people have hundreds of files
    const groups = ["Go to", "Conversations", "Metrics", "Files"];
    const found = groups.flatMap((g) => [...pages, ...data].filter((e) => e.group === g && match(e)).slice(0, t ? 8 : g === "Go to" ? 12 : 5));
    return t.includes(" ") || !found.length ? [...ask, ...found] : [...found, ...ask]; // a phrase is probably a question; a word is probably a lookup
  }, [q, pages, data]);

  function choose(e: Entry | undefined) {
    if (!e) return;
    onClose();
    if (e.go.startsWith("/?")) location.href = e.go;
    else router.push(e.go);
  }

  function key(ev: React.KeyboardEvent) {
    if (ev.key === "ArrowDown") { ev.preventDefault(); setSel((s) => Math.min(s + 1, results.length - 1)); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
    else if (ev.key === "Enter") { ev.preventDefault(); choose(results[sel]); }
  }

  useEffect(() => {
    document.getElementById(`cmd-${sel}`)?.scrollIntoView({ block: "nearest" });
  }, [sel]);

  return (
    <dialog ref={ref} onClose={onClose} onClick={(e) => e.target === ref.current && onClose()} aria-label="Search and go"
      className="mx-auto mt-[12vh] w-[min(40rem,calc(100vw-2rem))] rounded-md border border-line bg-panel p-0 text-foreground shadow-overlay">
      <div className="flex items-center gap-3 border-b border-line px-4">
        <Search className="h-4 w-4 shrink-0 text-muted" aria-hidden />
        <input autoFocus value={q} onChange={(e) => { setQ(e.target.value); setSel(0); }} onKeyDown={key}
          placeholder="Search pages, conversations, metrics and files, or type a question"
          role="combobox" aria-expanded="true" aria-controls="cmd-list" aria-activedescendant={results[sel] ? `cmd-${sel}` : undefined}
          className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-muted focus-visible:outline-none" />
        <kbd className="rounded border border-line px-1.5 text-[11px] text-muted">Esc</kbd>
      </div>
      <ul id="cmd-list" role="listbox" className="max-h-[min(60vh,28rem)] overflow-y-auto py-1">
        {results.map((e, i) => (
          <li key={`${e.group}-${e.go}-${i}`}>
            {(i === 0 || results[i - 1].group !== e.group) && <p className="px-4 pt-2.5 pb-1 text-xs font-medium text-muted">{e.group}</p>}
            <button id={`cmd-${i}`} role="option" aria-selected={i === sel} onMouseMove={() => setSel(i)} onClick={() => choose(e)}
              className={`flex w-full items-center gap-3 px-4 py-2 text-left text-sm ${i === sel ? "bg-accent-weak" : ""}`}>
              <e.Icon className={`h-4 w-4 shrink-0 ${i === sel ? "text-accent" : "text-muted"}`} aria-hidden />
              <span className="min-w-0 flex-1 truncate">{e.label}</span>
              {e.hint && <span className="hidden truncate text-xs text-muted sm:block sm:max-w-[45%]">{e.hint}</span>}
              {i === sel && <CornerDownLeft className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />}
            </button>
          </li>
        ))}
        {results.length === 0 && <li className="px-4 py-6 text-sm text-text2">Nothing matches. Type a full question and press Enter to ask it.</li>}
      </ul>
      <div className="flex gap-4 border-t border-line px-4 py-2 text-xs text-muted">
        <span><kbd className="font-sans">↑↓</kbd> to move</span><span><kbd className="font-sans">Enter</kbd> to open</span>
      </div>
    </dialog>
  );
}
