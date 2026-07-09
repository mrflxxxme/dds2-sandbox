'use client';
import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import ClusterTable, { DailyBudgetBar, drrColor, clNum, clMoney, clMoneyN } from '../../components/ClusterTable';
import type { ProductClustersResponse, SearchCluster, ProductDailyRow, ProductDailyResponse } from '@/types/api';

const iso = (d: Date) => d.toISOString().slice(0, 10);
const num = (v: unknown) => Number(v) || 0;

// ─── Форматтеры «По дням» (Decimal-поля бэка приходят строкой → Number()) ───
const dCount = (x: number) => formatNumber(Number(x), 0);
const dMoney = (x: number) => `${formatNumber(Number(x), 0)} ₽`;
const dPct = (x: number) => `${formatNumber(Number(x), 2)} %`;
const dMoneyN = (x: number | null) => (x == null ? '—' : `${formatNumber(Number(x), 0)} ₽`);
const dPctN = (x: number | null) => (x == null ? '—' : `${formatNumber(Number(x), 2)} %`);

// ─── Вкладка «По дням»: два блока колонок (Статистика по РК ‖ Воронка продаж) ───
const dThBase: React.CSSProperties = { background: '#f9fafb', color: '#4b5563', fontSize: 11, textAlign: 'right', padding: '7px 9px', whiteSpace: 'nowrap', borderBottom: '2px solid #e5e7eb' };
const dTd: React.CSSProperties = { textAlign: 'right', padding: '7px 9px', fontSize: 13, borderBottom: '1px solid #f3f4f6', whiteSpace: 'nowrap' };
// Светлый разделитель между группой РК и группой «Воронка» — левая граница у первой колонки воронки
const dDivider = '2px solid #e5e7eb';

function DailyRow({ r, totals }: { r: ProductDailyRow; totals?: boolean }) {
    const base: React.CSSProperties = totals
        ? { ...dTd, fontWeight: 700, background: '#f3f4f6' }
        : dTd;
    const dateCell: React.CSSProperties = { ...base, textAlign: 'left' };
    const funnelFirst: React.CSSProperties = { ...base, borderLeft: dDivider };
    return (
        <tr style={{ color: '#111827' }}>
            <td style={dateCell}>{r.date}</td>
            {/* Статистика по РК */}
            <td style={base}>{dCount(r.views)}</td>
            <td style={base}>{dCount(r.clicks)}</td>
            <td style={base}>{dPct(r.ctr)}</td>
            <td style={base}>{dMoney(r.cpc)}</td>
            <td style={base}>{dMoney(r.spend)}</td>
            <td style={base}>{dMoneyN(r.cpo)}</td>
            {/* Воронка продаж */}
            <td style={funnelFirst}>{dCount(r.opens)}</td>
            <td style={base}>{dCount(r.carts)}</td>
            <td style={base}>{dPct(r.cr1)}</td>
            <td style={base}>{dCount(r.orders)}</td>
            <td style={base}>{dPct(r.cr2)}</td>
            <td style={base}>{dMoney(r.revenue)}</td>
            <td style={base}>{dMoneyN(r.price)}</td>
            <td style={{ ...base, color: drrColor(r.drr == null ? null : Number(r.drr), 8) }}>{dPctN(r.drr)}</td>
        </tr>
    );
}

function DailyTab({ daily, loading, error, onExport }: { daily: ProductDailyResponse | null; loading: boolean; error: string; onExport: () => void }) {
    if (error) {
        return (
            <div className="glass-card" style={{ padding: '12px 20px', marginBottom: 12, border: '1px solid var(--color-danger)' }}>
                <span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {error}</span>
            </div>
        );
    }
    if (loading) {
        return <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Загрузка статистики по дням…</div>;
    }
    if (!daily) return null;
    if (daily.rows.length === 0) {
        return <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>За выбранный период у артикула нет дневной статистики</div>;
    }
    return (
        <div className="glass-card" style={{ padding: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
                <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Дней: {daily.rows.length}</span>
                <button className="btn btn-secondary btn-sm" style={{ fontSize: 12, marginLeft: 'auto' }} onClick={onExport} title="Выгрузить в Excel">📥 Excel</button>
            </div>
            <div style={{ overflowX: 'auto' }}>
                <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                    <thead>
                        <tr>
                            <th style={{ ...dThBase, textAlign: 'left' }} rowSpan={2}>Дата</th>
                            <th style={{ ...dThBase, textAlign: 'center', color: '#2563eb' }} colSpan={6}>Статистика по РК</th>
                            <th style={{ ...dThBase, textAlign: 'center', color: '#7c3aed', borderLeft: dDivider }} colSpan={8}>Воронка продаж</th>
                        </tr>
                        <tr>
                            <th style={dThBase}>Показы</th>
                            <th style={dThBase}>Клики</th>
                            <th style={dThBase}>CTR</th>
                            <th style={dThBase}>CPC</th>
                            <th style={dThBase}>Затраты</th>
                            <th style={dThBase}>CPO</th>
                            <th style={{ ...dThBase, borderLeft: dDivider }}>Переходы</th>
                            <th style={dThBase}>Корзины</th>
                            <th style={dThBase}>CR1</th>
                            <th style={dThBase}>Заказы</th>
                            <th style={dThBase}>CR2</th>
                            <th style={dThBase}>Сумма заказов</th>
                            <th style={dThBase}>Цена</th>
                            <th style={dThBase}>ДРР</th>
                        </tr>
                    </thead>
                    <tbody>
                        <DailyRow r={daily.totals} totals />
                        {daily.rows.map(r => <DailyRow key={r.date} r={r} />)}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

export default function ProductClustersPage() {
    const routeParams = useParams();
    const slug = typeof routeParams?.slug === 'string' ? routeParams.slug : Array.isArray(routeParams?.slug) ? routeParams.slug[0] : '';
    const rawNm = typeof routeParams?.nmId === 'string' ? routeParams.nmId : Array.isArray(routeParams?.nmId) ? routeParams.nmId[0] : '';
    const nmId = Number(rawNm) || 0;

    const sp = useSearchParams();
    const rawFrom = sp.get('from') ?? '';
    const rawTo = sp.get('to') ?? '';
    const defFrom = iso(new Date(Date.now() - 29 * 86400_000));
    const defTo = iso(new Date());

    const [dateFrom, setDateFrom] = useState(rawFrom || defFrom);
    const [dateTo, setDateTo] = useState(rawTo || defTo);
    const [tab, setTab] = useState<'clusters' | 'daily'>('clusters');
    const [data, setData] = useState<ProductClustersResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [reloadKey, setReloadKey] = useState(0);
    // Вкладка «По дням» — ленивая загрузка, отдельные состояния
    const [daily, setDaily] = useState<ProductDailyResponse | null>(null);
    const [dailyLoading, setDailyLoading] = useState(false);
    const [dailyError, setDailyError] = useState('');
    // Минус-фразы: набор norm_query, по которым идёт запрос, и текст последней ошибки
    const [pending, setPending] = useState<Set<string>>(new Set());
    const [minusError, setMinusError] = useState<string | null>(null);
    // Ставки: набор norm_query, по которым идёт запись, и текст последней ошибки
    const [bidPending, setBidPending] = useState<Set<string>>(new Set());
    const [bidError, setBidError] = useState<string | null>(null);

    useEffect(() => {
        if (!nmId) { setLoading(false); setError('Некорректный артикул'); return; }
        const controller = new AbortController();
        setLoading(true); setError(''); setData(null);
        api.getProductClusters(nmId, dateFrom, dateTo)
            .then(res => {
                if (controller.signal.aborted) return;
                if (res.error) {
                    setError(res.error === 'no_api_key' ? 'Не задан API-ключ WB — подключите ключ в настройках проекта, чтобы анализировать кластеры.'
                        : res.error === 'no_campaign_for_nm' ? 'У артикула нет CPM-поиск-кампаний.'
                        : res.error);
                } else {
                    setData(res);
                }
            })
            .catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка загрузки кластеров'); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [nmId, dateFrom, dateTo, reloadKey]);

    // Ленивая загрузка «По дням»: только когда вкладка активна; рефетч при смене дат / «Показать»
    useEffect(() => {
        if (tab !== 'daily') return;
        if (!nmId) { setDailyLoading(false); setDailyError('Некорректный артикул'); return; }
        const controller = new AbortController();
        setDailyLoading(true); setDailyError(''); setDaily(null);
        api.getProductDaily(nmId, dateFrom, dateTo)
            .then(res => { if (!controller.signal.aborted) setDaily(res); })
            .catch(e => { if (!controller.signal.aborted) setDailyError(e instanceof Error ? e.message : 'Ошибка загрузки статистики по дням'); })
            .finally(() => { if (!controller.signal.aborted) setDailyLoading(false); });
        return () => controller.abort();
    }, [nmId, dateFrom, dateTo, tab, reloadKey]);

    const handleToggleMinus = useCallback(async (c: SearchCluster) => {
        const action: 'add' | 'remove' = c.is_minused ? 'remove' : 'add';
        const confirmText = action === 'add'
            ? `Добавить «${c.norm_query}» в минус-фразы во всех кампаниях артикула (кабинет WB)?`
            : `Вернуть «${c.norm_query}» — убрать из минус-фраз во всех кампаниях артикула (кабинет WB)?`;
        if (!window.confirm(confirmText)) return;
        setMinusError(null);
        setPending(prev => new Set(prev).add(c.norm_query));
        try {
            const res = await api.toggleProductClusterMinus(nmId, { norm_query: c.norm_query, action });
            if (!res.ok) { setMinusError(res.error || 'WB отклонил операцию'); return; }
            setData(prev => prev ? { ...prev, clusters: prev.clusters.map(x => x.norm_query === c.norm_query ? { ...x, is_minused: action === 'add' } : x) } : prev);
        } catch (e) {
            setMinusError(e instanceof Error ? e.message : 'Ошибка обращения к WB');
        } finally {
            setPending(prev => { const nx = new Set(prev); nx.delete(c.norm_query); return nx; });
        }
    }, [nmId]);

    const handleSetBid = useCallback(async (c: SearchCluster, bid: number) => {
        if (!window.confirm(`Поставить ставку ${formatNumber(bid, 0)} ₽ на кластер «${c.norm_query}» во всех кампаниях артикула (кабинет WB)?`)) return;
        setBidError(null);
        setBidPending(prev => new Set(prev).add(c.norm_query));
        try {
            const res = await api.setProductClusterBid(nmId, { norm_query: c.norm_query, bid });
            if (!res.ok) { setBidError(res.error || 'WB отклонил ставку'); return; }
            setData(prev => prev ? { ...prev, clusters: prev.clusters.map(x => x.norm_query === c.norm_query ? { ...x, bid: res.bid ?? bid } : x) } : prev);
        } catch (e) {
            setBidError(e instanceof Error ? e.message : 'Ошибка обращения к WB');
        } finally {
            setBidPending(prev => { const nx = new Set(prev); nx.delete(c.norm_query); return nx; });
        }
    }, [nmId]);

    const doDailyExport = useCallback(() => {
        if (!daily) return;
        const toRow = (r: ProductDailyRow) => ({
            'Дата': r.date,
            'Показы': num(r.views), 'Клики': num(r.clicks), 'CTR %': num(r.ctr), 'CPC ₽': num(r.cpc),
            'Затраты ₽': num(r.spend), 'CPO ₽': r.cpo == null ? '' : Number(r.cpo),
            'Переходы': num(r.opens), 'Корзины': num(r.carts), 'CR1 %': num(r.cr1),
            'Заказы': num(r.orders), 'CR2 %': num(r.cr2), 'Сумма заказов ₽': num(r.revenue),
            'Цена ₽': r.price == null ? '' : Number(r.price), 'ДРР %': r.drr == null ? '' : Number(r.drr),
        });
        exportToExcel([toRow(daily.totals), ...daily.rows.map(toRow)], `product-daily_${nmId}_${dateFrom}_${dateTo}`);
    }, [daily, nmId, dateFrom, dateTo]);

    const totals = data?.totals;
    const campDrr = data && num(data.aov) > 0 && num(totals?.orders) > 0 ? num(totals?.spend) / (num(totals?.orders) * num(data.aov)) * 100 : null;

    const title = data?.vendor_code || `#${nmId}`;
    const campNames = data?.campaigns.map(c => c.name).filter(Boolean).join(', ') || '';
    const subtitle = [data?.subject, campNames].filter(Boolean).join(' · ');

    const inputStyle: React.CSSProperties = { background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)' };

    return (
        <PageGuard page="ads-manager">
            <div className="animate-in">
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                    <div>
                        <h1 style={{ fontSize: 28, fontWeight: 700, margin: '0 0 4px' }}>🧩 Кластеры артикула — {title}</h1>
                        <p style={{ fontSize: 13, color: 'var(--color-text-dim)', margin: '0 0 16px' }}>
                            {subtitle ? subtitle : `#${nmId}`} · выберите кластер и отправьте в минус-фразы во всех кампаниях артикула.
                        </p>
                    </div>
                    <Link href={`/p/${slug}/ads-manager/categories`} className="btn btn-secondary btn-sm" style={{ fontSize: 13, whiteSpace: 'nowrap' }}>
                        ← К категории
                    </Link>
                </div>

                {/* Переключатель вкладок */}
                <div style={{ display: 'inline-flex', border: '1px solid var(--color-border)', borderRadius: 10, overflow: 'hidden', marginBottom: 12 }}>
                    {([['clusters', '🧩 Кластеры'], ['daily', '📈 По дням']] as const).map(([key, label], i) => {
                        const active = tab === key;
                        return (
                            <button key={key} onClick={() => setTab(key)}
                                style={{ padding: '7px 18px', fontSize: 13, fontWeight: 600, cursor: 'pointer', border: 'none',
                                    borderLeft: i > 0 ? '1px solid var(--color-border)' : 'none',
                                    background: active ? 'var(--color-accent)' : 'var(--color-bg-card)', color: active ? '#fff' : 'var(--color-text)' }}>
                                {label}
                            </button>
                        );
                    })}
                </div>

                {/* Период */}
                <div className="glass-card" style={{ padding: 16, marginBottom: 12, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <input type="date" value={dateFrom} max={dateTo} onChange={e => setDateFrom(e.target.value)} style={inputStyle} />
                    <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                    <input type="date" value={dateTo} min={dateFrom} onChange={e => setDateTo(e.target.value)} style={inputStyle} />
                    <button className="btn btn-secondary btn-sm" style={{ fontSize: 12 }} onClick={() => { setDateFrom(defFrom); setDateTo(defTo); }}>30 дней</button>
                    <button className="btn btn-primary btn-sm" style={{ fontSize: 13 }} onClick={() => setReloadKey(k => k + 1)} disabled={loading}>
                        {loading ? 'Загрузка…' : 'Показать'}
                    </button>
                    <a href={`https://www.wildberries.ru/catalog/${nmId}/detail.aspx`} target="_blank" rel="noreferrer"
                        className="btn btn-secondary btn-sm" style={{ fontSize: 12, marginLeft: 'auto' }}>Карточка на WB ↗</a>
                </div>

                {tab === 'clusters' && (<>
                {error && (
                    <div className="glass-card" style={{ padding: '12px 20px', marginBottom: 12, border: '1px solid var(--color-danger)' }}>
                        <span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {error}</span>
                    </div>
                )}

                {loading && (
                    <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Загрузка кластеров артикула…</div>
                )}

                {!loading && !error && data && (
                    <>
                        {/* Тоталы + ДРР артикула */}
                        <div className="glass-card" style={{ padding: 16, marginBottom: 12 }}>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
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
                                    <div style={{ fontSize: 11, color: '#6b7280' }}>ДРР артикула</div>
                                    <div style={{ fontSize: 17, fontWeight: 700, color: drrColor(campDrr, data.target_drr) }}>
                                        {campDrr == null ? '—' : `${formatNumber(campDrr, 1)}%`}
                                    </div>
                                </div>
                                {num(data.aov) > 0 && (
                                    <div style={{ minWidth: 92, padding: '8px 12px', border: '1px solid #e5e7eb', borderRadius: 12, background: '#f9fafb' }}>
                                        <div style={{ fontSize: 11, color: '#6b7280' }}>AOV</div>
                                        <div style={{ fontSize: 17, fontWeight: 700, color: '#111827' }}>{clMoney(data.aov)}</div>
                                    </div>
                                )}
                            </div>
                        </div>

                        {/* Feature 1 — распределение расхода по дням */}
                        {data.daily && data.daily.length > 0 && (
                            <div className="glass-card" style={{ padding: 16, marginBottom: 12 }}>
                                <div style={{ fontSize: 12, fontWeight: 600, color: '#374151', marginBottom: 6 }}>📊 Распределение расхода по дням</div>
                                <DailyBudgetBar daily={data.daily} />
                            </div>
                        )}

                        {/* Таблица кластеров + минус-фразы */}
                        <div className="glass-card" style={{ padding: 16 }}>
                            {data.clusters.length === 0 ? (
                                <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>За выбранный период у артикула нет данных по кластерам</div>
                            ) : (
                                <ClusterTable
                                    clusters={data.clusters}
                                    targetDrr={data.target_drr}
                                    exportName={`product-clusters_${nmId}_${dateFrom}_${dateTo}`}
                                    minus={{ pending, onToggle: handleToggleMinus, error: minusError }}
                                    bids={{ pending: bidPending, onSetBid: handleSetBid, error: bidError }}
                                />
                            )}
                        </div>
                    </>
                )}
                </>)}

                {tab === 'daily' && (
                    <DailyTab daily={daily} loading={dailyLoading} error={dailyError} onExport={doDailyExport} />
                )}
            </div>
        </PageGuard>
    );
}
