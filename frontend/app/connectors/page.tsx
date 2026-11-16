"use client";

import { BarChart3, Database, FileText, Globe, MoreHorizontal, Plug, Plus, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import SortableTable, { type Column } from "@/components/SortableTable";
import { Button, EmptyState, Field, inputCls, Page, PageHeader, Panel, SidePanel, Skeleton, Status, useUI } from "@/components/ui";
import { API, api, apiJson } from "@/lib/api";
import { ago } from "@/lib/time";

type FieldDef = { key: string; label: string; help: string; required?: boolean };
type Paste = { label: string; placeholder: string; help: string; multiline?: boolean; optional?: boolean };
type Available = {
  kind: string; label: string; config: FieldDef[]; doc: string; blurb: string; category: "index" | "business" | "warehouse" | "tool";
  guide: string[]; oauth: boolean; oauth_ready: boolean; paste: Paste | null;
};
type Connection = {
  id: number; kind: string; name: string; config: Record<string, string>; enabled: boolean; last_synced_at: string | null;
  status: string | null; error: string | null; account: string | null; allowed: string[] | null; documents: number; running: boolean;
};
type Catalog = { available: Available[]; connections: Connection[]; interval_minutes: number };

const synced = (c?: Available["category"]) => c === "index" || c === "business"; // copied in on each sync, not queried live

const GROUPS: { category: Available["category"]; title: string; blurb: string; Icon: typeof Plug }[] = [
  { category: "index", title: "Knowledge sources", blurb: "Copied into search and kept in sync.", Icon: FileText },
  { category: "business", title: "Business systems", blurb: "Sales, finance and customer data, synced as tables you can analyze.", Icon: BarChart3 },
  { category: "warehouse", title: "Databases", blurb: "Queried live and read-only, for figures and charts. Nothing is copied.", Icon: Database },
  { category: "tool", title: "Apps", blurb: "Tools with an MCP server, used live by the assistant.", Icon: Globe },
];

export default function Connectors() {
  const { toast } = useUI();
  const [cat, setCat] = useState<Catalog | null>(null);
  const [picking, setPicking] = useState(false);
  const [adding, setAdding] = useState<Available | null>(null);

  const load = useCallback(() => apiJson<Catalog>("/api/connectors").then(setCat).catch((e) => toast(e.message, "error")), [toast]);

  useEffect(() => {
    load();
    const q = new URLSearchParams(location.search); // result of an OAuth round-trip
    if (q.get("connected")) toast("Connected. The first sync has started.");
    if (q.get("error")) toast(q.get("error")!, "error");
    if (q.size) history.replaceState(null, "", "/connectors");
  }, [load, toast]);

  const busy = cat?.connections.some((c) => c.running || c.status === "syncing");
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [busy, load]);

  const byKind = Object.fromEntries((cat?.available ?? []).map((a) => [a.kind, a]));

  return (
    <>
      <PageHeader title="Connectors" description="Connect the places your company's knowledge and data live. People only see results from sources they're allowed to access."
        actions={<Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setPicking(true)}>Add connector</Button>} />
      <Page wide>
        <Panel title="Connections" description={cat ? (cat.interval_minutes > 0 ? `Knowledge sources re-sync every ${cat.interval_minutes} minutes, picking up only changes.` : "Automatic sync is off. Use Sync now.") : undefined} flush>
          {!cat ? <div className="space-y-2 p-4"><Skeleton className="h-8" /><Skeleton className="h-8" /></div> : (
            <ConnectionsTable rows={cat.connections} byKind={byKind} onChange={load}
              empty={<EmptyState icon={<Plug className="h-6 w-6" />} title="No connections yet"
                body="Add Slack, Google Drive, a database or another source so Ask can answer from it."
                action={<Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setPicking(true)}>Add connector</Button>} />} />
          )}
        </Panel>
      </Page>
      {picking && cat && <CatalogPanel available={cat.available} onClose={() => setPicking(false)} onPick={(a) => { setPicking(false); setAdding(a); }} />}
      {adding && <AddPanel a={adding} onClose={() => setAdding(null)} onAdded={() => { toast(`${adding.label} connected.`); setAdding(null); load(); }} />}
    </>
  );
}

function statusOf(c: Connection, live: boolean): React.ReactNode {
  if (c.running || c.status === "syncing") return <Status kind="running">{live ? "Checking" : "Syncing"}</Status>;
  if (c.status === "error") return <Status kind="error" title={c.error ?? ""}>Needs attention</Status>;
  if (!c.enabled) return <Status kind="neutral">Paused</Status>;
  if (live) return <Status kind="success">Live</Status>;
  if (c.last_synced_at) return <Status kind="success">Synced</Status>;
  return <Status kind="neutral">Not synced</Status>;
}

function ConnectionsTable({ rows, byKind, onChange, empty }: { rows: Connection[]; byKind: Record<string, Available>; onChange: () => void; empty: React.ReactNode }) {
  const { confirm, toast } = useUI();
  const [access, setAccess] = useState<Connection | null>(null);

  async function act(c: Connection, path: string, method: string, done: string) {
    const res = await api(path, { method });
    if (res.ok) toast(done);
    else toast((await res.json()).detail, "error");
    setTimeout(onChange, 300);
  }
  async function remove(c: Connection, live: boolean) {
    if (await confirm({ title: `Remove ${c.name}?`, action: "Remove", danger: true,
      body: live ? "Its stored credentials are deleted and people can no longer query it." : `Its ${c.documents} indexed documents and stored credentials are deleted. This can't be undone.` }))
      act(c, `/api/connections/${c.id}`, "DELETE", `${c.name} removed.`);
  }

  const columns: Column<Connection>[] = [
    { key: "name", label: "Name", sort: (c) => c.name.toLowerCase(), render: (c) => (
      <span className="block min-w-0">
        <span className="block truncate font-medium">{c.name}</span>
        <span className="block truncate text-xs text-muted">{c.account ?? byKind[c.kind]?.label}</span>
      </span>
    ) },
    { key: "type", label: "Type", sort: (c) => c.kind, render: (c) => <span className="text-text2">{byKind[c.kind]?.label ?? c.kind}</span> },
    { key: "status", label: "Status", render: (c) => statusOf(c, !synced(byKind[c.kind]?.category)) },
    { key: "access", label: "Access", render: (c) => c.allowed ? <Status kind="restricted">{c.allowed.length} {c.allowed.length === 1 ? "person" : "people"}</Status> : <span className="text-text2">Everyone</span> },
    { key: "docs", label: "Documents", num: true, sort: (c) => c.documents, render: (c) => synced(byKind[c.kind]?.category) ? c.documents.toLocaleString() : <span className="text-muted">Live</span> },
    { key: "last", label: "Last checked", sort: (c) => c.last_synced_at ?? "", render: (c) => <span className="text-text2">{c.last_synced_at ? ago(c.last_synced_at) : "Never"}</span> },
    { key: "actions", label: "", className: "w-1", render: (c) => {
      const live = !synced(byKind[c.kind]?.category);
      const syncing = c.running || c.status === "syncing";
      return (
        <span className="flex justify-end gap-1">
          <Button size="sm" icon={<RefreshCw className="h-3.5 w-3.5" />} disabled={syncing}
            onClick={() => act(c, `/api/connections/${c.id}/sync`, "POST", live ? "Connection check started." : "Sync started.")}>{live ? "Test" : "Sync now"}</Button>
          <OverflowMenu label={`More actions for ${c.name}`} items={[
            ...(!live ? [{ label: "Full re-sync", onClick: () => act(c, `/api/connections/${c.id}/sync?full=true`, "POST", "Full re-sync started.") }] : []),
            { label: "Edit access", onClick: () => setAccess(c) },
            { label: c.enabled ? "Pause" : "Resume", onClick: () => act(c, `/api/connections/${c.id}?enabled=${!c.enabled}`, "PATCH", c.enabled ? `${c.name} paused.` : `${c.name} resumed.`) },
            { label: "Remove", danger: true, onClick: () => remove(c, live) },
          ]} />
        </span>
      );
    } },
  ];
  const errors = rows.filter((c) => c.status === "error" && c.error);
  return (
    <>
      {errors.map((c) => (
        <div key={c.id} className="flex items-start gap-3 border-b border-line border-l-4 border-l-bad bg-bad-weak/40 px-4 py-2.5 text-sm">
          <Status kind="error">{c.name}</Status>
          <span className="min-w-0 flex-1 break-words font-mono text-xs text-text2">{c.error}</span>
        </div>
      ))}
      <SortableTable columns={columns} rows={rows} rowKey={(c) => c.id} empty={empty} />
      {access && <AccessDialog c={access} onClose={() => setAccess(null)} onSaved={() => { setAccess(null); toast("Access updated."); onChange(); }} />}
    </>
  );
}

function OverflowMenu({ label, items }: { label: string; items: { label: string; onClick: () => void; danger?: boolean }[] }) {
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) ref.current.open = false; };
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, []);
  return (
    <details ref={ref} className="relative">
      <summary aria-label={label} title="More actions" className="grid h-7 w-7 cursor-pointer list-none place-items-center rounded text-text2 hover:bg-hover">
        <MoreHorizontal className="h-4 w-4" />
      </summary>
      <ul role="menu" className="absolute right-0 z-20 mt-1 w-44 rounded border border-line bg-panel py-1 shadow-lg">
        {items.map((it) => (
          <li key={it.label} role="none">
            <button role="menuitem" onClick={() => { if (ref.current) ref.current.open = false; it.onClick(); }}
              className={`block w-full px-3 py-1.5 text-left text-sm hover:bg-hover ${it.danger ? "text-bad" : ""}`}>{it.label}</button>
          </li>
        ))}
      </ul>
    </details>
  );
}

function CatalogPanel({ available, onClose, onPick }: { available: Available[]; onClose: () => void; onPick: (a: Available) => void }) {
  return (
    <SidePanel title="Add connector" description="Choose where to connect." onClose={onClose}>
      <div className="space-y-6">
        {GROUPS.map((g) => (
          <section key={g.category}>
            <h3 className="text-sm font-semibold">{g.title}</h3>
            <p className="mb-2 text-xs text-muted">{g.blurb}</p>
            <ul className="divide-y divide-line rounded border border-line">
              {available.filter((a) => a.category === g.category).map((a) => (
                <li key={a.kind}>
                  <button onClick={() => onPick(a)} className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-hover">
                    <g.Icon className="h-4 w-4 shrink-0 text-muted" aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium">{a.label}</span>
                      <span className="block text-xs text-text2">{a.blurb}</span>
                    </span>
                    {a.oauth_ready && <Status kind="info">One-click</Status>}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </SidePanel>
  );
}

const splitEmails = (s: string) => s.split(/[\s,;]+/).map((e) => e.trim()).filter(Boolean);

function AccessFields({ mode, setMode, emails, setEmails }: { mode: string; setMode: (m: "everyone" | "people") => void; emails: string; setEmails: (s: string) => void }) {
  return (
    <fieldset className="space-y-2">
      <legend className="mb-1 text-xs font-medium text-text2">Who can see results from this source</legend>
      <label className="flex items-center gap-2 text-sm"><input type="radio" checked={mode === "everyone"} onChange={() => setMode("everyone")} className="accent-[var(--accent)]" /> Everyone signed in</label>
      <label className="flex items-center gap-2 text-sm"><input type="radio" checked={mode === "people"} onChange={() => setMode("people")} className="accent-[var(--accent)]" /> Only specific people</label>
      {mode === "people" && (
        <Field label="Email addresses" helper="Separate with commas.">
          <textarea value={emails} onChange={(e) => setEmails(e.target.value)} rows={2} placeholder="ana@company.com, bo@company.com"
            className={`${inputCls} h-auto py-1.5`} />
        </Field>
      )}
      <p className="text-xs text-muted">Slack private channels and Google Drive files also keep their own sharing rules.</p>
    </fieldset>
  );
}

function AccessDialog({ c, onClose, onSaved }: { c: Connection; onClose: () => void; onSaved: () => void }) {
  const [mode, setMode] = useState<"everyone" | "people">(c.allowed ? "people" : "everyone");
  const [emails, setEmails] = useState((c.allowed ?? []).join(", "));
  const [err, setErr] = useState("");
  async function save() {
    const res = await api(`/api/connections/${c.id}/access`, { method: "PUT", body: JSON.stringify({ allowed: mode === "everyone" ? null : splitEmails(emails) }) });
    if (res.ok) onSaved();
    else setErr((await res.json()).detail);
  }
  return (
    <SidePanel title={`Access for ${c.name}`} description="Changes apply immediately to search, queries and dashboards." onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" onClick={save}>Save access</Button></>}>
      <AccessFields mode={mode} setMode={setMode} emails={emails} setEmails={setEmails} />
      {err && <p className="mt-3 text-sm text-bad">{err}</p>}
    </SidePanel>
  );
}

function AddPanel({ a, onClose, onAdded }: { a: Available; onClose: () => void; onAdded: () => void }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [name, setName] = useState(a.label);
  const [credential, setCredential] = useState("");
  const [usePaste, setUsePaste] = useState(!a.oauth_ready);
  const [mode, setMode] = useState<"everyone" | "people">("everyone");
  const [emails, setEmails] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const allowed = mode === "people" ? splitEmails(emails) : null;
  const formId = `connect-${a.kind}`;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    if (mode === "people" && !allowed?.length) return setErr("Add at least one email address, or choose Everyone signed in.");
    if (a.oauth_ready && !usePaste) {
      const q = new URLSearchParams({ name, config: JSON.stringify(values), allowed: (allowed ?? []).join(",") });
      location.href = `${API}/api/oauth/${a.kind}/start?${q}`; // backend records state + PKCE, then redirects to the provider
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
    <SidePanel title={`Connect ${a.label}`} description={a.blurb} onClose={onClose}
      footer={<>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" type="submit" form={formId} loading={busy}>{a.oauth_ready && !usePaste ? `Continue to ${a.label}` : "Connect"}</Button>
      </>}>
      <form id={formId} onSubmit={submit} className="space-y-5">
        {a.guide.length > 0 && (usePaste || !a.oauth_ready) && (
          <section className="rounded border border-line bg-nav p-3">
            <h3 className="text-xs font-semibold">Before you start</h3>
            <ol className="mt-2 list-decimal space-y-1.5 pl-5 text-xs text-text2">{a.guide.map((g) => <li key={g}>{g}</li>)}</ol>
          </section>
        )}
        <Field label="Name" helper="What people will see, for example Sales Slack or Finance warehouse.">
          <input value={name} onChange={(e) => setName(e.target.value)} className={inputCls} />
        </Field>
        {a.config.filter((f) => f.required).map((f) => (
          <Field key={f.key} label={f.label} helper={f.help} required>
            <input value={values[f.key] ?? ""} onChange={(e) => setValues((p) => ({ ...p, [f.key]: e.target.value }))} required className={inputCls} />
          </Field>
        ))}
        {a.paste && usePaste && (
          <Field label={a.paste.label} required={!a.paste.optional} helper={`${a.paste.help}. It's verified with ${a.label}, then stored encrypted and never shown again.`}>
            {a.paste.multiline ? (
              <textarea value={credential} onChange={(e) => setCredential(e.target.value)} required={!a.paste.optional} rows={5} placeholder={a.paste.placeholder}
                spellCheck={false} className={`${inputCls} h-auto py-1.5 font-mono text-xs`} />
            ) : (
              <input type="password" autoComplete="off" value={credential} onChange={(e) => setCredential(e.target.value)} required={!a.paste.optional}
                placeholder={a.paste.placeholder} className={`${inputCls} font-mono`} />
            )}
          </Field>
        )}
        {a.config.some((f) => !f.required) && (
          <details className="rounded border border-line">
            <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-text2 hover:bg-hover">Optional settings</summary>
            <div className="space-y-4 border-t border-line p-3">
              {a.config.filter((f) => !f.required).map((f) => (
                <Field key={f.key} label={f.label} helper={f.help}>
                  <input value={values[f.key] ?? ""} onChange={(e) => setValues((p) => ({ ...p, [f.key]: e.target.value }))} className={inputCls} />
                </Field>
              ))}
            </div>
          </details>
        )}
        <AccessFields mode={mode} setMode={setMode} emails={emails} setEmails={setEmails} />
        {a.oauth_ready && a.paste && (
          <button type="button" onClick={() => setUsePaste(!usePaste)} className="text-xs text-accent underline">
            {usePaste ? `Sign in with ${a.label} instead` : "Use a token instead"}
          </button>
        )}
        {a.oauth && !a.oauth_ready && (
          <p className="text-xs text-muted">For one-click sign-in for your team, set up the {a.label} app in <Link href="/settings" className="text-accent underline">Settings</Link>.</p>
        )}
        {err && <div className="flex items-start gap-2 rounded border border-l-4 border-line border-l-bad bg-panel p-3 text-sm" role="alert"><Status kind="error">Error</Status>{err}</div>}
      </form>
    </SidePanel>
  );
}
