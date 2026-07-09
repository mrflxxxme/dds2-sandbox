'use client';
import React, { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { exportToExcel, formatNumber } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import ProductHistoryChart, { CampaignHistoryChart } from './components/ProductHistoryChart';
import ClusterTable, { DailyBudgetBar, drrColor, clNum, clMoney, clMoneyN } from './components/ClusterTable';
import type { AdsManagerCampaign, AdsAutopaySetting, AdsAutopayLogEntry, AdsBudgetGap, AdTabProduct, FunnelFilters, FunnelSkuRow, CampaignClustersResponse, SearchCluster } from '@/types/api';

const fmt = (n: number | undefined) => (Number(n) || 0).toLocaleString('ru-RU', { maximumFractionDigits: 2 });
const num = (v: unknown) => Number(v) || 0;
const fmtPct = (n: number | undefined) => (Number(n) || 0).toFixed(2) + '%';
const iso = (d: Date) => d.toISOString().slice(0, 10);

type Tab = 'campaigns' | 'high-drr' | 'no-ads' | 'no-organic' | 'budget-gap';
type SortDir = 'asc' | 'desc';

const TABS: { key: Tab; label: string }[] = [
    { key: 'campaigns', label: '📋 Кампании' },
    { key: 'high-drr', label: '🔥 Высокий ДРР' },
    { key: 'no-ads', label: '💤 Не работает реклама' },
    { key: 'no-organic', label: '🚫 Нет органики' },
    { key: 'budget-gap', label: '⏳ Нехватка бюджета' },
];

const STATUS_BADGE: Record<number, string> = { 9: 'badge-success', 11: 'badge-warning', 7: 'badge-secondary' };

const thStyle: React.CSSProperties = { background: '#f9fafb', color: '#4b5563', fontSize: 11, textAlign: 'right', borderBottom: '2px solid #e5e7eb', padding: '8px 10px', whiteSpace: 'nowrap' };
const thLeft: React.CSSProperties = { ...thStyle, textAlign: 'left' };
const tdStyle: React.CSSProperties = { textAlign: 'right', borderBottom: '1px solid #f3f4f6', padding: '7px 10px', fontSize: 13 };
const tdLeft: React.CSSProperties = { ...tdStyle, textAlign: 'left' };

function mskTime(isoStr: string | null): string {
    if (!isoStr) return 'до первого синка';
    try {
        return new Date(isoStr).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' });
    } catch { return '—'; }
}

/** Ссылка на кампанию в кабинете WB (best-effort deep-link). */
function wbCampaignUrl(c: AdsManagerCampaign): string {
    const kind = c.campaign_type === 'cpc' ? 'auction' : 'auto';
    return `https://cmp.wildberries.ru/campaigns/list/all/edit/${kind}/${c.campaign_id}`;
}

/** Подпись типа рекламы (модель оплаты WB). Режим ставки CPM (единая/ручная)
 *  придёт из bid_mode, когда синк начнёт его сохранять. */
function adTypeLabel(c: AdsManagerCampaign): { text: string; hint: string; color: string } {
    const t = (c.campaign_type || '').toLowerCase();
    const mode = c.bid_mode === 'unified' ? ' · единая ставка' : c.bid_mode === 'manual' ? ' · ручная ставка' : '';
    if (t === 'cpm') return { text: `Реклама: CPM${mode}`, hint: 'CPM — оплата за 1000 показов', color: '#6366f1' };
    if (t === 'cpc') return { text: 'Реклама: CPC', hint: 'CPC — оплата за клик', color: '#0ea5e9' };
    return { text: `Реклама: ${c.campaign_type || '—'}`, hint: '', color: '#9ca3af' };
}

const DEFAULT_AUTOPAY: AdsAutopaySetting = { enabled: true, amount: 1000, hour: 9, threshold_pct: 50 };

/** Модалка настройки автопополнения одной кампании. */
function AutopayModal({ campaign, initial, onClose, onSave }: {
    campaign: AdsManagerCampaign;
    initial: AdsAutopaySetting | undefined;
    onClose: () => void;
    onSave: (s: AdsAutopaySetting) => Promise<void>;
}) {
    const [form, setForm] = useState<AdsAutopaySetting>(initial ?? DEFAULT_AUTOPAY);
    const [saving, setSaving] = useState(false);

    const handleSave = async () => {
        setSaving(true);
        try { await onSave(form); onClose(); }
        catch { setSaving(false); }
    };

    const inputStyle: React.CSSProperties = { background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '8px 10px', fontSize: 14, color: 'var(--color-text)', width: '100%' };

    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
            <div className="glass-card" style={{ width: 440, padding: 24, background: '#fff' }} onClick={e => e.stopPropagation()}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 4px' }}>⏰ Автопополнение бюджета</h3>
                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 16 }}>{campaign.name || `#${campaign.campaign_id}`} · #{campaign.campaign_id}</div>

                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, marginBottom: 14, cursor: 'pointer' }}>
                    <input type="checkbox" checked={form.enabled} onChange={e => setForm(f => ({ ...f, enabled: e.target.checked }))} />
                    Пополнять автоматически
                </label>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
                    <label style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                        Дневной бюджет X, ₽
                        <input type="number" min={0} step={50} value={form.amount}
                            onChange={e => setForm(f => ({ ...f, amount: Math.max(0, Number(e.target.value) || 0) }))} style={inputStyle} />
                    </label>
                    <label style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                        Час пополнения (МСК)
                        <select value={form.hour} onChange={e => setForm(f => ({ ...f, hour: Number(e.target.value) }))} style={inputStyle}>
                            {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
                        </select>
                    </label>
                </div>
                <label style={{ fontSize: 12, color: 'var(--color-text-dim)', display: 'block', marginBottom: 16 }}>
                    Порог открута за сутки, %
                    <input type="number" min={0} max={100} value={form.threshold_pct}
                        onChange={e => setForm(f => ({ ...f, threshold_pct: Math.min(100, Math.max(0, Number(e.target.value) || 0)) }))} style={inputStyle} />
                    <span style={{ fontSize: 11 }}>Если за сутки открутилось меньше {form.threshold_pct}% от {form.amount || 'X'} ₽ — пополнения не будет. Иначе бюджет доливается до {form.amount || 'X'} ₽.</span>
                </label>

                <div style={{ fontSize: 11, color: 'var(--color-warning, #f59e0b)', marginBottom: 16 }}>
                    ⚠️ Автопополнение активно: в заданный час бюджет доливается до X реальными деньгами
                    (минимум 1000₽ за пополнение — ограничение WB). Все операции — в журнале (📜 в таблице).
                </div>

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={saving}>Отмена</button>
                    <button className="btn btn-primary" onClick={handleSave} disabled={saving}>{saving ? 'Сохранение…' : 'Сохранить'}</button>
                </div>
            </div>
        </div>
    );
}

const AUTOPAY_STATUS_BADGE: Record<AdsAutopayLogEntry['status'], { label: string; cls: string }> = {
    ok: { label: 'Успешно', cls: 'badge-success' },
    error: { label: 'Ошибка', cls: 'badge-danger' },
    unknown: { label: 'Неизвестно', cls: 'badge-warning' },
};

/** Модалка истории автопополнений: по кампании или по всем сразу. */
function AutopayLogModal({ campaign, onClose }: {
    campaign: AdsManagerCampaign;
    onClose: () => void;
}) {
    const [entries, setEntries] = useState<AdsAutopayLogEntry[] | null>(null);
    const [error, setError] = useState('');
    const [allCampaigns, setAllCampaigns] = useState(false);

    useEffect(() => {
        const controller = new AbortController();
        setEntries(null);
        setError('');
        api.getAutopayLog(allCampaigns ? undefined : campaign.campaign_id)
            .then(data => { if (!controller.signal.aborted) setEntries(data); })
            .catch(() => { if (!controller.signal.aborted) setError('Не удалось загрузить журнал'); });
        return () => controller.abort();
    }, [campaign.campaign_id, allCampaigns]);

    const fmtTs = (ts: string) => {
        try {
            return new Date(ts).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' });
        } catch { return ts; }
    };

    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={onClose}>
            <div className="glass-card" style={{ width: 640, maxHeight: '80vh', overflow: 'auto', padding: 24, background: '#fff' }} onClick={e => e.stopPropagation()}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 4px' }}>📜 История пополнений</h3>
                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 12 }}>
                    {allCampaigns ? 'Все кампании' : <>{campaign.name || `#${campaign.campaign_id}`} · #{campaign.campaign_id}</>}
                </div>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, marginBottom: 12, cursor: 'pointer' }}>
                    <input type="checkbox" checked={allCampaigns} onChange={e => setAllCampaigns(e.target.checked)} />
                    Показать все кампании
                </label>

                {error && <div style={{ color: '#ef4444', fontSize: 13, padding: '12px 0' }}>{error}</div>}
                {!error && entries === null && <div style={{ color: '#9ca3af', fontSize: 13, padding: '12px 0' }}>Загрузка…</div>}
                {!error && entries !== null && entries.length === 0 && (
                    <div style={{ color: '#9ca3af', fontSize: 13, padding: '12px 0' }}>Пополнений ещё не было.</div>
                )}
                {!error && entries !== null && entries.length > 0 && (
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <thead><tr>
                            <th style={thLeft}>Кампания</th>
                            <th style={thLeft}>Дата (МСК)</th>
                            <th style={thStyle}>Сумма, ₽</th>
                            <th style={thLeft}>Источник</th>
                            <th style={thLeft}>Статус</th>
                            <th style={thStyle} title="Бюджет кампании после пополнения">Бюджет после</th>
                        </tr></thead>
                        <tbody>
                            {entries.map((e, i) => {
                                const badge = AUTOPAY_STATUS_BADGE[e.status] ?? AUTOPAY_STATUS_BADGE.unknown;
                                return (
                                    <tr key={`${e.campaign_id}-${e.ts}-${i}`} style={{ color: '#111827' }}>
                                        <td style={tdLeft}>#{e.campaign_id}</td>
                                        <td style={tdLeft}>{fmtTs(e.ts)}</td>
                                        <td style={{ ...tdStyle, fontWeight: 600 }}>{e.status === 'ok' ? fmt(e.amount) : fmt(e.requested)}</td>
                                        <td style={tdLeft}>{e.source || '—'}</td>
                                        <td style={tdLeft}>
                                            <span className={`badge ${badge.cls}`} title={e.reason || undefined}>{badge.label}</span>
                                        </td>
                                        <td style={tdStyle}>{e.budget_after != null ? fmt(e.budget_after) : '—'}</td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                )}

                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary" onClick={onClose}>Закрыть</button>
                </div>
            </div>
        </div>
    );
}

/** ДРР-цвет кампании: ≤8% зелёный, ≤12% янтарь, иначе красный. */
/** Модалка «Кластеры» одной CPM-кампании (Features 1 & 2). */
function ClustersModal({ campaign, onClose }: { campaign: AdsManagerCampaign; onClose: () => void }) {
    const [dateFrom, setDateFrom] = useState(iso(new Date(Date.now() - 29 * 86400_000)));
    const [dateTo, setDateTo] = useState(iso(new Date()));
    const [data, setData] = useState<CampaignClustersResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [reloadKey, setReloadKey] = useState(0);
    // Минус-фразы: набор norm_query, по которым идёт запрос, и текст последней ошибки
    const [pending, setPending] = useState<Set<string>>(new Set());
    const [minusError, setMinusError] = useState<string | null>(null);
    // Ставки: набор norm_query, по которым идёт запись, и текст последней ошибки
    const [bidPending, setBidPending] = useState<Set<string>>(new Set());
    const [bidError, setBidError] = useState<string | null>(null);

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true); setError(''); setData(null);
        api.getCampaignClusters(campaign.campaign_id, dateFrom, dateTo)
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
    }, [campaign.campaign_id, dateFrom, dateTo, reloadKey]);

    const handleToggleMinus = async (c: SearchCluster) => {
        const action: 'add' | 'remove' = c.is_minused ? 'remove' : 'add';
        const nmId = data?.nm_ids?.[0] ?? campaign.nm_ids?.[0];
        if (nmId == null) { setMinusError('Не удалось определить товар (nm_id) кампании'); return; }
        const confirmText = action === 'add'
            ? `Добавить «${c.norm_query}» в минус-фразы кампании в кабинете WB?`
            : `Вернуть «${c.norm_query}» — убрать из минус-фраз кампании в кабинете WB?`;
        if (!window.confirm(confirmText)) return;
        setMinusError(null);
        setPending(prev => new Set(prev).add(c.norm_query));
        try {
            const res = await api.toggleClusterMinus(campaign.campaign_id, { nm_id: nmId, norm_query: c.norm_query, action });
            if (!res.ok) { setMinusError(res.error || 'WB отклонил операцию'); return; }
            setData(prev => prev ? { ...prev, clusters: prev.clusters.map(x => x.norm_query === c.norm_query ? { ...x, is_minused: action === 'add' } : x) } : prev);
        } catch (e) {
            setMinusError(e instanceof Error ? e.message : 'Ошибка обращения к WB');
        } finally {
            setPending(prev => { const nx = new Set(prev); nx.delete(c.norm_query); return nx; });
        }
    };

    const handleSetBid = async (c: SearchCluster, bid: number) => {
        const nmId = data?.nm_ids?.[0] ?? campaign.nm_ids?.[0];
        if (nmId == null) { setBidError('Не удалось определить товар (nm_id) кампании'); return; }
        if (!window.confirm(`Поставить ставку ${formatNumber(bid, 0)} ₽ на кластер «${c.norm_query}» в кабинете WB?`)) return;
        setBidError(null);
        setBidPending(prev => new Set(prev).add(c.norm_query));
        try {
            const res = await api.setCampaignClusterBid(campaign.campaign_id, { nm_id: nmId, norm_query: c.norm_query, bid });
            if (!res.ok) { setBidError(res.error || 'WB отклонил ставку'); return; }
            setData(prev => prev ? { ...prev, clusters: prev.clusters.map(x => x.norm_query === c.norm_query ? { ...x, bid: res.bid ?? bid } : x) } : prev);
        } catch (e) {
            setBidError(e instanceof Error ? e.message : 'Ошибка обращения к WB');
        } finally {
            setBidPending(prev => { const nx = new Set(prev); nx.delete(c.norm_query); return nx; });
        }
    };

    const totals = data?.totals;
    const campDrr = data && num(data.aov) > 0 && num(totals?.orders) > 0 ? num(totals?.spend) / (num(totals?.orders) * num(data.aov)) * 100 : null;

    return (
        <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '32px 16px', overflow: 'auto' }} onClick={onClose}>
            <div className="glass-card" style={{ width: 'min(1080px, 96vw)', padding: 24, background: '#fff' }} onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 4 }}>
                    <div style={{ flex: 1 }}>
                        <h3 style={{ fontSize: 17, fontWeight: 700, margin: '0 0 4px' }}>🧩 Кластеры запросов — {campaign.name || `#${campaign.campaign_id}`}</h3>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>#{campaign.campaign_id}{data?.subject ? ` · ${data.subject}` : ''}</div>
                    </div>
                    <button className="btn btn-secondary btn-sm" onClick={onClose} style={{ fontSize: 13 }}>Закрыть</button>
                </div>

                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', margin: '12px 0' }}>
                    <input type="date" value={dateFrom} max={dateTo} onChange={e => setDateFrom(e.target.value)}
                        style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)' }} />
                    <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                    <input type="date" value={dateTo} min={dateFrom} onChange={e => setDateTo(e.target.value)}
                        style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)' }} />
                    <button className="btn btn-secondary btn-sm" style={{ fontSize: 12 }} onClick={() => { setDateFrom(iso(new Date(Date.now() - 29 * 86400_000))); setDateTo(iso(new Date())); }}>30 дней</button>
                    <button className="btn btn-secondary btn-sm" style={{ fontSize: 12 }} onClick={() => setReloadKey(k => k + 1)} disabled={loading}>🔄 Обновить</button>
                </div>

                {loading && <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Загрузка кластеров…</div>}
                {!loading && error && (
                    <div className="glass-card" style={{ padding: '16px 20px', border: '1px solid var(--color-danger)', background: '#fef2f2' }}>
                        <span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {error}</span>
                    </div>
                )}
                {!loading && !error && data && (
                    <>
                        {/* Тоталы + ДРР кампании */}
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: 16 }}>
                            {[
                                { label: 'Кластеров', value: clNum(totals?.clusters) },
                                { label: 'Показы', value: clNum(totals?.views) },
                                { label: 'Клики', value: clNum(totals?.clicks) },
                                { label: 'Заказы', value: clNum(totals?.orders) },
                                { label: 'Расход', value: clMoney(totals?.spend) },
                                { label: 'CPO', value: clMoneyN(totals?.cpo ?? null) },
                            ].map(m => (
                                <div key={m.label} style={{ minWidth: 92, padding: '8px 12px', border: '1px solid #e5e7eb', borderRadius: 12, background: '#f9fafb' }}>
                                    <div style={{ fontSize: 11, color: '#6b7280' }}>{m.label}</div>
                                    <div style={{ fontSize: 17, fontWeight: 700, color: '#111827' }}>{m.value}</div>
                                </div>
                            ))}
                            <div style={{ minWidth: 92, padding: '8px 12px', border: '1px solid #e5e7eb', borderRadius: 12, background: '#f9fafb' }}>
                                <div style={{ fontSize: 11, color: '#6b7280' }}>ДРР кампании</div>
                                <div style={{ fontSize: 17, fontWeight: 700, color: drrColor(campDrr, data.target_drr) }}>
                                    {campDrr == null ? '—' : `${formatNumber(campDrr, 1)}%`}
                                </div>
                            </div>
                            {data.aov > 0 && (
                                <div style={{ minWidth: 92, padding: '8px 12px', border: '1px solid #e5e7eb', borderRadius: 12, background: '#f9fafb' }}>
                                    <div style={{ fontSize: 11, color: '#6b7280' }}>AOV</div>
                                    <div style={{ fontSize: 17, fontWeight: 700, color: '#111827' }}>{clMoney(data.aov)}</div>
                                </div>
                            )}
                        </div>

                        {/* Feature 1 — распределение бюджета по дням */}
                        {data.daily && data.daily.length > 0 && (
                            <div style={{ marginBottom: 20 }}>
                                <div style={{ fontSize: 12, fontWeight: 600, color: '#374151', marginBottom: 6 }}>📊 Распределение расхода по дням</div>
                                <DailyBudgetBar daily={data.daily} />
                            </div>
                        )}

                        {/* Cluster table + Feature 2 (минус-фразы) */}
                        {data.clusters.length === 0 ? (
                            <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>За выбранный период у кампании нет данных по кластерам</div>
                        ) : (
                            <ClusterTable
                                clusters={data.clusters}
                                targetDrr={data.target_drr}
                                exportName={`clusters_${campaign.campaign_id}_${dateFrom}_${dateTo}`}
                                minus={{ pending, onToggle: handleToggleMinus, error: minusError }}
                                bids={{ pending: bidPending, onSetBid: handleSetBid, error: bidError }}
                            />
                        )}
                    </>
                )}
            </div>
        </div>
    );
}

export default function AdsManagerPage() {
    const routeParams = useParams();
    const slug = typeof routeParams?.slug === 'string' ? routeParams.slug : Array.isArray(routeParams?.slug) ? routeParams.slug[0] : '';
    const [clustersModal, setClustersModal] = useState<AdsManagerCampaign | null>(null);
    const [tab, setTab] = useState<Tab>('campaigns');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    // Фильтры как в воронке: бренд/категория (сервер) + поиск по артикулу (клиент)
    const [filters, setFilters] = useState<FunnelFilters | null>(null);
    const [brand, setBrand] = useState('');
    const [subject, setSubject] = useState('');
    const [search, setSearch] = useState('');
    // Фильтр по типу кампании (только вкладка «Кампании»): CPM/CPC, а для CPM — режим ставки
    const [campaignType, setCampaignType] = useState<'' | 'cpm' | 'cpc'>('');
    const [bidMode, setBidMode] = useState<'' | 'unified' | 'manual'>('');

    // Кампании
    const [campaigns, setCampaigns] = useState<AdsManagerCampaign[]>([]);
    const [campSort, setCampSort] = useState<{ field: keyof AdsManagerCampaign; dir: SortDir } | null>(null);
    const [syncing, setSyncing] = useState(false);
    const [autopay, setAutopay] = useState<Record<string, AdsAutopaySetting>>({});
    const [autopayModal, setAutopayModal] = useState<AdsManagerCampaign | null>(null);
    const [autopayLogModal, setAutopayLogModal] = useState<AdsManagerCampaign | null>(null);
    const [stateBusy, setStateBusy] = useState<number | null>(null);  // кампания в процессе смены статуса

    // Высокий ДРР / Не работает реклама — общий период
    const today = new Date();
    const weekAgo = new Date(Date.now() - 6 * 86400_000);
    const yesterday = new Date(Date.now() - 86400_000);
    const [dateFrom, setDateFrom] = useState(iso(weekAgo));
    const [dateTo, setDateTo] = useState(iso(today));
    const [drrThreshold, setDrrThreshold] = useState(7);
    const [drrRows, setDrrRows] = useState<AdTabProduct[]>([]);
    const [drrSort, setDrrSort] = useState<{ field: keyof AdTabProduct; dir: SortDir }>({ field: 'drr', dir: 'desc' });

    // Не работает реклама
    const [turnoverDays, setTurnoverDays] = useState(30);
    const [noAdsRows, setNoAdsRows] = useState<FunnelSkuRow[]>([]);

    // Нет органики — порог доли рекл. кликов (по анализу: 50% слабеет, 60% критично)
    const [organicRows, setOrganicRows] = useState<FunnelSkuRow[]>([]);
    const [organicThreshold, setOrganicThreshold] = useState(50);
    const [minOpensOrganic, setMinOpensOrganic] = useState(300);

    // Единый график сверху: выбранный артикул (nm_id) и выбранная кампания (campaign_id).
    // Клик по строке лишь меняет выбор — график один, он перерисовывается.
    const [selectedNm, setSelectedNm] = useState<number | null>(null);
    const [selectedCamp, setSelectedCamp] = useState<number | null>(null);

    // Нехватка бюджета
    const [gaps, setGaps] = useState<AdsBudgetGap[]>([]);

    const loadCampaigns = useCallback(async (df?: string, dt?: string) => {
        setLoading(true); setError('');
        try {
            const [list, ap] = await Promise.all([api.getAdCampaignsList(df, dt), api.getCampaignsAutopay().catch(() => ({}))]);
            setCampaigns(list);
            setAutopay(ap);
        }
        catch (e) { setError(e instanceof Error ? e.message : 'Ошибка загрузки'); }
        finally { setLoading(false); }
    }, []);

    const saveAutopay = useCallback(async (campaignId: number, s: AdsAutopaySetting) => {
        const res = await api.setCampaignAutopay(campaignId, s);
        setAutopay(res.settings);
        if (res.activation && !res.activation.ok) {
            setError(`Автопополнение сохранено, но кампанию не удалось активировать: ${res.activation.error || 'ошибка WB'}`);
        }
        // Включение автопополнения активирует кампанию — подтянем свежий статус в таблицу
        if (s.enabled) loadCampaigns(dateFrom, dateTo);
    }, [loadCampaigns, dateFrom, dateTo]);

    const toggleCampaignState = useCallback(async (c: AdsManagerCampaign) => {
        const active = c.status !== 9;  // не активна → запускаем, активна → пауза
        setStateBusy(c.campaign_id); setError('');
        try {
            await api.setCampaignState(c.campaign_id, active);
            await loadCampaigns(dateFrom, dateTo);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось изменить статус кампании');
        } finally { setStateBusy(null); }
    }, [loadCampaigns, dateFrom, dateTo]);

    const loadDrr = useCallback(async (df: string, dt: string, b = brand, s = subject) => {
        setLoading(true); setError('');
        try { setDrrRows(await api.getAdTab({ date_from: df, date_to: dt, brand: b || undefined, subject: s || undefined })); }
        catch (e) { setError(e instanceof Error ? e.message : 'Ошибка загрузки'); }
        finally { setLoading(false); }
    }, [brand, subject]);

    const loadNoAds = useCallback(async (df: string, dt: string, b = brand, s = subject) => {
        setLoading(true); setError('');
        try {
            const res = await api.getFunnelData({ date_from: df, date_to: dt, brand: b || undefined, subject: s || undefined, group_by: 'sku', extended: true });
            setNoAdsRows((res.data || []) as FunnelSkuRow[]);
        } catch (e) { setError(e instanceof Error ? e.message : 'Ошибка загрузки'); }
        finally { setLoading(false); }
    }, [brand, subject]);

    const loadNoOrganic = useCallback(async (df: string, dt: string, b = brand, s = subject) => {
        setLoading(true); setError('');
        try {
            const res = await api.getFunnelData({ date_from: df, date_to: dt, brand: b || undefined, subject: s || undefined, group_by: 'sku' });
            setOrganicRows((res.data || []) as FunnelSkuRow[]);
        } catch (e) { setError(e instanceof Error ? e.message : 'Ошибка загрузки'); }
        finally { setLoading(false); }
    }, [brand, subject]);

    const loadGaps = useCallback(async () => {
        setLoading(true); setError('');
        try { setGaps(await api.getAdsBudgetGaps()); }
        catch (e) { setError(e instanceof Error ? e.message : 'Ошибка загрузки'); }
        finally { setLoading(false); }
    }, []);

    useEffect(() => {
        api.getFunnelFilters().then(setFilters).catch(() => { /* фильтры не критичны */ });
    }, []);

    useEffect(() => {
        // Кампании/нехватка бюджета: бренд/категория фильтруются на клиенте — без перезагрузки
        if (tab === 'campaigns') loadCampaigns(dateFrom, dateTo);
        else if (tab === 'budget-gap') loadGaps();
    }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        // Товарные вкладки: бренд/категория — серверный фильтр, как в воронке
        if (tab === 'high-drr') loadDrr(dateFrom, dateTo);
        else if (tab === 'no-ads') loadNoAds(dateFrom, dateTo);
        else if (tab === 'no-organic') loadNoOrganic(dateFrom, dateTo);
    }, [tab, brand, subject]); // eslint-disable-line react-hooks/exhaustive-deps

    const applyPeriod = (df: string, dt: string) => {
        setDateFrom(df); setDateTo(dt);
        if (tab === 'campaigns') loadCampaigns(df, dt);
        if (tab === 'high-drr') loadDrr(df, dt);
        if (tab === 'no-ads') loadNoAds(df, dt);
        if (tab === 'no-organic') loadNoOrganic(df, dt);
    };

    const handleSync = async () => {
        setSyncing(true);
        try {
            await api.syncAdCampaigns();
            // Подождать завершения фоновой синхронизации и перезагрузить
            for (let i = 0; i < 60; i++) {
                await new Promise(r => setTimeout(r, 2000));
                const p = await api.getSyncCampaignsProgress();
                if (p.status === 'done' || p.status === 'error' || p.status === 'idle') break;
            }
            await loadCampaigns();
        } catch (e) { setError(e instanceof Error ? e.message : 'Ошибка синхронизации'); }
        finally { setSyncing(false); }
    };

    // Поиск по артикулу: vendor_code / nm_id; для кампаний — ещё название и ID кампании
    const q = search.trim().toLowerCase();
    const matchProduct = (vendorCode: string | null | undefined, nmId: number | undefined) =>
        !q || (vendorCode || '').toLowerCase().includes(q) || String(nmId || '').includes(q);
    const matchCampaign = (name: string | null, campaignId: number, nmIds: number[]) =>
        !q || (name || '').toLowerCase().includes(q) || String(campaignId).includes(q) || nmIds.some(id => String(id).includes(q));

    // Кампании: бренд/категория — по товарам кампании (клиентский фильтр)
    const visibleCampaigns = campaigns
        .filter(c => matchCampaign(c.name, c.campaign_id, c.nm_ids))
        .filter(c => !brand || c.brands.includes(brand))
        .filter(c => !subject || c.subjects.includes(subject))
        .filter(c => !campaignType || (c.campaign_type || '').toLowerCase() === campaignType)
        .filter(c => !bidMode || (c.bid_mode || '') === bidMode)
        .sort((a, b) => {
            if (!campSort) return 0; // без сортировки — порядок бэка (активные выше)
            const av = Number(a[campSort.field]) || 0;
            const bv = Number(b[campSort.field]) || 0;
            return campSort.dir === 'asc' ? av - bv : bv - av;
        });
    const campFiltered = !!(q || brand || subject || campaignType || bidMode);
    const toggleCampSort = (field: keyof AdsManagerCampaign) =>
        setCampSort(prev => ({ field, dir: prev?.field === field && prev.dir === 'desc' ? 'asc' : 'desc' }));
    const campArrow = (field: keyof AdsManagerCampaign) => campSort?.field === field ? (campSort.dir === 'desc' ? ' ↓' : ' ↑') : '';
    // bid_mode начнёт приходить после доработки синка — до этого фильтр ставки задизейблен
    const bidModeAvailable = campaigns.some(c => !!c.bid_mode);
    const visibleGaps = gaps
        .filter(g => matchCampaign(g.name, g.campaign_id, g.nm_ids))
        .filter(g => !brand || g.brands.includes(brand))
        .filter(g => !subject || g.subjects.includes(subject));

    // Высокий ДРР: фильтр + сортировка
    const highDrr = drrRows
        .filter(r => (Number(r.drr) || 0) > drrThreshold)
        .filter(r => matchProduct(r.vendor_code, r.nm_id))
        .sort((a, b) => {
            const av = Number(a[drrSort.field]) || 0;
            const bv = Number(b[drrSort.field]) || 0;
            return drrSort.dir === 'asc' ? av - bv : bv - av;
        });
    const toggleDrrSort = (field: keyof AdTabProduct) =>
        setDrrSort(prev => ({ field, dir: prev.field === field && prev.dir === 'desc' ? 'asc' : 'desc' }));
    const sortArrow = (field: keyof AdTabProduct) => drrSort.field === field ? (drrSort.dir === 'desc' ? ' ↓' : ' ↑') : '';

    // Не работает реклама: запас ПО WB-остатку > N дней (нет продаж = 999+), реклама за период = 0.
    // Считаем по остатку на WB (то, что реально лежит на площадке), а не по общему.
    const noAds = noAdsRows.filter(r =>
        (Number(r.adv_sum) || 0) === 0 && r.wb_stock_days_left != null && Number(r.wb_stock_days_left) > turnoverDays
        && (Number(r.wb_stock_qty) || 0) > 0
        && matchProduct(r.vendor_code, r.nm_id),
    ).sort((a, b) => (Number(b.wb_stock_cost) || 0) - (Number(a.wb_stock_cost) || 0));

    // Нет органики: доля рекл. кликов (adv_clicks / переходы) ≥ порога, с ощутимым трафиком
    const adShare = (r: FunnelSkuRow) => {
        const opens = Number(r.open_card) || 0;
        return opens > 0 ? (Number(r.adv_clicks) || 0) / opens * 100 : 0;
    };
    const noOrganic = organicRows
        .filter(r => (Number(r.open_card) || 0) >= minOpensOrganic && adShare(r) >= organicThreshold && matchProduct(r.vendor_code, r.nm_id))
        .sort((a, b) => adShare(b) - adShare(a));

    // Excel-экспорт: выгружаем то, что видно (с учётом фильтров и сортировки)
    const exportCampaigns = () => exportToExcel(visibleCampaigns.map(c => ({
        'Кампания': c.name || `#${c.campaign_id}`, 'ID': c.campaign_id,
        'Тип': (c.campaign_type || '').toUpperCase() + (c.bid_mode === 'unified' ? ' · единая' : c.bid_mode === 'manual' ? ' · ручная' : ''),
        'Статус': c.status_label, 'Остаток бюджета ₽': num(c.budget), 'Расход сегодня ₽': num(c.spend_today),
        'Расход за период ₽': num(c.spend_period), 'Клики': num(c.clicks_period), 'CTR %': num(c.ctr), 'CPC ₽': num(c.cpc),
        'Рекл. клики %': num(c.ad_click_share), 'ДРР %': num(c.drr), 'Маржа %': num(c.margin),
        'Конв. корзина %': num(c.cr_cart), 'Конв. заказ %': num(c.cr_order), 'Товаров': c.nm_count, 'Бренды': c.brands.join(', '),
    })), `ads-campaigns_${dateFrom}_${dateTo}`);
    const exportHighDrr = () => exportToExcel(highDrr.map(r => ({
        'Артикул': r.vendor_code || '', 'nm_id': r.nm_id, 'Бренд': r.brand || '', 'Категория': r.subject || '',
        'ДРР %': num(r.drr), 'Расход ₽': num(r.adv_sum), 'Заказы ₽': num(r.orders_sum_rub),
        'CTR %': num(r.ctr), 'CPC ₽': num(r.cpc), 'Кампаний': num(r.active_campaigns),
    })), `ads-high-drr_${dateFrom}_${dateTo}`);
    const exportNoAds = () => exportToExcel(noAds.map(r => ({
        'Артикул': r.vendor_code || '', 'nm_id': r.nm_id, 'Бренд': r.brand || '', 'Категория': r.subject || '',
        'Остаток WB, шт': num(r.wb_stock_qty), 'Наши склады, шт': num(r.own_stock_qty),
        'Себест. остатков ₽': num(r.wb_stock_cost) + num(r.own_stock_cost),
        'Хватит на WB, дн': num(r.wb_stock_days_left), 'Хватит общий, дн': num(r.stock_days_left),
        'Заказы за период ₽': num(r.orders_sum_rub),
    })), `ads-no-ads_${dateFrom}_${dateTo}`);
    const exportNoOrganic = () => exportToExcel(noOrganic.map(r => ({
        'Артикул': r.vendor_code || '', 'nm_id': r.nm_id, 'Бренд': r.brand || '', 'Категория': r.subject || '',
        'Рекл. клики %': Math.round(adShare(r) * 100) / 100, 'Переходы': num(r.open_card), 'Рекл. клики': num(r.adv_clicks),
        'Заказы ₽': num(r.orders_sum_rub), 'Расход рекл. ₽': num(r.adv_sum), 'ДРР %': num(r.drr), 'В заказ %': num(r.cart_to_order_pct),
    })), `ads-no-organic_${dateFrom}_${dateTo}`);
    const exportGaps = () => exportToExcel(visibleGaps.map(g => ({
        'Кампания': g.name || `#${g.campaign_id}`, 'ID': g.campaign_id, 'Тип': (g.campaign_type || '').toUpperCase(),
        'Потрачено сегодня ₽': num(g.spend_today), 'Остановилась в (МСК)': mskTime(g.ran_out_at),
        'Скорость ₽/час': num(g.burn_rate), 'Долить до 00:00 ₽': num(g.needed_till_midnight), 'Товаров': g.nm_count,
    })), 'ads-budget-gap');
    const excelBtn = (onClick: () => void, disabled: boolean) => (
        <button className="btn btn-secondary btn-sm" style={{ fontSize: 13 }} onClick={onClick} disabled={disabled} title="Выгрузить таблицу в Excel (с учётом фильтров)">
            📥 Excel
        </button>
    );

    // Список nm_id текущей товарной вкладки — для авто-выбора первой строки в график
    const currentNmRows = tab === 'high-drr' ? highDrr : tab === 'no-ads' ? noAds : tab === 'no-organic' ? noOrganic : [];
    const currentNmIds = currentNmRows.map(r => r.nm_id);
    const campIds = visibleCampaigns.map(c => c.campaign_id);

    // Если выбранного нет в текущем списке (сменили вкладку/фильтр/период) — берём первую строку
    useEffect(() => {
        if (currentNmIds.length && (selectedNm == null || !currentNmIds.includes(selectedNm))) setSelectedNm(currentNmIds[0]);
    }, [currentNmIds.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps
    useEffect(() => {
        if (tab === 'campaigns' && campIds.length && (selectedCamp == null || !campIds.includes(selectedCamp))) setSelectedCamp(campIds[0]);
    }, [campIds.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

    const selectedNmLabel = (() => {
        const r = currentNmRows.find(x => x.nm_id === selectedNm);
        return r ? `${r.vendor_code || r.nm_id} · ${r.brand || ''}${r.subject ? ' · ' + r.subject : ''} · #${r.nm_id}` : undefined;
    })();
    const selectedCampObj = visibleCampaigns.find(c => c.campaign_id === selectedCamp);
    const selectedCampLabel = selectedCampObj ? `${selectedCampObj.name || `#${selectedCampObj.campaign_id}`} · #${selectedCampObj.campaign_id}` : undefined;

    const periodControls = (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <input type="date" value={dateFrom} onChange={e => applyPeriod(e.target.value, dateTo)}
                style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)' }} />
            <span style={{ color: 'var(--color-text-dim)' }}>—</span>
            <input type="date" value={dateTo} onChange={e => applyPeriod(dateFrom, e.target.value)}
                style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)' }} />
            <button className="btn btn-secondary btn-sm" style={{ fontSize: 13 }} onClick={() => applyPeriod(iso(yesterday), iso(yesterday))}>Вчера</button>
            <button className="btn btn-secondary btn-sm" style={{ fontSize: 13 }} onClick={() => applyPeriod(iso(weekAgo), iso(today))}>Последние 7 дней</button>
        </div>
    );

    return (
        <PageGuard page="ads-manager">
            <div className="animate-in">
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                    <div>
                        <h1 style={{ fontSize: 28, fontWeight: 700, margin: '0 0 4px' }}>📢 Управление рекламой</h1>
                        <p style={{ fontSize: 13, color: 'var(--color-text-dim)', margin: '0 0 16px' }}>
                            Кампании WB: бюджеты, проблемный ДРР, товары без рекламы, нехватка дневного бюджета.
                        </p>
                    </div>
                    <Link href={`/p/${slug}/ads-manager/categories`} className="btn btn-secondary btn-sm" style={{ fontSize: 13, whiteSpace: 'nowrap' }}>
                        🧩 Кластеры по категории →
                    </Link>
                </div>

                {/* Вкладки */}
                <div style={{ display: 'flex', gap: 0, border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden', width: 'fit-content', marginBottom: 12 }}>
                    {TABS.map((t, idx) => (
                        <button key={t.key} onClick={() => setTab(t.key)}
                            style={{
                                padding: '8px 18px', fontSize: 13, fontWeight: 500, cursor: 'pointer', border: 'none',
                                borderLeft: idx > 0 ? '1px solid #e5e7eb' : 'none',
                                background: tab === t.key ? '#3b82f6' : '#fff', color: tab === t.key ? '#fff' : '#374151',
                            }}>{t.label}</button>
                    ))}
                </div>

                {/* Фильтры (как в воронке): артикул — везде; бренд/категория — там, где данные по товарам */}
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
                    <input placeholder="🔍 Артикул..." value={search} onChange={e => setSearch(e.target.value)}
                        style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', color: 'var(--color-text)', fontSize: 13, width: 170 }} />
                    {(tab === 'campaigns' || tab === 'high-drr' || tab === 'no-ads' || tab === 'no-organic' || tab === 'budget-gap') && (
                        <>
                            <select value={brand} onChange={e => setBrand(e.target.value)}
                                style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)' }}>
                                <option value="">Все бренды</option>
                                {filters?.brands?.map(b => <option key={b} value={b}>{b}</option>)}
                            </select>
                            <select value={subject} onChange={e => setSubject(e.target.value)}
                                style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)', maxWidth: 260 }}>
                                <option value="">Все категории</option>
                                {filters?.subjects?.map(s => <option key={s} value={s}>{s}</option>)}
                            </select>
                        </>
                    )}
                    {/* Тип кампании (только «Кампании»): сначала CPM/CPC, для CPM — режим ставки */}
                    {tab === 'campaigns' && (
                        <>
                            <select value={campaignType}
                                onChange={e => { const v = e.target.value as '' | 'cpm' | 'cpc'; setCampaignType(v); if (v !== 'cpm') setBidMode(''); }}
                                title="Тип оплаты рекламы WB: CPM — за 1000 показов, CPC — за клик"
                                style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)' }}>
                                <option value="">Все типы рекламы</option>
                                <option value="cpm">CPM · за показы</option>
                                <option value="cpc">CPC · за клик</option>
                            </select>
                            {campaignType === 'cpm' && (
                                // Режим ставки задизейблен, пока синк не начнёт отдавать bid_mode — включится сам
                                <select value={bidMode} onChange={e => setBidMode(e.target.value as '' | 'unified' | 'manual')}
                                    disabled={!bidModeAvailable}
                                    title={bidModeAvailable ? 'Режим ставки CPM: единая или ручная' : 'Фильтр по режиму ставки появится после обновления синхронизации WB'}
                                    style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)', opacity: bidModeAvailable ? 1 : 0.55, cursor: bidModeAvailable ? 'pointer' : 'not-allowed' }}>
                                    {bidModeAvailable ? (
                                        <>
                                            <option value="">Любая ставка</option>
                                            <option value="unified">Единая ставка</option>
                                            <option value="manual">Ручная ставка</option>
                                        </>
                                    ) : (
                                        <option value="">Единая/ручная · скоро</option>
                                    )}
                                </select>
                            )}
                        </>
                    )}
                    {(brand || subject || search || campaignType || bidMode) && (
                        <button className="btn btn-secondary btn-sm" style={{ fontSize: 12 }}
                            onClick={() => { setBrand(''); setSubject(''); setSearch(''); setCampaignType(''); setBidMode(''); }}>✕ Сбросить</button>
                    )}
                </div>

                {error && (
                    <div className="glass-card" style={{ padding: '12px 20px', marginBottom: 12, border: '1px solid var(--color-danger)' }}>
                        <span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {error}</span>
                    </div>
                )}

                {/* ─── Кампании ─── */}
                {tab === 'campaigns' && (
                  <>
                    {selectedCamp != null && (
                        <div className="glass-card" style={{ padding: 0, overflow: 'hidden', marginBottom: 12 }}>
                            <CampaignHistoryChart campaignId={selectedCamp} dateFrom={dateFrom} dateTo={dateTo} title={selectedCampLabel} />
                        </div>
                    )}
                    <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                        <div style={{ padding: '10px 16px', display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap', borderBottom: '1px solid #e5e7eb', background: '#f9fafb' }}>
                            {periodControls}
                            <button onClick={handleSync} disabled={syncing} className="btn btn-secondary btn-sm" style={{ fontSize: 13 }}>
                                {syncing ? 'Синхронизация…' : '🔄 Синхронизировать с WB'}
                            </button>
                            {excelBtn(exportCampaigns, visibleCampaigns.length === 0)}
                            <span style={{ fontSize: 12, color: 'var(--color-text-dim)', marginLeft: 'auto' }}>
                                {campFiltered ? `Найдено: ${visibleCampaigns.length} из ${campaigns.length}` : `Всего: ${campaigns.length} · Активных: ${campaigns.filter(c => c.status === 9).length}`}
                            </span>
                        </div>
                        <div style={{ overflowX: 'auto' }}>
                            {loading ? <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div> :
                                visibleCampaigns.length === 0 ? <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>{campFiltered ? 'Ничего не найдено по заданным фильтрам' : 'Кампаний нет — нажмите «Синхронизировать с WB»'}</div> : (
                                    <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                                        <thead><tr>
                                            <th style={thLeft}>Кампания</th>
                                            <th style={thLeft}>Статус</th>
                                            <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleCampSort('budget')}>Остаток бюджета ₽{campArrow('budget')}</th>
                                            <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleCampSort('spend_today')}>Расход сегодня ₽{campArrow('spend_today')}</th>
                                            <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleCampSort('spend_period')}>Расход за период ₽{campArrow('spend_period')}</th>
                                            <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleCampSort('clicks_period')}>Клики{campArrow('clicks_period')}</th>
                                            <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleCampSort('ctr')}>CTR{campArrow('ctr')}</th>
                                            <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleCampSort('cpc')} title="CPC за период: расход кампании / клики">CPC ₽{campArrow('cpc')}</th>
                                            <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleCampSort('ad_click_share')} title="Доля рекламных кликов от всех переходов товаров кампании. ≥50% — органика слабеет, ≥60% — критично">Рекл. клики %{campArrow('ad_click_share')}</th>
                                            <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleCampSort('drr')} title="ДРР за период: расход кампании / сумма заказов её товаров (из воронки)">ДРР{campArrow('drr')}</th>
                                            <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleCampSort('margin')} title="Маржа за период по товарам кампании (прибыль / выручка, из воронки)">Маржа{campArrow('margin')}</th>
                                            <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleCampSort('cr_cart')} title="Конверсия переход→корзина по товарам кампании">Конв. корзина{campArrow('cr_cart')}</th>
                                            <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleCampSort('cr_order')} title="Конверсия корзина→заказ по товарам кампании">Конв. заказ{campArrow('cr_order')}</th>
                                            <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleCampSort('nm_count')}>Товаров{campArrow('nm_count')}</th>
                                            <th style={{ ...thStyle, textAlign: 'center' }}>Автопополнение</th>
                                            <th style={{ ...thStyle, textAlign: 'center' }}>WB</th>
                                        </tr></thead>
                                        <tbody>
                                            {visibleCampaigns.map(c => {
                                                const ap = autopay[String(c.campaign_id)];
                                                return (
                                                <tr key={c.campaign_id} style={{ color: '#111827', cursor: 'pointer', background: selectedCamp === c.campaign_id ? '#eff6ff' : undefined }} onClick={() => setSelectedCamp(c.campaign_id)}>
                                                    <td style={tdLeft}>
                                                        <div style={{ fontWeight: 600, fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
                                                            <span style={{ fontSize: 11, color: selectedCamp === c.campaign_id ? '#3b82f6' : '#d1d5db', width: 12 }}>{selectedCamp === c.campaign_id ? '●' : '○'}</span>
                                                            {c.name || `#${c.campaign_id}`}
                                                        </div>
                                                        <div style={{ fontSize: 11, color: '#9ca3af', paddingLeft: 18, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                                                            <span>#{c.campaign_id}</span>
                                                            {(() => { const a = adTypeLabel(c); return <span title={a.hint} style={{ color: a.color, fontWeight: 600 }}>{a.text}</span>; })()}
                                                            {c.brands.length > 0 && <span>· {c.brands.join(', ')}</span>}
                                                        </div>
                                                    </td>
                                                    <td style={tdLeft}>
                                                        <span className={`badge ${STATUS_BADGE[c.status] || 'badge-secondary'}`}>{c.status_label}</span>
                                                        {(c.status === 9 || c.status === 11) && (
                                                            <button onClick={e => { e.stopPropagation(); toggleCampaignState(c); }} disabled={stateBusy === c.campaign_id}
                                                                title={c.status === 9 ? 'Поставить на паузу' : 'Запустить кампанию'}
                                                                style={{ marginLeft: 8, border: 'none', background: 'none', cursor: 'pointer', fontSize: 13, color: c.status === 9 ? '#f59e0b' : '#10b981' }}>
                                                                {stateBusy === c.campaign_id ? '…' : c.status === 9 ? '⏸' : '▶'}
                                                            </button>
                                                        )}
                                                    </td>
                                                    <td style={{ ...tdStyle, fontWeight: 600, color: c.budget <= 0 && c.status === 9 ? '#ef4444' : '#111827' }}>{fmt(c.budget)}</td>
                                                    <td style={tdStyle}>{fmt(c.spend_today)}</td>
                                                    <td style={tdStyle}>{fmt(c.spend_period)}</td>
                                                    <td style={tdStyle}>{fmt(c.clicks_period)}</td>
                                                    <td style={tdStyle}>{fmtPct(c.ctr)}</td>
                                                    <td style={tdStyle}>{c.cpc > 0 ? fmt(c.cpc) : '—'}</td>
                                                    <td style={{ ...tdStyle, fontWeight: 600, color: c.ad_click_share >= 60 ? '#ef4444' : c.ad_click_share >= 50 ? '#f59e0b' : c.ad_click_share > 0 ? '#374151' : '#9ca3af' }}>{c.ad_click_share > 0 ? fmtPct(c.ad_click_share) : '—'}</td>
                                                    <td style={{ ...tdStyle, fontWeight: 600, color: c.drr > 30 ? '#ef4444' : c.drr > 7 ? '#f59e0b' : c.drr > 0 ? '#10b981' : '#9ca3af' }}>{c.drr > 0 ? fmtPct(c.drr) : '—'}</td>
                                                    <td style={{ ...tdStyle, fontWeight: 600, color: c.margin > 20 ? '#10b981' : c.margin > 0 ? '#65a30d' : c.margin < 0 ? '#ef4444' : '#9ca3af' }}>{c.margin !== 0 ? fmtPct(c.margin) : '—'}</td>
                                                    <td style={tdStyle}>{c.cr_cart > 0 ? fmtPct(c.cr_cart) : '—'}</td>
                                                    <td style={tdStyle}>{c.cr_order > 0 ? fmtPct(c.cr_order) : '—'}</td>
                                                    <td style={tdStyle}>{c.nm_count}</td>
                                                    <td style={{ ...tdStyle, textAlign: 'center', whiteSpace: 'nowrap' }}>
                                                        <button onClick={e => { e.stopPropagation(); setAutopayModal(c); }} title="Настроить автопополнение"
                                                            style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, color: ap?.enabled ? '#10b981' : '#9ca3af', fontWeight: ap?.enabled ? 600 : 400, whiteSpace: 'nowrap' }}>
                                                            {ap?.enabled ? `⏰ ${String(ap.hour).padStart(2, '0')}:00 · ${fmt(ap.amount)}₽` : '⚙ настроить'}
                                                        </button>
                                                        <button onClick={e => { e.stopPropagation(); setAutopayLogModal(c); }} title="История пополнений"
                                                            style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 13, color: '#6b7280', padding: '0 2px' }}>
                                                            📜
                                                        </button>
                                                    </td>
                                                    <td style={{ ...tdStyle, textAlign: 'center', whiteSpace: 'nowrap' }}>
                                                        {(c.campaign_type || '').toLowerCase() === 'cpm' && (
                                                            <button onClick={e => { e.stopPropagation(); setClustersModal(c); }} title="Кластеризатор поисковых запросов кампании"
                                                                className="btn btn-secondary btn-sm" style={{ fontSize: 12, marginRight: 6 }}>🧩 Кластеры</button>
                                                        )}
                                                        <a href={wbCampaignUrl(c)} target="_blank" rel="noreferrer" title="Открыть кампанию в кабинете WB" onClick={e => e.stopPropagation()}
                                                            style={{ color: 'var(--color-accent)', textDecoration: 'none', fontSize: 15 }}>↗</a>
                                                    </td>
                                                </tr>
                                            ); })}
                                        </tbody>
                                    </table>
                                )}
                        </div>
                    </div>
                  </>
                )}

                {/* ─── Высокий ДРР ─── */}
                {tab === 'high-drr' && (
                  <>
                    {selectedNm != null && (
                        <div className="glass-card" style={{ padding: 0, overflow: 'hidden', marginBottom: 12 }}>
                            <ProductHistoryChart nmId={selectedNm} dateFrom={dateFrom} dateTo={dateTo} title={selectedNmLabel} />
                        </div>
                    )}
                    <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                        <div style={{ padding: '10px 16px', display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap', borderBottom: '1px solid #e5e7eb', background: '#f9fafb' }}>
                            {periodControls}
                            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text-dim)' }}>
                                ДРР выше
                                <input type="number" min={0} step={0.5} value={drrThreshold}
                                    onChange={e => setDrrThreshold(Number(e.target.value) || 0)}
                                    style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 8px', fontSize: 13, color: 'var(--color-text)', width: 64 }} />%
                            </label>
                            {excelBtn(exportHighDrr, highDrr.length === 0)}
                            <span style={{ fontSize: 12, color: 'var(--color-text-dim)', marginLeft: 'auto' }}>Найдено: {highDrr.length}</span>
                        </div>
                        <div style={{ overflowX: 'auto' }}>
                            {loading ? <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div> :
                                highDrr.length === 0 ? <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>Нет товаров с ДРР выше {drrThreshold}% за период 🎉</div> : (
                                    <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                                        <thead><tr>
                                            <th style={thLeft}>Товар</th>
                                            <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleDrrSort('drr')}>ДРР{sortArrow('drr')}</th>
                                            <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleDrrSort('adv_sum')}>Расход ₽{sortArrow('adv_sum')}</th>
                                            <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleDrrSort('orders_sum_rub')}>Заказы ₽{sortArrow('orders_sum_rub')}</th>
                                            <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleDrrSort('ctr')}>CTR{sortArrow('ctr')}</th>
                                            <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleDrrSort('cpc')}>CPC{sortArrow('cpc')}</th>
                                            <th style={thStyle}>Кампаний</th>
                                        </tr></thead>
                                        <tbody>
                                            {highDrr.map(r => (
                                                <tr key={r.nm_id} style={{ color: '#111827', cursor: 'pointer', background: selectedNm === r.nm_id ? '#eff6ff' : undefined }} onClick={() => setSelectedNm(r.nm_id)}>
                                                    <td style={tdLeft}>
                                                        <div style={{ fontWeight: 600, fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
                                                            <span style={{ fontSize: 11, color: selectedNm === r.nm_id ? '#3b82f6' : '#d1d5db', width: 12 }}>{selectedNm === r.nm_id ? '●' : '○'}</span>
                                                            {r.vendor_code || r.nm_id}
                                                        </div>
                                                        <div style={{ fontSize: 11, color: '#9ca3af', paddingLeft: 18 }}>{r.brand} · {r.subject} · <a href={`https://www.wildberries.ru/catalog/${r.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }} onClick={e => e.stopPropagation()}>#{r.nm_id}</a></div>
                                                    </td>
                                                    <td style={{ ...tdStyle, fontWeight: 700, color: (Number(r.drr) || 0) > 30 ? '#ef4444' : '#f59e0b' }}>{fmtPct(r.drr)}</td>
                                                    <td style={{ ...tdStyle, color: '#f97316' }}>{fmt(r.adv_sum)}</td>
                                                    <td style={tdStyle}>{fmt(r.orders_sum_rub)}</td>
                                                    <td style={tdStyle}>{fmtPct(r.ctr)}</td>
                                                    <td style={tdStyle}>{fmt(r.cpc)}</td>
                                                    <td style={tdStyle}>{r.active_campaigns}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                )}
                        </div>
                    </div>
                  </>
                )}

                {/* ─── Не работает реклама ─── */}
                {tab === 'no-ads' && (
                  <>
                    {selectedNm != null && (
                        <div className="glass-card" style={{ padding: 0, overflow: 'hidden', marginBottom: 12 }}>
                            <ProductHistoryChart nmId={selectedNm} dateFrom={dateFrom} dateTo={dateTo} title={selectedNmLabel} />
                        </div>
                    )}
                    <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                        <div style={{ padding: '10px 16px', display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap', borderBottom: '1px solid #e5e7eb', background: '#f9fafb' }}>
                            {periodControls}
                            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text-dim)' }}
                                title="Товар попадает в список, если остатка хватит больше чем на N дней при текущем темпе продаж, а расход рекламы за период = 0">
                                Запас более
                                <input type="number" min={1} value={turnoverDays}
                                    onChange={e => setTurnoverDays(Math.max(1, Number(e.target.value) || 30))}
                                    style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 8px', fontSize: 13, color: 'var(--color-text)', width: 64 }} /> дн
                            </label>
                            {excelBtn(exportNoAds, noAds.length === 0)}
                            <span style={{ fontSize: 12, color: 'var(--color-text-dim)', marginLeft: 'auto' }}>Найдено: {noAds.length}</span>
                        </div>
                        <div style={{ overflowX: 'auto' }}>
                            {loading ? <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div> :
                                noAds.length === 0 ? <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>Все залежавшиеся товары под рекламой 🎉</div> : (
                                    <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                                        <thead><tr>
                                            <th style={thLeft}>Товар</th>
                                            <th style={thStyle}>Остаток WB, шт</th>
                                            <th style={thStyle}>Наши склады, шт</th>
                                            <th style={thStyle}>Себест. остатков ₽</th>
                                            <th style={thStyle} title="Запас в днях по остатку на WB (без наших складов). Внизу — общий, с учётом наших складов.">Хватит на WB, дн</th>
                                            <th style={thStyle}>Заказы за период ₽</th>
                                            <th style={thStyle}>Расход рекл. ₽</th>
                                        </tr></thead>
                                        <tbody>
                                            {noAds.map(r => (
                                                <tr key={r.nm_id} style={{ color: '#111827', cursor: 'pointer', background: selectedNm === r.nm_id ? '#eff6ff' : undefined }} onClick={() => setSelectedNm(r.nm_id)}>
                                                    <td style={tdLeft}>
                                                        <div style={{ fontWeight: 600, fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
                                                            <span style={{ fontSize: 11, color: selectedNm === r.nm_id ? '#3b82f6' : '#d1d5db', width: 12 }}>{selectedNm === r.nm_id ? '●' : '○'}</span>
                                                            {r.vendor_code || r.nm_id}
                                                        </div>
                                                        <div style={{ fontSize: 11, color: '#9ca3af', paddingLeft: 18 }}>{r.brand} · {r.subject} · <a href={`https://www.wildberries.ru/catalog/${r.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }} onClick={e => e.stopPropagation()}>#{r.nm_id}</a></div>
                                                    </td>
                                                    <td style={tdStyle}>{fmt(r.wb_stock_qty)}</td>
                                                    <td style={tdStyle}>{fmt(r.own_stock_qty)}</td>
                                                    <td style={{ ...tdStyle, fontWeight: 600 }}>{fmt((Number(r.wb_stock_cost) || 0) + (Number(r.own_stock_cost) || 0))}</td>
                                                    <td style={{ ...tdStyle, fontWeight: 600, color: '#ef4444' }}>
                                                        {r.wb_stock_days_left != null && Number(r.wb_stock_days_left) >= 999 ? '∞' : fmt(r.wb_stock_days_left)}
                                                        <div style={{ fontSize: 10, fontWeight: 400, color: '#9ca3af' }} title="Запас с учётом наших складов">
                                                            общий: {r.stock_days_left != null && Number(r.stock_days_left) >= 999 ? '∞' : fmt(r.stock_days_left)}
                                                        </div>
                                                    </td>
                                                    <td style={tdStyle}>{fmt(r.orders_sum_rub)}</td>
                                                    <td style={{ ...tdStyle, color: '#9ca3af' }}>0</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                )}
                        </div>
                    </div>
                  </>
                )}

                {/* ─── Нет органики ─── */}
                {tab === 'no-organic' && (
                  <>
                    {selectedNm != null && (
                        <div className="glass-card" style={{ padding: 0, overflow: 'hidden', marginBottom: 12 }}>
                            <ProductHistoryChart nmId={selectedNm} dateFrom={dateFrom} dateTo={dateTo} title={selectedNmLabel} />
                        </div>
                    )}
                    <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                        <div style={{ padding: '10px 16px', display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap', borderBottom: '1px solid #e5e7eb', background: '#f9fafb' }}>
                            {periodControls}
                            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text-dim)' }}
                                title="Доля рекламных кликов от всех переходов. По анализу «Ковров» и «Чехлов»: ≥50% органика слабеет, ≥60% конверсия в заказ падает вдвое">
                                Доля рекл. кликов ≥
                                <input type="number" min={0} max={100} value={organicThreshold}
                                    onChange={e => setOrganicThreshold(Math.min(100, Math.max(0, Number(e.target.value) || 0)))}
                                    style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 8px', fontSize: 13, color: 'var(--color-text)', width: 60 }} />%
                            </label>
                            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text-dim)' }}
                                title="Минимум переходов за период, чтобы отсечь товары без трафика (шум)">
                                Мин. переходов
                                <input type="number" min={0} value={minOpensOrganic}
                                    onChange={e => setMinOpensOrganic(Math.max(0, Number(e.target.value) || 0))}
                                    style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 8px', fontSize: 13, color: 'var(--color-text)', width: 70 }} />
                            </label>
                            {excelBtn(exportNoOrganic, noOrganic.length === 0)}
                            <span style={{ fontSize: 12, color: 'var(--color-text-dim)', marginLeft: 'auto' }}>Найдено: {noOrganic.length}</span>
                        </div>
                        <div style={{ padding: '8px 16px', fontSize: 12, color: 'var(--color-text-dim)', borderBottom: '1px solid #f3f4f6' }}>
                            Товары, у которых большинство переходов — платные (органика площадки их не продвигает). Порог 50% выбран по анализу «Ковров» и «Чехлов»: на 60%+ конверсия в заказ падает вдвое. С такими товарами надо работать (цена, карточка, отзывы) или выводить.
                        </div>
                        <div style={{ overflowX: 'auto' }}>
                            {loading ? <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div> :
                                noOrganic.length === 0 ? <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>Нет товаров с долей рекл. кликов ≥ {organicThreshold}% 🎉</div> : (
                                    <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                                        <thead><tr>
                                            <th style={thLeft}>Товар</th>
                                            <th style={thStyle} title="Доля рекламных кликов от всех переходов">Рекл. клики %</th>
                                            <th style={thStyle}>Переходы</th>
                                            <th style={thStyle}>Рекл. клики</th>
                                            <th style={thStyle}>Заказы ₽</th>
                                            <th style={thStyle}>Расход рекл. ₽</th>
                                            <th style={thStyle}>ДРР</th>
                                            <th style={thStyle}>В заказ %</th>
                                        </tr></thead>
                                        <tbody>
                                            {noOrganic.map(r => {
                                                const share = adShare(r);
                                                return (
                                                <tr key={r.nm_id} style={{ color: '#111827', cursor: 'pointer', background: selectedNm === r.nm_id ? '#eff6ff' : undefined }} onClick={() => setSelectedNm(r.nm_id)}>
                                                    <td style={tdLeft}>
                                                        <div style={{ fontWeight: 600, fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
                                                            <span style={{ fontSize: 11, color: selectedNm === r.nm_id ? '#3b82f6' : '#d1d5db', width: 12 }}>{selectedNm === r.nm_id ? '●' : '○'}</span>
                                                            {r.vendor_code || r.nm_id}
                                                        </div>
                                                        <div style={{ fontSize: 11, color: '#9ca3af', paddingLeft: 18 }}>{r.brand} · {r.subject} · <a href={`https://www.wildberries.ru/catalog/${r.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }} onClick={e => e.stopPropagation()}>#{r.nm_id}</a></div>
                                                    </td>
                                                    <td style={{ ...tdStyle, fontWeight: 700, color: share >= 60 ? '#ef4444' : '#f59e0b' }}>{fmtPct(share)}</td>
                                                    <td style={tdStyle}>{fmt(r.open_card)}</td>
                                                    <td style={tdStyle}>{fmt(r.adv_clicks)}</td>
                                                    <td style={tdStyle}>{fmt(r.orders_sum_rub)}</td>
                                                    <td style={{ ...tdStyle, color: '#f97316' }}>{fmt(r.adv_sum)}</td>
                                                    <td style={{ ...tdStyle, color: (Number(r.drr) || 0) > 15 ? '#ef4444' : '#374151' }}>{fmtPct(r.drr)}</td>
                                                    <td style={tdStyle}>{fmtPct(r.cart_to_order_pct)}</td>
                                                </tr>
                                            ); })}
                                        </tbody>
                                    </table>
                                )}
                        </div>
                    </div>
                  </>
                )}

                {/* ─── Нехватка бюджета ─── */}
                {tab === 'budget-gap' && (
                    <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                        <div style={{ padding: '10px 16px', display: 'flex', gap: 12, alignItems: 'center', borderBottom: '1px solid #e5e7eb', background: '#f9fafb' }}>
                            <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                                Активные кампании, у которых сегодня закончился бюджет. Час остановки — по данным часовой синхронизации (точность ±1 час). Доливка — при текущей скорости траты до 00:00 МСК, но не меньше минимума пополнения WB (1000 ₽).
                            </span>
                            <button onClick={loadGaps} className="btn btn-secondary btn-sm" style={{ fontSize: 13, marginLeft: 'auto', whiteSpace: 'nowrap' }}>Обновить</button>
                            {excelBtn(exportGaps, visibleGaps.length === 0)}
                        </div>
                        <div style={{ overflowX: 'auto' }}>
                            {loading ? <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div> :
                                visibleGaps.length === 0 ? <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>{q ? 'Ничего не найдено по запросу' : 'Сегодня бюджет нигде не кончался 🎉'}</div> : (
                                    <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                                        <thead><tr>
                                            <th style={thLeft}>Кампания</th>
                                            <th style={thStyle}>Потрачено сегодня ₽</th>
                                            <th style={thStyle}>Остановилась в (МСК)</th>
                                            <th style={thStyle}>Скорость ₽/час</th>
                                            <th style={thStyle}>Долить до 00:00 ₽</th>
                                            <th style={thStyle}>Товаров</th>
                                        </tr></thead>
                                        <tbody>
                                            {visibleGaps.map(g => (
                                                <tr key={g.campaign_id} style={{ color: '#111827' }}>
                                                    <td style={tdLeft}>
                                                        <div style={{ fontWeight: 600, fontSize: 13 }}>{g.name || `#${g.campaign_id}`}</div>
                                                        <div style={{ fontSize: 11, color: '#9ca3af' }}>#{g.campaign_id} · {g.campaign_type || '—'}</div>
                                                    </td>
                                                    <td style={tdStyle}>{fmt(g.spend_today)}</td>
                                                    <td style={{ ...tdStyle, fontWeight: 700, color: '#ef4444' }}>{mskTime(g.ran_out_at)}</td>
                                                    <td style={tdStyle}>{fmt(g.burn_rate)}</td>
                                                    <td style={{ ...tdStyle, fontWeight: 700, color: 'var(--color-accent)' }}
                                                        title={g.raw_needed < g.min_topup ? `Расчётная нужда ${fmt(g.raw_needed)} ₽ округлена вверх до минимума пополнения WB ${fmt(g.min_topup)} ₽` : undefined}>
                                                        {fmt(g.needed_till_midnight)}{g.raw_needed < g.min_topup && g.raw_needed > 0 ? ' *' : ''}
                                                    </td>
                                                    <td style={tdStyle}>{g.nm_count}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                )}
                        </div>
                    </div>
                )}
                {autopayModal && (
                    <AutopayModal
                        campaign={autopayModal}
                        initial={autopay[String(autopayModal.campaign_id)]}
                        onClose={() => setAutopayModal(null)}
                        onSave={s => saveAutopay(autopayModal.campaign_id, s)}
                    />
                )}
                {autopayLogModal && (
                    <AutopayLogModal
                        campaign={autopayLogModal}
                        onClose={() => setAutopayLogModal(null)}
                    />
                )}
                {clustersModal && (
                    <ClustersModal campaign={clustersModal} onClose={() => setClustersModal(null)} />
                )}
            </div>
        </PageGuard>
    );
}
