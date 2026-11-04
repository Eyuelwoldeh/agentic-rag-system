"use client";

import { useEffect, useState } from "react";
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
      .catch(() => setError("Backend not reachable"));
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
    <main className="grid min-h-dvh place-items-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-xl border border-line bg-panel p-6">
        <div>
          <h1 className="text-lg font-semibold">Agentic RAG</h1>
          <p className="mt-1 text-sm text-muted">
            {setup ? "Create the admin account. You can invite your team afterwards." : "Sign in to continue."}
          </p>
        </div>
        <label className="block text-sm">
          <span className="text-xs font-medium">Email</span>
          <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-md border border-line bg-background px-3 py-2 outline-none focus:border-accent" />
        </label>
        <label className="block text-sm">
          <span className="text-xs font-medium">Password</span>
          <input type="password" autoComplete={setup ? "new-password" : "current-password"} required minLength={setup ? 10 : undefined}
            value={password} onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-md border border-line bg-background px-3 py-2 outline-none focus:border-accent" />
          {setup && <span className="mt-1 block text-xs text-muted">At least 10 characters.</span>}
        </label>
        {error && <p className="text-sm text-bad" role="alert">{error}</p>}
        <button disabled={busy || setup === null} className="w-full rounded-md bg-accent py-2 font-medium text-background disabled:opacity-50">
          {setup ? "Create admin account" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
