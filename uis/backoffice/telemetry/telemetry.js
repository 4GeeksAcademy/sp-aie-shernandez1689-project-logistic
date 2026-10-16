/**
 * Dashboard técnico de telemetría — uis/backoffice/telemetry.
 *
 * Consume GET /telemetry/report (endpoint de Fase 2) y visualiza las mismas
 * métricas operacionales del pipeline, nada de negocio.
 *
 * Reglas de la vista:
 * - Es una vista técnica para el equipo de ingeniería.
 * - Muestra el "period" (from/to) que cubren los números.
 * - Si el endpoint devuelve una lista vacía en una métrica, muestra el
 *   estado vacío en esa tabla.
 */

const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const API_STORAGE_KEY = "trackflow_api_base";
const TOKEN_STORAGE_KEY = "trackflow_access_token";

const dom = {
  startDate: document.getElementById("startDate"),
  endDate: document.getElementById("endDate"),
  apiBase: document.getElementById("apiBase"),
  reloadBtn: document.getElementById("reloadBtn"),
  controlsFeedback: document.getElementById("controlsFeedback"),
  periodSection: document.getElementById("periodSection"),
  periodFrom: document.getElementById("periodFrom"),
  periodTo: document.getElementById("periodTo"),
  tableSummary: document.getElementById("tableSummary"),
  loadingState: document.getElementById("loadingState"),
  errorState: document.getElementById("errorState"),
  errorMessage: document.getElementById("errorMessage"),
  retryButton: document.getElementById("retryButton"),
  metricsContent: document.getElementById("metricsContent"),
  tables: {
    eventsPerDay: document.getElementById("eventsPerDayBody"),
    eventsPerType: document.getElementById("eventsPerTypeBody"),
    errorRateDaily: document.getElementById("errorRateDailyBody"),
    errorRateByType: document.getElementById("errorRateByTypeBody"),
    apiErrors: document.getElementById("apiErrorsBody"),
    authFailure: document.getElementById("authFailureBody"),
  },
};

function getApiBase() {
  const saved = localStorage.getItem(API_STORAGE_KEY) || DEFAULT_API_BASE;
  return saved.trim().replace(/\/$/, "");
}

function requestHeaders() {
  const token = localStorage.getItem(TOKEN_STORAGE_KEY);
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

function setFeedback(node, message, type = "") {
  node.textContent = message;
  node.className = "feedback" + (type ? ` ${type}` : "");
}

function friendlyError(status) {
  if (status === 401) return "Tu sesión expiró. Inicia sesión nuevamente.";
  if (status === 403) return "No tienes permiso para consultar este reporte.";
  if (status === 503) return "La base de datos de telemetría no está disponible.";
  if (status >= 500) return "El servicio no está disponible en este momento. Inténtalo de nuevo más tarde.";
  return "No se pudo cargar el reporte. Inténtalo de nuevo.";
}

function setLoading(isLoading) {
  dom.loadingState.hidden = !isLoading;
  dom.reloadBtn.disabled = isLoading;
  if (isLoading) {
    dom.errorState.hidden = true;
    dom.metricsContent.hidden = true;
  }
}

function showError(status) {
  dom.loadingState.hidden = true;
  dom.metricsContent.hidden = true;
  dom.errorState.hidden = false;
  dom.errorMessage.textContent = friendlyError(status);
}

function fmtRate(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(1)} %`;
}

function fmtNum(value) {
  return Number(value).toLocaleString("es-ES");
}

/** Convierte un datetime-local en ISO 8601 UTC (ej. 2026-10-01T00:00:00Z). */
function toUtcIso(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toISOString();
}

function buildQuery() {
  const params = new URLSearchParams();
  const start = toUtcIso(dom.startDate.value);
  const end = toUtcIso(dom.endDate.value);
  if (start) params.set("start_date", start);
  if (end) params.set("end_date", end);
  const query = params.toString();
  return query ? `/telemetry/report?${query}` : "/telemetry/report";
}

/** Llena una <tbody> a partir de una lista de filas y plantilla de celdas. */
function renderTable(body, rows, cellRenderer) {
  if (!rows || rows.length === 0) {
    const empty = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 99;
    cell.className = "empty-cell";
    cell.textContent = "Sin datos en esta ventana.";
    empty.append(cell);
    body.replaceChildren(empty);
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const cellContent of cellRenderer(row)) {
      const td = document.createElement("td");
      td.textContent = cellContent;
      tr.append(td);
    }
    fragment.append(tr);
  }
  body.replaceChildren(fragment);
}

function renderReport(report) {
  // Período resuelto por el endpoint
  dom.periodFrom.textContent = report.period.from;
  dom.periodTo.textContent = report.period.to;
  dom.periodSection.hidden = false;

  const metrics = report.metrics || {};

  renderTable(dom.tables.eventsPerDay, metrics.events_per_day, (r) => [r.date, fmtNum(r.events)]);

  renderTable(dom.tables.eventsPerType, metrics.events_per_type_per_day, (r) => [
    r.date,
    r.event_type,
    fmtNum(r.events),
  ]);

  renderTable(dom.tables.errorRateDaily, metrics.error_rate_daily, (r) => [
    r.date,
    fmtNum(r.total),
    fmtNum(r.errors),
    fmtRate(r.error_rate),
  ]);

  renderTable(dom.tables.errorRateByType, metrics.error_rate_by_type, (r) => [
    r.event_type,
    fmtNum(r.total),
    fmtNum(r.errors),
    fmtRate(r.error_rate),
  ]);

  renderTable(dom.tables.apiErrors, metrics.api_errors_by_endpoint, (r) => [
    r.endpoint,
    fmtNum(r.errors),
    fmtNum(r.server_errors),
  ]);

  renderTable(dom.tables.authFailure, metrics.auth_failure_rate, (r) => [
    r.date,
    fmtNum(r.attempts),
    fmtNum(r.failures),
    fmtRate(r.failure_rate),
  ]);
}

async function loadReport() {
  setLoading(true);
  setFeedback(dom.controlsFeedback, "Consultando reporte…");

  try {
    const response = await fetch(`${getApiBase()}${buildQuery()}`, {
      headers: requestHeaders(),
    });
    const result = await response.json().catch(() => null);
    if (!response.ok || !result || !result.period || !result.metrics) {
      throw { status: response.status || 500 };
    }
    renderReport(result);
    dom.loadingState.hidden = true;
    dom.metricsContent.hidden = false;
    setFeedback(dom.controlsFeedback, "Reporte actualizado", "ok");
  } catch (error) {
    if (error.name === "AbortError") return;
    setFeedback(dom.controlsFeedback, "No se pudo actualizar el reporte", "error");
    showError(error.status || 0);
  }
}

function init() {
  const saved = localStorage.getItem(API_STORAGE_KEY);
  dom.apiBase.value = saved || DEFAULT_API_BASE;

  dom.reloadBtn.addEventListener("click", () => {
    localStorage.setItem(API_STORAGE_KEY, dom.apiBase.value.trim());
    loadReport();
  });
  dom.retryButton.addEventListener("click", loadReport);

  loadReport();
}

init();