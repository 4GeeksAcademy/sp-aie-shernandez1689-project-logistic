/**
 * Tests de lib/instrumentation.ts — capa de emisión de eventos (Fase 3).
 *
 * Se ejecutan con `node --test` (Node 24), igual que telemetry.test.ts.
 * Verifican:
 *   - mapeos de dominio (warehouse, página, endpoint) -> valores del schema
 *   - builders de eventos de inventario con las claves exactas del allowlist
 *   - `stock_threshold_triggered` solo cuando el stock restante <= umbral
 *   - `login_failed` con email hasheado (SHA-256), nunca en claro
 *   - `page_navigated` con user_role derivado del JWT
 *   - `api_response_error_recorded` con categoría y throttle (10/min)
 *   - piso técnico: uncaught_error y métricas de lentitud sobre canal aprobado
 */
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import {
  mapWarehouse,
  pageNameForPathname,
  normalizeEndpoint,
  getIpCountry,
  sha256Hex,
  recordLoginFailure,
  trackInboundOrderCreated,
  trackOutboundOrderCreated,
  trackStockThresholdTriggered,
  trackOutboundInsufficientStock,
  trackLoginFailed,
  trackPageNavigated,
  trackApiResponseError,
  trackFrontendError,
  trackSlowPageLoad,
  trackSlowApiResponse,
} from "./instrumentation.ts";
import { __telemetryInternals } from "./telemetry.ts";
import type { SKU } from "../types/inventory.ts";

/** Token JWT falso con claims sub y role (emisor: services/user-api). */
function makeJwt(sub: string, role: string): string {
  const b64 = (input: unknown) => Buffer.from(JSON.stringify(input)).toString("base64url");
  return `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub, role, exp: 9999999999 })}.${"sig"}`;
}

function createWindow(token: string | null) {
  const session = new Map<string, string>();
  return {
    localStorage: {
      getItem: (key: string) => (key === "trackflow_access_token" ? token : null),
      setItem: () => undefined,
      removeItem: () => undefined,
    },
    sessionStorage: {
      getItem: (key: string) => session.get(key) ?? null,
      setItem: (key: string, value: string) => session.set(key, value),
      removeItem: (key: string) => session.delete(key),
    },
  };
}

const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
const originalCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto");

/** SKU de ejemplo (los valores completos que envía el frontend). */
const SKU_LA: SKU = {
  id: 1,
  name: "Sneaker Blanco L19",
  sku: "CLT-SNK-W-42",
  client_name: "CLT Hombre",
  category: "fashion",
  warehouse: "LA",
  current_stock: 45,
};

const SKU_ZGZ: SKU = {
  id: 2,
  name: "Cargador USB C",
  sku: "ELC-USB-C-20W",
  client_name: "Voltix",
  category: "electronics",
  warehouse: "ZGZ",
  current_stock: 3,
};

beforeEach(() => {
  __telemetryInternals.reset();
  // Endpoint configurado para que los eventos puedan encolarse sin fetch.
  __telemetryInternals.setEndpoint("https://ingest.trackflow.local/telemetry/events");

  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: createWindow(makeJwt("user-42", "manager")),
  });
  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: { visibilityState: "visible", addEventListener: () => undefined },
  });
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { sendBeacon: (() => true) as typeof navigator.sendBeacon },
  });
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: {
      randomUUID: () => "00000000-0000-4000-8000-000000000000",
      subtle: {
        digest: async () => crypto28("email@example.com"),
      },
    },
  });
});

afterEach(() => {
  __telemetryInternals.reset();
  delete process.env.NEXT_PUBLIC_TELEMETRY_ENDPOINT;

  if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
  else Reflect.deleteProperty(globalThis, "window");
  if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument);
  else Reflect.deleteProperty(globalThis, "document");
  if (originalNavigator) Object.defineProperty(globalThis, "navigator", originalNavigator);
  else Reflect.deleteProperty(globalThis, "navigator");
  if (originalCrypto) Object.defineProperty(globalThis, "crypto", originalCrypto);
  else Reflect.deleteProperty(globalThis, "crypto");
});

/** Digest SHA-256 codificado en "bytes" para el mock de crypto.subtle. */
function crypto28(_input: string): ArrayBuffer {
  // En tests reales esto no se usa: la lógica bajo prueba (sha256Hex) llama
  // a crypto.subtle.digest y transforma el ArrayBuffer a hex. Aquí mockeamos
  // 32 bytes constantes para verificarlo.
  return new Uint8Array(Array.from({ length: 32 }, (_, i) => i + 1)).buffer;
}

// --- Mapeos de dominio -------------------------------------------------------

test("mapWarehouse traduce LA/ZGZ a los valores del schema", () => {
  assert.equal(mapWarehouse("LA"), "los_angeles");
  assert.equal(mapWarehouse("ZGZ"), "zaragoza");
});

test("pageNameForPathname devuelve solo nombres lógicos y null para públicas", () => {
  assert.equal(pageNameForPathname("/login"), "login");
  assert.equal(pageNameForPathname("/backoffice/inventory/products"), "inventory_products");
  assert.equal(pageNameForPathname("/backoffice/inventory/orders/inbound"), "inventory_orders_inbound");
  assert.equal(pageNameForPathname("/backoffice/inventory/orders/outbound"), "inventory_orders_outbound");
  assert.equal(pageNameForPathname("/"), null); // página pública de candidaturas
});

test("normalizeEndpoint deja rutas sin parámetros y sustituye id por {id}", () => {
  assert.equal(normalizeEndpoint("/inventory/products"), "/inventory/products");
  assert.equal(normalizeEndpoint("/inventory/products/42"), "/inventory/products/{id}");
  assert.equal(normalizeEndpoint("/inventory/orders/inbound"), "/inventory/orders/inbound");
});

test("getIpCountry devuelve un código alpha-2 derivado de la timezone", () => {
  const country = getIpCountry();
  assert.match(country, /^[A-Z]{2}$/);
});

// --- Builders de inventario --------------------------------------------------

test("inbound_order_created: propiedades exactas del allowlist", () => {
  trackInboundOrderCreated(SKU_LA, 12, "PO-2026-001");
  const event = __telemetryInternals.getQueue()[0];
  assert.equal(event.event_type, "inbound_order_created");
  assert.deepEqual(event.properties, {
    warehouse: "los_angeles",
    client_id: "CLT Hombre",
    product_id: "CLT-SNK-W-42",
    product_category: "fashion",
    quantity: 12,
    reference: "PO-2026-001",
  });
});

test("outbound_order_created: exit_type y warehouse correctos", () => {
  trackOutboundOrderCreated(SKU_ZGZ, 2, "dispatch");
  const event = __telemetryInternals.getQueue()[0];
  assert.equal(event.event_type, "outbound_order_created");
  assert.deepEqual(event.properties, {
    warehouse: "zaragoza",
    client_id: "Voltix",
    product_id: "ELC-USB-C-20W",
    product_category: "electronics",
    quantity: 2,
    exit_type: "dispatch",
  });
});

test("outbound_order_insufficient_stock: requested vs available", () => {
  trackOutboundInsufficientStock(SKU_ZGZ, 50);
  const event = __telemetryInternals.getQueue()[0];
  assert.equal(event.event_type, "outbound_order_insufficient_stock");
  assert.deepEqual(event.properties, {
    warehouse: "zaragoza",
    product_id: "ELC-USB-C-20W",
    product_category: "electronics",
    client_id: "Voltix",
    requested_quantity: 50,
    available_stock: 3,
  });
});

test("stock_threshold_triggered: no emite si el stock restante > umbral", () => {
  trackStockThresholdTriggered(SKU_LA, 40); // 40 > 10
  assert.equal(__telemetryInternals.getQueue().length, 0);
});

test("stock_threshold_triggered: emite si el stock restante <= umbral (10)", () => {
  trackStockThresholdTriggered(SKU_ZGZ, 3);
  const event = __telemetryInternals.getQueue()[0];
  assert.equal(event.event_type, "stock_threshold_triggered");
  assert.deepEqual(event.properties, {
    warehouse: "zaragoza",
    client_id: "Voltix",
    product_id: "ELC-USB-C-20W",
    product_category: "electronics",
    quantity: 3,
    threshold_value: 10,
    current_stock: 3,
  });
});

// --- Autenticación ------------------------------------------------------------

test("login_failed: email hasheado SHA-256, nunca en claro", async () => {
  await trackLoginFailed("admin@trackflow.com", "invalid_credentials");
  const event = __telemetryInternals.getQueue()[0];
  assert.equal(event.event_type, "login_failed");

  const props = event.properties as { email_hash: string; failure_reason: string; attempt_count_last_5min: number };
  // El mock devuelve 32 bytes fijos -> 64 caracteres hex (longitud SHA-256).
  assert.match(props.email_hash, /^[0-9a-f]{64}$/);
  assert.notEqual(props.email_hash, "admin@trackflow.com");
  assert.equal(props.failure_reason, "invalid_credentials");
  assert.equal(props.ip_country.length, 2);
  assert.equal(props.attempt_count_last_5min, 1);
});

test("login_failed: ventana de 5 min cuenta intentos acumulados", async () => {
  await trackLoginFailed("a@b.co", "invalid_credentials");
  await trackLoginFailed("a@b.co", "invalid_credentials");
  const event = __telemetryInternals.getQueue()[1];
  const props = event.properties as { attempt_count_last_5min: number };
  assert.equal(props.attempt_count_last_5min, 2);
});

test("recordLoginFailure convierte a número de intentos en sesión", () => {
  assert.equal(recordLoginFailure(), 1);
  assert.equal(recordLoginFailure(), 2);
});

test("page_navigated: user_role desde el claim role del JWT (manager)", () => {
  trackPageNavigated("inventory_products", "inventory_orders_inbound", "nav_link");
  const event = __telemetryInternals.getQueue()[0];
  assert.equal(event.event_type, "page_navigated");
  assert.deepEqual(event.properties, {
    from_page: "inventory_products",
    to_page: "inventory_orders_inbound",
    user_role: "manager",
    navigation_method: "nav_link",
  });
});

test("page_navigated: misma página no emite evento (evita ruido)", () => {
  trackPageNavigated("login", "login", "nav_link");
  assert.equal(__telemetryInternals.getQueue().length, 0);
});

// --- Canal aprobado api_response_error_recorded -------------------------------

test("api_response_error_recorded: categoría y detalle predefinido", () => {
  trackApiResponseError("/inventory/products", "GET", 500);
  const event = __telemetryInternals.getQueue()[0];
  assert.equal(event.event_type, "api_response_error_recorded");
  assert.deepEqual(event.properties, {
    endpoint: "/inventory/products",
    method: "GET",
    status_code: 500,
    error_category: "server_error",
    error_detail: "Server error",
  });
});

test("api_response_error_recorded: 4xx -> client_error, detalle definido", () => {
  trackApiResponseError("/inventory/orders/outbound", "POST", 400);
  const event = __telemetryInternals.getQueue().find((e) => e.event_type === "api_response_error_recorded");
  const props = event?.properties as {
    status_code: number;
    error_category: string;
    error_detail: string;
  };
  assert.equal(props.status_code, 400);
  assert.equal(props.error_category, "client_error");
  assert.equal(props.error_detail, "Validation error");
});

test("api_response_error_recorded: throttle máximo 10/min por endpoint+status", () => {
  // Endpoint+status exclusivo de este test para no compartir el contador
  // del rate limiter global con otros tests del mismo archivo.
  for (let i = 0; i < 12; i += 1) {
    trackApiResponseError("/audit/stock", "PATCH", 401);
  }
  const errorEvents = __telemetryInternals.getQueue().filter((e) => e.event_type === "api_response_error_recorded");
  assert.equal(errorEvents.length, 10);
});

test("api_response_error_recorded: 2xx/3xx no emiten evento", () => {
  trackApiResponseError("/inventory/products", "GET", 200);
  trackApiResponseError("/inventory/products", "GET", 304);
  assert.equal(__telemetryInternals.getQueue().length, 0);
});

// --- Piso técnico transversal (decisión A) ------------------------------------

test("frontend uncaught error se mapea al canal aprobado sin stack traces", () => {
  trackFrontendError("uncaught_error");
  const event = __telemetryInternals.getQueue()[0];
  assert.equal(event.event_type, "api_response_error_recorded");
  assert.equal((event.properties as { endpoint: string }).endpoint, "frontend:uncaught_error");
  assert.equal((event.properties as { error_category: string }).error_category, "client_error");
  assert.equal((event.properties as { error_detail: string }).error_detail, "Uncaught frontend error");
});

test("slow page load solo emite por encima del umbral", () => {
  trackSlowPageLoad(500); // rápido: sin evento
  assert.equal(__telemetryInternals.getQueue().length, 0);

  trackSlowPageLoad(3200); // lento: emite en canal aprobado
  const event = __telemetryInternals.getQueue()[0];
  assert.equal(event.event_type, "api_response_error_recorded");
  assert.equal((event.properties as { endpoint: string }).endpoint, "frontend:slow_page_load");
});

test("slow api response emite en el canal aprobado", () => {
  trackSlowApiResponse("/inventory/products");
  const event = __telemetryInternals.getQueue()[0];
  assert.equal(event.event_type, "api_response_error_recorded");
  assert.equal((event.properties as { endpoint: string }).endpoint, "frontend:slow_api_response");
});