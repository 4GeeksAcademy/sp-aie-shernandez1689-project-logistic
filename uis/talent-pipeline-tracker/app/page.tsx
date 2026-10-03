import Link from "next/link";
import { getCandidates, stageLabels, statusLabels } from "@/lib/candidates";

export default async function Home() {
  const candidates = await getCandidates();

  return (
    <main className="workspace">
      <header className="page-heading">
        <div>
          <p className="eyebrow">Selección de personal</p>
          <h1>Candidaturas</h1>
        </div>
        <span className="record-count">{candidates.length} candidaturas</span>
      </header>
      {candidates.length === 0 ? (
        <p className="empty-state">Todavía no hay candidaturas.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <caption className="sr-only">Todas las candidaturas de TrackFlow</caption>
            <thead>
              <tr>
                <th scope="col">Candidato</th>
                <th scope="col">Puesto</th>
                <th scope="col">Estado</th>
                <th scope="col">Etapa</th>
                <th scope="col"><span className="sr-only">Detalle</span></th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((candidate) => (
                <tr key={candidate.id}>
                  <td>
                    <Link className="candidate-name" href={`/candidates/${candidate.id}`}>
                      {candidate.full_name}
                    </Link>
                    <span className="secondary-text">{candidate.email}</span>
                  </td>
                  <td>{candidate.position}</td>
                  <td><span className={`status status-${candidate.status}`}>{statusLabels[candidate.status] ?? "Sin estado"}</span></td>
                  <td>{stageLabels[candidate.stage] ?? "Sin etapa"}</td>
                  <td>
                    <Link className="detail-link" href={`/candidates/${candidate.id}`} aria-label={`Ver candidatura de ${candidate.full_name}`}>
                      Ver detalle
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
