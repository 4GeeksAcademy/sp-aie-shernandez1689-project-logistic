/**
 * Instrumentación TrackFlow — capa de emisión de eventos (Fase 3).
 *
 * Este módulo es la ÚNICA fuente de builders de eventos del backoffice.
 * Todo `track()` de negocio/técnico pasa por las funciones exportadas aquí,
 * y solo se emiten `event_type` + claves de `properties` que existen en
 * `docs/telemetry/event-schemas.json` (allowlist por evento, Fase 1).
 *
 * Reglas de la Fase 3:
 *   - NUNCA se añaden propiedades "por si acaso": `additionalProperties:
 *     false` en cada schema.
 *   - Los nombres de página de `page_navigated` son lógicos, sin URLs ni
 *     query params (schema §page_navigated).
 *   - `api_response_error_recorded.error_detail` solo contiene mensajes
 *     predefinidos, nunca input de usuario ni stack traces.
 *   - `login_failed.email_hash` es SHA-256 del email (nunca en claro).
 *   - La IP solo se reduce a país (ISO 3166-1 alpha-2), nunca IP completa.
 *
 * Piso técnico transversal (decisión A aprobada por el usuario):
 *   Los eventos dedicados `frontend_uncaught_error`, `page_load_time` y
 *   `api_response_time_high` están pospuestos en el catálogo de Fase 1
 *   (telemetry-plan.md §8.1) y NO tienen schema aprobado. Para no romper el
 *   contrato, estas señales se mapean al event_type aprobado
 *   `api_response_error_recorded` con endpoints sintéticos bien separados:
 *     - "frontend:uncaught_error"        (errores no capturados)
 *     - "frontend:slow_page_load"        (tiempo de carga de página alto)
 *     - "frontend:slow_api_response"     (latencia alta de una llamada API)
 *   con `error_category: "client_error"` y `error_detail` predefinido.
 *   Este mapeo queda documentado en memory-bank/progress.md y estará
 *   pendiente de migrar cuando se aprueben los schemas dedicados.
 */

import { track } from "./telemetry.ts";
import { readUserClaims } from "./telemetry.ts";
import type { Category, ExitType, SKU } from "../types/inventory.ts";

/** Semáforo de stock "bajo" que ya usa el frontend (products/page.tsx: 1-10). */
export const STOCK_LOW_THRESHOLD = 10;
/** Umbral (ms) a partir del cual una página se considera lenta. */
export const SLOW_PAGE_LOAD_MS = 3_000;
/** Umbral (ms) a partir del cual una respuesta de API se considera lenta. */
export const SLOW_API_RESPONSE_MS = 1_500;

// --- Constantes de almacenamiento local ------------------------------------

const LOGIN_TIME_KEY = "trackflow_login_timestamp_ms";
const FAILED_ATTEMPTS_KEY = "trackflow_failed_login_attempts";
const FAILED_ATTEMPTS_WINDOW_MS = 5 * 60 * 1000;

// --- Helpers de dominio ------------------------------------------------------

/** Traduce el código de almacén del frontend al valor del schema aprobado. */
const WAREHOUSE_TO_SCHEMA: Record<string, string> = {
  LA: "los_angeles",
  ZGZ: "zaragoza",
};

export function mapWarehouse(warehouse: string): string {
  return WAREHOUSE_TO_SCHEMA[warehouse] ?? warehouse;
}

/**
 * Nombre lógico de página para `page_navigated`. Devuelve `null` para
 * rutas no instrumentadas (la página raíz de candidaturas es pública y
 * no forma parte del backoffice).
 */
const PAGE_NAMES: Record<string, string> = {
  "/login": "login",
  "/backoffice/inventory/products": "inventory_products",
  "/backoffice/inventory/orders": "inventory_orders",
  "/backoffice/inventory/orders/inbound": "inventory_orders_inbound",
  "/backoffice/inventory/orders/outbound": "inventory_orders_outbound",
};

export function pageNameForPathname(pathname: string): string | null {
  return PAGE_NAMES[pathname] ?? null;
}

/**
 * País (ISO 3166-1 alpha-2) derivado de la timezone del navegador.
 * Aproximación cliente: no se expone la IP completa; el schema solo
 * exige un código de país de 2 letras.
 */
const TIMEZONE_COUNTRY: Record<string, string> = {
  "Europe/Madrid": "ES",
  "Europe/Paris": "FR",
  "Europe/Berlin": "DE",
  "Europe/London": "GB",
  "Europe/Milan": "IT",
  "Europe/Lisbon": "PT",
  "America/Los_Angeles": "US",
  "America/New_York": "US",
  "America/Chicago": "US",
  "America/Denver": "US",
  "America/Phoenix": "US",
  "America/Toronto": "CA",
  "America/Mexico_City": "MX",
  "America/Bogota": "CO",
  "America/Sao_Paulo": "BR",
  "America/Buenos_Aires": "AR",
};

export function getIpCountry(): string {
  if (typeof Intl === "undefined") return "US";
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const mapped = TIMEZONE_COUNTRY[timeZone];
  if (mapped) return mapped;
  const [region] = timeZone.split("/");
  // Default conservador: US para América, ES para Europa y resto.
  return region === "Europe" ? "ES" : "US";
}

/** Hash SHA-256 (hex) del email para `login_failed`. Nunca se emite en claro. */
export async function sha256Hex(input: string): Promise<string> {
  const data = new TextEncoder().encode(String(input).trim().toLowerCase());
  const subtle = typeof crypto !== "undefined" ? crypto.subtle : undefined;
  if (!subtle) {
    // Sin WebCrypto (entornos no seguros) NO se emite el evento: mejor no
    // enviar un hash falso que rompa la semántica del schema.
    throw new Error("WebCrypto unavailable; cannot hash email for login_failed");
  }
  const digest = await subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

// --- Sesión / intentos fallidos ----------------------------------------------

/** Registra el instante del login con éxito (para time_since_login_minutes). */
export function recordLoginTime(): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(LOGIN_TIME_KEY, String(Date.now()));
}

/** Minutos transcurridos desde el último login con éxito. */
export function getMinutesSinceLogin(): number {
  if (typeof window === "undefined") return 0;
  const stored = window.localStorage.getItem(LOGIN_TIME_KEY);
  if (!stored) return 0;
  const loginAt = Number(stored);
  if (!Number.isFinite(loginAt)) return 0;
  return Math.max(0, Math.floor((Date.now() - loginAt) / 60_000));
}

/** Registra un intento de login fallido y devuelve el total en ventana 5 min. */
export function recordLoginFailure(): number {
  if (typeof window === "undefined") return 0;
  const now = Date.now();
  const raw = window.sessionStorage.getItem(FAILED_ATTEMPTS_KEY);
  const stamps: number[] = raw ? (JSON.parse(raw) as number[]) : [];
  const recent = [...stamps, now].filter((ts) => now - ts < FAILED_ATTEMPTS_WINDOW_MS);
  window.sessionStorage.setItem(FAILED_ATTEMPTS_KEY, JSON.stringify(recent));
  return recent.length;
}

// --- Normalización de endpoints para api_response_error_recorded --------------

/**
 * Normaliza una ruta de API sin parámetros de ruta (schema:
 * "/api/incidents, not /api/incidents/{id}"). Sustituye segmentos numéricos
 * por `{id}`.
 */
export function normalizeEndpoint(path: string): string {
  return `/${path
    .split("/")
    .filter(Boolean)
    .map((segment) => (/^\d+$/.test(segment) ? "{id}" : segment))
    .join("/")}`;
}

// --- Throttle de api_response_error_recorded (plan §6.4) ----------------------
// Máximo 10 eventos por minuto por combinación (endpoint, status_code).
// El exceso se descarta silenciosamente.

const ERROR_THROTTLE_WINDOW_MS = 60_000;
const ERROR_THROTTLE_MAX_PER_KEY = 10;
const errorTimestamps = new Map<string, number[]>();

function shouldEmitError(endpoint: string, statusCode: number): boolean {
  const key = `${statusCode}:${endpoint}`;
  const now = Date.now();
  const recent = (errorTimestamps.get(key) ?? []).filter((ts) => now - ts < ERROR_THROTTLE_WINDOW_MS);
  if (recent.length >= ERROR_THROTTLE_MAX_PER_KEY) {
    errorTimestamps.set(key, recent);
    return false;
  }
  recent.push(now);
  errorTimestamps.set(key, recent);
  return true;
}

/** Mensaje genérico predefinido (schema: no refleja input de usuario). */
export function errorDetailFor(statusCode: number): string {
  switch (statusCode) {
    case 400:
    case 422:
      return "Validation error";
    case 401:
      return "Unauthorized request";
    case 403:
      return "Insufficient permissions";
    case 404:
      return "Resource not found";
    case 409:
      return "Conflicting request";
    default:
      return statusCode >= 500 ? "Server error" : "Client error";
  }
}

// --- Piso técnico transversal ------------------------------------------------

/**
 * Emite `api_response_error_recorded` para un error HTTP real (4xx/5xx).
 * `endpoint` es la ruta lógica normalizada (sin parámetros).
 */
export function trackApiResponseError(
  endpoint: string,
  method: "GET" | "POST" | "PATCH" | "DELETE",
  statusCode: number,
): void {
  if (statusCode < 400) return;
  if (!shouldEmitError(endpoint, statusCode)) return;
  track("api_response_error_recorded", {
    endpoint,
    method,
    status_code: statusCode,
    error_category: statusCode >= 500 ? "server_error" : "client_error",
    error_detail: errorDetailFor(statusCode),
  });
}

/**
 * Emite un error de frontend no capturado mapeado al canal aprobado
 * `api_response_error_recorded` (decisión A — ver cabecera del módulo).
 * `error_detail` es predefinido; se descarta el detail real para no
 * reflejar stack traces ni datos del usuario.
 */
export function trackFrontendError(kind: "uncaught_error" | "error_boundary" | "unhandled_rejection"): void {
  const errorDetail =
    kind === "unhandled_rejection"
      ? "Unhandled promise rejection"
      : kind === "error_boundary"
        ? "Unhandled render error (error boundary)"
        : "Uncaught frontend error";
  if (!shouldEmitError("frontend:uncaught_error", 0)) return;
  track("api_response_error_recorded", {
    endpoint: "frontend:uncaught_error",
    method: "GET",
    status_code: 0,
    error_category: "client_error",
    error_detail: errorDetail,
  });
}

/** Emite la métrica de tiempo de carga de página (mapeada a canal aprobado). */
export function trackSlowPageLoad(loadTimeMs: number): void {
  if (loadTimeMs < SLOW_PAGE_LOAD_MS) return;
  if (!shouldEmitError("frontend:slow_page_load", 0)) return;
  track("api_response_error_recorded", {
    endpoint: "frontend:slow_page_load",
    method: "GET",
    status_code: 0,
    error_category: "client_error",
    error_detail: "Slow page load",
  });
}

/** Emite la latencia alta de una llamada a API relevante (canal aprobado). */
export function trackSlowApiResponse(endpoint: string): void {
  if (!shouldEmitError("frontend:slow_api_response", 0)) return;
  track("api_response_error_recorded", {
    endpoint: "frontend:slow_api_response",
    method: "GET",
    status_code: 0,
    error_category: "client_error",
    error_detail: "Slow API response",
  });
}

// --- page_navigated (vista de página / navegación) ----------------------------

/**
 * Emite `page_navigated` con el rol del JWT. `user_role` es admin/manager/user
 * tal y como exige el schema; si no hay token (p. ej. la página de login),
 * se usa un default documentado de "user" (permiso mínimo del schema).
 */
export function trackPageNavigated(
  fromPage: string,
  toPage: string,
  navigationMethod: "nav_link" | "breadcrumb" | "direct_link" | "browser_back",
): void {
  if (typeof window === "undefined") return;
  if (fromPage === toPage) return;
  const { userRole } = readUserClaims();
  track("page_navigated", {
    from_page: fromPage,
    to_page: toPage,
    user_role: userRole,
    navigation_method: navigationMethod,
  });
}

// --- Eventos de negocio: inventario ------------------------------------------

/** Campos comunes de inventario según los schemas (4 claves exactas). */
function inventoryBase(product: SKU) {
  return {
    warehouse: mapWarehouse(product.warehouse),
    // El modelo SKU no expone client_id; `client_name` es el único
    // identificador de cliente disponible (schema pide la clave client_id).
    client_id: product.client_name,
    // El schema define product_id como el código SKU (ej. CLT-SNK-W-42).
    product_id: product.sku,
    product_category: product.category,
  };
}

/** `inbound_order_created` — tras POST /inventory/orders/inbound correcto. */
export function trackInboundOrderCreated(product: SKU, quantity: number, reference: string): void {
  track("inbound_order_created", {
    ...inventoryBase(product),
    quantity,
    reference,
  });
}

/** `outbound_order_created` — tras POST /inventory/orders/outbound correcto. */
export function trackOutboundOrderCreated(product: SKU, quantity: number, exitType: ExitType): void {
  track("outbound_order_created", {
    ...inventoryBase(product),
    quantity,
    exit_type: exitType,
  });
}

/**
 * `outbound_order_insufficient_stock` — la API rechazó por stock insuficiente.
 * `available_stock` es el stock actual del SKU al dispararse el rechazo.
 */
export function trackOutboundInsufficientStock(product: SKU, requestedQuantity: number): void {
  track("outbound_order_insufficient_stock", {
    warehouse: mapWarehouse(product.warehouse),
    product_id: product.sku,
    product_category: product.category,
    client_id: product.client_name,
    requested_quantity: requestedQuantity,
    available_stock: product.current_stock,
  });
}

/**
 * `stock_threshold_triggered` — evaluado tras cada outbound correcto.
 * El backend no expone el umbral configurado por SKU/cliente, así que se usa
 * la frontera comercial ya codificada en el frontend ("stock bajo" = ≤ 10)
 * como `threshold_value`, documentado en progress.md como convención cliente.
 * `current_stock` = stock restante tras la operación (nunca negativo).
 */
export function trackStockThresholdTriggered(
  product: SKU,
  quantity: number,
  thresholdValue: number = STOCK_LOW_THRESHOLD,
): void {
  const remaining = Math.max(0, quantity);
  if (remaining > thresholdValue) return;
  track("stock_threshold_triggered", {
    warehouse: mapWarehouse(product.warehouse),
    client_id: product.client_name,
    product_id: product.sku,
    product_category: product.category,
    quantity: remaining,
    threshold_value: thresholdValue,
    current_stock: remaining,
  });
}

// --- Eventos de negocio: autenticación ---------------------------------------

/** `login_succeeded` — tras POST /auth/login 200 (auth_method: password). */
export function trackLoginSucceeded(): void {
  const { userRole } = readUserClaims();
  const { userId } = readUserClaims();
  track("login_succeeded", {
    user_id: userId,
    user_role: userRole,
    ip_country: getIpCountry(),
    auth_method: "password",
  });
}

/**
 * `login_failed` — credenciales inválidas o usuario inactivo.
 * El email se hashea con SHA-256 y se cuenta la ventana de 5 min por sesión.
 */
export async function trackLoginFailed(
  email: string,
  failureReason: "invalid_credentials" | "inactive_user",
): Promise<void> {
  let emailHash: string;
  try {
    emailHash = await sha256Hex(email);
  } catch {
    // Sin WebCrypto no se emite el evento (nunca enviar email en claro).
    return;
  }
  track("login_failed", {
    email_hash: emailHash,
    failure_reason: failureReason,
    ip_country: getIpCountry(),
    attempt_count_last_5min: recordLoginFailure(),
  });
}

/** `session_token_expired` — un endpoint protegido devolvió 401. */
export function trackSessionTokenExpired(endpoint: string): void {
  const { userId } = readUserClaims();
  track("session_token_expired", {
    user_id: userId,
    endpoint,
    time_since_login_minutes: getMinutesSinceLogin(),
    ip_country: getIpCountry(),
  });
}

/** Referencia de tipos utilizada (evita unused imports en algunos builds). */
export type { Category };