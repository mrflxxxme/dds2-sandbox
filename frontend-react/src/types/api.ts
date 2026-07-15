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
  sort_order?: number;
  is_cogs?: boolean;
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
  service: 'wb' | 'wb_advert' | 'wb_analytics' | 'ozon' | (string & {});
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
  started_at?: string;
  finished_at?: string;
  status: string;
  rows_fetched: number;
  rows_inserted: number;
  error_msg?: string | null;
}

export interface FakturaStatus {
  configured: boolean;
  login?: string | null;
  last_sync_at?: string | null;
  last_run?: SyncLog | null;
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
  stock_days_left?: number;  // запас по общему остатку (WB + наши склады)
  wb_stock_days_left?: number;  // запас ТОЛЬКО по остатку на WB
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
    size?: string;
    subcategory?: string;
    nm_id?: number;
    vendor_code?: string;
    children?: FunnelGroupRow[];
}

export interface FunnelColorsResponse {
  colors: string[];
}

// ─── Ценообразование (наценка по артикулам) ──────────────────────────────
export interface PricingRow {
  nm_id: number;
  vendor_code: string | null;
  brand: string | null;
  subject: string | null;
  category: string;
  size: string;
  current_price: number | null;
  base_price: number | null;
  discount: number | null;
  cost_price: number | null;
  has_cost: boolean;
  has_price: boolean;
  markup_coef: number | null;
  markup_pct: number | null;
  cost_share_pct: number | null;
  spp_rate: number;
  buyer_price: number | null;
  orders_count: number;
  revenue: number;
  wb_expenses: number;
  adv_sum: number;
  tax: number;
  cost_total: number;
  profit: number;
  margin_pct: number;
  net_markup_pct: number | null;
  wb_stock: number;
  own_stock: number;
  assembly_stock: number;
  transit_stock: number;
  total_stock: number;
  is_new: boolean;
  stock_value_cost: number | null;
  stock_potential_profit: number | null;
  stock_potential_revenue: number | null;
  days_left: number | null;
  sales_per_month: number | null;
  anomaly: string | null;
  breakeven_price: number | null;
  breakeven_with_adv: number | null;
  safety_margin_pct: number | null;
  drr: number;
  cr: number;
  ctr: number;
  cpc: number;
  adv_views: number;
  adv_clicks: number;
  gmroi: number | null;
  sell_through_pct: number | null;
  elasticity: number | null;
  elasticity_label: string;
  optimal_price: number | null;
  abc: string | null;
  recommendation: string;
  imt_id: number | null;
  sklejka: string;
  rev_share_pct: number | null;
  adv_share_pct: number | null;
  sklejka_role: string;
}

export interface PricingGroup {
  category: string;
  articles: number;
  priced_articles: number;
  imt_id?: number | null;
  advertised_variants?: number;
  converting_variants?: number;
  markup_coef: number | null;
  markup_pct: number | null;
  cost_share_pct: number | null;
  revenue: number;
  profit: number;
  cost_total: number;
  wb_expenses: number;
  margin_pct: number;
  adv_sum: number;
  drr: number;
  ctr: number;
  cpc: number;
  adv_views: number;
  adv_clicks: number;
  wb_stock: number;
  own_stock: number;
  assembly_stock: number;
  transit_stock: number;
  total_stock: number;
  stock_value_cost: number;
  children: PricingRow[];
  subgroups: PricingGroup[];
}

export interface PricingSummary {
  total_articles: number;
  priced_articles: number;
  costed_articles: number;
  revenue: number;
  profit: number;
  cost_total: number;
  wb_expenses: number;
  markup_pct: number | null;
  cost_share_pct: number | null;
  margin_pct: number;
  wb_stock_units: number;
  total_stock_units: number;
  stock_value_cost: number;
  anomalies: number;
}

export interface PricingResponse {
  group_by: string;
  data_groups: PricingGroup[];
  data_rows: PricingRow[];
  summary: PricingSummary;
  price_synced_at: string | null;
  has_bdr: boolean;
}

export interface PricingAiResponse {
  html: string;
  model: string;
  articles_analyzed: number;
  items_sent?: number;
  sklejki_sent?: number;
  singles_sent?: number;
  dynamics_window?: Record<string, string> | null;
  generated_at?: string;
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
  measurements_notify_enabled: boolean;
  /** Отдельный opt-in под алерты «Расхождение поставок ФФ» (раз в 2ч). */
  supply_notify_enabled: boolean;
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

export interface ExpenseTypeGroup {
  type: string | null;       // counterparty primary_type (null = «Без контрагента»)
  type_label: string;        // localized label
  value: number;
  count: number;
  categories: ExpenseCategoryPie[];  // level-2 breakdown within the type
}

export interface DailyIncomeByType {
  date: string;
  marketplace: number;
  financing: number;
  other: number;
}

export interface IncomeTypeSlice {
  key: string;   // 'marketplace' | 'financing' | 'other'
  name: string;  // localized label
  value: number;
  count: number;
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
  daily_income_by_type: DailyIncomeByType[];
  income_by_type: IncomeTypeSlice[];
  expense_by_category: ExpenseCategoryPie[];
  expense_by_type: ExpenseTypeGroup[];
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

// ─── SKU Valuation analytics (FIFO / moving / lifetime) ─────────────────────

/** Valuation method keys returned/accepted by the backend. */
export type ValuationMethod = 'lifetime_avg' | 'fifo' | 'moving_avg';

/** Per-method numeric map (eff_now / on_hand_value). */
export interface ValuationMethodMap {
  lifetime_avg: number;
  fifo: number;
  moving_avg: number;
}

/** One batch in the SKU ledger (received / consumed / remaining). */
export interface CostLayer {
  order_no: string;
  avail_date: string | null;
  qty: number;
  remaining: number;
  consumed: number;
  unit_cost: number;
  arrival_known: boolean;
}

/** Per-month COGS for a SKU under all three methods. */
export interface MonthlyCogs {
  month: string;
  qty: number; // net = sold − returned
  sold: number;
  returned: number;
  cogs_fifo: number;
  cogs_avg: number;
  cogs_moving: number;
}

/** Full per-SKU valuation analytics for the cost page drill-down. */
export interface SkuValuation {
  sku: string;
  barcode: string | null;
  article_wb: number | null;
  brand: string;
  subject: string;
  lifetime_avg: number;
  eff_now: ValuationMethodMap;
  on_hand_qty: number;
  on_hand_value: ValuationMethodMap;
  total_received: number;
  total_sold: number;
  is_estimated: boolean;
  warnings: string[];
  ledger: CostLayer[];
  monthly: MonthlyCogs[];
}

/** One SKU in the project-wide distortion summary. */
export interface ValuationSummaryRow {
  sku: string;
  barcode: string | null;
  article_wb: number | null;
  brand: string;
  subject: string;
  qty: number;
  cogs_current: number;
  cogs_fifo: number;
  distortion: number; // cogs_fifo − cogs_current (per window)
  is_estimated: boolean;
}

/** One opening-balance row (manual stock seed for valuation). */
export interface OpeningBalanceItem {
  barcode: string;
  qty: number;
  unit_cost: number;
  as_of_date?: string | null;
  note?: string | null;
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

export interface ProductSubcategory {
  id: number;
  name: string;
  color: string;
}

export interface DetectedSize {
  raw_size: string;
  display_name: string;
  count: number;
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
  subject: string;
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

// ─── Stock Analytics (GET /reports/stock_analytics — «Аналитика остатков») ─────

export interface StockAnalyticsArticle {
  nm_id: number;
  vendor_code: string;
  subject: string;
  brand: string;
  orders_30d: number;
  trend_pct: number;
  avg_daily: number;
  stocks_wb: number;
  /** Запас в днях по остатку выбранного mode (wb / wb_rf / wb_rf_transit / wb_assembly_transit). */
  days_left: number;
  traffic_light: string;
  forecast: number[];
  /** Свободный остаток на ФФ-складах (mode ≠ wb). */
  stocks_rf?: number;
  in_assembly?: number;
  in_transit?: number;
  wb_buyout_pct?: number;
  /** Реализация БДР за trend_days, ₽. */
  revenue_bdr?: number;
  margin_pct?: number | null;
  rf_avg_days?: number | null;
  first_sale_date?: string | null;
}

export interface StockAnalyticsResponse {
  articles: StockAnalyticsArticle[];
  dates: string[];
  total_articles: number;
  orders_30d: number;
  avg_daily: number;
  data_date: string;
  most_critical: { article: string; days_left: number } | null;
  traffic_light: { red: number; orange: number; yellow: number; green: number };
  subjects: string[];
  brands: string[];
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
  return_processed_at?: string | null;
  return_type?: 'GOODS' | 'DEFECT' | 'UTILIZED' | 'MIXED' | null;
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

export type AssemblyStatus = 'PENDING' | 'PRE_DISTRIBUTED' | 'IN_PROGRESS' | 'READY' | 'VEHICLE_ASSIGNED' | 'SHIPPED' | 'DELIVERED' | 'RETURNED' | 'CLOSED' | 'CANCELLED';

export type PackageType = 'BOX' | 'MONOPALLET' | 'SUPERSAFE';

export interface AssemblyRequestItem {
  id: number;
  nomenclature_id: number;
  barcode: string;
  quantity: number;
  product_name?: string;
  article?: string | null;
  brand?: string;
  stock_quantity: number;
}

/** Содержимое одного SKU внутри паллеты: целые короба + хвост-россыпь. */
export interface BoxContent {
  barcode: string;
  box_count: number;
  loose_units: number;
}

/** Одна физическая паллета раскладки: номер + короба/россыпь по SKU. */
export interface PalletBox {
  pallet_no: number;
  boxes: BoxContent[];
}

/** Тело PATCH .../pallet-manifest. pallets=null → сброс к «авто». */
export interface PalletManifestUpdate {
  pallets: PalletBox[] | null;
}

// ─── WB portal supply (реплей кабинета) ──────────────────────────────────────

export type WbSupplySyncStatus =
  | 'NONE'
  | 'DRAFT'
  | 'PREORDER'
  | 'BOOKED'
  | 'BOXED'
  | 'PASSED'
  | 'ERROR';

export interface WbBoxItem {
  barcode: string;
  quantity: number;
}

export interface WbBox {
  boxcode: string | null;
  items: WbBoxItem[];
}

export interface WbSupplyState {
  assembly_request_id: number;
  sync_status: WbSupplySyncStatus;
  warehouse_id_wb: number | null;
  box_type_id: number | null;
  draft_id: string | null;
  preorder_id: number | null;
  supply_id: number | null;
  barcode_id: number | null;
  last_error: string | null;
  last_synced_at: string | null;
  wb_supply_state: string | null;
  wb_supply_state_id: number | null;
  wb_state_synced_at: string | null;
  // Дата забронированного слота сдачи + текст кабинетных ошибок поставки.
  supply_date: string | null;
  reject_reason: string | null;
  boxes: WbBox[];
  pass_driver_first: string | null;
  pass_driver_last: string | null;
  pass_driver_phone: string | null;
  pass_car_model: string | null;
  pass_car_number: string | null;
  pass_pallets: number | null;
  // Зеркало назначенной машины заявки (read-only) — для префилла пропуска и
  // подсветки расхождений «машина заявки ↔ WB-пропуск» (F3).
  assembly_vehicle_info: string | null;
  assembly_vehicle_brand: string | null;
  assembly_driver_phone: string | null;
  // Локальное число паллет заявки — для баннера «паллеты ≠ WB» (F2).
  assembly_pallets_count: number | null;
}

// Компактная WB-сводка в строке списка заявок (F1/F2).
export interface WbSupplyStateBrief {
  sync_status: WbSupplySyncStatus;
  wb_supply_state: string | null;
  supply_id: number | null;
  preorder_id: number | null;
  pass_pallets: number | null;
  // Данные пропуска — для префилла модалки «Назначить машину» (F1).
  pass_driver_first: string | null;
  pass_driver_last: string | null;
  pass_driver_phone: string | null;
  pass_car_model: string | null;
  pass_car_number: string | null;
  // Дата брони слота WB — колонка «Дата брони WB» в списке сборок.
  supply_date: string | null;
  wb_state_synced_at: string | null;
}

// Итог bulk-синка WB-состояний заявок проекта (F1).
/** Машина с заявками сборки — опция фильтра «Источник» в списке сборок. */
export interface SourceVehicleOption {
  id: number;
  order_no: string;
}

export interface WbBulkSyncResult {
  checked: number;
  updated: number;
  supplies_seen: number;
}

export interface WbBoxesUpdate {
  boxes: WbBox[];
}

// Короб поставки из кабинета WB (вкладка «Упаковка», как в кабинете).
export interface WbCabinetBoxItem {
  barcode: string;
  quantity: number;
  imt_name: string | null;
  img_src: string | null;
  brand: string | null;
  sa_nm: string | null;
  nm_id: number | null;
  color_name: string | null;
  volume: number | null;
}

export interface WbCabinetBox {
  boxcode: string;
  quantity: number;
  items: WbCabinetBoxItem[];
}

export interface WbCabinetBoxes {
  boxes: WbCabinetBox[];
  total_boxes: number;
  total_barcodes: number;
  total_units: number;
}

// Существующий пропуск поставки из кабинета WB (trn_details).
export interface WbCabinetPass {
  has_pass: boolean;
  driver_first: string | null;
  driver_last: string | null;
  driver_phone: string | null;
  car_model: string | null;
  car_number: string | null;
  pallets: number | null;
  barcode_id: number | null;
  barcode_prefix: string | null;
  date_from: string | null;
  date_to: string | null;
}

export interface WbPassUpdate {
  driver_first?: string | null;
  driver_last?: string | null;
  driver_phone?: string | null;
  car_model?: string | null;
  car_number?: string | null;
  pallets?: number | null;
}

export interface WbDriver {
  firstName: string;
  lastName: string;
  phone: string;
}

export interface WbPortalStatus {
  status: 'NONE' | 'ACTIVE' | 'EXPIRED';
  updated_at?: string | null;
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
  /** true — «Общий вес» показан РАСЧЁТНЫМ (ручной не задан): нетто + тара коробов */
  weight_is_estimated?: boolean;
  /** ручная раскладка коробов по паллетам (null/[] = «авто», считается на лету) */
  pallet_manifest?: PalletBox[] | null;
  /** расчётный вес товаров (нетто) = Σ(qty × вес SKU); Decimal — приходит строкой */
  goods_weight_kg?: number | string | null;
  /** ШК позиций без веса в справочнике (дозаполнить в настройках) */
  weight_missing_barcodes?: string[];
  /** число коробов (для расчёта веса отгрузки = нетто + вес_коробки × коробов) */
  boxes_count?: number;
  /** геометрическая оценка числа паллет (footprint по коробам); только на детали */
  suggested_pallets_count?: number | null;
  /** расчётный ВЕС ОТГРУЗКИ (кандидат в «Общий вес»): нетто товаров + тара коробов; Decimal — приходит строкой; null если нет нетто-веса */
  suggested_total_weight_kg?: number | string | null;
  /** госномер машины; у старых заявок — свободная строка «Номер, водитель, ТК» */
  vehicle_info?: string;
  vehicle_brand?: string;
  driver_phone?: string;
  driver_first_name?: string | null;
  driver_last_name?: string | null;
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
  /** WB-сводка поставки (F1/F2): статус в кабинете + паллеты пропуска. null — не заведена */
  wb_supply?: WbSupplyStateBrief | null;
  items: AssemblyRequestItem[];
  created_at: string;
  updated_at: string;
  /** привязанная ФФ-заявка (зеркало фулфилмента); ff_request_* — первая привязка */
  ff_request_id?: number | null;
  ff_request_number?: string | null;
  ff_stage_title?: string | null;
  ff_warehouse_id?: number | null;
  /** все привязки (migfull/«Натали» — 2+ ФФ-заявки на одну сборку) */
  ff_links?: FfLinkInfo[] | null;
  /** состав сборки расходится с привязанной заявкой(ами) ФФ по наполнению (true — расхождение, null — неизвестно) */
  ff_mismatch?: boolean | null;
  /** Предраспределение: машина-источник (CostOrder), флаг и её номер для бейджа. */
  source_vehicle_id?: number | null;
  is_pre_distribution?: boolean;
  source_vehicle_order_no?: string | null;
  /** Предзаявка (бронь) на моно: целая моно-паллета на WB-склад без лимита приёмки (⌛). */
  is_prebooking?: boolean;
  /** ФФ предложил правку состава, ожидает согласования в DDS («Согласовать»/«Отказать») */
  ff_review_pending?: boolean;
  ff_proposed_items?: FfProposedItem[] | null;
  ff_proposed_at?: string | null;
  ff_proposed_by?: string | null;
  /** совместная поставка: эта сборка делит WB FBO-поставку с другими (≥2 сборок на одну поставку, по одной на ФФ-источник) */
  joint_supply?: boolean;
  /** другие сборки той же совместной поставки (для бейджа «Совместная» и тултипа) */
  joint_siblings?: JointSibling[] | null;
  /** совместная готова к назначению машины: ВСЕ сборки поставки в READY и дальше (ни одной PENDING/IN_PROGRESS) */
  joint_ready?: boolean;
  /** сумма паллет по всем активным сборкам совместной поставки */
  joint_total_pallets?: number | null;
  /** сумма веса (кг) по всем активным сборкам совместной поставки (Decimal — приходит строкой) */
  joint_total_weight_kg?: number | string | null;
  /** по заявке есть активная (SENT/MATCHED) отправка в Газельку — логистику ведёт агрегатор, ручное «Назначить машину» запрещено */
  via_gazelka?: boolean;
}

export interface FfProposedItem {
  barcode: string;
  quantity: number;
  product_name?: string | null;
  article?: string | null;
}

export interface FfLinkInfo {
  ff_request_id: number;
  ff_request_number?: string | null;
  ff_stage_title?: string | null;
  ff_warehouse_id?: number | null;
}

/** соседняя сборка той же совместной WB-поставки (другой ФФ-источник) */
export interface JointSibling {
  assembly_id: number;
  number: string;
  warehouse_id: number;
  warehouse_name?: string | null;
  status: string;
  /** паллеты сборки этого склада-источника */
  pallets_count?: number | null;
  /** вес паллеты (кг) этого склада-источника (Decimal — приходит строкой) */
  pallet_weight_kg?: number | string | null;
  /** внутренний номер заявки в ФФ-портале склада-источника */
  ff_request_number?: string | null;
}

/** Расходящаяся позиция: наш qty vs суммарный qty привязанных заявок ФФ */
export interface FfMismatchDetailRow {
  barcode: string;
  article_seller?: string | null;
  our_qty: number;
  ff_qty: number;
  /** ff_qty - our_qty (>0 — ФФ заявил больше, <0 — у нас больше) */
  diff: number;
}

/** Разбивка расхождения наполнения сборки с привязанными заявками ФФ (модалка) */
export interface FfMismatchDetail {
  assembly_id: number;
  assembly_number?: string | null;
  /** barcode — сверка по ШК (rows заполнены); total — состав ФФ по позициям недоступен (только итоги) */
  mode: 'barcode' | 'total';
  our_total: number;
  ff_total: number;
  ff_request_numbers: string[];
  /** Расхождение по НАШИМ ШК (наш qty ≠ qty ФФ, включая «мы отправили, а в заявке нет») */
  rows: FfMismatchDetailRow[];
  /** ШК только у ФФ (мы их не отправляли) — инфо, расхождением не считается */
  extra_rows?: FfMismatchDetailRow[];
}

// ─── Предраспределение машины в пути ─────────────────────────────────────────
/** Машина (CostOrder) в статусе CUSTOMS/DISPATCHED — кандидат на предраспределение. */
export interface PreDistVehicle {
  id: number;
  order_no: string;
  /** CUSTOMS | DISPATCHED — в пути; DELIVERED — принята ≤ 3 дн. назад (см. accepted_date). */
  status: string;
  target_warehouse_id: number | null;
  target_warehouse_name: string | null;
  eta: string | null;
  total_qty: number;
  sku_count: number;
  distributed_qty: number;
  can_distribute: boolean;
  block_reason: string | null;
  /** Дата приёмки (только для DELIVERED): остаток уже на ФФ, заявки из машины
   *  создаются ОБЫЧНЫМИ сборками (со списанием остатков), метка машины остаётся. */
  accepted_date: string | null;
}

/** Строка пула машины: товар и его доступный для раздачи остаток (gross − уже разнесённое). */
export interface PreDistPoolRow {
  barcode: string;
  article_seller: string | null;
  article_wb: string | null;
  name: string | null;
  brand: string | null;
  gross_qty: number;
  distributed_qty: number;
  available_qty: number;
  /** Кратность короба (шт/короб) ИЗ САМОЙ машины (qty-weighted mode строк cost_order).
   *  Машина ещё в пути → её кратность нет в справочнике принятых приёмок; берём отсюда.
   *  null — у машины нет заданной кратности. Приоритет на фронте: pool-row → справочник. */
  box_qty: number | null;
  /** Габариты короба «ДxШxВ» (см) машины, спаренные с выбранной кратностью. null — нет. */
  box_size: string | null;
  /** Новинка cold-start (first_sale_date IS NULL или ≥ today-14) — засеваем с машины,
   *  даже без ФФ-остатка (cold-start-справочник её не видит, т.к. требует rf_qty>0). */
  is_newcomer: boolean;
}

export interface PreDistVehiclePool {
  vehicle: PreDistVehicle;
  rows: PreDistPoolRow[];
}

/** Одна назначаемая строка: товар → WB-склад, количество, тип упаковки. */
export interface PreDistRow {
  barcode: string;
  wb_warehouse_name: string;
  qty: number;
  package_type: PackageType;
}

export interface PreDistributionCreate {
  vehicle_id: number;
  wb_fbo_supply_id?: number | null;
  rows: PreDistRow[];
  /** Число целых паллет по группе `"{wb_warehouse_name}::{package_type}"` — геометрию
   *  считает фронт (как у обычных заявок); вес заявки бэк досчитывает из веса товаров. */
  pallets_by_group?: Record<string, number>;
}

export interface PreDistributionCreateResult {
  created: number;
  request_ids: number[];
  requests: AssemblyRequest[];
}

/** Предзаявка (бронь) на моно: целые моно-паллеты на WB-склад без лимита приёмки. */
export interface PrebookingRow {
  warehouse_id: number;       // ФФ-склад-источник (где лежит товар предброни)
  barcode: string;
  wb_warehouse_name: string;  // склад назначения WB
  qty: number;
  package_type: PackageType;
}

export interface PrebookingCreate {
  rows: PrebookingRow[];
}

export interface PrebookingCreateResult {
  created: number;
  request_ids: number[];
  requests: AssemblyRequest[];
}

export interface AssemblyListResponse {
  items: AssemblyRequest[];
  total: number;
}

export interface AssemblyBulkDeleteSkip {
  id: number;
  number: string | null;
  status: string | null;
  reason: string;
}

export interface AssemblyBulkDeleteResult {
  deleted: number;
  skipped: AssemblyBulkDeleteSkip[];
}

/** Массовый перевод заявок в статус одним запросом (вместо N поштучных —
 *  поштучные съедали общий write-лимит → 429 «Слишком много запросов»). */
export type AssemblyBulkStatus = 'IN_PROGRESS' | 'READY';

export interface AssemblyBulkStatusResult {
  updated: AssemblyRequest[];
  skipped: AssemblyBulkDeleteSkip[];
}

/** Массовое авто-заполнение «Общего веса» (нетто товаров + тара коробов) одним запросом. */
export interface AssemblyApplyWeightBulkResult {
  applied: AssemblyRequest[];
  skipped: { id: number; number: string; reason: string }[];
}

// ─── Gazelka integration ─────────────────────────────────────────────────────

export interface GazelkaConfig {
  configured: boolean;
  warehouse_id: number | null;
  warehouse_name: string | null;
}

export interface GazelkaSelectOption {
  value: string;
  label: string;
  /** Только у складов назначения: id направления и маркетплейса (название неуникально). */
  place_id?: string | null;
  marketplace_id?: string | null;
}

/** График склада: дни недели (0=Вс … 6=Сб) и срок в пути. null — ограничения нет. */
export interface GazelkaSchedulePlan {
  loading_days: number[] | null;
  delivery_days: number[] | null;
  eta_days: number;
}

export interface GazelkaFormOptions {
  entities: GazelkaSelectOption[];
  price_lists: GazelkaSelectOption[];
  marketplaces: GazelkaSelectOption[];
  delivery_warehouses: GazelkaSelectOption[];
  supply_types: GazelkaSelectOption[];
  timeslots: GazelkaSelectOption[];
  /** Выбранные порталом значения: порядок опций произвольный, «первая» ≠ «выбранная». */
  default_entity_id: string | null;
  default_price_id: string | null;
  /** Активные направления, ключ «{price_id}-{place_id}». Нет ключа — склад недоступен. */
  schedule: Record<string, GazelkaSchedulePlan>;
  min_departure_date: string | null;
  min_delivery_date: string | null;
}

export interface GazelkaPrefill {
  customer_phone: string | null;
  delivery_address: string | null;
  delivery_address_x2: string | null;
  departure_date: string | null;
  delivery_date: string | null;
  delivery_contact: string | null;
  daily_delivery_timeslot: string | null;
  supply_id: string | null;
  marketplace_id: string | null;
  pallets: number;
  boxes: number;
  weight: string | null;
  notes: string | null;
}

export interface GazelkaDraft {
  eligible: boolean;
  already_sent: boolean;
  sent_ref: string | null;
  options: GazelkaFormOptions;
  prefill: GazelkaPrefill;
}

export interface GazelkaSendRequest {
  entity_id: string;
  payer_id: string;
  price_id: string;
  is_marketplace: string;
  marketplace_id?: string | null;
  supply_id?: string | null;
  delivery_address?: string | null;
  delivery_address_x2?: string | null;
  departure_date?: string | null;
  delivery_date?: string | null;
  delivery_time?: string | null;
  daily_delivery_timeslot?: string | null;
  delivery_contact?: string | null;
  customer_phone?: string | null;
  monomix?: string | null;
  pallets: number;
  boxes: number;
  weight2?: string | null;
  weight?: string | null;
  volume?: string | null;
  length: number;
  height: number;
  width: number;
  palleting: boolean;
  notes?: string | null;
  force_resend?: boolean;
}

export interface GazelkaSendResult {
  ok: boolean;
  ref: string | null;
  message: string | null;
  gazelka_order_id: number | null;
}

export interface GazelkaOrderRow {
  gazelka_id: string;
  status: string;
  status_label: string;
  application_date: string | null;
  departure_date: string | null;
  departure_time: string | null;
  departure_address: string | null;
  delivery_date: string | null;
  delivery_time: string | null;
  delivery_address: string | null;
  marketplace: string | null;
  monomix: string | null;
  pallets: number;
  boxes: number;
  weight: string | null;
  supply_id: string | null;
  rate: string | null;
  entity: string | null;
  notes: string | null;
  editable: boolean;
  linked_assembly_id: number | null;
  linked_assembly_number: string | null;
  linked_assembly_status: string | null;
  suggested_assembly_id: number | null;
  suggested_assembly_number: string | null;
  route_number: string | null;
  route_date: string | null;
  carrier: string | null;
  driver_name: string | null;
  driver_phone: string | null;
  driver_passport: string | null;
  vehicle: string | null;
  finish_time: string | null;
}

export interface GazelkaOrderList {
  items: GazelkaOrderRow[];
  count: number;
}

export interface GazelkaEditDraft {
  gazelka_id: string;
  options: GazelkaFormOptions;
  values: GazelkaSendRequest;
}

export interface GazelkaMatchCandidate {
  assembly_id: number;
  number: string;
  warehouse_name: string | null;
  wb_supply_id: string | null;
  delivery_date: string | null;
  pallets_count: number | null;
  status: string | null;
  already_linked_to: string | null;
}

export interface GazelkaMatchResult {
  ok: boolean;
  linked_assembly_id: number | null;
  linked_assembly_number: string | null;
}

export interface GazelkaUnmatchResult {
  ok: boolean;
}

// ─── Migfull-portal integration (ФФ «Натали») ────────────────────────────────

export interface MigfullPortalConfig {
  configured: boolean;
  warehouse_id: number | null;
  warehouse_name: string | null;
}

export interface MigfullDeliveryTypeOption {
  value: string;
  label: string;
}

export interface MigfullShipmentPrefill {
  number: string | null;                       // № поставки WB
  shipment_date: string | null;                // YYYY-MM-DD
  filter_delivery_type: 'direct' | 'transit' | 'pickup';
  notes: string | null;
  wb_warehouse_name: string | null;            // инфо: куда отгрузка (WB-склад)
  destination_name: string | null;            // распознанный склад в ФФ (выставим при создании)
  destination_matched: boolean;               // удалось ли сматчить склад назначения
  assembly_number: string | null;
}

export interface MigfullOpisLine {
  barcode: string;          // ШК короба (ITF14) или товара (EAN13)
  name: string | null;
  size: string | null;
  color: string | null;
  quantity: number;         // КОРОБОВ (для коробов) или ШТУК (россыпь)
  is_box: boolean;          // короб?
  units_per_box: number;
  pieces: number;           // всего штук (инфо)
}

export interface MigfullDraftResponse {
  eligible: boolean;                    // склад сборки == склад интеграции
  already_sent: boolean;                // уже отправляли эту сборку
  sent_guid: string | null;
  sent_number: string | null;
  prefill: MigfullShipmentPrefill;
  delivery_types: MigfullDeliveryTypeOption[];
  opis_lines: MigfullOpisLine[];
  total_boxes: number;
  total_pieces: number;
  warnings: string[];                   // напр. «кол-во не кратно коробу»
}

export interface MigfullSendRequest {
  filter_delivery_type: 'direct' | 'transit' | 'pickup';
  number: string | null;
  shipment_date: string | null;
  notes: string | null;
  force_resend: boolean;
}

export interface MigfullSendResult {
  ok: boolean;
  shipment_guid: string | null;
  shipment_number: string | null;
  message: string | null;
  order_id: number | null;
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

/* ─── Связи и расхождения сборки (link anomalies) ─── */

/** Сборка, состав которой расходится с привязанными заявками ФФ. */
export interface FfMismatchRow {
  assembly_id: number;
  number: string;
  status: AssemblyStatus;
  warehouse_id: number;
  warehouse_name: string | null;
  ff_request_numbers: string[];
  /** наш итог, шт */
  our_total: number;
  /** итог по привязанным заявкам ФФ, шт */
  ff_total: number;
  /** ff_total - our_total (знаковая разница) */
  diff: number;
  /** barcode — сверка по ШК; total — по суммарному кол-ву */
  mode: 'barcode' | 'total';
}

/** Наша сборка на ФФ-складе без привязанной заявки ФФ. */
export interface UnlinkedAssemblyRow {
  assembly_id: number;
  number: string;
  status: AssemblyStatus;
  warehouse_id: number;
  warehouse_name: string | null;
  provider: string | null;
  total_qty: number;
  created_at: string | null;
  age_days: number;
}

/** Заявка ФФ без привязанной нашей сборки. */
export interface UnlinkedFfRow {
  ff_request_id: number;
  provider: string;
  number: string | null;
  warehouse_id: number;
  warehouse_name: string | null;
  stage_title: string | null;
  status: string | null;
  total_qty: number | null;
  external_created_at: string | null;
}

/** Одна аномальная FBO-поставка (разворот-список с drill на /warehouse/fbo-supplies). */
export interface FboAnomalySupply {
  supply_id: number;
  /** WB-I-xxxx — для deep-link и показа */
  wb_supply_id: string | null;
  name: string | null;
  /** склад ВБ (город сдачи) */
  warehouse_name: string | null;
  total_qty: number;
  accepted_qty: number;
  /** accepted_qty - total_qty (<0 — недоприёмка, >0 — излишек) */
  diff: number;
  planned_date: string | null;
  actual_date: string | null;
  assembly_request_number: string | null;
}

/** Сводка аномалий FBO-поставок ВБ (drill-through на /warehouse/fbo-supplies). */
export interface FboAnomalyRollup {
  without_assembly_count: number;
  under_accepted_count: number;
  under_accepted_qty: number;
  excess_count: number;
  excess_qty: number;
  without_assembly_supplies: FboAnomalySupply[];
  under_accepted_supplies: FboAnomalySupply[];
  excess_supplies: FboAnomalySupply[];
}

/** Построчное расхождение остатка по SKU: наш склад vs ФФ-зеркало. */
export interface StockMismatchSkuRow {
  barcode: string;
  article_seller: string | null;
  brand: string | null;
  name: string | null;
  /** у ФФ (зеркало провайдера), штук россыпи */
  ff_good: number;
  /** у нас годный (WarehouseStock.quantity) */
  our_quantity: number;
  /** у нас брак (вычтен из diff только для migfull) */
  our_defect: number;
  /** ff_good − (our_quantity + our_defect для migfull); >0 — у ФФ больше */
  diff: number;
}

/** Расхождение остатка по ФФ-интегрированному складу (наш склад vs API-зеркало). */
export interface StockMismatchWarehouseRow {
  warehouse_id: number;
  warehouse_name: string | null;
  provider: string | null;
  /** суммарно у ФФ больше, штук */
  surplus_ff_qty: number;
  /** на скольких SKU у ФФ больше */
  surplus_ff_sku: number;
  /** суммарно у нас больше, штук */
  surplus_our_qty: number;
  /** на скольких SKU у нас больше */
  surplus_our_sku: number;
  /** surplus_ff_qty - surplus_our_qty (нетто ФФ − наш) */
  net_diff: number;
  /** всего SKU с расхождением */
  sku_total: number;
  /** rows обрезаны до лимита (на складе больше расхождений) */
  truncated: boolean;
  /** ISO — последний синк остатков ФФ */
  synced_at: string | null;
  rows: StockMismatchSkuRow[];
}

/** Сборка (машина назначена / в пути), чья WB-поставка расходится по дате / паллетам / пропуску. */
export interface SupplyDiscrepancyRow {
  assembly_id: number;
  number: string;
  /** VEHICLE_ASSIGNED / SHIPPED */
  status: string;
  /** наш ФФ-склад, откуда забрали товар */
  source_warehouse_name: string | null;
  /** склад ВБ (город сдачи) */
  warehouse_name: string | null;
  /** наша дата сдачи, ISO */
  delivery_date: string | null;
  /** дата брони WB, ISO */
  planned_date: string | null;
  /** delivery_date − planned_date, дней (знаковая) */
  date_diff_days: number | null;
  /** наши паллеты (AssemblyRequest.pallets_count) */
  pallets_count: number;
  /** паллеты в пропуске WB (null — пропуск не оформлен) */
  pass_pallets: number | null;
  wb_supply_id: string | null;
  wb_status: string | null;
  /** стадия реплея пропуска (PASSED = пропуск занесён) */
  sync_status: string | null;
  /** номер машины в пропуске кабинета WB (снимок) */
  wb_car_number: string | null;
  /** паллеты в пропуске кабинета WB (снимок) */
  wb_pass_pallets: number | null;
  date_mismatch: boolean;
  pallet_mismatch: boolean;
  /** пропуск не оформлен нигде (ни ВБ, ни наш PASSED) */
  pass_missing: boolean;
  /** пропуск заведён в кабинете WB */
  pass_on_wb: boolean;
  /** пропуск есть на ВБ, а у нас поля пусты */
  pass_missing_dds: boolean;
  /** номер машины ДДС ≠ ВБ */
  car_number_mismatch: boolean;
}

export interface LinkAnomaliesResponse {
  ff_composition_mismatch: FfMismatchRow[];
  assemblies_without_ff: UnlinkedAssemblyRow[];
  ff_without_assembly: UnlinkedFfRow[];
  fbo: FboAnomalyRollup;
  /** Расхождение остатков по складам с ФФ-интеграцией. */
  stock_mismatch: StockMismatchWarehouseRow[];
  /** Расхождение WB-поставок (машина назначена/в пути): дата / паллеты / пропуск. */
  supply_discrepancies: SupplyDiscrepancyRow[];
}

/* ─── Распределение остатков сборки (stock distribution) ─── */

/** Где сейчас товар (шт + доля от итога). Сумма долей ≈ 100. */
export interface StockDistributionBucket {
  /** на складе ФФ: qty_good - qty_reserve (≥0) */
  ff_stock: number;
  /** IN_PROGRESS («в сборке») */
  in_assembly: number;
  /** READY + VEHICLE_ASSIGNED («готово») */
  ready: number;
  /** SHIPPED («в пути») */
  in_transit: number;
  total: number;
  ff_stock_pct: number;
  in_assembly_pct: number;
  ready_pct: number;
  in_transit_pct: number;
}

/** Бакет с подписью группы — склад или статус товара. */
export interface StockDistributionGroup {
  key: string;
  label: string;
  bucket: StockDistributionBucket;
}

export interface StockDistributionResponse {
  total: StockDistributionBucket;
  by_warehouse: StockDistributionGroup[];
  by_status: StockDistributionGroup[];
}

/** Снимок 4 бакетов «где товар» за один день (шт). */
export interface StockDistributionDailyStat {
  /** ISO YYYY-MM-DD */
  date: string;
  ff_stock: number;
  in_assembly: number;
  ready: number;
  in_transit: number;
}

export interface StockDistributionHistoryResponse {
  daily: StockDistributionDailyStat[];
}

export interface AssemblyHistoryEntry {
    id: number;
    old_status: string | null;
    new_status: string;
    changed_at: string;
    changed_by: string | null;
    comment: string | null;
}

/** Одна попытка отгрузки заявки (цепочка: отгрузил → не приняли → вернул → переотгрузил). */
export interface AssemblyAttempt {
    attempt_no: number;
    shipment_id: number;
    shipment_number: string | null;
    shipped_at: string | null;
    wb_supply_id: string | null;
    wb_supply_name: string | null;
    wb_warehouse_name: string | null;
    wb_fbo_status: string | null;
    vehicle_info: string | null;
    vehicle_brand: string | null;
    driver_phone: string | null;
    carrier_inn: string | null;
    carrier_name: string | null;
    pickup_cost: number | null;
    pallets_count: number | null;
    pickup_date: string | null;
    delivery_date: string | null;
    outcome: 'accepted' | 'rejected' | 'in_transit';
    returned_to_warehouse_id: number | null;
    returned_to_warehouse_name: string | null;
    returned_at: string | null;
}

/** Тело возврата отгрузки на склад (опц. другой склад). */
export interface AssemblyReturnPayload {
    return_warehouse_id?: number | null;
    comment?: string | null;
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
  driver_first_name?: string | null;
  driver_last_name?: string | null;
  carrier_inn?: string | null;
  carrier_name?: string | null;
}

export interface RefreshFromFboResponse {
  added: number;
  removed: number;
  changed: number;
  items: AssemblyRequestItem[];
  // ШК из состава WB, которых нет в номенклатуре проекта — не добавлены в заявку.
  skipped?: string[];
}

// ─── Logistics Analytics ───────────────────────────────────────────────────

export interface LogisticsCostSummary {
  total_cost: number;
  avg_cost_per_pallet: number;
  total_pallets: number;
  total_shipments: number;
  total_requests: number;
  total_items: number;
  total_skus: number;
  total_weight_kg: number;
  total_destinations: number;
  total_carriers: number;
}

export interface LogisticsDestStat {
  dest_warehouse: string;
  avg_cost: number;
  total_cost: number;
  shipments_count: number;
  total_pallets: number;
  avg_pallets: number;
  min_cost_per_pallet: number;
  max_cost_per_pallet: number;
}

export interface LogisticsRouteStat {
  src_warehouse: string;
  dest_warehouse: string;
  avg_cost: number;
  shipments_count: number;
}

export interface LogisticsCarrierStat {
  counterparty_id: number | null;
  carrier_inn: string | null;
  carrier_name: string;
  shipments_count: number;
  total_pallets: number;
  total_cost: number;
  avg_cost_per_pallet: number;
  destinations_count: number;
}

export interface LogisticsPalletBucketStat {
  bucket: string;
  sort_order: number;
  shipments_count: number;
  avg_pallets: number;
  total_pallets: number;
  total_cost: number;
  avg_cost_per_pallet: number;
}

export interface LogisticsCostPoint {
  dest_warehouse: string;
  pallets: number;
  cost_per_pallet: number;
  shipped_date: string | null;
}

export interface LogisticsDestBucketCell {
  dest_warehouse: string;
  bucket: string;
  sort_order: number;
  shipments_count: number;
  total_pallets: number;
  avg_cost_per_pallet: number;
}

export type LogisticsAnomalyType = 'no_cost' | 'overpriced' | 'underpriced';

export interface LogisticsAnomaly {
  shipment_id: number;
  assembly_request_id: number | null;
  assembly_number: string | null;
  dest_warehouse: string;
  carrier_name: string | null;
  pallets_count: number | null;
  pickup_cost: number | null;
  cost_per_pallet: number | null;
  shipped_date: string | null;
  anomaly_type: LogisticsAnomalyType;
  severity: number;
  expected_low: number | null;
  expected_high: number | null;
  reason: string;
}

export interface LogisticsAnalyticsResponse {
  summary: LogisticsCostSummary;
  by_destination: LogisticsDestStat[];
  by_route: LogisticsRouteStat[];
  by_carrier: LogisticsCarrierStat[];
  pallet_buckets: LogisticsPalletBucketStat[];
  dest_pallet_cells: LogisticsDestBucketCell[];
  cost_points: LogisticsCostPoint[];
  anomalies: LogisticsAnomaly[];
}

export interface LogisticsShipmentRow {
  shipment_id: number;
  attempt_no: number;
  assembly_request_id: number | null;
  assembly_number: string | null;
  status: string | null;
  brands: string | null;
  src_warehouse: string | null;
  dest_warehouse: string | null;
  counterparty_id: number | null;
  carrier_inn: string | null;
  carrier_name: string | null;
  wb_supply_id: string | null;
  wb_supply_name: string | null;
  wb_fbo_status: string | null;
  wb_fbo_planned_date: string | null;
  wb_fbo_actual_date: string | null;
  pallets_count: number | null;
  pickup_cost: number | null;
  cost_per_pallet: number | null;
  total_weight_kg: number | null;
  shipped_date: string | null;
  shipped_at: string | null;
  anomaly_type: LogisticsAnomalyType | null;
  via_gazelka: boolean;
}

export interface LogisticsShipmentListResponse {
  items: LogisticsShipmentRow[];
  total: number;
  truncated: boolean;
}

export interface CostForecastBucket {
  bucket: string;
  sort_order: number;
  cpp: number;
  low: number;
  high: number;
  sample_size: number;
}

export interface CostForecastWarehouse {
  dest_warehouse: string;
  cpp: number;
  low: number;
  high: number;
  sample_size: number;
  buckets: CostForecastBucket[];
}

export interface CostForecastResponse {
  global_cpp: number;
  global_low: number;
  global_high: number;
  sample_size: number;
  warehouses: CostForecastWarehouse[];
}

// ─── Payment Requests ──────────────────────────────────────────────────────

export type PaymentRequestStatus = 'DRAFT' | 'PENDING_REVIEW' | 'APPROVED' | 'DRAFT_CREATED' | 'PAID' | 'REJECTED' | 'CANCELLED';
export type PaymentRequestSource = 'MANUAL' | 'COUNTERPARTY';
export type PaymentRequestDocType = 'INVOICE' | 'ACT';
// «Назначение оплаты» — теперь редактируемый справочник (PaymentCategory), поэтому код — string.
// LOGISTICS/OTHER остаются системными кодами со спец-логикой (привязка к отгрузке/банк, дефолт).
export type PaymentRequestCategory = string;

/** Строка справочника «Назначение оплаты» (управляется в модалке на странице Оплаты). */
export interface PaymentCategory {
  id: number;
  code: string;
  label: string;
  sort_order: number;
  is_system: boolean;
  project_id: number | null;
}

export interface PaymentRequestRow {
  id: number;
  number: string;
  status: PaymentRequestStatus;
  category: PaymentRequestCategory | null;
  brand: string | null;  // бренд-атрибуция; null = «Все бренды»
  project_id: number | null;
  project_name: string | null;
  payee_name: string | null;
  payee_inn: string | null;
  amount: string;
  currency: string;
  pickup_date: string | null;
  matched_transaction_id: number | null;
  doc_count: number;
  created_at: string;
}

/** Разрезанный под-документ из многостраничного файла (счёт+акт в одном PDF). */
export interface ParsedDocument {
  doc_type: PaymentRequestDocType;
  filename: string;
  mime_type: string;
  content_b64: string;
}

export interface InvoiceParseResult {
  payee_name: string | null;
  payee_inn: string | null;
  payee_kpp: string | null;
  payee_account: string | null;
  payee_bik: string | null;
  payee_bank_name: string | null;
  payee_corr_account: string | null;
  amount: string | null;
  purpose: string | null;
  fields_found: string[];
  warnings: string[];
  /** Авто-разнесение: счёт→INVOICE, акт→ACT. Пусто, если резать нечего. */
  documents: ParsedDocument[];
}

export interface PaymentRequestDocument {
  id: number;
  payment_request_id: number;
  doc_type: PaymentRequestDocType;
  original_filename: string | null;
  file_size: number | null;
  mime_type: string | null;
  uploaded_at: string;
}

export interface PaymentRequestEvent {
  id: number;
  old_status: PaymentRequestStatus | null;
  new_status: PaymentRequestStatus;
  changed_at: string;
  changed_by: string | null;
  comment: string | null;
}

export interface PaymentRequestDetail extends PaymentRequestRow {
  source: PaymentRequestSource;
  counterparty_id: number | null;
  outbound_shipment_id: number | null;
  assembly_request_id: number | null;
  payee_account: string | null;
  payee_bik: string | null;
  payee_bank_name: string | null;
  payee_corr_account: string | null;
  payee_kpp: string | null;
  purpose: string | null;
  bank_doc_id: string | null;
  matched_at: string | null;
  shipment_numbers: string[];
  shipment_count: number;
  documents: PaymentRequestDocument[];
  events: PaymentRequestEvent[];
}

export interface CreateDraftResult {
  id: number;
  ok: boolean;
  bank_doc_id?: string | null;
  error?: string | null;
}

export interface CreateDraftsResponse {
  results: CreateDraftResult[];
  created: number;
  failed: number;
}

export interface PaymentRequestListResponse {
  items: PaymentRequestRow[];
  total: number;
}

export interface ShippableShipmentRow {
  outbound_shipment_id: number;
  assembly_request_id: number | null;
  number: string;
  destination: string | null;
  shipped_date: string | null;
  pickup_cost: string | null;
  counterparty_id: number | null;
  carrier_inn: string | null;
  carrier_name: string | null;
  carrier_kpp: string | null;
  carrier_bank_account: string | null;
  carrier_bik: string | null;
  carrier_bank_name: string | null;
  carrier_corr_account: string | null;
  already_requested: boolean;
  request_id: number | null;
  request_status: PaymentRequestStatus | null;
  matched_transaction_id: number | null;
  matched_at: string | null;
  matched_txn_date: string | null;
  pallets_count: number | null;
  pickup_date: string | null;
  source_warehouse: string | null;
  wb_supply_number: string | null;
}

export interface ReconciliationPaymentRow {
  transaction_id: number;
  date: string;
  amount: string;
  purpose: string | null;
  account: string | null;
  counterparty_name: string | null;
  inn: string | null;
  archived: boolean;
  linked_shipment_ids: number[];
  linked_count: number;
  linked_sum: string;
  diff: string;
}

export interface CounterpartyReconciliation {
  shipments: ShippableShipmentRow[];
  payments: ReconciliationPaymentRow[];
  matched_count: number;
  unpaid_count: number;
  payment_count: number;
  unlinked_payment_count: number;
  payments_truncated: boolean;
}

export interface PaymentRequestCreate {
  source: PaymentRequestSource;
  // project_id: не передано → текущий проект; null → общая (без проекта); число → этот проект.
  project_id?: number | null;
  category?: PaymentRequestCategory;
  outbound_shipment_id?: number;
  outbound_shipment_ids?: number[];
  counterparty_id?: number;
  payee_inn?: string;
  payee_account?: string;
  payee_bik?: string;
  payee_bank_name?: string;
  payee_corr_account?: string;
  payee_kpp?: string;
  payee_name?: string;
  amount?: string;
  currency?: string;
  pickup_date?: string;
  purpose?: string;
  // Бренд-атрибуция. Опущено → «Все бренды»; null → сбросить в «Все бренды» (при PATCH).
  brand?: string | null;
}

export interface PaymentRequestStatusPoll {
  id: number;
  status: PaymentRequestStatus;
  matched_transaction_id: number | null;
  matched_at: string | null;
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

// ─── Управление рекламой ─────────────────────────────────────────────────────

export interface AdsManagerCampaign {
  campaign_id: number;
  name: string | null;
  campaign_type: string | null;
  advert_type?: number | null;  // числовой тип WB: 8=авто/рекомендации, 9=аукцион (цветовая кодировка)
  created_at?: string | null;  // дата создания кампании в WB (ISO) — фильтр по дате добавления
  status: number;
  status_label: string;
  budget: number;
  nm_ids: number[];
  nm_count: number;
  brands: string[];
  subjects: string[];
  spend_today: number;
  spend_period: number;
  views_period: number;
  clicks_period: number;
  ctr: number;
  cpc: number;
  cpl: number;  // стоимость одной корзины за период
  cpo: number;  // стоимость одного заказа за период
  drr: number;
  margin: number;
  spend_per_hour: number;  // средний расход ₽/час за день (расход за период / (дней × 24))
  ad_click_share: number;  // доля рекл. кликов от всех переходов товаров кампании
  cr_cart: number;  // конверсия переход→корзина
  cr_order: number;  // конверсия корзина→заказ
  rev_yesterday: number;  // сумма заказов товаров кампании ВЧЕРА (для «ДРР план» = rev_yesterday × целевой ДРР%)
  budget_gap: number;  // недобор бюджета до конца дня, ₽ (0 — бюджет не исчерпан сегодня)
  bid_mode?: string | null;  // для CPM: 'unified' (единая) / 'manual' (ручная); пока не синкается
  updated_at: string | null;
}

/** Режим автопополнения: to_target — долить до X в заданный час при пороге по обороту (наш);
 *  low_balance — «как на ВБ»: когда остаток < порога, долить фиксированную сумму (любой час, повторяемо). */
export type AdsAutopayMode = 'to_target' | 'low_balance';

export interface AdsAutopaySetting {
  enabled: boolean;
  mode: AdsAutopayMode;
  amount: number;  // to_target: дневной бюджет X
  hour: number;  // to_target: час пополнения, МСК
  threshold_pct: number;  // to_target: пополнять, только если открут за сутки ≥ порога
  low_balance_threshold: number;  // low_balance: долить, когда остаток < этого, ₽
  topup_amount: number;  // low_balance: сумма разового долива, ₽
  daily_cap: number;  // low_balance: не чаще N раз в день (0 = без ограничения)
}

export interface AdsAutopayLogEntry {
  campaign_id: number;
  ts: string;  // ISO UTC
  amount: number;  // фактически пополнено ₽ (0, если не удалось)
  requested: number;  // запрошенная сумма ₽
  source: string;  // счёт | баланс
  status: 'ok' | 'error' | 'unknown';
  budget_before: number | null;
  budget_after: number | null;
  reason: string | null;  // текст ошибки WB, если была
}

/** Результат смены статуса кампании (запуск/пауза) в WB. */
export interface AdsCampaignStateResult {
  ok: boolean;
  status: number | null;  // новый статус (9 активна / 11 пауза), null при ошибке
  error: string | null;
}

/** Ответ сохранения автопополнения: настройки + результат авто-активации (если включили). */
export interface AdsAutopaySaveResult {
  settings: Record<string, AdsAutopaySetting>;
  activation: AdsCampaignStateResult | null;
}

export interface AdsHistoryPoint {
  date: string;
  price_spp: number;
  open_card: number;
  adv_sum: number;
  drr: number;
  orders_sum_rub: number;
}

export interface AdsBudgetGap {
  campaign_id: number;
  name: string | null;
  campaign_type: string | null;
  nm_ids: number[];
  nm_count: number;
  brands: string[];
  subjects: string[];
  spend_today: number;
  ran_out_at: string | null;  // null = кончился до первого синка (час неизвестен)
  burn_rate: number;
  needed_till_midnight: number;  // с учётом минимума пополнения WB (1000 ₽)
  raw_needed: number;  // расчётная нужда без учёта минимума
  min_topup: number;
  hours_active: number;
  remaining_hours: number;
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

// ─── Кластеризатор рекламных запросов ───
export type ClusterTier = 'HEAD' | 'EFFICIENT' | 'CONVERTS' | 'TRASH' | 'WASTE' | 'NOCONV';

export interface ClusterWindow {
  from: string;
  to: string;
}

export interface ClusterDailyPoint {
  date: string;
  views: number;
  clicks: number;
  spend: number;
  spend_pct: number;
}

/** Классифицированный поисковый кластер (запрос) кампании/категории. */
export interface SearchCluster {
  norm_query: string;
  views: number;
  clicks: number;
  ctr: number;
  cpc: number;
  cpm: number;
  spend: number;
  orders: number;
  atbs: number;
  shks: number;
  avg_pos: number;
  cr: number;
  cpo: number | null;
  drr: number | null;
  relevant: boolean;
  tier: ClusterTier;
  reason: string;
  is_minused?: boolean;  // только в разрезе кампании; в категории отсутствует
  bid?: number | null;   // текущая ставка CPM по кластеру, ₽ (null — не задана)
  locked?: boolean;      // views < 100 — WB не даёт ставку/минус («стадия сбора данных»)
}

export interface ClusterTotals {
  clusters: number;
  views: number;
  clicks: number;
  orders: number;
  spend: number;
  ctr: number;
  cpo: number | null;
}

/** Метрики одной зоны показов (поиск / рекомендации). */
export interface ZoneMetrics {
  views: number;
  clicks: number;
  spend: number;
  orders: number;
  ctr: number;
  cpc: number;
  cpo: number | null;
}

/** Статистика по зонам. WB не отдаёт её напрямую: поиск = кластеры,
 *  рекомендации = итог кампании минус поиск (флаг derived). */
export interface CampaignZoneStats {
  search: ZoneMetrics;
  recommendations: ZoneMetrics | null;
  /** У CPC бэкенд total не отдаёт: зона одна, итог кампании равен ей. */
  total?: { views: number; clicks: number; spend: number; orders: number } | null;
  derived: boolean;
}

/** Зоны показов кампании и правила ставки (GET /campaigns/{id}/zones). */
export interface CampaignZones {
  campaign_id: number;
  payment_type: string;   // cpm | cpc
  bid_mode: string | null;  // unified | manual
  placements: Record<string, boolean>;
  bids: { search: number | null; recommendations: number | null };
  zones_locked: boolean;  // зоны нельзя включать/выключать (не CPM-ручная, либо статус не 4/9/11)
  lock_reason?: string | null;  // почему нельзя — показываем в подсказке у тумблера
  single_bid: boolean;    // ставка одна: на все зоны (CPM-единая) или на все фразы (CPC)
  zone_stats?: CampaignZoneStats | null;  // только для CPC: зона одна, данные прямые
  error?: string;
}

/** Результат PUT /campaigns/{id}/zones */
export interface CampaignZonesUpdate {
  ok: boolean;
  error: string | null;
  placements: Record<string, boolean> | null;
}

export interface CampaignClustersResponse {
  campaign_id: number;
  name: string | null;
  campaign_type: string | null;
  bid_mode?: string | null;  // 'unified' (единая) / 'manual' (ручная); при unified WB не даёт управлять кластерами
  nm_ids: number[];
  subject: string | null;
  window: ClusterWindow;
  aov: number;
  target_drr: number;
  default_bid?: number | null;  // ставка кампании — действует на кластеры без своей ставки
  placements?: Record<string, boolean>;  // зоны показов: search / recommendations
  zone_bids?: { search: number | null; recommendations: number | null };
  zone_stats?: CampaignZoneStats;
  totals: ClusterTotals;
  daily: ClusterDailyPoint[];
  clusters: SearchCluster[];
  error?: 'no_api_key' | 'campaign_not_found' | string;  // при ошибке остальные поля отсутствуют
}

/** Строка посуточных метрик кампании (РК + воронка); date может быть меткой «За всё время». */
export interface CampaignMetricRow {
  date: string;
  views: number;
  clicks: number;
  ctr: number;
  cpc: number;
  spend: number;
  open_card: number;
  add_to_cart: number;
  cr1: number;
  orders: number;
  cr2: number;
  orders_sum: number;
  cpl: number | null;  // стоимость 1 корзины
  cpo: number | null;  // стоимость 1 заказа
  avg_price: number;
  customer_price: number;  // цена клиенту с учётом СПП (avg_price × (1 − СПП))
  spp: number;             // средний СПП за день, %
  drr: number;
}

export interface CampaignMetricsResponse {
  campaign_id: number;
  name: string | null;
  window: ClusterWindow;
  nm_id?: number | null;
  ad_by_nm?: boolean;  // РК-метрики отфильтрованы по товару, а не по всей кампании
  totals: CampaignMetricRow;
  rows: CampaignMetricRow[];
  error?: string;
}

/** Точка почасового расхода кампании (hour 0..23, ₽ за час). */
export interface CampaignHourlyPoint {
  hour: number;
  spend: number;
}

/** Почасовой расход кампании за день (GET /campaigns/{id}/hourly).
 *  Восстановлен из снимков остатка бюджета (~10 мин). Показы/клики по часам WB не отдаёт. */
export interface CampaignHourlySpend {
  campaign_id: number;
  name: string | null;
  date: string;
  total: number;
  hours: CampaignHourlyPoint[];
  error?: string;
}

/** Органическая позиция товара по фразе (последний + предыдущий снимок из search.wb.ru). */
export interface PositionSnapshot {
  position: number | null;  // 1-based ранг; null = не найден в пределах depth (или не собрано)
  prev: number | null;      // позиция в предыдущем сборе («Была»)
  depth: number | null;     // сколько позиций проверено (для «N+» — не в топ-N)
  at: string | null;        // ISO момент последнего сбора
}
export interface PositionsResponse {
  nm_id: number;
  positions: Record<string, PositionSnapshot>;  // norm_query → снимок
}
export interface PositionsProgress { status: string; done: number; total: number; throttled: number; error: string | null; }
export interface CollectPositionsResult { started: boolean; status: string; done: number; total: number; throttled: number; error: string | null; }
/** Результат сбора ОДНОЙ фразы (кнопка-кругляшок). throttled=true → «слишком частый запрос». */
export interface CollectOneResult extends PositionSnapshot { phrase: string; throttled: boolean; }

/** Интервал внутридневного графика (дельта между снимками накопительного счётчика). */
export interface CampaignIntradayPoint {
  time: string;   // ЧЧ:ММ МСК (момент снимка = конец интервала)
  views: number;  // показы за интервал
  clicks: number; // клики за интервал
  spend: number;  // расход ₽ за интервал
}

/** Внутридневные показы/клики/расход по кампании (GET /campaigns/{id}/intraday).
 *  Копятся вперёд из снимков кабинетного campaigns-stats (~30 мин) — WB нативно почасовку
 *  не отдаёт. CTR и порог «мин показов» считаются на клиенте. */
export interface CampaignIntradayMetrics {
  campaign_id: number;
  name: string | null;
  date: string;
  points: CampaignIntradayPoint[];
  totals: { views: number; clicks: number; spend: number };
  snapshots: number;
  interval_min?: number;  // текущая частота снимков проекта (10/20/30/60)
  error?: string;
}

/** Строка РК-метрик зоны показов (без воронки — её WB по зонам не делит).
 *  atbs/orders — корзины/заказы, атрибутированные рекламе. date может быть «За всё время». */
export interface CampaignZoneMetricRow {
  date: string;
  views: number;
  clicks: number;
  ctr: number;
  cpc: number;
  cpm: number;    // цена 1000 показов зоны
  spend: number;
  atbs: number;   // корзины (реклама)
  orders: number; // заказы (реклама)
  cpo: number | null;  // стоимость 1 заказа
}

/** Посуточные РК-метрики кампании по зоне показов (GET /campaigns/{id}/metrics/by-zone).
 *  zone: total — вся кампания; search — поиск; recommendations — итог минус поиск. */
export interface CampaignZoneMetricsResponse {
  campaign_id: number;
  name: string | null;
  zone: 'total' | 'search' | 'recommendations';
  window: ClusterWindow;
  totals: CampaignZoneMetricRow;
  rows: CampaignZoneMetricRow[];
  error?: string;
}

export interface ClusterProduct {
  nm_id: number;
  vendor_code?: string | null;
  brand?: string | null;
  imt_id?: number | null;  // склейка карточек WB
  views: number;
  clicks: number;
  orders: number;
  spend: number;
  drr: number | null;
  cpo: number | null;
  cr: number;
}

export interface CategoryClustersResponse {
  subject: string;
  window: ClusterWindow;
  aov: number;
  target_drr: number;
  campaigns_count: number;
  products_count: number;
  pairs: number;
  truncated: boolean;
  totals: ClusterTotals;
  clusters: SearchCluster[];
  products: ClusterProduct[];
  error?: string;
}

export interface AdCategory {
  subject: string;
  nm_count: number;
}

export interface ClusterMinusResult {
  ok: boolean;
  action?: 'add' | 'remove';
  norm_query?: string;
  minus?: string[];
  error?: string;
}

/** Результат установки CPM-ставки на кластер (кампания или артикул). */
export interface ClusterBidResult {
  ok: boolean;
  norm_query?: string;
  bid?: number | null;
  campaigns?: number;
  error?: string;
}

export interface ProductClusterCampaign {
  campaign_id: number;
  name: string;
}

/** Кластеры одного артикула (nm_id) со всех его CPM-поиск-кампаний. */
export interface ProductClustersResponse {
  nm_id: number;
  vendor_code: string | null;
  subject: string | null;
  campaigns: ProductClusterCampaign[];
  window: ClusterWindow;
  aov: number;
  target_drr: number;
  totals: ClusterTotals;
  daily: ClusterDailyPoint[];
  clusters: SearchCluster[];
  error?: 'no_api_key' | 'no_campaign_for_nm' | string;  // при ошибке остальные поля отсутствуют
}

export interface ProductMinusResult {
  ok: boolean;
  action?: 'add' | 'remove';
  norm_query?: string;
  campaigns?: number;
  error?: string;
}

/** Одна строка «По дням» карточки артикула (Decimal-поля могут прийти строкой → Number()). */
export interface ProductDailyRow {
  date: string;      // «YYYY-MM-DD» или «За всё время» у totals
  views: number;
  clicks: number;
  ctr: number;
  cpc: number;
  spend: number;
  opens: number;
  carts: number;
  cr1: number;
  orders: number;
  cr2: number;
  revenue: number;
  price: number | null;
  cpo: number | null;
  drr: number | null;
}

/** Дневная статистика артикула: РК + воронка продаж. */
export interface ProductDailyResponse {
  nm_id: number;
  vendor_code: string | null;
  window: { from: string; to: string };
  totals: ProductDailyRow;
  rows: ProductDailyRow[];
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
  bank_account?: string | null;
  bik?: string | null;
  bank_name?: string | null;
  corr_account?: string | null;
  created_by_import: boolean;
  created_at: string | null;
  updated_at: string | null;
  /** Per-period turnover; populated when date_from/date_to are passed. */
  income_rub?: number | null;
  expense_rub?: number | null;
  income_cny?: number | null;
  expense_cny?: number | null;
  tx_count?: number | null;
  /** Expense category (level-2), shown/managed in the list. */
  cat_lvl1?: string | null;
  cat_lvl2?: string | null;
}

export interface CounterpartyDetail extends CounterpartyListItem {
  /** Expense category (level-2), managed on the card and propagated to transactions. */
  cat_lvl1?: string | null;
  cat_lvl2?: string | null;
  stats_rub: CounterpartyStats;
  stats_cny: CounterpartyStats;
  linked_warehouses: { id: number; name: string; warehouse_type?: string }[];
  linked_suppliers: { id: number; name: string }[];
  active_loans: LoanShort[];
  docs_count: number;
}

export interface SetExpenseCategoryResponse {
  applied: number;
  cp_key: string;
  cat_lvl1: string | null;
  cat_lvl2: string | null;
}

export interface BulkCategoryResponse {
  counterparties: number;
  transactions: number;
}

export interface CounterpartyMergeResponse {
  target_id: number;
  source_id: number;
  moved: Record<string, number>;
  fields_filled: string[];
  inn_assigned: boolean;
  category_action: string;
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
  bank_account?: string | null;
  bik?: string | null;
  bank_name?: string | null;
  corr_account?: string | null;
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

/** Уже едет/зарезервировано на WB-склад активной заявкой (вкл. PRE_DISTRIBUTED). */
export interface InTransitItem {
  nm_id: number;
  warehouse_name: string;
  quantity: number;
}

export interface InTransitResponse {
  items: InTransitItem[];
}

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
  /** Сознательно отгруженная ЧАСТИЧНАЯ паллета («Оставить так» в предброни).
   *  normalizeDraft такие строки НЕ трогает; всё непомеченное приводится к
   *  «целые коробы + целые паллеты» при загрузке страницы (self-heal). */
  as_is?: boolean;
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
  /** Предбронь: целые коробы, не собравшиеся в целую паллету при «Заполнить черновик»
   *  (под-паллетный хвост). Не теряются на ФФ — отдельный список с действиями. */
  prebook?: AssemblyDraftRow[];
  /** Провенанс «из предброни»: ключи `${nm_id}::${wb}`, чей контент попал в rows из
   *  предброни (Оставить так / Дозабить / авто-консолидация). Только для бейджа на
   *  паллете раскладки; сбрасывается при полном «Заполнить из потребности». */
  prebook_origin?: string[];
  /** РУЧНЫЕ SKU (nm_id): план правлен в матрице-редакторе (степпер/✕) — авто-синк
   *  расчёта такие SKU не трогает, пока юзер не вернёт SKU «в авто». */
  manual_nms?: number[];
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
  /** Если задано — бэк логирует событие истории со снапшотом черновика (для отката). */
  event?: DraftEventLog;
}

export interface AssemblyDraftCommitResponse {
  created_request_ids: number[];
  draft_id: number;
}

/** Опциональный маркер для updateAssemblyDraft — логирует событие истории со снапшотом. */
export interface DraftEventLog {
  event_type: 'PREBOOK_TOPUP' | 'MATRIX_WRITE' | 'MATRIX_EDIT' | 'AUTO_SYNC';
  summary?: string;
}

/** Событие истории изменений черновика (новейшие первыми в списке). */
export interface DraftEvent {
  id: number;
  event_type: 'PREBOOK_TOPUP' | 'MATRIX_WRITE' | 'COMMIT_REQUEST' | string;
  summary: string | null;
  created_at: string;
  created_by: string | null;
  reverted_at: string | null;
  reverted_by: string | null;
  created_request_ids: number[] | null;
  can_revert: boolean;
  revert_blocked_reason: string | null;
}

export interface DraftHistoryResponse {
  events: DraftEvent[];
}

export interface DraftEventRevertResponse {
  reverted_event_id: number;
  event_type: string;
  restored_draft: boolean;
  deleted_request_ids: number[];
  draft: AssemblyDraft | null;
}

/** Явная отгрузка ФФ→склад для commit (режим «только целые паллеты»): заявка
 *  создаётся ровно из этих баркодов, минуя pro-rata распределение на бэке. */
export interface CommitSupply {
  source_ff_id: number;
  target_wb_name: string;
  package_type: string;
  /** barcode → штук. */
  items: Record<string, number>;
}

// ─── Прогноз загрузки WB-складов (вкладка «Прогноз/Локализация») ─────────────
export type TrafficLight = 'red' | 'orange' | 'yellow' | 'green';

export interface ForecastLeadTime {
  assembly_ship_days: number; // создание → отгрузка (сборка + отправка)
  delivery_days: number;      // отгрузка → приёмка WB
  total_days: number;
  has_history: boolean;       // false = использованы дефолты
}

export interface ForecastItem {
  warehouse_name: string;
  district: string;
  district_label: string;
  nm_id: number;
  vendor_code: string;
  current_stock: number;
  incoming: number;
  avg_daily: number;          // скорость продаж на складе (шт/день)
  projected_on_arrival: number; // остаток на момент прихода поставки (может быть <0)
  days_cover: number;         // дней покрытия после поставки
  traffic_light: TrafficLight;
}

export interface ForecastWarehouse {
  warehouse_name: string;
  district: string;
  district_label: string;
  sku_count: number;
  current_stock: number;
  incoming: number;
  traffic_light_counts: Record<string, number>;
}

export interface ForecastLocalizationDistrict {
  district: string;
  label: string;
  demand: number;
  avail_current: number;
  avail_after: number;
  local_pct_current: number;
  local_pct_after: number;
}

export interface ForecastLocalization {
  index_current: number;      // ИЛ (КТР-взвеш.), НИЖЕ = лучше
  index_after: number;
  avg_loc_pct_current: number; // средняя доля локализации %, ВЫШЕ = лучше
  avg_loc_pct_after: number;
  status_current: string;     // excellent / neutral / weak / critical
  status_after: string;
  horizon_days: number;
  by_district: ForecastLocalizationDistrict[];
}

export interface ForecastSkuLocalization {
  nm_id: number;
  loc_pct_current: number;
  loc_pct_after: number;
}

export interface ForecastSummary {
  sku_count: number;
  warehouse_count: number;
  total_incoming: number;
  traffic_light_counts: Record<string, number>;
}

export interface ForecastResponse {
  draft_id: number;
  lead_time: ForecastLeadTime;
  summary: ForecastSummary;
  localization: ForecastLocalization;
  sku_localization: ForecastSkuLocalization[];
  newcomer_nm_ids: number[];
  warehouses: ForecastWarehouse[];
  items: ForecastItem[];
  generated_at: string;
}

export interface AssemblyDraftMergeRequest {
  /** IDs of drafts to merge (≥2 distinct values). */
  draft_ids: number[];
}

// ─── Barcode eligibility (приёмка WB по баркоду: типы упаковки + лимиты + ФФ-остаток) ───

/** Доступность одного WB-склада для баркода: разрешённые типы упаковки + слоты приёмки. */
export interface BarcodeEligibilityTarget {
  wb_name: string;
  can_box: boolean;
  can_monopallet: boolean;
  can_supersafe: boolean;
  free_days_14: number;
  paid_days_14: number;
  no_limit: boolean;
}

/** Остаток баркода на конкретном ФФ-складе (источник отгрузки). */
export interface BarcodeFfStock {
  ff_id: number;
  ff_name: string;
  available: number;
}

export interface BarcodeEligibilityItem {
  nm_id: number;
  vendor_code: string;
  barcode: string;
  targets: BarcodeEligibilityTarget[];
  ff_stock: BarcodeFfStock[];
}

export interface BarcodeEligibilityResponse {
  items: BarcodeEligibilityItem[];
  /** Баркоды, не найденные в номенклатуре проекта. */
  unknown: string[];
  /** ISO-таймстамп проверки лимитов приёмки WB. */
  checked_at: string;
}

// ─── Stock need (потребность по складам) — shared, для «добавить из потребности» ───
// Бэкенд-ответ /warehouse/stock-need. Локальные копии живут в WarehouseNeedView.tsx;
// эти экспортируемые версии переиспользует панель «добавить из потребности».

export interface StockNeedRfWarehouse {
  id: number;
  name: string;
  assembly_days: number;
}

export interface StockNeedArticleRfStock {
  stock: number;
  available: number;
}

export interface StockNeedArticle {
  nm_id: number;
  vendor_code: string;
  barcode: string;
  brand: string;
  subject: string;
  total_need: number;
  revenue_30d: number;
  rf_stocks: Record<number, StockNeedArticleRfStock>;
  in_assembly: number;
  in_transit: number;
  in_transit_date: string | null;
  can_send: number;
  deficit: number;
  stocks_wb: number;
  /** Плоское среднее заказов за analysis_days, шт/день. */
  avg_daily_base?: number;
  /** Рабочая growth-aware скорость: max(окно, 7д, 3д), шт/день. */
  eff_avg_daily?: number;
  /** Коэффициент роста eff/base (≥1; ⚡ растущий SKU при ≥1.3). */
  growth_ratio?: number;
  /** Спрос-взвешенное плечо доставки (сборка+дорога+приёмка), дни. */
  lead_days?: number;
  /** На сколько дней хватит остатка на WB при eff-скорости (null — нет продаж). */
  wb_days_left?: number | null;
  /** То же, но с учётом «в сборке» и «в пути». */
  wb_days_left_inbound?: number | null;
  /** Раскладочная потребность: Σ локальных дефицитов складов (то, что реально
   *  хотим дослать). total_need — нетто по сети (KPI «сколько докупить»). */
  ship_need?: number;
  /** Per-WB-склад: сколько уже в сборке на этот склад (не учтено в need). */
  asm_by_warehouse?: Record<string, number>;
  /** Per-WB-склад: сколько уже едет на этот склад транзитом. */
  transit_by_warehouse?: Record<string, number>;
}

export interface StockNeedWbWarehouse {
  name: string;
  total_need: number;
  /** need_raw — остаточный дефицит клетки ДО greedy-налива (only_available);
   *  веса «Распределить все остатки» и паритет с сырыми клетками машины. */
  articles: Record<number, { need: number; stock: number; avg_daily: number; need_raw?: number }>;
  /** Ключ ФО (central|south_caucasus|volga|ural|far_east_siberia|northwest|abroad|unknown).
   *  Backend заполняет через `warehouse_to_district`. UI рендерит label под названием. */
  district_key?: string;
  /** Вес «схемы воришек» (якорь↑/воришка↓) — порядок среза при дефиците источника
   *  (паритет клиентского движка машины с серверным greedy черновика). */
  priority_weight?: number;
}

export interface StockNeedSummary {
  total_need: number;
  total_can_send: number;
  total_deficit: number;
  avg_delivery_days: number;
  deficit_count: number;
  can_send_count: number;
  no_wb_count: number;
  /** Целевая локализация (%), до которой ведётся распределение (по умолч. 75). */
  localization_target?: number;
  /** Спрос гео-непривязанных заказов (СНГ и пр.): учтён в total_need, но НЕ локальный. */
  unmapped_demand_qty?: number;
}

export interface StockNeedResponse {
  warehouses: StockNeedWbWarehouse[];
  articles: StockNeedArticle[];
  rf_warehouses: StockNeedRfWarehouse[];
  brands: string[];
  subjects: string[];
  supply_days: number;
  analysis_days: number;
  mode: string;
  total_warehouses: number;
  total_articles: number;
  summary: StockNeedSummary;
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
  /** Гвард пересорта: посев лежит на WB и не продаётся → авто-досев остановлен. */
  oversort_guard?: boolean;
  guard_reason?: string | null;
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

// Сводные лимиты на сдачу — календарь дат (GET /warehouse/acceptance-limits)
export type AcceptanceBoxType = 'box' | 'mono' | 'super';
export interface AcceptanceLimitDay {
  date: string; // ISO date
  coefficient: number; // -1 closed, 0..1 free, >=2 paid multiplier
  allow_unload: boolean;
  is_free: boolean;
  is_closed: boolean;
  storage_coef?: number | null;
  delivery_coef?: number | null;
}
export interface AcceptanceLimitEntry {
  warehouse_id: number;
  warehouse_name: string; // raw WB name
  canonical_name: string;
  box_type: AcceptanceBoxType;
  days: AcceptanceLimitDay[];
}
export interface AcceptanceLimitsResponse {
  warehouses: AcceptanceLimitEntry[];
  dates: string[];
  fetched_at: string;
}

// Слоты сдачи по поставкам — активные заявки + календарь приёмки их склада
// (GET /warehouse/acceptance-slots). matched=false → склад не нашёлся, days пуст.
export interface SupplyAcceptanceSlotRow {
  assembly_request_id: number;
  assembly_number: string;
  status: string;
  wb_supply_id?: string | null; // ФБО-поставка WB
  warehouse_name?: string | null; // effective: FBO или ручной
  canonical_name: string; // ключ группировки
  warehouse_id?: number | null;
  box_type: AcceptanceBoxType;
  package_type: string; // BOX | MONOPALLET | SUPERSAFE
  planned_date?: string | null; // плановая «Сдача ВБ»
  actual_date?: string | null;
  wb_fbo_status?: string | null;
  matched: boolean;
  days: AcceptanceLimitDay[];
}
export interface SupplyAcceptanceSlotsResponse {
  rows: SupplyAcceptanceSlotRow[];
  dates: string[];
  fetched_at: string;
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
  /** migfull: часть резерва под активные отгрузки (собрано) */
  ff_reserve_ready: number;
  /** migfull: часть резерва под свежий приход (позиции в EXPECTED-приёмках) */
  ff_inbound_locked: number;
  ff_defect: number;
  ff_nominal: number;
  /** из ff_good пришло коробами (в штуках россыпи) */
  ff_box_units: number;
  /** сколько коробов годного сведено в этот товар */
  ff_box_count: number;
  /** досчитано к ff_good: товар в стадии списания логистики ФФ, физически ещё на складе */
  ff_logistics: number;
  our_quantity: number;
  our_defect: number;
  /** ff_good - our_quantity (ff_good уже включает ff_logistics) */
  diff: number;
}

export interface FfStockTotals {
  ff_good: number;
  ff_reserve: number;
  /** migfull: резерв под активные отгрузки (собрано) */
  ff_reserve_ready: number;
  /** migfull: резерв под свежий приход (EXPECTED-приёмки) */
  ff_inbound_locked: number;
  ff_defect: number;
  /** сколько штук годного пришло коробами */
  ff_box_units: number;
  /** досчитано к ff_good: товар в стадии списания логистики ФФ */
  ff_logistics: number;
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
  /** состав нашего документа расходится с заявкой(ами) ФФ (true — расхождение, null — неизвестно) */
  linked_mismatch?: boolean | null;
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
  /** guid товара у ФФ (migfull) — для ручной привязки ШК на строках без номенклатуры */
  product_guid?: string | null;
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

/** Ручной ШК для товара ФФ без штрихкода в карточке (привязка по product_guid) */
export interface FfGuidBarcodeRow {
  product_guid: string;
  barcode: string;
  note: string | null;
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
  /** со сколькими ДРУГИМИ ФФ-заявками сборка уже связана (>0 только для migfull) */
  linked_ff_count: number;
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

// ─── WB Measurements (замеры складов + удержания за габариты) ────────────────

export interface WarehouseMeasurement {
  id: number;
  dim_id: number;
  nm_id: number;
  subject_name: string | null;
  brand: string | null;
  length: number | null;
  width: number | null;
  height: number | null;
  volume: string | null;
  /** текущий объём карточки WB (л) — для сравнения с замером */
  card_volume: string | null;
  photo_urls: string[] | null;
  measured_at: string | null;
}

export interface MeasurementPenalty {
  id: number;
  dim_id: number;
  nm_id: number;
  subject_name: string | null;
  brand: string | null;
  prc_over: string | null;
  /** фактический замер WB */
  act_length: number | null;
  act_width: number | null;
  act_height: number | null;
  act_volume: string | null;
  /** заявлено продавцом */
  dec_length: number | null;
  dec_width: number | null;
  dec_height: number | null;
  dec_volume: string | null;
  penalty_amount: string | null;
  reversal_amount: string | null;
  units_count: number | null;
  is_valid: boolean | null;
  is_valid_at: string | null;
  penalty_date: string | null;
  photo_urls: string[] | null;
}

export interface WarehouseMeasurementListResponse {
  items: WarehouseMeasurement[];
  total: number;
}

export interface MeasurementPenaltyListResponse {
  items: MeasurementPenalty[];
  total: number;
  total_penalty: string;
  total_reversal: string;
}

export interface MeasurementFiltersResponse {
  brands: string[];
  subjects: string[];
}

export interface MeasurementSyncResult {
  warehouse: number;
  penalties: number;
}

export interface PenaltyArticleSummaryRow {
  nm_id: number;
  subject_name: string | null;
  brand: string | null;
  total_penalty: string;
  total_reversal: string;
  net: string;
  penalties_count: number;
  measurements_count: number;
}

export interface PenaltyArticleSummaryResponse {
  items: PenaltyArticleSummaryRow[];
  articles: number;
  total_penalty: string;
  total_reversal: string;
  net: string;
}

// ─── Сырые данные (GET /raw-data/sources) ───────────────────────────────────

/** Прогресс принудительной дозагрузки источника (живёт в памяти бэкенда). */
export interface RawRefreshProgress {
  status: 'running' | 'ok' | 'error';
  started_at: string;
  finished_at: string | null;
  error: string | null;
  result?: Record<string, unknown> | null;
}

/** Один источник сырых данных: что копим и сколько накопили. */
export interface RawSource {
  key: string;
  title: string;
  group: string;         // Реклама / Продажи / Склад / Финансы
  table: string;         // имя таблицы в БД
  date_field: string;
  description: string;
  source: string;        // внешний API, откуда тянем
  schedule: string;      // как часто тянет планировщик
  refreshable: boolean;  // доступна ли кнопка дозагрузки
  refresh_hint: string | null;
  ranged: boolean;       // дозагрузка принимает период
  rows: number | null;
  first_date: string | null;
  last_date: string | null;
  progress: RawRefreshProgress | null;
}

export interface RawSourcesResponse {
  sources: RawSource[];
  groups: string[];
}

export interface RawRefreshStart {
  status: 'started' | 'already_running' | 'unsupported';
  error?: string;
}

/** Колонка таблицы источника — берётся из ORM-модели на бэкенде. */
export interface RawColumn {
  key: string;
  label: string;
  type: 'id' | 'date' | 'datetime' | 'number' | 'bool' | 'json' | 'string';
}

/** Содержимое таблицы источника (GET /raw-data/sources/{key}/rows). */
export interface RawSourceRows {
  key: string;
  title: string;
  table: string;
  date_field: string;
  description: string;
  source: string;
  columns: RawColumn[];
  rows: Record<string, unknown>[];
  total: number;
  limit: number;
  offset: number;
}

/** Предмет для создания кампании (GET /funnel/ad-subjects). */
export interface AdSubject { id: number; name: string; count: number }
/** Карточка товара для кампании (POST /funnel/ad-nms). */
export interface AdNmCard { nm: number; title: string; subjectId: number }
/** Результат создания кампании. */
export interface CreateCampaignResult { ok: boolean; campaign_id: number | null; error: string | null }

// ── АБ-тесты главного фото ───────────────────────────────────────────────────
export type AbTestStatus = 'draft' | 'running' | 'paused' | 'finished' | 'error';

export interface AbTestListItem {
  id: number;
  nm_id: number;
  campaign_id: number;
  name: string;
  status: AbTestStatus;
  pause_reason: string | null;
  title: string;
  vendor_code: string;
  variants_count: number;
  progress_pct: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface AbTestInfo {
  id: number;
  nm_id: number;
  campaign_id: number;
  name: string;
  comment: string | null;
  status: AbTestStatus;
  pause_reason: string | null;
  views_per_round: number;
  round_minutes: number;
  target_views: number;
  max_days: number;
  title: string;
  vendor_code: string;
  started_at: string | null;
  finished_at: string | null;
  winner_variant_id: number | null;
  winner_applied_at: string | null;
}

export interface AbTestVariantStats {
  id: number;
  position: number;
  is_control: boolean;
  excluded: boolean;
  is_active: boolean;
  is_winner: boolean;
  rounds: number;
  views: number;
  clicks: number;
  ctr: number;
  atbs: number;
  orders: number;
  spend: number;
  orders_sum: number;
  organic_open: number;
  organic_cart: number;
  organic_orders: number;
  round_wins: number;
  progress_pct: number;
  enough_data: boolean;
  ctr_gap: number | null;
}

export interface AbTestRoundRow {
  round_no: number;
  variant_id: number;
  started_at: string;
  ended_at: string | null;
  views: number;
  clicks: number;
  ctr: number;
  atbs: number;
  orders: number;
  organic_open: number;
  organic_cart: number;
  flags: Record<string, unknown>;
}

export interface AbTestResults {
  test: AbTestInfo;
  variants: AbTestVariantStats[];
  rounds: AbTestRoundRow[];
}

export interface AbTestCreatePayload {
  nm_id: number;
  campaign_id: number;
  name?: string;
  comment?: string | null;
  views_per_round?: number;
  round_minutes?: number;
  target_views?: number;
  max_days?: number;
}
