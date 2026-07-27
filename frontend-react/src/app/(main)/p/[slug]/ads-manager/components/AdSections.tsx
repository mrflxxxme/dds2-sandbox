'use client';
import React, { useState, useCallback, useEffect } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import { cThStyle as thStyle, cThLeft as thLeft, tdStyle, tdLeft, mskTime } from './adsShared';
import WbThumb from '@/components/WbThumb';
import { IcCopy } from './icons';
import { useToast } from './Toasts';
import type { AdTabProduct, FunnelSkuRow, AdsBudgetGap, AdsBudgetGapHistory } from '@/types/api';

type View = 'high-drr' | 'no-ads' | 'no-organic' | 'budget-gap';
type SortDir = 'asc' | 'desc';

// Decimal-поля бэка приходят строкой → перед formatNumber оборачиваем в Number()
const num = (x: unknown) => Number(x) || 0;
const money = (x: unknown) => formatNumber(num(x), 1);          // деньги/дроби — 1 знак
const int = (x: unknown) => formatNumber(num(x), 0);            // штуки/счётчики — 0 знаков
const pct = (x: unknown) => formatNumber(num(x), 1) + '%';      // проценты — 1 знак

// Дробный час МСК (0..24) → «ЧЧ:ММ» (типичный час остановки в «Нехватке бюджета»)
const hourMsk = (h: number | null | undefined) => {
    if (h == null) return '—';
    const hh = Math.floor(h);
    let mm = Math.round((h - hh) * 60);
    let H = hh;
    if (mm === 60) { H += 1; mm = 0; }
    return `${String(H).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
};

// Доля рекл. кликов от всех переходов (для «Нет органики»)
const adShare = (r: FunnelSkuRow) => {
    const opens = num(r.open_card);
    return opens > 0 ? (num(r.adv_clicks) / opens) * 100 : 0;
};

const wbUrl = (nmId: number) => `https://www.wildberries.ru/catalog/${nmId}/detail.aspx`;

export default function AdSections({ view, dateFrom, dateTo, brand, subject, campNm, selectedNms, onProductClick, campMeta, nmsWithCampaigns }: {
    view: View;
    dateFrom: string; dateTo: string; brand: string; subject: string;
    campNm?: Record<number, number>;  // campaign_id → nm_id (миниатюра товара в «Нехватке бюджета»)
    selectedNms?: Map<number, boolean>;  // выбранные товары (nm → есть ли кампания) — подсветка строк
    onProductClick?: (nmId: number, hasCampaign: boolean) => void;  // клик по товару (создать / в выбор)
    campMeta?: Record<number, { subjects: string[]; brands: string[] }>;  // предмет/бренд кампании — фильтр «Нехватки бюджета»
    nmsWithCampaigns?: Set<number>;  // nm с незавершённой кампанией — честный hasCampaign при клике
}) {
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const [drrRows, setDrrRows] = useState<AdTabProduct[]>([]);
    const [noAdsRows, setNoAdsRows] = useState<FunnelSkuRow[]>([]);
    const [organicRows, setOrganicRows] = useState<FunnelSkuRow[]>([]);
    const [gaps, setGaps] = useState<AdsBudgetGap[]>([]);
    // История недобора по дням (раскрытие строки): ленивая загрузка + кэш по campaign_id
    const [histOpen, setHistOpen] = useState<Set<number>>(() => new Set());
    const [histData, setHistData] = useState<Record<number, AdsBudgetGapHistory>>({});
    const [histLoading, setHistLoading] = useState<Set<number>>(() => new Set());
    const [histError, setHistError] = useState<Record<number, string>>({});

    // Пороги-фильтры (дефолты — как в старом коде)
    const [drrThreshold, setDrrThreshold] = useState(7);
    const [drrSort, setDrrSort] = useState<{ field: keyof AdTabProduct; dir: SortDir }>({ field: 'drr', dir: 'desc' });
    const [turnoverDays, setTurnoverDays] = useState(30);
    const [organicThreshold, setOrganicThreshold] = useState(50);
    const [minOpensOrganic, setMinOpensOrganic] = useState(300);
    // Копирование артикула (nm_id) из ячейки товара — единый тост, как в списке кампаний
    const toast = useToast();
    const copyNm = (nmId: number) => {
        navigator.clipboard?.writeText(String(nmId))
            .then(() => toast.success(`Артикул ${nmId} скопирован`))
            .catch(() => toast.error('Не удалось скопировать — буфер обмена недоступен'));
    };

    // Загрузка данных нужного раздела. AbortController — защита от двойного монтирования StrictMode.
    const load = useCallback(async (signal: AbortSignal) => {
        setLoading(true); setError('');
        try {
            if (view === 'high-drr') {
                const rows = await api.getAdTab({ date_from: dateFrom, date_to: dateTo, brand: brand || undefined, subject: subject || undefined });
                if (signal.aborted) return;
                setDrrRows(rows);
            } else if (view === 'no-ads') {
                const res = await api.getFunnelData({ date_from: dateFrom, date_to: dateTo, brand: brand || undefined, subject: subject || undefined, group_by: 'sku', extended: true });
                if (signal.aborted) return;
                setNoAdsRows((res.data || []) as FunnelSkuRow[]);
            } else if (view === 'no-organic') {
                const res = await api.getFunnelData({ date_from: dateFrom, date_to: dateTo, brand: brand || undefined, subject: subject || undefined, group_by: 'sku' });
                if (signal.aborted) return;
                setOrganicRows((res.data || []) as FunnelSkuRow[]);
            } else {
                const rows = await api.getAdsBudgetGaps();
                if (signal.aborted) return;
                setGaps(rows);
            }
        } catch (e) {
            if (signal.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (!signal.aborted) setLoading(false);
        }
    }, [view, dateFrom, dateTo, brand, subject]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    // Ленивая загрузка истории недобора по дням для раскрытой кампании
    const loadHistory = useCallback(async (cid: number) => {
        setHistLoading(prev => new Set(prev).add(cid));
        try {
            const h = await api.getBudgetGapHistory(cid, 30);
            setHistData(prev => ({ ...prev, [cid]: h }));
            setHistError(prev => { const n = { ...prev }; delete n[cid]; return n; });
        } catch (e) {
            setHistError(prev => ({ ...prev, [cid]: e instanceof Error ? e.message : 'Ошибка загрузки истории' }));
        } finally {
            setHistLoading(prev => { const n = new Set(prev); n.delete(cid); return n; });
        }
    }, []);
    const toggleHist = (cid: number) => {
        setHistOpen(prev => {
            const n = new Set(prev);
            if (n.has(cid)) { n.delete(cid); return n; }
            n.add(cid);
            if (!histData[cid] && !histLoading.has(cid)) loadHistory(cid);
            return n;
        });
    };

    // ─── Клиентские фильтры/сортировки (как в старом коде) ───

    // Высокий ДРР: ДРР выше N% + сортировка по клику на заголовок
    // drr === null — расход без заказов (ДРР = ∞): всегда «выше порога» и сортируется поверх любых чисел
    const drrVal = (r: AdTabProduct, field: keyof AdTabProduct) =>
        field === 'drr' && r.drr === null ? Infinity : num(r[field]);
    const highDrr = drrRows
        .filter(r => (r.drr === null ? num(r.adv_sum) > 0 : num(r.drr) > drrThreshold))
        .sort((a, b) => {
            const av = drrVal(a, drrSort.field);
            const bv = drrVal(b, drrSort.field);
            return drrSort.dir === 'asc' ? av - bv : bv - av;
        });
    const toggleDrrSort = (field: keyof AdTabProduct) =>
        setDrrSort(prev => ({ field, dir: prev.field === field && prev.dir === 'desc' ? 'asc' : 'desc' }));
    const drrArrow = (field: keyof AdTabProduct) => drrSort.field === field ? (drrSort.dir === 'desc' ? ' ↓' : ' ↑') : '';

    // Не работает реклама: сток > N дней, расход рекл. = 0, есть остаток; сорт по себест. остатков ↓
    const noAds = noAdsRows
        .filter(r => num(r.adv_sum) === 0 && r.stock_days_left != null && num(r.stock_days_left) > turnoverDays
            && (num(r.wb_stock_qty) + num(r.own_stock_qty)) > 0)
        .sort((a, b) => (num(b.wb_stock_cost) + num(b.own_stock_cost)) - (num(a.wb_stock_cost) + num(a.own_stock_cost)));

    // Нет органики: доля рекл. кликов ≥ порога, с ощутимым трафиком; сорт по доле ↓
    const noOrganic = organicRows
        .filter(r => num(r.open_card) >= minOpensOrganic && adShare(r) >= organicThreshold)
        .sort((a, b) => adShare(b) - adShare(a));

    // Нехватка бюджета: клиентский фильтр по предмету/бренду (эндпоинт их не принимает)
    const gapsFiltered = gaps.filter(g => {
        const meta = campMeta?.[g.campaign_id];
        if (brand && !meta?.brands.includes(brand)) return false;
        if (subject && !meta?.subjects.includes(subject)) return false;
        return true;
    });

    // ─── Excel-экспорт (то, что видно — с учётом фильтров и сортировки) ───
    const exportHighDrr = () => exportToExcel(highDrr.map(r => ({
        'Артикул': r.vendor_code || '', 'nm_id': r.nm_id, 'Бренд': r.brand || '', 'Категория': r.subject || '',
        'ДРР %': r.drr === null ? '∞ (заказов нет)' : num(r.drr), 'Расход ₽': num(r.adv_sum), 'Заказы ₽': num(r.orders_sum_rub),
        'CTR %': num(r.ctr), 'CPC ₽': num(r.cpc), 'Кампаний': num(r.active_campaigns),
    })), `ads-high-drr_${dateFrom}_${dateTo}`);
    const exportNoAds = () => exportToExcel(noAds.map(r => ({
        'Артикул': r.vendor_code || '', 'nm_id': r.nm_id, 'Бренд': r.brand || '', 'Категория': r.subject || '',
        'Остаток WB, шт': num(r.wb_stock_qty), 'Наши склады, шт': num(r.own_stock_qty),
        'Себест. остатков ₽': num(r.wb_stock_cost) + num(r.own_stock_cost),
        'Хватит, дн': num(r.stock_days_left), 'Заказы за период ₽': num(r.orders_sum_rub),
    })), `ads-no-ads_${dateFrom}_${dateTo}`);
    const exportNoOrganic = () => exportToExcel(noOrganic.map(r => ({
        'Артикул': r.vendor_code || '', 'nm_id': r.nm_id, 'Бренд': r.brand || '', 'Категория': r.subject || '',
        'Рекл. клики %': Math.round(adShare(r) * 100) / 100, 'Переходы': num(r.open_card), 'Рекл. клики': num(r.adv_clicks),
        'Заказы ₽': num(r.orders_sum_rub), 'Расход рекл. ₽': num(r.adv_sum), 'ДРР %': num(r.drr), 'В заказ %': num(r.cart_to_order_pct),
    })), `ads-no-organic_${dateFrom}_${dateTo}`);
    const exportGaps = () => exportToExcel(gapsFiltered.map(g => ({
        'Кампания': g.name || `#${g.campaign_id}`, 'ID': g.campaign_id, 'Тип': (g.campaign_type || '').toUpperCase(),
        'Статус': g.predicted ? 'Прогноз' : 'Факт',
        'Потрачено сегодня ₽': num(g.spend_today),
        'Остановилась в (МСК)': g.predicted ? `≈ ${hourMsk(g.typical_stop_hour)}` : mskTime(g.ran_out_at),
        'Кончается дней': num(g.active_days) > 0 ? `${int(g.runout_days)}/${int(g.active_days)}` : '',
        'Полный день ₽': g.potential_daily != null ? num(g.potential_daily) : '',
        'Недобор ₽': g.potential_daily != null ? num(g.needed_potential) : num(g.needed_till_midnight),
        'Скорость ₽/час': num(g.burn_rate), 'Товаров': g.nm_count,
    })), 'ads-budget-gap');

    // Кол-во показанных строк текущего раздела
    const shownCount = view === 'high-drr' ? highDrr.length
        : view === 'no-ads' ? noAds.length
        : view === 'no-organic' ? noOrganic.length
        : gapsFiltered.length;

    const inputStyle: React.CSSProperties = { background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 8px', fontSize: 13, color: 'var(--color-text)' };
    const excelBtn = (onClick: () => void, disabled: boolean) => (
        <button className="btn btn-secondary btn-sm" style={{ fontSize: 13 }} onClick={onClick} disabled={disabled}
            title="Выгрузить таблицу в Excel (с учётом фильтров)">📥 Excel</button>
    );

    // Ячейка товара: миниатюра + артикул + бренд/категория + ссылка на карточку WB
    const productCell = (r: { nm_id: number; vendor_code: string | null; brand: string | null; subject: string | null }) => (
        <td style={tdLeft}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <WbThumb nmId={r.nm_id} size={38} />
                <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.vendor_code || r.nm_id}</div>
                    <div style={{ fontSize: 10, color: '#9ca3af', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                        <span>{r.brand || '—'}{r.subject ? ` · ${r.subject}` : ''} · </span>
                        <a href={wbUrl(r.nm_id)} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()} style={{ color: 'var(--color-accent)' }}>#{r.nm_id}</a>
                        <button onClick={e => { e.stopPropagation(); copyNm(r.nm_id); }} title="Скопировать артикул" aria-label="Скопировать артикул"
                            style={{ border: 'none', background: 'none', cursor: 'pointer', padding: 0, display: 'inline-flex', lineHeight: 1, color: '#9ca3af' }}>
                            <IcCopy size={12} />
                        </button>
                    </div>
                </div>
            </div>
        </td>
    );

    const centerMsg = (text: string) => (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>{text}</div>
    );

    // Раскрытая история недобора по дням для одной кампании
    const renderHistory = (cid: number) => {
        if (histLoading.has(cid)) return <div style={{ padding: '12px 46px', fontSize: 12, color: '#9ca3af' }}>Загрузка истории…</div>;
        if (histError[cid]) return <div style={{ padding: '12px 46px', fontSize: 12, color: 'var(--color-danger)' }}>⚠️ {histError[cid]}</div>;
        const h = histData[cid];
        if (!h) return null;
        if (h.days.length === 0) return <div style={{ padding: '12px 46px', fontSize: 12, color: '#9ca3af' }}>Нет данных за {h.window_days} дн</div>;
        return (
            <div style={{ padding: '10px 16px 14px 46px' }}>
                <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 8 }}>
                    Потенциал полного дня: <b>{h.potential_daily != null ? `${money(h.potential_daily)} ₽` : '—'}</b>
                    {h.potential_daily != null ? ` (медиана по ${int(h.full_days)} дн)` : ' (нет данных для оценки)'} · Кончался бюджет: <b>{int(h.days_ran_out)}</b> дн из {int(h.window_days)} · Суммарный недобор: <b style={{ color: 'var(--color-accent)' }}>{money(h.total_shortfall)} ₽</b>
                </div>
                <table style={{ width: '100%', maxWidth: 620, borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead><tr>
                        <th style={{ ...tdLeft, fontWeight: 600, color: '#6b7280' }}>Дата</th>
                        <th style={{ ...tdStyle, fontWeight: 600, color: '#6b7280' }}>Расход ₽</th>
                        <th style={{ ...tdStyle, fontWeight: 600, color: '#6b7280' }}>Кончился в (МСК)</th>
                        <th style={{ ...tdStyle, fontWeight: 600, color: '#6b7280' }}>Недобор ₽</th>
                    </tr></thead>
                    <tbody>
                        {h.days.map(d => (
                            <tr key={d.date} style={{ background: d.ran_out ? '#fff7ed' : undefined }}>
                                <td style={tdLeft}>{formatDate(d.date)}</td>
                                <td style={tdStyle}>{money(d.spend)}</td>
                                <td style={{ ...tdStyle, color: d.ran_out ? '#ef4444' : '#9ca3af', fontWeight: d.ran_out ? 700 : 400 }}>{d.ran_out ? mskTime(d.ran_out_at) : '—'}</td>
                                <td style={{ ...tdStyle, color: d.shortfall > 0 ? 'var(--color-accent)' : '#9ca3af', fontWeight: d.shortfall > 0 ? 600 : 400 }}>{d.shortfall > 0 ? money(d.shortfall) : '—'}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        );
    };

    // ─── Тулбар раздела ───
    const toolbar = (
        <div style={{ padding: '10px 16px', display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap', borderBottom: '1px solid #e5e7eb', background: '#f9fafb', flexShrink: 0 }}>
            {view === 'high-drr' && (
                <>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text-dim)' }}>
                        ДРР выше
                        <input type="number" min={0} step={0.5} value={drrThreshold}
                            onChange={e => setDrrThreshold(num(e.target.value))} style={{ ...inputStyle, width: 64 }} />%
                    </label>
                    {excelBtn(exportHighDrr, highDrr.length === 0)}
                </>
            )}
            {view === 'no-ads' && (
                <>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text-dim)' }}
                        title="Товар попадает в список, если остатка хватит больше чем на N дней при текущем темпе продаж, а расход рекламы за период = 0">
                        Запас более
                        <input type="number" min={1} value={turnoverDays}
                            onChange={e => setTurnoverDays(Math.max(1, num(e.target.value) || 30))} style={{ ...inputStyle, width: 64 }} /> дн
                    </label>
                    {excelBtn(exportNoAds, noAds.length === 0)}
                </>
            )}
            {view === 'no-organic' && (
                <>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text-dim)' }}
                        title="Доля рекламных кликов от всех переходов. ≥50% органика слабеет, ≥60% конверсия в заказ падает вдвое">
                        Доля рекл. кликов ≥
                        <input type="number" min={0} max={100} value={organicThreshold}
                            onChange={e => setOrganicThreshold(Math.min(100, Math.max(0, num(e.target.value))))} style={{ ...inputStyle, width: 60 }} />%
                    </label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text-dim)' }}
                        title="Минимум переходов за период, чтобы отсечь товары без трафика (шум)">
                        Мин. переходов
                        <input type="number" min={0} value={minOpensOrganic}
                            onChange={e => setMinOpensOrganic(Math.max(0, num(e.target.value)))} style={{ ...inputStyle, width: 70 }} />
                    </label>
                    {excelBtn(exportNoOrganic, noOrganic.length === 0)}
                </>
            )}
            {view === 'budget-gap' && excelBtn(exportGaps, gaps.length === 0)}
            {!loading && <span style={{ fontSize: 12, color: 'var(--color-text-dim)', marginLeft: 'auto' }}>Показано: {shownCount}</span>}
        </div>
    );

    const tableStyle: React.CSSProperties = { width: '100%', borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' };

    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
            {toolbar}
            {error && (
                <div style={{ padding: '10px 16px', flexShrink: 0 }}>
                    <span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {error}</span>
                </div>
            )}
            <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'auto' }}>
                {loading ? <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div> : (
                    <>
                        {/* ─── Высокий ДРР ─── */}
                        {view === 'high-drr' && (
                            highDrr.length === 0 ? centerMsg(`Нет товаров с ДРР выше ${drrThreshold}% за период 🎉`) : (
                                <table className="data-table" style={tableStyle}>
                                    <thead><tr>
                                        <th style={thLeft}>Товар</th>
                                        <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleDrrSort('drr')}>ДРР{drrArrow('drr')}</th>
                                        <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleDrrSort('adv_sum')}>Расход ₽{drrArrow('adv_sum')}</th>
                                        <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleDrrSort('orders_sum_rub')}>Заказы ₽{drrArrow('orders_sum_rub')}</th>
                                        <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleDrrSort('ctr')}>CTR{drrArrow('ctr')}</th>
                                        <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleDrrSort('cpc')}>CPC{drrArrow('cpc')}</th>
                                        <th style={{ ...thStyle, cursor: 'pointer' }} onClick={() => toggleDrrSort('active_campaigns')}>Кампаний{drrArrow('active_campaigns')}</th>
                                    </tr></thead>
                                    <tbody>
                                        {highDrr.map(r => {
                                            const hasCamp = num(r.active_campaigns) > 0;
                                            const sel = selectedNms?.has(r.nm_id);
                                            return (
                                            <tr key={r.nm_id} onClick={onProductClick ? () => onProductClick(r.nm_id, hasCamp) : undefined}
                                                title={onProductClick ? (hasCamp ? 'Клик — добавить/убрать товар из выбора, затем «Подтвердить выбор»' : 'Нет кампании — клик откроет создание кампании по этому товару') : undefined}
                                                style={{ cursor: onProductClick ? 'pointer' : undefined, background: sel ? '#dbeafe' : undefined }}>
                                                {productCell(r)}
                                                <td style={{ ...tdStyle, fontWeight: 700, color: r.drr === null || num(r.drr) > 30 ? '#ef4444' : '#f59e0b' }}
                                                    title={r.drr === null ? 'Расход есть, заказов нет — ДРР бесконечен' : undefined}>
                                                    {r.drr === null ? '∞' : pct(r.drr)}</td>
                                                <td style={{ ...tdStyle, color: '#f97316' }}>{money(r.adv_sum)}</td>
                                                <td style={tdStyle}>{money(r.orders_sum_rub)}</td>
                                                <td style={tdStyle}>{pct(r.ctr)}</td>
                                                <td style={tdStyle}>{money(r.cpc)}</td>
                                                <td style={hasCamp ? tdStyle : { ...tdStyle, color: 'var(--color-accent)', fontWeight: 600 }}>
                                                    {hasCamp ? int(r.active_campaigns) : '+ создать'}
                                                </td>
                                            </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            )
                        )}

                        {/* ─── Не работает реклама ─── */}
                        {view === 'no-ads' && (
                            noAds.length === 0 ? centerMsg('Все залежавшиеся товары под рекламой 🎉') : (
                                <table className="data-table" style={tableStyle}>
                                    <thead><tr>
                                        <th style={thLeft}>Товар</th>
                                        <th style={thStyle}>Остаток WB, шт</th>
                                        <th style={thStyle}>Наши склады, шт</th>
                                        <th style={thStyle}>Себест. остатков ₽</th>
                                        <th style={thStyle}>Хватит, дн</th>
                                        <th style={thStyle}>Заказы за период ₽</th>
                                        <th style={thStyle}>Расход рекл. ₽</th>
                                    </tr></thead>
                                    <tbody>
                                        {noAds.map(r => {
                                            const sel = selectedNms?.has(r.nm_id);
                                            return (
                                            <tr key={r.nm_id} onClick={onProductClick ? () => onProductClick(r.nm_id, nmsWithCampaigns?.has(r.nm_id) ?? false) : undefined}
                                                title={onProductClick ? 'Клик — добавить/убрать товар из выбора, затем выбрать тип и создать сверху' : undefined}
                                                style={{ cursor: onProductClick ? 'pointer' : undefined, background: sel ? '#dbeafe' : undefined }}>
                                                {productCell(r)}
                                                <td style={tdStyle}>{int(r.wb_stock_qty)}</td>
                                                <td style={tdStyle}>{int(r.own_stock_qty)}</td>
                                                <td style={{ ...tdStyle, fontWeight: 600 }}>{money(num(r.wb_stock_cost) + num(r.own_stock_cost))}</td>
                                                <td style={{ ...tdStyle, fontWeight: 600, color: '#ef4444' }}>{r.stock_days_left != null && num(r.stock_days_left) >= 999 ? '∞' : int(r.stock_days_left)}</td>
                                                <td style={tdStyle}>{money(r.orders_sum_rub)}</td>
                                                <td style={{ ...tdStyle, color: '#9ca3af' }}>0</td>
                                            </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            )
                        )}

                        {/* ─── Нет органики ─── */}
                        {view === 'no-organic' && (
                            noOrganic.length === 0 ? centerMsg(`Нет товаров с долей рекл. кликов ≥ ${organicThreshold}% 🎉`) : (
                                <table className="data-table" style={tableStyle}>
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
                                            const sel = selectedNms?.has(r.nm_id);
                                            return (
                                                <tr key={r.nm_id} onClick={onProductClick ? () => onProductClick(r.nm_id, nmsWithCampaigns?.has(r.nm_id) ?? true) : undefined}
                                                    title={onProductClick ? 'Клик — добавить/убрать товар из выбора, затем «Подтвердить выбор»' : undefined}
                                                    style={{ cursor: onProductClick ? 'pointer' : undefined, background: sel ? '#dbeafe' : undefined }}>
                                                    {productCell(r)}
                                                    <td style={{ ...tdStyle, fontWeight: 700, color: share >= 60 ? '#ef4444' : '#f59e0b' }}>{pct(share)}</td>
                                                    <td style={tdStyle}>{int(r.open_card)}</td>
                                                    <td style={tdStyle}>{int(r.adv_clicks)}</td>
                                                    <td style={tdStyle}>{money(r.orders_sum_rub)}</td>
                                                    <td style={{ ...tdStyle, color: '#f97316' }}>{money(r.adv_sum)}</td>
                                                    <td style={{ ...tdStyle, color: num(r.drr) > 15 ? '#ef4444' : '#374151' }}>{pct(r.drr)}</td>
                                                    <td style={tdStyle}>{pct(r.cart_to_order_pct)}</td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            )
                        )}

                        {/* ─── Нехватка бюджета ─── */}
                        {view === 'budget-gap' && (
                            gapsFiltered.length === 0 ? centerMsg('Сегодня бюджет нигде не кончался, хронических рисков нет 🎉') : (
                                <table className="data-table" style={tableStyle}>
                                    <thead><tr>
                                        <th style={{ ...thStyle, width: 28, padding: '5px 4px' }} />
                                        <th style={thLeft}>Кампания</th>
                                        <th style={thStyle}>Потрачено сегодня ₽</th>
                                        <th style={thStyle} title="Факт — во сколько кончился бюджет сегодня (МСК). Прогноз — типичный час остановки по истории (медиана дней-остановок)">Остановилась в (МСК)</th>
                                        <th style={thStyle} title="Как часто кампания упиралась в бюджет: дней-остановок / дней с расходом за окно">Кончается, дн</th>
                                        <th style={thStyle} title="Реальный спрос: медиана экстраполяции дней-остановок (скорость расхода × 24 ч)">Полный день ₽</th>
                                        <th style={thStyle} title="Сколько долить сегодня, чтобы выйти на потенциал полного дня">Недобор ₽</th>
                                        <th style={thStyle}>Товаров</th>
                                    </tr></thead>
                                    <tbody>
                                        {gapsFiltered.map(g => {
                                            const nm = campNm?.[g.campaign_id];
                                            const sel = nm != null && selectedNms?.has(nm);
                                            const clickable = onProductClick && nm != null;
                                            const open = histOpen.has(g.campaign_id);
                                            const hasPot = g.potential_daily != null;
                                            // Недобор: по потенциалу полного дня, иначе фолбэк на линейную оценку
                                            const nedobor = hasPot ? num(g.needed_potential) : num(g.needed_till_midnight);
                                            const roundedUp = hasPot && num(g.raw_needed_potential) > 0 && num(g.raw_needed_potential) < num(g.min_topup);
                                            return (
                                            <React.Fragment key={g.campaign_id}>
                                            <tr onClick={clickable ? () => onProductClick!(nm!, true) : undefined}
                                                title={clickable ? 'Клик — добавить/убрать товар из выбора, затем «Подтвердить выбор»' : undefined}
                                                style={{ cursor: clickable ? 'pointer' : undefined, background: sel ? '#dbeafe' : undefined }}>
                                                <td style={{ ...tdStyle, textAlign: 'center', cursor: 'pointer', color: '#9ca3af' }}
                                                    onClick={e => { e.stopPropagation(); toggleHist(g.campaign_id); }}
                                                    title="История недобора по дням">
                                                    <span style={{ display: 'inline-block', transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }}>▶</span>
                                                </td>
                                                <td style={tdLeft}>
                                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                                        {campNm?.[g.campaign_id] != null && <WbThumb nmId={campNm[g.campaign_id]} size={38} />}
                                                        <div style={{ minWidth: 0 }}>
                                                            <div style={{ fontWeight: 600, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                                                {g.name || `#${g.campaign_id}`}
                                                                {g.predicted && (
                                                                    <span style={{ marginLeft: 6, fontSize: 9.5, fontWeight: 700, color: '#b45309', background: '#fef3c7', borderRadius: 24, padding: '1px 7px', whiteSpace: 'nowrap' }}
                                                                        title="Сегодня бюджет ещё есть, но по истории кампания регулярно кончается до конца дня — риск">прогноз</span>
                                                                )}
                                                            </div>
                                                            <div style={{ fontSize: 10, color: '#9ca3af' }}>#{g.campaign_id} · {g.campaign_type || '—'}</div>
                                                        </div>
                                                    </div>
                                                </td>
                                                <td style={tdStyle}>{money(g.spend_today)}</td>
                                                {g.predicted ? (
                                                    <td style={{ ...tdStyle, fontWeight: 700, color: '#f59e0b' }}
                                                        title={`Обычно кончается около ${hourMsk(g.typical_stop_hour)} МСК (медиана по ${int(g.runout_days)} дн-остановок)`}>
                                                        ≈ {hourMsk(g.typical_stop_hour)}
                                                    </td>
                                                ) : (
                                                    <td style={{ ...tdStyle, fontWeight: 700, color: '#ef4444' }}>{mskTime(g.ran_out_at)}</td>
                                                )}
                                                <td style={tdStyle} title={`Кончался бюджет ${int(g.runout_days)} из ${int(g.active_days)} дней с расходом за ${int(g.window_days)} дн`}>
                                                    {num(g.active_days) > 0 ? `${int(g.runout_days)}/${int(g.active_days)}` : '—'}
                                                </td>
                                                <td style={tdStyle} title={hasPot ? `Медиана экстраполяции по ${int(g.full_days)} дн (окно ${int(g.window_days)} дн)` : `Нет данных за ${int(g.window_days)} дн — оценить потенциал нечем (линейная скорость ${money(g.burn_rate)} ₽/час)`}>
                                                    {hasPot ? money(g.potential_daily) : '—'}
                                                </td>
                                                <td style={{ ...tdStyle, fontWeight: 700, color: nedobor > 0 ? 'var(--color-accent)' : '#9ca3af' }}
                                                    title={hasPot
                                                        ? (roundedUp ? `Нужно ${money(g.raw_needed_potential)} ₽, округлено вверх до минимума пополнения WB ${money(g.min_topup)} ₽` : `Потенциал ${money(g.potential_daily)} ₽/день − потрачено ${money(g.spend_today)} ₽`)
                                                        : `Полных дней нет — линейная оценка по скорости ${money(g.burn_rate)} ₽/час`}>
                                                    {nedobor > 0 ? money(nedobor) : '—'}{roundedUp ? ' *' : ''}{!hasPot && nedobor > 0 ? ' ~' : ''}
                                                </td>
                                                <td style={tdStyle}>{int(g.nm_count)}</td>
                                            </tr>
                                            {open && (
                                                <tr>
                                                    <td colSpan={8} style={{ padding: 0, background: '#fafafa', borderBottom: '1px solid #e8eaed' }}>
                                                        {renderHistory(g.campaign_id)}
                                                    </td>
                                                </tr>
                                            )}
                                            </React.Fragment>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            )
                        )}
                    </>
                )}
            </div>
        </div>
    );
}
