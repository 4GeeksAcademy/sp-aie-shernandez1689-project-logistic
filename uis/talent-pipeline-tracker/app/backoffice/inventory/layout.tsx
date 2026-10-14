"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { AuthApiError, clearAccessToken, requireAuthenticatedUser } from "@/lib/auth";

export default function InventoryLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSession] = useState<{ pathname: string; authenticated: boolean; error?: string } | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    requireAuthenticatedUser()
      .then(() => { if (active) setSession({ pathname, authenticated: true }); })
      .catch((reason: unknown) => {
        if (!active) return;
        if (reason instanceof AuthApiError && reason.status === 401) {
          clearAccessToken();
          router.replace("/login");
          return;
        }
        setSession({ pathname, authenticated: false, error: reason instanceof Error ? reason.message : "No se pudo validar la sesión." });
      });
    return () => { active = false; };
  }, [pathname, router, attempt]);

  if (session?.pathname === pathname && session.authenticated) return children;

  return (
    <main className="workspace">
      {session?.pathname === pathname && session.error ? (
        <div role="alert">
          <p className="action-error">{session.error}</p>
          <button type="button" onClick={() => { setSession(null); setAttempt((value) => value + 1); }}>Reintentar</button>
        </div>
      ) : <p role="status">Comprobando sesión...</p>}
    </main>
  );
}