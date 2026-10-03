"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { startTransition } from "react";

export default function ErrorPage({ reset }: { reset: () => void }) {
  const router = useRouter();

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