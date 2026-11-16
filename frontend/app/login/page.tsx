"use client";

import { KeyRound } from "lucide-react";
import { useEffect, useState } from "react";
import { Button, Field, inputCls, Status } from "@/components/ui";
import { API, apiJson } from "@/lib/api";

export default function Login() {
  const [setup, setSetup] = useState<boolean | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [sso, setSso] = useState(false);

  useEffect(() => {
    const e = new URLSearchParams(location.search).get("error"); // set by the single sign-on callback
    if (e) setError(e);
    apiJson<{ needs_setup: boolean }>("/api/auth/status")
      .then((s) => setSetup(s.needs_setup))
      .catch(() => setError("Can't reach the server. Check that the backend is running."));
    apiJson<{ enabled: boolean }>("/api/sso/status").then((s) => setSso(s.enabled)).catch(() => {});
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await apiJson(setup ? "/api/auth/setup" : "/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
      location.href = "/home";
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-dvh place-items-center bg-background px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2">
          <svg viewBox="0 0 24 24" className="h-7 w-7" aria-hidden>
            <rect width="24" height="24" rx="5" fill="var(--accent)" />
            <rect x="5" y="6" width="8" height="2.4" rx="1.2" fill="#fff" opacity="0.65" />
            <rect x="5" y="10.8" width="11" height="2.4" rx="1.2" fill="#fff" opacity="0.8" />
            <rect x="5" y="15.6" width="14" height="2.4" rx="1.2" fill="#fff" />
          </svg>
          <span className="text-base font-semibold">Agentic RAG</span>
        </div>
        <form onSubmit={submit} className="space-y-5 rounded-md border border-line bg-panel p-6 shadow-raised">
          <div>
            <h1 className="text-xl leading-7 font-semibold">{setup ? "Create the administrator account" : "Sign in"}</h1>
            <p className="mt-1 text-sm text-text2">{setup ? "You can add your team after setup." : "Use your work email address."}</p>
          </div>
          <Field label="Email address">
            <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} className={inputCls} />
          </Field>
          <Field label="Password" helper={setup ? "At least 10 characters." : undefined}>
            <input type="password" autoComplete={setup ? "new-password" : "current-password"} required minLength={setup ? 10 : undefined}
              value={password} onChange={(e) => setPassword(e.target.value)} className={inputCls} />
          </Field>
          {error && <div role="alert" className="flex items-start gap-2 text-sm"><Status kind="error">Error</Status><span>{error}</span></div>}
          <Button variant="primary" type="submit" loading={busy} disabled={setup === null} className="w-full">{setup ? "Create account" : "Sign in"}</Button>
          {sso && !setup && (
            <>
              <div className="flex items-center gap-3 text-xs text-muted"><span className="h-px flex-1 bg-line" />or<span className="h-px flex-1 bg-line" /></div>
              <Button type="button" icon={<KeyRound className="h-4 w-4" />} className="w-full" onClick={() => { location.href = `${API}/api/sso/start`; }}>Sign in with SSO</Button>
            </>
          )}
        </form>
        {!setup && <p className="mt-4 text-xs text-muted">Forgot your password? Ask an administrator to reset it.</p>}
      </div>
    </main>
  );
}
