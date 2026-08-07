import type {
  Carrier,
  Product,
  ProductCategory,
  ProductStatus,
  Shipment,
  ShipmentStatus,
  WarehouseLocation,
} from "../types/models";

export interface ProductFilterCriteria {
  warehouse?: WarehouseLocation;
  category?: ProductCategory;
  status?: ProductStatus;
  minUnitCostUSD?: number;
  maxUnitCostUSD?: number;
}

export type ProductSortableField =
  | "name"
  | "stockQuantity"
  | "unitCostUSD"
  | "weightKg"
  | "minStockThreshold";

export interface ProductSortFieldRule {
  field: ProductSortableField;
  order: "asc" | "desc";
}

export function filterProductsByWarehouse(
  products: Product[],
  warehouse: WarehouseLocation,
): Product[] {
  return products.filter((product) => product.warehouse === warehouse);
}

export function filterProductsByCategory(
  products: Product[],
  category: ProductCategory,
): Product[] {
  return products.filter((product) => product.category === category);
}

export function filterProducts(
  products: Product[],
  criteria: ProductFilterCriteria,
): Product[] {
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

    if (
      criteria.minUnitCostUSD !== undefined &&
      product.unitCostUSD < criteria.minUnitCostUSD
    ) {
      return false;
    }

    if (
      criteria.maxUnitCostUSD !== undefined &&
      product.unitCostUSD > criteria.maxUnitCostUSD
    ) {
      return false;
    }

    return true;
  });
}

export function filterShipmentsByStatus(
  shipments: Shipment[],
  status: ShipmentStatus,
): Shipment[] {
  return shipments.filter((shipment) => shipment.status === status);
}

export function filterLowStockProducts(products: Product[]): Product[] {
  return products.filter(
    (product) => product.stockQuantity <= product.minStockThreshold,
  );
}

export function sortProductsByStock(
  products: Product[],
  order: "asc" | "desc",
): Product[] {
  return [...products].sort((leftProduct, rightProduct) => {
    return order === "asc"
      ? leftProduct.stockQuantity - rightProduct.stockQuantity
      : rightProduct.stockQuantity - leftProduct.stockQuantity;
  });
}

export function sortCarriersByReliability(
  carriers: Carrier[],
  order: "asc" | "desc",
): Carrier[] {
  return [...carriers].sort((leftCarrier, rightCarrier) => {
    return order === "asc"
      ? leftCarrier.onTimeRate - rightCarrier.onTimeRate
      : rightCarrier.onTimeRate - leftCarrier.onTimeRate;
  });
}

export function sortProductsByFields(
  products: Product[],
  rules: ProductSortFieldRule[],
): Product[] {
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
