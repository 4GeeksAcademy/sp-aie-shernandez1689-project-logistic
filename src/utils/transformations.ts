import type {
  Carrier,
  CarrierSelectionResult,
  Product,
  ProductCategory,
  Shipment,
  ShipmentStatus,
} from "../types/models";

const CATEGORY_KEYS: ProductCategory[] = [
  "Fashion",
  "Electronics",
  "Cosmetics",
  "Home",
  "Other",
];

const SHIPMENT_STATUS_KEYS: ShipmentStatus[] = [
  "Pending",
  "Assigned",
  "In transit",
  "Delivered",
  "Failed",
];

function roundToTwoDecimals(value: number): number {
  return Math.round(value * 100) / 100;
}

function getPriorityMultiplier(priority: Shipment["priority"]): number {
  if (priority === "Express") {
    return 1.3;
  }

  if (priority === "Same-day") {
    return 1.6;
  }

  return 1;
}

export function calculateShippingCost(
  shipment: Shipment,
  product: Product,
  carrier: Carrier,
): number {
  const baseRate = carrier.baseRateUSD;
  const weightCost = product.weightKg * carrier.ratePerKgUSD * shipment.quantity;
  const distanceCost = shipment.destination.distanceKm * carrier.ratePerKmUSD;
  const subtotal = baseRate + weightCost + distanceCost;
  const total = subtotal * getPriorityMultiplier(shipment.priority);

  return roundToTwoDecimals(total);
}

export function scoreCarrierForShipment(
  carrier: Carrier,
  shipment: Shipment,
  product: Product,
): number {
  let score = 0;

  if (carrier.operatesIn.includes(shipment.destination.country)) {
    score += 20;
  }

  if (product.weightKg * shipment.quantity <= carrier.maxWeightKg) {
    score += 20;
  }

  if (carrier.acceptsPriority.includes(shipment.priority)) {
    score += 15;
  }

  if (!product.isFragile || carrier.handlesFragile) {
    score += 15;
  }

  score += carrier.onTimeRate * 0.3;

  return roundToTwoDecimals(score);
}

export function selectBestCarrier(
  carriers: Carrier[],
  shipment: Shipment,
  product: Product,
): CarrierSelectionResult | null {
  const suitableCarrierCandidates = carriers
    .map((carrier) => {
      const score = scoreCarrierForShipment(carrier, shipment, product);
      const cost = calculateShippingCost(shipment, product, carrier);

      return { carrier, score, cost };
    })
    .filter((item) => item.score >= 50);

  if (suitableCarrierCandidates.length === 0) {
    return null;
  }

  const bestCandidate = suitableCarrierCandidates.reduce((currentBest, candidate) => {
    if (candidate.cost < currentBest.cost) {
      return candidate;
    }

    if (candidate.cost === currentBest.cost && candidate.score > currentBest.score) {
      return candidate;
    }

    return currentBest;
  });

  return {
    carrier: bestCandidate.carrier,
    score: roundToTwoDecimals(bestCandidate.score),
    cost: roundToTwoDecimals(bestCandidate.cost),
  };
}

export function countProductsByCategory(
  products: Product[],
): Record<ProductCategory, number> {
  const counts = CATEGORY_KEYS.reduce((accumulator, category) => {
    accumulator[category] = 0;
    return accumulator;
  }, {} as Record<ProductCategory, number>);

  for (const product of products) {
    counts[product.category] += 1;
  }

  return counts;
}

export function calculateTotalInventoryValue(products: Product[]): number {
  const total = products.reduce((sum, product) => {
    return sum + product.stockQuantity * product.unitCostUSD;
  }, 0);

  return roundToTwoDecimals(total);
}

export function calculateAverageShipmentDistance(shipments: Shipment[]): number {
  if (shipments.length === 0) {
    return 0;
  }

  const totalDistance = shipments.reduce((sum, shipment) => {
    return sum + shipment.destination.distanceKm;
  }, 0);

  return roundToTwoDecimals(totalDistance / shipments.length);
}

export function calculateMaxShipmentDistance(shipments: Shipment[]): number {
  if (shipments.length === 0) {
    return 0;
  }

  let maxDistance = -Infinity;

  for (const shipment of shipments) {
    maxDistance = Math.max(maxDistance, shipment.destination.distanceKm);
  }

  return roundToTwoDecimals(maxDistance);
}

export function calculateMinShipmentDistance(shipments: Shipment[]): number {
  if (shipments.length === 0) {
    return 0;
  }

  let minDistance = Infinity;

  for (const shipment of shipments) {
    minDistance = Math.min(minDistance, shipment.destination.distanceKm);
  }

  return roundToTwoDecimals(minDistance);
}

export function groupShipmentsByStatus(
  shipments: Shipment[],
): Record<ShipmentStatus, Shipment[]> {
  const grouped = SHIPMENT_STATUS_KEYS.reduce((accumulator, status) => {
    accumulator[status] = [];
    return accumulator;
  }, {} as Record<ShipmentStatus, Shipment[]>);

  for (const shipment of shipments) {
    grouped[shipment.status].push(shipment);
  }

  return grouped;
}

export function findTopCarriers(
  shipments: Shipment[],
  topN: number,
): Array<{ carrier: string; count: number }> {
  if (topN <= 0) {
    return [];
  }

  const usage = new Map<string, number>();

  for (const shipment of shipments) {
    if (shipment.carrier === null) {
      continue;
    }

    usage.set(shipment.carrier, (usage.get(shipment.carrier) ?? 0) + 1);
  }

  return [...usage.entries()]
    .map(([carrier, count]) => ({ carrier, count }))
    .sort((leftCarrierUsage, rightCarrierUsage) => {
      if (rightCarrierUsage.count !== leftCarrierUsage.count) {
        return rightCarrierUsage.count - leftCarrierUsage.count;
      }

      return leftCarrierUsage.carrier.localeCompare(rightCarrierUsage.carrier);
    })
    .slice(0, topN);
}

export function calculateMaxInventoryUnitCost(products: Product[]): number {
  if (products.length === 0) {
    return 0;
  }

  let maxCost = -Infinity;

  for (const product of products) {
    maxCost = Math.max(maxCost, product.unitCostUSD);
  }

  return roundToTwoDecimals(maxCost);
}

export function calculateMinInventoryUnitCost(products: Product[]): number {
  if (products.length === 0) {
    return 0;
  }

  let minCost = Infinity;

  for (const product of products) {
    minCost = Math.min(minCost, product.unitCostUSD);
  }

  return roundToTwoDecimals(minCost);
}
