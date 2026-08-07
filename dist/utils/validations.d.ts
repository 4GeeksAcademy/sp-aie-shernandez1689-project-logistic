import type { Carrier, Product, Shipment, ValidationResult } from "../types/domain";
export declare function validateProduct(product: Product): ValidationResult;
export declare function validateShipment(shipment: Shipment): ValidationResult;
export declare function validateCarrier(carrier: Carrier): ValidationResult;
