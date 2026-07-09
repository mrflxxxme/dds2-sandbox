'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import ClusterTable, { clNum, clMoney, clMoneyN, clPctN, drrColor } from '../components/ClusterTable';
import type { AdCategory, CategoryClustersResponse, ClusterProduct } from '@/types/api';

const iso = (d: Date) => d.toISOString().slice(0, 10);
const num = (v: unknown) => Number(v) || 0;

const thStyle: React.CSSProperties = { background: '#f9fafb', color: '#4b5563', fontSize: 11, textAlign: 'right', borderBottom: '2px solid #e5e7eb', padding: '8px 10px', whiteSpace: 'nowrap' };
const thLeft: React.CSSProperties = { ...thStyle, textAlign: 'left' };
const tdStyle: React.CSSProperties = { textAlign: 'right', borderBottom: '1px solid #f3f4f6', padding: '7px 10px', fontSize: 13 };
const tdLeft: React.CSSProperties = { ...tdStyle, textAlign: 'left' };

type ProdSort = 'orders' | 'spend' | 'drr' | 'cpo' | 'cr' | 'views' | 'clicks';

export default function AdCategoriesPage() {
    const routeParams = useParams();
    const slug = typeof routeParams?.slug === 'string' ? routeParams.slug : Array.isArray(routeParams?.slug) ? routeParams.slug[0] : '';

    const [categories, setCategories] = useState<AdCategory[] | null>(null);
    const [subject, setSubject] = useState('');
    const [dateFrom, setDateFrom] = useState(iso(new Date(Date.now() - 29 * 86400_000)));
    const [dateTo, setDateTo] = useState(iso(new Date()));

    const [data, setData] = useState<CategoryClustersResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [submitted, setSubmitted] = useState(false);

    const [prodSort, setProdSort] = useState<{ field: ProdSort; dir: 'asc' | 'desc' }>({ field: 'orders', dir: 'desc' });
    // Фильтры таблицы «Товары категории» (клиентские)
    const [fBrand, setFBrand] = useState('');
    const [fImt, setFImt] = useState('');
    const [fArticle, setFArticle] = useState('');

    useEffect(() => {
        const controller = new AbortController();
        api.getAdCategories()
            .then(cats => { if (!controller.signal.aborted) setCategories(cats); })
            .catch(() => { if (!controller.signal.aborted) setCategories([]); });
        return () => controller.abort();
    }, []);

    const load = useCallback(async () => {
        if (!subject) return;
        setLoading(true); setError(''); setSubmitted(true); setData(null);
        try {
            const res = await api.getCategoryClusters(subject, dateFrom, dateTo);
            if (res.error) {
                setError(res.error === 'no_api_key' ? 'Не задан API-ключ WB — подключите ключ в настройках проекта.' : res.error);
            } else {
                setData(res);
            }
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [subject, dateFrom, dateTo]);

    // Список брендов для селекта — по товарам текущей категории
    const brandOptions = useMemo(() => {
        if (!data) return [];
        return Array.from(new Set(data.products.map(p => p.brand).filter((b): b is string => !!b))).sort((a, b) => a.localeCompare(b, 'ru'));
    }, [data]);

    const products = useMemo(() => {
        if (!data) return [];
        const imt = fImt.trim();
        const art = fArticle.trim().toLowerCase();
        const filtered = data.products.filter(p => {
            if (fBrand && (p.brand || '') !== fBrand) return false;
            if (imt && !String(p.imt_id ?? '').includes(imt)) return false;
            if (art && !((p.vendor_code || '').toLowerCase().includes(art) || String(p.nm_id).includes(art))) return false;
            return true;
        });
        const dir = prodSort.dir === 'asc' ? 1 : -1;
        return filtered.sort((a, b) => {
            const av = a[prodSort.field] == null ? -Infinity : Number(a[prodSort.field]);
            const bv = b[prodSort.field] == null ? -Infinity : Number(b[prodSort.field]);
            return dir * (av - bv);
        });
    }, [data, prodSort, fBrand, fImt, fArticle]);

    const toggleProdSort = (field: ProdSort) =>
        setProdSort(prev => ({ field, dir: prev.field === field && prev.dir === 'desc' ? 'asc' : 'desc' }));
    const arrow = (field: ProdSort) => prodSort.field === field ? (prodSort.dir === 'desc' ? ' ↓' : ' ↑') : '';
    const prodTh = (field: ProdSort, label: string, title?: string) => (
        <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleProdSort(field)} title={title}>{label}{arrow(field)}</th>
    );

    const exportProducts = () => data && exportToExcel(products.map((p: ClusterProduct) => ({
        'Артикул': p.vendor_code || '', 'nm_id': p.nm_id, 'Бренд': p.brand || '', 'Склейка (imt)': p.imt_id ?? '',
        'Показы': num(p.views), 'Клики': num(p.clicks), 'Заказы': num(p.orders),
        'Расход ₽': num(p.spend), 'ДРР %': p.drr == null ? '' : Number(p.drr), 'CPO ₽': p.cpo == null ? '' : Number(p.cpo), 'CR %': num(p.cr),
    })), `category-products_${subject}_${dateFrom}_${dateTo}`);

    const totals = data?.totals;
    const target = data?.target_drr ?? 8;

    return (
        <PageGuard page="ads-manager">
            <div className="animate-in">
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                    <div>
                        <h1 style={{ fontSize: 28, fontWeight: 700, margin: '0 0 4px' }}>🧩 Кластеры по категории</h1>
                        <p style={{ fontSize: 13, color: 'var(--color-text-dim)', margin: '0 0 16px' }}>
                            Средние и топовые поисковые запросы по категории, а также какие товары конвертят, а какие сливают бюджет.
                        </p>
                    </div>
                    <Link href={`/p/${slug}/ads-manager`} className="btn btn-secondary btn-sm" style={{ fontSize: 13, whiteSpace: 'nowrap' }}>
                        ← К кампаниям
                    </Link>
                </div>

                {/* Панель выбора */}
                <div className="glass-card" style={{ padding: 16, marginBottom: 12, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <select value={subject} onChange={e => setSubject(e.target.value)} disabled={categories == null}
                        style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)', minWidth: 260 }}>
                        <option value="">{categories == null ? 'Загрузка категорий…' : 'Выберите категорию…'}</option>
                        {categories?.map(c => (
                            <option key={c.subject} value={c.subject}>{c.subject} ({formatNumber(c.nm_count, 0)} товаров)</option>
                        ))}
                    </select>
                    <input type="date" value={dateFrom} max={dateTo} onChange={e => setDateFrom(e.target.value)}
                        style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)' }} />
                    <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                    <input type="date" value={dateTo} min={dateFrom} onChange={e => setDateTo(e.target.value)}
                        style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)' }} />
                    <button className="btn btn-secondary btn-sm" style={{ fontSize: 12 }} onClick={() => { setDateFrom(iso(new Date(Date.now() - 29 * 86400_000))); setDateTo(iso(new Date())); }}>30 дней</button>
                    <button className="btn btn-primary btn-sm" style={{ fontSize: 13 }} onClick={load} disabled={!subject || loading}>
                        {loading ? 'Загрузка…' : 'Показать'}
                    </button>
                </div>

                {error && (
                    <div className="glass-card" style={{ padding: '12px 20px', marginBottom: 12, border: '1px solid var(--color-danger)' }}>
                        <span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {error}</span>
                    </div>
                )}

                {/* Пустое состояние (до первого запроса) */}
                {!submitted && !loading && !error && (
                    <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 14 }}>
                        Выберите категорию и период, затем нажмите «Показать».
                    </div>
                )}

                {loading && (
                    <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Загрузка кластеров категории…</div>
                )}

                {!loading && !error && submitted && data && (data.clusters.length > 0 || data.products.length > 0) && (
                    <>
                        {data.truncated && (
                            <div className="glass-card" style={{ padding: '10px 16px', marginBottom: 12, border: '1px solid var(--color-warning, #f59e0b)', background: '#fffbeb' }}>
                                <span style={{ fontSize: 13, color: '#b45309' }}>⚠️ Показаны первые {formatNumber(data.pairs, 0)} пар кампания×запрос — данные усечены, показатели приблизительны.</span>
                            </div>
                        )}

                        {/* Сводка */}
                        <div className="glass-card" style={{ padding: 16, marginBottom: 12 }}>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
                                {[
                                    { label: 'Кампаний', value: clNum(data.campaigns_count) },
                                    { label: 'Товаров', value: clNum(data.products_count) },
                                    { label: 'Кластеров', value: clNum(totals?.clusters) },
                                    { label: 'Показы', value: clNum(totals?.views) },
                                    { label: 'Клики', value: clNum(totals?.clicks) },
                                    { label: 'Заказы', value: clNum(totals?.orders) },
                                    { label: 'Расход', value: clMoney(totals?.spend) },
                                    { label: 'CPO', value: clMoneyN(totals?.cpo ?? null) },
                                    { label: 'AOV', value: clMoney(data.aov) },
                                ].map(m => (
                                    <div key={m.label} style={{ minWidth: 92, padding: '8px 12px', border: '1px solid #e5e7eb', borderRadius: 12, background: '#f9fafb' }}>
                                        <div style={{ fontSize: 11, color: '#6b7280' }}>{m.label}</div>
                                        <div style={{ fontSize: 17, fontWeight: 700, color: '#111827' }}>{m.value}</div>
                                    </div>
                                ))}
                            </div>
                        </div>

                        {/* Агрегированная таблица кластеров */}
                        <div className="glass-card" style={{ padding: 16, marginBottom: 12 }}>
                            <div style={{ fontSize: 14, fontWeight: 600, color: '#374151', marginBottom: 10 }}>Средние и топовые запросы категории</div>
                            {data.clusters.length === 0 ? (
                                <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>Нет данных по кластерам за период</div>
                            ) : (
                                <ClusterTable clusters={data.clusters} targetDrr={data.target_drr} exportName={`category-clusters_${subject}_${dateFrom}_${dateTo}`} />
                            )}
                        </div>

                        {/* Товары категории */}
                        <div className="glass-card" style={{ padding: 16 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
                                <div style={{ fontSize: 14, fontWeight: 600, color: '#374151' }}>Товары категории</div>
                                <select value={fBrand} onChange={e => setFBrand(e.target.value)}
                                    style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '4px 8px', fontSize: 12, color: 'var(--color-text)', maxWidth: 200 }}>
                                    <option value="">Все бренды</option>
                                    {brandOptions.map(b => <option key={b} value={b}>{b}</option>)}
                                </select>
                                <input placeholder="Склейка (imt)" value={fImt} onChange={e => setFImt(e.target.value)}
                                    style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '4px 8px', fontSize: 12, color: 'var(--color-text)', width: 120 }} />
                                <input placeholder="Артикул / #nm_id" value={fArticle} onChange={e => setFArticle(e.target.value)}
                                    style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '4px 8px', fontSize: 12, color: 'var(--color-text)', width: 150 }} />
                                {(fBrand || fImt || fArticle) && (
                                    <button className="btn btn-secondary btn-sm" style={{ fontSize: 12 }}
                                        onClick={() => { setFBrand(''); setFImt(''); setFArticle(''); }}>✕ Сбросить</button>
                                )}
                                <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Товаров: {products.length}</span>
                                <button className="btn btn-secondary btn-sm" style={{ fontSize: 12, marginLeft: 'auto' }} onClick={exportProducts} disabled={products.length === 0}>📥 Excel</button>
                            </div>
                            {products.length === 0 ? (
                                <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>Нет товаров с данными за период</div>
                            ) : (
                                <div style={{ overflowX: 'auto' }}>
                                    <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                                        <thead><tr>
                                            <th style={thLeft}>Товар</th>
                                            <th style={thLeft}>Бренд</th>
                                            <th style={thLeft} title="Склейка карточек WB">Склейка</th>
                                            {prodTh('views', 'Показы')}
                                            {prodTh('clicks', 'Клики')}
                                            {prodTh('orders', 'Заказы')}
                                            {prodTh('spend', 'Расход ₽')}
                                            {prodTh('drr', 'ДРР', 'Доля рекламных расходов')}
                                            {prodTh('cpo', 'CPO', 'Стоимость заказа')}
                                            {prodTh('cr', 'CR', 'Конверсия клик→заказ')}
                                        </tr></thead>
                                        <tbody>
                                            {products.map(p => {
                                                const highDrr = p.drr != null && Number(p.drr) > target * 1.5;
                                                const zeroOrders = num(p.orders) === 0 && num(p.spend) > 0;
                                                const bad = highDrr || zeroOrders;
                                                return (
                                                    <tr key={p.nm_id} style={{ color: '#111827', background: bad ? '#fef2f2' : undefined }}>
                                                        <td style={tdLeft}>
                                                            <Link href={`/p/${slug}/ads-manager/product/${p.nm_id}?from=${dateFrom}&to=${dateTo}`}
                                                                style={{ color: 'var(--color-accent)', fontWeight: 600, textDecoration: 'none' }}
                                                                title="Кластеры и минус-фразы артикула">🧩 {p.vendor_code || `#${p.nm_id}`}</Link>
                                                            <a href={`https://www.wildberries.ru/catalog/${p.nm_id}/detail.aspx`} target="_blank" rel="noreferrer"
                                                                style={{ color: 'var(--color-text-dim)', fontSize: 11, marginLeft: 6, textDecoration: 'none' }} title="Открыть карточку на WB">↗</a>
                                                            {zeroOrders && <span style={{ fontSize: 11, color: '#ef4444', marginLeft: 6 }}>0 заказов</span>}
                                                        </td>
                                                        <td style={tdLeft}>{p.brand || '—'}</td>
                                                        <td style={tdLeft}>{p.imt_id ?? '—'}</td>
                                                        <td style={tdStyle}>{clNum(p.views)}</td>
                                                        <td style={tdStyle}>{clNum(p.clicks)}</td>
                                                        <td style={{ ...tdStyle, fontWeight: 600 }}>{clNum(p.orders)}</td>
                                                        <td style={{ ...tdStyle, fontWeight: 600 }}>{clMoney(p.spend)}</td>
                                                        <td style={{ ...tdStyle, fontWeight: 600, color: drrColor(p.drr, target) }}>{clPctN(p.drr)}</td>
                                                        <td style={tdStyle}>{clMoneyN(p.cpo)}</td>
                                                        <td style={tdStyle}>{clPctN(p.cr, 2)}</td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </div>
                    </>
                )}

                {/* Empty data (запрос был, но категория пустая) */}
                {!loading && !error && submitted && data && data.clusters.length === 0 && data.products.length === 0 && (
                    <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 14, marginTop: 12 }}>
                        По категории «{subject}» нет рекламных данных за выбранный период.
                    </div>
                )}
            </div>
        </PageGuard>
    );
}
