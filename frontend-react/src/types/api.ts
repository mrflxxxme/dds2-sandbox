/**
 * DDS API Types — TypeScript interfaces for all API responses.
 * Replaces `any` in API client with typed interfaces.
 */

// ─── Auth ────────────────────────────────────────────────────────────────────

export interface TokenResponse {
  access_token: string;
  refresh_token?: string;
  token_type: string;
}

export interface UserProfile {
  id: number;
  username: string;
  email?: string;
  first_name?: string;
  last_name?: string;
  is_active: boolean;
}

// ─── Projects ────────────────────────────────────────────────────────────────

export interface Project {
  id: number;
  name: string;
  slug: string;
  owner_id: number;
  created_at: string;
  tax_rate?: number;
}

export interface ProjectMember {
  id: number;
  user_id: number;
  project_id: number;
  username: string;
  email?: string;
  first_name?: string;
  last_name?: string;
  telegram_username?: string;
  role: 'owner' | 'admin' | 'editor' | 'viewer';
  pages: string[];
  joined_at: string;
}

export interface MyPermissions {
  role: 'owner' | 'admin' | 'editor' | 'viewer';
  pages: string[];
  is_owner: boolean;
}

export interface ProjectInvite {
  id: number;
  project_id: number;
  email?: string;
  invite_token: string;
  status: 'pending' | 'accepted' | 'expired';
  created_at: string;
}

// ─── Transactions ────────────────────────────────────────────────────────────

export interface Transaction {
  id: number;
  txn_id: string;
  project_id?: number;
  date: string;
  account: string;
  currency: string;
  income: number;
  expense: number;
  counterparty?: string;
  cp_key?: string;
  inn?: string;
  purpose?: string;
  cat_lvl1_2?: string;
  cat_lvl2_2?: string;
  status?: string;
  purpose_tag?: string;
  annex_id?: string;
  invoice_id?: string;
  is_cashflow2?: number;
}

export interface TransactionFilter {
  start?: string;
  end?: string;
  account?: string;
  currency?: string;
  cat_lvl1?: string;
  cat_lvl2?: string;
  status?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

export interface UnassignedGroupRow {
  cp_key: string;
  counterparty: string;
  count: number;
  total_income: string;
  total_expense: string;
  currency?: string;
}

// ─── References ──────────────────────────────────────────────────────────────

export interface Account {
  id: number;
  account: string;
  bank: string;
  currency: string;
  account_type: string;
  is_our_account: boolean;
  account_name?: string;
  is_customs_payee: boolean;
}

export interface CounterpartyCategory {
  id: number;
  cp_key: string;
  cat_lvl1: string;
  cat_lvl2?: string;
}

export interface CategoryRef {
  id: number;
  direction: string;
  cat_lvl1: string;
  cat_lvl2: string;
  sort_order: number;
}

// ─── Reports ─────────────────────────────────────────────────────────────────

export interface BalanceRow {
  account: string;
  bank: string;
  currency: string;
  balance: number;
}

export interface DdsMonthRow {
  cat_lvl1: string;
  cat_lvl2: string;
  income: number;
  expense: number;
}

export interface DashboardBalances {
  rub: number;
  cny: number;
  cashflow_income: number;
  cashflow_expense: number;
}

// ─── Planning ────────────────────────────────────────────────────────────────

export interface Order {
  id: number;
  order_no: number;
  project_id?: number;
  ship_date?: string;
  order_amount?: number;
  logistics_cny?: number;
  customs_rub?: number;
  note?: string;
}

export interface PlannedPayment {
  id: number;
  project_id?: number;
  order_no?: number;
  direction: string;
  pay_date?: string;
  currency: string;
  amount?: number;
  fx_rate?: number;
  amount_rub?: number;
  is_paid: boolean;
  paid_rub?: number;
  note?: string;
}

export interface PlannedIncome {
  id: number;
  project_id?: number;
  date: string;
  amount_rub: number;
  source?: string;
  note?: string;
}

export interface WbPayout {
  id: number;
  request_id: string;
  amount_rub: number;
  created_at: string;
  status: string;
  wb_status_raw?: string;
  bank_comment?: string;
  matched_txn_id?: string;
  matched_at?: string;
}

export interface CashflowDailyRow {
  date: string;
  planned_income: number;
  planned_expense: number;
  net: number;
  deficit_running: number;
}

export interface LeadTime {
  id: number;
  direction: string;
  days: number;
}

// ─── Brand Plans ─────────────────────────────────────────────────────────────

export interface BrandPlan {
  id?: number;
  brand: string;
  year: number;
  month: number;
  plan_amount: number;
  created_at?: string;
}

export interface PlanFactDayRow {
  dt: string;
  fact_day: number;
  plan_day: number;
  fact_cumulative: number;
  plan_cumulative: number;
  pct: number | null;
  is_future?: boolean;
}

export interface PlanFactDailyResult {
  rows: PlanFactDayRow[];
  forecast: number;
  plan_month: number;
  debt_prev: number;
  surplus_prev: number;
  plan_adjusted: number;
  fact_mtd: number;
  pct: number | null;
  days_in_month: number;
  current_day: number;
}

export interface PlanFactBrandRow {
  brand: string;
  plan_month: number;
  debt_prev: number;
  surplus_prev: number;
  plan_adjusted: number;
  fact_mtd: number;
  pct: number | null;
  forecast: number;
  days_in_month: number;
  current_day: number;
  /** Бренд с продажами, но без заведённого плана (или «без бренда») — план=0,
   *  добавляется для сходимости итога с ОПиУ. */
  no_plan?: boolean;
}

// ─── Cost ────────────────────────────────────────────────────────────────────

export interface Nomenclature {
  id: number;
  name: string;
  article_seller?: string;
  article_wb?: number;
  barcode?: string;
  subject?: string;
  volume_l?: number;
  area_m2?: number | null;
  weight_kg?: number | null;
  first_sale_date?: string | null;
}

export interface DutyRule {
  id: number;
  subject: string;
  basis: string;
  rate: number;
  util_collect_rub: number;
  note?: string | null;
}

/** Исключение пошлины по артикулу — переопределяет базис+ставку правила категории.
 *  Утиль-сбор не задаётся: берётся из правила категории (по subject). */
export interface DutyException {
  id: number;
  article_seller: string;
  basis: string;
  rate: number;
  note?: string | null;
}

/** Баркод в машинах с пошлиной «За м²», но без заданной площади. */
export interface MissingAreaBarcode {
  barcode: string;
  subject?: string | null;
  article_seller?: string | null;
  total_qty: number;
  vehicles: string[];
}

/** Баркод в машинах с пошлиной «От веса», но без заданного веса (та же форма, что MissingAreaBarcode). */
export type MissingWeightBarcode = MissingAreaBarcode;

export interface CostOrder {
  id: number;
  order_no: string;
  project_id?: number;
  invoice_no?: string;
  dt_number?: string;
  ship_date?: string;
  actual_arrival_date?: string;
  transport_type?: string;
  delivery_cost?: number;
  delivery_cost_cny?: number;
  delivery_cost_usd?: number;
  rate_cny?: number;
  rate_usd?: number;
  rate_eur?: number;
  note?: string;
  created_at: string;
  items_count?: number;
  total_rub?: number;
  status?: VehicleStatus;
  target_warehouse_id?: number;
  inbound_receipt_id?: number;
}

export interface CostOrderItem {
  id: number;
  order_id: number;
  nomenclature_id?: number;
  name: string;
  qty: number;
  price_cny: number;
  weight_kg?: number;
  volume_l?: number;
  duty_rate?: number;
  duty_amount?: number;
  vat_amount?: number;
  cost_rub?: number;
}

// ─── Integrations ────────────────────────────────────────────────────────────

export interface IntegrationKey {
  id: number;
  project_id?: number;
  service: string;
  label?: string;
  is_active: boolean;
  created_at: string;
  last_sync_at?: string;
}

export interface SyncLog {
  id: number;
  integration_id: number;
  service: string;
  sync_type: string;
  started_at: string;
  finished_at?: string;
  status: string;
  rows_fetched: number;
  error_message?: string;
}

// ─── Funnel ──────────────────────────────────────────────────────────────────

export interface FunnelDayRow {
  date: string;
  nm_id?: number;
  brand_name?: string;
  brand?: string;
  vendor_code?: string;
  subject_name?: string;
  subject?: string;
  opens: number;
  open_card?: number;
  add_to_cart: number;
  orders: number;
  orders_count?: number;
  orders_sum: number;
  orders_sum_rub?: number;
  buyout_sum: number;
  revenue?: number;
  ad_sum?: number;
  adv_sum?: number;
  ad_views?: number;
  adv_views?: number;
  ad_clicks?: number;
  adv_clicks?: number;
  cost_price?: number;
  cost_total?: number;
  profit?: number;
  margin?: number;
  roi?: number;
  tax?: number;
  commission_rate?: number;
  commission?: number;
  avg_price?: number;
  ctr?: number;
  cpc?: number;
  cpm?: number;
  drr?: number;
  add_to_cart_pct?: number;
  cart_to_order_pct?: number;
  has_bdr?: boolean;
  spp_rate?: number;
  to_pay_rate?: number;
  buyout_pct?: number;
  buyout_percent?: number;
}

export interface FunnelSkuRow {
  nm_id: number;
  vendor_code: string;
  brand: string | null;
  subject: string | null;
  open_card: number;
  add_to_cart: number;
  orders_count: number;
  orders_sum_rub: number;
  buyout_percent: number;
  revenue: number;
  adv_sum: number;
  adv_views: number;
  adv_clicks: number;
  avg_price: number;
  add_to_cart_pct: number;
  cart_to_order_pct: number;
  tax: number;
  profit: number;
  margin: number;
  commission: number;
  commission_rate: number;
  cost_total: number;
  spp_rate: number;
  to_pay_rate: number;
  has_tariff_gaps: boolean;
  has_bdr: boolean;
  ctr: number;
  cpc: number;
  cpm: number;
  cr: number;
  drr: number;
  // Расширенный режим (extended=true): остатки и себестоимость
  wb_stock_qty?: number;
  wb_stock_cost?: number;
  own_stock_qty?: number;
  own_stock_cost?: number;
  stock_days_left?: number;
  stock_out_date?: string | null;
  stock_trend_pct?: number;
}

export interface FunnelAbcRow {
    nm_id: number;
    vendor_code: string;
    subject: string;
    brand: string;
    orders_sum: number;
    orders_count: number;
    adv_sum: number;
    adv_views: number;
    adv_clicks: number;
    revenue: number;
    profit: number;
    abc_revenue: string;
    abc_profit: string;
    drr: number;
    margin_pct: number;
    // Расширенный режим (extended=true): остатки и себестоимость
    wb_stock_qty?: number;
    wb_stock_cost?: number;
    own_stock_qty?: number;
    own_stock_cost?: number;
    stock_days_left?: number;
    stock_out_date?: string | null;
    stock_trend_pct?: number;
}

export interface FunnelGroupRow extends Omit<FunnelSkuRow, 'nm_id' | 'vendor_code' | 'brand' | 'subject'> {
    brand?: string | null;
    subject?: string | null;
    tag?: string;
    imt_group?: string;
    children?: FunnelSkuRow[];
}

export interface FunnelSummary {
  opens: number;
  open_card?: number;
  add_to_cart: number;
  orders: number;
  orders_count?: number;
  orders_sum: number;
  orders_sum_rub?: number;
  buyout_sum: number;
  ad_sum: number;
  adv_sum?: number;
  adv_views?: number;
  adv_clicks?: number;
  profit: number;
  drr?: number;
  [key: string]: number | undefined;
}

// ─── WB Tariffs ─────────────────────────────────────────────────────────────

export interface WbTariff {
  id: number;
  subject_name: string;
  commission_rate: number;
}

export interface WbTariffUploadResult {
  inserted: number;
  replaced: number;
}

export interface MissingCostItem {
  nm_id: number;
  barcode: string;
  vendor_code: string;
  subject: string;
  brand: string;
  total_orders: number;
  total_qty: number;
  days_count: number;
  current_cost: number;
  avg_price: number;
  is_suspicious: boolean;
}

// ─── Common ──────────────────────────────────────────────────────────────────

export interface MessageResponse {
  message: string;
  project_slug?: string;
}

export interface ImportResult {
  ok: boolean;
  rows_inserted: number;
  rows_skipped: number;
  rows_error: number;
  errors?: string[];
}

// ─── Order Geography ─────────────────────────────────────────────────────────

export interface CityOrderCount {
  city: string;
  region: string;
  okrug: string;
  order_count: number;
}

export interface DailyOrderCount {
  date: string;
  count: number;
}

export interface OrderGeographyResponse {
  cities: CityOrderCount[];
  daily: DailyOrderCount[];
  dates: string[];
  totals: {
    total_orders: number;
    unique_cities: number;
  };
  filters: {
    brands: string[];
    categories: string[];
    articles: string[];
  };
}

// ─── Telegram ─────────────────────────────────────────────────────────────────

export interface TelegramChatBinding {
  id: number;
  chat_id: number;
  project_id: number;
  brand: string | null;
  notify_enabled: boolean;
  ff_notify_enabled: boolean;
  ff_board_enabled: boolean;
  /** NULL = табло по всем складам; иначе — заявки только этого склада ФФ. */
  ff_board_warehouse_id: number | null;
  created_by_id: number;
  created_at: string;
}

export interface TelegramLinkResponse {
  deep_link_url: string;
}

// ─── Dashboard ──────────────────────────────────────────────────────────────

export interface DailyCashflowRow {
  date: string;
  income: number;
  expense: number;
}

export interface ExpenseCategoryPie {
  name: string;
  value: number;
  count?: number;
}

export interface IncomeCounterparty {
  name: string;
  key: string;
  total: number;
  count: number;
}

export interface DashboardSummary {
  balance_rub: number;
  balance_cny: number;
  month_income: number;
  month_expense: number;
  orders_count: number;
  orders_total_cny: number;
  debt_rub: number;
  debt_cny: number;
  inbox_count: number;
  accounts_count: number;
  daily_cashflow: DailyCashflowRow[];
  expense_by_category: ExpenseCategoryPie[];
  income_counterparties: IncomeCounterparty[];
  date_from: string;
  date_to: string;
}

export interface BalanceAccount {
  account: string;
  account_name: string | null;
  currency: string;
  balance: number;
}

export interface DashboardFunnelSummary {
  opens: number;
  add_to_cart: number;
  orders: number;
  orders_count?: number;
  orders_sum: number;
  orders_sum_rub?: number;
  buyout_sum: number;
  ad_sum: number;
  adv_sum?: number;
  profit: number;
}

export interface FilteredTransactionsResponse {
  total: number;
  items: DashboardTransaction[];
}

export interface DashboardTransaction {
  date: string;
  counterparty: string;
  income: number;
  expense: number;
  purpose: string;
  category: string;
  account: string;
}

export interface CategoryCounterparty {
  key: string;
  name: string;
  total: number;
  count: number;
}

// ─── Inbox ──────────────────────────────────────────────────────────────────

export interface AutoCategorizeRule {
  id: number;
  keyword: string;
  direction: string;
  cat_lvl1: string;
  cat_lvl2: string | null;
  priority: number;
  is_active: boolean;
}

export interface AutoCategorizePreview {
  txn_id: string;
  date: string;
  counterparty: string;
  purpose: string;
  expense: number;
  income: number;
  currency: string;
  matched_keyword: string;
  suggested_cat_lvl1: string;
  suggested_cat_lvl2: string | null;
}

// ─── Cost History ───────────────────────────────────────────────────────────

export interface CostHistoryArticle {
  article_seller: string;
  article_wb: string | null;
  barcode: string;
  brand: string | null;
  subject: string;
  avg_cost: number | null;
  latest_cost: number | null;
  costs: Record<string, { cost: number; qty: number }>;
}

export interface CostHistoryOrder {
  order_no: string;
  ship_date: string;
}

export interface CostHistoryResponse {
  articles: CostHistoryArticle[];
  orders: CostHistoryOrder[];
  brands: string[];
}

// ─── Funnel Filters ─────────────────────────────────────────────────────────

export interface FunnelFilters {
  brands: string[];
  subjects: string[];
  vendor_codes: string[];
  min_date: string | null;
  max_date: string | null;
}

// ─── Product Classification ──────────────────────────────────────────────────

export interface ProductTag {
  id: number;
  name: string;
  color: string;
}

export interface ProductTagMappingPayload {
  nm_ids: number[];
  add_tags: number[];
  remove_tags: number[];
}

export interface ProductStatusPayload {
  nm_id: number;
  status: string;
}

export interface ProductStatusBulkPayload {
  nm_ids: number[];
  status: string;
}

export interface FunnelProduct {
  nm_id: number;
  brand: string;
  vendor_code: string;
  imt_id: number | null;
}

export interface FunnelProductsResponse {
  products: FunnelProduct[];
}

export interface FunnelDataResponse {
  data: FunnelDayRow[];
  detailed: boolean;
  tax_rate?: number;
  has_bdr?: boolean;
  tax_info?: Record<string, unknown>;
}

export interface FunnelSyncStatus {
  scheduler: Record<string, unknown>;
  last_syncs: Array<{
    id: number;
    sync_type: string;
    status: string;
    rows_inserted: number;
    started_at: string | null;
    finished_at: string | null;
    error_msg: string | null;
  }>;
  missing_days?: number;
}

// ─── DDS PnL ────────────────────────────────────────────────────────────────

export interface DDSPnLMonthlyValues {
  total?: number;
  [monthKey: string]: number | undefined;
}

export interface DDSPnLCategory {
  name: string;
  type: 'income' | 'expense';
  monthly: DDSPnLMonthlyValues;
  counterparties?: DDSPnLCounterparty[];
}

export interface DDSPnLCounterparty {
  name: string;
  monthly: DDSPnLMonthlyValues;
}

export interface DDSPnLMonth {
  key: number;
  label: string;
}

export interface DDSPnLResponse {
  months: DDSPnLMonth[];
  categories: DDSPnLCategory[];
  summary: {
    total_income: DDSPnLMonthlyValues;
    total_expense: DDSPnLMonthlyValues;
    net_profit: DDSPnLMonthlyValues;
  };
  revenue: DDSPnLMonthlyValues;
}

// ─── Chart tooltip props ────────────────────────────────────────────────────

export interface ChartTooltipPayloadItem {
  name: string;
  value: number;
  color?: string;
  fill?: string;
}

// ─── Warehouse ─────────────────────────────────────────────────────────────

export interface Warehouse {
  id: number;
  project_id: number;
  name: string;
  warehouse_type: 'EXTERNAL' | 'FULFILLMENT';
  country?: string;
  address?: string;
  assembly_days?: number;
  wb_acceptance_days?: number;
  external_id?: string;
  sort_order: number;
  is_active: boolean;
  total_stock: number;
  vehicles_in_transit: number;
  counterparty_id?: number | null;
  counterparty_inn?: string | null;
  counterparty_name?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface DeliveryTimeRow {
  wb_warehouse_name: string;
  delivery_days: number;
  total_days: number;
}

export interface DeliveryTimesResponse {
  warehouse_id: number;
  warehouse_name: string;
  assembly_days: number;
  wb_acceptance_days: number;
  wb_warehouses: DeliveryTimeRow[];
}

export interface DeliveryTimesUpdate {
  assembly_days?: number;
  wb_acceptance_days?: number;
  items: { wb_warehouse_name: string; delivery_days: number }[];
}

export interface InboundReceiptItem {
  id: number;
  receipt_id: number;
  nomenclature_id: number;
  barcode: string;
  expected_qty: number;
  actual_qty: number;
}

export interface InboundReceipt {
  id: number;
  project_id: number;
  warehouse_id: number;
  number: string;
  status: 'DRAFT' | 'EXPECTED' | 'ACCEPTED' | 'CANCELLED';
  planned_date?: string;
  actual_date?: string;
  comment?: string;
  tags?: string;
  cost_order_id?: number;
  is_defect?: boolean;
  defect_reason?: string;
  created_at?: string;
  updated_at?: string;
  items: InboundReceiptItem[];
}

export interface OutboundShipmentItem {
  id: number;
  shipment_id: number;
  nomenclature_id: number;
  barcode: string;
  quantity: number;
}

export interface OutboundShipment {
  id: number;
  project_id: number;
  warehouse_id: number;
  number: string;
  status: 'DRAFT' | 'SHIPPED' | 'DELIVERED' | 'CANCELLED';
  destination?: string;
  wb_supply_id?: string;
  shipped_date?: string;
  comment?: string;
  is_defect?: boolean;
  defect_reason?: string;
  created_at?: string;
  updated_at?: string;
  items: OutboundShipmentItem[];
}

export interface StockTransferItem {
  id: number;
  transfer_id: number;
  nomenclature_id: number;
  barcode: string;
  quantity: number;
}

export interface StockTransfer {
  id: number;
  project_id: number;
  from_warehouse_id: number;
  to_warehouse_id: number;
  number: string;
  status: 'DRAFT' | 'IN_TRANSIT' | 'COMPLETED';
  comment?: string;
  is_defect: boolean;
  defect_reason?: string;
  created_at?: string;
  updated_at?: string;
  items: StockTransferItem[];
}

export interface StockMovement {
  id: number;
  project_id: number;
  warehouse_id: number;
  nomenclature_id: number;
  barcode: string;
  movement_type: string;
  quantity: number;
  defect_delta: number;
  reference_type: string;
  reference_id?: number;
  comment?: string;
  created_at?: string;
}

export interface WarehouseStockRow {
  id: number;
  project_id: number;
  warehouse_id: number;
  nomenclature_id: number;
  barcode: string;
  quantity: number;
  in_transit: number;
  defect_quantity: number;
  defect_in_transit: number;
  cost_price?: number;
  updated_at?: string;
  reserved: number;
  available: number;
}

export interface DefectOperation {
  barcode: string;
  quantity: number;
  reason: string;
}

export interface DefectBulkItem {
  barcode: string;
  quantity: number;
}
export interface DefectBulkOperation {
  reason: string;
  items: DefectBulkItem[];
}
export interface DefectBulkError {
  barcode: string;
  error: string;
}
export interface DefectBulkResponse {
  status: 'ok' | 'partial' | 'error';
  processed: number;
  failed: number;
  errors: DefectBulkError[];
  operation_id?: number | null;
  receipt_id?: number | null;
  shipment_id?: number | null;
  number?: string | null;
}

export interface DefectMarkOperationItem {
  id: number;
  operation_id: number;
  nomenclature_id: number;
  barcode: string;
  quantity: number;
}

export interface DefectMarkOperation {
  id: number;
  project_id: number;
  warehouse_id: number;
  number: string;
  status: 'ACCEPTED' | 'CANCELLED';
  actual_date?: string | null;
  reason?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  items: DefectMarkOperationItem[];
}

export interface DefectMarkCancelResponse {
  status: string;
  operation_id: number;
  number: string;
  reverted_items: number;
}

export interface StockSummaryRow {
  nomenclature_id: number;
  barcode: string;
  warehouses: Record<number, number>;
  in_transit: Record<number, number>;
  reserved: Record<number, number>;
  total: number;
  total_in_transit: number;
  total_reserved: number;
  total_available: number;
}

export interface TrendPeriodData {
  avg_daily_qty: number;
  sale_qty?: number;  // total units sold in the window (added 2026-04-15 for novelty KPI)
  revenue: number;
  profit: number;
  date_from: string;  // ISO YYYY-MM-DD — first day of the trend window (inclusive)
  date_to: string;    // ISO YYYY-MM-DD — last day of the trend window (= yesterday, inclusive)
}

export interface UnifiedStockRow {
  nomenclature_id: number;
  barcode: string;
  article_seller: string | null;
  subject: string | null;
  brand: string | null;
  warehouses: Record<string, number>;
  wb_stocks: Record<string, number>;
  in_transit: number;
  reserved: number;
  total_own: number;
  total_wb: number;
  total_defect: number;
  total: number;
  factory_qty: number;
  vehicle_forming_qty: number;
  vehicle_transit_qty: number;
  avg_cost: number;
  cost_factory_unit?: number;
  is_cost_estimated?: boolean;
  is_revenue_estimated?: boolean;
  avg_daily_revenue: number;
  avg_daily_profit: number;
  avg_price: number;
  avg_profit: number;
  trend_7: TrendPeriodData;
  trend_14: TrendPeriodData;
  trend_30: TrendPeriodData;
  // Per-unit price/profit drawn from the item's subject (category) average —
  // used by the «Новинки» KPI card to estimate revenue on SKUs that are in
  // transit but have no sales history yet. Always present; 0 if the category
  // had no sales in the 30-day window.
  novelty_unit_revenue?: number;
  novelty_unit_profit?: number;
  // «Новинка» flag — true when the SKU has had no sales in the last 60 days.
  // Drives the Новинки KPI card filter. Backend computes from wb_finance_rows.
  is_novelty?: boolean;
  group_name?: string;
  items_count?: number;
  abc_class?: string;
  children?: UnifiedStockRow[];
}

export interface StockAdjustment {
  id: number;
  project_id: number;
  warehouse_id: number;
  nomenclature_id: number;
  barcode: string;
  delta: number;
  reason: string;
  created_at?: string;
}

// ─── WB Stock (warehouse stock snapshots) ─────────────────────────────────

export interface WbWarehouseRow {
  name: string;
  total_qty: number;
  in_way_to_client: number;
  in_way_from_client: number;
  articles_count: number;
  yesterday_qty: number;
  change: number;
}

export interface WbStocksResponse {
  warehouses: WbWarehouseRow[];
  total_warehouses: number;
  total_qty: number;
  total_in_way_to_client: number;
  total_in_way_from_client: number;
  yesterday_total_qty: number;
  change_total: number;
  last_synced_at: string | null;
}

export interface WbArticleWarehouse {
  name: string;
  quantity: number;
  in_way_to_client: number;
  in_way_from_client: number;
}

export interface WbArticleRow {
  nm_id: number;
  vendor_code: string;
  subject: string;
  brand: string;
  total_qty: number;
  in_way_to_client: number;
  in_way_from_client: number;
  warehouses: WbArticleWarehouse[];
}

export interface WbStocksArticlesResponse {
  articles: WbArticleRow[];
  total_articles: number;
}

export interface WbStockHistoryDay {
  date: string;
  total_qty: number;
  in_way_to_client: number;
  in_way_from_client: number;
  total: number;
  articles_count: number;
}

export interface WbStockHistoryResponse {
  days: WbStockHistoryDay[];
  warehouses: string[];
}

// ─── Cost DNA (разложение рубля выручки по категориям) ─────────────────────

export interface CostDnaCategory {
  category: string;        // subject_name
  revenue: number;
  revenue_share_pct: number;
  // Cost components — null if no cost_orders for category
  cost_factory_pct: number | null;
  cost_duty_pct: number | null;
  cost_delivery_pct: number | null;
  cost_vat_pct: number | null;
  cost_total_pct: number | null;
  has_cost_data: boolean;
  // Marketplace fees as % of revenue
  mp_commission_pct: number;
  mp_logistics_pct: number;
  mp_storage_pct: number;
  mp_advertising_pct: number;
  mp_other_pct: number;
  mp_total_pct: number;
  tax_pct: number;
  // Margin (null if no cost data)
  margin_pct: number | null;
  margin_pct_prev: number | null;
  margin_trend: 'up' | 'down' | null;
}

export interface CostDnaTotals {
  revenue: number;
  cost_factory_pct: number;
  cost_duty_pct: number;
  cost_delivery_pct: number;
  cost_vat_pct: number;
  cost_total_pct: number;
  mp_commission_pct: number;
  mp_logistics_pct: number;
  mp_storage_pct: number;
  mp_advertising_pct: number;
  mp_other_pct: number;
  mp_total_pct: number;
  tax_pct: number;
  margin_pct: number;
  margin_pct_prev: number | null;
  margin_trend: 'up' | 'down' | null;
}

export interface CostDnaResponse {
  period_days: number;      // span in days: legacy preset 30/60 OR custom 1–365
  date_from: string;        // YYYY-MM-DD
  date_to: string;          // yesterday (legacy preset) or custom end
  prev_date_from: string;
  prev_date_to: string;
  categories: CostDnaCategory[];
  totals: CostDnaTotals;
  has_tax_settings: boolean;
}

export interface ChartTooltipProps {
  active?: boolean;
  payload?: ChartTooltipPayloadItem[];
  label?: string;
}

// ─── Monitoring ─────────────────────────────────────────────────────────────

export interface SyncTypeStatus {
  service: string;
  sync_type: string;
  last_ok_at: string | null;
  last_error_at: string | null;
  last_error_msg: string | null;
  last_status: string | null;
  total_24h: number;
  ok_24h: number;
  error_24h: number;
  avg_duration_sec: number | null;
  is_running: boolean;
  running_since: string | null;
}

export interface SchedulerJobInfo {
  id: string;
  name: string;
  next_run: string | null;
  is_active: boolean;
}

export interface MonitoringOverview {
  sync_types: SyncTypeStatus[];
  scheduler: {
    running: boolean;
    jobs: SchedulerJobInfo[];
  };
  total_syncs_24h: number;
  total_errors_24h: number;
  health: Record<string, string>;
}

export interface MonitoringSyncLogEntry {
  id: number;
  integration_id: number;
  service: string;
  sync_type: string;
  started_at: string | null;
  finished_at: string | null;
  status: string;
  rows_fetched: number;
  rows_inserted: number;
  error_msg: string | null;
  duration_sec: number | null;
}

export interface PieLabelProps {
  cx: number;
  cy: number;
  midAngle: number;
  outerRadius: number;
  name: string;
  percent: number;
  index: number;
}

// ─── WB FBO Supplies ──────────────────────────────────────────────────────

export type WbSupplyStatus = 'ACTIVE' | 'ON_DELIVERY' | 'IN_PROGRESS' | 'ACCEPTED' | 'CANCELLED';

export interface WbFboSupply {
  id: number;
  project_id: number;
  wb_supply_id: string;
  wb_status: WbSupplyStatus;
  name?: string;
  created_at_wb: string;
  planned_date?: string;
  actual_date?: string;
  warehouse_name?: string;
  cargo_type?: string;
  total_qty: number;
  accepted_qty: number;
  outbound_shipment_id?: number;
  outbound_shipment_number?: string;
  outbound_shipment_status?: string;
  outbound_shipment_warehouse_id?: number;
  outbound_shipment_warehouse_name?: string;
  outbound_shipment_shipped_date?: string;
  is_archived?: boolean | null;
  excess_processed_at?: string | null;
  excess_qty?: number | null;
  reassigned_to_supply_id?: number | null;
  reassigned_to_wb_supply_id?: string | null;
  reassigned_from_wb_supply_ids?: string[];
  assembly_request_id?: number;
  assembly_request_number?: string;
  assembly_request_status?: string;
  source_warehouse_id?: number;
  synced_at?: string;
  return_processed_at?: string;
  return_type?: 'GOODS' | 'DEFECT' | 'UTILIZED' | null;
  return_qty?: number | null;
  created_at?: string;
  updated_at?: string;
}

export interface WbFboSupplyItem {
  id: number;
  supply_id: number;
  wb_order_id: string;
  nm_id?: number;
  barcode: string;
  article_seller?: string;
  product_name?: string;
  quantity: number;
  accepted_qty: number;
}

export interface WbFboSupplyListResponse {
  items: WbFboSupply[];
  total: number;
}

export interface FboPartialSummaryItem {
  barcode: string;
  product_name?: string;
  article_seller?: string;
  delta: number;
}

export interface FboPartialSummary {
  unaccepted_total: number;
  unprocessed: number;
  returned_to_stock: number;
  utilized: number;
  items_breakdown: FboPartialSummaryItem[];
}

export type FboReturnType = 'GOODS' | 'DEFECT' | 'UTILIZED';

export interface FboReturnItem {
  barcode: string;
  quantity: number;
  return_type: FboReturnType;
}

export interface FboReturnRequest {
  warehouse_id?: number | null;
  items: FboReturnItem[];
  comment?: string | null;
}

export interface FboReturnResponse {
  supply_id: number;
  receipt_id?: number | null;
  receipt_number?: string | null;
}

export interface FboSyncResult {
  synced: number;
  created: number;
  updated: number;
  errors: number;
  message: string;
}

export interface FboReassignCandidateItem {
  barcode: string;
  article_seller?: string | null;
  product_name?: string | null;
  quantity: number;
  accepted_qty: number;
  unaccepted_delta: number;
}

export interface FboReassignCandidate {
  id: number;
  wb_supply_id: string;
  warehouse_name?: string | null;
  created_at_wb: string;
  total_qty: number;
  accepted_qty: number;
  same_warehouse: boolean;
  matched_qty: number;
  matched_items: FboReassignCandidateItem[];
}

export interface FboReassignCandidatesResponse {
  source_supply_id: number;
  candidates: FboReassignCandidate[];
}

export interface FboReassignRequest {
  target_supply_id: number;
}

export interface FboReassignResponse {
  source_supply_id: number;
  target_supply_id: number;
  target_wb_supply_id: string;
  reassigned_qty: number;
}

export type FboAuditAction =
  | 'ARCHIVE' | 'UNARCHIVE' | 'BULK_RESTORE'
  | 'RETURN' | 'EXCESS' | 'REASSIGN' | 'REVERT';

export interface FboAuditEntry {
  id: number;
  supply_id: number;
  action: FboAuditAction;
  payload: Record<string, unknown>;
  user_id: number | null;
  created_at: string;
  reverted_audit_id: number | null;
  reverted_at: string | null;
  revertible: boolean;
}

export interface FboAuditResponse {
  supply_id: number;
  entries: FboAuditEntry[];
}

export interface FboAuditListEntry extends FboAuditEntry {
  supply_wb_id: string;
  warehouse_name: string | null;
  username: string | null;
}

export interface FboAuditListResponse {
  entries: FboAuditListEntry[];
  total: number;
}

export interface FboAuditRevertResponse {
  audit_id: number;
  revert_id: number;
  action: string;
}

// ─── Assembly Requests ────────────────────────────────────────────────────

export type AssemblyStatus = 'PENDING' | 'IN_PROGRESS' | 'READY' | 'VEHICLE_ASSIGNED' | 'SHIPPED' | 'DELIVERED' | 'CLOSED' | 'CANCELLED';

export type PackageType = 'BOX' | 'MONOPALLET' | 'SUPERSAFE';

export interface AssemblyRequestItem {
  id: number;
  nomenclature_id: number;
  barcode: string;
  quantity: number;
  product_name?: string;
  brand?: string;
  stock_quantity: number;
}

export interface AssemblyRequest {
  id: number;
  warehouse_id: number;
  warehouse_name?: string;
  number: string;
  status: AssemblyStatus;
  wb_fbo_supply_id: number | null;
  wb_supply_name?: string;
  wb_warehouse_name?: string;
  wb_warehouse_name_manual?: string | null;
  source_draft_id?: number | null;
  effective_wb_warehouse?: string | null;
  wb_supply_id_wb?: string;
  wb_fbo_status?: string;
  wb_fbo_planned_date?: string;
  wb_fbo_actual_date?: string;
  outbound_shipment_id?: number;
  estimated_ready_date?: string;
  actual_ready_date?: string;
  pallets_count: number;
  pallet_weight_kg: number;
  total_weight_kg?: number;
  vehicle_info?: string;
  vehicle_brand?: string;
  driver_phone?: string;
  pickup_date?: string;
  pickup_time_slot?: string;
  pickup_cost?: number;
  delivery_date?: string;
  vehicle_assigned_at?: string;
  shipped_at?: string;
  comment?: string;
  brands?: string;
  counterparty_id?: number | null;
  carrier_inn?: string | null;
  carrier_name?: string | null;
  package_type?: PackageType;
  items: AssemblyRequestItem[];
  created_at: string;
  updated_at: string;
  /** привязанная ФФ-заявка (зеркало фулфилмента) — только в GET деталки */
  ff_request_id?: number | null;
  ff_request_number?: string | null;
  ff_stage_title?: string | null;
  ff_warehouse_id?: number | null;
}

export interface AssemblyListResponse {
  items: AssemblyRequest[];
  total: number;
}

export interface CreatedRequestBrief {
  id: number;
  number: string;
  ff_id: number;
  ff_name: string;
  wb_name: string | null;
  package_type: PackageType;
  status: AssemblyStatus;
  qty: number;
  sku: number;
}

/** Группа созданных заявок одного черновика («Предпросмотр созданных»). */
export interface CreatedAssemblyGroup {
  draft_id: number;
  draft_name: string | null;
  request_count: number;
  total_qty: number;
  total_sku: number;
  requests: CreatedRequestBrief[];
}

/* ─── Анализ потока сборки (flow analytics) ─── */

export type AssemblyAnomalyKind =
  | 'stuck_assembly'          // IN_PROGRESS дольше порога
  | 'stuck_shipment'          // READY/VEHICLE_ASSIGNED дольше порога от готовности
  | 'wb_accepted_not_shipped' // ВБ уже принял поставку, а заявка не отгружена — забыли отгрузить
  | 'ff_closed_not_shipped'   // ФФ закрыл/заархивировал заявку, а наша сборка ещё не отгружена
  | 'shipped_not_accepted';   // SHIPPED дольше порога без DELIVERED

export interface AssemblyStageDuration {
  /** этап, длительность которого измерена: IN_PROGRESS | READY | VEHICLE_ASSIGNED | SHIPPED */
  stage: AssemblyStatus;
  avg_days: number | null;
  median_days: number | null;
  /** на скольких заявках посчитано */
  count: number;
}

export interface AssemblyTransitionStat {
  /** null = создание заявки */
  from_status: string | null;
  to_status: string;
  count: number;
  /** среднее время в from_status до этого перехода, дни */
  avg_days: number | null;
}

export interface AssemblyAnomalyRow {
  id: number;
  number: string;
  status: AssemblyStatus;
  warehouse_id: number;
  warehouse_name: string | null;
  wb_warehouse_name: string | null;
  kind: AssemblyAnomalyKind;
  /** сколько дней висит на текущем этапе */
  days_stuck: number;
  /** ISO — начало текущего этапа */
  since: string | null;
  total_qty: number;
  /** статус связанной WB FBO-поставки (для wb_accepted_not_shipped) */
  wb_fbo_status: string | null;
  /** номер WB-поставки (wb_fbo_supplies.wb_supply_id) */
  wb_supply_number: string | null;
  pallets_count: number;
  /** номер ФФ-заявки (для ff_closed_not_shipped) */
  ff_request_number?: string | null;
}

export interface AssemblyWarehouseFlowStat {
  warehouse_id: number;
  warehouse_name: string | null;
  active_count: number;
  avg_cycle_days: number | null;
  anomaly_count: number;
}

export interface AssemblyFlowDailyStat {
  /** ISO YYYY-MM-DD */
  date: string;
  /** заявок создано в этот день */
  created_count: number;
  /** товаров (шт) в созданных заявках */
  created_qty: number;
  /** заявок отгружено в этот день */
  shipped_count: number;
  /** средний цикл (создание → отгрузка) отгруженных в этот день */
  avg_cycle_days: number | null;
}

/** Товары по этапам на конец дня (снимок): сколько шт лежало в каждом этапе. */
export interface AssemblyFlowStageDailyStat {
  /** ISO YYYY-MM-DD */
  date: string;
  in_progress_qty: number;
  ready_qty: number;
  vehicle_assigned_qty: number;
  shipped_qty: number;
}

export interface AssemblyFlowSummary {
  /** заявок в работе сейчас (IN_PROGRESS..SHIPPED) */
  active_count: number;
  /** дошло до DELIVERED за период (CLOSED — «ВБ не принял» — не считается) */
  completed_in_period: number;
  /** создание → отгрузка, дни */
  avg_cycle_days: number | null;
  /** создание → READY, дни */
  avg_assembly_days: number | null;
  anomaly_count: number;
}

export interface AssemblyFlowThresholds {
  assembly_days: number;
  ship_days: number;
  delivery_days: number;
}

export interface AssemblyFlowAnalyticsResponse {
  summary: AssemblyFlowSummary;
  stages: AssemblyStageDuration[];
  transitions: AssemblyTransitionStat[];
  by_warehouse: AssemblyWarehouseFlowStat[];
  anomalies: AssemblyAnomalyRow[];
  daily: AssemblyFlowDailyStat[];
  stage_daily: AssemblyFlowStageDailyStat[];
  thresholds: AssemblyFlowThresholds;
}

export interface AssemblyHistoryEntry {
    id: number;
    old_status: string | null;
    new_status: string;
    changed_at: string;
    changed_by: string | null;
    comment: string | null;
}

export interface AssemblyRequestCreate {
  warehouse_id: number;
  wb_fbo_supply_id?: number | null;
  wb_warehouse_name_manual?: string | null;
  estimated_ready_date?: string;
  pallets_count: number;
  pallet_weight_kg: number;
  comment?: string;
  package_type?: PackageType;
  items: { barcode: string; quantity: number }[];
}

export interface AssemblyRequestUpdate {
  warehouse_id?: number;
  estimated_ready_date?: string | null;
  pallets_count?: number;
  pallet_weight_kg?: number;
  comment?: string | null;
  wb_fbo_supply_id?: number | null;
  wb_warehouse_name_manual?: string | null;
  package_type?: PackageType;
  items?: { barcode: string; quantity: number }[];
  pickup_cost?: number;
  vehicle_info?: string;
  vehicle_brand?: string;
  driver_phone?: string;
  carrier_inn?: string | null;
  carrier_name?: string | null;
}

export interface RefreshFromFboResponse {
  added: number;
  removed: number;
  changed: number;
  items: AssemblyRequestItem[];
}

// ─── Logistics Analytics ───────────────────────────────────────────────────

export interface LogisticsCostSummary {
  total_cost: number;
  avg_cost_per_pallet: number;
  total_pallets: number;
  total_shipments: number;
}

export interface LogisticsDestStat {
  dest_warehouse: string;
  avg_cost: number;
  total_cost: number;
  shipments_count: number;
}

export interface LogisticsRouteStat {
  src_warehouse: string;
  dest_warehouse: string;
  avg_cost: number;
  shipments_count: number;
}

export interface LogisticsAnalyticsResponse {
  summary: LogisticsCostSummary;
  by_destination: LogisticsDestStat[];
  by_route: LogisticsRouteStat[];
}

// ─── Anomalies ──────────────────────────────────────────────────────────────

export interface AnomalyMetrics {
  margin?: number;
  was_margin?: number;
  drr?: number;
  adv_sum?: number;
  turnover_days?: number;
  stocks_wb?: number;
  days_left?: number;
  restock_qty?: number;
  frozen_value?: number;
  buyout_pct?: number;
  avg_price?: number;
  orders_sum?: number;
  appetite_weekly?: number;
}

export interface AnomalyItem {
  nm_id: number;
  vendor_code: string;
  brand: string;
  subject: string;
  severity: 'critical' | 'warning';
  anomaly_type: string;
  title: string;
  description: string;
  loss_amount: number | null;
  metrics: AnomalyMetrics;
  action: string;
}

export interface AnomalySummary {
  total_loss: number;
  oos_risk_count: number;
  frozen_capital: number;
  healthy_count: number;
}

export interface AnomaliesResponse {
  summary: AnomalySummary;
  anomalies: AnomalyItem[];
  total_products: number;
  period_days: number;
}

// ─── Capital ──────────────────────────────────────────────────────────────────

export interface PriceRecommendation {
  type: string;
  label: string;
  current_roi: number;
  roi_at_minus_10: number | null;
  roi_at_minus_20: number | null;
}

export interface CapitalSummary {
  total_capital: number;
  liquid_capital: number;
  liquid_pct: number;
  transition_capital: number;
  transition_pct: number;
  illiquid_capital: number;
  illiquid_pct: number;
  roi_monthly: number;
  roi_trend: number;
}

export interface CapitalTrendDay {
  date: string;
  liquid: number;
  transition: number;
  illiquid: number;
  total: number;
  roi_monthly: number;
}

export interface CapitalGroupRow {
  group_key: string;
  group_type: string;
  nm_id: number | null;
  vendor_code: string | null;
  capital: number;
  liquid_pct: number;
  illiquid_pct: number;
  frozen_amount: number;
  roi_monthly: number;
  turnover_days: number;
  sales_per_day: number;
  margin: number;
  drr: number;
  recommendation: PriceRecommendation;
  children_count: number;
}

export interface CapitalResponse {
  summary: CapitalSummary;
  trend: CapitalTrendDay[];
  groups: CapitalGroupRow[];
  total_products: number;
  period_days: number;
  elasticity: number;
}

// ─── Unified Ad Sync ────────────────────────────────────────────────────────

export interface UnifiedSyncProgress {
  phase: 'campaigns' | 'budgets' | 'funnel' | 'done' | 'error' | 'idle';
  campaigns_total?: number;
  budgets_done?: number;
  budgets_total?: number;
  funnel_days_done?: number;
  funnel_days_total?: number;
  rows?: number;
  error?: string;
  message?: string;
  detail?: string;
}

export interface FirstSyncProgress {
  phase: 'nomenclature' | 'campaigns' | 'budgets' | 'funnel' | 'backfill' | 'done' | 'error' | 'idle';
  step?: number;
  total_steps?: number;
  detail?: string;
  funnel_days_done?: number;
  funnel_days_total?: number;
  error?: string;
}

// ─── Ad Campaigns (Ads Tab) ─────────────────────────────────────────────────

export interface AdCampaignEvent {
  event_type: string;  // budget_change, status_change
  old_value: string;
  new_value: string;
  created_at: string;
}

export interface AdCampaign {
  campaign_id: number;
  name: string | null;
  campaign_type: string | null;
  status: number;
  budget: number;
  views?: number;
  clicks?: number;
  spend?: number;
  ctr?: number;
  cpc?: number;
  cpm?: number;
  events?: AdCampaignEvent[];
}

export interface AdTabProduct {
  nm_id: number;
  vendor_code: string | null;
  subject: string | null;
  brand: string | null;
  adv_views: number;
  adv_clicks: number;
  adv_sum: number;
  orders_sum_rub: number;
  orders_count: number;
  ctr: number;
  cpc: number;
  cpm: number;
  drr: number;
  abc_revenue: string;
  abc_profit: string;
  bdr_revenue: number;
  bdr_profit: number;
  stock_qty: number;
  active_campaigns: number;
  campaigns: AdCampaign[];
}

export interface AdTabGroupRow {
  group_name: string;
  adv_views: number;
  adv_clicks: number;
  adv_sum: number;
  orders_sum_rub: number;
  orders_count: number;
  ctr: number;
  cpc: number;
  cpm: number;
  drr: number;
  bdr_revenue: number;
  bdr_profit: number;
  abc_revenue: string;
  abc_profit: string;
  stock_qty: number;
  product_count: number;
  active_campaigns: number;
  children: AdTabProduct[];
}

// ─── Supply Chain ─────────────────────────────────────────────────────────────

export interface Supplier {
  id: number;
  project_id: number;
  name: string;
  country: 'CHINA' | 'RUSSIA';
  currency: 'CNY' | 'RUB';
  delivery_days_min?: number;
  delivery_days_max?: number;
  note?: string;
  inn?: string | null;
  contract_number?: string | null;
  counterparty_id?: number | null;
  created_at?: string;
  updated_at?: string;
}

export interface FactoryOrderItem {
  id: number;
  factory_order_id: number;
  barcode: string;
  subject?: string;
  article_seller?: string;
  qty: number;
  assigned_qty: number;
  price_cny: number;
  box_size?: string;
  pcs_per_box?: number;
  weight_kg?: number;
  note?: string;
  box_detail?: number[] | null;
  mix_group_id?: string | null;
  mix_box_size?: string | null;
  mix_pcs_per_box?: number | null;
  remaining_qty?: number;
}

export type FactoryOrderStatus = 'FORMING' | 'DISTRIBUTED' | 'CLOSED';

export interface SupplyProject {
  id: number;
  project_id: number;
  name: string;
  color?: string | null;
  note?: string | null;
  created_at?: string;
  updated_at?: string;
  orders_count?: number | null;
}

export interface SupplyProjectCreate {
  name: string;
  color?: string | null;
  note?: string | null;
}

export interface SupplyProjectUpdate {
  name?: string;
  color?: string | null;
  note?: string | null;
}

export interface MergeOrdersRequest {
  target_id: number;
  source_ids: number[];
}

export interface MergeOrdersResult {
  target_id: number;
  merged_orders: number;
  items_moved: number;
  items_merged: number;
}

export interface FactoryOrder {
  id: number;
  project_id: number;
  order_number: string;
  factory_name?: string;
  order_date?: string;
  expected_ready_date?: string;
  total_cny?: number;
  status: FactoryOrderStatus;
  is_archived?: boolean;
  note?: string;
  supplier_id?: number;
  supplier?: Supplier;
  supply_project_id?: number | null;
  supply_project?: SupplyProject | null;
  created_at?: string;
  updated_at?: string;
  items?: FactoryOrderItem[];
}

export interface FactoryOrderHistory {
  id: number;
  project_id: number;
  factory_order_id: number;
  event_type: string;
  old_status?: string;
  new_status?: string;
  details?: string;
  changed_at: string;
  changed_by?: string;
}

export interface FactoryOrderItemUpdate {
  barcode?: string;
  subject?: string;
  article_seller?: string;
  qty?: number;
  price_cny?: number;
  box_size?: string;
  pcs_per_box?: number;
  weight_kg?: number;
  note?: string;
  box_detail?: number[] | null;
  mix_group_id?: string | null;
  mix_box_size?: string | null;
  mix_pcs_per_box?: number | null;
}

export interface FactoryOrderCreate {
  order_number: string;
  factory_name?: string;
  order_date?: string;
  expected_ready_date?: string;
  total_cny?: number;
  note?: string;
  supplier_id?: number;
  supply_project_id?: number | null;
  items?: { barcode: string; subject?: string; article_seller?: string; qty: number; price_cny: number; note?: string }[];
}

export type VehicleStatus = 'FORMING' | 'SHIPPED' | 'CUSTOMS' | 'DISPATCHED' | 'DELIVERED';

export interface VehicleStatusUpdate {
  status: VehicleStatus;
  target_warehouse_id?: number;
  dt_number?: string;
}

export interface VehicleCostSummary {
  total_cost_rub: number;
  total_delivery_rub: number;
  total_duty_rub: number;
  total_vat_rub: number;
  total_rub: number;
}

export interface SupplyChainOverview {
  total_factory_orders: number;
  total_vehicles: number;
  vehicles_by_status: Record<string, number>;
  total_items: number;
  total_amount_cny: number;
}

export interface SplitItem {
  factory_order_item_id: number;
  qty: number;
  vehicle_order_no: string;
}

export type VehicleCountry = 'CHINA' | 'RUSSIA';

export interface VehicleCreate {
  order_no?: string;
  container_type?: string;
  country?: VehicleCountry;
  delivery_cost_cny?: number;
  delivery_cost_usd?: number;
  delivery_cost_rub?: number;
  rate_cny?: number;
  rate_usd?: number;
  rate_eur?: number;
  ship_date?: string;
  invoice_no?: string;
  payment_ref?: string;
  target_warehouse_id?: number;
  note?: string;
  vehicle_name?: string;
  plate_number?: string;
}

export interface VehicleUpdateData {
  container_type?: string;
  country?: VehicleCountry;
  delivery_cost_cny?: number;
  delivery_cost_usd?: number;
  delivery_cost_rub?: number;
  rate_cny?: number;
  rate_usd?: number;
  rate_eur?: number;
  ship_date?: string;
  invoice_no?: string;
  dt_number?: string;
  target_warehouse_id?: number;
  note?: string;
  vehicle_name?: string;
  plate_number?: string;
}

export interface VehicleItemSchema {
  id: number;
  order_no: string;
  barcode: string;
  subject?: string;
  article_seller?: string;
  qty: number;
  price_cny: number;
  weight_kg?: number;
  volume_m3?: number;
  cost_rub?: number;
  delivery_rub?: number;
  duty_rub?: number;
  vat_rub?: number;
  total_rub?: number;
  factory_order_item_id?: number;
  factory_order_id?: number;
  box_size?: string;
  pcs_per_box?: number;
  box_detail?: number[] | null;
  mix_group_id?: string | null;
  mix_box_size?: string | null;
  mix_pcs_per_box?: number | null;
  factory_order_number?: string;
  // Drift fields (sc17 — vehicle qty drift confirm)
  qty_drift?: number | null;
  fo_qty?: number | null;
  fo_assigned?: number | null;
  // sc18: позиция добавлена после отгрузки
  added_after_ship?: boolean;
}

// ── Vehicle qty drift (sc17) ─────────────────────────────────────────────
export interface VehicleItemUpdate {
  qty?: number;
  box_size_override?: string | null;
  pcs_per_box_override?: number | null;
  box_detail_override?: number[] | null;
  mode?: 'strict' | 'extend_plan';
}

export interface FactoryQtyExceededDetail {
  error: 'exceeds_factory_qty';
  fo_id: number;
  fo_number: string | null;
  foi_id: number;
  barcode: string;
  subject: string | null;
  fo_qty: number;
  fo_assigned: number;
  available: number;
  attempted_delta: number;
  overflow: number;
  in_mix_group: boolean;
  mix_group_id: string | null;
}

export class FactoryQtyExceededError extends Error {
  detail: FactoryQtyExceededDetail;
  constructor(detail: FactoryQtyExceededDetail) {
    super(`Factory plan exceeded: foi=${detail.foi_id}`);
    this.name = 'FactoryQtyExceededError';
    this.detail = detail;
  }
}

// ── Post-shipment add (sc18) ─────────────────────────────────────────────
export interface PostShipmentAddItem {
  factory_order_item_id: number;
  qty: number;
  box_size_override?: string | null;
  pcs_per_box_override?: number | null;
  mode?: 'strict' | 'extend_plan';
}

export interface PostShipmentItemsRequest {
  items: PostShipmentAddItem[];
}

export interface PostShipmentItemsResponse {
  ok: boolean;
  added: number;
  receipt_items_added: number;
  new_receipt_id: number | null;
  vehicle_status: string;
}

// Корзина «✗»: штрихкода нет ни в одном заказе — завести в заказ (новый/существующий) + положить в машину.
export interface UnorderedItem {
  barcode: string;
  qty: number;
  price_cny: string | number;
  box_size?: string | null;
  pcs_per_box?: number | null;
  weight_kg?: string | number | null;
  subject?: string | null;
  article_seller?: string | null;
}

export interface AddUnorderedItemsRequest {
  target: 'new_order' | 'existing_order';
  factory_order_id?: number | null;  // обязателен для existing_order
  supplier_id?: number | null;        // опц. для new_order
  factory_name?: string | null;       // опц. для new_order
  items: UnorderedItem[];
}

export interface AddUnorderedItemsResponse {
  ok: boolean;
  added: number;
  factory_order_id: number;
  factory_order_number: string;
}

export interface VehicleSchema {
  id: number;
  order_no: string;
  status?: VehicleStatus;
  transport_type?: string;
  container_type?: string;
  country?: VehicleCountry;
  ship_date?: string;
  actual_ship_date?: string;
  actual_arrival_date?: string;
  estimated_arrival_date?: string;
  delivery_cost_cny: number;
  delivery_cost_usd: number;
  delivery_cost_rub?: number;
  rate_cny: number;
  rate_usd: number;
  rate_eur: number;
  invoice_no?: string;
  payment_ref?: string;
  note?: string;
  dt_number?: string;
  vehicle_name?: string;
  plate_number?: string;
  target_warehouse_id?: number;
  inbound_receipt_id?: number;
  created_at?: string;
  items: VehicleItemSchema[];
  items_count: number;
  total_qty: number;
  total_cny: number;
  total_weight_kg?: number;
  total_volume_m3?: number;
  cost_summary?: VehicleCostSummary;
}

export interface VehiclePriceResyncItem {
  cost_item_id: number;
  barcode: string;
  article_seller?: string | null;
  subject?: string | null;
  qty: number;
  old_price_cny: number;
  new_price_cny: number;
  delta_sum_cny: number;
}

export interface VehiclePriceResyncPreview {
  vehicle_status?: string | null;
  total_items: number;
  unlinked_items: number;
  changed_items: number;
  sum_delta_cny: number;
  items: VehiclePriceResyncItem[];
}

export interface VehiclePriceResyncApplyResult {
  applied: number;
}

export interface AvailableItemGroup {
  order_id: number;
  order_number: string;
  factory_name?: string;
  items: AvailableItem[];
}

export interface AvailableItem {
  id: number;
  barcode: string;
  subject?: string;
  article_seller?: string;
  qty: number;
  assigned_qty: number;
  remaining_qty: number;
  price_cny: string;
  box_size?: string;
  pcs_per_box?: number;
  box_detail?: number[] | null;
  mix_group_id?: string | null;
  mix_box_size?: string | null;
  mix_pcs_per_box?: number | null;
  weight_kg?: string;
}

// ─── Vehicle Documents & History ────────────────────────────────────────────

export interface VehicleDocument {
  id: number;
  project_id: number;
  order_no: string;
  doc_type: string;
  filename: string;
  file_size: number;
  note: string | null;
  created_at: string;
}

export interface VehicleStatusHistoryEntry {
  id: number;
  project_id: number;
  order_no: string;
  old_status: string | null;
  new_status: string;
  changed_at: string;
  changed_by: string | null;
  comment: string | null;
}

// ─── Supplier Catalog ─────────────────────────────────────────────────────────

export interface SkuOrderHistoryEntry {
  factory_order_id: number;
  order_number: string;
  order_date: string | null;
  qty: number;
  price_cny: string;
  amount: string;
  vehicle_order_no: string | null;
  vehicle_status: string | null;
  is_delivered: boolean;
}

export interface SupplierCatalogItem {
  barcode: string;
  article_seller: string | null;
  subject: string | null;
  brand: string | null;
  box_size: string | null;
  pcs_per_box: number | null;
  weight_kg: string | null;
  total_qty: number;
  last_price: string;
  avg_price: string;
  total_amount: string;
  orders_count: number;
  last_order_date: string | null;
  delivered_qty: number;
  distributed_qty: number;
  order_history: SkuOrderHistoryEntry[];
}

export interface SupplierCatalogSubjectGroup {
  subject: string;
  sku_count: number;
  total_qty: number;
  total_amount: string;
  delivered_qty: number;
  distributed_qty: number;
  items: SupplierCatalogItem[];
}

export interface SupplierCatalogSummary {
  orders_count: number;
  sku_count: number;
  total_qty: number;
  total_amount: string;
  delivered_amount: string;
}

export interface SupplierCatalogSupplierInfo {
  id: number;
  name: string;
  country: string;
  currency: string;
}

export interface SupplierCatalogResponse {
  supplier: SupplierCatalogSupplierInfo;
  summary: SupplierCatalogSummary;
  subjects: SupplierCatalogSubjectGroup[];
}

// ─── Shipment Matrix (Отгрузочная карта) ─────────────────────────────────────

export interface ShipmentMatrixVehicle {
  order_no: string;
  status: string | null;
  container_type: string | null;
  ship_date: string | null;
}

export interface ShipmentMatrixItem {
  barcode: string;
  article_seller: string | null;
  subject: string | null;
  brand: string | null;
  total_qty: number;
  total_boxes: number;
  pcs_per_box: number | null;
  remaining_qty: number;
  shipped_pct: string;
  really_shipped_qty: number;
  latest_order_date: string | null;
  vehicle_allocations: Record<string, number>;
}

export interface ShipmentMatrixSubjectGroup {
  subject: string;
  items: ShipmentMatrixItem[];
  total_qty: number;
  total_boxes: number;
  remaining_qty: number;
  shipped_pct: string;
}

export interface ShipmentMatrixSummary {
  total_qty: number;
  total_boxes: number;
  shipped_qty: number;
  really_shipped_qty: number;
  remaining_qty: number;
}

export interface ShipmentMatrixResponse {
  supplier: SupplierCatalogSupplierInfo;
  vehicles: ShipmentMatrixVehicle[];
  summary: ShipmentMatrixSummary;
  subjects: ShipmentMatrixSubjectGroup[];
}

// ─── AI Chat ──────────────────────────────────────────────────────────────────

export interface AiConversation {
  id: number;
  project_id: number;
  user_id: number;
  brand: string | null;
  title: string;
  created_at: string;
  updated_at: string | null;
}

export interface AiMessage {
  id: number;
  conversation_id: number;
  role: 'user' | 'assistant';
  content: string;
  files: FileAttachment[] | null;
  tokens_used: number;
  tools_used: string[] | null;
  created_at: string;
}

export interface FileAttachment {
  name: string;
  type: 'excel' | 'image';
  size: number;
  content: string;
}

export interface AiFileUploadResponse {
  name: string;
  type: string;
  size: number;
  content: string;
}

// ─── Counterparties ──────────────────────────────────────────────────────────

export type CounterpartyType =
  | 'SUPPLIER'
  | 'FULFILLMENT'
  | 'CARRIER'
  | 'CUSTOMS_BROKER'
  | 'DESIGNER'
  | 'LEGAL'
  | 'LANDLORD'
  | 'IT_SERVICE'
  | 'MARKETPLACE'
  | 'BANK'
  | 'GOVERNMENT'
  | 'AFFILIATED'
  | 'OTHER';

export type DocType = 'CONTRACT' | 'CERTIFICATE' | 'INVOICE' | 'OTHER';

export interface CounterpartyContacts {
  phone?: string;
  email?: string;
  tg?: string;
  contact_person?: string;
}

export interface CounterpartyStats {
  in_sum: number;
  out_sum: number;
  net: number;
  tx_count: number;
}

export interface CounterpartyListItem {
  id: number;
  inn: string | null;
  name: string;
  primary_type: CounterpartyType;
  secondary_types: CounterpartyType[] | null;
  kpp: string | null;
  contract_number: string | null;
  notes?: string | null;
  contacts?: CounterpartyContacts | null;
  created_by_import: boolean;
  created_at: string | null;
  updated_at: string | null;
  /** Per-period turnover; populated when date_from/date_to are passed. */
  income_rub?: number | null;
  expense_rub?: number | null;
  income_cny?: number | null;
  expense_cny?: number | null;
  tx_count?: number | null;
}

export interface CounterpartyDetail extends CounterpartyListItem {
  stats_rub: CounterpartyStats;
  stats_cny: CounterpartyStats;
  linked_warehouses: { id: number; name: string; warehouse_type?: string }[];
  linked_suppliers: { id: number; name: string }[];
  active_loans: LoanShort[];
  docs_count: number;
}

export type Counterparty = CounterpartyListItem;

export interface CounterpartyCreate {
  inn?: string | null;
  name: string;
  primary_type: CounterpartyType;
  secondary_types?: CounterpartyType[] | null;
  kpp?: string | null;
  contract_number?: string | null;
  notes?: string | null;
  contacts?: CounterpartyContacts | null;
}

export interface CounterpartyUpdate {
  inn?: string | null;
  name?: string;
  primary_type?: CounterpartyType;
  secondary_types?: CounterpartyType[] | null;
  kpp?: string | null;
  contract_number?: string | null;
  notes?: string | null;
  contacts?: CounterpartyContacts | null;
}

export interface CounterpartyDocument {
  id: number;
  counterparty_id: number;
  doc_type: DocType;
  original_filename: string | null;
  file_size: number | null;
  mime_type: string | null;
  uploaded_at: string;
  /** Signed MinIO URL (TTL 300s). Computed by backend per-request. */
  minio_path_signed_url: string;
}

export interface CounterpartyListResponse {
  items: CounterpartyListItem[];
  total: number;
}

export interface CounterpartyTransactionItem {
  id: number;
  date: string;
  account: string;
  currency: string;
  income: number;
  expense: number;
  purpose: string | null;
  event_type2: string | null;
  loan_payment_type: string | null;
  contract_number: string | null;
}

export interface CounterpartyTransactionsResponse {
  items: CounterpartyTransactionItem[];
  total: number;
}

export interface CounterpartyCategorySummary {
  primary_type: CounterpartyType;
  count_cps: number;
  income_rub: number;
  expense_rub: number;
  income_cny: number;
  expense_cny: number;
  tx_count: number;
}

export interface CounterpartySummaryResponse {
  items: CounterpartyCategorySummary[];
  date_from: string | null;
  date_to: string | null;
}

// ─── Loans ───────────────────────────────────────────────────────────────────

export type LoanDirection = 'INCOMING' | 'OUTGOING' | 'AFFILIATED';
export type LoanStatus = 'ACTIVE' | 'CLOSED' | 'DEFAULTED';
export type LoanPaymentType =
  | 'DISBURSEMENT'
  | 'PRINCIPAL_REPAY'
  | 'INTEREST_PAY'
  | 'PENALTY';

export interface LoanShort {
  id: number;
  direction: LoanDirection;
  principal: number;
  currency: string;
  rate?: number | null;
  contract_number?: string;
  contract_date?: string;
  start_date: string;
  maturity_date: string | null;
  status: LoanStatus;
}

export interface Loan extends LoanShort {
  project_id?: number;
  counterparty_id: number;
  rate: number | null;
  contract_number: string;
  contract_date: string;
  notes: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface LoanPayment {
  id: number;
  loan_id: number;
  transaction_id: number | null;
  payment_type: LoanPaymentType;
  amount: number;
  currency: string;
  paid_at: string;
  created_at?: string | null;
}

export interface LoanScheduleSummary {
  principal_paid: number;
  interest_paid: number;
  penalty_paid: number;
  remaining: number;
}

export interface LoanDetail extends Loan {
  counterparty_name?: string | null;
  counterparty_inn?: string | null;
  payments: LoanPayment[];
  schedule_summary: LoanScheduleSummary;
}

export interface LoanDirectionTotals {
  count: number;
  sum_rub: number;
  sum_cny: number;
}

export interface LoanListResponse {
  items: Loan[];
  totals_by_direction: {
    INCOMING: LoanDirectionTotals;
    OUTGOING: LoanDirectionTotals;
    AFFILIATED: LoanDirectionTotals;
  };
  total: number;
}

export interface LoanCreate {
  counterparty_id: number;
  direction: LoanDirection;
  principal: number;
  currency?: string;
  rate?: number | null;
  contract_number: string;
  contract_date: string;
  start_date: string;
  maturity_date?: string | null;
  status?: LoanStatus;
  notes?: string | null;
}

export interface LoanUpdate {
  direction?: LoanDirection;
  principal?: number;
  currency?: string;
  rate?: number | null;
  contract_number?: string;
  contract_date?: string;
  start_date?: string;
  maturity_date?: string | null;
  status?: LoanStatus;
  notes?: string | null;
}

export interface LoanPaymentMatch {
  transaction_id: number;
  payment_type: LoanPaymentType;
  amount: number;
}

// ─── Counterparty Turnovers Report ───────────────────────────────────────────

export interface CounterpartyTurnoverMonth {
  month: string;
  in: number;
  out: number;
  net: number;
}

export interface CounterpartyTurnoverRow {
  counterparty_id: number;
  inn: string | null;
  name: string;
  primary_type: CounterpartyType;
  months: CounterpartyTurnoverMonth[];
  total: {
    in: number;
    out: number;
    net: number;
  };
  tx_count: number;
}

export interface CounterpartyTurnoversResponse {
  rows: CounterpartyTurnoverRow[];
  period: { from: string; to: string };
  currency: string;
}

// ─── WB Goods Returns (отчёт по возвратам и перемещению товаров) ───────────

export type WbGoodsReturnUiState =
  | 'ready_for_pickup'
  | 'in_transit_to_pvz'
  | 'pickup_planned'
  | 'picked_up_pending_receipt'
  | 'received'
  | 'expired'
  | 'picked_without_receipt'
  | 'archived';

export interface WbGoodsReturn {
  id: number;
  srid: string;
  shk_id: string | null;
  sticker_id: string | null;
  barcode: string | null;
  nm_id: number | null;
  subject_name: string | null;
  brand: string | null;
  tech_size: string | null;
  order_id: number | null;
  order_dt: string | null;
  ready_to_return_dt: string | null;
  expired_dt: string | null;
  completed_dt: string | null;
  status: string | null;
  return_type: string | null;
  reason: string | null;
  is_status_active: boolean;
  dst_office_id: number | null;
  dst_office_address: string | null;
  inbound_receipt_id: number | null;
  inbound_receipt_number: string | null;
  inbound_receipt_status: string | null;
  inbound_warehouse_id: number | null;
  inbound_warehouse_name: string | null;
  ui_state: WbGoodsReturnUiState;
  archived_at: string | null;
  article_seller: string | null;
  synced_at: string;
}

export interface WbGoodsReturnSummary {
  ready_for_pickup: number;
  in_transit_to_pvz: number;
  soon_expires: number;
  pickup_planned: number;
  picked_up_pending_receipt: number;
  picked_without_receipt: number;
  expired: number;
  received: number;
  archived: number;
}

export interface WbGoodsReturnPvzGroup {
  dst_office_id: number | null;
  dst_office_address: string | null;
  total: number;
  ready_count: number;
  in_transit_count: number;
  picked_without_receipt_count: number;
  nearest_expired_dt: string | null;
  items: WbGoodsReturn[];
}

export interface WbGoodsReturnsListResponse {
  items: WbGoodsReturn[];
  total: number;
}

export interface CreateReceiptFromReturnsInput {
  warehouse_id: number;
  srids: string[];
  comment?: string;
}

export interface CreateReceiptFromReturnsResult {
  receipt_id: number;
  receipt_number: string;
  warehouse_id: number;
  status: string;
  items_count: number;
  linked_srids: string[];
  /** srid без barcode — залинкованы в приёмку, но без InboundReceiptItem. */
  skipped_srids: string[];
}

export interface ArchiveReturnsInput {
  srids: string[];
}

export interface ArchiveReturnsResult {
  archived_count: number;
  archived_srids: string[];
  /** srid не подошли под критерий picked_without_receipt. */
  skipped_srids: string[];
}

export interface WbGoodsReturnsSyncResult {
  rows_fetched: number;
  rows_upserted: number;
  started_at: string;
  finished_at: string;
}

// ─── Localization Index (КТР / ИРП) ────────────────────────────────────────────

export interface DistrictBreakdown {
  /** Ключ округа (central, south_caucasus, volga, ural, far_east_siberia, northwest, abroad, unknown) */
  district: string;
  /** Локализованное название (ru) */
  label: string;
  local: number;
  non_local: number;
  total: number;
  /** Процент локализации в округе: local / total * 100 */
  local_pct: number;
}

export interface LocalizationSummary {
  /** Средневзвешенный КТР по проекту (Индекс Локализации) */
  localization_index: number;
  /** Средневзвешенный КРП в процентах (Индекс Распределения Продаж) */
  irp_percent: number;
  local_orders: number;
  non_local_orders: number;
  total_orders: number;
  /** Общий процент локализации (local / total * 100) */
  loc_pct_overall: number;
  articles_count: number;
  /** Количество артикулов с КРП = 0 (полностью локальные) */
  articles_local_count: number;
  /** Количество артикулов с КРП > 0 (есть нелокальные продажи) */
  articles_critical_count: number;
  /** Распределение заказов по федеральным округам (агрегат по проекту) */
  district_totals: DistrictBreakdown[];
}

export interface LocalizationSkuRow {
  nm_id: number;
  vendor_code: string;
  title: string;
  subject: string;
  brand: string;
  total: number;
  local: number;
  non_local: number;
  /** Процент локализации артикула: local / total * 100 */
  loc_pct: number;
  /** КТР — коэффициент тарифной разницы */
  ktr: number;
  /** КРП в процентах — коэффициент распределения продаж */
  krp: number;
  /** Вклад в общий индекс: total × ktr */
  contribution: number;
  status: 'excellent' | 'neutral' | 'weak' | 'critical';
  /** Дата первой продажи (ISO YYYY-MM-DD) — UI рассчитывает «Новинка / Активный / Без продаж». */
  first_sale_date?: string | null;
  /** Распределение по округам для этого артикула. Пустой массив = sync wb_orders ещё не запускался. */
  districts: DistrictBreakdown[];
}

export interface LocalizationSyncResult {
  ok: true;
  total_fetched: number;
  inserted: number;
  updated: number;
}

export interface LocalizationDailyPoint {
  /** ISO-дата (YYYY-MM-DD) */
  date: string;
  /** Средневзвешенный КТР за день — Индекс Локализации */
  localization_index: number;
  /** Средневзвешенный КРП в процентах — Индекс Распределения Продаж */
  irp_percent: number;
  /** Объём заказов за день — для tooltip и фильтра «дни без шума» */
  total_orders: number;
  /** Уникальных артикулов за день */
  articles_count: number;
}

// ─── Assembly Drafts (NxM distribution: RF source × WB target) ──────────────────

export interface AssemblyDraftRow {
  nm_id: number;
  barcode: string;
  vendor_code: string;
  /** warehouse_id (str) -> qty (источник, ФФ-склад) */
  src: Record<string, number>;
  /** wb_warehouse_name -> qty (цель, WB-склад) */
  tgt: Record<string, number>;
  /** WB acceptance package type — определяется через POST /warehouse/acceptance-check.
   *  Группирует строки в AssemblyRequest при commit_draft (одна заявка = один тип). */
  package_type?: PackageType;
}

export interface HandedUnitItem {
  nm_id: number;
  barcode: string;
  vendor_code: string;
  qty: number;
}

/** Заявка-юнит, переданная на ФФ: вырезана из rows, заморожена снимком.
 *  Ключ — (ff × wb × pkg); новинки и обычные товары на один склад в одном юните. */
export interface HandedUnit {
  source_ff_id: number;
  target_wb_name: string;
  package_type: PackageType;
  status: string; // "handed" | "draft"
  items: HandedUnitItem[];
}

export interface AssemblyDraftDistribution {
  source_warehouse_ids: number[];
  target_warehouse_names: string[];
  rows: AssemblyDraftRow[];
  pallets_count: number;
  pallet_weight_kg: number;
  /** YYYY-MM-DD or null */
  estimated_ready_date: string | null;
  /** Cold-start доли по WB-складам (name → 0..1). Если задано —
   *  Авто-баланс распределяет qty пропорционально этим долям, не по wbNeed. */
  cold_start_shares?: Record<string, number> | null;
  /** Замороженные заявки-юниты, переданные на ФФ (вырезаны из rows). */
  handed_units?: HandedUnit[];
}

/** Ссылка на заявку-юнит черновика (hand-off / revert / commit).
 *  Ключ — (ff × wb × pkg); новинки и обычные не разделяются. */
export interface AssemblyDraftUnitRef {
  source_ff_id: number;
  target_wb_name: string;
  package_type: PackageType;
}

export interface AssemblyDraft {
  id: number;
  project_id: number;
  name: string;
  distribution: AssemblyDraftDistribution;
  comment: string | null;
  created_at: string;
  updated_at: string;
  /** SKU-новинки в draft (Nomenclature.first_sale_date IS NULL OR ≥ today-14d).
   *  Backend заполняет на чтении draft; commit_draft создаёт по ним отдельные заявки. */
  newcomer_nm_ids?: number[];
}

export interface AssemblyDraftCreate {
  name?: string;
  distribution: AssemblyDraftDistribution;
  comment?: string | null;
}

export interface AssemblyDraftUpdate {
  name?: string | null;
  distribution?: AssemblyDraftDistribution | null;
  comment?: string | null;
}

export interface AssemblyDraftCommitResponse {
  created_request_ids: number[];
  draft_id: number;
}

export interface AssemblyDraftMergeRequest {
  /** IDs of drafts to merge (≥2 distinct values). */
  draft_ids: number[];
}

// Cold-start table — сегмент SKU «новинки + без продаж за 14д с остатком»
export interface ColdStartMainWarehouse {
  district_key: string;
  district_label: string;
  warehouse: string;
  share_pct: number;
}
export interface ColdStartRfWarehouse {
  id: number;
  name: string;
}
export interface ColdStartTableRow {
  nm_id: number;
  article_seller: string | null;
  subject: string | null;
  brand: string | null;
  barcode: string | null;
  rf_qty: number;
  rf_by_warehouse: Record<number, number>;
  wb_qty: number;
  wb_by_warehouse: Record<string, number>;
  in_assembly_total: number;
  asm_by_warehouse: Record<string, number>;
  sales_14d: number;
  revenue_30d: number;
  is_newcomer: boolean;
  allocations: Record<string, number>;
  total_allocated: number;
}
export interface ColdStartTableResponse {
  rows: ColdStartTableRow[];
  main_warehouses: ColdStartMainWarehouse[];
  rf_warehouses: ColdStartRfWarehouse[];
  bench_source: string;
  bench_total_orders: number;
  meta: { min_pack: number; window_days: number; excluded_warehouses: string[] };
}

// WB Acceptance check (POST /warehouse/acceptance-check)
export interface AcceptanceCoefMeta {
  free_days_14: number;
  paid_days_14: number;
  min_coefficient: number | null;
}
export interface AcceptanceFlags {
  warehouse_id: number;
  can_box: boolean;
  can_monopallet: boolean;
  can_supersafe: boolean;
  box_meta?: AcceptanceCoefMeta | null;
  mono_meta?: AcceptanceCoefMeta | null;
  super_meta?: AcceptanceCoefMeta | null;
}
export interface AcceptanceCheckItemRequest {
  nm_id: number;
  barcode: string;
  distribution: Record<string, number>;
}
export interface AcceptanceCheckRequest {
  items: AcceptanceCheckItemRequest[];
}
export interface RedistributionMove {
  nm_id: number;
  barcode: string;
  from_warehouse: string;
  to_warehouse: string | null;
  quantity: number;
  reason: string;
}
export interface AcceptanceCheckSplit {
  package_type: PackageType;
  distribution: Record<string, number>;
  warnings: string[];
}
export interface AcceptanceCheckPerItem {
  nm_id: number;
  barcode: string;
  availability: Record<string, AcceptanceFlags>;
  package_type: PackageType;
  distribution: Record<string, number>;
  splits: AcceptanceCheckSplit[];
  warnings: string[];
}
export interface AcceptanceCheckResponse {
  items: AcceptanceCheckPerItem[];
  moves: RedistributionMove[];
  checked_at: string;
  cache_hit: boolean;
}

// ─── Box multiplicity (кратность коробки) ────────────────────────────────────
// Кратность резолвится ПЕР (товар, ФФ-склад) из принятых машин-поставок:
//   machine → manual per-ФФ → default (box_qty_override) → none.

export type BoxMultiplicitySource = 'machine' | 'manual' | 'default' | 'none';

export interface BoxMultiplicityPerWarehouseRow {
  warehouse_id: number;
  warehouse_name: string;
  box_qty: number | null;                  // резолвнутая кратность для этого ФФ
  box_size: string | null;                 // размер коробки (если из машины)
  source: BoxMultiplicitySource;            // откуда взята кратность
  editable: boolean;                        // false если ФФ машинно-заблокирован
  use_box_multiplicity: boolean;            // per-ФФ флаг «учитывать»
  rf_stock: number;                         // сток на этом ФФ
  machine_order_no: string | null;          // № машины (только source=machine)
  machine_received_at: string | null;       // ISO-date приёмки машины (только source=machine)
  machine_variants: number;                  // иные машины этого ФФ с отличной кратностью
}

export interface BoxMultiplicityRow {
  nm_id: number;
  vendor_code: string | null;
  barcode: string;
  brand: string | null;
  subject: string | null;
  box_qty_override: number | null;          // ручной дефолт SKU (редактируется всегда)
  use_box_multiplicity: boolean;            // SKU-level флаг «учитывать»
  has_machine_data: boolean;                // есть хотя бы один ФФ с source=machine
  has_mixed_ppb: boolean;                    // у товара в истории машин (любой статус) >1 различных ppb
  rf_stock: number;                         // суммарный остаток на ФФ-складах
  in_assembly: number;                      // в активной сборке (PENDING..VEHICLE_ASSIGNED)
  in_transit: number;                       // в пути на WB (SHIPPED)
  wb_stock: number;                         // суммарный остаток на WB-складах
  per_warehouse: BoxMultiplicityPerWarehouseRow[];
}

export interface BoxMultiplicityResponse {
  items: BoxMultiplicityRow[];
  total: number;
}

export interface BoxMultiplicityPatch {
  box_qty_override?: number | null;
  use_box_multiplicity?: boolean;
}

export interface BoxMultiplicityPerWarehousePatch {
  box_qty?: number | null;
  box_size?: string | null;                 // размер коробки per-ФФ (ручной)
  use_box_multiplicity?: boolean;
}

export interface BoxMultiplicityBulkItem {
  barcode: string;
  warehouse_id?: number | null;             // если задан — per-ФФ; иначе SKU-level
  box_qty_override?: number | null;
  box_size?: string | null;                 // размер коробки (per-ФФ)
  use_box_multiplicity?: boolean;
}

export interface BoxMultiplicityBulkRequest {
  items: BoxMultiplicityBulkItem[];
}

export interface BoxMultiplicityBulkResponse {
  updated: BoxMultiplicityRow[];
  not_found: string[];        // barcodes that don't exist in the project
  matched_count: number;      // how many barcodes matched (some may have had no diff)
  locked: string[];           // barcodes пропущены — ФФ машинно-заблокирован
  batch_id: string | null;    // UUID этой вставки — позволяет откатить целиком
}

export interface BoxMultiplicityBatchRevertResponse {
  reverted: number;
  locked_barcodes: string[];
  affected_barcodes: string[];
}

export interface BoxMultiplicityBatchSummary {
  batch_id: string;
  created_at: string;     // ISO datetime
  changes_count: number;  // строк журнала в batch
  affected_barcodes: number;  // уникальных barcode
}

export interface BoxMultiplicityBatchListResponse {
  items: BoxMultiplicityBatchSummary[];
}

// Drill-down: история снабжения одного SKU (второй уровень под артикулом).
export interface BoxMultiplicitySourceRow {
  source_type: 'vehicle' | 'factory';      // машина либо заказ на фабрику
  order_no: string;                         // № машины / № заказа
  warehouse_name: string | null;            // ФФ-склад назначения (только vehicle)
  qty: number;
  box_qty: number | null;                   // кратность из этой поставки
  box_size: string | null;                  // размер коробки
  date: string | null;                      // ISO-date — прибытие машины / дата заказа
  status: string | null;                    // статус машины/заказа
  accepted: boolean;                         // машина принята (не «в пути»)
}

export interface BoxMultiplicitySourcesResponse {
  items: BoxMultiplicitySourceRow[];
}

// История изменений кратности/размера одного SKU (второй уровень под артикулом).
export interface BoxMultiplicityChangeRow {
  id: number;
  warehouse_id: number | null;              // null = изменение SKU-уровня
  warehouse_name: string | null;            // имя ФФ (для per-ФФ записи)
  field: string;                            // box_qty_override | box_qty | box_size | use_box_multiplicity
  old_value: string | null;                 // null = было «не задано»
  new_value: string | null;                 // null = стало «не задано»
  change_source: string;                    // manual | bulk | revert
  created_at: string;                       // ISO datetime
}

export interface BoxMultiplicityChangesResponse {
  items: BoxMultiplicityChangeRow[];
}

// ─── Warehouse Speed Priority (WB delivery speed map) ────────────────────────
// Endpoints: /api/v1/warehouse/speed/*
// UI: (main)/p/[slug]/warehouse/speed/page.tsx

export interface SpeedMeta {
  version: string;
  source: string;
  cities_count: number;
  okrug_keys: string[];
}

export interface OkrugCount {
  warehouse_name: string;
  cities_count: number;
}

export interface OkrugInfo {
  okrug_key: string;
  okrug_label: string;
  anchors_top: OkrugCount[];
  stealers_top: OkrugCount[];
}

export type BasketWarningSeverity = 'info' | 'warning' | 'error';

export interface BasketWarning {
  kind: string;
  severity: BasketWarningSeverity;
  okrug: string;
  okrug_label: string;
  message: string;
  stealer: string | null;
  suggested_anchors: string[];
}

export interface OkrugBasketStats {
  okrug_key: string;
  okrug_label: string;
  anchors_in_basket: string[];
  stealers_in_basket: string[];
  cities_covered_locally: number;
  cities_total: number;
}

export interface CityPriorityRow {
  warehouse_name: string;
  hours: number;
  /** ФО самого склада (например 'volga' для Казани). Нужно для отличия
   *  anchor (own_okrug === city.okrug_key) от stealer (другой ФО). */
  own_okrug: string;
}

export interface CitySpeedDTO {
  city: string;
  okrug_key: string;
  okrug_label: string;
  priorities: CityPriorityRow[];
}

export interface BasketEvaluation {
  loaded_warehouses: string[];
  realistic_ceiling_pct: number;
  cities_local: number;
  cities_stealer: number;
  cities_uncovered: number;
  cities_total: number;
  warnings: BasketWarning[];
  per_okrug: OkrugBasketStats[];
  cities: CitySpeedDTO[] | null;
}

// ─── Fulfillment integration (skladbot, wmscelicom, migfull) ───────────────

export type FulfillmentProviderId = 'skladbot' | 'wmscelicom' | 'migfull';

export interface FulfillmentStatus {
  connected: boolean;
  provider: string | null;
  /** "***xxxx" — сам токен назад не отдаётся */
  key_preview: string | null;
  customer_id: number | null;
  customer_name: string | null;
  token_expires_at: string | null;
  /** wmscelicom: адрес клиентского инстанса, на который ходим */
  api_base_url: string | null;
  /** migfull: GUID кабинета */
  tenant_guid: string | null;
  last_sync_at: string | null;
}

export interface FulfillmentConnectPayload {
  provider: FulfillmentProviderId;
  token: string;
  /** wmscelicom: адрес инстанса {client}.wmscelicom.ru */
  base_url?: string | null;
  /** migfull: GUID кабинета */
  tenant_guid?: string | null;
  /** skladbot: id кабинета. Обязателен для FF-operator токена (видит >1 клиента) */
  customer_id?: number | null;
}

export interface FfSyncResult {
  stocks_synced: number;
  requests_synced: number;
  unmatched_barcodes: number;
  assemblies_marked_ready?: number;
  inbound_receipts_accepted?: number;
  synced_at: string;
}

export interface FfStockRow {
  barcode: string;
  name: string | null;
  vendor_code: string | null;
  nomenclature_id: number | null;
  /** наш артикул (если товар сматчен) */
  article_seller: string | null;
  /** предмет из номенклатуры (если сматчен) */
  subject: string | null;
  /** бренд из номенклатуры (если сматчен) */
  brand: string | null;
  ff_good: number;
  ff_reserve: number;
  ff_defect: number;
  ff_nominal: number;
  /** из ff_good пришло коробами (в штуках россыпи) */
  ff_box_units: number;
  /** сколько коробов годного сведено в этот товар */
  ff_box_count: number;
  our_quantity: number;
  our_defect: number;
  /** ff_good - our_quantity */
  diff: number;
}

export interface FfStockTotals {
  ff_good: number;
  ff_reserve: number;
  ff_defect: number;
  /** сколько штук годного пришло коробами */
  ff_box_units: number;
  our_quantity: number;
  diff: number;
  /** строк ФФ без нашей номенклатуры */
  unmatched: number;
}

export interface FfStocksResponse {
  rows: FfStockRow[];
  totals: FfStockTotals;
  synced_at: string | null;
  /** distinct предметы для фильтра */
  subjects: string[];
  /** distinct бренды для фильтра */
  brands: string[];
}

/** Строка сопоставления короб→россыпь (авто-вывод при синке) */
export interface FfBoxPack {
  /** ШК короба (ITF14) */
  box_barcode: string;
  /** ШК россыпи (EAN13); null — короб ещё не сопоставлен */
  base_barcode: string | null;
  /** штук россыпи в коробе («короб N шт.» из названия) */
  units_per_box: number;
  /** название коробной карточки у ФФ */
  name: string | null;
  nomenclature_id: number | null;
  /** наш артикул (если сматчен) */
  article_seller: string | null;
  subject: string | null;
  /** остаток в коробах */
  box_qty: number;
  /** = box_qty × units_per_box (в штуках россыпи) */
  units_qty: number;
  /** сматчен ли короб с нашей номенклатурой */
  matched: boolean;
  /** auto — авто-вывод | manual — ручной override | unmapped — не сопоставлен */
  source: 'auto' | 'manual' | 'unmapped';
}

export interface FfBoxOverridePayload {
  nomenclature_id: number;
  units_per_box: number;
}

/** Кандидат номенклатуры для ручной привязки короба */
export interface FfNomenclatureOption {
  id: number;
  barcode: string;
  article_seller: string | null;
  subject: string | null;
}

export type FfRequestKind = 'assembly' | 'inbound';

/** Нормализованный высокоуровневый статус ФФ-заявки (бэкенд: _ff_status_code) */
export type FfStatusCode = 'assembling' | 'ready' | 'shipped' | 'expected' | 'accepted' | 'archived' | 'expired';

export interface FfRequestRow {
  id: number;
  external_id: string;
  number: string | null;
  kind: 'assembly' | 'inbound' | 'other';
  type_name: string | null;
  status: string | null;
  stage_code: string | null;
  stage_title: string | null;
  is_completed: boolean;
  archived: boolean;
  expired: boolean;
  /** Нормализованный статус ФФ: assembling | ready | shipped | expected | accepted | archived | expired */
  ff_status: FfStatusCode;
  /** заявлено всего, шт (skladbot — из деталки) */
  total_qty: number | null;
  /** кол-во в штуках россыпи (пересчёт коробов, migfull); null — без коробов/не разрезолвлено */
  total_qty_units: number | null;
  /** склад отгрузки МП («Склад МП» / shipped_target) */
  dest_warehouse: string | null;
  external_created_at: string | null;
  synced_at: string;
  assembly_request_id: number | null;
  inbound_receipt_id: number | null;
  /** Обогащение по связанному документу (заполняет сервис) */
  linked_number: string | null;
  linked_status: string | null;
  /** Локальный архив (наша пометка, не статус провайдера) */
  local_archived: boolean;
  local_archived_at: string | null;
}

/** Событие истории синхронизации: синк зафиксировал смену стадии/статуса заявки ФФ */
export interface FfStatusEvent {
  id: number;
  fulfillment_request_id: number;
  external_id: string;
  number: string | null;
  kind: 'assembly' | 'inbound' | 'other';
  provider: string;
  /** created — заявка впервые появилась; changed — статус/стадия изменились */
  event_type: 'created' | 'changed';
  old_status: string | null;
  new_status: string | null;
  old_stage_code: string | null;
  new_stage_code: string | null;
  old_stage_title: string | null;
  new_stage_title: string | null;
  old_is_completed: boolean | null;
  new_is_completed: boolean | null;
  old_archived: boolean | null;
  new_archived: boolean | null;
  changed_at: string;
  /** Обогащение из текущей заявки ФФ */
  dest_warehouse?: string | null;
  total_qty?: number | null;
  linked_number?: string | null;
}

/** Наша заявка сборки (ASM-xxx) без привязанной ФФ-заявки — для реверс-линка из заявки ФФ. */
export interface FfUnlinkedAssembly {
  id: number;
  number: string;
  status: string;
  /** распознанные бренды позиций через запятую (или null) */
  brands: string | null;
  /** сумма количеств позиций, шт */
  total_qty: number;
  /** склад сдачи МП (FBO warehouse_name или ручной) */
  dest_warehouse: string | null;
  estimated_ready_date: string | null;
  created_at: string;
}

/** Один прогон синхронизации ФФ-склада (журнал sync_log) — вкладка «ФФ синхронизация». */
export interface FfSyncRun {
  id: number;
  /** skladbot | wmscelicom | migfull */
  service: string;
  /** RUNNING | OK | ERROR */
  status: string;
  started_at: string;
  finished_at: string | null;
  /** позиций остатков */
  stocks_synced: number;
  /** заявок */
  requests_synced: number;
  duration_seconds: number | null;
  error_msg: string | null;
}

export interface FfRequestDetailProduct {
  barcode: string | null;
  vendor_code: string | null;
  name: string | null;
  nomenclature_id: number | null;
  /** наш артикул (если товар сматчен) */
  article_seller: string | null;
  /** заявлено; для короба — уже в штуках россыпи (×units_per_box) */
  qty: number;
  accepted_qty: number;
  delivery_qty: number;
  defect_qty: number;
  /** штук россыпи в коробе (1 — позиция россыпью) */
  units_per_box: number;
  /** сколько коробов (если позиция коробом), иначе 0 */
  box_qty: number;
  /** кол-во в связанном нашем документе; null — связи нет */
  our_qty: number | null;
  color: string | null;
  size: string | null;
  comment: string | null;
  image: string | null;
}

/** Строка расхождения состава: ФФ-заявка vs наш документ (по barcode) */
export interface FfMatchRow {
  barcode: string;
  article_seller: string | null;
  /** название со стороны ФФ (если позиция там есть) */
  name: string | null;
  ff_qty: number;
  our_qty: number;
  /** ff_qty - our_qty */
  diff: number;
}

/** Итог сверки состава ФФ-заявки со связанным нашим документом */
export interface FfRequestMatch {
  matched: boolean;
  ff_positions: number;
  our_positions: number;
  ff_total: number;
  our_total: number;
  mismatches: FfMatchRow[];
}

export interface FfRequestStageLog {
  stage: string | null;
  executor: string | null;
  /** формат провайдера «10.06.2026 17:53:35» — показывать как есть */
  created_at: string | null;
  spent_time: string | null;
}

/** Динамическое поле заявки (Маркетплейс, Склад МП, Дата забора, ...) */
export interface FfRequestFieldValue {
  name: string | null;
  field: string | null;
  value: string | null;
}

/** Деталка заявки ФФ: шапка списочной строки + живой состав от провайдера */
export interface FfRequestDetail extends FfRequestRow {
  comment: string | null;
  customer_name: string | null;
  executor: string | null;
  creator: string | null;
  stage_description: string | null;
  total_qty: number;
  total_accepted: number;
  products: FfRequestDetailProduct[];
  stage_logs: FfRequestStageLog[];
  fields: FfRequestFieldValue[];
  /** сверка состава со связанным нашим документом; null — связи нет */
  match: FfRequestMatch | null;
}

/* ─── Сводная страница «Заявки ФФ» (все склады с интеграцией) ─── */

export interface FfIntegratedWarehouse {
  warehouse_id: number;
  warehouse_name: string;
  /** skladbot | wmscelicom */
  provider: string;
  /** человекочитаемое имя провайдера */
  provider_label: string;
  last_sync_at: string | null;
  requests_total: number;
  /** активные несвязанные заявки kind=assembly */
  requests_unlinked: number;
}

/** Кандидат авто-мэтчинга ФФ-заявки к нашей заявке на сборку */
export interface FfMatchSuggestion {
  assembly_request_id: number;
  number: string;
  status: AssemblyStatus;
  created_at: string;
  total_qty: number;
  /** 0..100 — уверенность эвристики */
  score: number;
  /** объяснение: «дата ±1 дн», «пересечение ШК 80%» */
  reason: string;
}

export interface FfOverviewRequestRow extends FfRequestRow {
  warehouse_id: number;
  warehouse_name: string;
  provider: string;
  /** топ-кандидаты для несвязанных активных заявок (иначе []) */
  suggestions: FfMatchSuggestion[];
}

export interface FfOverviewResponse {
  warehouses: FfIntegratedWarehouse[];
  requests: FfOverviewRequestRow[];
}

export interface FfLinkPayload {
  assembly_request_id?: number | null;
  inbound_receipt_id?: number | null;
}

/** Кандидат для связывания ФФ-заявки с нашим документом (модал «Связать») */
export interface FfLinkCandidate {
  doc_id: number;
  number: string;
  status: string;
  created_at: string | null;
  /** всего, шт */
  total_qty: number;
  /** только assembly */
  fbo_supply_number: string | null;
  /** только assembly */
  dest_warehouse: string | null;
  /** 0..100 — уверенность эвристики «похож по наполнению»; null — не похож */
  score: number | null;
  /** объяснение score: «ШК 75%, кол-во ±10%, дата ±1 дн» */
  reason: string | null;
  /** склад сдачи кандидата совпал со складом сдачи ФФ-заявки (нормализованно) */
  warehouse_match: boolean;
}

export interface FfLinkCandidatesResponse {
  kind: FfRequestKind;
  ff_number: string | null;
  ff_total_qty: number | null;
  /** склад сдачи самой ФФ-заявки (для фильтра кандидатов по складу) */
  ff_dest_warehouse: string | null;
  /** false — состав ФФ-заявки недоступен, score не рассчитан */
  composition_available: boolean;
  candidates: FfLinkCandidate[];
}

/** Итог создания заявки на сборку из ФФ-заявки */
export interface FfCreateAssemblyResult {
  /** ФФ-заявка уже связана с созданной заявкой */
  request: FfRequestRow;
  assembly_request_id: number;
  assembly_number: string;
  items_created: number;
  /** ШК из состава ФФ, не найденные в номенклатуре (пропущены) */
  skipped_barcodes: string[];
}

/** Опция select-поля формы создания заявки ФФ (id + имя). */
export interface FfFormOption {
  id: number;
  name: string;
}

/** Тип поставки: value — строковый ключ (straight/cross_dock). */
export interface FfDeliveryTypeOption {
  value: string;
  name: string;
}

/** Справочники для диалога создания заявки 851 (живой GET /v1/requests/form-data). */
export interface FfCreateFormResponse {
  marketplace_id: number;
  marketplace_name: string;
  /** Склады МП для marketplace_id (id отправляется в payload) */
  warehouses: FfFormOption[];
  delivery_types: FfDeliveryTypeOption[];
  /** Совпадение со складом WB заявки, иначе null */
  suggested_warehouse_id?: number | null;
  /** Склад WB заявки (для подсказки в UI) */
  suggested_warehouse_hint?: string | null;
  collection_date: string;
  unloading_date: string;
  delivery_type: string;
}

/** Создание заявки ФФ из нашей сборки (provider-agnostic).
 *  skladbot («Доставка на склад МП», 851): склад МП + даты обязательны.
 *  wmscelicom («Целиком»): самовывоз — склад/даты не нужны, шлём только comment. */
export interface FfCreateRequestPayload {
  /** skladbot: id склада МП (utils.marketplaceWarehouses[].value); wms — не нужен */
  marketplace_warehouse_id?: number | null;
  /** skladbot: дата забора груза (YYYY-MM-DD) */
  collection_date?: string | null;
  /** skladbot: дата выгрузки на склад МП (YYYY-MM-DD) */
  unloading_date?: string | null;
  /** skladbot: id маркетплейса (utils.marketplaces[].value); Wildberries=1 */
  marketplace_id?: number;
  /** skladbot: straight — прямая, cross_dock — транзит */
  delivery_type?: 'straight' | 'cross_dock';
  comment?: string | null;
  notify?: boolean;
}

/** Итог отправки нашей заявки на сборку в ФФ (создан реальный заказ у skladbot). */
export interface FfPushAssemblyResult {
  /** Зеркало созданной ФФ-заявки (уже связано со сборкой) */
  request: FfRequestRow;
  external_id: string;
  ff_number: string | null;
  items_sent: number;
  total_qty: number;
  /** ШК без остатка/карточки у ФФ — не отправлены */
  skipped_barcodes: string[];
}

/** ШК, по которому у ФФ доступно меньше, чем нужно по сборке. */
export interface FfDeficitItem {
  barcode: string;
  needed: number;
  available: number;
}

/** Массовое создание заявок ФФ из нескольких сборок (склад МП/дата выгрузки — по каждой). */
export interface FfBulkCreateRequestPayload {
  assembly_request_ids: number[];
  /** Дата забора груза — общая для всех (YYYY-MM-DD) */
  collection_date: string;
  marketplace_id?: number;
  delivery_type?: 'straight' | 'cross_dock';
  comment?: string | null;
  notify?: boolean;
}

/** Итог push одной сборки в батче. */
export interface FfBulkCreateAssemblyResult {
  assembly_request_id: number;
  assembly_number: string;
  status: 'created' | 'deficit' | 'no_warehouse' | 'already_linked' | 'empty' | 'error';
  ff_number: string | null;
  external_id: string | null;
  items_sent: number;
  total_qty: number;
  /** подобранный склад МП (для созданных) */
  dest_warehouse: string | null;
  deficit: FfDeficitItem[];
  message: string | null;
}

export interface FfBulkCreateResult {
  results: FfBulkCreateAssemblyResult[];
  created_count: number;
  /** всё, что не created */
  failed_count: number;
}

export interface FfBulkArchivePayload {
  ff_request_ids: number[];
  /** true — в архив, false — вернуть из архива */
  archived: boolean;
}

export interface FfBulkArchiveResult {
  updated: number;
}
