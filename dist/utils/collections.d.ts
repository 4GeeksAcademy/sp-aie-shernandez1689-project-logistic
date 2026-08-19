import type { Carrier, Product, ProductCategory, ProductStatus, Shipment, ShipmentStatus, WarehouseLocation } from "../types/domain";
export interface ProductFilterCriteria {
    warehouse?: WarehouseLocation;
    category?: ProductCategory;
    status?: ProductStatus;
    minUnitCostUSD?: number;
    maxUnitCostUSD?: number;
}
export type ProductSortableField = "name" | "stockQuantity" | "unitCostUSD" | "weightKg" | "minStockThreshold";
export interface ProductSortFieldRule {
    field: ProductSortableField;
    order: "asc" | "desc";
}
export declare function filterProductsByWarehouse(products: Product[], warehouse: WarehouseLocation): Product[];
export declare function filterProductsByCategory(products: Product[], category: ProductCategory): Product[];
export declare function filterProducts(products: Product[], criteria: ProductFilterCriteria): Product[];
export declare function filterShipmentsByStatus(shipments: Shipment[], status: ShipmentStatus): Shipment[];
export declare function filterLowStockProducts(products: Product[]): Product[];
export declare function sortProductsByStock(products: Product[], order: "asc" | "desc"): Product[];
export declare function sortCarriersByReliability(carriers: Carrier[], order: "asc" | "desc"): Carrier[];
export declare function sortProductsByFields(products: Product[], rules: ProductSortFieldRule[]): Product[];
