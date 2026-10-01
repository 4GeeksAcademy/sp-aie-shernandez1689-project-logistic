import type { Carrier, Product, Shipment, ValidationResult } from "../types/models";
export declare function validateProduct(product: Product): ValidationResult;
export declare function validateShipment(shipment: Shipment): ValidationResult;
export declare function validateCarrier(carrier: Carrier): ValidationResult;
