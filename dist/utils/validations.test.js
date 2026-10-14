import { validateCarrier, validateProduct, validateShipment } from "./validations";
describe("validateProduct", () => {
    const validProduct = {
        sku: "SKU-001",
        name: "Laptop",
        category: "Electronics",
        weightKg: 2,
        dimensions: { lengthCm: 30, widthCm: 20, heightCm: 5 },
        warehouse: "Zaragoza",
        stockQuantity: 0,
        minStockThreshold: 0,
        unitCostUSD: 750,
        isFragile: true,
        status: "Active",
    };
    it("accepts a valid product including zero stock", () => {
        expect(validateProduct(validProduct)).toEqual({ valid: true, errors: [] });
    });
    it("rejects empty SKU and values outside allowed numeric ranges", () => {
        const result = validateProduct({
            ...validProduct,
            sku: "  ",
            weightKg: 0,
            stockQuantity: -1,
        });
        expect(result.valid).toBe(false);
        expect(result.errors).toContain("sku no debe estar vacio");
        expect(result.errors).toContain("weightKg debe ser > 0 y <= 100");
        expect(result.errors).toContain("stockQuantity debe ser >= 0");
    });
});
describe("validateShipment", () => {
    const validShipment = {
        id: "SHP-001",
        sku: "SKU-001",
        quantity: 1,
        origin: "Zaragoza",
        destination: {
            city: "Los Angeles",
            country: "United States",
            postalCode: "90001",
            distanceKm: 0,
        },
        priority: "Standard",
        declaredValueUSD: 100,
        carrier: null,
        status: "Pending",
        createdAt: new Date("2026-01-01T00:00:00.000Z"),
    };
    it("accepts a valid shipment at zero distance", () => {
        expect(validateShipment(validShipment)).toEqual({ valid: true, errors: [] });
    });
    it("rejects non-positive quantities and declared values", () => {
        const result = validateShipment({
            ...validShipment,
            quantity: 0,
            declaredValueUSD: -1,
        });
        expect(result.valid).toBe(false);
        expect(result.errors).toContain("quantity debe ser > 0");
        expect(result.errors).toContain("declaredValueUSD debe ser > 0");
    });
});
describe("validateCarrier", () => {
    const validCarrier = {
        id: "CAR-001",
        name: "TrackFlow Express",
        operatesIn: ["Spain"],
        baseRateUSD: 0,
        ratePerKgUSD: 0,
        ratePerKmUSD: 0,
        avgDeliveryDays: 1,
        onTimeRate: 100,
        maxWeightKg: 1,
        handlesFragile: true,
        acceptsPriority: ["Standard"],
    };
    it("accepts valid rates and the inclusive 100 percent on-time boundary", () => {
        expect(validateCarrier(validCarrier)).toEqual({ valid: true, errors: [] });
    });
    it("rejects negative rates, out-of-range reliability, and empty coverage", () => {
        const result = validateCarrier({
            ...validCarrier,
            baseRateUSD: -1,
            onTimeRate: 101,
            operatesIn: [],
        });
        expect(result.valid).toBe(false);
        expect(result.errors).toContain("baseRateUSD debe ser >= 0");
        expect(result.errors).toContain("onTimeRate debe estar entre 0 y 100");
        expect(result.errors).toContain("operatesIn debe contener al menos 1 pais");
    });
});
