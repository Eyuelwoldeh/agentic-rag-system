"use client";

import { Copy, KeyRound, Plus, Trash2, UserPlus, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import SortableTable, { type Column } from "@/components/SortableTable";
import { Button, EmptyState, Field, inputCls, Panel, Status, useUI } from "@/components/ui";
import { api, apiJson } from "@/lib/api";
import { ago } from "@/lib/time";

function CopyValue({ value, label }: { value: string; label: string }) {
  const { toast } = useUI();
  return (
    <span className="flex flex-wrap items-center gap-2">
      <code className="rounded bg-hover px-2 py-1 font-mono text-xs break-all text-foreground">{value}</code>
      <Button type="button" size="sm" variant="ghost" icon={<Copy className="h-3.5 w-3.5" />}
        onClick={() => navigator.clipboard.writeText(value).then(() => toast(`${label} copied.`))}>Copy</Button>
    </span>
  );
}

/* ---------- single sign-on (OIDC) + SCIM provisioning ---------- */

type SSO = { issuer: string; client_id: string; domains: string[]; default_role: "member" | "admin"; require_sso: boolean; has_secret: boolean; enabled: boolean; redirect_uri: string };

export function SingleSignOn() {
  const { confirm, toast } = useUI();
  const [s, setS] = useState<SSO | null>(null);
  const [form, setForm] = useState({ issuer: "", client_id: "", client_secret: "", domains: "", default_role: "member", require_sso: false });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => apiJson<SSO>("/api/sso/settings").then((r) => {
    setS(r);
    setForm({ issuer: r.issuer, client_id: r.client_id, client_secret: "", domains: r.domains.join(", "), default_role: r.default_role, require_sso: r.require_sso });
  }), []);
  useEffect(() => {
    load();
  }, [load]);
  if (!s) return null;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      await apiJson("/api/sso/settings", { method: "PUT", body: JSON.stringify({ ...form, domains: form.domains.split(/[\s,;]+/).filter(Boolean) }) });
      toast("Single sign-on settings saved.");
      load();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!(await confirm({ title: "Turn off single sign-on?", action: "Turn off", danger: true, body: "People will need a password to sign in. Members created by SSO don't have one; an administrator can remove and re-add them." }))) return;
    await api("/api/sso/settings", { method: "DELETE" });
    toast("Single sign-on turned off.");
    load();
  }
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value });

  return (
    <div className="space-y-6">
      <Panel title="Single sign-on (OpenID Connect)" description="Works with Okta, Microsoft Entra ID, Google Workspace and any OpenID Connect provider."
        actions={<>{s.enabled ? <Status kind="success">On</Status> : <Status kind="neutral">Off</Status>}
          {s.enabled && <Button size="sm" variant="ghost" onClick={remove}>Turn off</Button>}</>}>
        <form onSubmit={save} className="space-y-4">
          <ol className="list-decimal space-y-1.5 pl-5 text-sm text-text2">
            <li>In your identity provider, create a web application (OpenID Connect, authorization code).</li>
            <li className="space-y-1"><span>Set its sign-in redirect URL to:</span><CopyValue value={s.redirect_uri} label="Redirect URL" /></li>
            <li>Enter the issuer URL, client ID and client secret below.</li>
          </ol>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Issuer URL" required helper="For example https://acme.okta.com, https://login.microsoftonline.com/<tenant>/v2.0 or https://accounts.google.com">
              <input value={form.issuer} onChange={set("issuer")} required placeholder="https://" className={inputCls} />
            </Field>
            <Field label="Client ID" required><input value={form.client_id} onChange={set("client_id")} required className={inputCls} /></Field>
            <Field label="Client secret" required={!s.has_secret} helper={s.has_secret ? "Stored encrypted. Leave blank to keep it." : "Stored encrypted and never shown again."}>
              <input type="password" autoComplete="off" value={form.client_secret} onChange={set("client_secret")} required={!s.has_secret} className={inputCls} />
            </Field>
            <Field label="Allowed email domains" helper="People from these domains get an account on first sign-in. Leave empty to allow only existing people.">
              <input value={form.domains} onChange={set("domains")} placeholder="company.com" className={inputCls} />
            </Field>
            <Field label="Role for new people">
              <select value={form.default_role} onChange={set("default_role")} className={inputCls}>
                <option value="member">Member</option>
                <option value="admin">Administrator</option>
              </select>
            </Field>
          </div>
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" checked={form.require_sso} onChange={set("require_sso")} className="mt-0.5 accent-[var(--accent)]" />
            <span>Require single sign-on for members<span className="block text-xs text-muted">Members can no longer sign in with a password. Administrators can, so you can&apos;t be locked out.</span></span>
          </label>
          {err && <p className="text-sm text-bad">{err}</p>}
          <Button variant="primary" type="submit" loading={busy}>Save</Button>
        </form>
      </Panel>
      <Scim />
    </div>
  );
}

function Scim() {
  const { confirm, toast } = useUI();
  const [s, setS] = useState<{ configured: boolean; created_at: string | null; base_url: string } | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const load = useCallback(() => apiJson<typeof s>("/api/scim/token").then(setS), []);
  useEffect(() => {
    load();
  }, [load]);
  if (!s) return null;

  async function generate() {
    if (s?.configured && !(await confirm({ title: "Replace the provisioning token?", action: "Replace token", danger: true, body: "The current token stops working immediately. Update it in your identity provider." }))) return;
    setToken((await apiJson<{ token: string }>("/api/scim/token", { method: "POST" })).token);
    load();
  }
  async function revoke() {
    if (!(await confirm({ title: "Revoke the provisioning token?", action: "Revoke", danger: true, body: "Your identity provider will no longer be able to create, update or deactivate people." }))) return;
    await api("/api/scim/token", { method: "DELETE" });
    setToken(null);
    toast("Provisioning token revoked.");
    load();
  }
  return (
    <Panel title="User provisioning (SCIM 2.0)" description="Let your identity provider create, update and deactivate people and groups automatically."
      actions={<>{s.configured ? <Status kind="success">On</Status> : <Status kind="neutral">Off</Status>}
        <Button size="sm" icon={<KeyRound className="h-3.5 w-3.5" />} onClick={generate}>{s.configured ? "Replace token" : "Generate token"}</Button>
        {s.configured && <Button size="sm" variant="ghost" onClick={revoke}>Revoke</Button>}</>}>
      <dl className="grid gap-y-3 text-sm sm:grid-cols-[10rem_1fr]">
        <dt className="text-text2">SCIM base URL</dt><dd><CopyValue value={s.base_url} label="Base URL" /></dd>
        <dt className="text-text2">Authentication</dt><dd>HTTP header (bearer token)</dd>
        {s.configured && <><dt className="text-text2">Token created</dt><dd>{ago(s.created_at)}</dd></>}
      </dl>
      {token && (
        <div className="mt-4 space-y-2 rounded border border-line border-l-4 border-l-accent bg-accent-weak/40 p-3 text-sm">
          <p>Copy this token into your identity provider now. It won&apos;t be shown again.</p>
          <CopyValue value={token} label="Token" />
        </div>
      )}
    </Panel>
  );
}

/* ---------- groups ---------- */

export type Group = { id: number; name: string; principal: string; managed: boolean; created_at: string; members: { id: number; email: string }[] };

export function Groups() {
  const { confirm, prompt, toast } = useUI();
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  const load = useCallback(() => apiJson<Group[]>("/api/groups").then(setGroups), []);
  useEffect(() => {
    load();
  }, [load]);

  async function run(fn: () => Promise<unknown>, ok: string) {
    try {
      await fn();
      toast(ok);
      load();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }
  async function create(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    try {
      await apiJson("/api/groups", { method: "POST", body: JSON.stringify({ name }) });
      toast(`Group ${name} created.`);
      setName("");
      load();
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  async function addMember(g: Group) {
    const email = await prompt({ title: `Add someone to ${g.name}`, action: "Add", input: { label: "Email address", value: "" } });
    if (email) run(() => apiJson(`/api/groups/${g.id}/members`, { method: "POST", body: JSON.stringify({ email }) }), `${email} added to ${g.name}.`);
  }
  async function removeMember(g: Group, m: { id: number; email: string }) {
    if (await confirm({ title: `Remove ${m.email} from ${g.name}?`, action: "Remove", danger: true, body: "They lose access to anything shared only with this group." }))
      run(() => apiJson(`/api/groups/${g.id}/members/${m.id}`, { method: "DELETE" }), `${m.email} removed from ${g.name}.`);
  }
  async function remove(g: Group) {
    if (await confirm({ title: `Delete the ${g.name} group?`, action: "Delete group", danger: true, body: "Anything shared only with this group becomes hidden from everyone until you change its access." }))
      run(() => apiJson(`/api/groups/${g.id}`, { method: "DELETE" }), `${g.name} deleted.`);
  }

  const cols: Column<Group>[] = [
    { key: "name", label: "Group", sort: (g) => g.name.toLowerCase(), render: (g) => <span className="font-medium">{g.name}</span> },
    { key: "source", label: "Managed by", sort: (g) => String(g.managed), render: (g) => <span className="text-text2">{g.managed ? "Identity provider" : "This app"}</span> },
    { key: "members", label: "Members", sort: (g) => g.members.length, render: (g) => (
      <div className="flex flex-wrap items-center gap-1.5">
        {g.members.map((m) => (
          <span key={m.id} className="inline-flex items-center gap-1 rounded bg-hover px-1.5 py-0.5 text-xs">
            {m.email}
            <button aria-label={`Remove ${m.email}`} onClick={() => removeMember(g, m)} className="text-muted hover:text-foreground"><X className="h-3 w-3" /></button>
          </span>
        ))}
        <Button size="sm" variant="ghost" icon={<UserPlus className="h-3.5 w-3.5" />} onClick={() => addMember(g)}>Add</Button>
      </div>
    ) },
    { key: "actions", label: "", className: "w-1", render: (g) => (
      <Button size="sm" variant="ghost" icon={<Trash2 className="h-3.5 w-3.5" />} onClick={() => remove(g)} aria-label={`Delete ${g.name}`}>Delete</Button>
    ) },
  ];
  return (
    <div className="space-y-6">
      <Panel title="Create a group" description="Share connectors with a group instead of listing people one by one. Groups from your identity provider (SCIM) appear here automatically.">
        <form onSubmit={create} className="flex flex-wrap items-end gap-3">
          <div className="min-w-64 flex-1"><Field label="Group name" error={err || undefined}><input value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} placeholder="Finance" className={inputCls} /></Field></div>
          <Button variant="primary" type="submit" icon={<Plus className="h-4 w-4" />}>Create group</Button>
        </form>
      </Panel>
      <Panel title={`Groups (${groups?.length ?? 0})`} flush>
        <SortableTable columns={cols} rows={groups ?? []} rowKey={(g) => g.id} initialSort={{ key: "name", dir: 1 }}
          empty={groups && <EmptyState title="No groups yet" body="Create one above, or connect your identity provider with SCIM." />} />
      </Panel>
    </div>
  );
}
