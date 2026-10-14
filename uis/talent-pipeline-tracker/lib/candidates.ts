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

async function readJson<T>(response: Response, message: string): Promise<T> {
  try {
    return (await response.json()) as T;
  } catch {
    throw new Error(message);
  }
}

function hasStringId(value: unknown): value is { id: string } {
  return typeof value === "object" && value !== null && "id" in value && typeof value.id === "string";
}

export async function createCandidate(input: CandidateInput): Promise<Candidate> {
  const response = await request("/records", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) throw new Error("No se pudo registrar la candidatura. Vuelve a intentarlo.");
  const result = await readJson<unknown>(response, "El servicio devolvió una respuesta inesperada. Vuelve a intentarlo.");
  if (!hasStringId(result)) throw new Error("El servicio devolvió una respuesta inesperada. Vuelve a intentarlo.");
  return result as Candidate;
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
  if (!baseUrl) throw new Error("El servicio no está configurado. Contacta con soporte.");
  try {
    return await fetch(`${baseUrl.replace(/\/$/, "")}${path}`, {
      ...options,
      cache: "no-store",
      signal: AbortSignal.timeout(15000),
    });
  } catch (error) {
    if (error instanceof Error && error.name === "TimeoutError") {
      throw new Error("La solicitud tardó demasiado. Vuelve a intentarlo.");
    }
    throw new Error("No se pudo conectar con el servicio. Revisa tu conexión e inténtalo de nuevo.");
  }
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
  const result = await readJson<Partial<CandidatePage> | null>(response, "No se pudieron cargar las candidaturas. Vuelve a intentarlo.");
  if (
    !result ||
    !Number.isSafeInteger(result.total) ||
    result.total! < 0 ||
    !Number.isSafeInteger(result.limit) ||
    result.limit! <= 0 ||
    !Array.isArray(result.data) ||
    !result.data.every(hasStringId)
  ) {
    throw new Error("El servicio devolvió datos de candidaturas no válidos. Vuelve a intentarlo.");
  }
  return {
    total: result.total!,
    page: Number.isSafeInteger(result.page) ? result.page! : page,
    limit: result.limit!,
    data: result.data,
  };
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
  const result = await readJson<unknown>(response, "No se pudo cargar la candidatura. Vuelve a intentarlo.");
  if (!hasStringId(result)) throw new Error("El servicio devolvió datos de candidatura no válidos. Vuelve a intentarlo.");
  return result as Candidate;
}

export async function getCandidateNotes(id: string): Promise<CandidateNote[]> {
  const response = await request(`/records/${encodeURIComponent(id)}/notes`);
  if (!response.ok) throw new Error("No se pudieron cargar las notas.");
  const result: CandidateNotesResponse | null = await readJson(response, "No se pudieron cargar las notas. Vuelve a intentarlo.");
  if (!Array.isArray(result?.data) || !result.data.every(hasStringId)) {
    throw new Error("El servicio devolvió notas no válidas. Vuelve a intentarlo.");
  }
  return result.data;
}

export function formatDate(value: string | null | undefined) {
  if (!value) return "Fecha no disponible";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Fecha no disponible";
  return new Intl.DateTimeFormat("es-ES", {
    dateStyle: "medium",
    timeZone: "Europe/Madrid",
  }).format(date);
}