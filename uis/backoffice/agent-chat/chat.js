/**
 * Chat en tiempo real con el agente de soporte first_line_cx (WS /agent/ws).
 *
 * Sesión: un flujo ``chat_session`` (``flow_id`` = thread de LangGraph) creado
 * con ``POST /agent/sessions`` y guardado en localStorage, así que recargar la
 * página o reconectar vuelve al mismo hilo.
 *
 * Al (re)conectar el primer evento es ``session_restored``: se repinta todo el
 * historial (incluida una respuesta aún en curso) y solo después se aceptan
 * ``token_chunk``. La respuesta se pinta token a token; escribir otro mensaje
 * mientras el agente responde (o pulsar "Detener") la interrumpe: el parcial
 * queda visible marcado como interrumpido y la nueva respuesta llega como un
 * mensaje nuevo (``generation_interrupted`` → ``generation_started``).
 *
 * Reconexión con backoff progresivo (1 s → 30 s + jitter) salvo cierres
 * definitivos (44xx de auth/sesión, o sesión cancelada por un operador).
 *
 * Control de operador (company-brifing2.md): pause / resume / cancel con
 * ``{agent_id, flow_id}``; el servidor exige rol de ingeniería u operaciones.
 *
 * Mismo convenio que rfp/: API base y token desde localStorage. El token va
 * en la query porque el navegador no permite cabeceras en el handshake WS.
 */

const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const API_STORAGE_KEY = "trackflow_api_base";
const TOKEN_STORAGE_KEY = "trackflow_access_token";
const FLOW_STORAGE_KEY = "trackflow_chat_flow_id";
const AGENT_ID = "first_line_cx";
const BACKOFF_BASE_MS = 1000;
const BACKOFF_MAX_MS = 30000;

// Cierres del servidor que no se arreglan reintentando.
const CLOSE_MESSAGES = {
  4400: "Falta la sesión de chat.",
  4401: "Tu sesión del backoffice no es válida. Vuelve a iniciar sesión.",
  4403: "Esta sesión de chat pertenece a otro usuario.",
  4404: "La sesión de chat ya no existe en el servidor (¿se reinició?).",
  4410: "Un operador cerró esta sesión de chat.",
};
// 4408: el servidor nos cortó por ir lentos; se reconecta y rehidrata al momento.
const CLOSE_LAGGED = 4408;

const dom = {
  flowId: document.getElementById("flowId"),
  status: document.getElementById("chatStatus"),
  log: document.getElementById("chatLog"),
  step: document.getElementById("chatStep"),
  error: document.getElementById("chatError"),
  form: document.getElementById("chatForm"),
  input: document.getElementById("chatInput"),
  sendBtn: document.getElementById("sendBtn"),
  stopBtn: document.getElementById("stopBtn"),
  newSessionBtn: document.getElementById("newSessionBtn"),
  controlFlowId: document.getElementById("controlFlowId"),
  controlButtons: document.querySelectorAll("#controlForm [data-action]"),
};

const state = {
  socket: null,
  flowId: null,
  restored: false, // nada de tokens hasta repintar el historial
  closed: false, // cierre definitivo: no reconectar
  generatingId: null,
  attempt: 0,
  retryTimer: null,
  bubbles: new Map(), // message_id -> <li>
};

const STATUS_LABELS = {
  connecting: "Conectando…",
  online: "En línea",
  paused: "Pausado",
  reconnecting: "Reconectando…",
  closed: "Sesión cerrada",
};

const MESSAGE_BADGES = { interrupted: "Interrumpida", failed: "Error" };
const ACTION_LABELS = { pause: "pausada", resume: "reanudada", cancel: "cancelada" };

function storage(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // almacenamiento bloqueado: la sesión dura lo que dure la pestaña
  }
  return null;
}

function apiBase() {
  return storage(API_STORAGE_KEY) || DEFAULT_API_BASE;
}

function setStatus(key, text) {
  dom.status.dataset.state = key;
  dom.status.textContent = text || STATUS_LABELS[key];
}

function showError(message) {
  dom.error.textContent = message;
  dom.error.hidden = !message;
}

function showStep(node) {
  // generation_step llega al terminar cada nodo del grafo (modo updates).
  dom.step.textContent = node ? `Paso completado: ${node}` : "";
  dom.step.hidden = !node;
}

function refreshControls() {
  const ready = state.restored && !state.closed;
  dom.input.disabled = !ready;
  dom.sendBtn.disabled = !ready;
  dom.sendBtn.textContent = state.generatingId ? "Enviar e interrumpir" : "Enviar";
  dom.stopBtn.hidden = !(ready && state.generatingId);
}

// ── Mensajes ────────────────────────────────────────────────────────────

function renderMessage(message) {
  const item = document.createElement("li");
  item.className = "chat-bubble";
  item.dataset.role = message.role;
  item.dataset.messageId = message.message_id;
  const text = document.createElement("span");
  text.className = "chat-text";
  text.textContent = message.content; // nunca innerHTML: el texto viene del LLM
  const badge = document.createElement("span");
  badge.className = "chat-badge";
  item.append(text, badge);
  dom.log.append(item);
  state.bubbles.set(message.message_id, item);
  setMessageStatus(item, message.status);
  dom.log.scrollTop = dom.log.scrollHeight;
  return item;
}

function setMessageStatus(item, status) {
  item.dataset.status = status;
  item.querySelector(".chat-badge").textContent = MESSAGE_BADGES[status] || "";
}

function setMessageText(item, content) {
  item.querySelector(".chat-text").textContent = content;
}

function addNote(text) {
  const item = document.createElement("li");
  item.className = "chat-bubble";
  item.dataset.role = "system";
  item.textContent = text;
  dom.log.append(item);
  dom.log.scrollTop = dom.log.scrollHeight;
}

function timeOf(timestamp) {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleTimeString();
}

function finishGeneration(messageId) {
  if (state.generatingId === messageId) state.generatingId = null;
  showStep("");
  refreshControls();
}

// ── Eventos del servidor ────────────────────────────────────────────────

const handlers = {
  session_restored(data) {
    dom.log.replaceChildren();
    state.bubbles.clear();
    for (const message of data.messages) renderMessage(message);
    state.generatingId = data.generation ? data.generation.message_id : null;
    state.restored = true;
    state.attempt = 0;
    setStatus(data.generation && data.generation.paused ? "paused" : "online");
    showError("");
    showStep("");
    refreshControls();
    dom.input.focus();
  },

  message_created(data) {
    renderMessage(data.message);
  },

  generation_started(data) {
    renderMessage({ message_id: data.message_id, role: "assistant", content: "", status: "streaming" });
    state.generatingId = data.message_id;
    setStatus("online");
    refreshControls();
  },

  generation_step(data) {
    if (data.message_id === state.generatingId) showStep(data.node);
  },

  token_chunk(data) {
    const item = state.bubbles.get(data.message_id);
    if (!item || item.dataset.status !== "streaming") return;
    item.querySelector(".chat-text").textContent += data.token;
    dom.log.scrollTop = dom.log.scrollHeight;
  },

  generation_completed(data) {
    const item = state.bubbles.get(data.message_id);
    if (item) {
      // Respuesta final validada (normalmente idéntica a lo ya pintado).
      setMessageText(item, data.content);
      setMessageStatus(item, "complete");
    }
    finishGeneration(data.message_id);
  },

  generation_interrupted(data) {
    const item = state.bubbles.get(data.message_id);
    if (item) {
      setMessageText(item, data.content); // el parcial, tal cual se mostró
      setMessageStatus(item, "interrupted");
    }
    finishGeneration(data.message_id);
  },

  generation_failed(data) {
    const item = state.bubbles.get(data.message_id);
    if (item) setMessageStatus(item, "failed");
    showError(data.message);
    finishGeneration(data.message_id);
  },

  control_applied(data) {
    const own = data.flow_id === state.flowId;
    const target = own ? "Respuesta" : `Sesión ${data.flow_id}`;
    addNote(`${target} ${ACTION_LABELS[data.action] || data.action} por ${data.actor_id} · ${timeOf(data.timestamp)}`);
    if (!own) return;
    if (data.action === "pause") setStatus("paused");
    if (data.action === "resume") setStatus("online");
  },

  chat_session_closed() {
    endSession(CLOSE_MESSAGES[4410]);
  },

  chat_error(data) {
    showError(data.message);
  },
};

// ── Conexión ────────────────────────────────────────────────────────────

async function createSession(token) {
  const resp = await fetch(new URL("/agent/sessions", apiBase()), {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (resp.status === 401) throw new Error(CLOSE_MESSAGES[4401]);
  if (!resp.ok) throw new Error(`No se pudo abrir la sesión de chat (HTTP ${resp.status}).`);
  return (await resp.json()).flow_id;
}

function endSession(message) {
  state.closed = true;
  state.restored = false;
  state.generatingId = null;
  clearTimeout(state.retryTimer);
  for (const item of state.bubbles.values()) {
    if (item.dataset.status === "streaming") setMessageStatus(item, "interrupted");
  }
  setStatus("closed");
  showStep("");
  if (message) showError(message);
  dom.newSessionBtn.hidden = false;
  refreshControls();
}

function scheduleReconnect(immediate) {
  if (state.closed) return;
  clearTimeout(state.retryTimer);
  const exp = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** state.attempt);
  const delay = immediate ? 0 : Math.round(exp * (1 + Math.random() * 0.3));
  state.attempt += 1;
  setStatus("reconnecting", delay ? `Reconectando en ${Math.ceil(delay / 1000)} s…` : undefined);
  state.retryTimer = setTimeout(openSocket, delay);
}

function openSocket() {
  const token = storage(TOKEN_STORAGE_KEY);
  const url = new URL("/agent/ws", apiBase().replace(/^http/, "ws"));
  url.searchParams.set("token", token || "");
  url.searchParams.set("flow_id", state.flowId);

  state.restored = false;
  refreshControls();
  if (state.attempt === 0) setStatus("connecting");
  const previous = state.socket;
  const socket = new WebSocket(url);
  state.socket = socket;
  if (previous && previous.readyState < WebSocket.CLOSING) previous.close();

  socket.addEventListener("message", (event) => {
    let frame;
    try {
      frame = JSON.parse(event.data);
    } catch {
      return;
    }
    if (!state.restored && frame.event !== "session_restored") return;
    const handler = handlers[frame.event];
    if (handler) handler(frame.data || {});
  });

  socket.addEventListener("close", (event) => {
    if (socket !== state.socket) return;
    state.restored = false;
    refreshControls();
    if (state.closed) return;
    if (CLOSE_MESSAGES[event.code]) {
      const ownFlow = storage(FLOW_STORAGE_KEY) === state.flowId;
      if (ownFlow && (event.code === 4403 || event.code === 4404)) storage(FLOW_STORAGE_KEY, null);
      return endSession(CLOSE_MESSAGES[event.code]);
    }
    showError("Se perdió la conexión con el agente; recuperando la conversación…");
    scheduleReconnect(event.code === CLOSE_LAGGED);
  });
}

async function start(forceNew) {
  clearTimeout(state.retryTimer);
  state.closed = false;
  state.attempt = 0;
  state.generatingId = null;
  dom.newSessionBtn.hidden = true;
  dom.log.replaceChildren();
  state.bubbles.clear();
  showError("");

  const token = storage(TOKEN_STORAGE_KEY);
  if (!token) return endSession("Inicia sesión en el backoffice para usar el chat.");
  // ?flow_id= en la URL: un supervisor observa la sesión de otro usuario
  // (solo lectura + control) sin pisar su propia sesión guardada.
  const observed = forceNew ? null : new URLSearchParams(location.search).get("flow_id");
  let flowId = observed || (forceNew ? null : storage(FLOW_STORAGE_KEY));
  if (!flowId) {
    try {
      flowId = await createSession(token);
    } catch (error) {
      return endSession(error.message);
    }
    storage(FLOW_STORAGE_KEY, flowId);
  }
  state.flowId = flowId;
  dom.flowId.textContent = flowId;
  if (!dom.controlFlowId.value || forceNew) dom.controlFlowId.value = flowId;
  openSocket();
}

function send(command, data) {
  if (!state.restored || state.socket.readyState !== WebSocket.OPEN) {
    showError("La conexión con el agente no está abierta.");
    return false;
  }
  state.socket.send(JSON.stringify({ command, data }));
  return true;
}

// ── UI ──────────────────────────────────────────────────────────────────

dom.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = dom.input.value.trim();
  if (!text) return;
  showError("");
  // Si hay una respuesta en curso, el servidor la interrumpe (new_input).
  if (send("send_message", { text })) dom.input.value = "";
});

dom.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    dom.form.requestSubmit();
  }
});

dom.stopBtn.addEventListener("click", () => send("interrupt", {}));

for (const button of dom.controlButtons) {
  button.addEventListener("click", () => {
    const flowId = dom.controlFlowId.value.trim();
    if (!flowId) {
      showError("Indica el flow_id de la sesión a controlar.");
      return;
    }
    showError("");
    send(button.dataset.action, { agent_id: AGENT_ID, flow_id: flowId });
  });
}

dom.newSessionBtn.addEventListener("click", () => start(true));

// Un corte de red puede tardar en cerrar el socket: se cierra ya y se
// reintenta con backoff; al volver la red no se espera al siguiente escalón.
window.addEventListener("offline", () => {
  if (state.socket && state.socket.readyState === WebSocket.OPEN) state.socket.close();
});
window.addEventListener("online", () => {
  if (!state.closed && !state.restored && state.flowId) scheduleReconnect(true);
});

start(false);
