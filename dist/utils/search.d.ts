import type { Product, Shipment } from "../types/domain";
export declare function findProductBySKU(products: Product[], sku: string): Product | null;
export declare function findShipmentById(shipments: Shipment[], id: string): Shipment | null;
export declare function binarySearchProductByWeight(sortedProducts: Product[], targetWeight: number): number;
