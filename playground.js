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
} from "./dist/index.js";

const sampleProducts = [
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

const sampleCarriers = [
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

const sampleShipments = [
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

function render(title, data) {
  output.textContent = `${title}\n${JSON.stringify(data, null, 2)}`;
}

function value(id) {
  const element = document.getElementById(id);
  return element.value;
}

document.getElementById("btnFilter").addEventListener("click", () => {
  const category = value("category");
  const status = value("status");
  const filtered = filterProducts(sampleProducts, {
    category: category || undefined,
    status: status || undefined,
  });
  render("Filtro por categoria/estado", filtered);
});

document.getElementById("btnSearch").addEventListener("click", () => {
  const sku = value("sku");
  const found = findProductBySKU(sampleProducts, sku);
  render("Busqueda lineal por SKU", found);
});

document.getElementById("btnBinary").addEventListener("click", () => {
  const target = Number(value("weight"));
  const sorted = [...sampleProducts].sort((a, b) => a.weightKg - b.weightKg);
  const index = binarySearchProductByWeight(sorted, target);
  render("Busqueda binaria por peso", { targetWeight: target, index, sorted });
});

document.getElementById("btnSort").addEventListener("click", () => {
  const field = value("sortField");
  const order = value("sortOrder");
  const sorted = sortProductsByFields(sampleProducts, [
    { field, order },
    { field: "name", order: "asc" },
  ]);
  render("Ordenamiento multi-campo", sorted);
});

document.getElementById("btnReports").addEventListener("click", () => {
  const reports = {
    byCategory: countProductsByCategory(sampleProducts),
    totalInventoryValue: calculateTotalInventoryValue(sampleProducts),
    averageDistance: calculateAverageShipmentDistance(sampleShipments),
    maxDistance: calculateMaxShipmentDistance(sampleShipments),
    minDistance: calculateMinShipmentDistance(sampleShipments),
    maxUnitCost: calculateMaxInventoryUnitCost(sampleProducts),
    minUnitCost: calculateMinInventoryUnitCost(sampleProducts),
    topCarriers: findTopCarriers(sampleShipments, 3),
  };
  render("Reportes agregados", reports);
});

document.getElementById("btnCarrier").addEventListener("click", () => {
  const shipment = sampleShipments[0];
  const product = sampleProducts.find((item) => item.sku === shipment.sku);

  if (!product) {
    render("Scoring carriers", { error: "Producto no encontrado para el shipment." });
    return;
  }

  const scoring = sampleCarriers.map((carrier) => ({
    carrier: carrier.name,
    score: scoreCarrierForShipment(carrier, shipment, product),
  }));

  const best = selectBestCarrier(sampleCarriers, shipment, product);
  render("Scoring y seleccion", { scoring, best });
});

document.getElementById("btnClear").addEventListener("click", () => {
  output.textContent = "";
});

render("Playground listo", { message: "Usa los controles para probar cada funcion." });
