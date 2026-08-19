import { calculateAverageShipmentDistance, calculateTotalInventoryValue, countProductsByCategory, } from "./index";
const sampleProducts = [
    {
        sku: "SHOE-BLK-42",
        name: "Zapatillas Negras Running - Talla 42",
        category: "Fashion",
        weightKg: 0.8,
        dimensions: { lengthCm: 35, widthCm: 22, heightCm: 12 },
        warehouse: "Los Angeles",
        stockQuantity: 45,
        minStockThreshold: 20,
        unitCostUSD: 35,
        isFragile: false,
        status: "Active",
    },
];
const sampleShipments = [
    {
        id: "SH-2024-8821",
        sku: "SHOE-BLK-42",
        quantity: 1,
        origin: "Los Angeles",
        destination: {
            city: "Madrid",
            country: "Spain",
            postalCode: "28001",
            distanceKm: 320,
        },
        priority: "Express",
        declaredValueUSD: 35,
        carrier: null,
        status: "Pending",
        createdAt: new Date("2024-03-15"),
    },
];
console.log("Inventory value:", calculateTotalInventoryValue(sampleProducts));
console.log("Average distance:", calculateAverageShipmentDistance(sampleShipments));
console.log("Count by category:", countProductsByCategory(sampleProducts));
