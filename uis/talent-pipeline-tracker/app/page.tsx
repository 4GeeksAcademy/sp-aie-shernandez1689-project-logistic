import { Suspense } from "react";
import Link from "next/link";
import { getCandidates } from "@/lib/candidates";
import CandidateList from "@/components/candidate-list";

export default async function Home() {
  const candidates = await getCandidates();

  return (
    <main className="workspace">
      <header className="page-heading">
        <div>
          <p className="eyebrow">Selección de personal</p>
          <h1>Candidaturas</h1>
        </div>
        <Link className="create-link" href="/candidates/new">Registrar candidatura</Link>
      </header>
      <Suspense fallback={<p className="empty-state" role="status">Cargando candidaturas…</p>}>
        <CandidateList candidates={candidates} />
      </Suspense>
    </main>
  );
}
