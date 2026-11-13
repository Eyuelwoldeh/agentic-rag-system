"use client";

import { useEffect, useState } from "react";
import { Button, Field, inputCls, Status } from "@/components/ui";
import { apiJson } from "@/lib/api";

export default function Login() {
  const [setup, setSetup] = useState<boolean | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    apiJson<{ needs_setup: boolean }>("/api/auth/status")
      .then((s) => setSetup(s.needs_setup))
      .catch(() => setError("Can't reach the server. Check that the backend is running."));
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await apiJson(setup ? "/api/auth/setup" : "/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
      location.href = "/";
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-dvh place-items-center bg-background px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2">
          <span className="grid h-7 w-7 place-items-center rounded bg-accent text-xs font-semibold text-white" aria-hidden>AR</span>
          <span className="text-base font-semibold">Agentic RAG</span>
        </div>
        <form onSubmit={submit} className="space-y-5 rounded border border-line bg-panel p-6">
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
        </form>
        {!setup && <p className="mt-4 text-xs text-muted">Forgot your password? Ask an administrator to reset it.</p>}
      </div>
    </main>
  );
}
