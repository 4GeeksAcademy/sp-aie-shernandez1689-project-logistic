import type { Carrier, CarrierSelectionResult, Product, ProductCategory, Shipment, ShipmentStatus } from "../types/domain";
export declare function calculateShippingCost(shipment: Shipment, product: Product, carrier: Carrier): number;
export declare function scoreCarrierForShipment(carrier: Carrier, shipment: Shipment, product: Product): number;
export declare function selectBestCarrier(carriers: Carrier[], shipment: Shipment, product: Product): CarrierSelectionResult | null;
export declare function countProductsByCategory(products: Product[]): Record<ProductCategory, number>;
export declare function calculateTotalInventoryValue(products: Product[]): number;
export declare function calculateAverageShipmentDistance(shipments: Shipment[]): number;
export declare function calculateMaxShipmentDistance(shipments: Shipment[]): number;
export declare function calculateMinShipmentDistance(shipments: Shipment[]): number;
export declare function groupShipmentsByStatus(shipments: Shipment[]): Record<ShipmentStatus, Shipment[]>;
export declare function findTopCarriers(shipments: Shipment[], topN: number): Array<{
    carrier: string;
    count: number;
}>;
export declare function calculateMaxInventoryUnitCost(products: Product[]): number;
export declare function calculateMinInventoryUnitCost(products: Product[]): number;
