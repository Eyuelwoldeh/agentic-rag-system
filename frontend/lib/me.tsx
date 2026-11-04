"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { api, type Me } from "./api";

const MeContext = createContext<Me | null>(null);
export const useMe = () => useContext(MeContext);

/** Loads the signed-in user once; api() redirects to /login if there isn't one. */
export function MeProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const path = usePathname();
  const onLogin = path.startsWith("/login");

  useEffect(() => {
    if (onLogin) return;
    api("/api/auth/me").then(async (r) => r.ok && setMe(await r.json()));
  }, [onLogin]);

  if (onLogin) return <>{children}</>;
  if (!me) return <p className="p-6 text-sm text-muted">Loading…</p>;
  return <MeContext.Provider value={me}>{children}</MeContext.Provider>;
}
