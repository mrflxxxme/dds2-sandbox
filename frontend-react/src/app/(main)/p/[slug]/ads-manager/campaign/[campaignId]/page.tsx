'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import type { AdsManagerCampaign, AdsAutopaySetting, CampaignClustersResponse, CampaignMetricsResponse, CampaignZones, SearchCluster } from '@/types/api';
import { IcChart, IcClusters, IcCalendar, IcRefresh, IcPause, IcPlay, IcExternal, IcClock, IcGear, IcHistory } from '../../components/icons';
import ClusterTable from '../../components/ClusterTable';
import CampaignMetricsTable from '../../components/CampaignMetricsTable';
import CampaignMetricsChart, { DEFAULT_CHART_METRICS, type MetricKey } from '../../components/CampaignMetricsChart';
import AutopayModal from '../../components/AutopayModal';
import AutopayLogModal from '../../components/AutopayLogModal';
import WbThumb from '../../components/WbThumb';
import AdsPeriodPicker from '../../components/AdsPeriodPicker';
import { fmt, iso, STATUS_BADGE, adTypeLabel, wbCampaignUrl } from '../../components/adsShared';

type CatalogEntry = { vendor_code: string; subject: string; brand: string };

type CampTab = 'metrics' | 'clusters';

// Зоны показов WB: ставка и статистика у каждой своя
const ZONES: { key: 'search' | 'recommendations'; label: string }[] = [
    { key: 'search', label: 'Поиск' },
    { key: 'recommendations', label: 'Рекомендации' },
];

/** Как устроена ставка у этого типа кампании (правила WB, зоны переключать нельзя). */
function zoneRuleText(z: CampaignZones): string {
    if (z.payment_type === 'cpc') return 'CPC · только поиск, ставка за клик — единая для всех фраз';
    if (z.bid_mode === 'unified') return 'CPM · единая ставка сразу на все зоны, они работают одновременно';
    return 'CPM · ручные ставки';
}

export default function CampaignPage() {
    const routeParams = useParams();
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
    // При заходе в кампанию метрики показываем графиком; «По дням» — та же таблица
    const [metricsView, setMetricsView] = useState<'chart' | 'table'>('chart');
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
        if (amt < 1000) { setCampError('Минимальная сумма пополнения — 1000 ₽.'); return; }
        const srcLabel = depositSource === 1 ? 'с баланса взаиморасчёта' : 'со счёта кабинета Продвижения';
        if (!window.confirm(`Пополнить бюджет кампании на ${amt} ₽ реальными деньгами ${srcLabel} WB?`)) return;
        setDepositing(true); setCampError('');
        try {
            const res = await api.depositCampaignBudget(campaign.campaign_id, amt, depositSource);
            if (!res.ok) { setCampError(res.error || 'Не удалось пополнить бюджет'); return; }
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
        try { await api.setCampaignState(campaign.campaign_id, active); await loadCampaign(); }
        catch (e) { setCampError(e instanceof Error ? e.message : 'Не удалось изменить статус кампании'); }
        finally { setStateBusy(false); }
    };

    const saveAutopay = async (s: AdsAutopaySetting) => {
        const res = await api.setCampaignAutopay(campaignId, s);
        setAutopay(res.settings);
        if (s.enabled) loadCampaign();  // включение активирует кампанию — подтянем статус
    };

    // Статистика зоны: у CPM берём расчётную разбивку из кластеров, у CPC зона одна —
    // это вся кампания, поэтому берём итог метрик за период.
    const zoneStat = (key: 'search' | 'recommendations') => {
        // CPC: зона одна, данные прямые (рекламные заказы, не воронка)
        if (zones?.payment_type === 'cpc') return key === 'search' ? zones.zone_stats?.search ?? null : null;
        return key === 'search' ? data?.zone_stats?.search ?? null : data?.zone_stats?.recommendations ?? null;
    };

    const ap = autopay[String(campaignId)];
    const typeInfo = useMemo(() => (campaign ? adTypeLabel(campaign) : null), [campaign]);

    return (
        <PageGuard page="ads-manager">
            <div className="animate-in">
                <Link href={`/p/${slug}/ads-manager`} className="btn btn-secondary btn-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, marginBottom: 12 }}>
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
                                    <button onClick={toggleState} disabled={stateBusy} title={campaign.status === 9 ? 'Приостановить рекламу' : 'Запустить рекламу'}
                                        aria-pressed={campaign.status === 9}
                                        style={{ width: 38, height: 20, borderRadius: 10, border: 'none', cursor: 'pointer', padding: 2, background: campaign.status === 9 ? '#10b981' : '#d1d5db', display: 'inline-flex', justifyContent: campaign.status === 9 ? 'flex-end' : 'flex-start', opacity: stateBusy ? 0.6 : 1 }}>
                                        <span style={{ width: 16, height: 16, borderRadius: '50%', background: '#fff' }} />
                                    </button>
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
                                    <h1 style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 22, fontWeight: 700, margin: '0 0 6px' }}>
                                        <IcChart size={22} />{campaign.name || `#${campaign.campaign_id}`}
                                    </h1>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 12, color: 'var(--color-text-dim)' }}>
                                        <span className={`badge ${STATUS_BADGE[campaign.status] || 'badge-secondary'}`}>{campaign.status_label}</span>
                                        {typeInfo && <span style={{ color: typeInfo.color, fontWeight: 600 }} title={typeInfo.hint}>{typeInfo.text}</span>}
                                        <span>#{campaign.campaign_id}</span>
                                        {campaign.subjects.length > 0 && <span>· {campaign.subjects.join(', ')}</span>}
                                    </div>
                                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
                                        {(campaign.status === 9 || campaign.status === 11) && (
                                            <button onClick={toggleState} disabled={stateBusy} className="btn btn-secondary btn-sm"
                                                style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: campaign.status === 9 ? '#f59e0b' : '#10b981' }}>
                                                {campaign.status === 9 ? <IcPause size={14} /> : <IcPlay size={14} />}
                                                {stateBusy ? '…' : campaign.status === 9 ? 'Приостановить' : 'Запустить'}
                                            </button>
                                        )}
                                        <a href={wbCampaignUrl(campaign, { from: dateFrom, to: dateTo })} target="_blank" rel="noreferrer" className="btn btn-secondary btn-sm"
                                            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }} title="Открыть кампанию в кабинете WB">
                                            <IcExternal size={14} />Открыть в WB
                                        </a>
                                    </div>
                                </div>

                                {/* Остаток бюджета + автопополнение */}
                                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                                    <div style={{ minWidth: 140, padding: '10px 14px', border: '1px solid #e5e7eb', borderRadius: 12, background: '#f9fafb' }}>
                                        <div style={{ fontSize: 11, color: '#6b7280' }}>Остаток бюджета</div>
                                        <div style={{ fontSize: 24, fontWeight: 700, color: campaign.budget <= 0 && campaign.status === 9 ? '#ef4444' : '#111827' }}>{fmt(campaign.budget)} ₽</div>
                                        <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 2 }}>Расход сегодня: {fmt(campaign.spend_today)} ₽</div>
                                    </div>
                                    <div style={{ minWidth: 160, padding: '10px 14px', border: '1px solid #e5e7eb', borderRadius: 12, background: '#f9fafb', display: 'flex', flexDirection: 'column', gap: 6 }}>
                                        <div style={{ fontSize: 11, color: '#6b7280' }}>Автопополнение</div>
                                        <button onClick={() => setAutopayModal(true)} className="btn btn-secondary btn-sm"
                                            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: ap?.enabled ? '#10b981' : '#374151', fontWeight: ap?.enabled ? 600 : 500 }}>
                                            {ap?.enabled ? <><IcClock size={14} />{`${String(ap.hour).padStart(2, '0')}:00 · ${fmt(ap.amount)} ₽`}</> : <><IcGear size={14} />Настроить</>}
                                        </button>
                                        <button onClick={() => setAutopayLogModal(true)} style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#6b7280', fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 4, padding: 0 }}>
                                            <IcHistory size={13} />История пополнений
                                        </button>
                                    </div>
                                    <div style={{ minWidth: 210, padding: '10px 14px', border: '1px solid #e5e7eb', borderRadius: 12, background: '#f9fafb', display: 'flex', flexDirection: 'column', gap: 6 }}>
                                        <div style={{ fontSize: 11, color: '#6b7280' }}>Пополнение бюджета</div>
                                        <div style={{ display: 'flex', gap: 6 }}>
                                            <input type="number" min={1000} step={100} inputMode="numeric" value={depositAmount} placeholder="Сумма, ₽"
                                                onChange={e => setDepositAmount(e.target.value)}
                                                disabled={depositing}
                                                style={{ width: 100, padding: '6px 8px', border: '1px solid #e5e7eb', borderRadius: 8, fontSize: 13, background: '#fff' }} />
                                            <select value={depositSource} onChange={e => setDepositSource(Number(e.target.value))} disabled={depositing}
                                                title="Откуда списать деньги"
                                                style={{ padding: '6px 8px', border: '1px solid #e5e7eb', borderRadius: 8, fontSize: 13, background: '#fff' }}>
                                                <option value={0}>Счёт</option>
                                                <option value={1}>Баланс</option>
                                            </select>
                                        </div>
                                        <button onClick={doDeposit} disabled={depositing || Number(depositAmount) < 1000} className="btn btn-primary btn-sm" style={{ fontSize: 13 }}>
                                            {depositing ? 'Пополняем…' : 'Пополнить'}
                                        </button>
                                        <div style={{ fontSize: 11, color: '#9ca3af' }}>Минимальный бюджет — 1000 ₽</div>
                                    </div>
                                </div>
                            </div>

                            {/* Товары кампании: фото + nm_id + артикул продавца */}
                            {campaign.nm_ids.length > 0 && (
                                <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid #eef0f2' }}>
                                    <div style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', marginBottom: 8 }}>
                                        ТОВАРЫ КАМПАНИИ · {campaign.nm_ids.length}
                                        {campaign.nm_ids.length > 1 && <span style={{ fontWeight: 500, textTransform: 'none' }}> · нажмите на товар, чтобы увидеть его воронку</span>}
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

                            {/* Зоны показов. Правила WB: CPM-единая — обе зоны всегда включены,
                                ставка одна на все зоны; CPC — только «Поиск», ставка за клик,
                                единая для всех фраз. Переключать зоны из интерфейса нельзя. */}
                            {zones && Object.keys(zones.placements).length > 0 && (
                                <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid #eef0f2' }}>
                                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
                                        <span style={{ fontSize: 11, fontWeight: 700, color: '#6b7280' }}>ЗОНЫ ПОКАЗОВ</span>
                                        <span style={{ fontSize: 11, color: '#9ca3af' }}>{zoneRuleText(zones)}</span>
                                    </div>
                                    <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                                        {ZONES.map(z => {
                                            const on = zones.placements[z.key] ?? false;
                                            const bid = zones.bids[z.key];
                                            const st = zoneStat(z.key);
                                            if (!on && bid == null) return null;
                                            return (
                                                <div key={z.key} style={{ minWidth: 236, padding: '10px 14px', borderRadius: 12, background: on ? '#fff' : '#f9fafb', border: `1px solid ${on ? '#a7f3d0' : '#e5e7eb'}`, opacity: on ? 1 : 0.65 }}>
                                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                                        <span style={{ width: 8, height: 8, borderRadius: '50%', background: on ? '#10b981' : '#d1d5db' }} />
                                                        <span style={{ fontSize: 13, fontWeight: 600, color: '#111827' }}>{z.label}</span>
                                                        <span style={{ fontSize: 11, color: on ? '#059669' : '#9ca3af' }}>{on ? 'работает' : 'не участвует'}</span>
                                                        {on && bid != null && (
                                                            <span style={{ marginLeft: 'auto', fontSize: 13, fontWeight: 700 }} title={zones.payment_type === 'cpc' ? 'Ставка за клик' : 'Ставка за 1000 показов'}>
                                                                {fmt(bid)} ₽<span style={{ fontSize: 10, fontWeight: 500, color: '#9ca3af' }}>{zones.payment_type === 'cpc' ? ' / клик' : ' / 1000'}</span>
                                                            </span>
                                                        )}
                                                    </div>
                                                    {on && st && (
                                                        <div style={{ display: 'flex', gap: 12, marginTop: 8, fontSize: 11, color: '#6b7280', flexWrap: 'wrap' }}>
                                                            <span>Показы <b style={{ color: '#111827' }}>{fmt(st.views)}</b></span>
                                                            <span>Клики <b style={{ color: '#111827' }}>{fmt(st.clicks)}</b></span>
                                                            <span>Затраты <b style={{ color: '#111827' }}>{fmt(st.spend)} ₽</b></span>
                                                            <span>Заказы <b style={{ color: '#111827' }}>{fmt(st.orders)}</b></span>
                                                        </div>
                                                    )}
                                                </div>
                                            );
                                        })}
                                    </div>
                                    {zones.payment_type !== 'cpc' && data?.zone_stats?.derived && (
                                        <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 6 }}>
                                            Разбивка по зонам — расчёт за период: поиск взят из поисковых кластеров, рекомендации — остаток от итога кампании. WB зоны в статистике не разделяет.
                                        </div>
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
                                        {([['table', 'По дням'], ['chart', 'График']] as const).map(([v, label]) => (
                                            <button key={v} onClick={e => { e.stopPropagation(); setMetricsView(v); }}
                                                style={{ padding: '3px 10px', fontSize: 12, fontWeight: 500, border: 'none', cursor: 'pointer', background: metricsView === v ? '#fff' : 'transparent', color: metricsView === v ? '#1d4ed8' : '#fff' }}>
                                                {label}
                                            </button>
                                        ))}
                                    </span>
                                )}
                            </div>
                            {isCpm && (
                                <button onClick={() => setTab('clusters')}
                                    style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 18px', fontSize: 13, fontWeight: 500, cursor: 'pointer', border: 'none', borderLeft: '1px solid #e5e7eb', background: tab === 'clusters' ? '#3b82f6' : '#fff', color: tab === 'clusters' ? '#fff' : '#374151' }}>
                                    <IcClusters />Кластеризатор
                                </button>
                            )}
                        </div>

                        {/* Выбранный товар — и метрики, и кластеры считаются по нему */}
                        {selectedNm != null && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', marginBottom: 8, background: '#ecfdf5', border: '1px solid #a7f3d0', borderRadius: 8, fontSize: 12, color: '#065f46' }}>
                                <b>{catalog[selectedNm]?.vendor_code || `#${selectedNm}`}</b>
                                <span style={{ color: '#059669' }}>#{selectedNm}</span>
                                <button onClick={() => setSelectedNm(null)} title="Показать всю кампанию"
                                    style={{ marginLeft: 'auto', border: 'none', background: 'none', cursor: 'pointer', color: '#065f46', fontSize: 12, padding: 0 }}>✕ вся кампания</button>
                            </div>
                        )}

                        {/* ─── Метрики по дням ─── */}
                        {tab === 'metrics' && (
                            <div className="glass-card static" style={{ padding: 0, overflow: 'hidden' }}>
                                {metricsLoading && <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Загрузка метрик…</div>}
                                {!metricsLoading && metricsError && <div style={{ padding: '16px 20px' }}><span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {metricsError === 'campaign_not_found' ? 'Кампания не найдена' : metricsError}</span></div>}
                                {!metricsLoading && !metricsError && metrics && (metrics.rows.length === 0
                                    ? <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>За выбранный период нет данных по кампании</div>
                                    : metricsView === 'chart' ? <CampaignMetricsChart rows={metrics.rows} selected={chartMetrics} onToggle={toggleChartMetric} /> : <CampaignMetricsTable resp={metrics} />)}
                            </div>
                        )}

                        {/* ─── Кластеризатор ─── */}
                        {tab === 'clusters' && isCpm && (
                            <div className="glass-card static" style={{ padding: 20 }}>
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
                                        />
                                    )
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
