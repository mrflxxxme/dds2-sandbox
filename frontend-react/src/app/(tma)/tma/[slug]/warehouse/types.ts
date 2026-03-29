// ─── Warehouse TMA shared types ──────────────────────────────────────────────

export type Tab = 'stock' | 'assembly' | 'logistics' | 'history';

export type TrafficLight = 'red' | 'orange' | 'yellow' | 'green';

// Stock
export interface WarehouseItem {
    name: string;
    total_qty: number;
    in_way_to_client: number;
    in_way_from_client: number;
    articles_count: number;
    yesterday_qty: number;
    change: number;
}

export interface WarehouseData {
    warehouses: WarehouseItem[];
    total_warehouses: number;
    total_qty: number;
    total_in_way_to_client: number;
    total_in_way_from_client: number;
    yesterday_total_qty: number;
    change_total: number;
    last_synced_at: string | null;
}

export interface StockArticle {
    nm_id: number;
    vendor_code: string;
    subject: string;
    brand: string;
    stocks_wb: number;
    days_left: number | null;
    traffic_light: TrafficLight;
    avg_daily: number;
    trend_pct: number | null;
    orders_30d: number;
}

export interface StockAnalyticsData {
    articles: StockArticle[];
    total: number;
}

// Assembly
export interface AssemblyItem {
    id: number;
    barcode: string;
    quantity: number;
    product_name: string;
    brand: string;
    stock_quantity: number;
}

export interface AssemblyRequest {
    id: number;
    number: string;
    status: string;
    warehouse_name: string;
    wb_warehouse_name: string;
    wb_supply_id_wb: string;
    wb_supply_name: string;
    brands: string;
    estimated_ready_date: string | null;
    actual_ready_date: string | null;
    pickup_date: string | null;
    pickup_time_slot: string | null;
    delivery_date: string | null;
    shipped_at: string | null;
    vehicle_info: string | null;
    vehicle_brand: string | null;
    driver_phone: string | null;
    pickup_cost: number | null;
    pallets_count: number;
    pallet_weight_kg: number;
    total_weight_kg: number;
    comment: string | null;
    items: AssemblyItem[];
    created_at: string;
    updated_at: string;
}

export interface AssemblyListResponse {
    items: AssemblyRequest[];
    total: number;
}

export interface AssignVehicleData {
    vehicle_info: string;
    vehicle_brand: string;
    driver_phone: string;
    pickup_date: string;
    pickup_time_slot: string;
    pickup_cost: string;
    delivery_date: string;
}

// Logistics
export interface NeedArticle {
    nm_id: number;
    vendor_code: string;
    brand: string;
    subject: string;
    total_need: number;
    stocks_wb: number;
    in_assembly: number;
    in_transit: number;
    can_send: number;
    deficit: number;
}

export interface NeedSummary {
    total_need: number;
    total_can_send: number;
    total_deficit: number;
    deficit_count: number;
    can_send_count: number;
    avg_delivery_days: number;
}

export interface StockNeedData {
    articles: NeedArticle[];
    summary: NeedSummary;
    total_articles: number;
}
