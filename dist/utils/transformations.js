const CATEGORY_KEYS = [
    "Fashion",
    "Electronics",
    "Cosmetics",
    "Home",
    "Other",
];
const SHIPMENT_STATUS_KEYS = [
    "Pending",
    "Assigned",
    "In transit",
    "Delivered",
    "Failed",
];
function roundToTwoDecimals(value) {
    return Math.round(value * 100) / 100;
}
function getPriorityMultiplier(priority) {
    if (priority === "Express") {
        return 1.3;
    }
    if (priority === "Same-day") {
        return 1.6;
    }
    return 1;
}
export function calculateShippingCost(shipment, product, carrier) {
    const baseRate = carrier.baseRateUSD;
    const weightCost = product.weightKg * carrier.ratePerKgUSD * shipment.quantity;
    const distanceCost = shipment.destination.distanceKm * carrier.ratePerKmUSD;
    const subtotal = baseRate + weightCost + distanceCost;
    const total = subtotal * getPriorityMultiplier(shipment.priority);
    return roundToTwoDecimals(total);
}
export function scoreCarrierForShipment(carrier, shipment, product) {
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
export function selectBestCarrier(carriers, shipment, product) {
    const suitableCarriers = carriers
        .map((carrier) => {
        const score = scoreCarrierForShipment(carrier, shipment, product);
        const cost = calculateShippingCost(shipment, product, carrier);
        return { carrier, score, cost };
    })
        .filter((item) => item.score >= 50);
    if (suitableCarriers.length === 0) {
        return null;
    }
    const best = suitableCarriers.reduce((currentBest, candidate) => {
        if (candidate.cost < currentBest.cost) {
            return candidate;
        }
        if (candidate.cost === currentBest.cost && candidate.score > currentBest.score) {
            return candidate;
        }
        return currentBest;
    });
    return {
        carrier: best.carrier,
        score: roundToTwoDecimals(best.score),
        cost: roundToTwoDecimals(best.cost),
    };
}
export function countProductsByCategory(products) {
    const counts = CATEGORY_KEYS.reduce((accumulator, category) => {
        accumulator[category] = 0;
        return accumulator;
    }, {});
    for (const product of products) {
        counts[product.category] += 1;
    }
    return counts;
}
export function calculateTotalInventoryValue(products) {
    const total = products.reduce((sum, product) => {
        return sum + product.stockQuantity * product.unitCostUSD;
    }, 0);
    return roundToTwoDecimals(total);
}
export function calculateAverageShipmentDistance(shipments) {
    if (shipments.length === 0) {
        return 0;
    }
    const totalDistance = shipments.reduce((sum, shipment) => {
        return sum + shipment.destination.distanceKm;
    }, 0);
    return roundToTwoDecimals(totalDistance / shipments.length);
}
export function calculateMaxShipmentDistance(shipments) {
    if (shipments.length === 0) {
        return 0;
    }
    let maxDistance = -Infinity;
    for (const shipment of shipments) {
        maxDistance = Math.max(maxDistance, shipment.destination.distanceKm);
    }
    return roundToTwoDecimals(maxDistance);
}
export function calculateMinShipmentDistance(shipments) {
    if (shipments.length === 0) {
        return 0;
    }
    let minDistance = Infinity;
    for (const shipment of shipments) {
        minDistance = Math.min(minDistance, shipment.destination.distanceKm);
    }
    return roundToTwoDecimals(minDistance);
}
export function groupShipmentsByStatus(shipments) {
    const grouped = SHIPMENT_STATUS_KEYS.reduce((accumulator, status) => {
        accumulator[status] = [];
        return accumulator;
    }, {});
    for (const shipment of shipments) {
        grouped[shipment.status].push(shipment);
    }
    return grouped;
}
export function findTopCarriers(shipments, topN) {
    if (topN <= 0) {
        return [];
    }
    const usage = new Map();
    for (const shipment of shipments) {
        if (shipment.carrier === null) {
            continue;
        }
        usage.set(shipment.carrier, (usage.get(shipment.carrier) ?? 0) + 1);
    }
    return [...usage.entries()]
        .map(([carrier, count]) => ({ carrier, count }))
        .sort((a, b) => {
        if (b.count !== a.count) {
            return b.count - a.count;
        }
        return a.carrier.localeCompare(b.carrier);
    })
        .slice(0, topN);
}
export function calculateMaxInventoryUnitCost(products) {
    if (products.length === 0) {
        return 0;
    }
    let maxCost = -Infinity;
    for (const product of products) {
        maxCost = Math.max(maxCost, product.unitCostUSD);
    }
    return roundToTwoDecimals(maxCost);
}
export function calculateMinInventoryUnitCost(products) {
    if (products.length === 0) {
        return 0;
    }
    let minCost = Infinity;
    for (const product of products) {
        minCost = Math.min(minCost, product.unitCostUSD);
    }
    return roundToTwoDecimals(minCost);
}
