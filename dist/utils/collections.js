export function filterProductsByWarehouse(products, warehouse) {
    return products.filter((product) => product.warehouse === warehouse);
}
export function filterProductsByCategory(products, category) {
    return products.filter((product) => product.category === category);
}
export function filterProducts(products, criteria) {
    return products.filter((product) => {
        if (criteria.warehouse !== undefined && product.warehouse !== criteria.warehouse) {
            return false;
        }
        if (criteria.category !== undefined && product.category !== criteria.category) {
            return false;
        }
        if (criteria.status !== undefined && product.status !== criteria.status) {
            return false;
        }
        if (criteria.minUnitCostUSD !== undefined &&
            product.unitCostUSD < criteria.minUnitCostUSD) {
            return false;
        }
        if (criteria.maxUnitCostUSD !== undefined &&
            product.unitCostUSD > criteria.maxUnitCostUSD) {
            return false;
        }
        return true;
    });
}
export function filterShipmentsByStatus(shipments, status) {
    return shipments.filter((shipment) => shipment.status === status);
}
export function filterLowStockProducts(products) {
    return products.filter((product) => product.stockQuantity <= product.minStockThreshold);
}
export function sortProductsByStock(products, order) {
    return [...products].sort((leftProduct, rightProduct) => {
        return order === "asc"
            ? leftProduct.stockQuantity - rightProduct.stockQuantity
            : rightProduct.stockQuantity - leftProduct.stockQuantity;
    });
}
export function sortCarriersByReliability(carriers, order) {
    return [...carriers].sort((leftCarrier, rightCarrier) => {
        return order === "asc"
            ? leftCarrier.onTimeRate - rightCarrier.onTimeRate
            : rightCarrier.onTimeRate - leftCarrier.onTimeRate;
    });
}
export function sortProductsByFields(products, rules) {
    if (rules.length === 0) {
        return [...products];
    }
    return [...products].sort((leftProduct, rightProduct) => {
        for (const rule of rules) {
            const leftValue = leftProduct[rule.field];
            const rightValue = rightProduct[rule.field];
            if (leftValue === rightValue) {
                continue;
            }
            if (typeof leftValue === "string" && typeof rightValue === "string") {
                const comparison = leftValue.localeCompare(rightValue);
                return rule.order === "asc" ? comparison : -comparison;
            }
            const comparison = Number(leftValue) - Number(rightValue);
            return rule.order === "asc" ? comparison : -comparison;
        }
        return 0;
    });
}
