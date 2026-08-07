export function findProductBySKU(products, sku) {
    const normalizedSku = sku.trim().toLowerCase();
    for (const product of products) {
        if (product.sku.toLowerCase() === normalizedSku) {
            return product;
        }
    }
    return null;
}
export function findShipmentById(shipments, id) {
    for (const shipment of shipments) {
        if (shipment.id === id) {
            return shipment;
        }
    }
    return null;
}
export function binarySearchProductByWeight(sortedProducts, targetWeight) {
    let left = 0;
    let right = sortedProducts.length - 1;
    while (left <= right) {
        const middle = Math.floor((left + right) / 2);
        const middleProduct = sortedProducts[middle];
        if (middleProduct === undefined) {
            return -1;
        }
        const middleWeight = middleProduct.weightKg;
        if (middleWeight === targetWeight) {
            return middle;
        }
        if (middleWeight < targetWeight) {
            left = middle + 1;
        }
        else {
            right = middle - 1;
        }
    }
    return -1;
}
