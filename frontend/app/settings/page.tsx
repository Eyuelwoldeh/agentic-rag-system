"use client";

import { useCallback, useEffect, useState } from "react";
import { api, apiJson } from "@/lib/api";
import { useMe } from "@/lib/me";

type OAuthApp = { provider: string; label: string; configured: boolean; client_id: string | null; redirect_uri: string; scopes: string[]; console: string | null; note: string | null };
type UserRow = { id: number; email: string; role: string; created_at: string };

export default function Settings() {
  const me = useMe()!;
  return (
    <main className="mx-auto max-w-3xl space-y-10 px-4 py-6">
      {me.role === "admin" && <AIModel />}
      <Password />
      {me.role === "admin" && <Users myId={me.id} />}
      {me.role === "admin" && <OAuthApps />}
    </main>
  );
}

function Section({ title, desc, children }: { title: string; desc?: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="font-semibold">{title}</h2>
      {desc && <p className="mt-1 text-sm text-muted">{desc}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

const input = "w-full rounded-md border border-line bg-background px-2 py-1.5 text-sm outline-none focus:border-accent";

function Password() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await apiJson("/api/auth/password", { method: "POST", body: JSON.stringify({ current, new: next }) });
      setMsg("Password changed. Other sessions were signed out.");
      setCurrent("");
      setNext("");
    } catch (err) {
      setMsg((err as Error).message);
    }
  }

  return (
    <Section title="Your password">
      <form onSubmit={submit} className="flex flex-wrap gap-2">
        <input type="password" placeholder="Current password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required className={`${input} sm:w-56`} />
        <input type="password" placeholder="New password (10+ chars)" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={10} className={`${input} sm:w-56`} />
        <button className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-background">Change</button>
      </form>
      {msg && <p className="mt-2 text-sm text-muted">{msg}</p>}
    </Section>
  );
}

function Users({ myId }: { myId: number }) {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [created, setCreated] = useState<{ email: string; temporary_password: string } | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => apiJson<UserRow[]>("/api/users").then(setUsers), []);
  useEffect(() => {
    load();
  }, [load]);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    try {
      setCreated(await apiJson("/api/users", { method: "POST", body: JSON.stringify({ email, role }) }));
      setEmail("");
      load();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  async function remove(u: UserRow) {
    if (!confirm(`Delete ${u.email}?`)) return;
    await api(`/api/users/${u.id}`, { method: "DELETE" });
    load();
  }

  return (
    <Section title="Team" desc="Admins manage connectors and users. Members can chat, upload, and see their own runs.">
      <ul className="divide-y divide-line rounded-lg border border-line bg-panel text-sm">
        {users.map((u) => (
          <li key={u.id} className="flex items-center gap-3 px-3 py-2">
            <span className="flex-1 truncate">{u.email}</span>
            <span className="text-xs text-muted">{u.role}</span>
            {u.id !== myId && <button onClick={() => remove(u)} className="text-xs text-muted hover:text-bad">Remove</button>}
          </li>
        ))}
      </ul>
      <form onSubmit={add} className="mt-3 flex flex-wrap gap-2">
        <input type="email" placeholder="teammate@company.com" value={email} onChange={(e) => setEmail(e.target.value)} required className={`${input} flex-1`} />
        <select value={role} onChange={(e) => setRole(e.target.value)} className={`${input} w-auto`} aria-label="Role">
          <option value="member">Member</option>
          <option value="admin">Admin</option>
        </select>
        <button className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-background">Add</button>
      </form>
      {err && <p className="mt-2 text-sm text-bad">{err}</p>}
      {created && (
        <p className="mt-3 rounded-md border border-warn/40 bg-warn/10 p-3 text-sm">
          Share this temporary password with <b>{created.email}</b>. It won&apos;t be shown again, so ask them to change it after signing in:{" "}
          <code className="select-all font-mono">{created.temporary_password}</code>
        </p>
      )}
    </Section>
  );
}

function OAuthApps() {
  const [apps, setApps] = useState<OAuthApp[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const load = useCallback(() => apiJson<OAuthApp[]>("/api/oauth/apps").then(setApps), []);
  useEffect(() => {
    load();
  }, [load]);

  return (
    <Section title="Sign-in apps for connectors"
      desc="Register an OAuth app once per provider so admins can connect with one click. Secrets are encrypted and never shown again.">
      <ul className="space-y-2">
        {apps.map((a) => (
          <li key={a.provider} className="rounded-lg border border-line bg-panel p-3 text-sm">
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-medium">{a.label}</span>
              <span className={a.configured ? "text-ok" : "text-muted"}>{a.configured ? `✓ configured · ${a.client_id}` : "not set up"}</span>
              <span className="ml-auto flex gap-2">
                <button onClick={() => setEditing(editing === a.provider ? null : a.provider)} className="rounded border border-line px-2 py-1 text-xs hover:border-accent">
                  {a.configured ? "Update" : "Set up"}
                </button>
                {a.configured && (
                  <button onClick={async () => { await api(`/api/oauth/apps/${a.provider}`, { method: "DELETE" }); load(); }}
                    className="rounded border border-line px-2 py-1 text-xs hover:border-bad hover:text-bad">Remove</button>
                )}
              </span>
            </div>
            {editing === a.provider && <AppForm app={a} onSaved={() => { setEditing(null); load(); }} />}
          </li>
        ))}
      </ul>
    </Section>
  );
}

function AppForm({ app, onSaved }: { app: OAuthApp; onSaved: () => void }) {
  const [clientId, setClientId] = useState(app.client_id ?? "");
  const [secret, setSecret] = useState("");
  const [err, setErr] = useState("");

  async function save(e: React.FormEvent) {
    e.preventDefault();
    try {
      await apiJson(`/api/oauth/apps/${app.provider}`, { method: "PUT", body: JSON.stringify({ client_id: clientId, client_secret: secret }) });
      onSaved();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  return (
    <form onSubmit={save} className="mt-3 space-y-2 border-t border-line pt-3">
      <ol className="list-decimal space-y-1 pl-5 text-xs text-muted">
        <li>Create an OAuth app in the {app.console ? <a href={app.console} target="_blank" rel="noreferrer" className="text-accent underline">{app.label} developer console</a> : `${app.label} developer console`}.</li>
        <li>Add this redirect URL: <code className="select-all font-mono text-foreground">{app.redirect_uri}</code></li>
        {app.scopes.length > 0 && <li>Scopes: <code className="font-mono text-foreground">{app.scopes.join(", ")}</code></li>}
        {app.note && <li>{app.note}</li>}
        <li>Paste the client ID and secret below.</li>
      </ol>
      <input placeholder="Client ID" value={clientId} onChange={(e) => setClientId(e.target.value)} required className={input} />
      <input type="password" placeholder="Client secret" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} required className={input} />
      {err && <p className="text-sm text-bad">{err}</p>}
      <button className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-background">Save</button>
    </form>
  );
}

type AIState = { primary: "anthropic" | "openai"; models: Record<string, string>; keys: Record<string, "settings" | "env" | null>; labels: Record<string, string> };
const KEY_HELP: Record<string, { placeholder: string; where: string }> = {
  anthropic: { placeholder: "sk-ant-…", where: "console.anthropic.com → API keys" },
  openai: { placeholder: "sk-proj-…", where: "platform.openai.com → API keys" },
};

function AIModel() {
  const [s, setS] = useState<AIState | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const load = useCallback(() => apiJson<AIState>("/api/ai").then(setS), []);
  useEffect(() => {
    load();
  }, [load]);
  if (!s) return null;

  async function save(body: object, ok: string) {
    setMsg(null);
    try {
      await apiJson("/api/ai", { method: "PUT", body: JSON.stringify(body) });
      setMsg({ ok: true, text: ok });
      load();
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
  }

  const configured = Object.entries(s.keys).filter(([, v]) => v).map(([k]) => k);
  return (
    <Section title="AI model" desc="Which AI writes answers. If the main one runs out of credits or its key stops working, the backup takes over automatically.">
      <div className="space-y-3">
        {(["anthropic", "openai"] as const).map((p) => (
          <ProviderRow key={p} provider={p} label={s.labels[p]} keySource={s.keys[p]} model={s.models[p]} primary={s.primary === p}
            onPrimary={() => save({ primary: p }, `${s.labels[p]} is now the main AI.`)}
            onSave={(body, ok) => save(body, ok)} />
        ))}
      </div>
      {configured.length === 0 && <p className="mt-2 text-sm text-warn">No AI connected yet: chat will show search results and calculated insights only.</p>}
      {msg && <p className={`mt-2 text-sm ${msg.ok ? "text-ok" : "text-bad"}`} role="status">{msg.text}</p>}
    </Section>
  );
}

function ProviderRow({ provider, label, keySource, model, primary, onPrimary, onSave }: {
  provider: "anthropic" | "openai"; label: string; keySource: "settings" | "env" | null; model: string; primary: boolean;
  onPrimary: () => void; onSave: (body: object, ok: string) => void;
}) {
  const [key, setKey] = useState("");
  const [models, setModels] = useState<string[] | null>(null);
  const [pick, setPick] = useState(model);
  useEffect(() => {
    setPick(model);
    if (!keySource) return setModels(null);
    apiJson<{ models: string[] }>(`/api/ai/models/${provider}`).then((r) => setModels(r.models)).catch(() => setModels([]));
  }, [provider, keySource, model]);

  return (
    <div className="rounded-lg border border-line bg-panel p-3 text-sm">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-medium">{label}</span>
        <span className={keySource ? "text-ok" : "text-muted"}>
          {keySource === "settings" ? "✓ key saved" : keySource === "env" ? "✓ key from server config" : "not connected"}
        </span>
        {primary ? <span className="rounded-full bg-accent/15 px-2 py-0.5 text-xs text-accent">main</span>
          : keySource && <button onClick={onPrimary} className="text-xs text-muted underline hover:text-foreground">Make main</button>}
      </div>
      <form className="mt-2 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); onSave({ keys: { [provider]: key } }, `${label} key saved.`); setKey(""); }}>
        <input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder={keySource ? "Paste a new key to replace it" : KEY_HELP[provider].placeholder}
          className={`${input} min-w-48 flex-1 font-mono`} aria-label={`${label} API key`} />
        <button disabled={!key.trim()} className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-background disabled:opacity-40">{keySource ? "Replace" : "Connect"}</button>
        {keySource === "settings" && (
          <button type="button" onClick={() => confirm(`Remove the saved ${label} key?`) && onSave({ keys: { [provider]: "" } }, `${label} key removed.`)}
            className="rounded-md border border-line px-3 py-1.5 text-sm hover:border-bad hover:text-bad">Remove</button>
        )}
      </form>
      <p className="mt-1 text-xs text-muted">Get a key at {KEY_HELP[provider].where}. It&apos;s checked first, then stored encrypted.</p>
      {keySource && (
        <label className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <span className="text-muted">Model</span>
          {models && models.length > 0 ? (
            <select value={pick} onChange={(e) => { setPick(e.target.value); onSave({ models: { [provider]: e.target.value } }, `${label} will use ${e.target.value}.`); }}
              className="rounded border border-line bg-background px-1.5 py-1">
              {!models.includes(pick) && <option value={pick}>{pick}</option>}
              {models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          ) : (
            <span className="font-mono">{pick}</span>
          )}
          {provider === "openai" && <span className="text-muted">· web search and connected apps (MCP) are Claude-only</span>}
        </label>
      )}
    </div>
  );
}
