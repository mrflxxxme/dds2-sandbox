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
}

export interface DutyRule {
  id: number;
  category: string;
  duty_rate: number;
  vat_included: boolean;
  basis: string;
}

export interface CostOrder {
  id: number;
  order_no: string;
  project_id?: number;
  invoice_no?: string;
  dt_number?: string;
  ship_date?: string;
  delivery_cost?: number;
  rate_cny?: number;
  rate_usd?: number;
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
}

export interface FunnelGroupRow extends Omit<FunnelSkuRow, 'nm_id' | 'vendor_code'> {
    brand?: string;
    subject?: string;
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
}

// ─── Common ──────────────────────────────────────────────────────────────────

export interface MessageResponse {
  message: string;
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
  cost_price?: number;
  updated_at?: string;
  reserved: number;
  available: number;
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
  total: number;
  avg_cost: number;
  avg_daily_revenue: number;
  avg_daily_profit: number;
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
  assembly_request_id?: number;
  assembly_request_number?: string;
  assembly_request_status?: string;
  synced_at?: string;
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

export interface FboSyncResult {
  synced: number;
  created: number;
  updated: number;
  errors: number;
  message: string;
}

// ─── Assembly Requests ────────────────────────────────────────────────────

export type AssemblyStatus = 'PENDING' | 'IN_PROGRESS' | 'READY' | 'VEHICLE_ASSIGNED' | 'SHIPPED' | 'DELIVERED' | 'CANCELLED';

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
  items: AssemblyRequestItem[];
  created_at: string;
  updated_at: string;
}

export interface AssemblyListResponse {
  items: AssemblyRequest[];
  total: number;
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
  items: { barcode: string; quantity: number }[];
}

export interface AssemblyRequestUpdate {
  estimated_ready_date?: string | null;
  pallets_count?: number;
  pallet_weight_kg?: number;
  comment?: string | null;
  wb_fbo_supply_id?: number | null;
  wb_warehouse_name_manual?: string | null;
  items?: { barcode: string; quantity: number }[];
  pickup_cost?: number;
  vehicle_info?: string;
  vehicle_brand?: string;
  driver_phone?: string;
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
  remaining_qty?: number;
}

export interface FactoryOrder {
  id: number;
  project_id: number;
  order_number: string;
  factory_name?: string;
  order_date?: string;
  expected_ready_date?: string;
  total_cny?: number;
  note?: string;
  created_at?: string;
  updated_at?: string;
  items?: FactoryOrderItem[];
}

export interface FactoryOrderCreate {
  order_number: string;
  factory_name?: string;
  order_date?: string;
  expected_ready_date?: string;
  total_cny?: number;
  note?: string;
  items?: { barcode: string; subject?: string; article_seller?: string; qty: number; price_cny: number; note?: string }[];
}

export type VehicleStatus = 'FORMING' | 'SHIPPED' | 'CUSTOMS' | 'DELIVERED';

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

export interface VehicleCreate {
  order_no: string;
  container_type?: string;
  delivery_cost_cny?: number;
  delivery_cost_usd?: number;
  rate_cny?: number;
  rate_usd?: number;
  rate_eur?: number;
  ship_date?: string;
  invoice_no?: string;
  target_warehouse_id?: number;
  note?: string;
}

export interface VehicleUpdateData {
  container_type?: string;
  delivery_cost_cny?: number;
  delivery_cost_usd?: number;
  rate_cny?: number;
  rate_usd?: number;
  rate_eur?: number;
  ship_date?: string;
  invoice_no?: string;
  dt_number?: string;
  target_warehouse_id?: number;
  note?: string;
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
  box_size?: string;
  pcs_per_box?: number;
  factory_order_number?: string;
}

export interface VehicleSchema {
  id: number;
  order_no: string;
  status?: VehicleStatus;
  transport_type?: string;
  container_type?: string;
  ship_date?: string;
  actual_ship_date?: string;
  actual_arrival_date?: string;
  estimated_arrival_date?: string;
  delivery_cost_cny: number;
  delivery_cost_usd: number;
  rate_cny: number;
  rate_usd: number;
  rate_eur: number;
  invoice_no?: string;
  note?: string;
  dt_number?: string;
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
  weight_kg?: string;
}
