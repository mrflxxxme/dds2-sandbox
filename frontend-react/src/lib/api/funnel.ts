/** Funnel (Воронка продаж) API methods */
import { ApiClient } from './client';
import type {
    AdSubject, AdNmCard, CreateCampaignResult, FunnelDayRow, FunnelSkuRow, FunnelGroupRow, FunnelSummary, FunnelFilters, FunnelColorsResponse, FunnelSyncStatus, MessageResponse, MissingCostItem, WbTariff, WbTariffUploadResult, AnomaliesResponse, CapitalResponse, AdTabProduct, AdGlueRow, AdsManagerCampaign, AdsAccountBalance, AdsAutopayLogEntry, WbAutorefillResponse, WbAutorefillSetting, WbAutorefillSaveResult, AdsScheduleSetting, AdsScheduleLogEntry, AdsWbAutopayStatus, BudgetLedgerEntry, AdsCampaignStateResult, AdsScheduleSaveResult, AdsBudgetGap, AdsBudgetGapHistory, AdsHistoryPoint, UnifiedSyncProgress, FirstSyncProgress, CampaignClustersResponse, CampaignMetricsResponse, CampaignZoneMetricsResponse, CampaignHourlySpend, CampaignIntradayMetrics, PositionsResponse, PositionsProgress, CollectPositionsResult, CollectOneResult, CampaignZones,
    CampaignZonesUpdate, ClusterMinusResult, ClusterBidResult, ClusterBidBulkResult, AdCategory, CategoryClustersResponse, ProductClustersResponse, ProductMinusResult, ProductDailyResponse, FunnelTreeResponse, FunnelDimensionsResponse } from '@/types/api';

export function addFunnelMethods(api: ApiClient) {
    return {
        syncFunnel(dateFrom: string, dateTo: string) {
            return api.request<{ ok: boolean; days_synced: number }>('POST', '/api/v1/funnel/sync', { date_from: dateFrom, date_to: dateTo });
        },
        getFunnelData(params?: { date_from?: string; date_to?: string; brand?: string; vendor_code?: string; subject?: string; group_by?: 'day' | 'sku' | 'brand' | 'subject' | 'tag' | 'imt' | 'size' | 'abc'; extended?: boolean; min_orders?: number; tag?: string; imt?: string; color?: string; subcat?: boolean }) {
            const q = new URLSearchParams();
            if (params?.date_from) q.set('date_from', params.date_from);
            if (params?.date_to) q.set('date_to', params.date_to);
            if (params?.brand) q.set('brand', params.brand);
            if (params?.vendor_code) q.set('vendor_code', params.vendor_code);
            if (params?.subject) q.set('subject', params.subject);
            if (params?.group_by) q.set('group_by', params.group_by);
            if (params?.extended) q.set('extended', 'true');
            if (params?.min_orders) q.set('min_orders', String(params.min_orders));
            if (params?.tag) q.set('tag', params.tag);
            if (params?.imt) q.set('imt', params.imt);
            if (params?.color) q.set('color', params.color);
            if (params?.subcat) q.set('subcat', 'true');
            return api.request<{ data: (FunnelDayRow | FunnelSkuRow | FunnelGroupRow)[]; detailed: boolean; tax_rate: number; has_bdr?: boolean; group_by?: string }>('GET', `/api/v1/funnel/data?${q.toString()}`);
        },
        /** Воронка деревом по произвольной цепочке группировок: dims в порядке уровней. */
        /** path — ключи узлов-предков (раскрытие ветки), depth — сколько уровней материализовать.
         *  Без path приезжает только верхний уровень: полное дерево весит десятки мегабайт. */
        getFunnelTree(params: { dims: string[]; date_from?: string; date_to?: string; brand?: string; subject?: string; vendor_code?: string; extended?: boolean; min_orders?: number; tag?: string; imt?: string; color?: string; path?: string[]; depth?: number }) {
            const q = new URLSearchParams();
            q.set('group_by', params.dims.join(','));
            (params.path || []).forEach(k => q.append('path', k));
            if (params.depth) q.set('depth', String(params.depth));
            if (params.date_from) q.set('date_from', params.date_from);
            if (params.date_to) q.set('date_to', params.date_to);
            if (params.brand) q.set('brand', params.brand);
            if (params.subject) q.set('subject', params.subject);
            if (params.vendor_code) q.set('vendor_code', params.vendor_code);
            if (params.extended) q.set('extended', 'true');
            if (params.min_orders) q.set('min_orders', String(params.min_orders));
            if (params.tag) q.set('tag', params.tag);
            if (params.imt) q.set('imt', params.imt);
            if (params.color) q.set('color', params.color);
            return api.request<FunnelTreeResponse>('GET', `/api/v1/funnel/tree?${q.toString()}`);
        },
        /** Каталог измерений для конструктора группировок. */
        getFunnelDimensions() {
            return api.request<FunnelDimensionsResponse>('GET', '/api/v1/funnel/dimensions');
        },
        getFunnelColors(subject?: string, brand?: string) {
            const q = new URLSearchParams();
            if (subject) q.set('subject', subject);
            if (brand) q.set('brand', brand);
            return api.request<FunnelColorsResponse>('GET', `/api/v1/funnel/colors?${q.toString()}`);
        },
        getFunnelSummary(dateFrom?: string, dateTo?: string, brand?: string, subject?: string) {
            const q = new URLSearchParams();
            if (dateFrom) q.set('date_from', dateFrom);
            if (dateTo) q.set('date_to', dateTo);
            if (brand) q.set('brand', brand);
            if (subject) q.set('subject', subject);
            return api.request<FunnelSummary>('GET', `/api/v1/funnel/summary?${q.toString()}`);
        },
        getFunnelFilters() {
            return api.request<FunnelFilters>('GET', '/api/v1/funnel/filters');
        },
        getFunnelCosts() {
            return api.request<Array<{ nm_id: number; cost_price: number; source: string }>>('GET', '/api/v1/funnel/costs');
        },
        getMissingCosts() {
            return api.request<MissingCostItem[]>('GET', '/api/v1/funnel/missing_costs');
        },
        setFunnelCost(nmId: number, costPrice: number) {
            return api.request<MessageResponse>('POST', '/api/v1/funnel/cost', { nm_id: nmId, cost_price: costPrice });
        },
        bulkSetFunnelCosts(items: { barcode: string; cost_price: number; currency?: string }[]) {
            return api.request<{ saved: number; not_found: string[]; errors: string[] }>('POST', '/api/v1/funnel/costs/bulk', { items });
        },
        getFunnelTax() {
            return api.request<{ tax_rate: number }>('GET', '/api/v1/funnel/tax');
        },
        setFunnelTax(taxRate: number) {
            return api.request<MessageResponse>('POST', '/api/v1/funnel/tax', { tax_rate: taxRate });
        },
        getSyncStatus() {
            return api.request<FunnelSyncStatus>('GET', '/api/v1/funnel/sync_status');
        },
        getDayAnalysis(params: { target_date: string; brand?: string; subject?: string; trend_days?: number }) {
            const q = new URLSearchParams();
            q.set('target_date', params.target_date);
            if (params.brand) q.set('brand', params.brand);
            if (params.subject) q.set('subject', params.subject);
            if (params.trend_days) q.set('trend_days', String(params.trend_days));
            return api.request<Record<string, unknown>>('GET', `/api/v1/funnel/day-analysis?${q.toString()}`);
        },
        getProductTrends(params: { trend_days?: number; brand?: string; search?: string } = {}) {
            const q = new URLSearchParams();
            if (params.trend_days) q.set('trend_days', String(params.trend_days));
            if (params.brand) q.set('brand', params.brand);
            if (params.search) q.set('search', params.search);
            return api.request<{ products: any[]; trend_days: number; total_products: number }>('GET', `/api/v1/funnel/trends?${q.toString()}`);
        },
        getTariffs() {
            return api.request<WbTariff[]>('GET', '/api/v1/funnel/tariffs');
        },
        uploadTariffs(file: File) {
            const formData = new FormData();
            formData.append('file', file);
            return api.uploadFormData<WbTariffUploadResult>('/api/v1/funnel/tariffs/upload', formData);
        },
        deleteTariff(id: number) {
            return api.request<{ status: string }>('DELETE', `/api/v1/funnel/tariffs/${id}`);
        },
        getAnomalies(params?: { period_days?: number; brand?: string; include_rf_stocks?: boolean; min_orders?: number }) {
            const q = new URLSearchParams();
            if (params?.period_days) q.set('period_days', String(params.period_days));
            if (params?.brand) q.set('brand', params.brand);
            if (params?.include_rf_stocks != null) q.set('include_rf_stocks', String(params.include_rf_stocks));
            if (params?.min_orders) q.set('min_orders', String(params.min_orders));
            return api.request<AnomaliesResponse>('GET', `/api/v1/funnel/anomalies?${q.toString()}`);
        },
        getCapital(params?: { period_days?: number; brand?: string; group_by?: string; parent_filter?: string; include_rf_stocks?: boolean; elasticity?: number; illiquid_threshold?: number }) {
            const q = new URLSearchParams();
            if (params?.period_days) q.set('period_days', String(params.period_days));
            if (params?.brand) q.set('brand', params.brand);
            if (params?.group_by) q.set('group_by', params.group_by);
            if (params?.parent_filter) q.set('parent_filter', params.parent_filter);
            if (params?.include_rf_stocks != null) q.set('include_rf_stocks', String(params.include_rf_stocks));
            if (params?.elasticity != null) q.set('elasticity', String(params.elasticity));
            if (params?.illiquid_threshold != null) q.set('illiquid_threshold', String(params.illiquid_threshold));
            return api.request<CapitalResponse>('GET', `/api/v1/funnel/capital?${q.toString()}`);
        },
        getAdTab(params: { date_from: string; date_to: string; brand?: string; subject?: string; group_by?: string }) {
            const q = new URLSearchParams({ date_from: params.date_from, date_to: params.date_to });
            if (params.brand) q.set('brand', params.brand);
            if (params.subject) q.set('subject', params.subject);
            if (params.group_by) q.set('group_by', params.group_by);
            return api.request<AdTabProduct[]>('GET', `/api/v1/funnel/ad_tab?${q.toString()}`);
        },
        getAdGlue(params: { date_from: string; date_to: string; brand?: string; subject?: string }) {
            const q = new URLSearchParams({ date_from: params.date_from, date_to: params.date_to });
            if (params.brand) q.set('brand', params.brand);
            if (params.subject) q.set('subject', params.subject);
            return api.request<AdGlueRow[]>('GET', `/api/v1/funnel/ad_glue?${q.toString()}`);
        },
        getAdCampaignsList(dateFrom?: string, dateTo?: string) {
            const q = new URLSearchParams();
            if (dateFrom) q.set('date_from', dateFrom);
            if (dateTo) q.set('date_to', dateTo);
            return api.request<AdsManagerCampaign[]>('GET', `/api/v1/funnel/campaigns?${q.toString()}`);
        },
        getAdsBudgetGaps() {
            return api.request<AdsBudgetGap[]>('GET', '/api/v1/funnel/campaigns/budget_gaps');
        },
        getBudgetGapHistory(campaignId: number, days = 30) {
            const q = new URLSearchParams({ days: String(days) });
            return api.request<AdsBudgetGapHistory>('GET', `/api/v1/funnel/campaigns/${campaignId}/budget_gap_history?${q.toString()}`);
        },
        getCampaignHistory(campaignId: number, dateFrom?: string, dateTo?: string) {
            const q = new URLSearchParams();
            if (dateFrom) q.set('date_from', dateFrom);
            if (dateTo) q.set('date_to', dateTo);
            return api.request<AdsHistoryPoint[]>('GET', `/api/v1/funnel/campaigns/${campaignId}/history?${q.toString()}`);
        },
        getAdArticleCatalog() {
            return api.request<{ nm_id: number; vendor_code: string; subject: string; brand: string }[]>('GET', '/api/v1/funnel/ad-article-catalog');
        },
        getCampaignMetrics(campaignId: number, dateFrom?: string, dateTo?: string, nmId?: number | null, zone?: string | null) {
            const q = new URLSearchParams();
            if (dateFrom) q.set('date_from', dateFrom);
            if (dateTo) q.set('date_to', dateTo);
            if (nmId != null) q.set('nm_id', String(nmId));
            if (zone) q.set('zone', zone);
            return api.request<CampaignMetricsResponse>('GET', `/api/v1/funnel/campaigns/${campaignId}/metrics?${q.toString()}`);
        },
        /** Посуточные РК-метрики кампании по зоне показов (total | search | recommendations). */
        getCampaignZoneMetrics(campaignId: number, dateFrom?: string, dateTo?: string, zone: 'total' | 'search' | 'recommendations' = 'total') {
            const q = new URLSearchParams();
            if (dateFrom) q.set('date_from', dateFrom);
            if (dateTo) q.set('date_to', dateTo);
            q.set('zone', zone);
            return api.request<CampaignZoneMetricsResponse>('GET', `/api/v1/funnel/campaigns/${campaignId}/metrics/by-zone?${q.toString()}`);
        },
        /** Почасовой расход кампании за день (восстановлен из снимков бюджета). */
        getCampaignHourly(campaignId: number, date?: string) {
            const q = new URLSearchParams();
            if (date) q.set('date', date);
            const qs = q.toString();
            return api.request<CampaignHourlySpend>('GET', `/api/v1/funnel/campaigns/${campaignId}/hourly${qs ? `?${qs}` : ''}`);
        },
        /** Внутридневные показы/клики/расход по интервалам (снимки официальной статистики WB). */
        getCampaignIntraday(campaignId: number, date?: string) {
            const q = new URLSearchParams();
            if (date) q.set('date', date);
            const qs = q.toString();
            return api.request<CampaignIntradayMetrics>('GET', `/api/v1/funnel/campaigns/${campaignId}/intraday${qs ? `?${qs}` : ''}`);
        },
        /** Частота внутридневных снимков рекламы (проект-глобально): 10/20/30/60 мин. */
        setAdsSnapshotInterval(intervalMin: number) {
            return api.request<{ interval_min: number }>('PUT', '/api/v1/funnel/ads/snapshot-interval', { interval_min: intervalMin });
        },
        /** Органические позиции товара по фразам (для кластеризатора): {phrase: {position, prev, depth, at}}. */
        getPositions(nmId: number) {
            return api.request<PositionsResponse>('GET', `/api/v1/funnel/positions?nm_id=${nmId}`);
        },
        /** Прогресс фонового сбора позиций для nm_id. */
        getPositionsProgress(nmId: number) {
            return api.request<PositionsProgress>('GET', `/api/v1/funnel/positions/progress?nm_id=${nmId}`);
        },
        /** Запустить фоновый сбор органических позиций по фразам из публичного поиска WB (Play). */
        collectPositions(nmId: number, phrases: string[], maxPages = 5) {
            return api.request<CollectPositionsResult>('POST', '/api/v1/funnel/positions/collect', { nm_id: nmId, phrases, max_pages: maxPages });
        },
        /** Синхронно собрать позицию одной фразы (кнопка-кругляшок в ячейке). */
        collectPositionOne(nmId: number, phrase: string, maxPages = 5) {
            return api.request<CollectOneResult>('POST', '/api/v1/funnel/positions/collect-one', { nm_id: nmId, phrase, max_pages: maxPages });
        },
        /** Остановить фоновый сбор позиций (Stop). */
        stopPositions(nmId: number) {
            return api.request<{ stopping: boolean }>('POST', `/api/v1/funnel/positions/stop?nm_id=${nmId}`, {});
        },
        /** Сменить ставку кампании для зоны (Поиск/Рекомендации). Реальные деньги.
         *  adjusted=true — WB поднял ставку до своего минимума (bid — применённая ставка). */
        setCampaignZoneBid(campaignId: number, zone: 'search' | 'recommendations', bid: number) {
            return api.request<{ ok: boolean; error: string | null; bid: number | null; adjusted?: boolean; min_bid?: number }>('PUT', `/api/v1/funnel/campaigns/${campaignId}/zone-bid`, { zone, bid });
        },
        /** Сменить ставку кампании из списка (инлайн, по активной зоне). Реальные деньги.
         *  Можно ввести любую ставку — WB поднимет до минимума (adjusted=true, bid — применённая). */
        setCampaignBid(campaignId: number, bid: number) {
            return api.request<{ ok: boolean; error: string | null; bid: number | null; adjusted?: boolean; min_bid?: number }>('PUT', `/api/v1/funnel/campaigns/${campaignId}/bid`, { bid });
        },
        /** Родная настройка автопополнения ВБ из кабинета (+ история доливов ВБ). */
        getCampaignAutorefill(campaignId: number) {
            return api.request<WbAutorefillResponse>('GET', `/api/v1/funnel/campaigns/${campaignId}/autorefill`);
        },
        /** Изменить автопополнение в кабинете ВБ. Реальное правило трат. */
        setCampaignAutorefill(campaignId: number, setting: Omit<WbAutorefillSetting, 'status' | 'history'>) {
            return api.request<WbAutorefillSaveResult>('POST', `/api/v1/funnel/campaigns/${campaignId}/autorefill`, setting);
        },
        /** Журнал пополнений (авто + ручные), новые первыми. */
        getAutopayLog(campaignId?: number) {
            const q = new URLSearchParams();
            if (campaignId != null) q.set('campaign_id', String(campaignId));
            const qs = q.toString();
            return api.request<AdsAutopayLogEntry[]>('GET', `/api/v1/funnel/campaigns/autopay/log${qs ? `?${qs}` : ''}`);
        },
        /** Кошелёк кабинета Продвижения WB: счёт / баланс взаиморасчётов / бонусы. */
        getAdsBalance() {
            return api.request<AdsAccountBalance>('GET', '/api/v1/funnel/ads/balance');
        },
        /** Ручное пополнение бюджета кампании (реальные деньги). */
        depositCampaignBudget(campaignId: number, amount: number, source = 0) {
            return api.request<{ ok: boolean; status: string | null; budget_after: number | null; error: string | null }>(
                'POST', `/api/v1/funnel/campaigns/${campaignId}/deposit`, { amount, source });
        },
        getCampaignsSchedule() {
            return api.request<Record<string, AdsScheduleSetting>>('GET', '/api/v1/funnel/campaigns/schedule');
        },
        /** Детект автопополнения ВБ по фактическим доливам бюджета (событиям) за 7 дней. */
        getWbAutopayStatus(campaignId?: number) {
            const q = new URLSearchParams();
            if (campaignId != null) q.set('campaign_id', String(campaignId));
            const qs = q.toString();
            return api.request<Record<string, AdsWbAutopayStatus>>('GET', `/api/v1/funnel/campaigns/wb-autopay${qs ? `?${qs}` : ''}`);
        },
        setCampaignSchedule(campaignId: number, setting: AdsScheduleSetting) {
            return api.request<AdsScheduleSaveResult>('POST', '/api/v1/funnel/campaigns/schedule', { campaign_id: campaignId, ...setting });
        },
        /** Запустить (active=true) или поставить кампанию на паузу (false) в WB. */
        setCampaignState(campaignId: number, active: boolean) {
            return api.request<AdsCampaignStateResult>('POST', `/api/v1/funnel/campaigns/${campaignId}/state`, { active });
        },
        /** Точечный догруз ОДНОЙ кампании из WB (деталь + бюджет + свежая дневная стата) в зеркало. */
        refreshCampaign(campaignId: number) {
            return api.request<{ ok: boolean; error: string | null }>('POST', `/api/v1/funnel/campaigns/${campaignId}/refresh`);
        },
        /** Завершить кампанию в WB (необратимо). */
        stopCampaign(campaignId: number) {
            return api.request<{ ok: boolean; status?: number; error: string | null }>('POST', `/api/v1/funnel/campaigns/${campaignId}/stop`);
        },
        /** Переименовать кампанию в WB. */
        renameCampaign(campaignId: number, name: string) {
            return api.request<{ ok: boolean; name?: string; error: string | null }>('POST', `/api/v1/funnel/campaigns/${campaignId}/rename`, { name });
        },
        /** Удалить кампанию в WB (только статус «Готова»). */
        deleteCampaign(campaignId: number) {
            return api.request<{ ok: boolean; error: string | null }>('DELETE', `/api/v1/funnel/campaigns/${campaignId}`);
        },
        /** Добавить/убрать товары кампании в WB. */
        changeCampaignNms(campaignId: number, body: { add?: number[]; delete?: number[] }) {
            return api.request<{ ok: boolean; error: string | null }>('PATCH', `/api/v1/funnel/campaigns/${campaignId}/nms`, body);
        },
        /** Ставки карточек товаров (по зонам). */
        setCardBids(campaignId: number, bids: { nm_id: number; bid_rub: number; placement: string }[]) {
            return api.request<{ ok: boolean; error: string | null }>('PATCH', `/api/v1/funnel/campaigns/${campaignId}/card-bids`, { bids });
        },
        /** Предметы, по которым можно создать кампанию. */
        getAdSubjects() {
            return api.request<{ subjects: AdSubject[]; error?: string }>('GET', '/api/v1/funnel/ad-subjects');
        },
        /** Карточки товаров для кампании по предметам. */
        getAdNms(subjectIds: number[]) {
            return api.request<{ nms: AdNmCard[]; error?: string }>('POST', '/api/v1/funnel/ad-nms', { subject_ids: subjectIds });
        },
        /** Создать рекламную кампанию в WB. */
        createCampaign(body: { name: string; nms: number[]; bid_type: string; payment_type: string; placement_types?: string[] | null }) {
            return api.request<CreateCampaignResult>('POST', '/api/v1/funnel/campaigns/create', body);
        },
        getScheduleLog(campaignId?: number) {
            const q = new URLSearchParams();
            if (campaignId != null) q.set('campaign_id', String(campaignId));
            const qs = q.toString();
            return api.request<AdsScheduleLogEntry[]>('GET', `/api/v1/funnel/campaigns/schedule/log${qs ? `?${qs}` : ''}`);
        },
        getBudgetLedger(campaignId?: number, kind?: 'topup' | 'charge') {
            const q = new URLSearchParams();
            if (campaignId != null) q.set('campaign_id', String(campaignId));
            if (kind) q.set('kind', kind);
            const qs = q.toString();
            return api.request<BudgetLedgerEntry[]>('GET', `/api/v1/funnel/campaigns/budget/ledger${qs ? `?${qs}` : ''}`);
        },
        syncAdCampaigns(priorityCampaignIds: number[] = []) {
            // priorityCampaignIds — кампании под текущим фильтром страницы: их бюджеты
            // бэкенд тянет первыми, остальные — следом.
            return api.request<{ status: string }>('POST', '/api/v1/funnel/sync_campaigns', {
                priority_campaign_ids: priorityCampaignIds,
            });
        },
        getSyncCampaignsProgress() {
            // last_sync_at — ISO UTC (с Z) момента последнего успешного синка, null если не было
            return api.request<{ status: string; campaigns_total?: number; budgets_done?: number; budgets_total?: number; error?: string; last_sync_at?: string | null }>('GET', '/api/v1/funnel/sync_campaigns_progress');
        },
        syncFunnelBg(dateFrom: string, dateTo: string) {
            return api.request<{ status: string }>('POST', `/api/v1/funnel/sync_funnel_bg?date_from=${dateFrom}&date_to=${dateTo}`);
        },
        getSyncFunnelProgress() {
            return api.request<{ status: string; days_total?: number; days_done?: number; rows?: number; error?: string }>('GET', '/api/v1/funnel/sync_funnel_progress');
        },
        unifiedSync(dateFrom: string, dateTo: string) {
            return api.request<{ status: string }>('POST', `/api/v1/funnel/unified_sync?date_from=${dateFrom}&date_to=${dateTo}`);
        },
        getUnifiedSyncProgress() {
            return api.request<UnifiedSyncProgress>('GET', '/api/v1/funnel/unified_sync_progress');
        },
        firstSync() {
            return api.request<{ status: string }>('POST', '/api/v1/funnel/first_sync');
        },
        // ─── Кластеризатор рекламных запросов ───
        getCampaignZones(campaignId: number, dateFrom?: string, dateTo?: string, nmId?: number | null) {
            const q = new URLSearchParams();
            if (dateFrom) q.set('date_from', dateFrom);
            if (dateTo) q.set('date_to', dateTo);
            if (nmId != null) q.set('nm_id', String(nmId));
            return api.request<CampaignZones>('GET', `/api/v1/funnel/campaigns/${campaignId}/zones?${q.toString()}`);
        },
        /** Включить/выключить зоны показов (только CPM с ручной ставкой). Обе зоны — WB заменяет набор целиком. */
        setCampaignZones(campaignId: number, body: { search: boolean; recommendations: boolean }) {
            return api.request<CampaignZonesUpdate>('PUT', `/api/v1/funnel/campaigns/${campaignId}/zones`, body);
        },
        getCampaignClusters(campaignId: number, dateFrom: string, dateTo: string, nmId?: number | null) {
            const q = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
            if (nmId != null) q.set('nm_id', String(nmId));
            return api.request<CampaignClustersResponse>('GET', `/api/v1/funnel/campaigns/${campaignId}/clusters?${q.toString()}`);
        },
        toggleClusterMinus(campaignId: number, body: { nm_id: number; norm_query: string; action: 'add' | 'remove' }) {
            return api.request<ClusterMinusResult>('POST', `/api/v1/funnel/campaigns/${campaignId}/clusters/minus`, body);
        },
        // basis_* / source — «паспорт» применения для колонки «Стоит»: с какого дня и от каких данных.
        setCampaignClusterBid(campaignId: number, body: { nm_id: number; norm_query: string; bid: number; verify?: boolean; source?: string; basis_drr?: number | null; basis_cpm?: number | null; target_drr?: number | null }) {
            return api.request<ClusterBidResult>('POST', `/api/v1/funnel/campaigns/${campaignId}/clusters/bid`, body);
        },
        // Массовая ставка ОДНИМ запросом (WB normquery/bids батчевый) — мгновенно, без rate-limit 429.
        setCampaignClusterBidsBulk(campaignId: number, items: { nm_id: number; norm_query: string; bid: number; source?: string; basis_drr?: number | null; basis_cpm?: number | null; target_drr?: number | null }[]) {
            return api.request<ClusterBidBulkResult>('POST', `/api/v1/funnel/campaigns/${campaignId}/clusters/bid-bulk`, { items });
        },
        getAdCategories() {
            return api.request<AdCategory[]>('GET', '/api/v1/funnel/categories');
        },
        getCategoryClusters(subject: string, dateFrom: string, dateTo: string) {
            const q = new URLSearchParams({ subject, date_from: dateFrom, date_to: dateTo });
            return api.request<CategoryClustersResponse>('GET', `/api/v1/funnel/categories/clusters?${q.toString()}`);
        },
        getProductClusters(nmId: number, dateFrom: string, dateTo: string) {
            const q = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
            return api.request<ProductClustersResponse>('GET', `/api/v1/funnel/products/${nmId}/clusters?${q.toString()}`);
        },
        getProductDaily(nmId: number, dateFrom: string, dateTo: string): Promise<ProductDailyResponse> {
            const q = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
            return api.request<ProductDailyResponse>('GET', `/api/v1/funnel/products/${nmId}/daily?${q.toString()}`);
        },
        toggleProductClusterMinus(nmId: number, body: { norm_query: string; action: 'add' | 'remove' }) {
            return api.request<ProductMinusResult>('POST', `/api/v1/funnel/products/${nmId}/clusters/minus`, body);
        },
        setProductClusterBid(nmId: number, body: { norm_query: string; bid: number }) {
            return api.request<ClusterBidResult>('POST', `/api/v1/funnel/products/${nmId}/clusters/bid`, body);
        },
        getFirstSyncProgress() {
            return api.request<FirstSyncProgress>('GET', '/api/v1/funnel/first_sync_progress');
        },
    };
}
