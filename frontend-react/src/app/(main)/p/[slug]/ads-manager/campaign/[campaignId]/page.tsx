'use client';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import type { AdsManagerCampaign, WbAutorefillSetting, AdsScheduleSetting, CampaignClustersResponse, CampaignMetricRow, CampaignMetricsResponse, CampaignZoneMetricsResponse, CampaignHourlySpend, CampaignIntradayMetrics, PositionSnapshot, CampaignZones, SearchCluster } from '@/types/api';
import { IcChart, IcClusters, IcCalendar, IcRefresh, IcPause, IcPlay, IcExternal, IcClock, IcGear, IcHistory, IcCopy, IcPlus, IcWallet } from '../../components/icons';
import ClusterTable from '../../components/ClusterTable';
import CampaignMetricsTable from '../../components/CampaignMetricsTable';
import CampaignZoneMetricsTable from '../../components/CampaignZoneMetricsTable';
import CampaignHourlyChart from '../../components/CampaignHourlyChart';
import CampaignIntradayChart from '../../components/CampaignIntradayChart';
import CampaignMetricsChart, { DEFAULT_CHART_METRICS, type MetricKey } from '../../components/CampaignMetricsChart';
import ScheduleModal from '../../components/ScheduleModal';
import BudgetLedgerModal from '../../components/BudgetLedgerModal';
import DepositModal from '../../components/DepositModal';
import AutopayModal from '../../components/AutopayModal';
import WbThumb from '@/components/WbThumb';
import PeriodPicker from '@/components/PeriodPicker';
import Tooltip from '@/components/Tooltip';
import EditableName from '../../components/EditableName';
import { useToast } from '../../components/Toasts';
import ZonesPanel, { ZONES, zoneRuleText } from '../../components/ZonesPanel';
import { fmt, iso, STATUS_BADGE, adTypeLabel, wbCampaignUrl, scheduleLabel, humanizeAdsError } from '../../components/adsShared';

type CatalogEntry = { vendor_code: string; subject: string; brand: string };

type CampTab = 'metrics' | 'clusters' | 'hourly' | 'intraday';

export default function CampaignPage() {
    const routeParams = useParams();
    const toast = useToast();
    const slug = typeof routeParams?.slug === 'string' ? routeParams.slug : Array.isArray(routeParams?.slug) ? routeParams.slug[0] : '';
    const campaignId = Number(Array.isArray(routeParams?.campaignId) ? routeParams.campaignId[0] : routeParams?.campaignId);

    const [dateFrom, setDateFrom] = useState(iso(new Date(Date.now() - 29 * 86400_000)));
    const [dateTo, setDateTo] = useState(iso(new Date()));
    const [reloadKey, setReloadKey] = useState(0);

    // Кампания и её расписание паузы — из общего списка (отдельного эндпоинта нет)
    const [campaign, setCampaign] = useState<AdsManagerCampaign | null>(null);
    const [campLoading, setCampLoading] = useState(true);
    const [campError, setCampError] = useState('');
    const [schedule, setSchedule] = useState<Record<string, AdsScheduleSetting>>({});
    const [headerCollapsed, setHeaderCollapsed] = useState(false);
    const [scheduleModal, setScheduleModal] = useState(false);
    const [budgetLogModal, setBudgetLogModal] = useState(false);
    const [depositModal, setDepositModal] = useState(false);
    const [autopayModal, setAutopayModal] = useState(false);
    // Родное правило ВБ: undefined — ещё не спрашивали, null — кабинет недоступен
    const [autorefill, setAutorefill] = useState<WbAutorefillSetting | null | undefined>(undefined);
    const [stateBusy, setStateBusy] = useState(false);
    const [refreshing, setRefreshing] = useState(false);  // живой догруз кампании из WB по «Обновить»

    // silent=true — тихая фоновая сверка после оптимистичного апдейта: не гасит шапку
    // спиннером и не роняет ошибку в UI (значение уже показано из ответа действия).
    const loadCampaign = useCallback(async (silent = false) => {
        if (!silent) setCampLoading(true);
        if (!silent) setCampError('');
        try {
            const [list, sched] = await Promise.all([
                api.getAdCampaignsList(),
                api.getCampaignsSchedule().catch(() => ({})),
            ]);
            const found = list.find(c => c.campaign_id === campaignId) ?? null;
            setCampaign(found);
            setSchedule(sched);
            if (!found && !silent) setCampError('Кампания не найдена. Возможно, она ещё не синхронизирована.');
        } catch (e) {
            if (!silent) setCampError(e instanceof Error ? e.message : 'Ошибка загрузки кампании');
        } finally { if (!silent) setCampLoading(false); }
    }, [campaignId]);

    useEffect(() => { loadCampaign(); }, [loadCampaign]);

    // Правило автопополнения ВБ — отдельным запросом: ходит в кабинет и может быть медленным
    // или недоступным, страница кампании от этого зависеть не должна.
    useEffect(() => {
        if (!Number.isFinite(campaignId)) return;
        const controller = new AbortController();
        api.getCampaignAutorefill(campaignId)
            .then(r => { if (!controller.signal.aborted) setAutorefill(r.settings); })
            .catch(() => { if (!controller.signal.aborted) setAutorefill(null); });
        return () => controller.abort();
    }, [campaignId]);

    // Ручное пополнение прошло: новый остаток от WB — сразу в карточку (оптимистично),
    // следом тихая сверка с зеркалом. WB иногда не возвращает total — тогда считаем сами.
    const onDeposited = useCallback((budgetAfter: number | null, amount: number) => {
        setCampaign(prev => (prev ? { ...prev, budget: budgetAfter ?? Number(prev.budget) + amount } : prev));
        toast.success(`Бюджет пополнен на ${fmt(amount)} ₽`);
        loadCampaign(true);
    }, [loadCampaign, toast]);

    // Каталог артикулов (nm_id → артикул продавца/предмет/бренд) для блока «Товары кампании»
    const [catalog, setCatalog] = useState<Record<number, CatalogEntry>>({});
    useEffect(() => {
        api.getAdArticleCatalog().then(rows => {
            const m: Record<number, CatalogEntry> = {};
            rows.forEach(r => { m[r.nm_id] = { vendor_code: r.vendor_code || String(r.nm_id), subject: r.subject || '', brand: r.brand || '' }; });
            setCatalog(m);
        }).catch(() => { /* каталог не критичен */ });
    }, []);

    const isCpm = (campaign?.campaign_type || '').toLowerCase() === 'cpm';
    const isCpc = (campaign?.campaign_type || '').toLowerCase() === 'cpc';
    const [tab, setTab] = useState<CampTab>('metrics');
    // При заходе в кампанию метрики показываем графиком; «По дням» — таблица с селектором
    // зоны показов (только CPM): «Всего» = воронка продаж, «Поиск»/«Рекомендации» = РК-метрики по зоне
    const [metricsView, setMetricsView] = useState<'chart' | 'table'>('chart');
    // Активная вкладка/подвкладка переживают перезагрузку страницы — не сбрасываются на «Метрики».
    // Восстанавливаем ПОСЛЕ первого рендера (localStorage недоступен на SSR); недоступную вкладку
    // «Кластеризатор» отобьёт эффект clustersAvailable ниже.
    const tabRestored = useRef(false);
    useEffect(() => {
        try {
            const t = localStorage.getItem('ads_camp_tab');
            if (t === 'metrics' || t === 'clusters' || t === 'hourly' || t === 'intraday') setTab(t);
            const mv = localStorage.getItem('ads_camp_metrics_view');
            if (mv === 'chart' || mv === 'table') setMetricsView(mv);
        } catch { /* SSR / приватный режим */ }
        tabRestored.current = true;
    }, []);
    useEffect(() => { if (tabRestored.current) try { localStorage.setItem('ads_camp_tab', tab); } catch { /* noop */ } }, [tab]);
    useEffect(() => { if (tabRestored.current) try { localStorage.setItem('ads_camp_metrics_view', metricsView); } catch { /* noop */ } }, [metricsView]);
    // Выбор метрик графика живёт здесь: при смене товара/периода метрики перезагружаются
    // и график размонтируется — своё состояние он бы потерял.
    const [chartMetrics, setChartMetrics] = useState<Set<MetricKey>>(() => new Set(DEFAULT_CHART_METRICS));
    const toggleChartMetric = useCallback((k: MetricKey) => setChartMetrics(prev => {
        const n = new Set(prev);
        if (n.has(k)) n.delete(k); else n.add(k);
        return n;
    }), []);
    // Выбранный товар: показывать воронку только по нему (клик по карточке товара)
    const [selectedNm, setSelectedNm] = useState<number | null>(null);
    // Кластеризатор (поисковые фразы) есть у CPM (с редактированием ставок/минус-фраз) и
    // у CPC — там только просмотр статистики по фразам (ставка единая, минус-фраз нет),
    // поэтому таблица идёт read-only. Разбивка статистики по зонам живёт селектором внутри
    // «По дням»; блок «ЗОНЫ ПОКАЗОВ» — лишь вкл/выкл зон.
    const clustersAvailable = isCpm || isCpc;
    // Вкладка кластеров исчезла под ногами (не CPM/CPC) — уводим на метрики,
    // иначе на экране не осталось бы ни одной вкладки с содержимым.
    // Отбиваем с кластеров только когда кампания УЖЕ загружена и она не поддерживает кластеры —
    // иначе на маунте (campaign ещё null → clustersAvailable=false) сбросили бы восстановленную вкладку.
    useEffect(() => { if (campaign && !clustersAvailable) setTab(t => (t === 'clusters' ? 'metrics' : t)); }, [campaign, clustersAvailable]);
    // Ручное пополнение бюджета

    // ─── Метрики по дням ───
    const [metrics, setMetrics] = useState<CampaignMetricsResponse | null>(null);
    const [metricsLoading, setMetricsLoading] = useState(true);
    const [metricsError, setMetricsError] = useState('');

    useEffect(() => {
        if (!Number.isFinite(campaignId)) return;
        const controller = new AbortController();
        setMetricsLoading(true); setMetricsError(''); setMetrics(null);
        api.getCampaignMetrics(campaignId, dateFrom, dateTo, selectedNm)
            .then(res => { if (controller.signal.aborted) return; if (res.error) setMetricsError(res.error); else setMetrics(res); })
            .catch(e => { if (!controller.signal.aborted) setMetricsError(e instanceof Error ? e.message : 'Ошибка загрузки метрик'); })
            .finally(() => { if (!controller.signal.aborted) setMetricsLoading(false); });
        return () => controller.abort();
    }, [campaignId, dateFrom, dateTo, reloadKey, selectedNm]);

    // ─── Метрики по зонам показов (только CPM): РК-метрики по выбранной зоне (селектор внутри «По дням»/«График») ───
    const [zoneMetricsZone, setZoneMetricsZone] = useState<'total' | 'search' | 'recommendations'>('total');
    const [zoneMetrics, setZoneMetrics] = useState<CampaignZoneMetricsResponse | null>(null);
    const [zoneMetricsLoading, setZoneMetricsLoading] = useState(false);
    const [zoneMetricsError, setZoneMetricsError] = useState('');
    // Не CPM — зон нет; селектор скрыт, поэтому держим зону на «Всего»
    useEffect(() => { if (!isCpm) setZoneMetricsZone('total'); }, [isCpm]);
    useEffect(() => {
        // Зонные данные нужны и таблице «По дням», и графику при выбранной зоне (кроме «Всего»)
        const needZone = tab === 'metrics' && zoneMetricsZone !== 'total';
        if (!Number.isFinite(campaignId) || !needZone) return;
        const controller = new AbortController();
        setZoneMetricsLoading(true); setZoneMetricsError(''); setZoneMetrics(null);
        api.getCampaignZoneMetrics(campaignId, dateFrom, dateTo, zoneMetricsZone)
            .then(res => { if (controller.signal.aborted) return; if (res.error) setZoneMetricsError(res.error); else setZoneMetrics(res); })
            .catch(e => { if (!controller.signal.aborted) setZoneMetricsError(e instanceof Error ? e.message : 'Ошибка загрузки метрик по зонам'); })
            .finally(() => { if (!controller.signal.aborted) setZoneMetricsLoading(false); });
        return () => controller.abort();
    }, [campaignId, dateFrom, dateTo, reloadKey, zoneMetricsZone, tab]);

    // ─── Расход по часам (вкладка «По часам»): восстановлен из снимков остатка бюджета ───
    const [hourly, setHourly] = useState<CampaignHourlySpend | null>(null);
    const [hourlyLoading, setHourlyLoading] = useState(false);
    const [hourlyError, setHourlyError] = useState('');
    useEffect(() => {
        if (!Number.isFinite(campaignId) || tab !== 'hourly') return;
        const controller = new AbortController();
        setHourlyLoading(true); setHourlyError(''); setHourly(null);
        api.getCampaignHourly(campaignId, dateTo)
            .then(res => { if (controller.signal.aborted) return; if (res.error) setHourlyError(res.error); else setHourly(res); })
            .catch(e => { if (!controller.signal.aborted) setHourlyError(e instanceof Error ? e.message : 'Ошибка загрузки почасового расхода'); })
            .finally(() => { if (!controller.signal.aborted) setHourlyLoading(false); });
        return () => controller.abort();
    }, [campaignId, dateTo, reloadKey, tab]);

    // ─── Внутридневные показы/клики/CTR (вкладка «Внутри дня»): снимки campaigns-stats ~30 мин ───
    const [intraday, setIntraday] = useState<CampaignIntradayMetrics | null>(null);
    const [intradayLoading, setIntradayLoading] = useState(false);
    const [intradayError, setIntradayError] = useState('');
    useEffect(() => {
        if (!Number.isFinite(campaignId) || tab !== 'intraday') return;
        const controller = new AbortController();
        setIntradayLoading(true); setIntradayError(''); setIntraday(null);
        api.getCampaignIntraday(campaignId, dateTo)
            .then(res => { if (controller.signal.aborted) return; if (res.error) setIntradayError(res.error); else setIntraday(res); })
            .catch(e => { if (!controller.signal.aborted) setIntradayError(e instanceof Error ? e.message : 'Ошибка загрузки внутридневных метрик'); })
            .finally(() => { if (!controller.signal.aborted) setIntradayLoading(false); });
        return () => controller.abort();
    }, [campaignId, dateTo, reloadKey, tab]);

    // Зонные RK-строки в форме строки графика: воронки продаж по зонам нет → её поля пустые
    // (в графике они покажутся как «нет данных»). Корзины/Заказы здесь — рекламная атрибуция.
    const zoneChartRows = useMemo<CampaignMetricRow[]>(() => (zoneMetrics?.rows ?? []).map(r => ({
        date: r.date, views: r.views, clicks: r.clicks, ctr: r.ctr, cpc: r.cpc, spend: r.spend,
        add_to_cart: r.atbs, orders: r.orders, cpo: r.cpo,
        open_card: 0, cr1: 0, cr2: 0, orders_sum: 0, cpl: null, avg_price: 0, customer_price: null, spp: null, drr: 0,
    })), [zoneMetrics]);

    // Селектор зоны показов — общий для «Графика» и таблицы «По дням»
    const zoneSelectorBar = (
        <div style={{ padding: '10px 12px', borderBottom: '1px solid #eef0f2', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: '#6b7280' }}>Зона показов:</span>
            <span style={{ display: 'inline-flex', borderRadius: 8, overflow: 'hidden', border: '1px solid #e5e7eb' }}>
                {(([['total', 'Всего'], ['search', 'Поиск'], ['recommendations', 'Рекомендации']]) as [typeof zoneMetricsZone, string][]).map(([v, label]) => (
                    <button key={v} onClick={() => setZoneMetricsZone(v)}
                        style={{ padding: '5px 12px', fontSize: 12, fontWeight: 500, border: 'none', cursor: 'pointer', background: zoneMetricsZone === v ? '#3b82f6' : '#fff', color: zoneMetricsZone === v ? '#fff' : '#374151' }}>
                        {label}
                    </button>
                ))}
            </span>
            <span style={{ fontSize: 11, color: '#9ca3af' }}>рекламные показы/клики/затраты/корзины/заказы по зоне; воронка продаж по зонам не делится</span>
        </div>
    );

    // Зоны показов и правила ставки (работает и для CPC, где кластеров нет)
    const [zones, setZones] = useState<CampaignZones | null>(null);
    const [zonesLoading, setZonesLoading] = useState(false);
    const [zonesError, setZonesError] = useState(false);
    useEffect(() => {
        if (!Number.isFinite(campaignId)) return;
        setZonesLoading(true); setZonesError(false);
        api.getCampaignZones(campaignId, dateFrom, dateTo, selectedNm)
            .then(z => { if (z.error) { setZones(null); setZonesError(true); } else setZones(z); })
            .catch(() => { setZones(null); setZonesError(true); })
            .finally(() => setZonesLoading(false));
    }, [campaignId, dateFrom, dateTo, selectedNm, reloadKey]);


    // ─── Кластеры / минус-фразы / ставки (только CPM) ───
    const [data, setData] = useState<CampaignClustersResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [pending, setPending] = useState<Set<string>>(new Set());
    const [minusError, setMinusError] = useState<string | null>(null);
    const [bidPending, setBidPending] = useState<Set<string>>(new Set());
    const [bidError, setBidError] = useState<string | null>(null);

    useEffect(() => {
        if (!Number.isFinite(campaignId)) return;
        const controller = new AbortController();
        setLoading(true); setError(''); setData(null);
        api.getCampaignClusters(campaignId, dateFrom, dateTo, selectedNm)
            .then(res => {
                if (controller.signal.aborted) return;
                if (res.error) {
                    setError(res.error === 'no_api_key' ? 'Не задан API-ключ WB — подключите ключ в настройках проекта, чтобы анализировать кластеры.'
                        : res.error === 'campaign_not_found' ? 'Кампания не найдена в кабинете WB.'
                        : res.error);
                } else {
                    setData(res);
                }
            })
            .catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка загрузки кластеров'); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [campaignId, dateFrom, dateTo, reloadKey, selectedNm]);

    // ─── Органические позиции по фразам (кластеризатор): «Позиция»/«Была» ───
    // Позиция товара по фразе из публичного поиска WB; собирается по кнопке, копится история.
    const posNm = selectedNm ?? campaign?.nm_ids?.[0] ?? null;
    const [positions, setPositions] = useState<Record<string, PositionSnapshot> | undefined>(undefined);
    const [posCollecting, setPosCollecting] = useState<{ done: number; total: number; throttled?: number } | null>(null);
    const [collectingOne, setCollectingOne] = useState<Set<string>>(new Set());
    useEffect(() => {
        if (tab !== 'clusters' || posNm == null) return;
        let aborted = false;
        api.getPositions(posNm).then(res => { if (!aborted) setPositions(res.positions); }).catch(() => { /* позиции опциональны */ });
        return () => { aborted = true; };
    }, [posNm, tab, reloadKey]);
    // Play: массовый сбор ПРОГОНОМ по таблице — воркеры идут по фразам сверху вниз, помечают
    // текущую строку крутящимся кружком (collectingOne) и вписывают позицию. Видно, как бежит.
    const collectAbortRef = useRef(false);
    const handleCollectPositions = useCallback(async () => {
        if (posNm == null || !data || data.clusters.length === 0) return;
        const phrases = data.clusters.map(c => c.norm_query);
        collectAbortRef.current = false;
        let done = 0, throttled = 0, idx = 0;
        setPosCollecting({ done: 0, total: phrases.length, throttled: 0 });
        const worker = async () => {
            while (idx < phrases.length && !collectAbortRef.current) {
                const phrase = phrases[idx++];
                setCollectingOne(prev => new Set(prev).add(phrase));  // строка «крутится»
                try {
                    const res = await api.collectPositionOne(posNm, phrase);
                    setPositions(prev => ({ ...(prev ?? {}), [phrase]: { position: res.position, prev: res.prev, depth: res.depth, at: res.at } }));
                    if (res.throttled) throttled++;
                } catch { /* одну фразу пропускаем, прогон продолжается */ }
                finally {
                    setCollectingOne(prev => { const n = new Set(prev); n.delete(phrase); return n; });
                    done++;
                    setPosCollecting({ done, total: phrases.length, throttled });
                }
            }
        };
        // 3 воркера параллельно (как лимит WB) — до 3 крутящихся строк одновременно
        await Promise.all([worker(), worker(), worker()]);
        setPosCollecting(null);
        if (throttled > 0) toast.warning(`Слишком частый запрос — WB ограничил, ${throttled} фраз(ы) не собрано. Повторите позже.`);
    }, [posNm, data, toast]);
    // Stop: прерываем прогон (собранное уже сохранено — collect-one коммитит каждую фразу)
    const handleStopPositions = useCallback(() => { collectAbortRef.current = true; }, []);
    // Кругляшок: собрать позицию одной фразы, сразу вписать в ячейку
    const handleCollectOne = useCallback(async (phrase: string) => {
        if (posNm == null) return;
        setCollectingOne(prev => new Set(prev).add(phrase));
        try {
            const res = await api.collectPositionOne(posNm, phrase);
            setPositions(prev => ({ ...(prev ?? {}), [phrase]: { position: res.position, prev: res.prev, depth: res.depth, at: res.at } }));
            if (res.throttled) toast.warning('Слишком частый запрос — WB временно ограничил. Подождите немного и попробуйте снова.');
        } catch (e) {
            toast.error(e instanceof Error ? e.message : 'Не удалось собрать позицию');
        } finally {
            setCollectingOne(prev => { const n = new Set(prev); n.delete(phrase); return n; });
        }
    }, [posNm, toast]);

    // Единая ставка CPM: WB игнорирует пофразовые ставки (одна ставка правит всеми фразами) —
    // молча гасим управление ставкой. Минус-фразы WB принимает и у единой ставки.
    const clusterUnified = ((data?.bid_mode ?? campaign?.bid_mode) || '') === 'unified';

    const handleToggleMinus = async (c: SearchCluster) => {
        const action: 'add' | 'remove' = c.is_minused ? 'remove' : 'add';
        const nmId = data?.nm_ids?.[0] ?? campaign?.nm_ids?.[0];
        if (nmId == null) { setMinusError('Не удалось определить товар (nm_id) кампании'); return; }
        const confirmText = action === 'add'
            ? `Добавить «${c.norm_query}» в минус-фразы кампании в кабинете WB?`
            : `Вернуть «${c.norm_query}» — убрать из минус-фраз кампании в кабинете WB?`;
        if (!window.confirm(confirmText)) return;
        setMinusError(null);
        setPending(prev => new Set(prev).add(c.norm_query));
        try {
            const res = await api.toggleClusterMinus(campaignId, { nm_id: nmId, norm_query: c.norm_query, action });
            if (!res.ok) { setMinusError(res.error || 'WB отклонил операцию'); return; }
            setData(prev => prev ? { ...prev, clusters: prev.clusters.map(x => x.norm_query === c.norm_query ? { ...x, is_minused: action === 'add' } : x) } : prev);
        } catch (e) {
            setMinusError(e instanceof Error ? e.message : 'Ошибка обращения к WB');
        } finally {
            setPending(prev => { const nx = new Set(prev); nx.delete(c.norm_query); return nx; });
        }
    };

    // Массовое добавление/возврат минус-фраз: одно подтверждение, затем последовательно по WB.
    const handleBulkMinus = async (list: SearchCluster[], action: 'add' | 'remove') => {
        const items = list.filter(c => !c.locked);
        if (items.length === 0) return;
        const nmId = data?.nm_ids?.[0] ?? campaign?.nm_ids?.[0];
        if (nmId == null) { setMinusError('Не удалось определить товар (nm_id) кампании'); return; }
        // Подтверждение уже получено в нижней панели кластеризатора
        setMinusError(null);
        setPending(prev => { const n = new Set(prev); items.forEach(c => n.add(c.norm_query)); return n; });
        const failed: string[] = [];
        let lastError: string | null = null;
        for (let idx = 0; idx < items.length; idx++) {
            const c = items[idx];
            try {
                const res = await api.toggleClusterMinus(campaignId, { nm_id: nmId, norm_query: c.norm_query, action });
                if (res.ok) {
                    setData(prev => prev ? { ...prev, clusters: prev.clusters.map(x => x.norm_query === c.norm_query ? { ...x, is_minused: action === 'add' } : x) } : prev);
                } else {
                    failed.push(c.norm_query);
                    lastError = res.error || 'WB отклонил операцию';
                }
            } catch (e) {
                failed.push(c.norm_query);
                lastError = e instanceof Error ? e.message : 'Ошибка обращения к WB';
            } finally {
                setPending(prev => { const n = new Set(prev); n.delete(c.norm_query); return n; });
            }
            // WB ограничивает частоту — пауза между фразами, чтобы пачка не ловила 429
            if (idx < items.length - 1) await new Promise(r => setTimeout(r, 600));
        }
        const verbCap = action === 'add' ? 'Отключено' : 'Включено';
        reportBulkOutcome(`${verbCap} фраз: ${items.length}`, verbCap, items.length, failed, lastError, setMinusError);
    };

    // Единый итог массовой операции кластеризатора: success-тост либо список отбитых фраз
    // (раньше каждая ошибка перетирала предыдущую — был виден только последний отказ).
    const reportBulkOutcome = (okMsg: string, failVerb: string, total: number, failed: string[], lastError: string | null, setErr: (m: string) => void) => {
        if (failed.length === 0) {
            toast.success(okMsg);
            return;
        }
        const list = failed.slice(0, 3).join('», «');
        const more = failed.length > 3 ? ` и ещё ${failed.length - 3}` : '';
        const msg = `${failVerb} ${total - failed.length} из ${total}; отбито: «${list}»${more} — ${lastError}`;
        setErr(msg);
        toast.error(msg);
    };

    // Тихая сверка кластеров с WB (после массовой правки ставок): подтягивает реальные ставки
    // из get-bids, не гася основной спиннер и не роняя ошибку в UI.
    const refetchClusters = useCallback(async () => {
        try {
            const res = await api.getCampaignClusters(campaignId, dateFrom, dateTo, selectedNm);
            if (!res.error) setData(res);
        } catch { /* фоновая сверка — молча */ }
    }, [campaignId, dateFrom, dateTo, selectedNm]);

    // meta — «паспорт» применения (колонка «Стоит»): источник + точка отсчёта ДРР/CPM/цель.
    const handleSetBid = async (c: SearchCluster, bid: number, meta?: { source?: string; targetDrr?: number | null }) => {
        if (clusterUnified) return;  // единая ставка — пофразовой ставки нет; UI уже погашен, молча выходим
        const nmId = data?.nm_ids?.[0] ?? campaign?.nm_ids?.[0];
        if (nmId == null) { setBidError('Не удалось определить товар (nm_id) кампании'); return; }
        // Подтверждение — встроенная кнопка ✓ в ячейке ставки (не блокирующий window.confirm)
        setBidError(null);
        setBidPending(prev => new Set(prev).add(c.norm_query));
        const basisDrr = c.drr == null ? null : Number(c.drr);
        const basisCpm = c.cpm == null ? null : Number(c.cpm);
        try {
            // verify:false — пишем оптимистично (как ставка зоны/списка). Немедленное перечитывание
            // ненадёжно из-за read-after-write лага WB и давало ложное «ставка не подтвердилась».
            // Заведомо неподдерживаемые случаи (единая ставка/CPC) отбивает гейт → res.unsupported.
            const res = await api.setCampaignClusterBid(campaignId, {
                nm_id: nmId, norm_query: c.norm_query, bid, verify: false,
                source: meta?.source, basis_drr: basisDrr, basis_cpm: basisCpm, target_drr: meta?.targetDrr ?? null,
            });
            if (!res.ok) {
                const msg = res.error || 'WB отклонил ставку';
                setBidError(msg);
                if (res.unsupported) toast.error(msg);  // гейт по типу кампании — показываем заметно
                return;
            }
            const applied = res.bid ?? bid;
            // Оптимистичный паспорт: ставка > 0 → «стоит с сегодня» (0 дн); сброс → паспорт снимаем.
            // Бэкенд не сбрасывает таймер при ТОЙ ЖЕ ставке — редкий случай выправит следующий refetch.
            const passport = applied > 0
                ? { bid_set_at: new Date().toISOString(), bid_days: 0, bid_source: meta?.source ?? 'manual', bid_basis_drr: basisDrr, bid_basis_cpm: basisCpm }
                : { bid_set_at: null, bid_days: null, bid_source: null, bid_basis_drr: null, bid_basis_cpm: null };
            setData(prev => prev ? { ...prev, clusters: prev.clusters.map(x => x.norm_query === c.norm_query ? { ...x, bid: applied, ...passport } : x) } : prev);
            if (res.adjusted) toast.warning(`Ниже минимума WB — поставлен минимум ${formatNumber(applied, 0)} ₽`);
        } catch (e) {
            setBidError(e instanceof Error ? e.message : 'Ошибка обращения к WB');
        } finally {
            setBidPending(prev => { const nx = new Set(prev); nx.delete(c.norm_query); return nx; });
        }
    };

    // Массовая ставка ОДНИМ запросом (WB normquery/bids батчевый — как Mkeeper): мгновенно, без
    // rate-limit 429, в отличие от прежнего N-поштучного цикла. bid=0 — сброс к ставке кампании.
    const handleBulkBid = async (items: { cluster: SearchCluster; bid: number }[], label: string, meta?: { source?: string; targetDrr?: number | null }) => {
        if (clusterUnified) return;  // единая ставка — пофразовой ставки нет; UI уже погашен, молча выходим
        if (items.length === 0) return;
        const nmId = data?.nm_ids?.[0] ?? campaign?.nm_ids?.[0];
        if (nmId == null) { setBidError('Не удалось определить товар (nm_id) кампании'); return; }
        // Подтверждение уже получено в нижней панели кластеризатора
        setBidError(null);
        const qs = items.map(i => i.cluster.norm_query);
        setBidPending(prev => { const n = new Set(prev); qs.forEach(q => n.add(q)); return n; });
        try {
            // basis_* — точка отсчёта на момент применения (ДРР/CPM фразы + цель); паспорт подтянется refetch'ем
            const res = await api.setCampaignClusterBidsBulk(campaignId, items.map(i => ({
                nm_id: nmId, norm_query: i.cluster.norm_query, bid: i.bid,
                source: meta?.source, basis_drr: i.cluster.drr == null ? null : Number(i.cluster.drr),
                basis_cpm: i.cluster.cpm == null ? null : Number(i.cluster.cpm), target_drr: meta?.targetDrr ?? null,
            })));
            // Общий отказ (нет кампании/ключа или гейт по типу) — результатов нет
            if (!res.results) {
                const msg = humanizeAdsError(res.error, 'WB отклонил массовую ставку');
                setBidError(msg);
                toast.error(msg);
                return;
            }
            const okSet = new Set(res.results.filter(r => r.ok).map(r => r.norm_query));
            const appliedBid = new Map(res.results.filter(r => r.ok).map(r => [r.norm_query, r.bid] as const));
            const sentBid = new Map(items.map(i => [i.cluster.norm_query, i.bid] as const));
            // Оптимистично применяем успешные (bid из ответа = факт, может быть минимумом WB)
            setData(prev => prev ? { ...prev, clusters: prev.clusters.map(x => {
                if (!okSet.has(x.norm_query)) return x;
                const sent = sentBid.get(x.norm_query) ?? 0;
                return { ...x, bid: sent > 0 ? (appliedBid.get(x.norm_query) ?? sent) : null };
            }) } : prev);
            const failed = res.results.filter(r => !r.ok).map(r => r.norm_query);
            const lastError = res.results.filter(r => !r.ok).map(r => r.error).filter(Boolean).pop() || null;
            reportBulkOutcome(`Готово: ${label} — фраз: ${items.length}`, 'Применено', items.length, failed, lastError, setBidError);
            // Сверка с WB: перечитываем кластеры — реальные ставки заменят оптимистичные
            if (failed.length < items.length) await refetchClusters();
        } catch (e) {
            setBidError(e instanceof Error ? e.message : 'Ошибка обращения к WB');
        } finally {
            setBidPending(prev => { const n = new Set(prev); qs.forEach(q => n.delete(q)); return n; });
        }
    };

    const toggleState = async () => {
        if (!campaign) return;
        const active = campaign.status !== 9;  // не активна → запускаем, активна → пауза
        setStateBusy(true);
        try {
            const r = await api.setCampaignState(campaign.campaign_id, active);
            if (r && (r as { ok?: boolean }).ok === false) { toast.error(humanizeAdsError((r as { error?: string }).error, 'Не удалось изменить статус')); return; }
            toast.success(active ? 'Кампания запущена' : 'Кампания приостановлена');
            // Мгновенный флип из эхо-статуса ответа (9 активна / 11 пауза), без ожидания рефетча
            const newStatus = r.status ?? (active ? 9 : 11);
            setCampaign(prev => prev ? { ...prev, status: newStatus, status_label: active ? 'Активна' : 'Пауза' } : prev);
            loadCampaign(true);  // тихая фоновая сверка — не блокирует тумблер
        }
        catch (e) { toast.error(humanizeAdsError(e, 'Не удалось изменить статус кампании')); }
        finally { setStateBusy(false); }
    };

    const saveSchedule = async (s: AdsScheduleSetting) => {
        // Сейв только пишет настройку: паузу/запуск делает scheduler-тик (раз в 15 минут)
        const res = await api.setCampaignSchedule(campaignId, s);
        setSchedule(res.settings);  // значение расписания (кнопка) — мгновенно из эхо-мапы
    };

    // ─── Управление кампанией (завершить / переименовать / удалить) ───
    const [manageBusy, setManageBusy] = useState(false);
    const renameWith = async (name: string) => {
        if (!campaign || name === campaign.name) return;
        setManageBusy(true); setCampError('');
        try {
            const res = await api.renameCampaign(campaign.campaign_id, name);
            if (!res.ok) { toast.error(humanizeAdsError(res.error, 'Не удалось переименовать кампанию')); return; }
            toast.success('Кампания переименована');
            setCampaign(prev => prev ? { ...prev, name: res.name ?? name } : prev);
            loadCampaign(true);
        } catch (e) { toast.error(humanizeAdsError(e, 'Не удалось переименовать кампанию')); }
        finally { setManageBusy(false); }
    };

    const doStop = async () => {
        if (!campaign || !window.confirm('Завершить кампанию? Это НЕОБРАТИМО — запустить её снова будет нельзя.')) return;
        setManageBusy(true); setCampError('');
        try {
            const res = await api.stopCampaign(campaign.campaign_id);
            if (!res.ok) { toast.error(humanizeAdsError(res.error, 'Не удалось завершить кампанию')); return; }
            toast.success('Кампания завершена');
            setCampaign(prev => prev ? { ...prev, status: res.status ?? 7, status_label: 'Завершена' } : prev);
            loadCampaign(true);
        } catch (e) { toast.error(humanizeAdsError(e, 'Не удалось завершить кампанию')); }
        finally { setManageBusy(false); }
    };

    const doDelete = async () => {
        if (!campaign || !window.confirm('Удалить кампанию в WB? Действие необратимо.')) return;
        setManageBusy(true); setCampError('');
        try {
            const res = await api.deleteCampaign(campaign.campaign_id);
            if (!res.ok) toast.error(humanizeAdsError(res.error, 'Не удалось удалить кампанию'));
            else toast.success('Кампания удалена');
            await loadCampaign();
        } catch (e) { toast.error(humanizeAdsError(e, 'Не удалось удалить кампанию')); }
        finally { setManageBusy(false); }
    };

    // «Обновить» = живой догруз этой кампании из WB (деталь+бюджет+свежая стата) → зеркало,
    // затем перечитать шапку и метрики/график/зоны из уже свежего зеркала.
    // auto=true — тихий догруз при заходе на страницу: не роняет warning-тост, если WB
    // недоступен (в шапке уже показаны последние данные из зеркала из loadCampaign()).
    const doRefresh = useCallback(async (auto = false) => {
        setRefreshing(true);
        try {
            const res = await api.refreshCampaign(campaignId);
            if (!res.ok && !auto) toast.warning('Не удалось догрузить из WB — показаны последние данные из базы');
        } catch { if (!auto) toast.warning('Не удалось догрузить из WB — показаны последние данные из базы'); }
        await loadCampaign(true);      // шапка (остаток/статус) из свежего зеркала, без мигания
        setReloadKey(k => k + 1);      // метрики/график/зоны — тоже перечитать
        setRefreshing(false);
    }, [campaignId, loadCampaign, toast]);

    // При заходе в кампанию сразу подтягиваем актуальные данные из WB (остаток/статус/расход/
    // метрики), а не только зеркало списка. Один раз на каждую кампанию: сначала быстрый показ
    // из зеркала (loadCampaign выше), затем тихий живой догруз поверх.
    const autoRefreshedFor = useRef<number | null>(null);
    useEffect(() => {
        if (!Number.isFinite(campaignId) || autoRefreshedFor.current === campaignId) return;
        autoRefreshedFor.current = campaignId;
        doRefresh(true);
    }, [campaignId, doRefresh]);

    // Копирование артикула (nm_id) из карточки товара кампании
    const copyNm = (nmId: number) => {
        navigator.clipboard?.writeText(String(nmId))
            .then(() => toast.success(`Артикул ${nmId} скопирован`))
            .catch(() => toast.error('Не удалось скопировать — буфер обмена недоступен'));
    };

    const sc = schedule[String(campaignId)];
    const typeInfo = useMemo(() => (campaign ? adTypeLabel(campaign) : null), [campaign]);

    return (
        <PageGuard page="ads-manager">
            {/* Во всю высоту .main-content (её паддинг 40+40) — только внутренняя панель скроллится, страница не прокручивается */}
            <div className="animate-in" style={{ height: 'calc(100vh - 80px)', display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
                <Link href={`/p/${slug}/ads-manager`} className="btn btn-secondary btn-sm" style={{ display: 'inline-flex', alignSelf: 'flex-start', alignItems: 'center', gap: 6, fontSize: 13, marginBottom: 12 }}>
                    ← Все кампании
                </Link>

                {campLoading ? (
                    <div className="glass-card static" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка кампании…</div>
                ) : campError && !campaign ? (
                    <div className="glass-card" style={{ padding: '16px 20px', border: '1px solid var(--color-danger)', background: '#fef2f2' }}>
                        <span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {campError}</span>
                    </div>
                ) : campaign && (
                    <>
                        {/* ─── Свёрнутый хедер: только важное, экран отдан графику и таблице ─── */}
                        {headerCollapsed ? (
                            <div className="glass-card static" style={{ padding: '8px 12px', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                                {campaign.nm_ids[0] != null && <WbThumb nmId={campaign.nm_ids[0]} size={32} />}
                                <span style={{ fontSize: 14, fontWeight: 600, color: '#111827', maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {campaign.name || `#${campaign.campaign_id}`}
                                </span>

                                {/* Переключатель «реклама вкл/выкл» */}
                                {(campaign.status === 9 || campaign.status === 11) && (
                                    <Tooltip text={campaign.status === 9 ? 'Приостановить рекламу' : 'Запустить рекламу'}>
                                        <button onClick={toggleState} disabled={stateBusy}
                                            aria-label={campaign.status === 9 ? 'Приостановить рекламу' : 'Запустить рекламу'}
                                            aria-pressed={campaign.status === 9}
                                            style={{ width: 38, height: 20, borderRadius: 10, border: 'none', cursor: 'pointer', padding: 2, background: campaign.status === 9 ? '#10b981' : '#d1d5db', display: 'inline-flex', justifyContent: campaign.status === 9 ? 'flex-end' : 'flex-start', opacity: stateBusy ? 0.6 : 1 }}>
                                            {/* Кружок не должен ловить мышь: переход button→span рвал бы hover */}
                                            <span style={{ width: 16, height: 16, borderRadius: '50%', background: '#fff', pointerEvents: 'none' }} />
                                        </button>
                                    </Tooltip>
                                )}

                                <span style={{ fontSize: 12, color: '#6b7280', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                                    Остаток: <b style={{ fontSize: 14, color: campaign.budget <= 0 && campaign.status === 9 ? '#ef4444' : '#111827' }}>{fmt(campaign.budget)} ₽</b>
                                    <Tooltip text="Пополнить бюджет">
                                        <button onClick={() => setDepositModal(true)} aria-label="Пополнить бюджет"
                                            style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 22, height: 22, borderRadius: 8, border: '1px solid #a7f3d0', background: '#ecfdf5', color: '#059669', cursor: 'pointer', padding: 0 }}>
                                            <IcPlus size={13} />
                                        </button>
                                    </Tooltip>
                                </span>

                                {/* Активные зоны показов видны и в свёрнутом виде */}
                                {zones && (
                                    <span style={{ display: 'inline-flex', gap: 6 }}>
                                        {ZONES.filter(z => zones.placements[z.key]).map(z => (
                                            <span key={z.key} title={`${zoneRuleText(zones)}. Ставка ${zones.bids[z.key] ?? '—'} ₽`}
                                                style={{ fontSize: 11, padding: '2px 8px', borderRadius: 24, background: '#ecfdf5', color: '#065f46', border: '1px solid #a7f3d0' }}>
                                                {z.label}
                                            </span>
                                        ))}
                                    </span>
                                )}

                                {/* Товары остаются кликабельными и в свёрнутом виде */}
                                {campaign.nm_ids.length > 1 && (
                                    <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
                                        {campaign.nm_ids.map(nmId => {
                                            const active = selectedNm === nmId;
                                            return (
                                                <button key={nmId} onClick={() => setSelectedNm(prev => (prev === nmId ? null : nmId))}
                                                    aria-pressed={active}
                                                    title={`${catalog[nmId]?.vendor_code || `#${nmId}`} — ${active ? 'показать всю кампанию' : 'смотреть только этот товар'}`}
                                                    style={{ padding: 1, borderRadius: 8, cursor: 'pointer', lineHeight: 0, background: active ? '#ecfdf5' : 'transparent', border: `1.5px solid ${active ? '#10b981' : 'transparent'}` }}>
                                                    <WbThumb nmId={nmId} size={26} />
                                                </button>
                                            );
                                        })}
                                    </span>
                                )}

                                <button onClick={() => setHeaderCollapsed(false)} className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto', fontSize: 12 }}>Развернуть ⌄</button>
                            </div>
                        ) : (
                        /* ─── Полный хедер кампании ─── */
                        <div className="glass-card static" style={{ padding: 20, marginBottom: 12 }}>
                            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
                                <div style={{ flex: '1 1 320px', minWidth: 260 }}>
                                    {/* Название редактируется прямо здесь — карандаш справа (как при создании) */}
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '0 0 6px' }}>
                                        <IcChart size={22} />
                                        <EditableName value={campaign.name || `#${campaign.campaign_id}`} onChange={renameWith} size={22} maxLength={50} />
                                    </div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 12, color: 'var(--color-text-dim)' }}>
                                        <span className={`badge ${STATUS_BADGE[campaign.status] || 'badge-secondary'}`}>{campaign.status_label}</span>
                                        {typeInfo && <span style={{ color: typeInfo.color, fontWeight: 600 }} title={typeInfo.hint}>{typeInfo.text}</span>}
                                        <span>#{campaign.campaign_id}</span>
                                        {campaign.subjects.length > 0 && <span>· {campaign.subjects.join(', ')}</span>}
                                    </div>
                                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
                                        {/* Порядок: Приостановить → Завершить → Открыть в WB. Цвета — из палитры: оранжевый/зелёный/красный/серый */}
                                        {(campaign.status === 9 || campaign.status === 11) && (
                                            <button onClick={toggleState} disabled={stateBusy} className="btn btn-secondary btn-sm"
                                                style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: campaign.status === 9 ? '#f59e0b' : '#10b981' }}>
                                                {campaign.status === 9 ? <IcPause size={14} /> : <IcPlay size={14} />}
                                                {stateBusy ? '…' : campaign.status === 9 ? 'Приостановить' : 'Запустить'}
                                            </button>
                                        )}
                                        {/* Переименование — карандашом у заголовка. Здесь только завершение/удаление. */}
                                        {(campaign.status === 4 || campaign.status === 9 || campaign.status === 11) && (
                                            <button onClick={doStop} disabled={manageBusy} className="btn btn-secondary btn-sm"
                                                style={{ fontSize: 13, color: '#ef4444' }} title="Завершить кампанию (необратимо)">
                                                {manageBusy ? '…' : 'Завершить'}
                                            </button>
                                        )}
                                        <a href={wbCampaignUrl(campaign, { from: dateFrom, to: dateTo })} target="_blank" rel="noreferrer" className="btn btn-secondary btn-sm"
                                            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: '#6b7280' }} title="Открыть кампанию в кабинете WB">
                                            <IcExternal size={14} />Открыть в WB
                                        </a>
                                        {campaign.status === 4 && (
                                            <button onClick={doDelete} disabled={manageBusy} className="btn btn-danger btn-sm"
                                                style={{ fontSize: 13 }} title="Удалить кампанию (только не запускавшуюся)">Удалить</button>
                                        )}
                                    </div>
                                </div>

                                {/* Остаток бюджета + расписание паузы — карточки одной высоты, метки на одном уровне.
                                    «Пополнить» — разовый долив через публичный API, «Автопополнение» — родное правило
                                    ВБ из кабинета (читаем и пишем его там же). И то, и другое — реальные деньги. */}
                                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'stretch' }}>
                                    <div style={{ minWidth: 140, padding: '10px 14px', border: '1px solid #d1d5db', borderRadius: 12, background: '#f9fafb', display: 'flex', flexDirection: 'column', gap: 6 }}>
                                        <div style={{ fontSize: 12, color: '#4b5563', fontWeight: 600 }}>Остаток бюджета</div>
                                        <div style={{ fontSize: 24, fontWeight: 700, color: campaign.budget <= 0 && campaign.status === 9 ? '#ef4444' : '#111827' }}>{fmt(campaign.budget)} ₽</div>
                                        <button onClick={() => setDepositModal(true)} className="btn btn-secondary btn-sm"
                                            style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6, fontSize: 13, color: '#10b981', fontWeight: 600 }}
                                            title="Пополнить бюджет кампании из кабинета WB (реальные деньги)">
                                            <IcPlus size={14} />Пополнить
                                        </button>
                                        <button onClick={() => setAutopayModal(true)} className="btn btn-secondary btn-sm"
                                            style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6, fontSize: 13, color: autorefill?.enabled ? '#10b981' : '#6b7280', fontWeight: autorefill?.enabled ? 600 : 500 }}
                                            title="Автопополнение бюджета — родная настройка кабинета ВБ">
                                            <IcWallet size={14} />{autorefill?.enabled ? `${fmt(autorefill.amount)} ₽ ниже ${fmt(autorefill.threshold)} ₽` : 'Автопополнение'}
                                        </button>
                                        <div style={{ fontSize: 12, color: '#6b7280', marginTop: 'auto' }}>Расход сегодня: {fmt(campaign.spend_today)} ₽</div>
                                    </div>
                                    <div style={{ minWidth: 160, padding: '10px 14px', border: '1px solid #d1d5db', borderRadius: 12, background: '#f9fafb', display: 'flex', flexDirection: 'column', gap: 6 }}>
                                        <div style={{ fontSize: 12, color: '#4b5563', fontWeight: 600 }}>Пауза по расписанию</div>
                                        <button onClick={() => setScheduleModal(true)} className="btn btn-secondary btn-sm"
                                            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: sc?.enabled ? '#10b981' : '#6b7280', fontWeight: sc?.enabled ? 600 : 500 }}>
                                            {sc?.enabled ? <><IcClock size={14} />{scheduleLabel(sc)}</> : <><IcGear size={14} />Настроить</>}
                                        </button>
                                        <button onClick={() => setBudgetLogModal(true)} style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#3b82f6', fontSize: 12.5, fontWeight: 500, display: 'inline-flex', alignItems: 'center', gap: 4, padding: 0, marginTop: 'auto' }}>
                                            <IcHistory size={13} />История бюджета
                                        </button>
                                    </div>
                                </div>
                            </div>

                            {/* Товары кампании: фото + nm_id + артикул продавца */}
                            {campaign.nm_ids.length > 0 && (
                                <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid #eef0f2' }}>
                                    <div style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', marginBottom: 8 }}>
                                        ТОВАРЫ КАМПАНИИ · {campaign.nm_ids.length}
                                    </div>
                                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
                                        {campaign.nm_ids.map(nmId => {
                                            const cat = catalog[nmId];
                                            const active = selectedNm === nmId;
                                            return (
                                                <div key={nmId} role="button" tabIndex={0} aria-pressed={active}
                                                    onClick={() => setSelectedNm(prev => (prev === nmId ? null : nmId))}
                                                    onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelectedNm(prev => (prev === nmId ? null : nmId)); } }}
                                                    style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 8px 6px 6px', border: `1px solid ${active ? '#10b981' : '#e5e7eb'}`, borderRadius: 12, background: active ? '#ecfdf5' : '#fff', color: 'inherit', cursor: 'pointer', boxShadow: active ? '0 0 0 1px #10b981' : undefined, transition: 'border-color .2s, background .2s' }}
                                                    title={active ? 'Показать воронку по всей кампании' : 'Показать воронку только по этому товару'}>
                                                    <WbThumb nmId={nmId} size={48} />
                                                    <div style={{ minWidth: 0 }}>
                                                        <div style={{ fontSize: 13, fontWeight: 600, color: active ? '#065f46' : '#111827', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{cat?.vendor_code || `#${nmId}`}</div>
                                                        <div style={{ fontSize: 11, color: active ? '#059669' : '#9ca3af', display: 'flex', alignItems: 'center', gap: 4 }}>
                                                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }} title="Артикул товара">
                                                                <span style={{ fontWeight: 700, color: active ? '#065f46' : '#374151' }}>{nmId}</span>
                                                                <button onClick={e => { e.preventDefault(); e.stopPropagation(); copyNm(nmId); }} title="Скопировать артикул" aria-label="Скопировать артикул"
                                                                    style={{ border: 'none', background: 'none', cursor: 'pointer', padding: 0, display: 'inline-flex', lineHeight: 1, color: active ? '#059669' : '#9ca3af' }}>
                                                                    <IcCopy size={11} />
                                                                </button>
                                                            </span>
                                                            {cat?.subject ? <span>· {cat.subject}</span> : null}
                                                        </div>
                                                    </div>
                                                    <a href={`https://www.wildberries.ru/catalog/${nmId}/detail.aspx`} target="_blank" rel="noreferrer"
                                                        onClick={e => e.stopPropagation()}
                                                        style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 28, height: 28, borderRadius: 8, border: '1px solid #e5e7eb', background: '#fff', color: '#6b7280', flexShrink: 0 }}
                                                        title="Открыть карточку товара на WB">
                                                        <IcExternal size={14} />
                                                    </a>
                                                </div>
                                            );
                                        })}
                                    </div>
                                </div>
                            )}

                            {/* Зоны показов: тумблеры вкл/выкл (где WB разрешает). Разбивка статистики по зонам — селектором внутри «По дням». */}
                            {zones && Object.keys(zones.placements).length > 0 && (
                                <ZonesPanel
                                    zones={zones}
                                    campaignId={campaignId}
                                    onZonesLocal={pl => setZones(z => z ? { ...z, placements: pl } : z)}
                                    onBidLocal={(zone, bid) => setZones(z => {
                                        if (!z) return z;
                                        // CPM · единая: одна ставка на обе зоны (редактор идёт через 'search')
                                        const unified = z.payment_type === 'cpm' && (z.bid_mode || '') === 'unified';
                                        const bids = unified ? { search: bid, recommendations: bid } : { ...z.bids, [zone]: bid };
                                        return { ...z, bids };
                                    })}
                                />
                            )}
                            {/* Редактор ставки зон не должен исчезать молча при ошибке/загрузке живого WB-вызова */}
                            {!zones && (zonesLoading || zonesError) && (
                                <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 0', fontSize: 12 }}>
                                    <span style={{ fontWeight: 700, letterSpacing: 0.3, color: 'var(--color-text-muted)' }}>ЗОНЫ ПОКАЗОВ</span>
                                    {zonesLoading ? (
                                        <span style={{ color: 'var(--color-text-muted)' }}>ставки зон загружаются…</span>
                                    ) : (
                                        <>
                                            <span style={{ color: 'var(--color-danger)' }}>⚠️ WB не ответил — ставки зон не загрузились</span>
                                            <button onClick={() => setReloadKey(k => k + 1)} className="btn btn-secondary btn-sm" style={{ fontSize: 11 }}>Обновить</button>
                                        </>
                                    )}
                                </div>
                            )}

                            {/* Свернуть — освободить экран под график и таблицу */}
                            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
                                <button onClick={() => setHeaderCollapsed(true)} className="btn btn-secondary btn-sm" style={{ fontSize: 12 }}>Свернуть ⌃</button>
                            </div>
                        </div>
                        )}

                        {campError && (
                            <div className="glass-card" style={{ padding: '12px 20px', marginBottom: 12, border: '1px solid var(--color-danger)' }}>
                                <span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {campError}</span>
                            </div>
                        )}

                        {/* Период + обновление */}
                        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
                            <PeriodPicker from={dateFrom} to={dateTo} onApply={(f, t) => { if (f && t) { setDateFrom(f); setDateTo(t); } }} minWidth={230} />
                            <button className="btn btn-secondary btn-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12 }} onClick={() => doRefresh()} disabled={loading || metricsLoading || refreshing}><IcRefresh size={14} />{refreshing ? 'Обновление…' : 'Обновить'}</button>
                        </div>

                        {/* Вкладки */}
                        <div style={{ display: 'flex', gap: 0, border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden', width: 'fit-content', marginBottom: 12 }}>
                            <div role="button" tabIndex={0} onClick={() => setTab('metrics')}
                                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setTab('metrics'); } }}
                                style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: tab === 'metrics' ? '6px 8px 6px 18px' : '8px 18px', fontSize: 13, fontWeight: 500, cursor: 'pointer', background: tab === 'metrics' ? '#3b82f6' : '#fff', color: tab === 'metrics' ? '#fff' : '#374151' }}>
                                <IcCalendar />Метрики
                                {tab === 'metrics' && (
                                    <span style={{ display: 'inline-flex', borderRadius: 6, overflow: 'hidden', border: '1px solid rgba(255,255,255,.55)' }}>
                                        {(([['table', 'По дням'], ['chart', 'График']]) as [typeof metricsView, string][]).map(([v, label]) => (
                                            <button key={v} onClick={e => { e.stopPropagation(); setMetricsView(v); }}
                                                style={{ padding: '3px 10px', fontSize: 12, fontWeight: 500, border: 'none', cursor: 'pointer', background: metricsView === v ? '#fff' : 'transparent', color: metricsView === v ? '#1d4ed8' : '#fff' }}>
                                                {label}
                                            </button>
                                        ))}
                                    </span>
                                )}
                            </div>
                            {clustersAvailable && (
                                <button onClick={() => setTab('clusters')}
                                    style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 18px', fontSize: 13, fontWeight: 500, cursor: 'pointer', border: 'none', borderLeft: '1px solid #e5e7eb', background: tab === 'clusters' ? '#3b82f6' : '#fff', color: tab === 'clusters' ? '#fff' : '#374151' }}>
                                    <IcClusters />Кластеризатор
                                </button>
                            )}
                            <button onClick={() => setTab('hourly')}
                                style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 18px', fontSize: 13, fontWeight: 500, cursor: 'pointer', border: 'none', borderLeft: '1px solid #e5e7eb', background: tab === 'hourly' ? '#3b82f6' : '#fff', color: tab === 'hourly' ? '#fff' : '#374151' }}>
                                <IcClock size={14} />По часам
                            </button>
                            <button onClick={() => setTab('intraday')}
                                style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 18px', fontSize: 13, fontWeight: 500, cursor: 'pointer', border: 'none', borderLeft: '1px solid #e5e7eb', background: tab === 'intraday' ? '#3b82f6' : '#fff', color: tab === 'intraday' ? '#fff' : '#374151' }}>
                                <IcClock size={14} />Внутри дня
                            </button>
                        </div>

                        {/* Выбранный товар — и метрики, и кластеры считаются по нему */}
                        {selectedNm != null && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', marginBottom: 8, background: '#ecfdf5', border: '1px solid #a7f3d0', borderRadius: 8, fontSize: 12, color: '#065f46' }}>
                                <b>{catalog[selectedNm]?.vendor_code || `#${selectedNm}`}</b>
                                <span style={{ color: '#059669' }}>#{selectedNm}</span>
                                <Tooltip text="Показать всю кампанию">
                                    <button onClick={() => setSelectedNm(null)}
                                        style={{ marginLeft: 'auto', border: 'none', background: 'none', cursor: 'pointer', color: '#065f46', fontSize: 12, padding: 0 }}>✕ вся кампания</button>
                                </Tooltip>
                            </div>
                        )}

                        {/* ─── Метрики: по дням / график / по зонам ─── */}
                        {tab === 'metrics' && (
                            <div className="glass-card static" style={{ padding: 0, overflow: 'hidden', flex: 1, minHeight: 0 }}>
                                {metricsView === 'chart' ? (
                                    <>
                                        {zoneSelectorBar}
                                        {zoneMetricsZone === 'total' ? (
                                            <>
                                                {metricsLoading && <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Загрузка метрик…</div>}
                                                {!metricsLoading && metricsError && <div style={{ padding: '16px 20px' }}><span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {metricsError === 'campaign_not_found' ? 'Кампания не найдена' : metricsError}</span></div>}
                                                {!metricsLoading && !metricsError && metrics && (metrics.rows.length === 0
                                                    ? <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>За выбранный период нет данных по кампании</div>
                                                    : <CampaignMetricsChart rows={metrics.rows} selected={chartMetrics} onToggle={toggleChartMetric} launchDate={campaign?.created_at} />)}
                                            </>
                                        ) : (
                                            <>
                                                {zoneMetricsLoading && <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Загрузка метрик…</div>}
                                                {!zoneMetricsLoading && zoneMetricsError && <div style={{ padding: '16px 20px' }}><span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {zoneMetricsError === 'campaign_not_found' ? 'Кампания не найдена' : zoneMetricsError}</span></div>}
                                                {!zoneMetricsLoading && !zoneMetricsError && zoneMetrics && (zoneChartRows.length === 0
                                                    ? <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>За выбранный период нет данных по зоне</div>
                                                    : <CampaignMetricsChart rows={zoneChartRows} selected={chartMetrics} onToggle={toggleChartMetric} launchDate={campaign?.created_at} />)}
                                            </>
                                        )}
                                    </>
                                ) : (
                                    // «По дням»: зона «Всего» — полная воронка; зона Поиск/Рекомендации — РК-метрики по зоне (воронка не делится)
                                    <>
                                        {isCpm && zoneSelectorBar}
                                        {zoneMetricsZone === 'total' ? (
                                            <>
                                                {metricsLoading && <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Загрузка метрик…</div>}
                                                {!metricsLoading && metricsError && <div style={{ padding: '16px 20px' }}><span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {metricsError === 'campaign_not_found' ? 'Кампания не найдена' : metricsError}</span></div>}
                                                {!metricsLoading && !metricsError && metrics && (metrics.rows.length === 0
                                                    ? <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>За выбранный период нет данных по кампании</div>
                                                    : <CampaignMetricsTable resp={metrics} />)}
                                            </>
                                        ) : (
                                            <>
                                                {zoneMetricsLoading && <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Загрузка метрик…</div>}
                                                {!zoneMetricsLoading && zoneMetricsError && <div style={{ padding: '16px 20px' }}><span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {zoneMetricsError === 'campaign_not_found' ? 'Кампания не найдена' : zoneMetricsError}</span></div>}
                                                {!zoneMetricsLoading && !zoneMetricsError && zoneMetrics && (zoneMetrics.rows.length === 0
                                                    ? <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>За выбранный период нет данных по зоне</div>
                                                    : <CampaignZoneMetricsTable resp={zoneMetrics} />)}
                                            </>
                                        )}
                                    </>
                                )}
                            </div>
                        )}

                        {/* ─── Кластеризатор ─── */}
                        {tab === 'clusters' && clustersAvailable && (
                            <div className="glass-card static" style={{ padding: 20, flex: 1, minHeight: 0, overflow: 'hidden' }}>
                                {loading && <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Загрузка кластеров…</div>}
                                {!loading && error && (
                                    <div className="glass-card" style={{ padding: '16px 20px', border: '1px solid var(--color-danger)', background: '#fef2f2' }}>
                                        <span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {error}</span>
                                    </div>
                                )}
                                {!loading && !error && data && (
                                    data.clusters.length === 0 ? (
                                        <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>За выбранный период у кампании нет данных по кластерам</div>
                                    ) : (
                                        // Сводка по кампании — в итоговой строке внизу таблицы, не в крупных карточках.
                                        // CPC: только просмотр статистики — ставка единая, минус-фраз нет, поэтому редакторы
                                        // ставок/минус-фраз не передаём (ClusterTable без minus/bids идёт read-only).
                                        <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
                                        {isCpc && (
                                            <div style={{ flexShrink: 0, marginBottom: 10, padding: '8px 12px', background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 12, fontSize: 12, color: '#1e3a8a' }}>
                                                Статистика по фразам — только просмотр. У CPC ставка единая на все фразы, а минус-фраз нет: менять ставки и исключать запросы отсюда нельзя.
                                            </div>
                                        )}
                                        <div style={{ flex: 1, minHeight: 0 }}>
                                        <ClusterTable
                                            clusters={data.clusters}
                                            targetDrr={data.target_drr}
                                            aov={data.aov}
                                            defaultBid={data.default_bid}
                                            minBid={zones?.min_bids?.search ?? null}
                                            exportName={`clusters_${campaignId}_${dateFrom}_${dateTo}`}
                                            bidLocked={clusterUnified}
                                            minus={isCpm ? { pending, onToggle: handleToggleMinus, onBulk: handleBulkMinus, error: minusError } : undefined}
                                            bids={isCpm ? { pending: bidPending, onSetBid: handleSetBid, onBulkBid: handleBulkBid, error: bidError } : undefined}
                                            positions={positions}
                                            onCollectPositions={handleCollectPositions}
                                            onStopPositions={handleStopPositions}
                                            collecting={posCollecting}
                                            onCollectOne={handleCollectOne}
                                            collectingOne={collectingOne}
                                        />
                                        </div>
                                        </div>
                                    )
                                )}
                            </div>
                        )}

                        {/* ─── Расход по часам ─── */}
                        {tab === 'hourly' && (
                            <div className="glass-card static" style={{ padding: 0, overflow: 'auto', flex: 1, minHeight: 0 }}>
                                {hourlyLoading && <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Загрузка расхода по часам…</div>}
                                {!hourlyLoading && hourlyError && <div style={{ padding: '16px 20px' }}><span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {hourlyError === 'campaign_not_found' ? 'Кампания не найдена' : hourlyError}</span></div>}
                                {!hourlyLoading && !hourlyError && hourly && <CampaignHourlyChart resp={hourly} />}
                            </div>
                        )}

                        {/* ─── Внутри дня: показы/клики/CTR по интервалам снимков ─── */}
                        {tab === 'intraday' && (
                            <div className="glass-card static" style={{ padding: 0, overflow: 'auto', flex: 1, minHeight: 0 }}>
                                {intradayLoading && <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Загрузка внутридневных метрик…</div>}
                                {!intradayLoading && intradayError && <div style={{ padding: '16px 20px' }}><span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {intradayError === 'campaign_not_found' ? 'Кампания не найдена' : intradayError}</span></div>}
                                {!intradayLoading && !intradayError && intraday && (
                                    <CampaignIntradayChart
                                        resp={intraday}
                                        onSetInterval={async (m) => {
                                            try { await api.setAdsSnapshotInterval(m); setReloadKey(k => k + 1); toast.success('Частота снимков: ' + m + ' мин'); }
                                            catch (e) { toast.error(e instanceof Error ? e.message : 'Не удалось сохранить частоту'); }
                                        }}
                                    />
                                )}
                            </div>
                        )}

                        {scheduleModal && (
                            <ScheduleModal campaign={campaign} initial={sc} onClose={() => setScheduleModal(false)} onSave={saveSchedule} />
                        )}
                        {budgetLogModal && (
                            <BudgetLedgerModal campaign={campaign} onClose={() => setBudgetLogModal(false)} />
                        )}
                        {depositModal && (
                            <DepositModal campaign={campaign} onClose={() => setDepositModal(false)} onDeposited={onDeposited} />
                        )}
                        {autopayModal && (
                            <AutopayModal campaign={campaign} onClose={() => setAutopayModal(false)}
                                onSaved={s => { setAutorefill(s); toast.success(s.enabled ? `Автопополнение ВБ: +${fmt(s.amount)} ₽ при остатке ниже ${fmt(s.threshold)} ₽` : 'Автопополнение в ВБ выключено'); }} />
                        )}
                    </>
                )}
            </div>
        </PageGuard>
    );
}
