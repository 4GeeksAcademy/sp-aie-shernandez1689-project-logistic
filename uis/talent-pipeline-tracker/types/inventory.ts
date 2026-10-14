export type Warehouse = "LA" | "ZGZ";
export type Category = "fashion" | "electronics" | "cosmetics";
export type ExitType = "dispatch" | "loss";

export interface SKUInput {
  name: string;
  sku: string;
  client_name: string;
  category: Category;
  warehouse: Warehouse;
}

export interface SKU extends SKUInput {
  id: number;
  current_stock: number;
}

export interface StockEntryInput {
  sku_id: number;
  quantity: number;
  reference: string;
  warehouse: Warehouse;
}

export interface StockExitInput {
  sku_id: number;
  quantity: number;
  exit_type: ExitType;
  tracking_number?: string | null;
  warehouse: Warehouse;
}

export interface StockEntry extends StockEntryInput {
  id: number;
  created_at: string;
  user_uuid: string;
}

export interface StockExit extends StockExitInput {
  id: number;
  created_at: string;
  user_uuid: string;
}

export interface StockMovementFields {
  id: number;
  quantity: number;
  warehouse: Warehouse;
  created_at: string;
  user_uuid: string;
  product: Pick<SKU, "name" | "sku">;
}

export type StockMovement =
  | (StockMovementFields & { order_type: "inbound" })
  | (StockMovementFields & { order_type: "outbound"; exit_type: ExitType });

export type ProductInput = SKUInput;
export type Product = SKU;
export type InboundOrderInput = StockEntryInput;
export type OutboundOrderInput = StockExitInput;
export type InboundOrder = StockEntry;
export type OutboundOrder = StockExit;
export type InventoryOrder = StockMovement;
