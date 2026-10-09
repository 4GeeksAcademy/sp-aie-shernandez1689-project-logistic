const API_STORAGE_KEY = "trackflow_api_base";
const TOKEN_STORAGE_KEY = "trackflow_access_token";
const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const FIELD_MESSAGES = {
  title: "Escribe un título breve de hasta 120 caracteres.",
  description: "Añade una descripción de la incidencia.",
  category: "Selecciona una categoría válida.",
  status: "Selecciona un estado válido.",
  origin: "Selecciona el origen del reporte.",
  branch: "Selecciona una sede.",
};

const dom = {
  form: document.getElementById("incidentForm"),
  title: document.getElementById("title"),
  description: document.getElementById("description"),
  category: document.getElementById("category"),
  status: document.getElementById("status"),
  origin: document.getElementById("origin"),
  branch: document.getElementById("branch"),
  branchField: document.getElementById("branchField"),
  submitButton: document.getElementById("submitButton"),
  submitLabel: document.getElementById("submitLabel"),
  loadingSpinner: document.getElementById("loadingSpinner"),
  formFeedback: document.getElementById("formFeedback"),
  incidentId: document.getElementById("incidentId"),
  createdAt: document.getElementById("createdAt"),
  updatedAt: document.getElementById("updatedAt"),
};

function setFeedback(message, type = "") {
  dom.formFeedback.textContent = message;
  dom.formFeedback.className = `feedback${type ? ` ${type}` : ""}`;
}

function setFieldError(field, message) {
  const input = dom[field];
  const error = document.getElementById(`${field}Error`);
  if (!input || !error) return false;

  input.setAttribute("aria-invalid", "true");
  error.textContent = message;
  return true;
}

function clearFieldError(field) {
  const input = dom[field];
  const error = document.getElementById(`${field}Error`);
  if (!input || !error) return;

  input.removeAttribute("aria-invalid");
  error.textContent = "";
}

function clearErrors() {
  Object.keys(FIELD_MESSAGES).forEach(clearFieldError);
  setFeedback("");
}

function validateForm() {
  const required = ["title", "description", "category", "status", "origin", "branch"];
  let firstInvalid = null;

  for (const field of required) {
    const value = dom[field].value;
    const invalid = !value.trim() || (field === "title" && value.trim().length > 120);
    if (!invalid) continue;

    setFieldError(field, FIELD_MESSAGES[field]);
    firstInvalid ||= dom[field];
  }

  if (firstInvalid) {
    setFeedback("Revisa los campos marcados antes de continuar.", "error");
    firstInvalid.focus();
    return false;
  }

  return true;
}

function updateBranchEmphasis() {
  dom.branchField.classList.toggle("branch-highlight", dom.origin.value === "branch");
}

function getApiBase() {
  const saved = localStorage.getItem(API_STORAGE_KEY) || DEFAULT_API_BASE;
  return saved.trim().replace(/\/$/, "");
}

function getApiFieldErrors(payload) {
  const detail = payload?.detail;
  if (Array.isArray(payload?.errors)) return payload.errors;
  if (Array.isArray(detail?.errors)) return detail.errors;
  if (detail && typeof detail === "object" && detail.field) return [detail];
  return [];
}

function friendlyRequestError(status) {
  if (status === 401) return "Tu sesión expiró. Inicia sesión nuevamente.";
  if (status === 403) return "No tienes permiso para registrar incidencias.";
  if (status >= 500) return "No pudimos registrar la incidencia. Inténtalo más tarde.";
  return "No se pudo registrar la incidencia. Revisa los datos e inténtalo de nuevo.";
}

function showApiError(status, payload) {
  const issues = getApiFieldErrors(payload);
  let hasFieldError = false;

  for (const issue of issues) {
    const field = issue.field || issue.loc?.at(-1);
    if (FIELD_MESSAGES[field]) {
      hasFieldError = setFieldError(field, FIELD_MESSAGES[field]) || hasFieldError;
    }
  }

  if (hasFieldError) {
    setFeedback("Revisa los campos marcados y vuelve a intentarlo.", "error");
  } else {
    setFeedback(friendlyRequestError(status), "error");
  }
}

function formatTimestamp(value) {
  if (!value) return "No disponible";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "No disponible";
  const formatted = new Intl.DateTimeFormat("es-ES", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(date);
  return `${formatted} UTC`;
}

function showCreatedIncident(incident) {
  dom.incidentId.textContent = incident.id || "No disponible";
  dom.createdAt.textContent = formatTimestamp(incident.created_at);
  dom.updatedAt.textContent = formatTimestamp(incident.updated_at);
}

async function submitIncident(event) {
  event.preventDefault();
  clearErrors();
  if (!validateForm()) return;

  const payload = {
    title: dom.title.value.trim(),
    description: dom.description.value,
    category: dom.category.value,
    status: dom.status.value,
    origin: dom.origin.value,
    branch: dom.branch.value,
  };
  const token = localStorage.getItem(TOKEN_STORAGE_KEY);

  dom.submitButton.disabled = true;
  dom.form.setAttribute("aria-busy", "true");
  dom.loadingSpinner.hidden = false;
  dom.submitLabel.textContent = "Enviando…";
  setFeedback("Registrando incidencia…");

  try {
    const response = await fetch(`${getApiBase()}/api/incidents`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => null);

    if (!response.ok) {
      showApiError(response.status, result);
      return;
    }

    showCreatedIncident(result);
    dom.form.reset();
    updateBranchEmphasis();
    setFeedback("Incidencia registrada correctamente.", "ok");
  } catch {
    setFeedback("No se pudo conectar con el servicio. Comprueba la conexión e inténtalo de nuevo.", "error");
  } finally {
    dom.submitButton.disabled = false;
    dom.form.removeAttribute("aria-busy");
    dom.loadingSpinner.hidden = true;
    dom.submitLabel.textContent = "Registrar incidencia";
  }
}

function init() {
  dom.form.addEventListener("submit", submitIncident);
  dom.origin.addEventListener("change", updateBranchEmphasis);

  for (const field of Object.keys(FIELD_MESSAGES)) {
    dom[field].addEventListener("input", () => clearFieldError(field));
    dom[field].addEventListener("change", () => clearFieldError(field));
  }
}

init();