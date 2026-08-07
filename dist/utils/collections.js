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
    return [...products].sort((a, b) => {
        return order === "asc"
            ? a.stockQuantity - b.stockQuantity
            : b.stockQuantity - a.stockQuantity;
    });
}
export function sortCarriersByReliability(carriers, order) {
    return [...carriers].sort((a, b) => {
        return order === "asc" ? a.onTimeRate - b.onTimeRate : b.onTimeRate - a.onTimeRate;
    });
}
export function sortProductsByFields(products, rules) {
    if (rules.length === 0) {
        return [...products];
    }
    return [...products].sort((a, b) => {
        for (const rule of rules) {
            const aValue = a[rule.field];
            const bValue = b[rule.field];
            if (aValue === bValue) {
                continue;
            }
            if (typeof aValue === "string" && typeof bValue === "string") {
                const comparison = aValue.localeCompare(bValue);
                return rule.order === "asc" ? comparison : -comparison;
            }
            const comparison = Number(aValue) - Number(bValue);
            return rule.order === "asc" ? comparison : -comparison;
        }
        return 0;
    });
}
