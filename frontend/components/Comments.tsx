"use client";

import { ChevronRight, Pencil, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button, useUI } from "@/components/ui";
import { api, apiJson } from "@/lib/api";
import { ago } from "@/lib/time";

type Comment = { id: number; author: string; mine: boolean; body: string; created_at: string; edited_at: string | null };

/** Collapsed comment thread on an answer or dashboard tile. Type @name or @email to notify someone who can see it. */
export default function Comments({ targetType, targetId, className = "" }: { targetType: "run" | "tile"; targetId: number; className?: string }) {
  const { confirm, prompt, toast } = useUI();
  const [items, setItems] = useState<Comment[] | null>(null);
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => apiJson<Comment[]>(`/api/comments?target_type=${targetType}&target_id=${targetId}`).then(setItems).catch(() => setItems(null)), [targetType, targetId]);
  useEffect(() => {
    load();
  }, [load]);
  if (!items) return null; // no access, or not loaded yet

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!body.trim()) return;
    setBusy(true);
    const res = await api("/api/comments", { method: "POST", body: JSON.stringify({ target_type: targetType, target_id: targetId, body }) });
    setBusy(false);
    if (!res.ok) return toast((await res.json()).detail ?? "Couldn't post the comment.", "error");
    setBody("");
    load();
  }
  async function edit(c: Comment) {
    const next = await prompt({ title: "Edit comment", action: "Save", input: { label: "Comment", value: c.body } });
    if (next && next !== c.body) {
      await api(`/api/comments/${c.id}`, { method: "PATCH", body: JSON.stringify({ body: next }) });
      load();
    }
  }
  async function remove(c: Comment) {
    if (!(await confirm({ title: "Delete this comment?", action: "Delete", danger: true }))) return;
    await api(`/api/comments/${c.id}`, { method: "DELETE" });
    load();
  }

  return (
    <details className={`group border-t border-line ${className}`}>
      <summary className="flex cursor-pointer list-none items-center gap-1.5 px-4 py-2.5 text-xs font-medium text-text2 hover:bg-hover">
        <ChevronRight className="h-3.5 w-3.5 transition-transform group-open:rotate-90" aria-hidden />
        Comments ({items.length})
      </summary>
      <div className="space-y-3 px-4 pt-1 pb-3">
        {items.length > 0 && (
          <ul className="space-y-2.5">
            {items.map((c) => (
              <li key={c.id} className="text-sm">
                <div className="flex items-baseline gap-2 text-xs">
                  <span className="font-medium text-foreground">{c.author}</span>
                  <span className="text-muted">{ago(c.created_at)}{c.edited_at ? " (edited)" : ""}</span>
                  {c.mine && (
                    <span className="ml-auto flex gap-0.5">
                      <button onClick={() => edit(c)} aria-label="Edit comment" title="Edit" className="rounded p-1 text-muted hover:bg-hover hover:text-foreground"><Pencil className="h-3.5 w-3.5" /></button>
                      <button onClick={() => remove(c)} aria-label="Delete comment" title="Delete" className="rounded p-1 text-muted hover:bg-hover hover:text-bad"><Trash2 className="h-3.5 w-3.5" /></button>
                    </span>
                  )}
                </div>
                <p className="mt-0.5 whitespace-pre-wrap text-text2">{c.body}</p>
              </li>
            ))}
          </ul>
        )}
        <form onSubmit={send} className="flex items-end gap-2">
          <label className="sr-only" htmlFor={`comment-${targetType}-${targetId}`}>Add a comment</label>
          <textarea id={`comment-${targetType}-${targetId}`} value={body} rows={1} onChange={(e) => setBody(e.target.value)} maxLength={5000}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }}
            placeholder="Add a comment. Type @name to notify someone."
            className="min-h-8 flex-1 resize-none rounded border border-line bg-panel px-2 py-1.5 text-sm outline-none placeholder:text-muted focus:border-accent focus:ring-1 focus:ring-accent" />
          <Button type="submit" size="sm" variant="primary" loading={busy} disabled={!body.trim()} className="h-8">Comment</Button>
        </form>
      </div>
    </details>
  );
}
