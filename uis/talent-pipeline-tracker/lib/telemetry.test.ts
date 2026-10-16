/**
 * Tests de lib/telemetry.ts — TelemetryService del backoffice.
 *
 * Se ejecutan con `node --test` (Node 24), igual que inventory.test.ts.
 * Verifican:
 *   - envelope completo generado automáticamente por track()
 *   - sessionId estable en la pestaña y userId del JWT (o "anonymous")
 *   - batch flush a los 20 eventos
 *   - debounce flush a los 10 segundos
 *   - sendBeacon en visibilitychange (flush de cierre confiable)
 *   - reintentos con backoff exponencial y descarte
 *   - 4xx sin reintento y descarte sin endpoint configurado
 */
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";

import { track, flushQueue, onVisibilityChange, __telemetryInternals } from "./telemetry.ts";
import { TELEMETRY_SCHEMA_VERSION } from "../types/telemetry.ts";

const ENDPOINT = "https://ingest.trackflow.local/telemetry/events";

const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");

/** Token JWT falso con el claim `sub` que emite services/user-api. */
function makeJwt(sub: string | null): string | null {
  if (sub === null) return null;
  const b64 = (input: unknown) => Buffer.from(JSON.stringify(input)).toString("base64url");
  return `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub, exp: 9999999999 })}.${"sig"}`;
}

/** Window falso con localStorage (token) y sessionStorage (sessionId). */
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

let token: string | null = "user-42";

beforeEach(() => {
  token = "user-42";
  __telemetryInternals.reset();
  __telemetryInternals.setEndpoint(ENDPOINT);

  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: createWindow(makeJwt(token)),
  });
  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: { visibilityState: "visible", addEventListener: () => undefined },
  });
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { sendBeacon: (() => true) as typeof navigator.sendBeacon },
  });
});

afterEach(() => {
  mock.restoreAll();
  mock.timers.reset();
  __telemetryInternals.reset();
  delete process.env.NEXT_PUBLIC_TELEMETRY_ENDPOINT;

  if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
  else Reflect.deleteProperty(globalThis, "window");
  if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument);
  else Reflect.deleteProperty(globalThis, "document");
  if (originalNavigator) Object.defineProperty(globalThis, "navigator", originalNavigator);
  else Reflect.deleteProperty(globalThis, "navigator");
});

test("track() encola un evento con el envelope completo (sin campos manuales)", () => {
  track("inbound_order_created", { order_id: 77 });
  const queue = __telemetryInternals.getQueue();
  assert.equal(queue.length, 1);

  const event = queue[0];
  // eventId: UUID v4.
  assert.match(event.eventId, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  // timestamp: ISO 8601 válido.
  assert.ok(!Number.isNaN(Date.parse(event.timestamp)));
  assert.ok(event.timestamp.includes("T") && event.timestamp.endsWith("Z"));
  // Campo derivado del argumento eventType.
  assert.equal(event.event_type, "inbound_order_created");
  // schemaVersion: constante compartida (nunca duplicada por el caller).
  assert.equal(event.schemaVersion, TELEMETRY_SCHEMA_VERSION);
  assert.equal(event.schemaVersion, "1.0.0");
  // properties intactos.
  assert.deepEqual(event.properties, { order_id: 77 });
});

test("sessionId estable por pestaña y userId desde el claim sub del JWT", () => {
  track("login_succeeded", {});
  const first = __telemetryInternals.getQueue()[0];
  assert.ok(first.sessionId.length > 0);
  assert.equal(first.userId, "user-42");

  __telemetryInternals.reset();
  track("inventory_created", {});
  const second = __telemetryInternals.getQueue()[0];
  // El sessionStorage falso persiste entre reset: mismo sessionId.
  assert.equal(second.sessionId, first.sessionId);
  assert.equal(second.userId, "user-42");
});

test("sin sesión autenticada userId es 'anonymous'", () => {
  token = null;
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: createWindow(null),
  });

  track("inventory_created", {});
  const event = __telemetryInternals.getQueue()[0];
  assert.equal(event.userId, "anonymous");
});

test("batch flush: al acumular 20 eventos se envía la cola en un solo POST", async () => {
  const fetchMock = mock.method(globalThis, "fetch", async () => Response.json({ received: 20 }));

  for (let i = 0; i < __telemetryInternals.MAX_BATCH_SIZE - 1; i += 1) {
    track("inventory_created", { index: i });
  }
  assert.equal(fetchMock.mock.callCount(), 0, "todavía no llega al tamaño de lote");

  track("inventory_created", { index: 19 }); // 20º evento → flush inmediato.
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(fetchMock.mock.callCount(), 1);
  const [url, options] = fetchMock.mock.calls[0].arguments as [string, RequestInit];
  assert.equal(url, ENDPOINT);

  const body = JSON.parse(String(options.body)) as { events: Array<Record<string, unknown>> };
  assert.equal(body.events.length, 20);
  // requestId de correlación presente en todos los eventos del lote.
  const requestIds = new Set(body.events.map((e) => e.requestId));
  assert.equal(requestIds.size, 1);
  assert.match([...requestIds][0] as string, /^req_/);

  assert.equal(__telemetryInternals.getQueue().length, 0);
});

test("debounce: si no se alcanza el lote, se envía la cola a los 10 segundos", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  const fetchMock = mock.method(globalThis, "fetch", async () => Response.json({ received: 3 }));

  track("inventory_created", { sku: "SKU-LA" });
  track("login_succeeded", {});
  track("inbound_order_created", { order_id: 1 });

  mock.timers.tick(__telemetryInternals.FLUSH_INTERVAL_MS - 1);
  assert.equal(fetchMock.mock.callCount(), 0);
  mock.timers.tick(1);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(fetchMock.mock.callCount(), 1);

  const body = JSON.parse(String((fetchMock.mock.calls[0].arguments as [string, RequestInit])[1].body)) as {
    events: Array<{ event_type: string }>;
  };
  assert.deepEqual(
    body.events.map((e) => e.event_type),
    ["inventory_created", "login_succeeded", "inbound_order_created"],
  );
});

test("visibilitychange oculto → sendBeacon con el lote pendiente como Blob JSON", async () => {
  const beacons: Array<[string, Blob]> = [];
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: {
      sendBeacon: (url: string, data: Blob) => {
        beacons.push([url, data]);
        return true;
      },
    },
  });

  track("inventory_created", { sku: "SKU-ZGZ" });
  track("outbound_order_created", { order_id: 5 });

  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: {
      visibilityState: "hidden",
      addEventListener: () => undefined,
    },
  });

  onVisibilityChange();
  assert.equal(beacons.length, 1);
  assert.equal(beacons[0][0], ENDPOINT);
  assert.ok(beacons[0][1] instanceof Blob);

  const payload = JSON.parse(await beacons[0][1].text()) as { events: Array<{ event_type: string }> };
  assert.equal(payload.events.length, 2);
  assert.equal(payload.events[0].event_type, "inventory_created");

  // La cola quedó vacía tras el flush de cierre.
  assert.equal(__telemetryInternals.getQueue().length, 0);
});

test("reintentos con backoff exponencial y descarte del lote tras agotarlos", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  // Falla siempre (error de red).
  const fetchMock = mock.method(globalThis, "fetch", async () => {
    throw new TypeError("Network failure");
  });

  // Helper: drena microtasks pendientes (await async/await en sendWithRetry).
  const step = async () => {
    await Promise.resolve();
    await Promise.resolve();
  };

  track("inventory_created", {});
  const pending = flushQueue();
  await step(); // intento inicial de fetch

  assert.equal(fetchMock.mock.callCount(), 1); // intento inicial
  mock.timers.tick(__telemetryInternals.BASE_BACKOFF_MS); // 1s
  await step();
  assert.equal(fetchMock.mock.callCount(), 2);
  mock.timers.tick(__telemetryInternals.BASE_BACKOFF_MS * 2); // 2s
  await step();
  assert.equal(fetchMock.mock.callCount(), 3);
  mock.timers.tick(__telemetryInternals.BASE_BACKOFF_MS * 4); // 4s
  await step();
  assert.equal(fetchMock.mock.callCount(), 4); // último intento permitido

  await pending;
  // El lote se descarta tras MAX_RETRIES reintentos fallidos.
  assert.equal(fetchMock.mock.callCount(), __telemetryInternals.MAX_RETRIES + 1);
  assert.equal(__telemetryInternals.getQueue().length, 0);
});

test("respuestas 4xx descartan el lote sin reintentar", async () => {
  const fetchMock = mock.method(globalThis, "fetch", async () => Response.json({}, { status: 400 }));
  track("inventory_created", {});
  await flushQueue();
  assert.equal(fetchMock.mock.callCount(), 1);
  assert.equal(__telemetryInternals.getQueue().length, 0);
});

test("sin endpoint configurado el lote se descarta silenciosamente", async () => {
  __telemetryInternals.setEndpoint("");
  const fetchMock = mock.method(globalThis, "fetch", async () => Response.json({}, { status: 200 }));
  track("inventory_created", {});
  await flushQueue();
  assert.equal(fetchMock.mock.callCount(), 0);
  assert.equal(__telemetryInternals.getQueue().length, 0);
});

test("flushQueue() exitoso vacía la cola y envía evts con requestId", async () => {
  const fetchMock = mock.method(globalThis, "fetch", async () => Response.json({ received: 2 }));
  track("inventory_created", { a: 1 });
  track("login_succeeded", {});
  await flushQueue();

  assert.equal(fetchMock.mock.callCount(), 1);
  const body = JSON.parse(String((fetchMock.mock.calls[0].arguments as [string, RequestInit])[1].body)) as {
    events: Array<{ requestId: string }>;
  };
  assert.ok(body.events.every((e) => typeof e.requestId === "string" && e.requestId.startsWith("req_")));
  assert.equal(__telemetryInternals.getQueue().length, 0);
});