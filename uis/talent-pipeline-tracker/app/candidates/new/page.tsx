import Link from "next/link";
import CandidateForm from "@/components/candidate-form";

export default function NewCandidatePage() {
  return (
    <main className="workspace">
      <Link className="back-link" href="/">Volver a candidaturas</Link>
      <header className="page-heading">
        <div>
          <p className="eyebrow">Gestión de candidaturas</p>
          <h1>Registrar candidatura</h1>
        </div>
      </header>
      <CandidateForm />
    </main>
  );
}