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
  joined_at: string;
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

export interface FunnelDataResponse {
  data: FunnelDayRow[];
  detailed: boolean;
  tax_rate: number;
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

export interface ChartTooltipProps {
  active?: boolean;
  payload?: ChartTooltipPayloadItem[];
  label?: string;
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
