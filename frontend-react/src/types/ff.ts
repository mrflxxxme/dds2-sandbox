/**
 * Types for the fulfillment-operator portal (route group `(ff)`).
 *
 * Self-contained: these are NOT part of the main `types/api.ts` source of truth,
 * because the FF portal is a separate cross-project app consumed only by the
 * `/api/v1/ff/*` endpoints (no `X-Project-Id` header).
 *
 * NOTE: backend `Numeric`/`Decimal` fields arrive as STRINGS in JSON despite the
 * `string` type here (e.g. `pallet_weight_kg`). Wrap with `Number()` before
 * `formatNumber()`.
 */

export interface FfMe {
    username: string;
    projects: { slug: string; name: string }[];
    warehouses: { project_slug: string; warehouse_id: number; warehouse_name: string }[];
}

export type FfAssemblyStatus =
    | 'PENDING'
    | 'IN_PROGRESS'
    | 'READY'
    | 'VEHICLE_ASSIGNED'
    | 'SHIPPED'
    | 'DELIVERED'
    | 'RETURNED'
    | 'CLOSED'
    | 'CANCELLED';

export interface FfAssemblyItem {
    barcode: string;
    article: string | null;
    product_name: string | null;
    quantity: number;
    box_qty: number | null;
    stock: number;
}

export interface FfAssemblyRow {
    id: number;
    project_slug: string;
    project_name: string;
    warehouse_id: number;
    warehouse_name: string;
    number: string;
    status: FfAssemblyStatus;
    wb_warehouse_name: string | null;
    fbo_supply_number: string | null;
    brands: string | null;
    package_type: string | null;
    pallets_count: number | null;
    /** Decimal — arrives as a string from the API. */
    pallet_weight_kg: string | null;
    estimated_ready_date: string | null;
    actual_ready_date: string | null;
    vehicle_assigned: boolean;
    is_archived: boolean;
    items: FfAssemblyItem[];
    created_at: string;
}

export interface FfAssemblyListResponse {
    items: FfAssemblyRow[];
    total: number;
}

export interface FfStatusEvent {
    status: FfAssemblyStatus;
    changed_at: string;
    comment: string | null;
}

/** Full assembly card for the detail page (`GET /ff/assemblies/{id}`). */
export interface FfAssemblyDetail extends FfAssemblyRow {
    editable: boolean;
    vehicle_info: string | null;
    vehicle_brand: string | null;
    driver_phone: string | null;
    carrier_name: string | null;
    pickup_date: string | null;
    pickup_time_slot: string | null;
    delivery_date: string | null;
    vehicle_assigned_at: string | null;
    via_gazelka: boolean;
    fbo_supply_name: string | null;
    fbo_supply_wb_id: string | null;
    fbo_supply_status: string | null;
    fbo_supply_planned_date: string | null;
    history: FfStatusEvent[];
    ff_review_pending: boolean;
    proposed_items: FfAssemblyItem[];
    ff_proposed_at: string | null;
}

/** Body item for `PUT /ff/assemblies/{id}/items`. Barcode-only (resolved server-side). */
export interface FfAssemblyItemInput {
    barcode: string;
    quantity: number;
}

export type FfAcceptanceKind = 'inbound' | 'return';
export type FfAcceptanceStatus = 'EXPECTED' | 'ACCEPTED';

export interface FfAcceptanceItem {
    item_id: number;
    barcode: string;
    article: string | null;
    product_name: string | null;
    expected_qty: number;
    actual_qty: number;
    defect_qty: number;
}

export interface FfAcceptanceRow {
    id: number;
    project_slug: string;
    project_name: string;
    warehouse_id: number;
    warehouse_name: string;
    number: string;
    kind: FfAcceptanceKind;
    status: FfAcceptanceStatus;
    in_work: boolean;
    assigned_to_me: boolean;
    planned_date: string | null;
    actual_date: string | null;
    comment: string | null;
    is_archived: boolean;
    items: FfAcceptanceItem[];
    created_at: string;
}

export interface FfAcceptanceListResponse {
    items: FfAcceptanceRow[];
    total: number;
}

/** Body for `POST /ff/acceptances/{id}/accept`. */
export interface FfAcceptItemInput {
    item_id: number;
    actual_qty: number;
    defect_qty: number;
    defect_reason?: string;
}

/** Body for `POST /ff/assemblies/{id}/ready`. */
export interface FfReadyInput {
    pallets_count: number;
    pallet_weight_kg: number;
    estimated_ready_date?: string | null;
}

export interface FfStockRow {
    project_slug: string;
    project_name: string;
    warehouse_id: number;
    warehouse_name: string;
    barcode: string;
    article: string | null;
    product_name: string | null;
    quantity: number;
    defect_quantity: number;
    reserved: number;
    available: number;
}

export interface FfStockListResponse {
    items: FfStockRow[];
    total: number;
    total_quantity: number;
}
