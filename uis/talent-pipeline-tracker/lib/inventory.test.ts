import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { createInboundOrder, createOutboundOrder, getProducts, InventoryApiError } from "./inventory";
import { AuthApiError, requireAuthenticatedUser } from "./auth";

const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
let token: string | null;

beforeEach(() => {
  token = "test-token";
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { localStorage: { getItem: (key: string) => key === "trackflow_access_token" ? token : null } },
  });
});

afterEach(() => {
  mock.restoreAll();
  if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
  else Reflect.deleteProperty(globalThis, "window");
});

test("products use the existing Bearer token and preserve warehouse stock", async () => {
  const products = [{ id: 1, sku: "SKU-LA", warehouse: "LA", current_stock: 10 }];
  const fetchMock = mock.method(globalThis, "fetch", async () => Response.json(products));
  assert.deepEqual(await getProducts(), products);
  const [url, options] = fetchMock.mock.calls[0].arguments as [string, RequestInit];
  assert.ok(url.endsWith("/inventory/products"));
  assert.equal(new Headers(options.headers).get("Authorization"), "Bearer test-token");
});

test("missing authentication fails before a request is sent", async () => {
  token = null;
  const fetchMock = mock.method(globalThis, "fetch", async () => Response.json([]));
  await assert.rejects(getProducts(), (error: unknown) => error instanceof InventoryApiError && error.status === 401);
  assert.equal(fetchMock.mock.callCount(), 0);
});

test("API error messages survive 4xx and 5xx responses", async () => {
  for (const [status, body, message] of [
    [400, { detail: "Insufficient stock" }, "Insufficient stock"],
    [401, { detail: "Token expired" }, "Token expired"],
    [403, { message: "Access denied" }, "Access denied"],
    [500, { error: "Inventory unavailable" }, "Inventory unavailable"],
  ] as const) {
    mock.method(globalThis, "fetch", async () => Response.json(body, { status }));
    await assert.rejects(getProducts(), (error: unknown) => error instanceof InventoryApiError && error.status === status && error.message === message);
    mock.restoreAll();
  }
});

test("validation errors expose field names and messages", async () => {
  mock.method(globalThis, "fetch", async () => Response.json({ detail: [{ loc: ["body", "quantity"], msg: "Must be positive" }] }, { status: 422 }));
  await assert.rejects(getProducts(), (error: unknown) => error instanceof InventoryApiError && error.fieldErrors.quantity === "Must be positive" && error.message.includes("quantity"));
});

test("network and non-JSON failures are explicit", async () => {
  mock.method(globalThis, "fetch", async () => { throw new TypeError("Network failure"); });
  await assert.rejects(getProducts(), /No se pudo conectar/);
  mock.restoreAll();
  mock.method(globalThis, "fetch", async () => new Response("Bad gateway", { status: 502 }));
  await assert.rejects(getProducts(), /Bad gateway/);
  mock.restoreAll();
  mock.method(globalThis, "fetch", async () => new Response("", { status: 502 }));
  await assert.rejects(getProducts(), /Error del servidor \(502\)/);
  mock.restoreAll();
  mock.method(globalThis, "fetch", async () => new Response("Not JSON"));
  await assert.rejects(getProducts(), /respuesta inesperada/);
});

test("entry and exit requests use the central authenticated client", async () => {
  const fetchMock = mock.method(globalThis, "fetch", async () => Response.json({ id: 1 }));
  const common = { sku_id: 1, warehouse: "LA" as const, quantity: 2 };
  await createInboundOrder({ ...common, reference: "PO-123" });
  await createOutboundOrder({ ...common, exit_type: "loss", tracking_number: null });
  for (const [index, suffix] of ["inbound", "outbound"].entries()) {
    const [url, options] = fetchMock.mock.calls[index].arguments as [string, RequestInit];
    assert.ok(url.endsWith(`/inventory/orders/${suffix}`));
    assert.equal(options.method, "POST");
    assert.equal(new Headers(options.headers).get("Authorization"), "Bearer test-token");
    assert.equal(JSON.parse(options.body as string).sku_id, 1);
  }
});

test("the session guard rejects missing tokens without fetching", async () => {
  token = null;
  const fetchMock = mock.method(globalThis, "fetch", async () => Response.json({}));
  await assert.rejects(requireAuthenticatedUser(), (error: unknown) => error instanceof AuthApiError && error.status === 401);
  assert.equal(fetchMock.mock.callCount(), 0);
});

test("the session guard validates the existing token through auth/me", async () => {
  const fetchMock = mock.method(globalThis, "fetch", async () => Response.json({ email: "operator@example.com", role: "user" }));
  await requireAuthenticatedUser();
  const [url, options] = fetchMock.mock.calls[0].arguments as [string, RequestInit];
  assert.ok(url.endsWith("/auth/me"));
  assert.equal(new Headers(options.headers).get("Authorization"), "Bearer test-token");
  assert.equal(options.cache, "no-store");
});

test("the session guard distinguishes invalid sessions from permission and server failures", async () => {
  for (const status of [401, 403, 500]) {
    mock.method(globalThis, "fetch", async () => Response.json({ detail: `Auth failure ${status}` }, { status }));
    await assert.rejects(requireAuthenticatedUser(), (error: unknown) => error instanceof AuthApiError && error.status === status && error.message === `Auth failure ${status}`);
    mock.restoreAll();
  }
});

test("the session guard exposes connection failures without treating them as logout", async () => {
  mock.method(globalThis, "fetch", async () => { throw new TypeError("Network failure"); });
  await assert.rejects(requireAuthenticatedUser(), (error: unknown) => error instanceof AuthApiError && error.status === null && error.message.includes("No se pudo validar"));
});