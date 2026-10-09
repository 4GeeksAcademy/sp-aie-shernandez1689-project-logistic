const API_STORAGE_KEY = "trackflow_api_base";
const TOKEN_STORAGE_KEY = "trackflow_access_token";
const DEFAULT_API_BASE = "http://127.0.0.1:8000";

const STATUS_LABELS = {
  open: "Abierta",
  in_progress: "En progreso",
  resolved: "Resuelta",
  discarded: "Descartada",
};
const ORIGIN_LABELS = {
  customer: "Cliente",
  branch: "Sede",
  internal: "Interno",
};
const BRANCH_LABELS = {
  central: "Central",
  la_warehouse: "Los Ángeles — Almacén",
  la_office: "Los Ángeles — Oficina",
  zaragoza_warehouse: "Zaragoza — Almacén",
  zaragoza_office: "Zaragoza — Oficina",
};
const CATEGORY_LABELS = {
  lost_parcel: "Paquete extraviado",
  delivery_failure: "Fallo de entrega",
  inventory_discrepancy: "Discrepancia de inventario",
  carrier_issue: "Problema de carrier",
  returns_issue: "Problema de devoluciones",
  warehouse_incident: "Incidente en almacén",
  system_failure: "Fallo de sistema",
  client_complaint: "Queja de cliente",
  other: "Otro",
};
const ALLOWED_TRANSITIONS = {
  open: ["in_progress", "discarded"],
  in_progress: ["resolved", "discarded"],
  resolved: [],
  discarded: [],
};
const SUMMARY_DIMENSIONS = [
  {
    key: "status",
    targetId: "summaryStatus",
    values: [
      ["open", "Abierta"],
      ["in_progress", "En progreso"],
      ["resolved", "Resuelta"],
      ["discarded", "Descartada"],
    ],
  },
  {
    key: "category",
    targetId: "summaryCategory",
    values: [
      ["lost_parcel", "Paquete extraviado"],
      ["delivery_failure", "Fallo de entrega"],
      ["inventory_discrepancy", "Discrepancia de inventario"],
      ["carrier_issue", "Problema de carrier"],
      ["returns_issue", "Problema de devoluciones"],
      ["warehouse_incident", "Incidente en almacén"],
      ["system_failure", "Fallo de sistema"],
      ["client_complaint", "Queja de cliente"],
      ["other", "Otro"],
    ],
  },
  {
    key: "origin",
    targetId: "summaryOrigin",
    values: [
      ["customer", "Cliente"],
      ["branch", "Sede"],
      ["internal", "Interno"],
    ],
  },
  {
    key: "branch",
    targetId: "summaryBranch",
    values: [
      ["central", "Central"],
      ["la_warehouse", "Los Ángeles — Almacén"],
      ["la_office", "Los Ángeles — Oficina"],
      ["zaragoza_warehouse", "Zaragoza — Almacén"],
      ["zaragoza_office", "Zaragoza — Oficina"],
    ],
  },
];

const dom = {
  summarySection: document.getElementById("summarySection"),
  summaryLoading: document.getElementById("summaryLoading"),
  summaryError: document.getElementById("summaryError"),
  summaryErrorMessage: document.getElementById("summaryErrorMessage"),
  summaryErrorRetryButton: document.getElementById("summaryErrorRetryButton"),
  summaryContent: document.getElementById("summaryContent"),
  summaryTotal: document.getElementById("summaryTotal"),
  summaryEmptyNote: document.getElementById("summaryEmptyNote"),
  statusFilter: document.getElementById("statusFilter"),
  originFilter: document.getElementById("originFilter"),
  branchFilter: document.getElementById("branchFilter"),
  reloadButton: document.getElementById("reloadButton"),
  pageFeedback: document.getElementById("pageFeedback"),
  loadingState: document.getElementById("loadingState"),
  errorState: document.getElementById("errorState"),
  errorMessage: document.getElementById("errorMessage"),
  retryButton: document.getElementById("retryButton"),
  emptyState: document.getElementById("emptyState"),
  emptyTitle: document.getElementById("emptyTitle"),
  emptyMessage: document.getElementById("emptyMessage"),
  clearFiltersButton: document.getElementById("clearFiltersButton"),
  resultsPanel: document.getElementById("resultsPanel"),
  resultsSummary: document.getElementById("resultsSummary"),
  incidentsBody: document.getElementById("incidentsBody"),
};

let incidents = [];
let activeLoad;
let activeSummaryLoad;

function getApiBase() {
  const saved = localStorage.getItem(API_STORAGE_KEY) || DEFAULT_API_BASE;
  return saved.trim().replace(/\/$/, "");
}

function setPageFeedback(message, type = "") {
  dom.pageFeedback.textContent = message;
  dom.pageFeedback.className = `feedback${type ? ` ${type}` : ""}`;
}

function friendlyError(status) {
  if (status === 401) return "Tu sesión expiró. Inicia sesión nuevamente.";
  if (status === 403) return "No tienes permiso para realizar esta acción.";
  if (status >= 500) return "El servicio no está disponible en este momento. Inténtalo de nuevo más tarde.";
  return "No se pudo completar la solicitud. Inténtalo de nuevo.";
}

function friendlySummaryError(status) {
  if (status === 401) return "Tu sesión expiró. Inicia sesión nuevamente.";
  if (status === 403) return "No tienes permiso para consultar el resumen.";
  if (status >= 500) return "El resumen no está disponible ahora. Puedes reintentarlo.";
  return "No se pudo cargar el resumen. Comprueba la conexión e inténtalo de nuevo.";
}

function requestHeaders() {
  const token = localStorage.getItem(TOKEN_STORAGE_KEY);
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

function setSummaryState(state) {
  dom.summaryLoading.hidden = state !== "loading";
  dom.summaryError.hidden = state !== "error";
  dom.summaryContent.hidden = state !== "content";
  dom.summarySection.setAttribute("aria-busy", String(state === "loading"));
}

function renderSummary(summary) {
  dom.summaryTotal.textContent = summary.total.toLocaleString("es-ES");
  dom.summaryEmptyNote.hidden = summary.total !== 0;

  for (const dimension of SUMMARY_DIMENSIONS) {
    const target = document.getElementById(dimension.targetId);
    const rows = [];
    for (const [value, label] of dimension.values) {
      const term = document.createElement("dt");
      term.textContent = `${label} · ${value}`;
      const count = document.createElement("dd");
      count.textContent = summary[dimension.key][value].toLocaleString("es-ES");
      rows.push(term, count);
    }
    target.replaceChildren(...rows);
  }

  setSummaryState("content");
}

function isValidSummary(summary) {
  if (!summary || !Number.isInteger(summary.total) || summary.total < 0) return false;
  return SUMMARY_DIMENSIONS.every((dimension) => {
    const totals = summary[dimension.key];
    return totals && dimension.values.every(([value]) => Number.isInteger(totals[value]) && totals[value] >= 0);
  });
}

async function loadSummary() {
  activeSummaryLoad?.abort();
  const controller = new AbortController();
  activeSummaryLoad = controller;
  setSummaryState("loading");

  try {
    const response = await fetch(`${getApiBase()}/api/incidents/summary`, {
      headers: requestHeaders(),
      signal: controller.signal,
    });
    const result = await response.json().catch(() => null);
    if (!response.ok || !isValidSummary(result)) {
      throw { status: response.status || 500 };
    }
    renderSummary(result);
  } catch (error) {
    if (error.name === "AbortError") return;
    dom.summaryErrorMessage.textContent = friendlySummaryError(error.status || 0);
    setSummaryState("error");
  } finally {
    if (activeSummaryLoad === controller) activeSummaryLoad = null;
  }
}

function selectedFilters() {
  return {
    status: dom.statusFilter.value,
    origin: dom.originFilter.value,
    branch: dom.branchFilter.value,
  };
}

function hasFilters(filters = selectedFilters()) {
  return Object.values(filters).some(Boolean);
}

function setLoading(isLoading) {
  dom.loadingState.hidden = !isLoading;
  dom.reloadButton.disabled = isLoading;
  if (isLoading) {
    dom.errorState.hidden = true;
    dom.emptyState.hidden = true;
    dom.resultsPanel.hidden = true;
  }
}

function showError(status) {
  dom.loadingState.hidden = true;
  dom.resultsPanel.hidden = true;
  dom.emptyState.hidden = true;
  dom.errorState.hidden = false;
  dom.errorMessage.textContent = friendlyError(status);
}

function formatTimestamp(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("es-ES", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function makeCell(content, className = "") {
  const cell = document.createElement("td");
  if (className) cell.className = className;
  if (content instanceof Node) cell.append(content);
  else cell.textContent = content;
  return cell;
}

function createStatusBadge(status) {
  const badge = document.createElement("span");
  badge.className = `status-badge status-${status}`;
  badge.textContent = STATUS_LABELS[status] || "Estado desconocido";
  return badge;
}

function createStateEditor(incident) {
  const wrapper = document.createElement("div");
  wrapper.className = "state-editor";
  const options = ALLOWED_TRANSITIONS[incident.status] || [];

  if (!options.length) {
    const finalState = document.createElement("span");
    finalState.className = "state-final";
    finalState.textContent = "Estado final";
    wrapper.append(finalState);
    return wrapper;
  }

  const select = document.createElement("select");
  select.setAttribute("aria-label", `Cambiar estado de ${incident.title}`);
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Cambiar estado…";
  select.append(placeholder);
  for (const status of options) {
    const option = document.createElement("option");
    option.value = status;
    option.textContent = STATUS_LABELS[status];
    select.append(option);
  }

  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Guardar estado";
  button.setAttribute("aria-label", `Guardar estado de ${incident.title}`);

  const feedback = document.createElement("p");
  feedback.className = "row-feedback";
  feedback.setAttribute("aria-live", "polite");

  button.addEventListener("click", () => updateStatus(incident, select, button, feedback));
  wrapper.append(select, button, feedback);
  return wrapper;
}

function createIncidentRow(incident) {
  const row = document.createElement("tr");
  row.dataset.id = incident.id;

  const incidentInfo = document.createElement("div");
  const title = document.createElement("span");
  title.className = "incident-title";
  title.textContent = incident.title;
  const description = document.createElement("span");
  description.className = "incident-description";
  description.textContent = incident.description;
  incidentInfo.append(title, description);

  row.append(
    makeCell(incidentInfo),
    makeCell(CATEGORY_LABELS[incident.category] || "Otra"),
    makeCell(ORIGIN_LABELS[incident.origin] || "No especificado"),
    makeCell(BRANCH_LABELS[incident.branch] || "No especificada"),
    makeCell(createStatusBadge(incident.status)),
    makeCell(formatTimestamp(incident.created_at)),
    makeCell(createStateEditor(incident)),
  );
  return row;
}

function renderIncidents() {
  const filters = selectedFilters();
  dom.loadingState.hidden = true;
  dom.errorState.hidden = true;

  if (!incidents.length) {
    dom.resultsPanel.hidden = true;
    dom.emptyState.hidden = false;
    const filtered = hasFilters(filters);
    dom.emptyTitle.textContent = filtered ? "No hay resultados para estos filtros" : "Aún no hay incidencias";
    dom.emptyMessage.textContent = filtered
      ? "Cambia los filtros o límpialos para ver otras incidencias."
      : "Cuando se registre una incidencia aparecerá en este listado.";
    dom.clearFiltersButton.hidden = !filtered;
    return;
  }

  dom.emptyState.hidden = true;
  dom.resultsPanel.hidden = false;
  dom.resultsSummary.textContent = `${incidents.length} incidencia${incidents.length === 1 ? "" : "s"}`;
  dom.incidentsBody.replaceChildren(...incidents.map(createIncidentRow));
}

function buildListUrl() {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(selectedFilters())) {
    if (value) params.set(key, value);
  }
  const query = params.toString();
  return `${getApiBase()}/api/incidents${query ? `?${query}` : ""}`;
}

async function loadIncidents() {
  activeLoad?.abort();
  const controller = new AbortController();
  activeLoad = controller;
  setPageFeedback("");
  setLoading(true);

  try {
    const response = await fetch(buildListUrl(), {
      headers: requestHeaders(),
      signal: controller.signal,
    });
    const result = await response.json().catch(() => null);
    if (!response.ok) throw { status: response.status };
    if (!Array.isArray(result)) throw { status: 500 };
    incidents = result;
    renderIncidents();
  } catch (error) {
    if (error.name === "AbortError") return;
    showError(error.status || 0);
  } finally {
    if (activeLoad === controller) {
      activeLoad = null;
      dom.reloadButton.disabled = false;
    }
  }
}

function updateRowFeedback(node, message, isError = false) {
  node.textContent = message;
  node.className = `row-feedback${isError ? " error" : ""}`;
}

async function updateStatus(incident, select, button, feedback) {
  const nextStatus = select.value;
  if (!nextStatus || !ALLOWED_TRANSITIONS[incident.status]?.includes(nextStatus)) return;

  const previousStatus = incident.status;
  const row = button.closest("tr");
  const badge = row.querySelector(".status-badge");
  select.disabled = true;
  button.disabled = true;
  badge.className = `status-badge status-${nextStatus}`;
  badge.textContent = STATUS_LABELS[nextStatus];
  updateRowFeedback(feedback, "Actualizando estado…");

  try {
    const response = await fetch(`${getApiBase()}/api/incidents/${encodeURIComponent(incident.id)}/status`, {
      method: "PATCH",
      headers: requestHeaders(),
      body: JSON.stringify({ status: nextStatus }),
    });
    const result = await response.json().catch(() => null);
    if (!response.ok) throw { status: response.status };

    incident.status = result.status;
    incident.updated_at = result.updated_at;
    if (selectedFilters().status && selectedFilters().status !== incident.status) {
      incidents = incidents.filter((item) => item.id !== incident.id);
    }
    renderIncidents();
    setPageFeedback("Estado actualizado correctamente.", "ok");
  } catch (error) {
    incident.status = previousStatus;
    badge.className = `status-badge status-${previousStatus}`;
    badge.textContent = STATUS_LABELS[previousStatus];
    select.value = "";
    updateRowFeedback(feedback, friendlyError(error.status || 0), true);
  } finally {
    select.disabled = false;
    button.disabled = false;
  }
}

function clearFilters() {
  dom.statusFilter.value = "";
  dom.originFilter.value = "";
  dom.branchFilter.value = "";
  loadIncidents();
}

function init() {
  for (const filter of [dom.statusFilter, dom.originFilter, dom.branchFilter]) {
    filter.addEventListener("change", loadIncidents);
  }
  dom.reloadButton.addEventListener("click", loadIncidents);
  dom.retryButton.addEventListener("click", loadIncidents);
  dom.clearFiltersButton.addEventListener("click", clearFilters);
  dom.summaryErrorRetryButton.addEventListener("click", loadSummary);
  loadSummary();
  loadIncidents();
}

init();