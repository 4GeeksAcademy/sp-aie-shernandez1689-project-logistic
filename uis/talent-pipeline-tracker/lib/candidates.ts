export const statusLabels = {
  received: "Recibida",
  in_progress: "En proceso",
  selected: "Seleccionada",
  discarded: "Descartada",
};

export const stageLabels = {
  pending: "Pendiente de revisión",
  review: "En revisión",
  personal_interview: "Entrevista personal",
  technical_interview: "Entrevista técnica",
  offer_presented: "Oferta presentada",
};

export type Candidate = {
  id: string;
  full_name: string;
  email: string;
  phone: string | null;
  position: string;
  linkedin_url: string | null;
  cv_url: string | null;
  status: keyof typeof statusLabels;
  stage: keyof typeof stageLabels;
  experience_years: number;
  applied_at: string;
  updated_at: string;
  notes_count: number;
};

type CandidatePage = {
  total: number;
  page: number;
  limit: number;
  data: Candidate[];
};

export type CandidateNote = {
  id: string;
  record_id: string;
  content: string;
  created_at: string;
};

async function request(path: string) {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL;
  if (!baseUrl) throw new Error("Falta la URL de la API.");
  return fetch(`${baseUrl.replace(/\/$/, "")}${path}`, {
    cache: "no-store",
    signal: AbortSignal.timeout(15000),
  });
}

async function getPage(page: number): Promise<CandidatePage> {
  const response = await request(`/records?page=${page}`);
  if (!response.ok) throw new Error("No se pudieron cargar las candidaturas.");
  return response.json();
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
  return response.json();
}

export async function getCandidateNotes(id: string): Promise<CandidateNote[]> {
  const response = await request(`/records/${encodeURIComponent(id)}/notes`);
  if (!response.ok) throw new Error("No se pudieron cargar las notas.");
  const result: { data: CandidateNote[] } = await response.json();
  return result.data;
}

export function formatDate(value: string) {
  return new Intl.DateTimeFormat("es-ES", {
    dateStyle: "medium",
    timeZone: "Europe/Madrid",
  }).format(new Date(value));
}