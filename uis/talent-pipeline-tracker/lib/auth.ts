const TOKEN_KEY = "trackflow_access_token";

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function clearAccessToken() {
  window.localStorage.removeItem(TOKEN_KEY);
}

export async function login(email: string, password: string): Promise<void> {
  const baseUrl = (process.env.NEXT_PUBLIC_AUTH_API_URL ?? process.env.NEXT_PUBLIC_INVENTORY_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  let response: Response;
  try {
    response = await fetch(`${baseUrl}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ email, password }),
      signal: AbortSignal.timeout(15000),
    });
  } catch {
    throw new Error("No se pudo conectar con el servicio de autenticación. Vuelve a intentarlo.");
  }

  const body = await response.text();
  let payload;
  try {
    payload = JSON.parse(body);
  } catch {
    if (!response.ok && body.trim()) throw new Error(body);
    throw new Error("El servicio de autenticación devolvió una respuesta inesperada.");
  }
  if (!response.ok) {
    const message = payload?.detail ?? payload?.message ?? payload?.error;
    throw new Error(typeof message === "string" ? message : `No se pudo iniciar sesión (${response.status}).`);
  }
  if (typeof payload?.access_token !== "string" || !payload.access_token.trim()) {
    throw new Error("El servicio de autenticación no devolvió un token válido.");
  }
  window.localStorage.setItem(TOKEN_KEY, payload.access_token);
}