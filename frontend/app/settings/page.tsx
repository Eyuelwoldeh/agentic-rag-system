"use client";

import { Copy, ExternalLink, Trash2, UserPlus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import SortableTable, { type Column } from "@/components/SortableTable";
import { Button, Field, inputCls, Page, PageHeader, Panel, Status, useUI } from "@/components/ui";
import { api, apiJson } from "@/lib/api";
import { useMe } from "@/lib/me";
import { ago } from "@/lib/time";
import DataRetention from "./data";
import { Groups, SingleSignOn } from "./identity";

type Tab = "ai" | "team" | "groups" | "sso" | "apps" | "notifications" | "slack" | "data" | "account";

export default function Settings() {
  const me = useMe()!;
  const admin = me.role === "admin";
  const tabs: [Tab, string][] = admin ? [["ai", "AI model"], ["team", "Team"], ["groups", "Groups"], ["sso", "Single sign-on"], ["apps", "Sign-in apps"],
    ["notifications", "Notifications"], ["slack", "Slack bot"], ["data", "Data and retention"], ["account", "Your account"]] : [["account", "Your account"]];
  const [tab, setTab] = useState<Tab>(tabs[0][0]);
  return (
    <>
      <PageHeader title="Settings" description={admin ? "Organization settings and your account." : "Your account."} />
      <div className="border-b border-line bg-panel px-6">
        <div role="tablist" className="flex gap-6 overflow-x-auto">
          {tabs.map(([id, label]) => (
            <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
              className={`-mb-px h-10 border-b-2 text-sm whitespace-nowrap ${tab === id ? "border-accent font-medium text-foreground" : "border-transparent text-text2 hover:text-foreground"}`}>
              {label}
            </button>
          ))}
        </div>
      </div>
      <Page>
        {tab === "ai" && <AIModel />}
        {tab === "team" && <Team myId={me.id} />}
        {tab === "groups" && <Groups />}
        {tab === "sso" && <SingleSignOn />}
        {tab === "apps" && <OAuthApps />}
        {tab === "notifications" && <NotificationSettings />}
        {tab === "slack" && <SlackBot />}
        {tab === "data" && <DataRetention />}
        {tab === "account" && <Account email={me.email} role={me.role} />}
      </Page>
    </>
  );
}

/* ---------- account ---------- */

function Account({ email, role }: { email: string; role: string }) {
  const { toast } = useUI();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [err, setErr] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    try {
      await apiJson("/api/auth/password", { method: "POST", body: JSON.stringify({ current, new: next }) });
      toast("Password changed. You've been signed out on other devices.");
      setCurrent("");
      setNext("");
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Panel title="Profile">
        <dl className="grid grid-cols-[8rem_1fr] gap-y-2 text-sm">
          <dt className="text-text2">Email</dt><dd>{email}</dd>
          <dt className="text-text2">Role</dt><dd>{role === "admin" ? "Administrator" : "Member"}</dd>
        </dl>
      </Panel>
      <Panel title="Change password">
        <form onSubmit={submit} className="space-y-4">
          <Field label="Current password" error={err || undefined}>
            <input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required className={inputCls} />
          </Field>
          <Field label="New password" helper="At least 10 characters.">
            <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={10} className={inputCls} />
          </Field>
          <Button variant="primary" type="submit">Change password</Button>
        </form>
      </Panel>
    </div>
  );
}

/* ---------- team ---------- */

type UserRow = { id: number; email: string; role: string; created_at: string; disabled?: boolean };

function Team({ myId }: { myId: number }) {
  const { confirm, toast } = useUI();
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
    if (!(await confirm({ title: `Remove ${u.email}?`, action: "Remove user", danger: true, body: "They'll be signed out and lose access immediately. Their uploads stay in the Library." }))) return;
    await api(`/api/users/${u.id}`, { method: "DELETE" });
    toast(`${u.email} removed.`);
    load();
  }
  async function changeRole(u: UserRow, next: string) {
    const label = next === "admin" ? "Administrator" : "Member";
    if (!(await confirm({ title: `Make ${u.email} ${next === "admin" ? "an administrator" : "a member"}?`, action: `Make ${label.toLowerCase()}`,
      body: next === "admin" ? "Administrators manage connectors, AI settings, people and the audit log." : "They lose access to administration pages." }))) return;
    try {
      await apiJson(`/api/users/${u.id}`, { method: "PATCH", body: JSON.stringify({ role: next }) });
      toast(`${u.email} is now ${next === "admin" ? "an administrator" : "a member"}.`);
      load();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  const cols: Column<UserRow>[] = [
    { key: "email", label: "Email", sort: (u) => u.email, render: (u) => <span className="font-medium">{u.email}{u.id === myId && <span className="ml-2 text-xs font-normal text-muted">(you)</span>}</span> },
    { key: "role", label: "Role", sort: (u) => u.role, render: (u) => u.id === myId ? "Administrator" : (
      <select value={u.role} onChange={(e) => changeRole(u, e.target.value)} aria-label={`Role for ${u.email}`} className={`${inputCls} h-7 w-36`}>
        <option value="member">Member</option>
        <option value="admin">Administrator</option>
      </select>
    ) },
    { key: "status", label: "Status", sort: (u) => String(!!u.disabled), render: (u) => u.disabled ? <Status kind="error">Deactivated</Status> : <Status kind="success">Active</Status> },
    { key: "added", label: "Added", sort: (u) => u.created_at, render: (u) => <span className="text-text2">{ago(u.created_at)}</span> },
    { key: "actions", label: "", className: "w-1", render: (u) => u.id !== myId && (
      <Button size="sm" variant="ghost" icon={<Trash2 className="h-3.5 w-3.5" />} onClick={() => remove(u)} aria-label={`Remove ${u.email}`}>Remove</Button>
    ) },
  ];

  return (
    <div className="space-y-6">
      <Panel title="Add a person" description="Administrators manage connectors, AI settings and people. Members can ask questions, upload files and see their own activity.">
        <form onSubmit={add} className="flex flex-wrap items-end gap-3">
          <div className="min-w-64 flex-1">
            <Field label="Email address"><input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required placeholder="name@company.com" className={inputCls} /></Field>
          </div>
          <Field label="Role">
            <select value={role} onChange={(e) => setRole(e.target.value)} className={`${inputCls} w-40`}>
              <option value="member">Member</option>
              <option value="admin">Administrator</option>
            </select>
          </Field>
          <Button variant="primary" type="submit" icon={<UserPlus className="h-4 w-4" />}>Add person</Button>
        </form>
        {err && <p className="mt-3 text-sm text-bad">{err}</p>}
        {created && (
          <div className="mt-4 flex flex-wrap items-center gap-3 rounded border border-line border-l-4 border-l-accent bg-accent-weak/40 p-3 text-sm">
            <span className="flex-1">Share this one-time password with <b>{created.email}</b>. It won&apos;t be shown again; they should change it after signing in.</span>
            <code className="rounded bg-panel px-2 py-1 font-mono">{created.temporary_password}</code>
            <Button size="sm" icon={<Copy className="h-3.5 w-3.5" />} onClick={() => navigator.clipboard.writeText(created.temporary_password).then(() => toast("Password copied."))}>Copy</Button>
          </div>
        )}
      </Panel>
      <Panel title={`People (${users.length})`} flush>
        <SortableTable columns={cols} rows={users} rowKey={(u) => u.id} initialSort={{ key: "email", dir: 1 }} />
      </Panel>
    </div>
  );
}

/* ---------- AI model ---------- */

type AIState = { primary: "anthropic" | "openai"; models: Record<string, string>; keys: Record<string, "settings" | "env" | null>; labels: Record<string, string> };
const KEY_HELP: Record<string, { placeholder: string; where: string; url: string }> = {
  anthropic: { placeholder: "sk-ant-…", where: "Anthropic Console", url: "https://console.anthropic.com/settings/keys" },
  openai: { placeholder: "sk-proj-…", where: "OpenAI Platform", url: "https://platform.openai.com/api-keys" },
};

function AIModel() {
  const { toast } = useUI();
  const [s, setS] = useState<AIState | null>(null);
  const load = useCallback(() => apiJson<AIState>("/api/ai").then(setS), []);
  useEffect(() => {
    load();
  }, [load]);
  if (!s) return null;

  async function save(body: object, ok: string) {
    try {
      await apiJson("/api/ai", { method: "PUT", body: JSON.stringify(body) });
      toast(ok);
      load();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }
  const none = !Object.values(s.keys).some(Boolean);
  return (
    <div className="space-y-4">
      <p className="max-w-3xl text-sm text-text2">The main provider writes answers. If it runs out of credits or its key stops working, the backup takes over automatically.</p>
      {none && (
        <div className="flex items-start gap-3 rounded border border-line border-l-4 border-l-warn bg-panel p-3 text-sm">
          <Status kind="warning">Not connected</Status>
          <span className="text-text2">Until a provider is connected, Ask returns search results and calculated spreadsheet findings only.</span>
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {(["anthropic", "openai"] as const).map((p) => (
          <ProviderPanel key={p} provider={p} label={s.labels[p]} keySource={s.keys[p]} model={s.models[p]} primary={s.primary === p}
            onPrimary={() => save({ primary: p }, `${s.labels[p]} is now the main provider.`)} onSave={save} />
        ))}
      </div>
    </div>
  );
}

function ProviderPanel({ provider, label, keySource, model, primary, onPrimary, onSave }: {
  provider: "anthropic" | "openai"; label: string; keySource: "settings" | "env" | null; model: string; primary: boolean;
  onPrimary: () => void; onSave: (body: object, ok: string) => void;
}) {
  const { confirm } = useUI();
  const [key, setKey] = useState("");
  const [models, setModels] = useState<string[] | null>(null);
  useEffect(() => {
    if (!keySource) return setModels(null);
    apiJson<{ models: string[] }>(`/api/ai/models/${provider}`).then((r) => setModels(r.models)).catch(() => setModels([]));
  }, [provider, keySource]);

  const status = keySource ? <Status kind="success">{keySource === "env" ? "Connected (server config)" : "Connected"}</Status> : <Status kind="neutral">Not connected</Status>;
  return (
    <Panel title={label} actions={<>{status}{primary ? <Status kind="info">Main</Status> : keySource && <Button size="sm" onClick={onPrimary}>Make main</Button>}</>}>
      <div className="space-y-4">
        <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); onSave({ keys: { [provider]: key } }, `${label} key saved.`); setKey(""); }}>
          <div className="flex-1">
            <Field label={keySource ? "Replace API key" : "API key"}
              helper={<>Create one in the <a href={KEY_HELP[provider].url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-accent underline">{KEY_HELP[provider].where}<ExternalLink className="h-3 w-3" /></a>. It&apos;s verified, then stored encrypted.</>}>
              <input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder={KEY_HELP[provider].placeholder} className={`${inputCls} font-mono`} />
            </Field>
          </div>
          <Button type="submit" variant="primary" disabled={!key.trim()} className="mb-5">{keySource ? "Replace" : "Connect"}</Button>
        </form>
        {keySource && (
          <Field label="Model" helper={provider === "openai" ? "Web search and connected apps (MCP) are available with Claude only." : undefined}>
            {models && models.length > 0 ? (
              <select value={model} onChange={(e) => onSave({ models: { [provider]: e.target.value } }, `${label} will use ${e.target.value}.`)} className={inputCls}>
                {!models.includes(model) && <option value={model}>{model}</option>}
                {models.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            ) : <input value={model} readOnly className={`${inputCls} font-mono text-text2`} />}
          </Field>
        )}
        {keySource === "settings" && (
          <Button variant="ghost" size="sm" icon={<Trash2 className="h-3.5 w-3.5" />}
            onClick={async () => (await confirm({ title: `Remove the ${label} key?`, action: "Remove key", danger: true })) && onSave({ keys: { [provider]: "" } }, `${label} key removed.`)}>
            Remove key
          </Button>
        )}
      </div>
    </Panel>
  );
}

/* ---------- OAuth sign-in apps ---------- */

type OAuthApp = { provider: string; label: string; configured: boolean; client_id: string | null; redirect_uri: string; scopes: string[]; console: string | null; note: string | null };

function OAuthApps() {
  const { confirm, toast } = useUI();
  const [apps, setApps] = useState<OAuthApp[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const load = useCallback(() => apiJson<OAuthApp[]>("/api/oauth/apps").then(setApps), []);
  useEffect(() => {
    load();
  }, [load]);
  return (
    <div className="space-y-4">
      <p className="max-w-3xl text-sm text-text2">Register an OAuth app once per provider so administrators can connect sources with one click instead of pasting tokens. Client secrets are encrypted and never shown again.</p>
      {apps.map((a) => (
        <Panel key={a.provider} title={a.label}
          actions={<>
            {a.configured ? <Status kind="success">Configured</Status> : <Status kind="neutral">Not set up</Status>}
            <Button size="sm" onClick={() => setEditing(editing === a.provider ? null : a.provider)}>{a.configured ? "Update" : "Set up"}</Button>
            {a.configured && <Button size="sm" variant="ghost" onClick={async () => {
              if (await confirm({ title: `Remove the ${a.label} sign-in app?`, action: "Remove", danger: true, body: "Existing connections keep working; new ones will need a pasted token." })) {
                await api(`/api/oauth/apps/${a.provider}`, { method: "DELETE" });
                toast(`${a.label} sign-in app removed.`);
                load();
              }
            }}>Remove</Button>}
          </>}>
          {a.configured && editing !== a.provider && <p className="text-sm text-text2">Client ID <code className="font-mono text-foreground">{a.client_id}</code></p>}
          {!a.configured && editing !== a.provider && <p className="text-sm text-text2">Not configured. Administrators can still connect {a.label} by pasting a token.</p>}
          {editing === a.provider && <AppForm app={a} onSaved={() => { setEditing(null); toast(`${a.label} sign-in app saved.`); load(); }} />}
        </Panel>
      ))}
    </div>
  );
}

function AppForm({ app, onSaved }: { app: OAuthApp; onSaved: () => void }) {
  const { toast } = useUI();
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
    <form onSubmit={save} className="space-y-4">
      <ol className="list-decimal space-y-1.5 pl-5 text-sm text-text2">
        <li>Create an OAuth app in the {app.console ? <a href={app.console} target="_blank" rel="noreferrer" className="text-accent underline">{app.label} developer console</a> : `${app.label} developer console`}.</li>
        <li className="space-y-1">
          <span>Add this redirect URL:</span>
          <span className="flex items-center gap-2">
            <code className="rounded bg-hover px-2 py-1 font-mono text-xs text-foreground">{app.redirect_uri}</code>
            <Button type="button" size="sm" variant="ghost" icon={<Copy className="h-3.5 w-3.5" />} onClick={() => navigator.clipboard.writeText(app.redirect_uri).then(() => toast("Redirect URL copied."))}>Copy</Button>
          </span>
        </li>
        {app.scopes.length > 0 && <li>Request these scopes: <code className="font-mono text-xs text-foreground">{app.scopes.join(", ")}</code></li>}
        {app.note && <li>{app.note}</li>}
        <li>Enter the client ID and secret below.</li>
      </ol>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Client ID" required><input value={clientId} onChange={(e) => setClientId(e.target.value)} required className={inputCls} /></Field>
        <Field label="Client secret" required error={err || undefined}><input type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} required className={inputCls} /></Field>
      </div>
      <Button variant="primary" type="submit">Save</Button>
    </form>
  );
}

/* ---------- notifications ---------- */

function NotificationSettings() {
  const { toast } = useUI();
  const [s, setS] = useState<{ document_updates: boolean } | null>(null);
  useEffect(() => {
    apiJson<{ document_updates: boolean }>("/api/settings/notifications").then(setS);
  }, []);
  if (!s) return null;
  async function toggle(document_updates: boolean) {
    try {
      setS(await apiJson("/api/settings/notifications", { method: "PUT", body: JSON.stringify({ document_updates }) }));
      toast("Notification settings saved.");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }
  return (
    <Panel title="Notifications" description="People always get notified when someone shares a conversation with them or mentions them in a comment.">
      <label className="flex items-start gap-3 text-sm">
        <input type="checkbox" className="mt-0.5" checked={s.document_updates} onChange={(e) => toggle(e.target.checked)} />
        <span>
          <span className="block font-medium">Notify people when documents they can see are updated</span>
          <span className="block text-xs text-muted">When a file in the Library is replaced, everyone with access gets a notification with a summary of what changed.</span>
        </span>
      </label>
    </Panel>
  );
}

/* ---------- Slack bot ---------- */

type SlackState = { signing_secret: boolean; bot_token: boolean; request_url: string; scopes: string[] };

function SlackBot() {
  const { confirm, toast } = useUI();
  const [s, setS] = useState<SlackState | null>(null);
  const [secret, setSecret] = useState("");
  const [token, setToken] = useState("");
  useEffect(() => {
    apiJson<SlackState>("/api/slack/settings").then(setS);
  }, []);
  if (!s) return null;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    try {
      setS(await apiJson("/api/slack/settings", { method: "PUT", body: JSON.stringify({ signing_secret: secret, bot_token: token }) }));
      setSecret("");
      setToken("");
      toast("Slack bot settings saved.");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }
  async function remove() {
    if (!(await confirm({ title: "Turn off the Slack bot?", action: "Remove", danger: true, body: "The stored signing secret and bot token are deleted. The bot stops answering in Slack." }))) return;
    setS(await apiJson("/api/slack/settings", { method: "DELETE" }));
    toast("Slack bot removed.");
  }
  const ready = s.signing_secret && s.bot_token;
  const copy = (text: string, what: string) => navigator.clipboard.writeText(text).then(() => toast(`${what} copied.`));

  return (
    <div className="space-y-4">
      <p className="max-w-3xl text-sm text-text2">
        People can mention the bot in a channel or send it a direct message to ask a question. Each answer uses only what that person can see here,
        matched by their Slack email address. People without an account are asked to contact an administrator.
      </p>
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Set up in Slack" actions={ready ? <Status kind="success">Connected</Status> : <Status kind="neutral">Not set up</Status>}>
          <ol className="list-decimal space-y-2 pl-5 text-sm text-text2">
            <li>Go to <a href="https://api.slack.com/apps" target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-accent underline">api.slack.com/apps<ExternalLink className="h-3 w-3" /></a>, select <b>Create New App</b>, then <b>From scratch</b>, and pick your workspace.</li>
            <li>Under <b>OAuth &amp; Permissions</b>, add these bot token scopes: <code className="font-mono text-xs text-foreground">{s.scopes.join(", ")}</code></li>
            <li>Select <b>Install to Workspace</b>, then copy the <b>Bot User OAuth Token</b> (starts with xoxb-).</li>
            <li>Under <b>Basic Information</b>, copy the <b>Signing Secret</b>.</li>
            <li className="space-y-1">
              <span>Under <b>Event Subscriptions</b>, turn events on and paste this Request URL (save the secret here first so Slack can verify it):</span>
              <span className="flex flex-wrap items-center gap-2">
                <code className="rounded bg-hover px-2 py-1 font-mono text-xs break-all text-foreground">{s.request_url}</code>
                <Button type="button" size="sm" variant="ghost" icon={<Copy className="h-3.5 w-3.5" />} onClick={() => copy(s.request_url, "Request URL")}>Copy</Button>
              </span>
            </li>
            <li>Subscribe to the bot events <code className="font-mono text-xs text-foreground">app_mention</code> and <code className="font-mono text-xs text-foreground">message.im</code>, then save.</li>
            <li>Under <b>App Home</b>, allow people to send messages to the app. Invite the bot to channels with /invite.</li>
          </ol>
          {!s.request_url.startsWith("https://") && (
            <p className="mt-3 text-xs text-muted">Slack needs a public https address. Set PUBLIC_URL to your deployed address or a tunnel.</p>
          )}
        </Panel>
        <Panel title="Credentials" description="Stored encrypted and never shown again. Leave a field blank to keep the saved value.">
          <form onSubmit={save} className="space-y-4">
            <Field label="Signing secret" helper={s.signing_secret ? "Saved." : "Basic Information, App Credentials."}>
              <input type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} className={`${inputCls} font-mono`} />
            </Field>
            <Field label="Bot token" helper={s.bot_token ? "Saved." : "OAuth & Permissions, Bot User OAuth Token."}>
              <input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder="xoxb-…" className={`${inputCls} font-mono`} />
            </Field>
            <div className="flex gap-2">
              <Button variant="primary" type="submit" disabled={!secret.trim() && !token.trim()}>Save</Button>
              {(s.signing_secret || s.bot_token) && <Button type="button" variant="ghost" icon={<Trash2 className="h-3.5 w-3.5" />} onClick={remove}>Remove</Button>}
            </div>
          </form>
        </Panel>
      </div>
    </div>
  );
}
