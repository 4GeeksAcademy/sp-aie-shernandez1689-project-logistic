"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { startTransition, useEffect } from "react";
import { trackFrontendError } from "@/lib/instrumentation";

export default function ErrorPage({ reset }: { reset: () => void }) {
  const router = useRouter();

  // Error boundary de Next.js: un render fallido en el árbol de páginas
  // dispara el piso técnico transversal (decisión A — canal aprobado).
  useEffect(() => {
    trackFrontendError("error_boundary");
  }, []);

  return (
    <main className="workspace">
      <h1>No se pudieron cargar los datos</h1>
      <p className="subtitle" role="alert">No hemos podido conectar con la API. Vuelve a intentarlo.</p>
      <div className="error-actions">
        <button type="button" onClick={() => startTransition(() => {
          router.refresh();
          reset();
        })}>Reintentar</button>
        <Link href="/">Volver a candidaturas</Link>
      </div>
    </main>
  );
}