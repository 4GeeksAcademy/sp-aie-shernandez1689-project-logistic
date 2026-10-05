const form = document.getElementById("uploadForm");
const fileInput = document.getElementById("csvFile");
const dropzone = document.getElementById("dropzone");
const selectedFileName = document.getElementById("selectedFileName");
const feedback = document.getElementById("feedback");
const downloadBtn = document.getElementById("downloadBtn");
const resultsPanel = document.getElementById("resultsPanel");
const contextBadge = document.getElementById("contextBadge");

const totalCount = document.getElementById("totalCount");
const validCount = document.getElementById("validCount");
const invalidCount = document.getElementById("invalidCount");
const categoryBreakdown = document.getElementById("categoryBreakdown");
const statusBreakdown = document.getElementById("statusBreakdown");
const countryBreakdown = document.getElementById("countryBreakdown");
const satisfactionSummary = document.getElementById("satisfactionSummary");
const satisfactionBreakdown = document.getElementById("satisfactionBreakdown");
const invalidBreakdown = document.getElementById("invalidBreakdown");

function setFeedback(message, type) {
  feedback.textContent = message;
  feedback.className = "show";
  feedback.classList.add(type);
}

function clearFeedback() {
  feedback.textContent = "";
  feedback.className = "";
}

function renderRows(container, data, suffix = "") {
  container.innerHTML = "";

  Object.entries(data).forEach(([name, value]) => {
    const row = document.createElement("div");
    row.className = "metric-row";

    const label = document.createElement("span");
    label.className = "metric-name";
    label.textContent = name;

    const number = document.createElement("span");
    number.className = "metric-value";
    number.textContent = `${value}${suffix}`;

    row.appendChild(label);
    row.appendChild(number);
    container.appendChild(row);
  });
}

function updateFileLabel(file) {
  selectedFileName.textContent = file ? file.name : "Ningun fichero seleccionado";
}

function onDragState(active) {
  dropzone.classList.toggle("drag-active", active);
}

dropzone.addEventListener("dragover", (event) => {
  event.preventDefault();
  onDragState(true);
});

dropzone.addEventListener("dragleave", () => {
  onDragState(false);
});

dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  onDragState(false);
  const [file] = event.dataTransfer.files;
  if (!file) return;
  fileInput.files = event.dataTransfer.files;
  updateFileLabel(file);
});

fileInput.addEventListener("change", () => {
  updateFileLabel(fileInput.files[0]);
});

downloadBtn.addEventListener("click", () => {
  window.open("/api/incidents/results/export", "_blank", "noopener,noreferrer");
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearFeedback();

  const file = fileInput.files[0];
  if (!file) {
    setFeedback("Debes seleccionar un fichero CSV antes de analizar.", "error");
    return;
  }

  const payload = new FormData();
  payload.append("file", file);

  try {
    const response = await fetch("/api/inbcidents/analyze", {
      method: "POST",
      body: payload,
    });

    const data = await response.json();
    if (!response.ok) {
      setFeedback(data.error || "Error al procesar el fichero.", "error");
      return;
    }

    totalCount.textContent = data.totals.total;
    validCount.textContent = data.totals.valid;
    invalidCount.textContent = data.totals.invalid;

    renderRows(categoryBreakdown, data.breakdown.category);
    renderRows(statusBreakdown, data.breakdown.status);
    renderRows(countryBreakdown, data.breakdown.country);
    renderRows(invalidBreakdown, data.invalid_breakdown);

    const satisfaction = data.satisfaction;
    const avgText =
      satisfaction.average === null
        ? "N/A"
        : `${Number(satisfaction.average).toFixed(2)} / 5.00`;
    satisfactionSummary.textContent = `Incidentes cerrados con puntuacion: ${satisfaction.scored_incidents} de ${satisfaction.closed_incidents}. Promedio: ${avgText}.`;
    renderRows(satisfactionBreakdown, satisfaction.distribution);

    const contextOk = data.context_verification?.all_match === true;
    contextBadge.textContent = contextOk
      ? "Verificacion CONTEXT.incidents.md: coincide exactamente."
      : "Verificacion CONTEXT.incidents.md: diferencias detectadas.";

    resultsPanel.classList.remove("hidden");
    downloadBtn.disabled = false;
    setFeedback("Analisis completado correctamente.", "ok");
  } catch (error) {
    setFeedback(
      "No se pudo conectar con la API. Verifica que el backend este levantado.",
      "error"
    );
  }
});
