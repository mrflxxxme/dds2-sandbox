'use client';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import type { AdsManagerCampaign, AdsAutopaySetting, CampaignClustersResponse, CampaignMetricRow, CampaignMetricsResponse, CampaignZoneMetricsResponse, CampaignHourlySpend, CampaignIntradayMetrics, PositionSnapshot, CampaignZones, SearchCluster } from '@/types/api';
import { IcChart, IcClusters, IcCalendar, IcRefresh, IcPause, IcPlay, IcExternal, IcClock, IcGear, IcHistory } from '../../components/icons';
import ClusterTable from '../../components/ClusterTable';
import CampaignMetricsTable from '../../components/CampaignMetricsTable';
import CampaignZoneMetricsTable from '../../components/CampaignZoneMetricsTable';
import CampaignHourlyChart from '../../components/CampaignHourlyChart';
import CampaignIntradayChart from '../../components/CampaignIntradayChart';
import CampaignMetricsChart, { DEFAULT_CHART_METRICS, type MetricKey } from '../../components/CampaignMetricsChart';
import AutopayModal from '../../components/AutopayModal';
import AutopayLogModal from '../../components/AutopayLogModal';
import WbThumb from '../../components/WbThumb';
import AdsPeriodPicker from '../../components/AdsPeriodPicker';
import Tooltip from '../../components/Tooltip';
import EditableName from '../../components/EditableName';
import { useToast } from '../../components/Toasts';
import ZonesPanel, { ZONES, zoneRuleText } from '../../components/ZonesPanel';
import { fmt, iso, STATUS_BADGE, adTypeLabel, wbCampaignUrl, autopayLabel } from '../../components/adsShared';

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

    // Кампания и её автопополнение — из общего списка (отдельного эндпоинта нет)
    const [campaign, setCampaign] = useState<AdsManagerCampaign | null>(null);
    const [campLoading, setCampLoading] = useState(true);
    const [campError, setCampError] = useState('');
    const [autopay, setAutopay] = useState<Record<string, AdsAutopaySetting>>({});
    const [headerCollapsed, setHeaderCollapsed] = useState(false);
    const [autopayModal, setAutopayModal] = useState(false);
    const [autopayLogModal, setAutopayLogModal] = useState(false);
    const [stateBusy, setStateBusy] = useState(false);

    const loadCampaign = useCallback(async () => {
        setCampLoading(true); setCampError('');
        try {
            const [list, ap] = await Promise.all([api.getAdCampaignsList(), api.getCampaignsAutopay().catch(() => ({}))]);
            const found = list.find(c => c.campaign_id === campaignId) ?? null;
            setCampaign(found);
            setAutopay(ap);
            if (!found) setCampError('Кампания не найдена. Возможно, она ещё не синхронизирована.');
        } catch (e) {
            setCampError(e instanceof Error ? e.message : 'Ошибка загрузки кампании');
        } finally { setCampLoading(false); }
    }, [campaignId]);

    useEffect(() => { loadCampaign(); }, [loadCampaign]);

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
    const [tab, setTab] = useState<CampTab>('metrics');
    // При заходе в кампанию метрики показываем графиком; «По дням» — та же таблица;
    // «По зонам» — отдельная таблица РК-метрик с селектором зоны (только CPM)
    const [metricsView, setMetricsView] = useState<'chart' | 'table' | 'zones'>('chart');
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
    // Кластеризатор (поисковые фразы) есть только у CPM. Разбивка статистики по зонам
    // живёт на отдельной вкладке «По зонам»; блок «ЗОНЫ ПОКАЗОВ» — лишь вкл/выкл зон.
    const clustersAvailable = isCpm;
    // Вкладка кластеров исчезла под ногами (выбрали «Рекомендации») — уводим на метрики,
    // иначе на экране не осталось бы ни одной вкладки с содержимым.
    useEffect(() => { if (!clustersAvailable) setTab(t => (t === 'clusters' ? 'metrics' : t)); }, [clustersAvailable]);
    // Ручное пополнение бюджета
    const [depositAmount, setDepositAmount] = useState('');
    const [depositSource, setDepositSource] = useState(0);  // 0 = счёт кабинета Продвижения, 1 = баланс взаиморасчёта
    const [depositing, setDepositing] = useState(false);

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

    // ─── Метрики по зонам показов (только CPM): своя таблица РК-метрик + селектор зоны ───
    const [zoneMetricsZone, setZoneMetricsZone] = useState<'total' | 'search' | 'recommendations'>('total');
    const [zoneMetrics, setZoneMetrics] = useState<CampaignZoneMetricsResponse | null>(null);
    const [zoneMetricsLoading, setZoneMetricsLoading] = useState(false);
    const [zoneMetricsError, setZoneMetricsError] = useState('');
    // Не CPM — вкладки «По зонам» нет; если вдруг активна, уводим на график
    useEffect(() => { if (!isCpm) setMetricsView(v => (v === 'zones' ? 'chart' : v)); }, [isCpm]);
    useEffect(() => {
        // Зонные данные нужны и таблице «По зонам», и графику при выбранной зоне (кроме «Всего»)
        const needZone = tab === 'metrics' && (metricsView === 'zones' || (metricsView === 'chart' && zoneMetricsZone !== 'total'));
        if (!Number.isFinite(campaignId) || !needZone) return;
        const controller = new AbortController();
        setZoneMetricsLoading(true); setZoneMetricsError(''); setZoneMetrics(null);
        api.getCampaignZoneMetrics(campaignId, dateFrom, dateTo, zoneMetricsZone)
            .then(res => { if (controller.signal.aborted) return; if (res.error) setZoneMetricsError(res.error); else setZoneMetrics(res); })
            .catch(e => { if (!controller.signal.aborted) setZoneMetricsError(e instanceof Error ? e.message : 'Ошибка загрузки метрик по зонам'); })
            .finally(() => { if (!controller.signal.aborted) setZoneMetricsLoading(false); });
        return () => controller.abort();
    }, [campaignId, dateFrom, dateTo, reloadKey, zoneMetricsZone, tab, metricsView]);

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
        open_card: 0, cr1: 0, cr2: 0, orders_sum: 0, cpl: null, avg_price: 0, customer_price: 0, spp: 0, drr: 0,
    })), [zoneMetrics]);

    // Селектор зоны показов — общий для «Графика» и таблицы «По зонам»
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
    useEffect(() => {
        if (!Number.isFinite(campaignId)) return;
        api.getCampaignZones(campaignId, dateFrom, dateTo, selectedNm)
            .then(z => setZones(z.error ? null : z))
            .catch(() => { /* зоны не критичны */ });
    }, [campaignId, dateFrom, dateTo, selectedNm, reloadKey]);


    const doDeposit = async () => {
        if (!campaign) return;
        const amt = Math.round(Number(depositAmount) || 0);
        if (amt < 1000) { toast.warning('Минимальная сумма пополнения — 1000 ₽.'); return; }
        const srcLabel = depositSource === 1 ? 'с баланса взаиморасчёта' : 'со счёта кабинета Продвижения';
        if (!window.confirm(`Пополнить бюджет кампании на ${amt} ₽ реальными деньгами ${srcLabel} WB?`)) return;
        setDepositing(true); setCampError('');
        try {
            const res = await api.depositCampaignBudget(campaign.campaign_id, amt, depositSource);
            if (!res.ok) { toast.error(res.error || 'Не удалось пополнить бюджет'); return; }
            toast.success(`Бюджет пополнен на ${amt.toLocaleString('ru-RU')} ₽`);
            setDepositAmount('');
            await loadCampaign();
        } catch (e) {
            setCampError(e instanceof Error ? e.message : 'Ошибка пополнения');
        } finally { setDepositing(false); }
    };

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
        for (const c of items) {
            try {
                const res = await api.toggleClusterMinus(campaignId, { nm_id: nmId, norm_query: c.norm_query, action });
                if (res.ok) {
                    setData(prev => prev ? { ...prev, clusters: prev.clusters.map(x => x.norm_query === c.norm_query ? { ...x, is_minused: action === 'add' } : x) } : prev);
                } else {
                    setMinusError(res.error || `WB отклонил «${c.norm_query}»`);
                }
            } catch (e) {
                setMinusError(e instanceof Error ? e.message : 'Ошибка обращения к WB');
            } finally {
                setPending(prev => { const n = new Set(prev); n.delete(c.norm_query); return n; });
            }
        }
    };

    const handleSetBid = async (c: SearchCluster, bid: number) => {
        const nmId = data?.nm_ids?.[0] ?? campaign?.nm_ids?.[0];
        if (nmId == null) { setBidError('Не удалось определить товар (nm_id) кампании'); return; }
        if (!window.confirm(`Поставить ставку ${formatNumber(bid, 0)} ₽ на кластер «${c.norm_query}» в кабинете WB?`)) return;
        setBidError(null);
        setBidPending(prev => new Set(prev).add(c.norm_query));
        try {
            const res = await api.setCampaignClusterBid(campaignId, { nm_id: nmId, norm_query: c.norm_query, bid });
            if (!res.ok) { setBidError(res.error || 'WB отклонил ставку'); return; }
            setData(prev => prev ? { ...prev, clusters: prev.clusters.map(x => x.norm_query === c.norm_query ? { ...x, bid: res.bid ?? bid } : x) } : prev);
        } catch (e) {
            setBidError(e instanceof Error ? e.message : 'Ошибка обращения к WB');
        } finally {
            setBidPending(prev => { const nx = new Set(prev); nx.delete(c.norm_query); return nx; });
        }
    };

    // Массовая ставка: одно подтверждение, затем последовательно по WB. bid=0 — сброс к ставке кампании.
    const handleBulkBid = async (items: { cluster: SearchCluster; bid: number }[], label: string) => {
        if (items.length === 0) return;
        const nmId = data?.nm_ids?.[0] ?? campaign?.nm_ids?.[0];
        if (nmId == null) { setBidError('Не удалось определить товар (nm_id) кампании'); return; }
        // Подтверждение уже получено в нижней панели кластеризатора
        setBidError(null);
        setBidPending(prev => { const n = new Set(prev); items.forEach(i => n.add(i.cluster.norm_query)); return n; });
        for (const { cluster, bid } of items) {
            try {
                const res = await api.setCampaignClusterBid(campaignId, { nm_id: nmId, norm_query: cluster.norm_query, bid });
                if (res.ok) {
                    setData(prev => prev ? { ...prev, clusters: prev.clusters.map(x => x.norm_query === cluster.norm_query ? { ...x, bid: bid > 0 ? (res.bid ?? bid) : null } : x) } : prev);
                } else {
                    setBidError(res.error || `WB отклонил «${cluster.norm_query}»`);
                }
            } catch (e) {
                setBidError(e instanceof Error ? e.message : 'Ошибка обращения к WB');
            } finally {
                setBidPending(prev => { const n = new Set(prev); n.delete(cluster.norm_query); return n; });
            }
        }
    };

    const toggleState = async () => {
        if (!campaign) return;
        const active = campaign.status !== 9;  // не активна → запускаем, активна → пауза
        setStateBusy(true);
        try {
            const r = await api.setCampaignState(campaign.campaign_id, active);
            if (r && (r as { ok?: boolean }).ok === false) toast.error((r as { error?: string }).error || 'Не удалось изменить статус');
            else toast.success(active ? 'Кампания запущена' : 'Кампания приостановлена');
            await loadCampaign();
        }
        catch (e) { toast.error(e instanceof Error ? e.message : 'Не удалось изменить статус кампании'); }
        finally { setStateBusy(false); }
    };

    const saveAutopay = async (s: AdsAutopaySetting) => {
        const res = await api.setCampaignAutopay(campaignId, s);
        setAutopay(res.settings);
        if (s.enabled) loadCampaign();  // включение активирует кампанию — подтянем статус
    };

    // ─── Управление кампанией (завершить / переименовать / удалить) ───
    const [manageBusy, setManageBusy] = useState(false);
    const renameWith = async (name: string) => {
        if (!campaign || name === campaign.name) return;
        setManageBusy(true); setCampError('');
        try {
            const res = await api.renameCampaign(campaign.campaign_id, name);
            if (!res.ok) toast.error(res.error || 'Не удалось переименовать кампанию');
            else toast.success('Кампания переименована');
            await loadCampaign();
        } catch (e) { toast.error(e instanceof Error ? e.message : 'Не удалось переименовать кампанию'); }
        finally { setManageBusy(false); }
    };

    const doStop = async () => {
        if (!campaign || !window.confirm('Завершить кампанию? Это НЕОБРАТИМО — запустить её снова будет нельзя.')) return;
        setManageBusy(true); setCampError('');
        try {
            const res = await api.stopCampaign(campaign.campaign_id);
            if (!res.ok) toast.error(res.error || 'Не удалось завершить кампанию');
            else toast.success('Кампания завершена');
            await loadCampaign();
        } catch (e) { toast.error(e instanceof Error ? e.message : 'Не удалось завершить кампанию'); }
        finally { setManageBusy(false); }
    };

    const doDelete = async () => {
        if (!campaign || !window.confirm('Удалить кампанию в WB? Действие необратимо.')) return;
        setManageBusy(true); setCampError('');
        try {
            const res = await api.deleteCampaign(campaign.campaign_id);
            if (!res.ok) toast.error(res.error || 'Не удалось удалить кампанию');
            else toast.success('Кампания удалена');
            await loadCampaign();
        } catch (e) { toast.error(e instanceof Error ? e.message : 'Не удалось удалить кампанию'); }
        finally { setManageBusy(false); }
    };

    const ap = autopay[String(campaignId)];
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

                                <span style={{ fontSize: 12, color: '#6b7280' }}>
                                    Остаток: <b style={{ fontSize: 14, color: campaign.budget <= 0 && campaign.status === 9 ? '#ef4444' : '#111827' }}>{fmt(campaign.budget)} ₽</b>
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

                                <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                                    <input type="number" min={1000} step={100} inputMode="numeric" value={depositAmount} placeholder="Сумма, ₽"
                                        onChange={e => setDepositAmount(e.target.value)} disabled={depositing}
                                        style={{ width: 92, padding: '4px 6px', border: '1px solid #e5e7eb', borderRadius: 8, fontSize: 12, background: '#fff' }} />
                                    <button onClick={doDeposit} disabled={depositing || Number(depositAmount) < 1000} className="btn btn-primary btn-sm" style={{ fontSize: 12 }}>
                                        {depositing ? '…' : 'Пополнить'}
                                    </button>
                                </span>

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

                                {/* Остаток бюджета + автопополнение + пополнение — карточки одной высоты, метки на одном уровне */}
                                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'stretch' }}>
                                    <div style={{ minWidth: 140, padding: '10px 14px', border: '1px solid #d1d5db', borderRadius: 12, background: '#f9fafb', display: 'flex', flexDirection: 'column', gap: 6 }}>
                                        <div style={{ fontSize: 12, color: '#4b5563', fontWeight: 600 }}>Остаток бюджета</div>
                                        <div style={{ fontSize: 24, fontWeight: 700, color: campaign.budget <= 0 && campaign.status === 9 ? '#ef4444' : '#111827' }}>{fmt(campaign.budget)} ₽</div>
                                        <div style={{ fontSize: 12, color: '#6b7280', marginTop: 'auto' }}>Расход сегодня: {fmt(campaign.spend_today)} ₽</div>
                                    </div>
                                    <div style={{ minWidth: 160, padding: '10px 14px', border: '1px solid #d1d5db', borderRadius: 12, background: '#f9fafb', display: 'flex', flexDirection: 'column', gap: 6 }}>
                                        <div style={{ fontSize: 12, color: '#4b5563', fontWeight: 600 }}>Автопополнение</div>
                                        <button onClick={() => setAutopayModal(true)} className="btn btn-secondary btn-sm"
                                            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: ap?.enabled ? '#10b981' : '#6b7280', fontWeight: ap?.enabled ? 600 : 500 }}>
                                            {ap?.enabled ? <><IcClock size={14} />{autopayLabel(ap)}</> : <><IcGear size={14} />Настроить</>}
                                        </button>
                                        <button onClick={() => setAutopayLogModal(true)} style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#3b82f6', fontSize: 12.5, fontWeight: 500, display: 'inline-flex', alignItems: 'center', gap: 4, padding: 0, marginTop: 'auto' }}>
                                            <IcHistory size={13} />История пополнений
                                        </button>
                                    </div>
                                    <div style={{ minWidth: 210, padding: '10px 14px', border: '1px solid #d1d5db', borderRadius: 12, background: '#f9fafb', display: 'flex', flexDirection: 'column', gap: 6 }}>
                                        <div style={{ fontSize: 12, color: '#4b5563', fontWeight: 600 }}>Пополнение бюджета</div>
                                        <div style={{ display: 'flex', gap: 6 }}>
                                            <input type="number" min={1000} step={100} inputMode="numeric" value={depositAmount} placeholder="Сумма, ₽"
                                                onChange={e => setDepositAmount(e.target.value)}
                                                disabled={depositing}
                                                style={{ width: 100, padding: '7px 9px', border: '1px solid #d1d5db', borderRadius: 8, fontSize: 14, color: '#111827', background: '#fff' }} />
                                            <select value={depositSource} onChange={e => setDepositSource(Number(e.target.value))} disabled={depositing}
                                                title="Откуда списать деньги"
                                                style={{ padding: '7px 9px', border: '1px solid #d1d5db', borderRadius: 8, fontSize: 14, color: '#111827', background: '#fff' }}>
                                                <option value={0}>Счёт</option>
                                                <option value={1}>Баланс</option>
                                            </select>
                                        </div>
                                        <button onClick={doDeposit} disabled={depositing || Number(depositAmount) < 1000} className="btn btn-primary btn-sm" style={{ fontSize: 13, marginTop: 'auto' }}>
                                            {depositing ? 'Пополняем…' : 'Пополнить'}
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
                                                        <div style={{ fontSize: 11, color: active ? '#059669' : '#9ca3af' }}>#{nmId}{cat?.subject ? ` · ${cat.subject}` : ''}</div>
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

                            {/* Зоны показов: тумблеры вкл/выкл (где WB разрешает). Разбивка статистики по зонам — на вкладке «По зонам». */}
                            {zones && Object.keys(zones.placements).length > 0 && (
                                <ZonesPanel
                                    zones={zones}
                                    campaignId={campaignId}
                                    onChanged={() => setReloadKey(k => k + 1)}
                                />
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
                            <AdsPeriodPicker from={dateFrom} to={dateTo} onApply={(f, t) => { if (f && t) { setDateFrom(f); setDateTo(t); } }} minWidth={230} />
                            <button className="btn btn-secondary btn-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12 }} onClick={() => setReloadKey(k => k + 1)} disabled={loading || metricsLoading}><IcRefresh size={14} />Обновить</button>
                        </div>

                        {/* Вкладки */}
                        <div style={{ display: 'flex', gap: 0, border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden', width: 'fit-content', marginBottom: 12 }}>
                            <div role="button" tabIndex={0} onClick={() => setTab('metrics')}
                                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setTab('metrics'); } }}
                                style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: tab === 'metrics' ? '6px 8px 6px 18px' : '8px 18px', fontSize: 13, fontWeight: 500, cursor: 'pointer', background: tab === 'metrics' ? '#3b82f6' : '#fff', color: tab === 'metrics' ? '#fff' : '#374151' }}>
                                <IcCalendar />Метрики
                                {tab === 'metrics' && (
                                    <span style={{ display: 'inline-flex', borderRadius: 6, overflow: 'hidden', border: '1px solid rgba(255,255,255,.55)' }}>
                                        {(([['table', 'По дням'], ['chart', 'График'], ...(isCpm ? [['zones', 'По зонам']] : [])]) as [typeof metricsView, string][]).map(([v, label]) => (
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
                                {metricsView === 'zones' ? (
                                    <>
                                        {zoneSelectorBar}
                                        {zoneMetricsLoading && <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Загрузка метрик…</div>}
                                        {!zoneMetricsLoading && zoneMetricsError && <div style={{ padding: '16px 20px' }}><span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {zoneMetricsError === 'campaign_not_found' ? 'Кампания не найдена' : zoneMetricsError}</span></div>}
                                        {!zoneMetricsLoading && !zoneMetricsError && zoneMetrics && (zoneMetrics.rows.length === 0
                                            ? <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>За выбранный период нет данных по зоне</div>
                                            : <CampaignZoneMetricsTable resp={zoneMetrics} />)}
                                    </>
                                ) : metricsView === 'chart' ? (
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
                                    <>
                                        {metricsLoading && <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Загрузка метрик…</div>}
                                        {!metricsLoading && metricsError && <div style={{ padding: '16px 20px' }}><span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {metricsError === 'campaign_not_found' ? 'Кампания не найдена' : metricsError}</span></div>}
                                        {!metricsLoading && !metricsError && metrics && (metrics.rows.length === 0
                                            ? <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>За выбранный период нет данных по кампании</div>
                                            : <CampaignMetricsTable resp={metrics} />)}
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
                                        // Сводка по кампании — в итоговой строке внизу таблицы, не в крупных карточках
                                        <ClusterTable
                                            clusters={data.clusters}
                                            targetDrr={data.target_drr}
                                            aov={data.aov}
                                            defaultBid={data.default_bid}
                                            exportName={`clusters_${campaignId}_${dateFrom}_${dateTo}`}
                                            minus={{ pending, onToggle: handleToggleMinus, onBulk: handleBulkMinus, error: minusError }}
                                            bids={{ pending: bidPending, onSetBid: handleSetBid, onBulkBid: handleBulkBid, error: bidError }}
                                            positions={positions}
                                            onCollectPositions={handleCollectPositions}
                                            onStopPositions={handleStopPositions}
                                            collecting={posCollecting}
                                            onCollectOne={handleCollectOne}
                                            collectingOne={collectingOne}
                                        />
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

                        {autopayModal && (
                            <AutopayModal campaign={campaign} initial={ap} onClose={() => setAutopayModal(false)} onSave={saveAutopay} />
                        )}
                        {autopayLogModal && (
                            <AutopayLogModal campaign={campaign} onClose={() => setAutopayLogModal(false)} />
                        )}
                    </>
                )}
            </div>
        </PageGuard>
    );
}
