import type { Product, Shipment } from "../types/models";

export function findProductBySKU(products: Product[], sku: string): Product | null {
  const normalizedSku = sku.trim().toLowerCase();

  for (const product of products) {
    if (product.sku.toLowerCase() === normalizedSku) {
      return product;
    }
  }

  return null;
}

export function findShipmentById(shipments: Shipment[], id: string): Shipment | null {
  for (const shipment of shipments) {
    if (shipment.id === id) {
      return shipment;
    }
  }

  return null;
}

export function binarySearchProductByWeight(
  sortedProducts: Product[],
  targetWeight: number,
): number {
  let leftIndex = 0;
  let rightIndex = sortedProducts.length - 1;

  while (leftIndex <= rightIndex) {
    const middleIndex = Math.floor((leftIndex + rightIndex) / 2);
    const middleProduct = sortedProducts[middleIndex];

    if (middleProduct === undefined) {
      return -1;
    }

    const middleWeight = middleProduct.weightKg;

    if (middleWeight === targetWeight) {
      return middleIndex;
    }

    if (middleWeight < targetWeight) {
      leftIndex = middleIndex + 1;
    } else {
      rightIndex = middleIndex - 1;
    }
  }

  return -1;
}
