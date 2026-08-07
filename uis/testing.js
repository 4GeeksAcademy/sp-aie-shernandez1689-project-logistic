import {
  binarySearchProductByWeight,
  calculateAverageShipmentDistance,
  calculateMaxInventoryUnitCost,
  calculateMaxShipmentDistance,
  calculateMinInventoryUnitCost,
  calculateMinShipmentDistance,
  calculateTotalInventoryValue,
  countProductsByCategory,
  filterProducts,
  findProductBySKU,
  findTopCarriers,
  scoreCarrierForShipment,
  selectBestCarrier,
  sortProductsByFields,
} from "../dist/index.js";

const products = [
  {
    sku: "SHOE-BLK-42",
    name: "Zapatillas Negras Running - Talla 42",
    category: "Fashion",
    weightKg: 0.8,
    dimensions: { lengthCm: 35, widthCm: 22, heightCm: 12 },
    warehouse: "Los Angeles",
    stockQuantity: 45,
    minStockThreshold: 20,
    unitCostUSD: 35.0,
    isFragile: false,
    status: "Active",
  },
  {
    sku: "LAPTOP-DELL-15",
    name: "Laptop Dell 15 pulgadas",
    category: "Electronics",
    weightKg: 2.3,
    dimensions: { lengthCm: 40, widthCm: 28, heightCm: 3 },
    warehouse: "Zaragoza",
    stockQuantity: 8,
    minStockThreshold: 10,
    unitCostUSD: 650.0,
    isFragile: true,
    status: "Low stock",
  },
  {
    sku: "PERFUME-COCO-50",
    name: "Perfume Coco 50ml",
    category: "Cosmetics",
    weightKg: 0.3,
    dimensions: { lengthCm: 12, widthCm: 8, heightCm: 15 },
    warehouse: "Los Angeles",
    stockQuantity: 120,
    minStockThreshold: 30,
    unitCostUSD: 85.0,
    isFragile: true,
    status: "Active",
  },
  {
    sku: "LAMP-HOME-01",
    name: "Lampara Minimal",
    category: "Home",
    weightKg: 1.6,
    dimensions: { lengthCm: 28, widthCm: 28, heightCm: 42 },
    warehouse: "Zaragoza",
    stockQuantity: 16,
    minStockThreshold: 12,
    unitCostUSD: 50.0,
    isFragile: true,
    status: "Active",
  },
];

const carriers = [
  {
    id: "CAR-UPS",
    name: "UPS",
    operatesIn: ["United States"],
    baseRateUSD: 5.0,
    ratePerKgUSD: 1.2,
    ratePerKmUSD: 0.05,
    avgDeliveryDays: 3,
    onTimeRate: 88,
    maxWeightKg: 30,
    handlesFragile: true,
    acceptsPriority: ["Standard", "Express"],
  },
  {
    id: "CAR-SEUR",
    name: "SEUR",
    operatesIn: ["Spain"],
    baseRateUSD: 6.5,
    ratePerKgUSD: 1.5,
    ratePerKmUSD: 0.08,
    avgDeliveryDays: 2,
    onTimeRate: 92,
    maxWeightKg: 25,
    handlesFragile: true,
    acceptsPriority: ["Standard", "Express", "Same-day"],
  },
  {
    id: "CAR-DHL",
    name: "DHL Express",
    operatesIn: ["United States", "Spain"],
    baseRateUSD: 12.0,
    ratePerKgUSD: 2.0,
    ratePerKmUSD: 0.1,
    avgDeliveryDays: 1,
    onTimeRate: 95,
    maxWeightKg: 50,
    handlesFragile: true,
    acceptsPriority: ["Express", "Same-day"],
  },
];

const shipments = [
  {
    id: "SH-2024-8821",
    sku: "LAPTOP-DELL-15",
    quantity: 1,
    origin: "Zaragoza",
    destination: {
      city: "Madrid",
      country: "Spain",
      postalCode: "28001",
      distanceKm: 320,
    },
    priority: "Express",
    declaredValueUSD: 650.0,
    carrier: "SEUR",
    status: "Assigned",
    createdAt: new Date("2024-03-15"),
  },
  {
    id: "SH-2024-8822",
    sku: "SHOE-BLK-42",
    quantity: 2,
    origin: "Los Angeles",
    destination: {
      city: "Los Angeles",
      country: "United States",
      postalCode: "90001",
      distanceKm: 18,
    },
    priority: "Standard",
    declaredValueUSD: 70.0,
    carrier: "UPS",
    status: "Delivered",
    createdAt: new Date("2024-03-16"),
  },
  {
    id: "SH-2024-8823",
    sku: "PERFUME-COCO-50",
    quantity: 3,
    origin: "Los Angeles",
    destination: {
      city: "San Diego",
      country: "United States",
      postalCode: "92101",
      distanceKm: 194,
    },
    priority: "Same-day",
    declaredValueUSD: 255.0,
    carrier: null,
    status: "Pending",
    createdAt: new Date("2024-03-17"),
  },
];

const output = document.getElementById("output");

function printResult(title, payload) {
  output.textContent = `${title}\n${JSON.stringify(payload, null, 2)}`;
}

function readValue(id) {
  const element = document.getElementById(id);
  return element.value;
}

document.getElementById("runFilter").addEventListener("click", () => {
  const category = readValue("filterCategory");
  const status = readValue("filterStatus");

  const filtered = filterProducts(products, {
    category: category || undefined,
    status: status || undefined,
  });

  printResult("Filtro por criterios", filtered);
});

document.getElementById("runSearch").addEventListener("click", () => {
  const sku = readValue("searchSku");
  const found = findProductBySKU(products, sku);
  printResult("Busqueda lineal por SKU", found);
});

document.getElementById("runBinary").addEventListener("click", () => {
  const target = Number(readValue("searchWeight"));
  const sorted = [...products].sort((a, b) => a.weightKg - b.weightKg);
  const index = binarySearchProductByWeight(sorted, target);
  printResult("Busqueda binaria por weightKg", { targetWeight: target, index, sorted });
});

document.getElementById("runSort").addEventListener("click", () => {
  const field = readValue("sortField");
  const order = readValue("sortOrder");

  const sorted = sortProductsByFields(products, [
    { field, order },
    { field: "name", order: "asc" },
  ]);

  printResult("Ordenamiento multi-campo", sorted);
});

document.getElementById("runReports").addEventListener("click", () => {
  const reports = {
    byCategory: countProductsByCategory(products),
    totalInventoryValue: calculateTotalInventoryValue(products),
    avgDistance: calculateAverageShipmentDistance(shipments),
    maxDistance: calculateMaxShipmentDistance(shipments),
    minDistance: calculateMinShipmentDistance(shipments),
    maxUnitCost: calculateMaxInventoryUnitCost(products),
    minUnitCost: calculateMinInventoryUnitCost(products),
    topCarriers: findTopCarriers(shipments, 3),
  };

  printResult("Reportes agregados", reports);
});

document.getElementById("runCarrier").addEventListener("click", () => {
  const shipment = shipments[0];
  const product = products.find((p) => p.sku === shipment.sku) || null;

  if (!product) {
    printResult("Seleccion de carrier", { error: "No existe producto para el shipment." });
    return;
  }

  const scoring = carriers.map((carrier) => ({
    carrier: carrier.name,
    score: scoreCarrierForShipment(carrier, shipment, product),
  }));

  const best = selectBestCarrier(carriers, shipment, product);
  printResult("Scoring y seleccion de carrier", { scoring, best });
});

document.getElementById("clearOutput").addEventListener("click", () => {
  output.textContent = "";
});

printResult("Data Lab listo", {
  message: "Usa los controles para ejecutar operaciones manuales.",
});
