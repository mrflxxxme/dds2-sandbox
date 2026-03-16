'use client';
import { useState, useEffect } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';

export function WarehouseNeedView() {
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(false);
    const [supplyDays, setSupplyDays] = useState(14);
    const [analysisDays, setAnalysisDays] = useState(14);
    const [mode, setMode] = useState<'actual' | 'hypothetical'>('actual');
    const [brandFilter, setBrandFilter] = useState('');
    const [subjectFilter, setSubjectFilter] = useState('');
    const [sortCol, setSortCol] = useState<string>('total_need');
    const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

    const load = async () => {
        setLoading(true);
        try { setData(await api.getStockNeed(supplyDays, analysisDays, mode)); } catch { }
        setLoading(false);
    };

    useEffect(() => { load(); }, [supplyDays, analysisDays, mode]);

    const getArticleNeed = (a: any, whName?: string) => {
        if (!data?.warehouses) return 0;
        if (whName) return data.warehouses.find((w: any) => w.name === whName)?.articles?.[a.nm_id]?.need || 0;
        let total = 0;
        data.warehouses.forEach((wh: any) => { total += wh.articles?.[a.nm_id]?.need || 0; });
        return total;
    };

    const filteredArticles = (data?.articles || []).filter((a: any) => {
        if (brandFilter && a.brand !== brandFilter) return false;
        if (subjectFilter && a.subject !== subjectFilter) return false;
        return true;
    });

    const sortedArticles = [...filteredArticles].sort((a: any, b: any) => {
        let va: any, vb: any;
        if (sortCol === 'vendor_code') { va = a.vendor_code; vb = b.vendor_code; }
        else if (sortCol === 'total_need') { va = getArticleNeed(a); vb = getArticleNeed(b); }
        else { va = getArticleNeed(a, sortCol); vb = getArticleNeed(b, sortCol); }
        if (typeof va === 'string') return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        return sortDir === 'asc' ? va - vb : vb - va;
    });

    const getWhTotal = (whName: string) => {
        let sum = 0;
        filteredArticles.forEach((a: any) => { sum += getArticleNeed(a, whName); });
        return sum;
    };

    const grandTotal = (() => {
        let sum = 0;
        filteredArticles.forEach((a: any) => { sum += getArticleNeed(a); });
        return sum;
    })();

    const handleSort = (col: string) => {
        if (sortCol === col) setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
        else { setSortCol(col); setSortDir('desc'); }
    };

    const sortIcon = (col: string) => sortCol === col ? (sortDir === 'asc' ? ' ↑' : ' ↓') : '';

    const handleExport = () => {
        if (!data) return;
        const whs = data.warehouses || [];
        const header = ['Артикул', 'Бренд', 'Категория', 'Потребность', ...whs.map((w: any) => w.name)];
        const rows = sortedArticles.map((a: any) => [
            a.vendor_code, a.brand || '', a.subject || '',
            getArticleNeed(a),
            ...whs.map((wh: any) => getArticleNeed(a, wh.name) || 0),
        ]);
        const totalRow = ['ИТОГО', '', '', grandTotal, ...whs.map((w: any) => getWhTotal(w.name))];
        rows.push(totalRow);
        exportToExcel([header, ...rows], `Потребность_запас${supplyDays}д_анализ${analysisDays}д`);
    };

    if (loading && !data) return <div className="glass-card" style={{ textAlign: 'center', padding: 40 }}>Расчёт потребности...</div>;

    const modeLabel = mode === 'actual' ? 'Фактический' : 'Гипотетический';

    const thStyle: any = { textAlign: 'right', minWidth: 85, cursor: 'pointer', userSelect: 'none', fontSize: 11, whiteSpace: 'nowrap', padding: '10px 8px', borderBottom: '2px solid var(--color-border)' };
    const thStickyStyle: any = { ...thStyle, textAlign: 'left', position: 'sticky', left: 0, background: 'var(--color-bg)', zIndex: 2, minWidth: 180 };
    const tdStyle: any = { padding: '8px', textAlign: 'right', fontSize: 12, borderBottom: '1px solid var(--color-border)' };
    const tdStickyStyle: any = { ...tdStyle, textAlign: 'left', fontWeight: 600, position: 'sticky', left: 0, background: 'var(--color-bg)', zIndex: 1 };

    return (
        <div>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
                <div>
                    <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>📦 Потребность по складам</h2>
                    <span style={{ fontSize: 13, opacity: 0.6 }}>
                        {data ? `${data.total_warehouses} складов · ${filteredArticles.length} артикулов · запас ${supplyDays} дн · анализ ${analysisDays} дн · ${modeLabel}` : 'Нет данных'}
                    </span>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    {data?.brands?.length > 0 && (
                        <select value={brandFilter} onChange={e => setBrandFilter(e.target.value)}
                            style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--color-border)', background: 'var(--color-bg)', fontSize: 12 }}>
                            <option value="">Все бренды</option>
                            {data.brands.map((b: string) => <option key={b} value={b}>{b}</option>)}
                        </select>
                    )}
                    {data?.subjects?.length > 0 && (
                        <select value={subjectFilter} onChange={e => setSubjectFilter(e.target.value)}
                            style={{ padding: '6px 10px', borderRadius: 8, border: '1px solid var(--color-border)', background: 'var(--color-bg)', fontSize: 12 }}>
                            <option value="">Все категории</option>
                            {data.subjects.map((s: string) => <option key={s} value={s}>{s}</option>)}
                        </select>
                    )}
                    <div style={{ display: 'flex', gap: 2, background: 'var(--color-border)', borderRadius: 8, padding: 2 }}>
                        <button className={`btn btn-sm ${mode === 'actual' ? 'btn-primary' : 'btn-secondary'}`}
                            style={{ borderRadius: 6, fontSize: 11 }}
                            onClick={() => setMode('actual')}>📊 Факт</button>
                        <button className={`btn btn-sm ${mode === 'hypothetical' ? 'btn-primary' : 'btn-secondary'}`}
                            style={{ borderRadius: 6, fontSize: 11 }}
                            onClick={() => setMode('hypothetical')}>🗺️ Гипотез.</button>
                    </div>
                    {mode === 'hypothetical' && (
                        <label className="btn btn-sm btn-secondary" style={{ cursor: 'pointer', fontSize: 11 }}
                            title="Загрузить Excel «Лента заказов» из ЛК WB для точного определения городов">
                            📤 Загрузить ленту
                            <input type="file" accept=".xlsx" style={{ display: 'none' }} onChange={async (e) => {
                                const f = e.target.files?.[0];
                                if (!f) return;
                                try {
                                    const result = await api.uploadOrderCities(f);
                                    alert(`✅ Загружено ${result.total_mappings} городов`);
                                    await load();
                                } catch (err: any) {
                                    alert(`❌ Ошибка: ${err.message}`);
                                }
                                e.target.value = '';
                            }} />
                        </label>
                    )}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <span style={{ fontSize: 11, opacity: 0.6, whiteSpace: 'nowrap' }}>Запас:</span>
                        {[7, 14, 30, 60].map(d => (
                            <button key={d} className={`btn btn-sm ${supplyDays === d ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setSupplyDays(d)}>{d}д</button>
                        ))}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <span style={{ fontSize: 11, opacity: 0.6, whiteSpace: 'nowrap' }}>Анализ:</span>
                        {[7, 14, 30].map(d => (
                            <button key={d} className={`btn btn-sm ${analysisDays === d ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setAnalysisDays(d)}>{d}д</button>
                        ))}
                    </div>
                    <button className="btn btn-sm btn-secondary" onClick={handleExport} title="Экспорт в Excel">📥 Excel</button>
                </div>
            </div>

            {/* Table */}
            {data && sortedArticles.length > 0 ? (
                <div className="glass-card" style={{ overflowX: 'auto', padding: 0 }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                        <thead>
                            <tr style={{ background: 'var(--color-bg)' }}>
                                <th style={thStickyStyle} onClick={() => handleSort('vendor_code')}>
                                    Артикул{sortIcon('vendor_code')}
                                </th>
                                <th style={thStyle} onClick={() => handleSort('total_need')}>
                                    Потребность{sortIcon('total_need')}
                                </th>
                                {(data.warehouses || []).map((wh: any) => (
                                    <th key={wh.name} style={thStyle} onClick={() => handleSort(wh.name)}>
                                        {wh.name.length > 16 ? wh.name.slice(0, 16) + '…' : wh.name}
                                        {sortIcon(wh.name)}
                                    </th>
                                ))}
                            </tr>
                            <tr style={{ background: 'rgba(var(--color-primary-rgb, 59,130,246), 0.06)', fontWeight: 700 }}>
                                <td style={{ ...tdStickyStyle, fontWeight: 700, background: 'rgba(var(--color-primary-rgb, 59,130,246), 0.06)', borderBottom: '2px solid var(--color-border)' }}>ИТОГО</td>
                                <td style={{ ...tdStyle, fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>{grandTotal > 0 ? formatNumber(grandTotal, 0) : '—'}</td>
                                {(data.warehouses || []).map((wh: any) => {
                                    const t = getWhTotal(wh.name);
                                    return (
                                        <td key={wh.name} style={{ ...tdStyle, fontWeight: 700, borderBottom: '2px solid var(--color-border)', color: t > 0 ? '#ef4444' : 'var(--color-text-muted)' }}>
                                            {t > 0 ? formatNumber(t, 0) : '—'}
                                        </td>
                                    );
                                })}
                            </tr>
                        </thead>
                        <tbody>
                            {sortedArticles.map((a: any) => {
                                const totalNeed = getArticleNeed(a);
                                return (
                                    <tr key={a.nm_id} style={{ transition: 'background 0.15s' }}
                                        onMouseEnter={e => (e.currentTarget.style.background = 'rgba(var(--color-primary-rgb, 59,130,246), 0.03)')}
                                        onMouseLeave={e => (e.currentTarget.style.background = '')}>
                                        <td style={tdStickyStyle}>
                                            <div>{a.vendor_code}</div>
                                            {(a.brand || a.subject) && (
                                                <div style={{ fontSize: 10, opacity: 0.5, fontWeight: 400 }}>
                                                    {[a.brand, a.subject].filter(Boolean).join(' · ')}
                                                </div>
                                            )}
                                        </td>
                                        <td style={{ ...tdStyle, fontWeight: 700, color: totalNeed > 0 ? '#ef4444' : 'var(--color-text-muted)' }}>
                                            {totalNeed > 0 ? formatNumber(totalNeed, 0) : '—'}
                                        </td>
                                        {(data.warehouses || []).map((wh: any) => {
                                            const need = getArticleNeed(a, wh.name);
                                            return (
                                                <td key={wh.name} style={{
                                                    ...tdStyle,
                                                    background: need > 0 ? 'rgba(239,68,68,0.08)' : undefined,
                                                    color: need > 0 ? '#ef4444' : 'var(--color-text-muted)',
                                                    fontWeight: need > 0 ? 600 : 400,
                                                }}>
                                                    {need > 0 ? formatNumber(need, 0) : '—'}
                                                </td>
                                            );
                                        })}
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            ) : (
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-text">{data ? 'Нет артикулов по выбранным фильтрам' : 'Нет данных. Сначала синхронизируйте склады (вкладка «По складам»).'}</div>
                    </div>
                </div>
            )}
        </div>
    );
}
