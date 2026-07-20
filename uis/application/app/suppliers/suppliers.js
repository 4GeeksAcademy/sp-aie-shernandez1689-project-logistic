const VALID_CATEGORIES = [
  "carrier_last_mile",
  "carrier_international",
  "warehouse_supplies",
  "packaging_materials",
  "reverse_logistics",
  "fleet_maintenance",
  "it_and_wms_software",
  "cleaning_and_facilities",
];

const API_STORAGE_KEY = "trackflow_api_base";
const defaultApiBase = "http://127.0.0.1:8000";

const dom = {
  countryFilter: document.getElementById("countryFilter"),
  categoryFilter: document.getElementById("categoryFilter"),
  apiBase: document.getElementById("apiBase"),
  reloadBtn: document.getElementById("reloadBtn"),
  suppliersBody: document.getElementById("suppliersBody"),
  tableSummary: document.getElementById("tableSummary"),
  listFeedback: document.getElementById("listFeedback"),
  formFeedback: document.getElementById("formFeedback"),
  form: document.getElementById("supplierForm"),
  name: document.getElementById("name"),
  country: document.getElementById("country"),
  currency: document.getElementById("currency"),
  category: document.getElementById("category"),
  rate: document.getElementById("rate"),
  status: document.getElementById("status"),
  serviceZone: document.getElementById("serviceZone"),
  contactEmail: document.getElementById("contactEmail"),
  notes: document.getElementById("notes"),
};

let suppliers = [];

function getApiBase() {
  const custom = dom.apiBase.value.trim();
  return (custom || defaultApiBase).replace(/\/$/, "");
}

function setFeedback(node, message, type = "") {
  node.textContent = message;
  node.className = "feedback" + (type ? ` ${type}` : "");
}

function populateCategorySelects() {
  const options = VALID_CATEGORIES.map((c) => `<option value="${c}">${c}</option>`).join("");
  dom.categoryFilter.innerHTML = '<option value="">Todas</option>' + options;
  dom.category.innerHTML = options;
}

function fmtRate(rate, currency) {
  const locale = currency === "EUR" ? "es-ES" : "en-US";
  return new Intl.NumberFormat(locale, { style: "currency", currency }).format(rate);
}

function rowTemplate(item) {
  const categoriesHtml = item.categories.map((category) => `<span>${category}</span>`).join("");
  const nextStatus = item.status === "active" ? "suspended" : "active";
  const nextLabel = item.status === "active" ? "Suspender" : "Activar";

  return `
    <tr data-id="${item.id}">
      <td>${item.name}</td>
      <td>${item.country}</td>
      <td><div class="categories">${categoriesHtml}</div></td>
      <td>
        <div class="rate-editor">
          <input data-role="rate-input" type="number" min="0.01" step="0.01" value="${Number(item.rate_per_shipment).toFixed(2)}" />
          <button data-role="rate-save" type="button">Guardar</button>
        </div>
        <small>${fmtRate(item.rate_per_shipment, item.currency)}</small>
      </td>
      <td>
        <span class="badge ${item.status}">${item.status}</span>
      </td>
      <td>
        <button data-role="toggle-status" data-next-status="${nextStatus}" class="status-toggle" type="button">${nextLabel}</button>
      </td>
    </tr>
  `;
}

function renderTable(items) {
  dom.suppliersBody.innerHTML = items.map(rowTemplate).join("");
  dom.tableSummary.textContent = `${items.length} proveedor(es) en pantalla`;
}

async function apiFetch(path, options = {}) {
  const response = await fetch(getApiBase() + path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });

  if (!response.ok) {
    let detail = "Error inesperado";
    try {
      const payload = await response.json();
      detail = payload.detail ? JSON.stringify(payload.detail) : JSON.stringify(payload);
    } catch {
      detail = await response.text();
    }
    throw new Error(`${response.status} ${detail}`);
  }

  if (response.status === 204) {
    return null;
  }
  return response.json();
}

function buildQuery() {
  const params = new URLSearchParams();
  if (dom.countryFilter.value) params.set("country", dom.countryFilter.value);
  if (dom.categoryFilter.value) params.set("category", dom.categoryFilter.value);
  const query = params.toString();
  return query ? `/suppliers?${query}` : "/suppliers";
}

async function loadSuppliers() {
  setFeedback(dom.listFeedback, "Cargando proveedores...");
  try {
    suppliers = await apiFetch(buildQuery());
    renderTable(suppliers);
    setFeedback(dom.listFeedback, "Listado actualizado", "ok");
  } catch (error) {
    renderTable([]);
    setFeedback(dom.listFeedback, `No fue posible cargar proveedores: ${error.message}`, "error");
  }
}

async function handleCreateSupplier(event) {
  event.preventDefault();
  setFeedback(dom.formFeedback, "Enviando...");

  const payload = {
    name: dom.name.value.trim(),
    country: dom.country.value,
    categories: [dom.category.value],
    rate_per_shipment: Number(dom.rate.value),
    currency: dom.currency.value,
    status: dom.status.value,
    service_zone: dom.serviceZone.value.trim() || null,
    contact_email: dom.contactEmail.value.trim() || null,
    notes: dom.notes.value.trim() || null,
  };

  try {
    await apiFetch("/suppliers", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    dom.form.reset();
    setFeedback(dom.formFeedback, "Proveedor creado correctamente", "ok");
    await loadSuppliers();
  } catch (error) {
    setFeedback(dom.formFeedback, `La API rechazo la entrada: ${error.message}`, "error");
  }
}

async function updateRate(id, ratePerShipment) {
  return apiFetch(`/suppliers/${id}/rate`, {
    method: "PATCH",
    body: JSON.stringify({ rate_per_shipment: ratePerShipment }),
  });
}

async function updateStatus(id, status) {
  return apiFetch(`/suppliers/${id}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

async function handleTableAction(event) {
  const button = event.target.closest("button");
  if (!button) return;

  const row = event.target.closest("tr[data-id]");
  if (!row) return;

  const id = Number(row.dataset.id);

  if (button.dataset.role === "rate-save") {
    const input = row.querySelector('input[data-role="rate-input"]');
    const value = Number(input.value);
    if (!Number.isFinite(value) || value <= 0) {
      setFeedback(dom.listFeedback, "La tarifa debe ser mayor a cero", "error");
      return;
    }

    try {
      await updateRate(id, value);
      setFeedback(dom.listFeedback, "Tarifa actualizada", "ok");
      await loadSuppliers();
    } catch (error) {
      setFeedback(dom.listFeedback, `No se pudo actualizar la tarifa: ${error.message}`, "error");
    }
  }

  if (button.dataset.role === "toggle-status") {
    const nextStatus = button.dataset.nextStatus;
    try {
      await updateStatus(id, nextStatus);
      setFeedback(dom.listFeedback, "Estado actualizado", "ok");
      await loadSuppliers();
    } catch (error) {
      setFeedback(dom.listFeedback, `No se pudo cambiar el estado: ${error.message}`, "error");
    }
  }
}

function syncCurrencyByCountry() {
  const country = dom.country.value;
  if (country === "USA") dom.currency.value = "USD";
  if (country === "Spain") dom.currency.value = "EUR";
}

function init() {
  const savedApiBase = localStorage.getItem(API_STORAGE_KEY);
  dom.apiBase.value = savedApiBase || defaultApiBase;

  populateCategorySelects();
  dom.country.addEventListener("change", syncCurrencyByCountry);

  dom.countryFilter.addEventListener("change", loadSuppliers);
  dom.categoryFilter.addEventListener("change", loadSuppliers);
  dom.reloadBtn.addEventListener("click", () => {
    localStorage.setItem(API_STORAGE_KEY, dom.apiBase.value.trim());
    loadSuppliers();
  });

  dom.form.addEventListener("submit", handleCreateSupplier);
  dom.suppliersBody.addEventListener("click", handleTableAction);

  loadSuppliers();
}

init();