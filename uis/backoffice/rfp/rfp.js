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
 * - POST /rfp/tickets/{id}/sections/{dep}/decision → Parte 3: el responsable
 *                                del departamento aprueba, rechaza o pide cambios.
 * - POST /rfp/tickets/{id}/approval → abre la Parte 3 (si no se abrió sola).
 * - GET  /rfp/tickets/{id}/final-document?format=markdown → documento final.
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
// Estados con trabajo en segundo plano (Parte 1 análisis, Parte 2 generación).
const IN_PROGRESS_STATUSES = new Set([STATUS_ANALIZANDO, "generando_borrador", "en_evaluación"]);
const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 180000;
const TABLE_REFRESH_MS = 3000;

let tableRefreshTimer = null;
// Con la Parte 2 encadenada, analisis_completo es un estado de paso.
let autoProposal = true;

// Estados desde los que arranca la Parte 3 (aprobación por departamento).
const APPROVABLE_STATUSES = new Set(["esperando_aprobación", "needs_human_review"]);
// Estados desde los que se puede relanzar la Parte 2.
const REGENERABLE_STATUSES = new Set(["analisis_completo", "needs_human_review"]);
const DECISIONS = [
  ["approve", "Aprobar"],
  ["request_changes", "Solicitar cambios"],
  ["reject", "Rechazar"],
];
const MIN_COMMENT_CHARS = 10;

/** ¿Sigue el ticket con trabajo en segundo plano? */
function isPending(detail) {
  const status = detail.ticket?.status;
  if (IN_PROGRESS_STATUSES.has(status)) return true;
  if (APPROVABLE_STATUSES.has(status) && detail.auto_approval !== false && !detail.approval) return true;
  return status === "analisis_completo" && detail.auto_proposal !== false && !detail.proposal_handoff;
}

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
    scheduleTableRefresh(tickets.some((t) => IN_PROGRESS_STATUSES.has(t.status)
      || (t.status === "analisis_completo" && autoProposal)));
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

/** Consulta el ticket hasta un estado final (o vence el timeout). */
async function pollTicket(ticketId, onUpdate) {
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const detail = await fetchTicket(ticketId);
    autoProposal = detail.auto_proposal !== false;
    onUpdate(detail);
    if (!isPending(detail)) return detail;
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

function createList(items) {
  const ul = document.createElement("ul");
  for (const text of items) {
    const li = document.createElement("li");
    li.textContent = text;
    ul.append(li);
  }
  return ul;
}

function createSubheading(text) {
  const p = document.createElement("p");
  p.className = "subheading";
  p.textContent = text;
  return p;
}

/** Synthesizer: qué pedir a cada responsable y qué preguntar al cliente. */
function createSalesSummary(summary) {
  const box = document.createElement("div");
  box.className = "detail-section sales-summary";
  const h4 = document.createElement("h4");
  h4.textContent = "Resumen para Ventas";
  box.append(h4);
  const provisional = summary.proposal?.provisional_sections || [];
  if (provisional.length) {
    box.append(createProvisionalWarning(
      `Propuesta con ${provisional.length} sección(es) provisional(es): no enviar al cliente sin revisión.`,
      provisional.map((p) => `${departmentLabel(p.department_id)}: revisar con ${p.owner} `
        + `(pendiente: ${(p.pending || []).join(", ") || "evaluación"}; ${p.iterations} iteraciones)`)
    ));
  }
  box.append(createSubheading("Qué pedir a quién"));
  box.append(createList((summary.requests || []).map((r) => `${r.owner} (${departmentLabel(r.department_id)}): ${r.ask}`)));
  const questions = summary.client_questions || [];
  box.append(createSubheading(`Preguntas para el cliente (${questions.length})`));
  box.append(questions.length ? createList(questions) : createSubheading("Ninguna: la RFP trae todo lo necesario."));
  return box;
}

function createProvisionalWarning(title, items) {
  const box = document.createElement("div");
  box.className = "provisional-warning";
  box.setAttribute("role", "alert");
  const strong = document.createElement("strong");
  strong.textContent = title;
  box.append(strong);
  if (items.length) box.append(createList(items));
  return box;
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
      li.append(head, createList(section.key_aspects || []));
      const questions = section.open_questions || [];
      if (questions.length) {
        li.append(createSubheading("Falta en la RFP:"), createList(questions));
      }
      ul.append(li);
    }
    box.append(ul);
  }
  node.append(box);
}

function createHandoff(handoff) {
  const box = document.createElement("div");
  box.className = "detail-section";
  const h4 = document.createElement("h4");
  h4.textContent = `Handoff a la Parte ${handoff.part}`;
  const dl = document.createElement("dl");
  dl.className = "rfp-result-grid";
  dl.append(
    createKv("Siguiente estado", handoff.next_status || "—"),
    createKv("Departamentos", (handoff.departments || []).map(departmentLabel).join(", ") || "—"),
    createKv("Moneda", handoff.currency || "—"),
    createKv("Preguntas pendientes", fmtNumber(handoff.pending_client_questions))
  );
  box.append(h4, dl, createSubheading("Lineamientos para el borrador (§5)"), createList(handoff.guidelines || []));
  return box;
}

function createEvaluation(evaluation) {
  const box = document.createElement("div");
  const dl = document.createElement("dl");
  dl.className = "rfp-result-grid";
  const readability = evaluation.readability || {};
  const relevance = evaluation.relevance || {};
  const compliance = evaluation.compliance || {};
  const verdict = (ok) => (ok ? "pasa" : "falla");
  dl.append(
    createKv("Resultado", evaluation.overall_pass ? "aprobada por los evaluadores" : "no aprobada"),
    createKv("Legibilidad", `${verdict(readability.pass)} · FK ${fmtScore(readability.score)}`),
    createKv("Pertinencia", verdict(relevance.pass)),
    createKv("Cumplimiento", `${verdict(compliance.pass)}${(compliance.rule_ids || []).length ? ` · ${compliance.rule_ids.join(", ")}` : ""}`)
  );
  box.append(dl);
  if ((relevance.missing_aspects || []).length) {
    box.append(createSubheading("Falta para responder la RFP:"), createList(relevance.missing_aspects));
  }
  if ((compliance.violations || []).length) {
    box.append(createSubheading("Incumplimientos:"),
      createList(compliance.violations.map((v) => `[${v.rule_id}] ${v.detail}`)));
  }
  if (evaluation.feedback_for_generator) {
    box.append(createSubheading("Feedback para el generador:"));
    const pre = document.createElement("pre");
    pre.className = "rfp-draft";
    pre.textContent = evaluation.feedback_for_generator;
    box.append(pre);
  }
  return box;
}

function createProposal(sections, ticketId, status) {
  const box = document.createElement("div");
  box.className = "detail-section";
  const h4 = document.createElement("h4");
  h4.textContent = "Propuesta económica (Parte 2)";
  box.append(h4);
  const drafted = sections.filter((s) => s.generation_status);
  if (!drafted.length) {
    box.append(createSubheading("Sin borradores todavía."));
  }
  for (const section of drafted) {
    const item = document.createElement("div");
    item.className = "aspect";
    const head = document.createElement("strong");
    head.textContent = `${departmentLabel(section.department_id)} · ${section.department_owner || ""} · `;
    const badge = document.createElement("span");
    badge.className = `status-pill status-${section.generation_status}`;
    badge.textContent = section.generation_status;
    head.append(badge);
    const iterations = document.createElement("p");
    iterations.textContent = `Iteraciones generador-evaluador: ${section.iterations || 0}`;
    item.append(head);
    if (section.generation_status === "needs_human_review") {
      item.append(createProvisionalWarning(
        `Borrador provisional: agotó el límite sin pasar la evaluación. Revisar con ${section.department_owner || "el responsable"} antes de enviarlo.`,
        []
      ));
    }
    item.append(iterations);
    const evaluations = section.evaluation_results || [];
    if (evaluations.length) item.append(createEvaluation(evaluations[evaluations.length - 1]));
    if (section.draft_content) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "Ver borrador";
      const pre = document.createElement("pre");
      pre.className = "rfp-draft";
      pre.textContent = section.draft_content;
      details.append(summary, pre);
      item.append(details);
    }
    box.append(item);
  }
  // Con la Parte 3 abierta los cambios se piden al responsable, no regenerando.
  if (REGENERABLE_STATUSES.has(status)) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "primary";
    button.id = "regenerateBtn";
    button.textContent = drafted.length ? "Regenerar propuesta" : "Generar propuesta";
    button.addEventListener("click", () => regenerateProposal(ticketId, button));
    box.append(button);
  }
  return box;
}

async function regenerateProposal(ticketId, button) {
  button.disabled = true;
  try {
    const response = await fetch(
      `${getApiBase()}/rfp/tickets/${encodeURIComponent(ticketId)}/proposal`,
      { method: "POST", headers: requestHeaders() }
    );
    if (!response.ok) throw { status: response.status };
    await sleep(POLL_INTERVAL_MS);
    await loadDetail(ticketId);
    loadTickets({ silent: true });
  } catch (error) {
    button.disabled = false;
    dom.detailError.hidden = false;
    dom.detailErrorMessage.textContent = friendlyError(error.status || 0);
  }
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

    if (result.sales_summary) container.append(createSalesSummary(result.sales_summary));
    renderAspects(container, result.departments || [], "Aspectos clave por departamento");
    if (result.handoff) container.append(createHandoff(result.handoff));
    if (result.handoff) {
      container.append(createProposal(result.departments || [], ticketId, result.ticket?.status));
    }
    if (result.approval) {
      container.append(createApproval(result.approval, ticketId));
    } else if (APPROVABLE_STATUSES.has(result.ticket?.status)) {
      container.append(createOpenApproval(ticketId));
    }
    if (result.final_document) container.append(createFinalDocument(result.final_document));
  } catch (error) {
    hideLoading(dom.detailLoading);
    dom.detailError.hidden = false;
    dom.detailErrorMessage.textContent = friendlyError(error.status || 0);
  }
}

// ── Parte 3: aprobación por departamento ────────────────────────────────────

function apiErrorMessage(status, result) {
  const detail = result?.detail;
  if (Array.isArray(detail)) return detail.map((e) => e.msg).join("; ");
  if (typeof detail === "string" && detail) return detail;
  return friendlyError(status);
}

function createApproval(approval, ticketId) {
  const box = document.createElement("div");
  box.className = "detail-section approval-section";
  const h4 = document.createElement("h4");
  h4.textContent = "Aprobación por departamento (Parte 3)";
  box.append(h4);
  box.append(createSubheading(approval.status === "terminado"
    ? "Todas las secciones están aprobadas: documento final generado."
    : "Cada responsable decide su sección de forma independiente; las demás no esperan."));

  const conflicts = approval.conflicts || [];
  if (conflicts.length) {
    box.append(createProvisionalWarning(
      "Conflictos abiertos entre departamentos (BRIEFING §7)",
      conflicts.map((c) => `[${c.trigger}] ${c.detail} Árbitro: ${[...new Set(Object.values(c.arbiters))].join(", ")}.`)
    ));
  }
  const arbitrations = approval.arbitrations || [];
  if (arbitrations.length) {
    box.append(createSubheading(`Arbitrajes aplicados (${approval.arbitration_rounds}/${approval.max_arbitration_rounds} rondas)`));
    box.append(createList(arbitrations.flatMap((a) => a.conflicts.map((c) =>
      `Ronda ${a.round} · ${c.trigger} · árbitro ${[...new Set(Object.values(c.arbiters))].join(", ")} · `
      + `${a.outcome === "reject" ? "rechaza" : "pide cambios en"} ${c.offenders.map(departmentLabel).join(", ")}`))));
  }

  for (const [dep, section] of Object.entries(approval.departments || {})) {
    box.append(createApprovalCard(ticketId, dep, section));
  }
  if ((approval.trace || []).length) box.append(createTrace(approval.trace));
  return box;
}

function createApprovalCard(ticketId, dep, section) {
  const card = document.createElement("div");
  card.className = "aspect approval-card";
  card.dataset.department = dep;
  const head = document.createElement("strong");
  head.textContent = `${departmentLabel(dep)} · ${section.owner} · `;
  const badge = document.createElement("span");
  badge.className = `status-pill status-approval-${section.approval_status}`;
  badge.textContent = section.approval_status.replace("_", " ");
  head.append(badge);
  card.append(head);

  const rounds = document.createElement("p");
  const minutes = section.approval_minutes !== null && section.approval_minutes !== undefined
    ? ` · Tiempo hasta la decisión: ${section.approval_minutes} min` : "";
  rounds.textContent = `Rondas de cambios: ${section.revisions}/${section.max_revisions}${minutes}`;
  card.append(rounds);

  const decisions = section.decisions || [];
  if (decisions.length) {
    card.append(createSubheading("Historial de decisiones"));
    card.append(createList(decisions.map((d) =>
      `${d.decision} · ${d.approver}${d.source === "arbitration" ? " (arbitraje)" : ""}`
      + `${d.comments ? ` — «${d.comments}»` : ""}`)));
  }
  if (section.awaiting) card.append(createDecisionForm(ticketId, dep, section.owner));
  return card;
}

function createDecisionForm(ticketId, dep, owner) {
  const form = document.createElement("form");
  form.className = "decision-form";
  form.setAttribute("aria-label", `Decisión de ${owner} sobre ${departmentLabel(dep)}`);
  const fieldset = document.createElement("fieldset");
  const legend = document.createElement("legend");
  legend.textContent = `Decisión de ${owner} (responsable de ${departmentLabel(dep)})`;
  fieldset.append(legend);
  for (const [value, label] of DECISIONS) {
    const option = document.createElement("label");
    option.className = "decision-option";
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = `decision-${dep}`;
    radio.value = value;
    radio.required = true;
    option.append(radio, ` ${label}`);
    fieldset.append(option);
  }
  const commentsLabel = document.createElement("label");
  commentsLabel.textContent = "Comentarios";
  const comments = document.createElement("textarea");
  comments.name = "comments";
  comments.rows = 3;
  comments.maxLength = 2000;
  comments.placeholder = `Obligatorio al rechazar o pedir cambios (mín. ${MIN_COMMENT_CHARS} caracteres).`;
  commentsLabel.append(comments);
  const button = document.createElement("button");
  button.type = "submit";
  button.className = "primary";
  button.textContent = "Registrar decisión";
  const feedback = document.createElement("p");
  feedback.className = "feedback";
  feedback.setAttribute("role", "status");
  fieldset.append(commentsLabel, button, feedback);
  form.append(fieldset);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const decision = form.querySelector(`input[name="decision-${dep}"]:checked`)?.value;
    const text = comments.value.trim();
    if (!decision) {
      setFeedback(feedback, "Elige una decisión.", "error");
      return;
    }
    if (decision !== "approve" && text.length < MIN_COMMENT_CHARS) {
      setFeedback(feedback, `Explica el motivo (mín. ${MIN_COMMENT_CHARS} caracteres).`, "error");
      return;
    }
    button.disabled = true;
    setFeedback(feedback, decision === "request_changes" ? "Reescribiendo la sección…" : "Registrando…");
    try {
      const response = await fetch(
        `${getApiBase()}/rfp/tickets/${encodeURIComponent(ticketId)}/sections/${encodeURIComponent(dep)}/decision`,
        {
          method: "POST",
          headers: { ...requestHeaders(), "Content-Type": "application/json" },
          body: JSON.stringify({ decision, approver: owner, comments: text || null }),
        }
      );
      const result = await response.json().catch(() => null);
      if (!response.ok) throw { status: response.status, result };
      await loadDetail(ticketId);
      loadTickets({ silent: true });
    } catch (error) {
      button.disabled = false;
      setFeedback(feedback, apiErrorMessage(error.status || 0, error.result), "error");
    }
  });
  return form;
}

function createOpenApproval(ticketId) {
  const box = document.createElement("div");
  box.className = "detail-section";
  const h4 = document.createElement("h4");
  h4.textContent = "Aprobación por departamento (Parte 3)";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "primary";
  button.textContent = "Abrir aprobación";
  const feedback = document.createElement("p");
  feedback.className = "feedback";
  feedback.setAttribute("role", "status");
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const response = await fetch(
        `${getApiBase()}/rfp/tickets/${encodeURIComponent(ticketId)}/approval`,
        { method: "POST", headers: requestHeaders() }
      );
      const result = await response.json().catch(() => null);
      if (!response.ok) throw { status: response.status, result };
      await loadDetail(ticketId);
      loadTickets({ silent: true });
    } catch (error) {
      button.disabled = false;
      setFeedback(feedback, apiErrorMessage(error.status || 0, error.result), "error");
    }
  });
  box.append(h4, button, feedback);
  return box;
}

function createTrace(trace) {
  const details = document.createElement("details");
  details.className = "approval-trace";
  const summary = document.createElement("summary");
  summary.textContent = `Trazabilidad (${trace.length} ejecuciones de nodo)`;
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  const table = document.createElement("table");
  table.innerHTML = "<thead><tr><th>Timestamp</th><th>Nodo</th><th>Agente</th><th>Hilo</th><th>Output</th></tr></thead>";
  const body = document.createElement("tbody");
  for (const entry of trace) {
    const tr = document.createElement("tr");
    for (const value of [entry.timestamp, entry.node, entry.agent, entry.thread_id,
      JSON.stringify(entry.output)]) {
      const td = document.createElement("td");
      td.textContent = value ?? "—";
      tr.append(td);
    }
    body.append(tr);
  }
  table.append(body);
  wrap.append(table);
  details.append(summary, wrap);
  return details;
}

function createFinalDocument(doc) {
  const box = document.createElement("div");
  box.className = "detail-section final-document";
  const h4 = document.createElement("h4");
  h4.textContent = "Documento final";
  const dl = document.createElement("dl");
  dl.className = "rfp-result-grid";
  dl.append(
    createKv("Moneda", doc.currency || "—"),
    createKv("Secciones", (doc.sections || []).map((s) => departmentLabel(s.department_id)).join(", ")),
    createKv("Generado", doc.generated_at || "—")
  );
  const download = document.createElement("button");
  download.type = "button";
  download.textContent = "Descargar Markdown";
  download.addEventListener("click", () => {
    const url = URL.createObjectURL(new Blob([doc.content], { type: "text/markdown" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${doc.ticket_id}-propuesta.md`;
    link.click();
    URL.revokeObjectURL(url);
  });
  const pre = document.createElement("pre");
  pre.className = "rfp-draft";
  pre.textContent = doc.content;
  box.append(h4, dl, download, pre);
  return box;
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