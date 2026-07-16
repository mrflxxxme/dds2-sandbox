'use client';
import React, { useEffect, useMemo, useState } from 'react';
import { formatNumber, exportToExcel } from '@/lib/utils';
import type { SearchCluster, ClusterDailyPoint } from '@/types/api';
import { cpmForTargetCpc } from './clusterBid';
import Tooltip from './Tooltip';

// ─── Форматтеры (Decimal-поля бэка приходят строкой → Number() перед formatNumber) ───
const num = (v: unknown) => Number(v) || 0;
export const clNum = (v: unknown) => formatNumber(num(v), 0);
export const clMoney = (v: unknown) => `${formatNumber(num(v), 0)} ₽`;
export const clPct = (v: unknown, d = 1) => `${formatNumber(num(v), d)}%`;
export const clMoneyN = (v: number | null | undefined) => (v == null ? '—' : `${formatNumber(Number(v), 0)} ₽`);
export const clPctN = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${formatNumber(Number(v), d)}%`);

// ДРР-цвет: ≤ target зелёный, ≤ 1.5×target янтарь, иначе красный
export function drrColor(drr: number | null, targetDrr: number): string {
    if (drr == null) return '#9ca3af';
    const t = targetDrr > 0 ? targetDrr : 8;
    if (drr <= t) return '#10b981';
    if (drr <= t * 1.5) return '#f59e0b';
    return '#ef4444';
}

// Минималистичный стиль: тонкие линии, плотные строки, малые капсы в шапке
// Шапка липнет к верху скролл-контейнера, фильтры — сразу под ней (HEAD_H — её высота)
const HEAD_H = 21;
const thStyle: React.CSSProperties = { background: '#f3f4f6', color: '#374151', fontSize: 9.5, fontWeight: 700, letterSpacing: 0.3, textTransform: 'uppercase', textAlign: 'right', borderBottom: '1px solid #d1d5db', padding: '3px 5px', whiteSpace: 'nowrap', position: 'sticky', top: 0, zIndex: 3 };
const thLeft: React.CSSProperties = { ...thStyle, textAlign: 'left' };
// Высоту строки задаёт ячейка, а не её содержимое: у фраз со ставкой стоит <input> (≈19px),
// а у «сбор данных» — обычный текст, из-за чего строки различались на ~6px.
// height у <td> действует как минимальная, поэтому обе ветки выравниваются по ROW_H.
const ROW_H = 28;
const tdStyle: React.CSSProperties = { textAlign: 'right', borderBottom: '1px solid #e8eaed', padding: '2px 5px', fontSize: 10.5, lineHeight: 1.35, whiteSpace: 'nowrap', height: ROW_H, color: '#111827' };
const tdLeft: React.CSSProperties = { ...tdStyle, textAlign: 'left' };
// Итоговая строка (tfoot) — тот же кегль, что у данных
const tfStyle: React.CSSProperties = {
    ...tdStyle, borderTop: '1px solid #e2e4e8', borderBottom: 'none', whiteSpace: 'nowrap',
    // Итоги видны всегда, без прокрутки до конца списка
    position: 'sticky', bottom: 0, zIndex: 3, background: '#f3f4f6',
};

// Первые две колонки (фраза и ставка) закреплены при горизонтальной прокрутке.
// Ширины фиксированы: sticky-left нужно знать смещение заранее.
const CHECK_W = 28, PHRASE_W = 260, BID_W = 96;
const FROZEN_LEFT = [CHECK_W, CHECK_W + PHRASE_W];

/** Стиль закрепления для колонки с индексом i (закреплены только первые две).
 *  Ширины жёсткие, поэтому закрепляем ТОЛЬКО штатную пару «фраза + ставка»:
 *  после перестановки колонок drag'ом смещения были бы неверными. */
function frozenCell(i: number, bg: string, z: number, enabled = true): React.CSSProperties | null {
    if (!enabled || i > 1) return null;
    return {
        position: 'sticky', left: FROZEN_LEFT[i], zIndex: z, background: bg,
        width: i === 0 ? PHRASE_W : BID_W, minWidth: i === 0 ? PHRASE_W : BID_W,
        // Тень-обрыв у правой границы закреплённой пары
        boxShadow: i === 1 ? '2px 0 4px -2px rgba(17,24,39,.18)' : undefined,
    };
}

// Блок фильтров: светло-серая подложка, поля ввода белые, ряды строго друг под другом
const fInput: React.CSSProperties = { width: '100%', minWidth: 44, boxSizing: 'border-box', padding: '2px 5px', fontSize: 10, border: '1px solid #e2e4e8', borderRadius: 4, color: '#374151', background: '#fff' };
const fTextInput: React.CSSProperties = { ...fInput, minWidth: 120 };
const filterThStyle: React.CSSProperties = { background: '#f3f4f6', borderBottom: '1px solid #e2e4e8', padding: '4px 6px', verticalAlign: 'top', position: 'sticky', top: HEAD_H, zIndex: 2 };

// Подсветка места вставки при переносе колонки (линия слева/справа от целевой)
const DROP_LINE = '#3b82f6';
// Синяя черта у ставки: фраза набрала ≥100 показов, WB даёт по ней ставку
const BID_ACTIVE_LINE = '#3b82f6';
// Тонкая линия-разделитель столбцов — через всю таблицу
const COL_DIVIDER = '1px solid #e5e7eb';
// Подсветка строк: выбранная — синяя, под курсором — светло-серая
const ROW_SELECTED = '#dbeafe';
const ROW_HOVER = '#f1f5f9';

/** Выдача WB по поисковому запросу (URLSearchParams: русские буквы и «&» кодируются). */
const wbSearchUrl = (q: string) => `https://www.wildberries.ru/catalog/0/search.aspx?${new URLSearchParams({ search: q })}`;

/** Цвет позиции: 1–10 зелёный, 11–30 жёлтый, 31 и дальше красный. */
const posColor = (pos: number) => (pos <= 0 ? '#d1d5db' : pos <= 10 ? '#10b981' : pos <= 30 ? '#f59e0b' : '#ef4444');

type SortField = 'norm_query' | 'bid' | 'position' | 'prev_pos' | 'views' | 'clicks' | 'ctr' | 'cpc' | 'cpm' | 'cpl'
    | 'orders' | 'cr' | 'cr1' | 'cr2' | 'cpo' | 'drr' | 'share' | 'avg_pos' | 'spend';

/** Кластер + метрики, которых нет в ответе WB (считаем из его же полей), + органическая
 *  позиция товара по фразе из отдельного сбора (search.wb.ru): position/prev_pos + глубина. */
type Row = SearchCluster & {
    cpl: number | null; cr1: number; cr2: number; share: number;
    position: number | null; prev_pos: number | null; pos_depth: number | null;
};

/** Данные позиций по фразам (norm_query → снимок). Приходят из отдельного endpoint. */
export type PositionsMap = Record<string, { position: number | null; prev: number | null; depth: number | null }>;

/** Поля-позиции: у них меньше = лучше, а null (не найден) при сортировке — в конец. */
const POS_FIELDS = new Set<SortField>(['position', 'prev_pos', 'avg_pos']);
type SortDir = 'asc' | 'desc';
type RelFilter = 'all' | 'active' | 'excluded';
type ColKey = SortField;
type Range = { min: string; max: string };

// Конфиг колонок: подпись, тип фильтра, выравнивание. Порядок — переставляемый (drag).
const COL_DEFS: Record<ColKey, { label: string; title?: string; align: 'left' | 'right'; filter: 'text' | 'range' }> = {
    norm_query: { label: 'Ключевая фраза', align: 'left', filter: 'text' },
    bid: { label: 'Ставка', title: 'CPM-ставка кластера, ₽', align: 'right', filter: 'range' },
    position: { label: 'Позиция', title: 'Органическая позиция товара по фразе (последний сбор). «N+» — не в топ-N.', align: 'right', filter: 'range' },
    prev_pos: { label: 'Была', title: 'Органическая позиция в предыдущем сборе — для динамики', align: 'right', filter: 'range' },
    views: { label: 'Показы', align: 'right', filter: 'range' },
    clicks: { label: 'Клики', align: 'right', filter: 'range' },
    ctr: { label: 'CTR', align: 'right', filter: 'range' },
    cpc: { label: 'CPC', title: 'Цена клика', align: 'right', filter: 'range' },
    cpm: { label: 'CPM', title: 'Цена 1000 показов', align: 'right', filter: 'range' },
    cpl: { label: 'CPL', title: 'Стоимость корзины: расход / корзины', align: 'right', filter: 'range' },
    orders: { label: 'Заказы', align: 'right', filter: 'range' },
    cr: { label: 'CR', title: 'Конверсия клик→заказ', align: 'right', filter: 'range' },
    cr1: { label: 'CR1', title: 'Конверсия в корзину: корзины / клики', align: 'right', filter: 'range' },
    cr2: { label: 'CR2', title: 'Конверсия в заказ: заказы / корзины', align: 'right', filter: 'range' },
    cpo: { label: 'CPO', title: 'Стоимость заказа', align: 'right', filter: 'range' },
    drr: { label: 'ДРР', title: 'Доля рекламных расходов = расход / (заказы × AOV)', align: 'right', filter: 'range' },
    share: { label: '%', title: 'Доля затрат фразы во всех затратах кампании', align: 'right', filter: 'range' },
    avg_pos: { label: 'Поз.', title: 'Средняя позиция', align: 'right', filter: 'range' },
    spend: { label: 'Затраты', align: 'right', filter: 'range' },
};
const DEFAULT_ORDER: ColKey[] = ['norm_query', 'bid', 'position', 'prev_pos', 'views', 'clicks', 'ctr', 'cpc', 'cpm', 'cpl',
    'orders', 'cr', 'cr1', 'cr2', 'cpo', 'drr', 'share', 'avg_pos', 'spend'];
const RANGE_FIELDS = DEFAULT_ORDER.filter(k => COL_DEFS[k].filter === 'range');
const ORDER_LS_KEY = 'ads_cluster_col_order';
const emptyRanges = (): Record<ColKey, Range> =>
    RANGE_FIELDS.reduce((acc, f) => { acc[f] = { min: '', max: '' }; return acc; }, {} as Record<ColKey, Range>);

export interface MinusControls {
    pending: Set<string>;         // norm_query, по которым идёт запрос
    onToggle: (c: SearchCluster) => void;
    onBulk?: (clusters: SearchCluster[], action: 'add' | 'remove') => void;  // массовое действие
    error?: string | null;        // текст последней ошибки
}

export interface BidControls {
    pending: Set<string>;         // norm_query, по которым идёт запись ставки
    onSetBid: (c: SearchCluster, bid: number) => void;
    /** Массово: пары «фраза → ставка ₽». Ставка 0 = сброс к ставке кампании. */
    onBulkBid?: (items: { cluster: SearchCluster; bid: number }[], label: string) => void;
    error?: string | null;        // текст последней ошибки
}

/** Редактируемая ячейка CPM-ставки кластера.
 *  Своей ставки у кластера может не быть — тогда показываем ставку кампании (по ней он и крутится).
 *  Набравшие ≥100 показов (не locked) помечены синей чертой слева: WB уже даёт по ним ставку.
 */
function BidCell({ cluster, bids, defaultBid, clusterLock }: { cluster: SearchCluster; bids?: BidControls; defaultBid?: number | null; clusterLock?: string | null }) {
    const own = cluster.bid == null ? null : Number(cluster.bid);
    const current = own ?? (defaultBid ?? null);
    const [val, setVal] = useState<string>(current == null ? '' : String(current));
    useEffect(() => { setVal(current == null ? '' : String(current)); }, [current]);

    if (!bids) return <span>{current == null ? '—' : clMoney(current)}</span>;
    // Единая ставка CPM — WB не даёт менять ставку по кластеру: показываем значение только для чтения.
    if (clusterLock) {
        return <Tooltip text={clusterLock}><span style={{ color: '#9ca3af' }}>{current == null ? '—' : clMoney(current)}</span></Tooltip>;
    }
    if (cluster.locked) {
        return <Tooltip text="<100 показов — WB не даёт ставку"><span style={{ color: '#9ca3af', fontSize: 10.5, fontStyle: 'italic' }}>сбор данных</span></Tooltip>;
    }
    const pending = bids.pending.has(cluster.norm_query);
    const commit = () => {
        if (pending) return;
        const s = val.trim();
        if (s === '') { setVal(current == null ? '' : String(current)); return; }
        const n = Number(s);
        if (!Number.isFinite(n) || n <= 0) { setVal(current == null ? '' : String(current)); return; }
        if (n === current) return;
        bids.onSetBid(cluster, n);
    };
    // Кружок относительно базовой ставки кампании: выше — зелёный, ниже — оранжевый, равна — без метки
    const diff = own == null || defaultBid == null ? 0 : own - defaultBid;
    const dot = diff > 0 ? { color: '#10b981', title: `Выше базовой ставки кампании (${defaultBid} ₽)` }
        : diff < 0 ? { color: '#f59e0b', title: `Ниже базовой ставки кампании (${defaultBid} ₽)` }
        : null;

    return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, justifyContent: 'flex-end' }} onClick={e => e.stopPropagation()}>
            {/* ≥100 показов — WB уже управляет ставкой этой фразы */}
            <Tooltip text="Набрано ≥100 показов — ставка по фразе активна" style={{ alignSelf: 'stretch', minHeight: 16 }}>
                <span aria-hidden style={{ width: 2, alignSelf: 'stretch', minHeight: 16, borderRadius: 1, background: BID_ACTIVE_LINE }} />
            </Tooltip>
            {dot && <Tooltip text={dot.title}><span style={{ width: 7, height: 7, borderRadius: '50%', flexShrink: 0, background: dot.color }} /></Tooltip>}
            <input type="number" min={0} value={val} disabled={pending}
                onChange={e => setVal(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
                onBlur={commit}
                title={own == null
                    ? `Своей ставки нет — действует ставка кампании${current == null ? '' : ` (${current} ₽)`}. Введите значение, чтобы задать ставку фразы.`
                    : 'CPM-ставка фразы, ₽ — Enter или потеря фокуса применит в кабинете WB'}
                style={{ width: 54, boxSizing: 'border-box', padding: '1px 5px', fontSize: 11, textAlign: 'right', border: '1px solid #e5e7eb', borderRadius: 5, color: own == null ? '#9ca3af' : '#111827', background: pending ? '#f3f4f6' : '#fff', opacity: pending ? 0.6 : 1 }} />
            {pending ? <span style={{ fontSize: 10, color: '#9ca3af' }}>…</span> : <span style={{ fontSize: 10, color: '#9ca3af' }}>₽</span>}
        </span>
    );
}

/** Окно «Изменить ставку»: выезжает из кнопки нижней панели.
 *  CPM — ставка ставится как есть. CPC — на каждую фразу считается своя CPM-ставка,
 *  дающая нужную цену клика (фразы без кликов пропускаются: CTR неизвестен).
 */
function BidPopover({ targets, onApply, onClose }: {
    targets: SearchCluster[];
    onApply: (items: { cluster: SearchCluster; bid: number }[], label: string) => void;
    onClose: () => void;
}) {
    const [mode, setMode] = useState<'cpm' | 'cpc'>('cpm');
    const [cpm, setCpm] = useState('');
    const [cpc, setCpc] = useState('');

    const planned = useMemo(() => {
        if (mode === 'cpm') {
            const v = Number(cpm);
            if (!(v > 0)) return { items: [], skipped: 0 };
            return { items: targets.map(c => ({ cluster: c, bid: Math.round(v) })), skipped: 0 };
        }
        const target = Number(cpc);
        const items: { cluster: SearchCluster; bid: number }[] = [];
        let skipped = 0;
        for (const c of targets) {
            const bid = cpmForTargetCpc(target, num(c.ctr));
            if (bid == null) skipped += 1; else items.push({ cluster: c, bid });
        }
        return { items, skipped };
    }, [mode, cpm, cpc, targets]);

    const apply = () => {
        if (planned.items.length === 0) return;
        onApply(planned.items, mode === 'cpm' ? `CPM ${Math.round(Number(cpm))} ₽` : `CPC ${Number(cpc)} ₽`);
        onClose();
    };

    const radio = (v: 'cpm' | 'cpc', label: string, hint: string) => (
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: mode === v ? 600 : 500, cursor: 'pointer' }} title={hint}>
            <input type="radio" checked={mode === v} onChange={() => setMode(v)} />{label}
        </label>
    );

    return (
        <div style={{
            position: 'absolute', bottom: 'calc(100% + 8px)', left: 0, zIndex: 30, width: 250,
            background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, padding: 12,
            boxShadow: '0 12px 32px rgba(17,24,39,.16)', display: 'flex', flexDirection: 'column', gap: 8,
        }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>Изменить ставку · {targets.length}</div>

            {radio('cpm', 'CPM — за 1000 показов', 'Ставка ставится одинаковой для всех выбранных фраз')}
            <input type="number" min={0} placeholder="700" value={cpm} disabled={mode !== 'cpm'}
                onChange={e => setCpm(e.target.value)}
                style={{ padding: '5px 8px', fontSize: 12, border: '1px solid #e5e7eb', borderRadius: 6, background: mode === 'cpm' ? '#fff' : '#f3f4f6' }} />

            {radio('cpc', 'CPC — цена клика', 'Для каждой фразы посчитаем свою CPM-ставку по её CTR')}
            <input type="number" min={0} placeholder="15" value={cpc} disabled={mode !== 'cpc'}
                onChange={e => setCpc(e.target.value)}
                style={{ padding: '5px 8px', fontSize: 12, border: '1px solid #e5e7eb', borderRadius: 6, background: mode === 'cpc' ? '#fff' : '#f3f4f6' }} />

            {mode === 'cpc' && (
                <div style={{ fontSize: 11, color: '#6b7280', lineHeight: 1.4 }}>
                    CPM = CPC × CTR × 10, для каждой фразы своя.
                    {planned.skipped > 0 && <div style={{ color: '#f59e0b' }}>Без кликов, пропустим: {planned.skipped}</div>}
                </div>
            )}

            <div style={{ display: 'flex', gap: 6, marginTop: 2 }}>
                <button className="btn btn-primary btn-sm" style={{ fontSize: 12 }} onClick={apply} disabled={planned.items.length === 0}>
                    Сохранить{planned.items.length > 0 ? ` (${planned.items.length})` : ''}
                </button>
                <button className="btn btn-secondary btn-sm" style={{ fontSize: 12 }} onClick={onClose}>Отмена</button>
            </div>
        </div>
    );
}

/**
 * Сортируемая таблица кластеров: сегмент релевантности + пер-колоночные фильтры,
 * перестановка колонок (drag), ячейки выбора + массовые действия.
 */
export default function ClusterTable({ clusters, targetDrr, exportName, minus, bids, defaultBid, aov = 0, positions, onCollectPositions, onStopPositions, collecting, onCollectOne, collectingOne, clusterLock = null }: {
    clusters: SearchCluster[];
    targetDrr: number;
    exportName: string;
    minus?: MinusControls;
    bids?: BidControls;
    /** Причина блокировки управления кластерами (единая ставка CPM) — гасит минус/ставки. */
    clusterLock?: string | null;
    defaultBid?: number | null;  // ставка кампании для фраз без своей
    aov?: number;                // средний чек — нужен для итогового ДРР
    positions?: PositionsMap;    // органические позиции по фразам (отдельный сбор)
    onCollectPositions?: () => void;  // Play: массовый сбор позиций по всем фразам
    onStopPositions?: () => void;     // Stop: остановить массовый сбор
    collecting?: { done: number; total: number; throttled?: number } | null;  // прогресс массового сбора
    onCollectOne?: (phrase: string) => void;  // кругляшок в ячейке: собрать одну фразу
    collectingOne?: Set<string>;      // фразы, по которым идёт единичный сбор (спиннер)
}) {
    const [rel, setRel] = useState<RelFilter>('all');
    const [contains, setContains] = useState('');
    const [notContains, setNotContains] = useState('');
    const [ranges, setRanges] = useState<Record<ColKey, Range>>(emptyRanges);
    const [sort, setSort] = useState<{ field: SortField; dir: SortDir }>({ field: 'spend', dir: 'desc' });
    const [order, setOrder] = useState<ColKey[]>(DEFAULT_ORDER);
    const [dragKey, setDragKey] = useState<ColKey | null>(null);
    const [dropPos, setDropPos] = useState<{ key: ColKey; after: boolean } | null>(null);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [hoverKey, setHoverKey] = useState<string | null>(null);
    const [bidPopover, setBidPopover] = useState(false);
    // Подтверждение массового действия — внутри панели, без системного window.confirm
    const [confirmAsk, setConfirmAsk] = useState<{ text: string; run: () => void } | null>(null);
    // Закрепляем слева только штатную пару колонок (ширины зашиты)
    const freezeCols = order[0] === 'norm_query' && order[1] === 'bid';

    // Порядок колонок переживает перезагрузку
    useEffect(() => {
        try {
            const raw = localStorage.getItem(ORDER_LS_KEY);
            if (raw) {
                const saved: ColKey[] = JSON.parse(raw);
                const valid = saved.filter(k => DEFAULT_ORDER.includes(k));
                const missing = DEFAULT_ORDER.filter(k => !valid.includes(k));
                setOrder([...valid, ...missing]);
            }
        } catch { /* SSR / битый JSON */ }
    }, []);
    const persistOrder = (o: ColKey[]) => { try { localStorage.setItem(ORDER_LS_KEY, JSON.stringify(o)); } catch { /* SSR */ } };

    const setRange = (f: ColKey, key: 'min' | 'max', v: string) =>
        setRanges(prev => ({ ...prev, [f]: { ...prev[f], [key]: v } }));

    const inRange = (raw: number | null | undefined, r: Range): boolean => {
        const hasMin = r.min.trim() !== ''; const hasMax = r.max.trim() !== '';
        if (!hasMin && !hasMax) return true;
        if (raw == null) return false;
        const n = Number(raw);
        if (hasMin && n < Number(r.min)) return false;
        if (hasMax && n > Number(r.max)) return false;
        return true;
    };

    // CPL / CR1 / CR2 / доля затрат WB не отдаёт — считаем из его же полей.
    // Доля — от затрат ВСЕЙ кампании, а не от видимых строк, иначе она «плавала» бы от фильтра.
    const rows: Row[] = useMemo(() => {
        const totalSpend = clusters.reduce((s, c) => s + num(c.spend), 0) || 1;
        return clusters.map(c => {
            const p = positions?.[c.norm_query];
            return {
                ...c,
                cpl: num(c.atbs) > 0 ? num(c.spend) / num(c.atbs) : null,
                cr1: num(c.clicks) > 0 ? (num(c.atbs) / num(c.clicks)) * 100 : 0,
                cr2: num(c.atbs) > 0 ? (num(c.orders) / num(c.atbs)) * 100 : 0,
                share: (num(c.spend) / totalSpend) * 100,
                position: p ? p.position : null,
                prev_pos: p ? p.prev : null,
                pos_depth: p ? p.depth : null,
            };
        });
    }, [clusters, positions]);

    const filtered = useMemo(() => {
        const cont = contains.trim().toLowerCase();
        const ncont = notContains.trim().toLowerCase();
        const base = rows.filter(c => {
            // Вкладки — по факту исключения (минус-фразы), а не по эвристике релевантности
            if (rel === 'active' && c.is_minused) return false;
            if (rel === 'excluded' && !c.is_minused) return false;
            const q = c.norm_query.toLowerCase();
            if (cont && !q.includes(cont)) return false;
            if (ncont && q.includes(ncont)) return false;
            for (const f of RANGE_FIELDS) {
                if (!inRange(c[f] as number | null | undefined, ranges[f])) return false;
            }
            return true;
        });
        const dir = sort.dir === 'asc' ? 1 : -1;
        // null у полей-позиций — «в конец» (не найден = хуже всех), у прочих — как раньше.
        const nullVal = POS_FIELDS.has(sort.field) ? Infinity : -Infinity;
        return [...base].sort((a, b) => {
            if (sort.field === 'norm_query') return dir * a.norm_query.localeCompare(b.norm_query, 'ru');
            const av = a[sort.field] == null ? nullVal : Number(a[sort.field]);
            const bv = b[sort.field] == null ? nullVal : Number(b[sort.field]);
            return dir * (av - bv);
        });
    }, [rows, rel, contains, notContains, ranges, sort]);

    // Итоги по видимым строкам: суммы — складываем, производные — пересчитываем из сумм
    // (среднее по столбцу дало бы неверный CTR/CPC/ДРР).
    const totals = useMemo(() => {
        const s = filtered.reduce((a, c) => {
            a.views += num(c.views); a.clicks += num(c.clicks);
            a.orders += num(c.orders); a.spend += num(c.spend); a.atbs += num(c.atbs);
            a.share += num(c.share);
            if (num(c.avg_pos) > 0) { a.posSum += num(c.avg_pos); a.posN += 1; }
            return a;
        }, { views: 0, clicks: 0, orders: 0, spend: 0, atbs: 0, share: 0, posSum: 0, posN: 0 });
        return {
            ...s,
            ctr: s.views ? (s.clicks / s.views) * 100 : 0,
            cpc: s.clicks ? s.spend / s.clicks : 0,
            cpm: s.views ? (s.spend / s.views) * 1000 : 0,
            cr: s.clicks ? (s.orders / s.clicks) * 100 : 0,
            cr1: s.clicks ? (s.atbs / s.clicks) * 100 : 0,
            cr2: s.atbs ? (s.orders / s.atbs) * 100 : 0,
            cpl: s.atbs ? s.spend / s.atbs : null,
            cpo: s.orders ? s.spend / s.orders : null,
            drr: aov > 0 && s.orders > 0 ? (s.spend / (s.orders * aov)) * 100 : null,
            avg_pos: s.posN ? s.posSum / s.posN : 0,
        };
    }, [filtered, aov]);

    const renderTotal = (k: ColKey): React.ReactNode => {
        switch (k) {
            case 'norm_query': return <span style={{ fontWeight: 600 }}>Всего: {clNum(filtered.length)}</span>;
            case 'bid': return defaultBid == null ? '—' : clMoney(defaultBid);
            case 'position': case 'prev_pos': return '—';
            case 'views': return clNum(totals.views);
            case 'clicks': return clNum(totals.clicks);
            case 'ctr': return clPct(totals.ctr, 2);
            case 'cpc': return clMoney(totals.cpc);
            case 'cpm': return clMoney(totals.cpm);
            case 'cpl': return clMoneyN(totals.cpl);
            case 'orders': return clNum(totals.orders);
            case 'cr': return clPct(totals.cr, 2);
            case 'cr1': return clPct(totals.cr1, 2);
            case 'cr2': return clPct(totals.cr2, 2);
            case 'share': return clPct(totals.share, 1);
            case 'cpo': return clMoneyN(totals.cpo);
            case 'drr': return <span style={{ color: drrColor(totals.drr, targetDrr) }}>{clPctN(totals.drr)}</span>;
            case 'avg_pos': return totals.avg_pos > 0 ? formatNumber(totals.avg_pos, 1) : '—';
            case 'spend': return clMoney(totals.spend);
            default: return null;
        }
    };

    const toggleSort = (field: SortField) =>
        setSort(prev => ({ field, dir: prev.field === field && prev.dir === 'desc' ? 'asc' : 'desc' }));
    const arrow = (field: SortField) => sort.field === field ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : '';

    // ─── Drag-перестановка колонок ───
    // dropPos.after — курсор в правой половине целевой колонки, вставляем ПОСЛЕ неё.
    // Без этого колонку нельзя было сдвинуть на одну позицию вправо: вставка всегда
    // шла перед целевой, т.е. на её же место.
    const onDragStart = (k: ColKey) => setDragKey(k);
    const onDragEnd = () => { setDragKey(null); setDropPos(null); };
    const onDragOverCol = (k: ColKey, e: React.DragEvent) => {
        e.preventDefault();
        if (!dragKey || k === dragKey) { setDropPos(null); return; }
        const r = e.currentTarget.getBoundingClientRect();
        setDropPos({ key: k, after: e.clientX > r.left + r.width / 2 });
    };
    const onDrop = () => {
        if (!dragKey || !dropPos || dropPos.key === dragKey) { onDragEnd(); return; }
        setOrder(prev => {
            const next = prev.filter(k => k !== dragKey);
            const idx = next.indexOf(dropPos.key) + (dropPos.after ? 1 : 0);
            next.splice(idx, 0, dragKey);
            persistOrder(next);
            return next;
        });
        onDragEnd();
    };
    // Линия-указатель вставки на границе колонки (inset — не двигает вёрстку)
    const dropShadow = (k: ColKey): string | undefined => {
        if (!dropPos || dropPos.key !== k) return undefined;
        return dropPos.after ? `inset -2px 0 0 ${DROP_LINE}` : `inset 2px 0 0 ${DROP_LINE}`;
    };

    // ─── Выбор строк ───
    const allSelected = filtered.length > 0 && filtered.every(c => selected.has(c.norm_query));
    const toggleAll = () => setSelected(allSelected ? new Set() : new Set(filtered.map(c => c.norm_query)));
    const toggleRow = (q: string) => setSelected(prev => { const n = new Set(prev); n.has(q) ? n.delete(q) : n.add(q); return n; });
    const selectedClusters = filtered.filter(c => selected.has(c.norm_query));
    // Действия применимы не ко всем: locked (<100 показов) WB не примет, минус-фразы уже отключены
    const toMinus = selectedClusters.filter(c => !c.is_minused && !c.locked);
    const toReturn = selectedClusters.filter(c => c.is_minused && !c.locked);
    const biddable = selectedClusters.filter(c => !c.locked);
    const copySelected = () => {
        const text = selectedClusters.map(c => c.norm_query).join('\n');
        navigator.clipboard?.writeText(text).catch(() => { /* нет доступа к буферу — молча */ });
    };

    const doExport = () => exportToExcel(filtered.map(c => ({
        'Кластер': c.norm_query, 'Релевантность': c.relevant ? 'целевой' : 'мусор', 'Причина': c.reason,
        'Ставка ₽': c.bid == null ? '' : Number(c.bid),
        'Показы': num(c.views), 'Клики': num(c.clicks), 'CTR %': num(c.ctr), 'CPC ₽': num(c.cpc), 'CPM ₽': num(c.cpm),
        'Заказы': num(c.orders), 'CR %': num(c.cr), 'CPO ₽': c.cpo == null ? '' : Number(c.cpo),
        'ДРР %': c.drr == null ? '' : Number(c.drr), 'Ср. позиция': num(c.avg_pos), 'Расход ₽': num(c.spend),
        'В минусе': c.is_minused ? 'да' : '',
    })), exportName);

    // Приоритет: выбор → курсор → исключена. Так видно и «где я», и «что отмечено».
    const rowBg = (c: SearchCluster) =>
        selected.has(c.norm_query) ? ROW_SELECTED
        : hoverKey === c.norm_query ? ROW_HOVER
        : c.is_minused ? '#fafafa' : undefined;

    const REL_TABS: { key: RelFilter; label: string }[] = [
        { key: 'all', label: 'Все' }, { key: 'active', label: 'Активные' }, { key: 'excluded', label: 'Исключённые' },
    ];

    // Рендер ячейки данных по ключу колонки
    const renderCell = (k: ColKey, c: Row): React.ReactNode => {
        switch (k) {
            // Исключённые фразы — красным и зачёркнуто; активные — нейтральным серым
            // Фраза = ссылка на выдачу WB по этому запросу. Красная и зачёркнутая — исключённая
            // (отдельная подпись «исключена» не нужна: цвет уже всё говорит).
            case 'norm_query': return (
                <a href={wbSearchUrl(c.norm_query)} target="_blank" rel="noreferrer"
                    onClick={e => e.stopPropagation()}
                    title={`Открыть выдачу WB по запросу «${c.norm_query}»`}
                    style={{
                        display: 'block', maxWidth: '100%', textDecorationLine: c.is_minused ? 'line-through' : 'none',
                        fontWeight: 600, color: c.is_minused ? '#ef4444' : '#1f2937',
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>{c.norm_query}</a>
            );
            case 'bid': return <BidCell cluster={c} bids={bids} defaultBid={defaultBid} clusterLock={clusterLock} />;
            // Органическая позиция товара по фразе (последний сбор). null+depth>0 = «N+» (не в топ-N),
            // null+depth 0 = не собрано. Дельта к «Была»: ↑ зелёная (улучшилась), ↓ красная (упала).
            case 'position': {
                const pos = c.position, depth = c.pos_depth;
                const loadingOne = collectingOne?.has(c.norm_query);
                const collected = pos != null || (depth != null && depth > 0);
                // Кругляшок — собрать/обновить позицию именно этой фразы. Не собрано → по центру.
                const roundBtn = (sz: number) => onCollectOne ? (
                    <button onClick={e => { e.stopPropagation(); if (!loadingOne) onCollectOne(c.norm_query); }}
                        disabled={loadingOne} title="Собрать/обновить позицию по этой фразе"
                        style={{
                            width: sz, height: sz, borderRadius: '50%', border: '1.5px solid #6b7280',
                            background: '#fff', cursor: loadingOne ? 'wait' : 'pointer', padding: 0,
                            fontSize: Math.round(sz * 0.62), color: '#1f2937', flexShrink: 0, fontWeight: 700,
                            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                        }}>{/* при сборе — тот же кружок, но крутится */}
                        <span className={loadingOne ? 'dds-spin' : undefined} style={{ display: 'inline-flex', lineHeight: 1 }}>⟳</span>
                    </button>
                ) : null;
                if (!collected) {
                    // ещё не собрано — только кругляшок по середине ячейки (клик собирает и «Позицию», и «Была»)
                    return <span style={{ display: 'flex', justifyContent: 'center' }}>{roundBtn(22)}</span>;
                }
                let val: React.ReactNode;
                if (pos == null) {
                    val = <span style={{ color: '#9ca3af' }} title={`Не в топ-${depth}`}>{depth}+</span>;
                } else {
                    const prev = c.prev_pos;
                    let delta: React.ReactNode = null;
                    if (prev != null && prev !== pos) {
                        const better = pos < prev;
                        delta = <span style={{ fontSize: 9, marginLeft: 3, color: better ? '#10b981' : '#ef4444' }}>{better ? '↑' : '↓'}{Math.abs(prev - pos)}</span>;
                    }
                    val = <span style={{ fontWeight: 700, color: posColor(pos) }}>{pos}{delta}</span>;
                }
                return <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'flex-end', gap: 6 }}>{val}{roundBtn(16)}</span>;
            }
            case 'prev_pos': return c.prev_pos == null
                ? <span style={{ color: '#d1d5db' }}>—</span>
                : <span style={{ color: posColor(c.prev_pos) }}>{c.prev_pos}</span>;
            case 'views': return clNum(c.views);
            case 'clicks': return clNum(c.clicks);
            case 'ctr': return clPct(c.ctr, 2);
            case 'cpc': return clMoney(c.cpc);
            case 'cpm': return clMoney(c.cpm);
            case 'cpl': return clMoneyN(c.cpl);
            case 'cr1': return clPct(c.cr1, 2);
            case 'cr2': return clPct(c.cr2, 2);
            case 'share': return c.share > 0 ? clPct(c.share, 1) : '—';
            case 'orders': return <span style={{ fontWeight: 600 }}>{clNum(c.orders)}</span>;
            case 'cr': return clPct(c.cr, 2);
            case 'cpo': return clMoneyN(c.cpo);
            case 'drr': return <span style={{ fontWeight: 600, color: drrColor(c.drr, targetDrr) }}>{clPctN(c.drr)}</span>;
            // Средняя позиция — ссылка на выдачу WB: проверить, где товар стоит сейчас.
            // Кружок здесь не нужен — он у ставки (сравнение с базовой ставкой кампании).
            case 'avg_pos': {
                const pos = num(c.avg_pos);
                if (pos <= 0) return '—';
                return (
                    <a href={wbSearchUrl(c.norm_query)} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                        title={`Проверить позицию товара в выдаче WB по запросу «${c.norm_query}»`}
                        style={{ color: posColor(pos), fontWeight: 600, textDecoration: 'none' }}>
                        {formatNumber(pos, 1)}
                    </a>
                );
            }
            case 'spend': return <span style={{ fontWeight: 600 }}>{clMoney(c.spend)}</span>;
            default: return null;
        }
    };

    // Ячейка фильтра колонки: текст (содержит/не содержит) или диапазон «от / до» (вертикально)
    const renderFilter = (k: ColKey) => {
        // Оба поля — одинаковой ширины, строго друг под другом (симметрия рядов «от» / «до»)
        if (COL_DEFS[k].filter === 'text') {
            return (
                <span style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                    <input placeholder="содержит" value={contains} onChange={e => setContains(e.target.value)} style={fTextInput} />
                    <input placeholder="не содержит" value={notContains} onChange={e => setNotContains(e.target.value)} style={fTextInput} />
                </span>
            );
        }
        return (
            <span style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                <input type="number" placeholder="от" value={ranges[k].min} onChange={e => setRange(k, 'min', e.target.value)} style={fInput} />
                <input type="number" placeholder="до" value={ranges[k].max} onChange={e => setRange(k, 'max', e.target.value)} style={fInput} />
            </span>
        );
    };

    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10, flexShrink: 0 }}>
                <div style={{ display: 'inline-flex', border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden' }}>
                    {REL_TABS.map((t, i) => {
                        const active = rel === t.key;
                        return (
                            <button key={t.key} onClick={() => setRel(t.key)}
                                style={{ padding: '5px 14px', fontSize: 12, fontWeight: 500, cursor: 'pointer', border: 'none',
                                    borderLeft: i > 0 ? '1px solid #e5e7eb' : 'none',
                                    background: active ? '#3b82f6' : '#fff', color: active ? '#fff' : '#374151' }}>{t.label}</button>
                        );
                    })}
                </div>
                <span style={{ fontSize: 12, color: 'var(--color-text-dim)', marginLeft: 'auto' }}>Кластеров: {filtered.length}</span>
                {onCollectPositions && (collecting ? (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontSize: 12, color: '#6b7280' }}>
                            Сбор {collecting.done}/{collecting.total}
                            {collecting.throttled ? <span style={{ color: '#f59e0b' }} title="WB ограничивает — часть не собрана"> · ⚠ {collecting.throttled}</span> : null}
                        </span>
                        {onStopPositions && (
                            <Tooltip text="Остановить сбор позиций"><button className="btn btn-danger btn-sm" style={{ fontSize: 12 }} onClick={onStopPositions}>■ Стоп</button></Tooltip>
                        )}
                    </span>
                ) : (
                    <Tooltip text="Собрать органические позиции по всем фразам (из поиска WB). Заполнит «Позиция»/«Была».">
                        <button className="btn btn-secondary btn-sm" style={{ fontSize: 12 }} onClick={onCollectPositions} disabled={clusters.length === 0}>▶ Собрать позиции</button>
                    </Tooltip>
                ))}
                <Tooltip text="Выгрузить в Excel"><button className="btn btn-secondary btn-sm" style={{ fontSize: 12 }} onClick={doExport} disabled={filtered.length === 0}>Excel</button></Tooltip>
            </div>

            {clusterLock && (
                <div style={{ color: '#92400e', background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, padding: '8px 12px', fontSize: 12, marginBottom: 8 }}>
                    ⓘ {clusterLock}
                </div>
            )}
            {minus?.error && <div style={{ color: '#ef4444', fontSize: 12, marginBottom: 8 }}>⚠️ {minus.error}</div>}
            {bids?.error && <div style={{ color: '#ef4444', fontSize: 12, marginBottom: 8 }}>⚠️ {bids.error}</div>}

            {filtered.length === 0 ? (
                <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>Нет кластеров по выбранному фильтру</div>
            ) : (
                // Прокручиваются строки, а не страница: скролл заполняет остаток flex-колонки,
                // sticky-шапка и tfoot-итоги остаются на виду при любой высоте хедера
                <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
                    <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                        <thead>
                            <tr>
                                <th style={{ ...thStyle, textAlign: 'center', width: CHECK_W, minWidth: CHECK_W, position: 'sticky', left: 0, zIndex: 6, background: '#fafafa' }}>
                                    <input type="checkbox" checked={allSelected} onChange={toggleAll} title="Выбрать все" />
                                </th>
                                {order.map((k, i) => {
                                    const def = COL_DEFS[k];
                                    const base = def.align === 'left' ? thLeft : thStyle;
                                    const dragging = dragKey === k;
                                    return (
                                        <th key={k}
                                            style={{
                                                ...base, cursor: dragging ? 'grabbing' : 'pointer',
                                                opacity: dragging ? 0.45 : 1,
                                                background: dragging ? '#eff6ff' : base.background,
                                                borderLeft: i > 0 ? COL_DIVIDER : undefined,
                                                ...frozenCell(i, dragging ? '#eff6ff' : '#fafafa', 5, freezeCols),
                                                boxShadow: dropShadow(k) ?? frozenCell(i, '', 0, freezeCols)?.boxShadow,
                                            }}
                                            title={def.title || 'Перетащите, чтобы переставить колонку'}
                                            draggable
                                            onDragStart={() => onDragStart(k)}
                                            onDragOver={e => onDragOverCol(k, e)}
                                            onDragEnd={onDragEnd}
                                            onDrop={onDrop}
                                            onClick={() => toggleSort(k)}>
                                            {def.label}{arrow(k)}
                                        </th>
                                    );
                                })}
                            </tr>
                            <tr>
                                <th style={{ ...filterThStyle, position: 'sticky', left: 0, zIndex: 4, background: '#f3f4f6' }}> </th>
                                {order.map((k, i) => (
                                    <th key={k} style={{
                                        ...(def_align(k) ? { ...filterThStyle, textAlign: 'left' as const } : filterThStyle),
                                        // Тонкая линия-разделитель между столбцами фильтров
                                        borderLeft: i > 0 ? COL_DIVIDER : undefined,
                                        ...frozenCell(i, '#f3f4f6', 3, freezeCols),
                                        boxShadow: dropShadow(k) ?? frozenCell(i, '', 0, freezeCols)?.boxShadow,
                                        opacity: dragKey === k ? 0.45 : 1,
                                    }}>{renderFilter(k)}</th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {filtered.map(c => {
                                const bg = rowBg(c) ?? '#fff';
                                return (
                                    <tr key={c.norm_query} style={{ color: '#111827', background: bg, cursor: 'pointer' }}
                                        onMouseEnter={() => setHoverKey(c.norm_query)}
                                        onMouseLeave={() => setHoverKey(h => (h === c.norm_query ? null : h))}
                                        onClick={() => toggleRow(c.norm_query)}>
                                        <td style={{ ...tdStyle, textAlign: 'center', position: 'sticky', left: 0, zIndex: 2, background: bg }} onClick={e => e.stopPropagation()}>
                                            <input type="checkbox" checked={selected.has(c.norm_query)} onChange={() => toggleRow(c.norm_query)} />
                                        </td>
                                        {order.map((k, i) => (
                                            <td key={k} style={{
                                                ...(COL_DEFS[k].align === 'left' ? tdLeft : tdStyle),
                                                // Фраза — строго в одну строку: перенос делал ряды разной высоты
                                                ...(k === 'norm_query' ? { whiteSpace: 'nowrap' as const, overflow: 'hidden', textOverflow: 'ellipsis' } : null),
                                                borderLeft: i > 0 ? COL_DIVIDER : undefined,
                                                ...frozenCell(i, dragKey === k ? '#eff6ff' : bg, 2, freezeCols),
                                                boxShadow: dropShadow(k) ?? frozenCell(i, '', 0, freezeCols)?.boxShadow,
                                                ...(dragKey === k ? { background: '#eff6ff' } : null),
                                            }}>{renderCell(k, c)}</td>
                                        ))}
                                    </tr>
                                );
                            })}
                        </tbody>
                        {/* Итоги по видимым строкам — внизу, тем же кеглем, что и таблица */}
                        <tfoot>
                            <tr style={{ background: '#f3f4f6', color: '#111827', fontWeight: 600 }}>
                                <td style={{ ...tfStyle, textAlign: 'center', left: 0, zIndex: 4 }} />
                                {order.map((k, i) => (
                                    <td key={k} style={{
                                        ...(COL_DEFS[k].align === 'left' ? { ...tfStyle, textAlign: 'left' as const } : tfStyle),
                                        borderLeft: i > 0 ? COL_DIVIDER : undefined,
                                        ...(freezeCols && i <= 1 ? { left: FROZEN_LEFT[i], zIndex: 4, width: i === 0 ? PHRASE_W : BID_W } : null),
                                    }}>
                                        {renderTotal(k)}
                                    </td>
                                ))}
                            </tr>
                        </tfoot>
                    </table>
                </div>
            )}

            {/* Нижняя панель действий — появляется при выборе фраз; flex-сосед снизу (не перекрывает итог) */}
            {selected.size > 0 && (
                <div style={{
                    flexShrink: 0, marginTop: 10,
                    display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
                    padding: '10px 12px', background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12,
                    boxShadow: '0 -6px 20px rgba(17,24,39,.08)',
                }}>
                    {confirmAsk ? (
                        <>
                            <span style={{ fontSize: 12, color: '#111827' }}>{confirmAsk.text}</span>
                            <button className="btn btn-primary btn-sm" style={{ fontSize: 12 }}
                                onClick={() => { confirmAsk.run(); setConfirmAsk(null); }}>Применить в WB</button>
                            <button className="btn btn-secondary btn-sm" style={{ fontSize: 12 }}
                                onClick={() => setConfirmAsk(null)}>Отмена</button>
                        </>
                    ) : (
                    <>
                    <span style={{ fontSize: 12, fontWeight: 600, color: '#1e3a8a' }}>Выбрано: {selected.size}</span>

                    <button className="btn btn-secondary btn-sm" style={{ fontSize: 12 }} onClick={copySelected}
                        title="Скопировать фразы в буфер обмена">Скопировать</button>

                    {minus?.onBulk && (
                        <>
                            <button className="btn btn-secondary btn-sm" style={{ fontSize: 12 }}
                                disabled={toReturn.length === 0 || !!clusterLock}
                                onClick={() => setConfirmAsk({ text: `Вернуть ${toReturn.length} фраз(ы) в кампанию?`, run: () => minus.onBulk!(toReturn, 'remove') })}
                                title={clusterLock ?? 'Вернуть фразы в кампанию (убрать из минус-фраз)'}>Включить {toReturn.length}</button>
                            <button className="btn btn-danger btn-sm" style={{ fontSize: 12 }}
                                disabled={toMinus.length === 0 || !!clusterLock}
                                onClick={() => setConfirmAsk({ text: `Отключить ${toMinus.length} фраз(ы) — добавить в минус?`, run: () => minus.onBulk!(toMinus, 'add') })}
                                title={clusterLock ?? 'Отключить фразы (добавить в минус-фразы)'}>Отключить {toMinus.length}</button>
                        </>
                    )}

                    {bids?.onBulkBid && (
                        <div style={{ position: 'relative' }}>
                            <button className="btn btn-secondary btn-sm" style={{ fontSize: 12 }}
                                disabled={biddable.length === 0 || !!clusterLock}
                                onClick={() => setBidPopover(v => !v)}
                                title={clusterLock ?? (biddable.length === 0 ? 'У выбранных фраз <100 показов — WB не примет ставку' : 'Задать ставку выбранным фразам')}>
                                Изменить ставку
                            </button>
                            {bidPopover && (
                                <BidPopover targets={biddable} onClose={() => setBidPopover(false)}
                                    onApply={(items, label) => setConfirmAsk({
                                        text: `Поставить ${label} на ${items.length} фраз(ы)?`,
                                        run: () => bids.onBulkBid!(items, label),
                                    })} />
                            )}
                        </div>
                    )}

                    {bids?.onBulkBid && (
                        <button className="btn btn-secondary btn-sm" style={{ fontSize: 12 }}
                            disabled={biddable.length === 0 || !!clusterLock}
                            onClick={() => setConfirmAsk({
                                text: `Сбросить ставку у ${biddable.length} фраз(ы) — вернуть ставку кампании?`,
                                run: () => bids.onBulkBid!(biddable.map(c => ({ cluster: c, bid: 0 })), 'сброс к ставке кампании'),
                            })}
                            title={clusterLock ?? 'Убрать свою ставку — фраза вернётся на ставку кампании'}>Сбросить ставку</button>
                    )}

                    <button className="btn btn-secondary btn-sm" style={{ fontSize: 12, marginLeft: 'auto' }}
                        onClick={() => { setSelected(new Set()); setBidPopover(false); }}>Снять выделение</button>
                    </>
                    )}
                </div>
            )}
        </div>
    );
}

// левый align для фильтр-ячейки (текстовая колонка)
function def_align(k: ColKey): boolean { return COL_DEFS[k].align === 'left'; }

/** Feature 1 — распределение бюджета по дням: горизонтальная полоса, ширина = spend_pct. */
export function DailyBudgetBar({ daily }: { daily: ClusterDailyPoint[] }) {
    if (!daily || daily.length === 0) return null;
    const palette = ['#60a5fa', '#818cf8', '#a78bfa', '#f472b6', '#fb923c', '#34d399'];
    const total = daily.reduce((s, d) => s + num(d.spend), 0);
    const dm = (iso: string) => iso.slice(5);
    return (
        <div>
            <div style={{ display: 'flex', width: '100%', height: 26, borderRadius: 8, overflow: 'hidden', border: '1px solid #e5e7eb' }}>
                {daily.map((d, i) => {
                    const pct = num(d.spend_pct);
                    return (
                        <div key={d.date} title={`${dm(d.date)} · ${clMoney(d.spend)} · ${clPct(d.spend_pct, 1)} бюджета`}
                            style={{ width: `${pct}%`, minWidth: pct > 0 ? 2 : 0, background: palette[i % palette.length], cursor: 'default' }} />
                    );
                })}
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', marginTop: 8 }}>
                {daily.map((d, i) => (
                    <span key={d.date} style={{ fontSize: 11, color: '#6b7280', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                        <span style={{ width: 8, height: 8, borderRadius: 2, background: palette[i % palette.length] }} />
                        {dm(d.date)}: {clPct(d.spend_pct, 0)}
                    </span>
                ))}
                <span style={{ fontSize: 11, color: '#9ca3af', marginLeft: 'auto' }}>Всего расход: {clMoney(total)}</span>
            </div>
        </div>
    );
}
