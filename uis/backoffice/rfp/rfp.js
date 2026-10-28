/**
 * Intake de RFPs — uis/backoffice/rfp.
 *
 * Vista de recepción de RFPs (Hito 9, Parte 1). Consume el router
 * ``/rfp/*`` del backend existente (services/api):
 *
 * - POST /rfp/upload          → procesa un PDF y devuelve estado del ticket.
 * - GET  /rfp/tickets         → lista tickets de intake (recientes primero).
 * - GET  /rfp/tickets/{id}    → detalle: metadatos + secciones por departamento.
 *
 * Mismo convenio que telemetry/: API base y token desde localStorage sobre la
 * app de proveedores (sessionStorage compartida).
 */

const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const API_STORAGE_KEY = "trackflow_api_base";
const TOKEN_STORAGE_KEY = "trackflow_access_token";

const dom = {
  uploadForm: document.getElementById("uploadForm"),
  fileInput: document.getElementById("fileInput"),
  apiBase: document.getElementById("apiBase"),
  uploadBtn: document.getElementById("uploadBtn"),
  uploadFeedback: document.getElementById("uploadFeedback"),
  resultSummary: document.getElementById("resultSummary"),
  resultDetail: document.getElementById("resultDetail"),
  uploadLoading: document.getElementById("uploadLoading"),
  ticketsBody: document.getElementById("ticketsBody"),
  ticketsLoading: document.getElementById("ticketsLoading"),
  ticketsError: document.getElementById("ticketsError"),
  ticketsErrorMessage: document.getElementById("ticketsErrorMessage"),
  ticketsRetryButton: document.getElementById("ticketsRetryButton"),
  detailLoading: document.getElementById("detailLoading"),
  detailError: document.getElementById("detailError"),
  detailErrorMessage: document.getElementById("detailErrorMessage"),
  detailRetryButton: document.getElementById("detailRetryButton"),
  detailContent: document.getElementById("detailContent"),
};

function getApiBase() {
  const saved = localStorage.getItem(API_STORAGE_KEY) || DEFAULT_API_BASE;
  return saved.trim().replace(/\/$/, "");
}

function requestHeaders() {
  const token = localStorage.getItem(TOKEN_STORAGE_KEY);
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

function setFeedback(node, message, type = "") {
  node.textContent = message;
  node.className = "feedback" + (type ? ` ${type}` : "");
}

function friendlyError(status) {
  if (status === 401) return "Tu sesión expiró. Inicia sesión nuevamente.";
  if (status === 403) return "No tienes permiso para esta operación.";
  if (status === 422) return "El archivo no es un PDF válido o está vacío.";
  if (status === 404) return "El ticket no existe.";
  if (status === 503) return "La base de datos no está disponible.";
  if (status >= 500) return "El servicio no está disponible en este momento. Inténtalo de nuevo más tarde.";
  return "No se pudo completar la operación. Inténtalo de nuevo.";
}

function showLoading(node) {
  node.hidden = false;
}

function hideLoading(node) {
  node.hidden = true;
}

function showError(node, messageNode, message, errorBox) {
  hideLoading(node);
  errorBox.hidden = false;
  messageNode.textContent = message;
}

function pill(status) {
  const cls = `status-pill status-${status || "invalid"}`;
  return `<span class="${cls}">${status || "desconocido"}</span>`;
}

function fmtNumber(value) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  return Number.isNaN(n) ? String(value) : n.toLocaleString("es-ES");
}

function renderEmptyRow(body, text) {
  const tr = document.createElement("tr");
  const td = document.createElement("td");
  td.colSpan = 7;
  td.className = "empty-cell";
  td.textContent = text;
  tr.append(td);
  body.replaceChildren(tr);
}

function departmentLabel(id) {
  const labels = {
    warehouse: "Warehouse",
    lastmile: "Última milla",
    reverse: "Reverse logistics",
  };
  return labels[id] || id;
}

function renderTickets(records) {
  if (!records || records.length === 0) {
    renderEmptyRow(dom.ticketsBody, "Sin tickets de intake todavía.");
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const ticket of records) {
    const tr = document.createElement("tr");
    const ticketCell = document.createElement("td");
    const link = document.createElement("a");
    link.href = "#";
    link.className = "client-row";
    link.textContent = ticket.ticket_id || "—";
    link.dataset.ticketId = ticket.ticket_id;
    link.addEventListener("click", (event) => {
      event.preventDefault();
      loadDetail(link.dataset.ticketId);
    });
    ticketCell.append(link);

    const statusCell = document.createElement("td");
    statusCell.innerHTML = pill(ticket.status);
    const clientCell = document.createElement("td");
    clientCell.textContent = ticket.client_name || "—";
    const countryCell = document.createElement("td");
    countryCell.textContent = ticket.client_country || "—";
    const currencyCell = document.createElement("td");
    currencyCell.textContent = ticket.currency || "—";
    const volumeCell = document.createElement("td");
    volumeCell.textContent = fmtNumber(ticket.monthly_volume);

    const deptCell = document.createElement("td");
    const depts = Array.isArray(ticket.departments_needed)
      ? ticket.departments_needed.map(departmentLabel).join(", ")
      : "—";
    deptCell.textContent = depts;

    tr.append(ticketCell, statusCell, clientCell, countryCell, currencyCell, volumeCell, deptCell);
    fragment.append(tr);
  }
  dom.ticketsBody.replaceChildren(fragment);
}

async function loadTickets() {
  dom.ticketsError.hidden = true;
  showLoading(dom.ticketsLoading);
  try {
    const response = await fetch(`${getApiBase()}/rfp/tickets`, {
      headers: requestHeaders(),
    });
    const result = await response.json().catch(() => null);
    if (!response.ok || !result) {
      throw { status: response.status || 500 };
    }
    renderTickets(result.tickets || []);
    hideLoading(dom.ticketsLoading);
  } catch (error) {
    hideLoading(dom.ticketsLoading);
    dom.ticketsError.hidden = false;
    dom.ticketsErrorMessage.textContent = friendlyError(error.status || 0);
  }
}

function renderUploadResult(result) {
  dom.resultDetail.hidden = false;
  dom.resultSummary.textContent = "Último análisis: " + (result.ticket_id || "—");

  const container = document.getElementById("resultDetail");
  container.querySelectorAll(".rfp-result-grid").forEach((el) => el.remove());
  container.querySelectorAll(".reason-block").forEach((el) => el.remove());

  const grid = document.createElement("dl");
  grid.className = "rfp-result-grid";

  const kv = (dt, dd) => {
    const div = document.createElement("div");
    div.className = "kv";
    const dtEl = document.createElement("dt");
    dtEl.textContent = dt;
    const ddEl = document.createElement("dd");
    ddEl.innerHTML = dd;
    div.append(dtEl, ddEl);
    return div;
  };

  const classification = result.classification || {};
  const metadata = result.metadata || {};
  const ticketId = (result.ticket_id || "").slice(0, 12) + "…";
  grid.append(
    kv("Ticket", `<code>${ticketId}</code>`),
    kv("Estado", pill(result.status)),
    kv("Tipo", classification.doc_type || "—"),
    kv("Cliente", classification.client_name || "—"),
    kv("País", classification.client_country || "—"),
    kv("Moneda", result.currency || "—"),
    kv("Volumen", fmtNumber(metadata.monthly_volume)),
    kv("Presupuesto", metadata.budget_range || "—"),
    kv("Deadline", metadata.deadline || "—")
  );
  container.append(grid);

  if (result.reason) {
    const reason = document.createElement("p");
    reason.className = "reason-block";
    reason.innerHTML = `<strong>Motivo:</strong> <code>${result.reason}</code>`;
    container.append(reason);
  }
}

async function submitUpload(event) {
  event.preventDefault();
  const file = dom.fileInput.files[0];
  if (!file) {
    setFeedback(dom.uploadFeedback, "Selecciona un archivo PDF primero.", "error");
    return;
  }
  localStorage.setItem(API_STORAGE_KEY, dom.apiBase.value.trim());

  const formData = new FormData();
  formData.append("file", file);
  setFeedback(dom.uploadFeedback, "Subiendo y analizando…");
  dom.uploadBtn.disabled = true;
  hideLoading(dom.uploadLoading);
  dom.uploadLoading.hidden = false;
  dom.uploadLoading.querySelector("p").textContent = "Procesando la RFP…";
  dom.resultDetail.hidden = true;
  dom.uploadLoading.hidden = false;
  showLoading(dom.uploadLoading);

  try {
    const response = await fetch(`${getApiBase()}/rfp/upload`, {
      method: "POST",
      headers: requestHeaders(),
      body: formData,
    });
    const result = await response.json().catch(() => null);
    if (!response.ok || !result) {
      throw { status: response.status || 500 };
    }
    hideLoading(dom.uploadLoading);
    setFeedback(dom.uploadFeedback, "RFP procesada correctamente.", "ok");
    renderUploadResult(result);
    await loadTickets();
  } catch (error) {
    hideLoading(dom.uploadLoading);
    setFeedback(dom.uploadFeedback, friendlyError(error.status || 0), "error");
  } finally {
    dom.uploadBtn.disabled = false;
  }
}

function renderAspects(node, sections, title) {
  const box = document.createElement("div");
  box.className = "detail-section";
  const h4 = document.createElement("h4");
  h4.textContent = title;
  box.append(h4);
  if (!sections || sections.length === 0) {
    const p = document.createElement("p");
    p.textContent = "Sin secciones.";
    box.append(p);
  } else {
    const ul = document.createElement("ul");
    for (const section of sections) {
      const li = document.createElement("li");
      const label = section.key_aspects || JSON.stringify(section);
      li.className = "aspect";
      li.textContent = label;
      ul.append(li);
    }
    box.append(ul);
  }
  node.append(box);
}

async function loadDetail(ticketId) {
  dom.detailError.hidden = true;
  dom.detailContent.replaceChildren();
  showLoading(dom.detailLoading);
  try {
    const response = await fetch(`${getApiBase()}/rfp/tickets/${encodeURIComponent(ticketId)}`, {
      headers: requestHeaders(),
    });
    const result = await response.json().catch(() => null);
    if (!response.ok || !result) {
      throw { status: response.status || 500 };
    }
    hideLoading(dom.detailLoading);

    const container = dom.detailContent;
    container.replaceChildren();

    const meta = document.createElement("div");
    meta.className = "detail-section";
    const h = document.createElement("h4");
    h.textContent = `Metadatos · ${ticketId}`;
    meta.append(h);
    const dl = document.createElement("dl");
    dl.className = "rfp-result-grid";
    const metadata = result.metadata || {};
    meta.append(dl);
    dl.append(
      createKv("Tipo", result.ticket?.doc_type || "—"),
      createKv("Cliente", metadata.client_name || "—"),
      createKv("País", metadata.client_country || "—"),
      createKv("Moneda", metadata.currency || "—"),
      createKv("Volumen", fmtNumber(metadata.monthly_volume)),
      createKv("Presupuesto", metadata.budget_range || "—"),
      createKv("Deadline", metadata.deadline || "—")
    );
    container.append(meta);

    const services = Array.isArray(metadata.services_requested)
      ? metadata.services_requested.map(departmentLabel).join(", ")
      : "—";
    container.append(createTextSection("Servicios solicitados", services));
    container.append(createTextSection("Departamentos necesarios",
      (metadata.departments_needed || []).map(departmentLabel).join(", ") || "—"));
    container.append(createTextSection("Motivo", result.ticket?.reason || "—"));

    renderAspects(container, result.departments || [], "Secciones por departamento");
  } catch (error) {
    hideLoading(dom.detailLoading);
    dom.detailError.hidden = false;
    dom.detailErrorMessage.textContent = friendlyError(error.status || 0);
  }
}

function createKv(dt, dd) {
  const div = document.createElement("div");
  div.className = "kv";
  const dtEl = document.createElement("dt");
  dtEl.textContent = dt;
  const ddEl = document.createElement("dd");
  ddEl.textContent = dd;
  div.append(dtEl, ddEl);
  return div;
}

function createTextSection(title, text) {
  const box = document.createElement("div");
  box.className = "detail-section";
  const h4 = document.createElement("h4");
  h4.textContent = title;
  const p = document.createElement("p");
  p.textContent = text || "—";
  box.append(h4, p);
  return box;
}

function init() {
  dom.apiBase.value = localStorage.getItem(API_STORAGE_KEY) || DEFAULT_API_BASE;
  dom.uploadForm.addEventListener("submit", submitUpload);
  dom.ticketsRetryButton.addEventListener("click", loadTickets);
  dom.detailRetryButton.addEventListener("click", () => {
    dom.detailError.hidden = true;
  });
  loadTickets();
}

init();