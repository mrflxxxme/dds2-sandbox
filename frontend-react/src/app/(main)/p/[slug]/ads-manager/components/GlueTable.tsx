'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { exportToExcel } from '@/lib/utils';
import type { AdGlueRow, AdTabProduct, AdCampaign } from '@/types/api';
import WbThumb from './WbThumb';
import InfoTip from './InfoTip';
import { IcColumns, IcDownload, IcSearch } from './icons';
import { fmt, fmtPct, tdStyle, tdLeft, cThStyle, cThLeft, STATUS_BADGE } from './adsShared';

const COLS_LS_KEY = 'ads_glue_cols';
// Миниатюр в карусели: 4×28px ≈ 125px, чтобы под название осталось ~200px — столько же,
// сколько под название кампании в соседней вкладке
const THUMBS_SHOWN = 4;

type Col = { key: string; label: string; title?: string; w: number; blockStart?: boolean };
const COLS: Col[] = [
    { key: 'spend', label: 'Затраты ₽', title: 'Расход на рекламу по всем артикулам склейки за период', w: 92 },
    { key: 'views', label: 'Показы', w: 84 },
    { key: 'ctr', label: 'CTR', title: 'Клики / показы по сумме склейки, а не среднее по артикулам', w: 60 },
    { key: 'clicks', label: 'Клики', w: 72 },
    { key: 'orders', label: 'Заказы', blockStart: true, w: 72 },
    { key: 'orders_sum', label: 'Сумма заказов ₽', w: 110 },
    { key: 'drr', label: 'ДРР', title: 'Затраты склейки / сумма её заказов. «∞» — расход есть, заказов нет', w: 64 },
    { key: 'cpc', label: 'CPC ₽', title: 'Стоимость клика: затраты склейки / её клики', w: 64 },
    { key: 'budget', label: 'Остаток бюджета ₽', title: 'Сумма остатков бюджета кампаний склейки. Кампания на нескольких артикулах учтена один раз', blockStart: true, w: 110 },
    { key: 'types', label: 'Тип кампании', title: 'Типы оплаты кампаний, крутящих склейку', w: 92 },
    { key: 'camps', label: 'Кампаний', title: 'Всего кампаний / из них активных', w: 78 },
    { key: 'stock', label: 'Остаток шт', blockStart: true, w: 82 },
];
const BLOCK_DIVIDER = '1px solid rgba(17,24,39,0.08)';
// Геометрия ячеек — один в один с таблицей кампаний (page.tsx)
const TH_PAD = { padding: '5px 5px', whiteSpace: 'normal' as const, lineHeight: 1.15 };
const TD_PAD = { padding: '3px 5px', overflow: 'hidden' as const, textOverflow: 'ellipsis' as const };

type SortDir = 'asc' | 'desc';
type SortState = { key: string; dir: SortDir } | null;

/** Числовое значение строки (склейки или артикула) для сортировки по ключу колонки. */
function sortVal(r: AdGlueRow | AdTabProduct, key: string): number {
    const g = r as AdGlueRow;
    switch (key) {
        case 'spend': return r.adv_sum;
        case 'views': return r.adv_views;
        case 'ctr': return r.ctr;
        case 'clicks': return r.adv_clicks;
        case 'orders': return r.orders_count;
        case 'orders_sum': return r.orders_sum_rub;
        // ДРР null = расход без заказов, то есть бесконечный: по убыванию такие идут первыми
        case 'drr': return r.drr == null ? Number.POSITIVE_INFINITY : r.drr;
        case 'cpc': return r.cpc;
        case 'stock': return r.stock_qty;
        case 'budget': return g.budget_total ?? 0;
        case 'camps': return g.campaign_count ?? (r as AdTabProduct).campaigns?.length ?? 0;
        case 'types': return g.campaign_types?.length ?? 0;
        case 'name': return g.product_count ?? 1;
        default: return 0;
    }
}

/** ДРР: null от бэка = расход без заказов, показываем «∞» и красим тревожно. */
function DrrCell({ drr }: { drr: number | null }) {
    if (drr == null) return <span style={{ color: 'var(--color-danger)', fontWeight: 700 }} title="Расход есть, заказов нет">∞</span>;
    const color = drr > 20 ? 'var(--color-danger)' : drr > 10 ? 'var(--color-warning)' : undefined;
    return <span style={{ color, fontWeight: color ? 600 : undefined }}>{fmtPct(drr)}</span>;
}

/** Карусель миниатюр артикулов склейки — как карточка-склейка в выдаче WB. */
function GlueThumbs({ nmIds }: { nmIds: number[] }) {
    const shown = nmIds.slice(0, THUMBS_SHOWN);
    const rest = nmIds.length - shown.length;
    return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, flexShrink: 0 }}>
            {shown.map(nm => <WbThumb key={nm} nmId={nm} size={36} rounded={6} />)}
            {rest > 0 && (
                <span title={`Ещё ${rest} артикулов в склейке`}
                    style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', background: '#f3f4f6', border: '1px solid #e5e7eb', borderRadius: 6, padding: '0 5px', lineHeight: '36px', height: 36 }}>
                    +{rest}
                </span>
            )}
        </span>
    );
}

/** Чекбокс с третьим состоянием: часть товаров склейки выбрана. */
function TriCheckbox({ checked, indeterminate, onChange, title }: {
    checked: boolean; indeterminate: boolean; onChange: () => void; title: string;
}) {
    const ref = React.useRef<HTMLInputElement>(null);
    // indeterminate выставляется только из JS — атрибута для него в HTML нет
    useEffect(() => { if (ref.current) ref.current.indeterminate = indeterminate; }, [indeterminate]);
    return (
        <input ref={ref} type="checkbox" checked={checked} title={title}
            onChange={onChange} onClick={e => e.stopPropagation()}
            style={{ cursor: 'pointer', width: 14, height: 14 }} />
    );
}

export default function GlueTable({ slug, dateFrom, dateTo, brand, subject, article, selectedNms, onProductClick, onToggleGlue, nmsWithCampaigns }: {
    slug: string;
    dateFrom: string;
    dateTo: string;
    brand: string;
    subject: string;
    /** nm_id из фильтра «Артикул»: оставляем склейки, содержащие этот артикул */
    article: string;
    /** nm_id → есть ли у товара незавершённая кампания; общий выбор на весь раздел */
    selectedNms: Map<number, boolean>;
    onProductClick: (nmId: number, hasCampaign: boolean) => void;
    onToggleGlue: (nmIds: number[], select: boolean) => void;
    nmsWithCampaigns: Set<number>;
}) {
    const [rows, setRows] = useState<AdGlueRow[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [search, setSearch] = useState('');
    // Раскрытие: ключ склейки → развёрнута; отдельно раскрытые артикулы (третий уровень)
    const [openGlue, setOpenGlue] = useState<Set<string>>(() => new Set());
    const [openNm, setOpenNm] = useState<Set<number>>(() => new Set());
    // Первый клик по колонке — сразу по убыванию (сценарий «где больше всего тратим»),
    // второй — по возрастанию, третий — снимает сортировку (порядок бэка: по затратам)
    const [sort, setSort] = useState<SortState>(null);
    const [colsMenu, setColsMenu] = useState(false);
    const [visibleCols, setVisibleCols] = useState<Set<string>>(() => new Set(COLS.map(c => c.key)));

    useEffect(() => {
        try {
            const raw = localStorage.getItem(COLS_LS_KEY);
            if (raw) setVisibleCols(new Set<string>(JSON.parse(raw)));
        } catch { /* битый JSON / SSR — игнор */ }
    }, []);
    const toggleCol = useCallback((key: string) => {
        setVisibleCols(prev => {
            const next = new Set(prev);
            if (next.has(key)) next.delete(key); else next.add(key);
            try { localStorage.setItem(COLS_LS_KEY, JSON.stringify([...next])); } catch { /* quota — игнор */ }
            return next;
        });
    }, []);

    // cancelled-флаг обязателен: StrictMode монтирует эффект дважды, и ответ первого
    // (уже размонтированного) прогона иначе перезаписывает состояние второго
    useEffect(() => {
        let cancelled = false;
        (async () => {
            setLoading(true);
            setError('');
            try {
                const data = await api.getAdGlue({ date_from: dateFrom, date_to: dateTo, brand, subject });
                if (!cancelled) setRows(data);
            } catch (e) {
                if (!cancelled) setError(e instanceof Error ? e.message : 'Не удалось загрузить склейки');
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [dateFrom, dateTo, brand, subject]);

    const glueKey = (r: AdGlueRow) => (r.is_glue ? `i${r.imt_id}` : `n${r.nm_ids[0]}`);

    const filtered = useMemo(() => {
        const q = search.trim().toLowerCase();
        const nm = article ? Number(article) : 0;
        const hits = rows.filter(r => {
            if (nm && !r.nm_ids.includes(nm)) return false;
            if (!q) return true;
            if (r.glue_name.toLowerCase().includes(q)) return true;
            if (String(r.imt_id ?? '').includes(q)) return true;
            return r.children.some(c => String(c.nm_id).includes(q) || (c.vendor_code || '').toLowerCase().includes(q));
        });
        if (!sort) return hits;
        const k = sort.dir === 'desc' ? -1 : 1;
        // Сортируем и состав склейки тем же ключом — иначе внутри остаётся порядок по затратам
        // и раскрытая склейка противоречит колонке, по которой отсортирована таблица
        return hits
            .map(r => ({ ...r, children: [...r.children].sort((a, b) => k * (sortVal(a, sort.key) - sortVal(b, sort.key))) }))
            .sort((a, b) => {
                if (sort.key === 'name') return k * a.glue_name.localeCompare(b.glue_name, 'ru');
                return k * (sortVal(a, sort.key) - sortVal(b, sort.key));
            });
    }, [rows, search, article, sort]);

    const totals = useMemo(() => ({
        spend: filtered.reduce((s, r) => s + r.adv_sum, 0),
        budget: filtered.reduce((s, r) => s + r.budget_total, 0),
        products: filtered.reduce((s, r) => s + r.product_count, 0),
    }), [filtered]);

    const onExport = useCallback(() => {
        exportToExcel(filtered.map(r => ({
            'Склейка': r.glue_name,
            'imt_id': r.imt_id ?? '',
            'Артикулов': r.product_count,
            'Затраты ₽': r.adv_sum,
            'Показы': r.adv_views,
            'Клики': r.adv_clicks,
            'CTR %': r.ctr,
            'Заказы': r.orders_count,
            'Сумма заказов ₽': r.orders_sum_rub,
            'ДРР %': r.drr ?? '∞',
            'CPC ₽': r.cpc,
            'Остаток бюджета ₽': r.budget_total,
            'Тип кампании': r.campaign_types.join(', '),
            'Кампаний': r.campaign_count,
            'Активных кампаний': r.active_campaigns,
            'Остаток шт': r.stock_qty,
            'Артикулы': r.nm_ids.join(', '),
        })), 'Склейки');
    }, [filtered]);

    const cols = COLS.filter(c => visibleCols.has(c.key));

    const onSort = useCallback((key: string) => {
        setSort(prev => (prev?.key !== key ? { key, dir: 'desc' } : prev.dir === 'desc' ? { key, dir: 'asc' } : null));
    }, []);
    const sortMark = (key: string) => (sort?.key !== key ? '' : sort.dir === 'desc' ? ' ↓' : ' ↑');

    /** Ячейка метрики — общая для склейки и артикула (у артикула нет бюджета/типов). */
    const metricCell = (key: string, src: AdGlueRow | AdTabProduct, isGlue: boolean) => {
        switch (key) {
            case 'spend': return fmt(src.adv_sum);
            case 'views': return fmt(src.adv_views);
            case 'ctr': return fmtPct(src.ctr);
            case 'clicks': return fmt(src.adv_clicks);
            case 'orders': return fmt(src.orders_count);
            case 'orders_sum': return fmt(src.orders_sum_rub);
            case 'drr': return <DrrCell drr={src.drr} />;
            case 'cpc': return fmt(src.cpc);
            case 'stock': return fmt(src.stock_qty);
            case 'budget': return isGlue ? fmt((src as AdGlueRow).budget_total) : '—';
            case 'types': return isGlue ? ((src as AdGlueRow).campaign_types.join(', ').toUpperCase() || '—') : '—';
            case 'camps': {
                if (!isGlue) return fmt((src as AdTabProduct).campaigns.length);
                const g = src as AdGlueRow;
                return <span title={`${g.active_campaigns} активных из ${g.campaign_count}`}>{g.campaign_count}<span style={{ color: '#9ca3af' }}> / </span><span style={{ color: 'var(--color-success)' }}>{g.active_campaigns}</span></span>;
            }
            default: return null;
        }
    };

    return (
        <div className="glass-card static" style={{ padding: 0, overflow: 'hidden', flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '10px 16px', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', borderBottom: '1px solid #e5e7eb', background: '#f9fafb', flexShrink: 0 }}>
                <div style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
                    <span style={{ position: 'absolute', left: 9, color: '#9ca3af', display: 'inline-flex' }}><IcSearch size={15} /></span>
                    <input placeholder="Поиск по склейке, артикулу или ID" value={search} onChange={e => setSearch(e.target.value)}
                        style={{ background: '#fff', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px 6px 30px', color: 'var(--color-text)', fontSize: 13, width: 260 }} />
                </div>
                <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                    склеек: <b style={{ color: 'var(--color-text)' }}>{fmt(filtered.length)}</b> · артикулов: <b style={{ color: 'var(--color-text)' }}>{fmt(totals.products)}</b> · затраты: <b style={{ color: 'var(--color-text)' }}>{fmt(totals.spend)} ₽</b> · бюджет: <b style={{ color: 'var(--color-text)' }}>{fmt(totals.budget)} ₽</b>
                </span>
                <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                    <button className="btn btn-secondary btn-sm" onClick={onExport} disabled={!filtered.length}
                        style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}><IcDownload size={14} />Excel</button>
                    <div style={{ position: 'relative' }}>
                        <button className="btn btn-secondary btn-sm" onClick={() => setColsMenu(o => !o)} aria-label="Колонки"
                            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}><IcColumns size={14} />Колонки</button>
                        {colsMenu && (<>
                            <div style={{ position: 'fixed', inset: 0, zIndex: 40 }} onClick={() => setColsMenu(false)} />
                            <div style={{ position: 'absolute', right: 0, top: '100%', marginTop: 6, zIndex: 41, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, boxShadow: '0 8px 24px rgba(0,0,0,0.12)', padding: 8, minWidth: 200 }}>
                                {COLS.map(c => (
                                    <label key={c.key} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', borderRadius: 8, cursor: 'pointer', fontSize: 13 }} className="menu-row">
                                        <input type="checkbox" checked={visibleCols.has(c.key)} onChange={() => toggleCol(c.key)} />
                                        {c.label}
                                    </label>
                                ))}
                            </div>
                        </>)}
                    </div>
                </span>
            </div>

            <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
                {/* Класс data-table и геометрия — те же, что у таблицы кампаний: КАПС в шапке,
                    моноширинные цифры (tabular-nums), фиксированный layout */}
                <table className="data-table" style={{ width: '100%', tableLayout: 'fixed', borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                    <thead>
                        <tr>
                            <th style={{ ...cThLeft, ...TH_PAD, width: 28 }} />
                            <th style={{ ...cThLeft, ...TH_PAD, width: 26 }} />
                            <th style={{ ...cThLeft, ...TH_PAD, width: 340, cursor: 'pointer', userSelect: 'none' }}
                                onClick={() => onSort('name')}>
                                Склейка{sortMark('name')}
                            </th>
                            {cols.map(c => (
                                <th key={c.key} onClick={() => onSort(c.key)}
                                    style={{ ...cThStyle, ...TH_PAD, width: c.w, borderLeft: c.blockStart ? BLOCK_DIVIDER : undefined, cursor: 'pointer', userSelect: 'none' }}>
                                    {c.title ? <InfoTip text={c.title}>{c.label}</InfoTip> : c.label}{sortMark(c.key)}
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {loading && (
                            <tr><td colSpan={cols.length + 3} style={{ ...tdLeft, padding: 24, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка склеек…</td></tr>
                        )}
                        {!loading && error && (
                            <tr><td colSpan={cols.length + 3} style={{ ...tdLeft, padding: 24, textAlign: 'center', color: 'var(--color-danger)' }}>⚠️ {error}</td></tr>
                        )}
                        {!loading && !error && !filtered.length && (
                            <tr><td colSpan={cols.length + 3} style={{ ...tdLeft, padding: 24, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                                {rows.length ? 'Под фильтры ничего не подошло' : 'Нет данных за период'}
                            </td></tr>
                        )}
                        {!loading && !error && filtered.map(r => {
                            const key = glueKey(r);
                            const open = openGlue.has(key);
                            const selCount = r.nm_ids.reduce((n, nm) => n + (selectedNms.has(nm) ? 1 : 0), 0);
                            const allSel = selCount > 0 && selCount === r.nm_ids.length;
                            const someSel = selCount > 0 && !allSel;
                            return (
                                <React.Fragment key={key}>
                                    <tr onClick={() => setOpenGlue(prev => { const n = new Set(prev); if (n.has(key)) n.delete(key); else n.add(key); return n; })}
                                        style={{ cursor: 'pointer', background: selCount ? '#eff6ff' : open ? '#f8fafc' : undefined }} className="menu-row">
                                        <td style={{ ...tdLeft, ...TD_PAD }}>
                                            <TriCheckbox checked={allSel} indeterminate={someSel}
                                                title={allSel ? 'Снять выбор со склейки' : `Выбрать все товары склейки (${r.product_count})`}
                                                onChange={() => onToggleGlue(r.nm_ids, !allSel)} />
                                        </td>
                                        <td style={{ ...tdLeft, ...TD_PAD, color: '#9ca3af' }}>
                                            <span style={{ display: 'inline-block', transition: 'transform .15s', transform: open ? 'rotate(90deg)' : undefined }}>▶</span>
                                        </td>
                                        <td style={{ ...tdLeft, ...TD_PAD }}>
                                            <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                                                <GlueThumbs nmIds={r.nm_ids} />
                                                <span style={{ minWidth: 0, lineHeight: 1.25 }}>
                                                    <span style={{ display: 'block', fontWeight: 600, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.glue_name}</span>
                                                    <span style={{ display: 'block', fontSize: 10, color: '#9ca3af', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                        {r.is_glue ? `Склейка ${r.imt_id} · ${r.product_count} арт.` : `Артикул ${r.nm_ids[0]} · вне склейки`}
                                                        {r.brand ? ` · ${r.brand}` : ''}
                                                    </span>
                                                </span>
                                            </span>
                                        </td>
                                        {cols.map(c => (
                                            <td key={c.key} style={{ ...tdStyle, ...TD_PAD, borderLeft: c.blockStart ? BLOCK_DIVIDER : undefined }}>
                                                {metricCell(c.key, r, true)}
                                            </td>
                                        ))}
                                    </tr>

                                    {open && r.children.map(child => {
                                        const nmOpen = openNm.has(child.nm_id);
                                        const camps: AdCampaign[] = child.campaigns || [];
                                        return (
                                            <React.Fragment key={child.nm_id}>
                                                <tr onClick={() => setOpenNm(prev => { const n = new Set(prev); if (n.has(child.nm_id)) n.delete(child.nm_id); else n.add(child.nm_id); return n; })}
                                                    style={{ cursor: 'pointer', background: selectedNms.has(child.nm_id) ? '#eff6ff' : '#fcfcfd' }} className="menu-row">
                                                    <td style={{ ...tdLeft, ...TD_PAD, paddingLeft: 14 }}>
                                                        <TriCheckbox checked={selectedNms.has(child.nm_id)} indeterminate={false}
                                                            title={`Выбрать артикул ${child.nm_id}`}
                                                            onChange={() => onProductClick(child.nm_id, nmsWithCampaigns.has(child.nm_id))} />
                                                    </td>
                                                    <td style={{ ...tdLeft, ...TD_PAD, color: '#c4c8ce' }}>
                                                        {camps.length > 0 && (
                                                            <span style={{ display: 'inline-block', fontSize: 10, transition: 'transform .15s', transform: nmOpen ? 'rotate(90deg)' : undefined }}>▶</span>
                                                        )}
                                                    </td>
                                                    <td style={{ ...tdLeft, ...TD_PAD, paddingLeft: 22 }}>
                                                        <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                                                            <WbThumb nmId={child.nm_id} size={26} rounded={5} />
                                                            <span style={{ minWidth: 0, lineHeight: 1.25 }}>
                                                                <Link href={`/p/${slug}/ads-manager/product/${child.nm_id}`} onClick={e => e.stopPropagation()}
                                                                    style={{ display: 'block', fontWeight: 600, fontSize: 12, color: 'var(--color-accent)', textDecoration: 'none', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                                    {child.vendor_code || `#${child.nm_id}`}
                                                                </Link>
                                                                <span style={{ display: 'block', fontSize: 10, color: '#9ca3af' }}>
                                                                    {child.nm_id}{camps.length ? ` · ${camps.length} камп.` : ' · без рекламы'}
                                                                </span>
                                                            </span>
                                                        </span>
                                                    </td>
                                                    {cols.map(c => (
                                                        <td key={c.key} style={{ ...tdStyle, ...TD_PAD, borderLeft: c.blockStart ? BLOCK_DIVIDER : undefined }}>
                                                            {metricCell(c.key, child, false)}
                                                        </td>
                                                    ))}
                                                </tr>

                                                {nmOpen && camps.map(camp => (
                                                    <tr key={camp.campaign_id} style={{ background: '#f9fafb' }}>
                                                        <td style={{ ...tdLeft, ...TD_PAD }} />
                                                        <td style={{ ...tdLeft, ...TD_PAD }} />
                                                        <td style={{ ...tdLeft, ...TD_PAD, paddingLeft: 52 }}>
                                                            <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                                                                <Link href={`/p/${slug}/ads-manager/campaign/${camp.campaign_id}`}
                                                                    style={{ fontSize: 12, color: 'var(--color-accent)', textDecoration: 'none', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                                    {camp.name || `#${camp.campaign_id}`}
                                                                </Link>
                                                                <span className={`badge ${STATUS_BADGE[camp.status] || 'badge-secondary'}`}
                                                                    style={{ fontSize: 10, padding: '2px 6px', flexShrink: 0 }}>
                                                                    {camp.status === 9 ? 'активна' : camp.status === 11 ? 'пауза' : camp.status === 7 ? 'завершена' : camp.status}
                                                                </span>
                                                                <span style={{ fontSize: 10, color: '#9ca3af', textTransform: 'uppercase', flexShrink: 0 }}>{camp.campaign_type}</span>
                                                            </span>
                                                        </td>
                                                        {cols.map(c => (
                                                            <td key={c.key} style={{ ...tdStyle, ...TD_PAD, borderLeft: c.blockStart ? BLOCK_DIVIDER : undefined, color: '#6b7280' }}>
                                                                {c.key === 'spend' ? fmt(camp.spend)
                                                                    : c.key === 'views' ? fmt(camp.views)
                                                                        : c.key === 'clicks' ? fmt(camp.clicks)
                                                                            : c.key === 'ctr' ? fmtPct(camp.ctr)
                                                                                : c.key === 'cpc' ? fmt(camp.cpc)
                                                                                    : c.key === 'budget' ? fmt(camp.budget)
                                                                                        : c.key === 'types' ? (camp.campaign_type || '').toUpperCase()
                                                                                            : '—'}
                                                            </td>
                                                        ))}
                                                    </tr>
                                                ))}
                                            </React.Fragment>
                                        );
                                    })}
                                </React.Fragment>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
