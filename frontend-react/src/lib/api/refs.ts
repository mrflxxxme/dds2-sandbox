/** Refs API methods */
import { ApiClient } from './client';
import type {
    Account,
    CategoryRef,
    MessageResponse,
    ProductTag,
    ProductTagMappingPayload,
    ProductStatusPayload,
    ProductStatusBulkPayload,
    ProductSubcategory,
    DetectedSize,
    FunnelProductsResponse,
    PalletCategoryCompat,
} from '@/types/api';

export function addRefMethods(api: ApiClient) {
    return {
        // Accounts
        getAccounts() { return api.request<Account[]>('GET', '/api/v1/refs/accounts'); },
        upsertAccount(data: Partial<Account>) { return api.request<Account>('POST', '/api/v1/refs/accounts', data); },
        deleteAccount(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/refs/accounts/${id}`); },

        // Категории контрагентов ведутся на странице «Контрагенты» (counterparty.ts).

        // Overrides
        getOverrides() { return api.request<Array<{ id: number; field: string; pattern: string; value: string }>>('GET', '/api/v1/refs/overrides'); },
        deleteOverride(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/refs/overrides/${id}`); },

        // Opening balances
        getOpeningBalances() { return api.request<Array<{ id: number; account: string; currency: string; amount: number; date: string }>>('GET', '/api/v1/refs/opening_balances'); },
        upsertOpeningBalance(data: { account: string; currency: string; amount: number; date: string }) { return api.request<MessageResponse>('POST', '/api/v1/refs/opening_balances', data); },

        // Category reference
        getCategoryRef() { return api.request<CategoryRef[]>('GET', '/api/v1/refs/categories'); },
        addCategoryRef(data: Partial<CategoryRef>) { return api.request<CategoryRef>('POST', '/api/v1/refs/categories', data); },
        updateCategoryRef(id: number, is_cogs: boolean) { return api.request<MessageResponse>('PATCH', `/api/v1/refs/categories/${id}`, { is_cogs }); },
        deleteCategoryRef(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/refs/categories/${id}`); },

        // WB Warehouses (all WB-side names: WAREHOUSE_COORDS + DB observed). NB: project FF warehouses are in warehouse.getWarehouses().
        getAllWbWarehouses() { return api.request<Array<{ name: string; lat: number; lng: number; is_sorting_center?: boolean }>>('GET', '/api/v1/refs/warehouses'); },
        getExcludedWarehouses() { return api.request<string[]>('GET', '/api/v1/refs/excluded-warehouses'); },
        setExcludedWarehouses(warehouses: string[]) { return api.request<{ ok: boolean; excluded: string[] }>('PUT', '/api/v1/refs/excluded-warehouses', { warehouses }); },
        // 🔥 Сгоревшие/потерянные склады WB: их остатки не учитываются в расчётах (потребность/запас/срочность),
        // но страницы «факта» (Остатки по складам, Сводные) показывают их как есть. Независим от excluded-warehouses.
        getStockIgnoredWarehouses() { return api.request<string[]>('GET', '/api/v1/refs/stock-ignored-warehouses'); },
        setStockIgnoredWarehouses(warehouses: string[]) { return api.request<{ ok: boolean; stock_ignored: string[] }>('PUT', '/api/v1/refs/stock-ignored-warehouses', { warehouses }); },
        // Whitelist складов, куда можно делать предзаявку без приёмочного лимита (⌛). Склад вне списка вырезается из расчёта.
        getPreorderAllowedWarehouses() { return api.request<string[]>('GET', '/api/v1/refs/preorder-allowed-warehouses'); },
        setPreorderAllowedWarehouses(warehouses: string[]) { return api.request<{ ok: boolean; preorder_allowed: string[] }>('PUT', '/api/v1/refs/preorder-allowed-warehouses', { warehouses }); },
        // Ручной override «коробок на паллету» по размеру коробки (canonical box_size → int). Перебивает геометрию.
        getPalletBoxesBySize() { return api.request<Record<string, number>>('GET', '/api/v1/refs/pallet-boxes-by-size'); },
        setPalletBoxesBySize(sizes: Record<string, number>) { return api.request<{ ok: boolean; sizes: Record<string, number> }>('PUT', '/api/v1/refs/pallet-boxes-by-size', { sizes }); },
        // Правила совместимости категорий на BOX-паллете: enabled + группы категорий, которым можно ехать вместе.
        getPalletCategoryCompat() { return api.request<PalletCategoryCompat>('GET', '/api/v1/refs/pallet-category-compat'); },
        setPalletCategoryCompat(payload: PalletCategoryCompat) { return api.request<{ ok: boolean } & PalletCategoryCompat>('PUT', '/api/v1/refs/pallet-category-compat', payload); },
        getForecastRfDefaultDays() { return api.request<{ days: number }>('GET', '/api/v1/refs/forecast-rf-default-days'); },
        setForecastRfDefaultDays(days: number) { return api.request<{ ok: boolean; days: number }>('PUT', '/api/v1/refs/forecast-rf-default-days', { days }); },
        // Вес коробки (кг) — одно число на проект; прибавляется к нетто товаров × число коробов при авто-расчёте веса отгрузки. null = не задан.
        getBoxWeight() { return api.request<{ weight_kg: number | null }>('GET', '/api/v1/refs/box-weight'); },
        setBoxWeight(weightKg: number) { return api.request<{ ok: boolean; weight_kg: number }>('PUT', '/api/v1/refs/box-weight', { weight_kg: weightKg }); },

        // Product Tags
        getProductTags() { return api.request<ProductTag[]>('GET', '/api/v1/refs/tags'); },
        upsertProductTag(data: Partial<ProductTag>) { return api.request<ProductTag>('POST', '/api/v1/refs/tags', data); },
        deleteProductTag(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/refs/tags/${id}`); },
        getProductTagMapping() { return api.request<Record<string, number[]>>('GET', '/api/v1/refs/tags/mapping'); },
        updateProductTagMapping(payload: ProductTagMappingPayload) { return api.request<MessageResponse>('POST', '/api/v1/refs/tags/mapping', payload); },

        // Product Statuses
        getProductStatuses() { return api.request<Record<string, string>>('GET', '/api/v1/refs/product-statuses'); },
        setProductStatus(data: ProductStatusPayload) { return api.request<MessageResponse>('PATCH', '/api/v1/refs/product-statuses', data); },
        bulkSetProductStatus(data: ProductStatusBulkPayload) { return api.request<MessageResponse>('POST', '/api/v1/refs/product-statuses/bulk', data); },

        // IMT Aliases
        getImtAliases() { return api.request<Record<string, string>>('GET', '/api/v1/refs/imt-aliases'); },
        setImtAlias(data: { imt_id: number; name: string }) { return api.request<MessageResponse>('PATCH', '/api/v1/refs/imt-aliases', data); },

        // Sizes (overrides + aliases): размер = оверрайд → парсинг артикула → «Без размера», затем алиас
        getSizes() { return api.request<DetectedSize[]>('GET', '/api/v1/refs/sizes'); },
        getSizeOverrides() { return api.request<Record<string, string>>('GET', '/api/v1/refs/size-overrides'); },
        bulkSetSizeOverride(nmIds: number[], sizeValue: string) { return api.request<MessageResponse>('POST', '/api/v1/refs/size-overrides', { nm_ids: nmIds, size_value: sizeValue }); },
        getSizeAliases() { return api.request<Record<string, string>>('GET', '/api/v1/refs/size-aliases'); },
        setSizeAlias(rawSize: string, displayName: string) { return api.request<MessageResponse>('PATCH', '/api/v1/refs/size-aliases', { raw_size: rawSize, display_name: displayName }); },

        // Category overrides: категория = оверрайд → предмет WB (subject) → «Без категории»
        getCategoryOverrides() { return api.request<Record<string, string>>('GET', '/api/v1/refs/category-overrides'); },
        bulkSetCategoryOverride(nmIds: number[], categoryValue: string) { return api.request<MessageResponse>('POST', '/api/v1/refs/category-overrides', { nm_ids: nmIds, category_value: categoryValue }); },

        // Barcode → nm_id map (массовая привязка размер/под-кат/категория по баркодам из Excel)
        getBarcodeMap() { return api.request<Record<string, number>>('GET', '/api/v1/refs/barcode-map'); },

        // Sub-categories (винтаж/обычные — одна на товар)
        getSubcategories() { return api.request<ProductSubcategory[]>('GET', '/api/v1/refs/subcategories'); },
        upsertSubcategory(data: Partial<ProductSubcategory>) { return api.request<ProductSubcategory>('POST', '/api/v1/refs/subcategories', data); },
        deleteSubcategory(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/refs/subcategories/${id}`); },
        getSubcategoryMapping() { return api.request<Record<string, number>>('GET', '/api/v1/refs/subcategories/mapping'); },
        bulkSetSubcategory(nmIds: number[], subcategoryId: number | null) { return api.request<MessageResponse>('POST', '/api/v1/refs/subcategories/mapping', { nm_ids: nmIds, subcategory_id: subcategoryId }); },

        // Funnel Products (for tag/status assignment)
        getFunnelProducts() { return api.request<FunnelProductsResponse>('GET', '/api/v1/funnel/products'); },
    };
}
