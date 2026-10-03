import Link from "next/link";
import { notFound } from "next/navigation";
import {
  formatDate,
  getCandidate,
  getCandidateNotes,
  stageLabels,
  statusLabels,
} from "@/lib/candidates";
import { CandidateNotes, CandidateProgress } from "./candidate-controls";

function webUrl(value: string | null) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

export default async function CandidateDetail({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const candidate = await getCandidate(id);
  if (!candidate) notFound();
  const notes = await getCandidateNotes(id);
  const linkedinUrl = webUrl(candidate.linkedin_url);
  const cvUrl = webUrl(candidate.cv_url);

  return (
    <main className="workspace">
      <Link className="back-link" href="/">Volver a candidaturas</Link>
      <header className="page-heading detail-heading">
        <div>
          <p className="eyebrow">Detalle de candidatura</p>
          <h1>{candidate.full_name}</h1>
          <p className="subtitle">{candidate.position}</p>
        </div>
        <span className={`status status-${candidate.status}`}>
          {statusLabels[candidate.status] ?? "Sin estado"}
        </span>
      </header>
      <div className="detail-grid">
        <section aria-labelledby="candidate-data">
          <h2 id="candidate-data">Datos de la candidatura</h2>
          <CandidateProgress candidate={candidate} />
          <dl className="candidate-data">
            <div><dt>Nombre completo</dt><dd>{candidate.full_name}</dd></div>
            <div><dt>Puesto</dt><dd>{candidate.position}</dd></div>
            <div><dt>Email</dt><dd><a href={`mailto:${candidate.email}`}>{candidate.email}</a></dd></div>
            <div><dt>Teléfono</dt><dd>{candidate.phone ? <a href={`tel:${candidate.phone}`}>{candidate.phone}</a> : "No indicado"}</dd></div>
            <div><dt>Experiencia</dt><dd>{candidate.experience_years ?? "No indicada"}{candidate.experience_years != null ? " años" : ""}</dd></div>
            <div><dt>Estado</dt><dd>{statusLabels[candidate.status] ?? "Sin estado"}</dd></div>
            <div><dt>Etapa</dt><dd>{stageLabels[candidate.stage] ?? "Sin etapa"}</dd></div>
            <div><dt>Fecha de candidatura</dt><dd><time dateTime={candidate.applied_at}>{formatDate(candidate.applied_at)}</time></dd></div>
            <div><dt>Última actualización</dt><dd><time dateTime={candidate.updated_at}>{formatDate(candidate.updated_at)}</time></dd></div>
            <div><dt>LinkedIn</dt><dd>{linkedinUrl ? <a href={linkedinUrl} target="_blank" rel="noopener noreferrer">Ver perfil de LinkedIn</a> : "No indicado"}</dd></div>
            <div><dt>Currículum</dt><dd>{cvUrl ? <a href={cvUrl} target="_blank" rel="noopener noreferrer">Abrir currículum</a> : "No disponible"}</dd></div>
            <div><dt>Identificador</dt><dd className="record-id">{candidate.id}</dd></div>
            <div><dt>Notas registradas</dt><dd>{candidate.notes_count}</dd></div>
          </dl>
        </section>
        <CandidateNotes id={candidate.id} notes={notes} />
      </div>
    </main>
  );
}