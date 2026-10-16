/**
 * Telemetría TrackFlow — servicio de captura en el frontend.
 *
 * Responsabilidades:
 *  1. Cola local: acumula eventos en memoria (arreglo interno).
 *  2. Batch + debounce: envía la cola a `NEXT_PUBLIC_TELEMETRY_ENDPOINT`
 *     cada 10 segundos o al llegar a 20 eventos, lo que ocurra primero.
 *  3. Flush confiable: en `visibilitychange` (pestaña oculta/cerrada) usa
 *     `navigator.sendBeacon` para no perder eventos pendientes.
 *  4. Reintentos con backoff: si el envío falla, reintenta hasta 3 veces
 *     con espera exponencial antes de descartar el lote.
 *
 * API pública: `track(eventType, properties)`. Todo el tracking del
 * backoffice pasa por esta función — nunca por `fetch`/`axios` directos.
 * El servicio completa el envelope (eventId, sessionId, userId, timestamp,
 * schemaVersion, requestId); los callers solo aportan `event_type` y
 * `properties`.
 *
 * Se auto-instancia: importar `lib/telemetry` e inicializa la cola, los
 * timers de flush y el listener de `visibilitychange`.
 */

import { TELEMETRY_SCHEMA_VERSION } from "../types/telemetry.ts";

const SESSION_KEY = "trackflow_telemetry_session_id";
const TOKEN_KEY = "trackflow_access_token";
const MAX_BATCH_SIZE = 20;
const FLUSH_INTERVAL_MS = 10_000;
const BASE_BACKOFF_MS = 1_000;
const MAX_RETRIES = 3;

/** URL del endpoint de ingesta (envelope stub de Fase 1 en services/api). */
// Lectura perezosa: NEXT_PUBLIC_* se inlinea igualmente por Next.js al
// compilar, y permite que los tests inyecten el endpoint tras el import.
function telemetryEndpoint(): string {
  return (process.env.NEXT_PUBLIC_TELEMETRY_ENDPOINT ?? "").replace(/\/$/, "");
}

/** Serial helper para generar IDs correlacionables entre eventos del lote. */
let requestCounter = 0;

interface QueuedEvent {
  eventId: string;
  timestamp: string;
  sessionId: string;
  userId: string;
  event_type: string;
  schemaVersion: string;
  properties: Record<string, unknown>;
}

let queue: QueuedEvent[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;

/** Obtiene el userId del claim `sub` del JWT (emisor: services/user-api). */
function readUserId(): string {
  return readUserClaims().userId;
}

/**
 * Decodifica los claims `sub` y `role` del JWT almacenado en localStorage.
 * El emisor es services/user-api (security.py): `sub` (id UUID) y `role`
 * (admin | manager | user). Exposed para que la capa de instrumentación
 * (`lib/instrumentation.ts`) complete el `user_role` de `page_navigated`
 * y `login_succeeded` sin duplicar la lógica de decode.
 */
export function readUserClaims(): { userId: string; userRole: "admin" | "manager" | "user" } {
  if (typeof window === "undefined") return { userId: "anonymous", userRole: "user" };
  const token = window.localStorage.getItem(TOKEN_KEY);
  if (!token) return { userId: "anonymous", userRole: "user" };
  try {
    const payload = token.split(".")[1];
    if (!payload) return { userId: "anonymous", userRole: "user" };
    const decoded = JSON.parse(
      typeof atob === "function"
        ? atob(payload.replace(/-/g, "+").replace(/_/g, "/"))
        : Buffer.from(payload, "base64").toString("utf-8"),
    );
    const userId = typeof decoded?.sub === "string" && decoded.sub ? decoded.sub : "anonymous";
    const role = decoded?.role === "admin" || decoded?.role === "manager" ? decoded.role : "user";
    return { userId, userRole: role };
  } catch {
    return { userId: "anonymous", userRole: "user" };
  }
}

/** sessionId generado una vez por pestaña y persistido en Session Storage. */
function getOrCreateSessionId(): string {
  if (typeof window === "undefined") return "anonymous";
  const existing = window.sessionStorage.getItem(SESSION_KEY);
  if (existing) return existing;
  const sessionId = crypto.randomUUID();
  window.sessionStorage.setItem(SESSION_KEY, sessionId);
  return sessionId;
}

/** UUID v4 para `eventId`. Usa crypto.randomUUID con fallback polifill. */
function generateEventId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
    const random = (Math.random() * 16) | 0;
    const value = char === "x" ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
}

/**
 * Función pública de tracking. Convierte `eventType` en `event_type`,
 * completa todos los campos del envelope y encola el evento.
 */
export function track(
  eventType: string,
  properties: Record<string, unknown> = {},
): void {
  queue.push({
    eventId: generateEventId(),
    timestamp: new Date().toISOString(),
    sessionId: getOrCreateSessionId(),
    userId: readUserId(),
    event_type: eventType,
    schemaVersion: TELEMETRY_SCHEMA_VERSION,
    properties,
  });

  if (queue.length >= MAX_BATCH_SIZE) {
    flushQueue();
    return;
  }
  scheduleFlush();
}

/** Agenda el envío automático del lote si no hay uno pendiente. */
function scheduleFlush(): void {
  if (flushTimer !== null || queue.length === 0) return;
  flushTimer = setTimeout(() => {
    flushTimer = null;
    flushQueue();
  }, FLUSH_INTERVAL_MS);
}

/** Envía el lote actual usando fetch (POST JSON) con reintentos backoff. */
export async function flushQueue(): Promise<void> {
  if (flushTimer !== null) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  if (queue.length === 0) return;

  const batch = queue;
  queue = [];

  if (!telemetryEndpoint()) {
    // Sin endpoint configurado (entorno de desarrollo sin .env), se descarta
    // el lote literalmente: no hay destino. Evita ruido y pérdida silenciosa.
    return;
  }

  const requestId = `req_${Date.now().toString(36)}_${(requestCounter++).toString(36)}`;
  const events = batch.map((event) => ({ ...event, requestId }));

  await sendWithRetry(events);
}

/**
 * Envía un lote con hasta `MAX_RETRIES` reintentos y espera exponencial
 * (base * 2^n). Si todos fallan, el lote se descarta (política del plan).
 */
async function sendWithRetry(events: QueuedEvent[]): Promise<void> {
  let attempt = 0;
  while (attempt <= MAX_RETRIES) {
    try {
      const response = await fetch(telemetryEndpoint(), {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ events }),
        cache: "no-store",
        keepalive: true,
      });
      if (response.ok) return;
      if (response.status >= 400 && response.status < 500) {
        // Errores de cliente no se corrigen reintentando: descartar.
        return;
      }
    } catch {
      // Error de red: reintentar.
    }

    if (attempt >= MAX_RETRIES) return; // lote descartado.
    await new Promise((resolve) => setTimeout(resolve, BASE_BACKOFF_MS * 2 ** attempt));
    attempt += 1;
  }
}

/**
 * Flush de cierre: usa `navigator.sendBeacon` para garantizar el envío del
 * lote pendiente cuando la pestaña se oculta o cierra. Beacon no soporta
 * reintentos, así que se usa una sola vez con el lote completo.
 */
function sendBeaconFlush(): void {
  if (queue.length === 0) return;
  const endpoint = telemetryEndpoint();
  if (!endpoint) return;

  const batch = queue;
  queue = [];

  const requestId = `req_${Date.now().toString(36)}_${(requestCounter++).toString(36)}`;
  const events = batch.map((event) => ({ ...event, requestId }));

  const blob = new Blob([JSON.stringify({ events })], {
    type: "application/json",
  });
  if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
    navigator.sendBeacon(endpoint, blob);
  }
}

export function onVisibilityChange(): void {
  if (document.visibilityState === "hidden") {
    sendBeaconFlush();
  }
}

// --- Auto-inicialización (solo en cliente) --------------------------------
if (typeof window !== "undefined" && typeof document !== "undefined") {
  document.addEventListener("visibilitychange", onVisibilityChange);
  // Indica al navegador que queremos que siga viva la página para el setTimeout.
  if ("onpagehide" in window) {
    window.addEventListener("pagehide", sendBeaconFlush);
  }
}

/** Exposición para tests: acceso al estado interno (no público). */
export const __telemetryInternals = {
  getQueue: () => queue,
  reset: () => {
    queue = [];
    if (flushTimer !== null) clearTimeout(flushTimer);
    flushTimer = null;
  },
  get MAX_BATCH_SIZE() {
    return MAX_BATCH_SIZE;
  },
  get FLUSH_INTERVAL_MS() {
    return FLUSH_INTERVAL_MS;
  },
  get MAX_RETRIES() {
    return MAX_RETRIES;
  },
  get BASE_BACKOFF_MS() {
    return BASE_BACKOFF_MS;
  },
  setEndpoint: (url: string) => {
    process.env.NEXT_PUBLIC_TELEMETRY_ENDPOINT = url;
  },
  sendBeaconFlush,
  onVisibilityChange,
};