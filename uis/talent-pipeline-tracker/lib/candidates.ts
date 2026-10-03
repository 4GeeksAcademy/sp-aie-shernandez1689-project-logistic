import type {
  Candidate,
  CandidateInput,
  CandidateNote,
  CandidateNotesResponse,
  CandidatePage,
  CandidateStage,
  CandidateStatus,
} from "@/types/candidates";

export type { Candidate, CandidateInput, CandidateNote } from "@/types/candidates";

export const statusLabels: Record<CandidateStatus, string> = {
  received: "Recibida",
  in_progress: "En proceso",
  selected: "Seleccionada",
  discarded: "Descartada",
};

export const stageLabels: Record<CandidateStage, string> = {
  pending: "Pendiente de revisión",
  review: "En revisión",
  personal_interview: "Entrevista personal",
  technical_interview: "Entrevista técnica",
  offer_presented: "Oferta presentada",
};

export async function createCandidate(input: CandidateInput): Promise<Candidate> {
  const response = await request("/records", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) throw new Error("No se pudo registrar la candidatura. Vuelve a intentarlo.");
  return await response.json();
}

export async function updateCandidate(id: string, input: CandidateInput): Promise<void> {
  const response = await request(`/records/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) throw new Error("No se pudieron guardar los datos. Vuelve a intentarlo.");
}

async function request(path: string, options: RequestInit = {}) {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL;
  if (!baseUrl) throw new Error("Falta la URL de la API.");
  return await fetch(`${baseUrl.replace(/\/$/, "")}${path}`, {
    ...options,
    cache: "no-store",
    signal: AbortSignal.timeout(15000),
  });
}

export async function updateCandidateProgress(
  id: string,
  change: { status: Candidate["status"] } | { stage: Candidate["stage"] },
): Promise<void> {
  const response = await request(`/records/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(change),
  });
  if (!response.ok) throw new Error("No se pudo actualizar la candidatura. Vuelve a intentarlo.");
}

export async function addCandidateNote(id: string, content: string): Promise<void> {
  const trimmedContent = content.trim();
  if (!trimmedContent) throw new Error("Escribe una nota antes de guardarla.");
  const response = await request(`/records/${encodeURIComponent(id)}/notes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: trimmedContent }),
  });
  if (!response.ok) throw new Error("No se pudo añadir la nota. Vuelve a intentarlo.");
}

export async function deleteCandidateNote(id: string, noteId: string): Promise<void> {
  const response = await request(
    `/records/${encodeURIComponent(id)}/notes/${encodeURIComponent(noteId)}`,
    { method: "DELETE" },
  );
  if (!response.ok) throw new Error("No se pudo eliminar la nota. Vuelve a intentarlo.");
}

async function getPage(page: number): Promise<CandidatePage> {
  const response = await request(`/records?page=${page}`);
  if (!response.ok) throw new Error("No se pudieron cargar las candidaturas.");
  return await response.json();
}

export async function getCandidates(): Promise<Candidate[]> {
  const firstPage = await getPage(1);
  const pageCount = Math.ceil(firstPage.total / firstPage.limit);
  const remainingPages = await Promise.all(
    Array.from({ length: Math.max(0, pageCount - 1) }, (_, index) =>
      getPage(index + 2),
    ),
  );
  return [firstPage, ...remainingPages].flatMap((page) => page.data);
}

export async function getCandidate(id: string): Promise<Candidate | null> {
  const response = await request(`/records/${encodeURIComponent(id)}`);
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("No se pudo cargar la candidatura.");
  return await response.json();
}

export async function getCandidateNotes(id: string): Promise<CandidateNote[]> {
  const response = await request(`/records/${encodeURIComponent(id)}/notes`);
  if (!response.ok) throw new Error("No se pudieron cargar las notas.");
  const result: CandidateNotesResponse = await response.json();
  return result.data;
}

export function formatDate(value: string) {
  return new Intl.DateTimeFormat("es-ES", {
    dateStyle: "medium",
    timeZone: "Europe/Madrid",
  }).format(new Date(value));
}