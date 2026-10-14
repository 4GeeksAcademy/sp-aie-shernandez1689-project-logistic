import type {
  InboundOrder,
  InboundOrderInput,
  InventoryOrder,
  OutboundOrder,
  OutboundOrderInput,
  Product,
  ProductInput,
} from "@/types/inventory";
import { getAccessToken } from "./auth";

export type * from "@/types/inventory";

export class InventoryApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly fieldErrors: Record<string, string> = {},
  ) {
    super(message);
    this.name = "InventoryApiError";
  }
}

function extractError(payload: unknown, status: number): InventoryApiError {
  const fallback =
    status >= 500
      ? `Error del servidor (${status}). Inténtalo de nuevo más tarde.`
      : `La solicitud no es válida (${status}).`;
  const detail =
    typeof payload === "object" && payload !== null
      ? "detail" in payload ? payload.detail
        : "message" in payload ? payload.message
          : "error" in payload ? payload.error : null
      : typeof payload === "string" ? payload : null;

  if (typeof detail === "string" && detail.trim()) {
    return new InventoryApiError(detail, status);
  }

  if (Array.isArray(detail)) {
    const fieldErrors: Record<string, string> = {};
    const messages: string[] = [];
    for (const item of detail) {
      const msg = typeof item?.msg === "string" ? item.msg : "Valor no válido";
      const loc = Array.isArray(item?.loc) ? item.loc.filter((part: unknown) => part !== "body") : [];
      const field = loc.join(".");
      if (field) fieldErrors[field] = msg;
      messages.push(field ? `${field}: ${msg}` : msg);
    }
    if (messages.length) return new InventoryApiError(messages.join(" · "), status, fieldErrors);
  }

  return new InventoryApiError(fallback, status);
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getAccessToken();
  if (!token) {
    throw new InventoryApiError("Tu sesión no está iniciada. Inicia sesión para continuar.", 401);
  }

  const baseUrl = (process.env.NEXT_PUBLIC_INVENTORY_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  const headers = new Headers(options.headers);
  headers.set("Authorization", `Bearer ${token}`);
  headers.set("Accept", "application/json");
  if (options.body) headers.set("Content-Type", "application/json");

  let response: Response;
  try {
    response = await fetch(`${baseUrl}/inventory${path}`, {
      ...options,
      headers,
      cache: "no-store",
      signal: AbortSignal.timeout(15000),
    });
  } catch (error) {
    if (error instanceof Error && error.name === "TimeoutError") {
      throw new InventoryApiError("La solicitud tardó demasiado. Vuelve a intentarlo.", null);
    }
    throw new InventoryApiError("No se pudo conectar con el servicio de inventario.", null);
  }

  let payload: unknown = null;
  try {
    const body = await response.text();
    if (body.trim()) {
      try {
        payload = JSON.parse(body);
      } catch {
        if (!response.ok) payload = body;
      }
    }
  } catch {
    payload = null;
  }

  if (!response.ok) throw extractError(payload, response.status);
  if (payload === null) {
    throw new InventoryApiError("El servicio devolvió una respuesta inesperada.", response.status);
  }
  return payload as T;
}

export function getProducts(): Promise<Product[]> {
  return request<Product[]>("/products");
}

export function getProduct(id: number): Promise<Product> {
  return request<Product>(`/products/${encodeURIComponent(id)}`);
}

export function createProduct(input: ProductInput): Promise<Product> {
  return request<Product>("/products", { method: "POST", body: JSON.stringify(input) });
}

export function getOrders(): Promise<InventoryOrder[]> {
  return request<InventoryOrder[]>("/orders");
}

export function createInboundOrder(input: InboundOrderInput): Promise<InboundOrder> {
  return request<InboundOrder>("/orders/inbound", { method: "POST", body: JSON.stringify(input) });
}

export function createOutboundOrder(input: OutboundOrderInput): Promise<OutboundOrder> {
  return request<OutboundOrder>("/orders/outbound", { method: "POST", body: JSON.stringify(input) });
}
