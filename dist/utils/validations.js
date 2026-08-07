function isNumberInRange(value, min, max) {
    return Number.isFinite(value) && value >= min && value <= max;
}
export function validateProduct(product) {
    const errors = [];
    if (product.sku.trim().length === 0) {
        errors.push("sku no debe estar vacio");
    }
    if (!isNumberInRange(product.weightKg, Number.EPSILON, 100)) {
        errors.push("weightKg debe ser > 0 y <= 100");
    }
    if (!isNumberInRange(product.dimensions.lengthCm, Number.EPSILON, 200)) {
        errors.push("dimensions.lengthCm debe ser > 0 y <= 200");
    }
    if (!isNumberInRange(product.dimensions.widthCm, Number.EPSILON, 200)) {
        errors.push("dimensions.widthCm debe ser > 0 y <= 200");
    }
    if (!isNumberInRange(product.dimensions.heightCm, Number.EPSILON, 200)) {
        errors.push("dimensions.heightCm debe ser > 0 y <= 200");
    }
    if (!(Number.isFinite(product.stockQuantity) && product.stockQuantity >= 0)) {
        errors.push("stockQuantity debe ser >= 0");
    }
    if (!(Number.isFinite(product.minStockThreshold) && product.minStockThreshold >= 0)) {
        errors.push("minStockThreshold debe ser >= 0");
    }
    if (!(Number.isFinite(product.unitCostUSD) && product.unitCostUSD > 0)) {
        errors.push("unitCostUSD debe ser > 0");
    }
    return {
        valid: errors.length === 0,
        errors,
    };
}
export function validateShipment(shipment) {
    const errors = [];
    if (!(Number.isFinite(shipment.quantity) && shipment.quantity > 0)) {
        errors.push("quantity debe ser > 0");
    }
    if (!(Number.isFinite(shipment.declaredValueUSD) && shipment.declaredValueUSD > 0)) {
        errors.push("declaredValueUSD debe ser > 0");
    }
    if (!(Number.isFinite(shipment.destination.distanceKm) && shipment.destination.distanceKm >= 0)) {
        errors.push("distanceKm debe ser >= 0");
    }
    return {
        valid: errors.length === 0,
        errors,
    };
}
export function validateCarrier(carrier) {
    const errors = [];
    if (!(Number.isFinite(carrier.baseRateUSD) && carrier.baseRateUSD >= 0)) {
        errors.push("baseRateUSD debe ser >= 0");
    }
    if (!(Number.isFinite(carrier.ratePerKgUSD) && carrier.ratePerKgUSD >= 0)) {
        errors.push("ratePerKgUSD debe ser >= 0");
    }
    if (!(Number.isFinite(carrier.ratePerKmUSD) && carrier.ratePerKmUSD >= 0)) {
        errors.push("ratePerKmUSD debe ser >= 0");
    }
    if (!(Number.isFinite(carrier.avgDeliveryDays) && carrier.avgDeliveryDays > 0)) {
        errors.push("avgDeliveryDays debe ser > 0");
    }
    if (!isNumberInRange(carrier.onTimeRate, 0, 100)) {
        errors.push("onTimeRate debe estar entre 0 y 100");
    }
    if (!(Number.isFinite(carrier.maxWeightKg) && carrier.maxWeightKg > 0)) {
        errors.push("maxWeightKg debe ser > 0");
    }
    if (!Array.isArray(carrier.operatesIn) || carrier.operatesIn.length === 0) {
        errors.push("operatesIn debe contener al menos 1 pais");
    }
    return {
        valid: errors.length === 0,
        errors,
    };
}
