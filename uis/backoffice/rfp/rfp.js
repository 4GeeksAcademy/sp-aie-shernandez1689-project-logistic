/**
 * Intake de RFPs — uis/backoffice/rfp.
 *
 * Vista de recepción de RFPs (Hito 9, Parte 1). Consume el router
 * ``/rfp/*`` del backend existente (services/api):
 *
 * - POST /rfp/upload          → guarda el PDF y crea el ticket en ``analizando``
 *                                (202); el pipeline corre en segundo plano.
 * - GET  /rfp/tickets         → lista tickets de intake (recientes primero).
 * - GET  /rfp/tickets/{id}    → detalle: metadatos + secciones por departamento.
 *
 * Modo ticket: cada PDF seleccionado se sube por separado (un ticket por
 * archivo). La vista consulta ``GET /rfp/tickets/{id}`` cada pocos segundos
 * hasta que el ticket sale de ``analizando`` y refresca la tabla mientras
 * quede alguno en curso.
 *
 * Mismo convenio que telemetry/: API base y token desde localStorage sobre la
 * app de proveedores (sessionStorage compartida).
 */

const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const API_STORAGE_KEY = "trackflow_api_base";
const TOKEN_STORAGE_KEY = "trackflow_access_token";
const STATUS_ANALIZANDO = "analizando";
const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 120000;
const TABLE_REFRESH_MS = 3000;

let tableRefreshTimer = null;

const dom = {
  uploadForm: document.getElementById("uploadForm"),
  fileInput: document.getElementById("fileInput"),
  apiBase: document.getElementById("apiBase"),
  uploadBtn: document.getElementById("uploadBtn"),
  uploadFeedback: document.getElementById("uploadFeedback"),
  resultSummary: document.getElementById("resultSummary"),
  resultDetail: document.getElementById("resultDetail"),
  uploadList: document.getElementById("uploadList"),
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

async function loadTickets({ silent = false } = {}) {
  dom.ticketsError.hidden = true;
  if (!silent) showLoading(dom.ticketsLoading);
  try {
    const response = await fetch(`${getApiBase()}/rfp/tickets`, {
      headers: requestHeaders(),
    });
    const result = await response.json().catch(() => null);
    if (!response.ok || !result) {
      throw { status: response.status || 500 };
    }
    const tickets = result.tickets || [];
    renderTickets(tickets);
    hideLoading(dom.ticketsLoading);
    scheduleTableRefresh(tickets.some((t) => t.status === STATUS_ANALIZANDO));
  } catch (error) {
    hideLoading(dom.ticketsLoading);
    dom.ticketsError.hidden = false;
    dom.ticketsErrorMessage.textContent = friendlyError(error.status || 0);
  }
}

function scheduleTableRefresh(pending) {
  clearTimeout(tableRefreshTimer);
  tableRefreshTimer = pending
    ? setTimeout(() => loadTickets({ silent: true }), TABLE_REFRESH_MS)
    : null;
}

async function fetchTicket(ticketId) {
  const response = await fetch(`${getApiBase()}/rfp/tickets/${encodeURIComponent(ticketId)}`, {
    headers: requestHeaders(),
  });
  const result = await response.json().catch(() => null);
  if (!response.ok || !result) {
    throw { status: response.status || 500 };
  }
  return result;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Consulta el ticket hasta que sale de ``analizando`` (o vence el timeout). */
async function pollTicket(ticketId, onUpdate) {
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const detail = await fetchTicket(ticketId);
    onUpdate(detail);
    if (detail.ticket?.status !== STATUS_ANALIZANDO) return detail;
    await sleep(POLL_INTERVAL_MS);
  }
  throw { timeout: true };
}

function renderUploadRow(row, { filename, ticketId, status, note }) {
  row.replaceChildren();
  const name = document.createElement("strong");
  name.textContent = filename;
  const id = document.createElement("code");
  id.textContent = ticketId || "—";
  row.append(name, id);
  if (status) {
    const state = document.createElement("span");
    state.innerHTML = pill(status);
    row.append(state);
  }
  if (status === STATUS_ANALIZANDO) {
    const spinner = document.createElement("span");
    spinner.className = "loading-spinner";
    spinner.setAttribute("aria-hidden", "true");
    row.append(spinner);
  }
  if (note) {
    const p = document.createElement("span");
    p.className = "upload-note";
    p.textContent = note;
    row.append(p);
  }
}

function renderUploadResult(detail) {
  const ticket = detail.ticket || {};
  const metadata = detail.metadata || {};
  dom.resultSummary.textContent = "Último análisis: " + (ticket.ticket_id || "—");

  const container = dom.resultDetail;
  container.querySelectorAll(".rfp-result-grid, .reason-block").forEach((el) => el.remove());

  const grid = document.createElement("dl");
  grid.className = "rfp-result-grid";
  const statusKv = createKv("Estado", "");
  statusKv.querySelector("dd").innerHTML = pill(ticket.status);
  grid.append(
    createKv("Ticket", ticket.ticket_id || "—"),
    statusKv,
    createKv("Tipo", metadata.doc_type || "—"),
    createKv("Cliente", metadata.client_name || "—"),
    createKv("País", metadata.client_country || "—"),
    createKv("Moneda", metadata.currency || "—"),
    createKv("Volumen", fmtNumber(metadata.monthly_volume)),
    createKv("Presupuesto", metadata.budget_range || "—"),
    createKv("Deadline", metadata.deadline || "—")
  );
  container.append(grid);

  if (ticket.reason) {
    const reason = document.createElement("p");
    reason.className = "reason-block";
    const label = document.createElement("strong");
    label.textContent = "Motivo: ";
    const code = document.createElement("code");
    code.textContent = ticket.reason;
    reason.append(label, code);
    container.append(reason);
  }
}

async function uploadOne(file) {
  const row = document.createElement("li");
  row.className = "upload-row";
  dom.uploadList.append(row);
  renderUploadRow(row, { filename: file.name, status: "", note: "Subiendo…" });

  const formData = new FormData();
  formData.append("file", file);
  let ticketId = null;
  try {
    const response = await fetch(`${getApiBase()}/rfp/upload`, {
      method: "POST",
      headers: requestHeaders(),
      body: formData,
    });
    const ticket = await response.json().catch(() => null);
    if (!response.ok || !ticket) {
      throw { status: response.status || 500 };
    }
    ticketId = ticket.ticket_id;
    renderUploadRow(row, { filename: file.name, ticketId, status: ticket.status });
    loadTickets({ silent: true });

    const detail = await pollTicket(ticketId, (d) =>
      renderUploadRow(row, { filename: file.name, ticketId, status: d.ticket?.status })
    );
    renderUploadResult(detail);
    return true;
  } catch (error) {
    const note = error.timeout
      ? "Sigue en análisis; la tabla se actualizará sola."
      : friendlyError(error.status || 0);
    renderUploadRow(row, { filename: file.name, ticketId, status: ticketId ? STATUS_ANALIZANDO : "", note });
    return false;
  }
}

async function submitUpload(event) {
  event.preventDefault();
  const files = Array.from(dom.fileInput.files);
  if (files.length === 0) {
    setFeedback(dom.uploadFeedback, "Selecciona al menos un archivo PDF.", "error");
    return;
  }
  localStorage.setItem(API_STORAGE_KEY, dom.apiBase.value.trim());

  dom.uploadBtn.disabled = true;
  dom.resultDetail.hidden = false;
  setFeedback(dom.uploadFeedback, `Subiendo ${files.length} RFP… cada una crea su ticket.`);

  try {
    const results = await Promise.all(files.map(uploadOne));
    const failed = results.filter((ok) => !ok).length;
    if (failed) {
      setFeedback(dom.uploadFeedback, `${failed} de ${files.length} subidas con incidencias.`, "error");
    } else {
      setFeedback(dom.uploadFeedback, "Análisis terminado.", "ok");
    }
    dom.uploadForm.reset();
    dom.apiBase.value = getApiBase();
    await loadTickets();
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
      li.className = "aspect";
      const head = document.createElement("strong");
      const owner = section.department_owner ? ` · ${section.department_owner}` : "";
      head.textContent = departmentLabel(section.department_id) + owner;
      const aspects = document.createElement("ul");
      for (const aspect of section.key_aspects || []) {
        const item = document.createElement("li");
        item.textContent = aspect;
        aspects.append(item);
      }
      li.append(head, aspects);
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
    const result = await fetchTicket(ticketId);
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
      createKv("Estado", result.ticket?.status || "—"),
      createKv("Tipo", metadata.doc_type || "—"),
      createKv("Cliente", metadata.client_name || "—"),
      createKv("País", metadata.client_country || "—"),
      createKv("Moneda", metadata.currency || "—"),
      createKv("Volumen", fmtNumber(metadata.monthly_volume)),
      createKv("Presupuesto", metadata.budget_range || "—"),
      createKv("Deadline", metadata.deadline || "—")
    );
    container.append(meta);
    container.append(createCostSection(metadata));

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

function fmtScore(value) {
  return value === null || value === undefined ? "n/d" : String(value);
}

/** Conversión MarkItDown + legibilidad + costo anticipado de procesamiento. */
function createCostSection(metadata) {
  const r = metadata.readability || {};
  const box = document.createElement("div");
  box.className = "detail-section";
  const h4 = document.createElement("h4");
  h4.textContent = "Conversión y costo de procesamiento";
  const dl = document.createElement("dl");
  dl.className = "rfp-result-grid";
  dl.append(
    createKv("Costo estimado", metadata.processing_cost || "—"),
    createKv("Tokens estimados", fmtNumber(metadata.estimated_tokens)),
    createKv("Palabras / frases", `${fmtNumber(r.num_words)} / ${fmtNumber(r.num_sentences)}`),
    createKv("Páginas", fmtNumber(metadata.num_pages)),
    createKv("Flesch-Kincaid", fmtScore(r.flesch_kincaid)),
    createKv("Flesch", fmtScore(r.flesch)),
    createKv("Conversor", metadata.converter || "—")
  );
  const note = document.createElement("p");
  note.className = "reason-block";
  note.textContent = "Índices n/d: py-readability-metrics requiere ≥ 100 palabras.";
  box.append(h4, dl);
  if (r.flesch === null || r.flesch === undefined) box.append(note);
  return box;
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
  dom.ticketsRetryButton.addEventListener("click", () => loadTickets());
  dom.detailRetryButton.addEventListener("click", () => {
    dom.detailError.hidden = true;
  });
  loadTickets();
}

init();