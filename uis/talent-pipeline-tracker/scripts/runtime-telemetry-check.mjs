/**
 * Verificación de runtime — Fase 3 (equivalente a la pestaña Network).
 *
 * No hay navegador headless instalado en este entorno, así que este script
 * emula fielmente la capa del navegador: shimmea window/document/navigator/
 * Blob/sendBeacon y ejecuta el MISMO código real que produce el backoffice
 * (`lib/telemetry.ts` + `lib/instrumentation.ts`) contra el stub vivo
 * `services/api` (POST /telemetry/events → 200 {"received": N}).
 *
 * Captura cada POST que sale del telemetry client — el equivalente de lo que
 * se vería en la pestaña Network del navegador — y valida que:
 *   1. El endpoint recibe {"events": [...]} con POST JSON.
 *   2. Cada evento de batch y de sendBeacon lleva el envelope completo
 *      (eventId, timestamp, sessionId, userId, event_type, schemaVersion,
 *      requestId, properties).
 *   3. Se emiten SOLO event_type + claves de properties aprobados en
 *      docs/telemetry/event-schemas.json (lista de abajo).
 *   4. El stub responde 200 y cuenta los eventos.
 *
 * Ejecución:
 *   node --experimental-strip-types scripts/runtime-telemetry-check.mjs
 *   (Node 24 ya soporta carga de .ts nativa sin flag en `node --test`,
 *   pero para scripts usamos mm.mjs y cargamos los .ts por import directo,
 *   que Node 24 resuelve con tipo stripping.)
 */

import { readFileSync } from "node:fs";

// Carga los módulos reales del cliente. El import dinámico ocurre MÁS ABAJO
// (tras definir los shims de window/document/navigator), para que la
// auto-inicialización del TelemetryService vea el entorno navegador simulado.

// --- 1. Allowed schemas (Fase 1) — para validar que NO hay claves extra -------
const raw = readFileSync(
  new URL("../../../docs/telemetry/event-schemas.json", import.meta.url),
  "utf8",
);
const schemas = JSON.parse(raw);

function resolveSchema(eventType) {
  // El documento es un JSON-Schema con `definitions.<event_type>`.
  const defs = schemas?.definitions;
  if (defs && typeof defs === "object" && defs[eventType]) return defs[eventType];
  return undefined;
}

function checkAllowedProperties(eventType, props, label) {
  const schema = resolveSchema(eventType);
  if (!schema) {
    assert(false, `${label}: no se encontró schema para ${eventType}`);
    return;
  }
  const allowedKeys = new Set(Object.keys(schema.properties || {}));
  const extra = Object.keys(props).filter((k) => !allowedKeys.has(k));
  assert(extra.length === 0, `${label}: claves de properties dentro del allowlist (extra: ${JSON.stringify(extra)})`);
  const required = schema.required || [];
  const missing = required.filter(
    (k) => props[k] === undefined || props[k] === null || props[k] === "",
  );
  assert(missing.length === 0, `${label}: required present (faltan: ${JSON.stringify(missing)})`);
  // Enum checks para warehouse/category/error_category/exit_type.
  for (const [key, value] of Object.entries(props)) {
    const enumVals = schema.properties?.[key]?.enum;
    if (Array.isArray(enumVals) && value !== undefined) {
      assert(enumVals.includes(value), `${label}: ${key} dentro de enum (${JSON.stringify(value)})`);
    }
  }
}

// --- 2. Endpoint de ingesta (stub de Fase 1) ----------------------------------
const ENDPOINT = "http://127.0.0.1:8000/telemetry/events";
process.env.NEXT_PUBLIC_TELEMETRY_ENDPOINT = ENDPOINT;

// --- 3. Shim de entorno navegador ---------------------------------------------
const storage = new Map();
const sessionStorageMap = new Map();

function makeJwt(userId, role) {
  const header = btoa(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = btoa(JSON.stringify({ sub: userId, role, exp: Math.floor(Date.now() / 1000) + 3600 }));
  return `${header}.${payload}.sig`;
}

globalThis.window = {
  localStorage: {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
  },
  sessionStorage: {
    getItem: (k) => (sessionStorageMap.has(k) ? sessionStorageMap.get(k) : null),
    setItem: (k, v) => sessionStorageMap.set(k, String(v)),
    removeItem: (k) => sessionStorageMap.delete(k),
  },
  addEventListener: () => {},
};
globalThis.document = {
  visibilityState: "visible",
  addEventListener: () => {},
};
Object.defineProperty(globalThis, "navigator", {
  value: {
    sendBeacon: (url, data) => {
      const text = typeof data === "string" ? data : JSON.stringify(data);
      captureHttp("beacon", url, text);
      return true;
    },
  },
  configurable: true,
  writable: true,
});

// --- 4. Captura del "pestaña Network": cada salida HTTP del telemetry client --
const networkLog = [];
function captureHttp(kind, url, bodyText) {
  networkLog.push({ kind, url, rawBody: bodyText });
}

// Intercepta fetch para registrar las peticiones del telemetry client.
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, options) => {
  const isTelemetry = String(url).includes("/telemetry/events");
  if (isTelemetry && options && String(options.method || "").toUpperCase() === "POST") {
    const bodyText = typeof options.body === "string" ? options.body : JSON.stringify(options.body);
    captureHttp("fetch", String(url), bodyText);
  }
  return realFetch(url, options);
};

// --- 5. Importar el cliente real después de definir los globals -----------------
const telemetry = await import("../lib/telemetry.ts");
const instr = await import("../lib/instrumentation.ts");

// Endpoint controlado por el cliente (mismo valor del .env.local.example).
telemetry.__telemetryInternals.setEndpoint(ENDPOINT);

// --- 6. Utilidades de validación ------------------------------------------------
let failures = 0;
function assert(cond, msg) {
  if (!cond) {
    failures += 1;
    console.error(`  ✖ ${msg}`);
  } else {
    console.log(`  ✔ ${msg}`);
  }
}

function checkEnvelope(e, label) {
  const required = ["eventId", "timestamp", "sessionId", "userId", "event_type", "schemaVersion", "requestId", "properties"];
  for (const field of required) {
    assert(e[field] !== undefined && e[field] !== null && e[field] !== "", `${label}: envelope.${field} presente`);
  }
  assert(/^\d{4}-\d{2}-\d{2}T/.test(e.timestamp), `${label}: timestamp ISO 8601 (${e.timestamp})`);
  assert(/^\d+\.\d+\.\d+$/.test(e.schemaVersion), `${label}: schemaVersion semver (${e.schemaVersion})`);
  assert(/^req_/.test(e.requestId), `${label}: requestId correlación de lote (${e.requestId})`);
  assert(typeof e.properties === "object" && e.properties !== null, `${label}: properties es objeto`);
}

// --- 7. Escenario real ---------------------------------------------------------

// 7.0 Autenticación: el usuario accede al backoffice (rol manager según JWT).
storage.set("trackflow_access_token", makeJwt("user-42", "manager"));
instr.recordLoginTime();
console.log("\n## Flujo A — Técnico transversal");
instr.trackPageNavigated("login", "inventory_products", "nav_link");
instr.trackPageNavigated("inventory_products", "inventory_orders", "nav_link");
instr.trackPageNavigated("inventory_orders", "inventory_products", "breadcrumb");
instr.trackFrontendError("uncaught_error");
instr.trackFrontendError("unhandled_rejection");
instr.trackSlowPageLoad(3_200);
instr.trackSlowApiResponse("/inventory/products");
instr.trackApiResponseError("/inventory/products", "GET", 401);
instr.trackSessionTokenExpired("/inventory/products");
instr.trackApiResponseError("/inventory/orders/inbound", "POST", 422);

console.log("\n## Flujo B — Inventario (negocio)");
const skuLa = { id: 1, name: "Sneaker CLT", sku: "CLT-SNK-W-42", client_name: "CLT Hombre", category: "fashion", warehouse: "LA", current_stock: 45 };
const skuZgz = { id: 2, name: "Cargador USB-C", sku: "ELC-USB-C-20W", client_name: "Voltix", category: "electronics", warehouse: "ZGZ", current_stock: 3 };
instr.trackInboundOrderCreated(skuLa, 20, "INB-2025-0142");
instr.trackOutboundOrderCreated(skuZgz, 2, "dispatch");
instr.trackStockThresholdTriggered(skuZgz, skuZgz.current_stock - 2, 10); // restan 1 ≤ 10
instr.trackOutboundInsufficientStock(skuZgz, 12);
instr.trackStockThresholdTriggered(skuLa, skuLa.current_stock - 3, 10); // 42 > 10 → NO emite

// 7.1 Autenticación (negocio): intento fallido + éxito.
console.log("\n## Flujo C — Autenticación (negocio)");
await instr.trackLoginFailed("alguien@example.com", "invalid_credentials");
await instr.trackLoginFailed("alguien@example.com", "invalid_credentials");
storage.set("trackflow_access_token", makeJwt("user-42", "manager"));
instr.recordLoginTime();
instr.trackLoginSucceeded();

// --- 8. Forzar el envío real al stub ---
console.log("\n## Envío al stub (flush fetch real)");
await telemetry.flushQueue();
await new Promise((r) => setTimeout(r, 300));

// --- 9. Verificación del "Network tab" ---
console.log("\n## Pestaña Network — peticiones emitidas por el telemetry client");
console.log(`Total salidas HTTP capturadas: ${networkLog.length} (fetch=${networkLog.filter((n) => n.kind === "fetch").length}, beacon=${networkLog.filter((n) => n.kind === "beacon").length})`);

for (const [i, entry] of networkLog.entries()) {
  let parsed;
  try {
    parsed = JSON.parse(entry.rawBody);
  } catch {
    parsed = null;
  }
  console.log(`\n[${i + 1}] ${entry.kind.toUpperCase()} ${entry.url}`);
  if (!parsed || !Array.isArray(parsed.events)) {
    console.error("  ✖ body no es un lote {\"events\":[...]}");
    failures += 1;
    continue;
  }
  console.log(`  batch: ${parsed.events.length} evento(s), event_types=${parsed.events.map((e) => e.event_type).join(", ")}`);
  for (const e of parsed.events) {
    const label = `  event ${e.event_type}`;
    checkEnvelope(e, label);
    checkAllowedProperties(e.event_type, e.properties, label);
  }
}

console.log("\n## Resumen");
if (failures === 0) {
  console.log("✔ TODAS las validaciones pasaron: envelopes completos, allowlists respetadas, batch JSON correcto.");
} else {
  console.error(`✖ ${failures} validación(es) fallaron.`);
  process.exitCode = 1;
}