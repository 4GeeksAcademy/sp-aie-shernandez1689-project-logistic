export type Warehouse = "LA" | "ZGZ";
export type Category = "fashion" | "electronics" | "cosmetics";
export type ExitType = "dispatch" | "loss";

export interface ProductInput {
  name: string;
  sku: string;
  client_name: string;
  category: Category;
  warehouse: Warehouse;
}

export interface Product extends ProductInput {
  id: number;
  current_stock: number;
}

export interface InboundOrderInput {
  sku_id: number;
  quantity: number;
  reference: string;
  warehouse: Warehouse;
}

export interface OutboundOrderInput {
  sku_id: number;
  quantity: number;
  exit_type: ExitType;
  tracking_number?: string | null;
  warehouse: Warehouse;
}

export interface InboundOrder extends InboundOrderInput {
  id: number;
  created_at: string;
  user_uuid: string;
}

export interface OutboundOrder extends OutboundOrderInput {
  id: number;
  created_at: string;
  user_uuid: string;
}

export type InventoryOrder =
  | (InboundOrder & { order_type: "inbound"; product: Product })
  | (OutboundOrder & { order_type: "outbound"; product: Product });
