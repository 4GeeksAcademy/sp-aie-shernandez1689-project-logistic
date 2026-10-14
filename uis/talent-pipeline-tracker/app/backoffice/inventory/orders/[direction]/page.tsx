"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { clearAccessToken } from "@/lib/auth";
import { createInboundOrder, createOutboundOrder, getProduct, getProducts, InventoryApiError, type Product, type ExitType } from "@/lib/inventory";

export default function OrderPage() {
  const { direction } = useParams<{ direction: string }>();
  const router = useRouter();
  const inbound = direction === "inbound";
  const validDirection = inbound || direction === "outbound";
  const [products, setProducts] = useState<Product[]>([]);
  const [productId, setProductId] = useState("");
  const product = products.find((item) => String(item.id) === productId);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState(false);
  const [exitType, setExitType] = useState<ExitType>("dispatch");

  useEffect(() => {
    let active = true;
    const id = Number(new URLSearchParams(window.location.search).get("sku_id"));
    const load = async () => {
      if (!validDirection || (!inbound && (!Number.isSafeInteger(id) || id <= 0))) {
        throw new Error("Selecciona un producto y un tipo de orden válidos desde el inventario.");
      }
      return inbound ? getProducts() : [await getProduct(id)];
    };
    load()
      .then((data) => {
        if (active) {
          setProducts(data);
          setProductId(data.some((item) => item.id === id) ? String(id) : "");
        }
      })
      .catch((reason: unknown) => {
        if (!active) return;
        if (reason instanceof InventoryApiError && reason.status === 401) {
          clearAccessToken();
          router.replace("/login");
          return;
        }
        setError(reason instanceof Error ? reason.message : "No se pudieron cargar los productos.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [inbound, validDirection, router]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!product || saving) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const quantity = Number(data.get("quantity"));
    if (!Number.isSafeInteger(quantity) || quantity <= 0) {
      setError("La cantidad debe ser un número entero mayor que cero.");
      return;
    }
    setSaving(true);
    setError("");
    setSuccess(false);
    try {
      const input = { sku_id: product.id, warehouse: product.warehouse, quantity };
      if (inbound) {
        const reference = String(data.get("reference") ?? "").trim();
        if (!reference) throw new Error("La referencia de recepción es obligatoria.");
        await createInboundOrder({ ...input, reference });
      } else {
        const tracking = String(data.get("tracking_number") ?? "").trim();
        if (exitType === "dispatch" && !tracking) throw new Error("El número de seguimiento es obligatorio para un despacho.");
        await createOutboundOrder({ ...input, exit_type: exitType, tracking_number: exitType === "loss" ? null : tracking });
      }
      if (inbound) {
        form.reset();
        setProductId("");
      }
      setSuccess(true);
    } catch (reason) {
      if (reason instanceof InventoryApiError && reason.status === 401) {
        clearAccessToken();
        router.replace("/login");
        return;
      }
      setError(reason instanceof Error ? reason.message : "No se pudo registrar la orden.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="workspace">
      <Link className="back-link" href="/backoffice/inventory/products">Volver a productos</Link>
      <header className="page-heading"><div><p className="eyebrow">Inventario · TrackFlow</p><h1>{validDirection ? inbound ? "Crear entrada" : "Crear salida" : "Orden no válida"}</h1></div></header>
      {error && <p role="alert" className="action-error">{error}</p>}
      {loading && <p role="status">Cargando productos...</p>}
      {success && <p role="status" className="status status-selected mb-4">Orden registrada correctamente.</p>}
      {!loading && !error && products.length === 0 && <p className="empty-state">No hay productos disponibles para registrar una orden.</p>}
      {!loading && products.length > 0 && (inbound || !success) && (
        <form className="candidate-form max-w-2xl" onSubmit={submit}>
          {product && <><h2>{product.name}</h2><p className="subtitle mb-6">{product.sku} · {product.client_name} · {product.warehouse} · Stock actual: {product.current_stock}</p></>}
          <fieldset disabled={saving} className="candidate-form-grid">
            <legend className="sr-only">Datos de la orden</legend>
            {inbound && <label className="candidate-form-wide">Producto
              <select aria-label="Producto" name="sku_id" value={productId} onChange={(event) => { setProductId(event.target.value); setSuccess(false); }} required className="w-full rounded-md border border-[var(--border)] bg-[var(--surface)] p-2 text-[var(--foreground)]">
                <option value="">Selecciona un producto</option>
                {products.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.sku} · {item.warehouse}</option>)}
              </select>
            </label>}
            <label>Cantidad<input name="quantity" type="number" min={1} step={1} required /></label>
            {inbound ? <label>Referencia de recepción<input name="reference" required /></label> : (
              <>
                <label>Tipo de salida
                  <select aria-label="Tipo de salida" name="exit_type" value={exitType} onChange={(event) => setExitType(event.target.value as ExitType)} className="w-full rounded-md border border-[var(--border)] bg-[var(--surface)] p-2 text-[var(--foreground)]">
                    <option value="dispatch">Despacho</option><option value="loss">Pérdida</option>
                  </select>
                </label>
                {exitType === "dispatch" && <label className="candidate-form-wide">Número de seguimiento<input name="tracking_number" required /></label>}
              </>
            )}
            <button type="submit" className="candidate-form-wide" disabled={saving || !product}>{saving ? "Registrando..." : inbound ? "Registrar entrada" : "Registrar salida"}</button>
          </fieldset>
        </form>
      )}
    </main>
  );
}