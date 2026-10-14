"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { clearAccessToken } from "@/lib/auth";
import { createInboundOrder, createOutboundOrder, getProduct, getProducts, InventoryApiError, type SKU, type ExitType } from "@/lib/inventory";

export default function OrderPage() {
  const { direction } = useParams<{ direction: string }>();
  const router = useRouter();
  const inbound = direction === "inbound";
  const validDirection = inbound || direction === "outbound";
  const [products, setProducts] = useState<SKU[]>([]);
  const [productId, setProductId] = useState("");
  const product = products.find((item) => String(item.id) === productId);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState(false);
  const [exitType, setExitType] = useState<ExitType>("dispatch");
  const [quantity, setQuantity] = useState("");
  const [quantityError, setQuantityError] = useState("");
  const [stockResult, setStockResult] = useState<{ selection: string; product?: SKU; error?: string } | null>(null);
  const selectedStock = stockResult?.selection === productId ? stockResult : null;
  const stockProduct = inbound ? product : selectedStock?.product;
  const stockPending = !inbound && !!productId && !selectedStock;
  const exceedsStock = !inbound && stockProduct && Number(quantity) > stockProduct.current_stock;

  useEffect(() => {
    let active = true;
    const id = Number(new URLSearchParams(window.location.search).get("sku_id"));
    const load = async () => {
      if (!validDirection) {
        throw new Error("Selecciona un SKU y un tipo de movimiento de stock válidos desde el inventario.");
      }
      return getProducts();
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
        setError(reason instanceof Error ? reason.message : "No se pudieron cargar los SKUs.");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [inbound, validDirection, router]);

  useEffect(() => {
    if (inbound || !productId) return;
    let active = true;
    getProduct(Number(productId))
      .then((data) => {
        if (active) setStockResult({ selection: productId, product: data });
      })
      .catch((reason: unknown) => {
        if (!active) return;
        if (reason instanceof InventoryApiError && reason.status === 401) {
          clearAccessToken();
          router.replace("/login");
          return;
        }
        setStockResult({ selection: productId, error: reason instanceof Error ? reason.message : "No se pudo consultar el stock." });
      });
    return () => { active = false; };
  }, [inbound, productId, router]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!stockProduct || saving) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const quantity = Number(data.get("quantity"));
    if (!Number.isSafeInteger(quantity) || quantity <= 0) {
      setError("La cantidad debe ser un número entero mayor que cero.");
      return;
    }
    setSaving(true);
    setError("");
    setQuantityError("");
    setSuccess(false);
    try {
      const input = { sku_id: stockProduct.id, warehouse: stockProduct.warehouse, quantity };
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
        setQuantity("");
      }
      setSuccess(true);
    } catch (reason) {
      if (reason instanceof InventoryApiError && reason.status === 401) {
        clearAccessToken();
        router.replace("/login");
        return;
      }
      if (!inbound && reason instanceof InventoryApiError && reason.status === 400) {
        setQuantityError(reason.message);
      } else {
        setError(reason instanceof Error ? reason.message : "No se pudo registrar el movimiento de stock.");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="workspace">
      <Link className="back-link" href="/backoffice/inventory/products">Volver a SKUs</Link>
      <header className="page-heading"><div><p className="eyebrow">Inventario · TrackFlow</p><h1>{validDirection ? inbound ? "Recepción de mercancía" : "Despacho o pérdida" : "Movimiento de stock no válido"}</h1></div></header>
      {error && <p role="alert" className="action-error">{error}</p>}
      {loading && <p role="status">Cargando SKUs...</p>}
      {success && <p role="status" className="status status-selected mb-4">{inbound ? "Recepción registrada correctamente." : "Salida de stock registrada correctamente."}</p>}
      {!loading && !error && products.length === 0 && <p className="empty-state">No hay SKUs disponibles para registrar movimientos de stock.</p>}
      {!loading && products.length > 0 && (inbound || !success) && (
        <form className="candidate-form max-w-2xl" onSubmit={submit}>
          {product && <><h2>{product.name}</h2><p className="subtitle mb-6">{product.sku} · {product.client_name} · {product.warehouse}{inbound && ` · Stock actual: ${product.current_stock}`}</p></>}
          <fieldset disabled={saving} className="candidate-form-grid">
            <legend className="sr-only">Datos del movimiento de stock</legend>
            <label className="candidate-form-wide">SKU
              <select aria-label="SKU" name="sku_id" value={productId} onChange={(event) => { setProductId(event.target.value); setSuccess(false); setQuantity(""); setQuantityError(""); setStockResult(null); setError(""); }} required className="w-full rounded-md border border-[var(--border)] bg-[var(--surface)] p-2 text-[var(--foreground)]">
                <option value="">Selecciona un SKU</option>
                {products.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.sku} · {item.warehouse}</option>)}
              </select>
            </label>
            {!inbound && <div className="candidate-form-wide" aria-live="polite">
              {stockPending && <p role="status">Consultando stock...</p>}
              {selectedStock?.error && <p role="alert" className="action-error">{selectedStock.error}</p>}
              {stockProduct && <p id="stock-current" role="status">Stock actual: <strong>{stockProduct.current_stock}</strong> unidades · {stockProduct.warehouse}</p>}
            </div>}
            <div className="flex min-w-0 flex-col gap-1.5">
              <label htmlFor="quantity">{inbound ? "Unidades recibidas" : exitType === "dispatch" ? "Unidades despachadas" : "Unidades dadas de baja"}</label>
              <input id="quantity" name="quantity" type="number" min={1} step={1} required value={quantity} onChange={(event) => { setQuantity(event.target.value); setQuantityError(""); }} disabled={!inbound && !stockProduct} aria-invalid={!!quantityError} aria-describedby={!inbound ? [stockProduct && "stock-current", exceedsStock && "quantity-warning", quantityError && "quantity-error"].filter(Boolean).join(" ") || undefined : undefined} className="w-full min-w-0 rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-[var(--foreground)]" />
              {exceedsStock && <p id="quantity-warning" role="status" className="text-amber-300">La cantidad supera el stock disponible ({stockProduct.current_stock} unidades).</p>}
              {quantityError && <p id="quantity-error" role="alert" className="action-error">{quantityError}</p>}
            </div>
            {inbound ? <label>Referencia de despacho del cliente<input name="reference" required /></label> : (
              <>
                <label>Tipo de salida
                  <select aria-label="Tipo de salida" name="exit_type" value={exitType} onChange={(event) => setExitType(event.target.value as ExitType)} className="w-full rounded-md border border-[var(--border)] bg-[var(--surface)] p-2 text-[var(--foreground)]">
                    <option value="dispatch">Despacho</option><option value="loss">Pérdida</option>
                  </select>
                </label>
                {exitType === "dispatch" && <label className="candidate-form-wide">Número de seguimiento<input name="tracking_number" required /></label>}
              </>
            )}
            <button type="submit" className="candidate-form-wide" disabled={saving || !stockProduct}>{saving ? "Registrando..." : inbound ? "Registrar recepción" : "Registrar salida de stock"}</button>
          </fieldset>
        </form>
      )}
    </main>
  );
}