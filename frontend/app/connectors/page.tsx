"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { API, api, apiJson } from "@/lib/api";
import { ago } from "@/lib/time";

type Field = { key: string; label: string; help: string; required?: boolean };
type Paste = { label: string; placeholder: string; help: string; multiline?: boolean; optional?: boolean };
type Available = {
  kind: string; label: string; config: Field[]; doc: string; blurb: string; category: "index" | "warehouse" | "tool";
  guide: string[]; oauth: boolean; oauth_ready: boolean; paste: Paste | null;
};
type Connection = {
  id: number; kind: string; name: string; config: Record<string, string>; enabled: boolean; last_synced_at: string | null;
  status: string | null; error: string | null; account: string | null; allowed: string[] | null; documents: number; running: boolean;
};
type Catalog = { available: Available[]; connections: Connection[]; interval_minutes: number };

const ICON: Record<string, string> = { slack: "#", gdrive: "▲", notion: "N", github: "⌥", web: "◎", bigquery: "BQ", snowflake: "❄", postgres: "PG", mcp: "⚡" };
const GROUPS: { category: Available["category"]; title: string; blurb: string }[] = [
  { category: "index", title: "Knowledge", blurb: "Documents and conversations, copied into search and kept in sync." },
  { category: "warehouse", title: "Data", blurb: "Databases queried live for numbers and charts. Nothing is copied; queries are read-only." },
  { category: "tool", title: "Apps", blurb: "Any app with an MCP server. The assistant can look things up there, or take actions you allow." },
];

export default function Connectors() {
  const [cat, setCat] = useState<Catalog | null>(null);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState<Available | null>(null);
  const [banner, setBanner] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(() => apiJson<Catalog>("/api/connectors").then((c) => { setCat(c); setError(""); }).catch((e) => setError(e.message)), []);

  useEffect(() => {
    load();
    const q = new URLSearchParams(location.search); // result of an OAuth round-trip
    if (q.get("connected")) setBanner({ ok: true, text: "Connected. The first sync is running." });
    if (q.get("error")) setBanner({ ok: false, text: q.get("error")! });
    if (q.size) history.replaceState(null, "", "/connectors");
  }, [load]);

  const busy = cat?.connections.some((c) => c.running || c.status === "syncing");
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [busy, load]);

  async function act(path: string, method = "POST") {
    await api(path, { method });
    setTimeout(load, 300);
  }

  if (error) return <p className="p-6 text-bad">{error}</p>;
  if (!cat) return <p className="p-6 text-sm text-muted">Loading…</p>;
  const byKind = Object.fromEntries(cat.available.map((a) => [a.kind, a]));

  return (
    <main className="mx-auto max-w-5xl space-y-10 px-4 py-6">
      <div>
        <h1 className="text-lg font-semibold">Connectors</h1>
        <p className="mt-1 text-sm text-muted">
          Connect the places your company&apos;s knowledge lives. Everyone only ever sees results from sources they&apos;re allowed to see.
        </p>
      </div>
      {banner && (
        <p role="status" className={`rounded-lg border px-3 py-2 text-sm ${banner.ok ? "border-ok/40 bg-ok/10 text-ok" : "border-bad/40 bg-bad/10 text-bad"}`}>{banner.text}</p>
      )}

      {cat.connections.length > 0 && (
        <section>
          <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">Your connections</h2>
          <ul className="space-y-2">
            {cat.connections.map((c) => <ConnectionRow key={c.id} c={c} a={byKind[c.kind]} act={act} onChange={load} />)}
          </ul>
        </section>
      )}

      {adding && <AddForm a={adding} onClose={() => setAdding(null)} onAdded={() => { setAdding(null); setBanner({ ok: true, text: `${adding.label} connected.` }); load(); }} />}

      {GROUPS.map((g) => (
        <section key={g.category}>
          <h2 className="font-semibold">{g.title}</h2>
          <p className="mt-0.5 text-sm text-muted">{g.blurb}</p>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {cat.available.filter((a) => a.category === g.category).map((a) => (
              <button key={a.kind} onClick={() => { setAdding(a); window.scrollTo({ top: 0, behavior: "smooth" }); }}
                className="flex items-start gap-3 rounded-lg border border-line bg-panel p-3 text-left hover:border-accent">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-accent/15 font-mono text-xs text-accent">{ICON[a.kind]}</span>
                <span className="min-w-0">
                  <span className="block font-medium">{a.label}</span>
                  <span className="mt-0.5 line-clamp-2 block text-xs text-muted">{a.blurb}</span>
                </span>
              </button>
            ))}
          </div>
        </section>
      ))}
      <p className="text-xs text-muted">
        {cat.interval_minutes > 0 ? `Knowledge sources re-sync every ${cat.interval_minutes} minutes, picking up only what changed.` : "Automatic sync is off (SYNC_INTERVAL_MINUTES=0). Use Sync now."}
      </p>
    </main>
  );
}

function ConnectionRow({ c, a, act, onChange }: { c: Connection; a?: Available; act: (p: string, m?: string) => void; onChange: () => void }) {
  const [editingAccess, setEditingAccess] = useState(false);
  const live = a && a.category !== "index";
  const syncing = c.running || c.status === "syncing";
  return (
    <li className="rounded-lg border border-line bg-panel p-3 text-sm">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="grid h-7 w-7 place-items-center rounded bg-accent/15 font-mono text-[10px] text-accent">{ICON[c.kind] ?? "?"}</span>
        <span className="min-w-0 flex-1">
          <span className="font-medium">{c.name}</span>
          {!c.enabled && <span className="ml-2 rounded bg-line px-1.5 py-0.5 text-xs text-muted">paused</span>}
          <span className="block truncate text-xs text-muted">{[c.account, ...Object.values(c.config)].filter(Boolean).join(" · ")}</span>
        </span>
        <span className={syncing ? "animate-pulse text-accent" : c.status === "error" ? "text-bad" : "text-muted"}>
          {syncing ? (live ? "checking…" : "syncing…") : c.status === "error" ? "needs attention" : live ? "● live" : c.status ?? "never synced"}
        </span>
        {!live && <span className="tabular-nums text-muted">{c.documents} docs</span>}
        <button onClick={() => setEditingAccess(!editingAccess)} className="text-xs text-muted underline-offset-2 hover:text-foreground hover:underline"
          title="Who can see results from this source">
          {c.allowed ? `🔒 ${c.allowed.length} ${c.allowed.length === 1 ? "person" : "people"}` : "Everyone"}
        </button>
        <span className="text-xs text-muted">{c.last_synced_at ? ago(c.last_synced_at) : ""}</span>
        <span className="flex gap-1">
          <Btn disabled={syncing} onClick={() => act(`/api/connections/${c.id}/sync`)}>{live ? "Test" : "Sync now"}</Btn>
          {!live && <Btn disabled={syncing} onClick={() => act(`/api/connections/${c.id}/sync?full=true`)} title="Re-read everything, not just changes">Full</Btn>}
          <Btn onClick={() => act(`/api/connections/${c.id}?enabled=${!c.enabled}`, "PATCH")}>{c.enabled ? "Pause" : "Resume"}</Btn>
          <Btn danger onClick={() => confirm(`Remove ${c.name}${live ? "" : ` and its ${c.documents} indexed documents`}? Its stored credentials are deleted too.`) && act(`/api/connections/${c.id}`, "DELETE")}>Remove</Btn>
        </span>
      </div>
      {c.status === "error" && c.error && <p className="mt-2 break-words rounded bg-bad/10 px-2 py-1 font-mono text-xs text-bad">{c.error}</p>}
      {editingAccess && <AccessEditor initial={c.allowed} onSave={async (allowed) => {
        const res = await api(`/api/connections/${c.id}/access`, { method: "PUT", body: JSON.stringify({ allowed }) });
        if (!res.ok) return (await res.json()).detail as string;
        setEditingAccess(false);
        onChange();
      }} />}
    </li>
  );
}

function AccessEditor({ initial, onSave }: { initial: string[] | null; onSave: (allowed: string[] | null) => Promise<string | void> }) {
  const [mode, setMode] = useState<"everyone" | "people">(initial ? "people" : "everyone");
  const [emails, setEmails] = useState((initial ?? []).join(", "));
  const [err, setErr] = useState("");
  return (
    <div className="mt-3 space-y-2 border-t border-line pt-3">
      <AccessFields mode={mode} setMode={setMode} emails={emails} setEmails={setEmails} />
      {err && <p className="text-xs text-bad">{err}</p>}
      <button onClick={async () => setErr((await onSave(mode === "everyone" ? null : splitEmails(emails))) || "")}
        className="rounded-md bg-accent px-3 py-1 text-xs font-medium text-background">Save access</button>
    </div>
  );
}

const splitEmails = (s: string) => s.split(/[\s,;]+/).map((e) => e.trim()).filter(Boolean);

function AccessFields({ mode, setMode, emails, setEmails }: { mode: string; setMode: (m: "everyone" | "people") => void; emails: string; setEmails: (s: string) => void }) {
  return (
    <fieldset className="space-y-1.5">
      <legend className="text-xs font-medium">Who can see results from this source?</legend>
      <label className="flex items-center gap-2 text-sm"><input type="radio" checked={mode === "everyone"} onChange={() => setMode("everyone")} /> Everyone signed in</label>
      <label className="flex items-center gap-2 text-sm"><input type="radio" checked={mode === "people"} onChange={() => setMode("people")} /> Only specific people</label>
      {mode === "people" && (
        <textarea value={emails} onChange={(e) => setEmails(e.target.value)} rows={2} placeholder="ana@company.com, bo@company.com"
          className="w-full rounded-md border border-line bg-background px-2 py-1.5 text-sm outline-none focus:border-accent" />
      )}
      <p className="text-xs text-muted">Slack private channels and Drive files also keep their own sharing rules on top of this.</p>
    </fieldset>
  );
}

function AddForm({ a, onClose, onAdded }: { a: Available; onClose: () => void; onAdded: () => void }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [name, setName] = useState(a.label);
  const [credential, setCredential] = useState("");
  const [usePaste, setUsePaste] = useState(!a.oauth_ready);
  const [mode, setMode] = useState<"everyone" | "people">("everyone");
  const [emails, setEmails] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const allowed = mode === "people" ? splitEmails(emails) : null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    if (mode === "people" && !allowed?.length) return setErr("Add at least one email, or choose Everyone.");
    if (a.oauth_ready && !usePaste) {
      const q = new URLSearchParams({ name, config: JSON.stringify(values), allowed: (allowed ?? []).join(",") });
      location.href = `${API}/api/oauth/${a.kind}/start?${q}`; // backend records state + PKCE, then sends you to the provider
      return;
    }
    setBusy(true);
    try {
      await apiJson("/api/connections", { method: "POST", body: JSON.stringify({ kind: a.kind, name, config: values, credential, allowed }) });
      onAdded();
    } catch (e) {
      setErr((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4 rounded-lg border border-accent/50 bg-panel p-4 text-sm">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 font-semibold">
          <span className="grid h-7 w-7 place-items-center rounded bg-accent/15 font-mono text-[10px] text-accent">{ICON[a.kind]}</span>
          Connect {a.label}
        </h2>
        <button type="button" onClick={onClose} className="text-muted hover:text-foreground" aria-label="Close">✕</button>
      </div>
      <p className="text-muted">{a.blurb}</p>

      {a.guide.length > 0 && (usePaste || !a.oauth_ready) && (
        <details open className="rounded-md border border-line bg-background/50 p-3">
          <summary className="cursor-pointer text-xs font-medium">Setup guide ({a.guide.length} steps)</summary>
          <ol className="mt-2 list-decimal space-y-1.5 pl-5 text-xs text-muted">{a.guide.map((g) => <li key={g}>{g}</li>)}</ol>
        </details>
      )}

      <Input label="Name" help="What your team will see, e.g. “Sales Slack” or “Finance warehouse”" value={name} onChange={setName} />
      {a.config.filter((f) => f.required).map((f) => (
        <Input key={f.key} label={f.label + " *"} help={f.help} value={values[f.key] ?? ""}
          onChange={(v) => setValues((p) => ({ ...p, [f.key]: v }))} required />
      ))}

      {a.paste && usePaste && (
        <label className="block">
          <span className="text-xs font-medium">{a.paste.label}{a.paste.optional ? "" : " *"}</span>
          {a.paste.multiline ? (
            <textarea value={credential} onChange={(e) => setCredential(e.target.value)} required={!a.paste.optional} rows={5} placeholder={a.paste.placeholder}
              spellCheck={false} className="mt-1 w-full rounded-md border border-line bg-background px-2 py-1.5 font-mono text-xs outline-none focus:border-accent" />
          ) : (
            <input type="password" autoComplete="off" value={credential} onChange={(e) => setCredential(e.target.value)} required={!a.paste.optional} placeholder={a.paste.placeholder}
              className="mt-1 w-full rounded-md border border-line bg-background px-2 py-1.5 font-mono outline-none focus:border-accent" />
          )}
          <span className="mt-1 block text-xs text-muted">{a.paste.help}. We check it with {a.label} first, then store it encrypted. It&apos;s never shown again.</span>
        </label>
      )}

      {a.config.some((f) => !f.required) && (
        <details className="rounded-md border border-line p-3">
          <summary className="cursor-pointer text-xs font-medium">Optional settings</summary>
          <div className="mt-3 space-y-3">
            {a.config.filter((f) => !f.required).map((f) => (
              <Input key={f.key} label={f.label} help={f.help} value={values[f.key] ?? ""} onChange={(v) => setValues((p) => ({ ...p, [f.key]: v }))} />
            ))}
          </div>
        </details>
      )}

      <AccessFields mode={mode} setMode={setMode} emails={emails} setEmails={setEmails} />

      {err && <p className="rounded bg-bad/10 px-2 py-1 text-bad" role="alert">{err}</p>}
      <div className="flex flex-wrap items-center gap-3">
        <button disabled={busy} className="rounded-md bg-accent px-4 py-1.5 font-medium text-background disabled:opacity-50">
          {a.oauth_ready && !usePaste ? `Continue with ${a.label} →` : busy ? "Checking…" : "Connect"}
        </button>
        {a.oauth_ready && a.paste && (
          <button type="button" onClick={() => setUsePaste(!usePaste)} className="text-xs text-muted underline hover:text-foreground">
            {usePaste ? `Use “Continue with ${a.label}” instead` : "Paste a token instead"}
          </button>
        )}
        {a.oauth && !a.oauth_ready && (
          <span className="text-xs text-muted">Want one-click sign-in for your team? Set up the {a.label} app in <Link href="/settings" className="text-accent underline">Settings</Link>.</span>
        )}
      </div>
    </form>
  );
}

function Input({ label, help, value, onChange, required }: { label: string; help?: string; value: string; onChange: (v: string) => void; required?: boolean }) {
  return (
    <label className="block">
      <span className="text-xs font-medium">{label}</span>
      <input value={value} onChange={(e) => onChange(e.target.value)} required={required}
        className="mt-1 w-full rounded-md border border-line bg-background px-2 py-1.5 outline-none focus:border-accent" />
      {help && <span className="mt-1 block text-xs text-muted">{help}</span>}
    </label>
  );
}

function Btn({ children, danger, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { danger?: boolean }) {
  return (
    <button {...p} className={`rounded border border-line px-2 py-1 text-xs disabled:opacity-40 ${danger ? "hover:border-bad hover:text-bad" : "hover:border-accent"}`}>
      {children}
    </button>
  );
}
