'use client';
/**
 * Матрица «товар × склад продавца WB»: что стоит на FBS и что могли бы туда
 * поставить.
 *
 * Колонки — только склады со связкой на наши: без привязки поставить нечего.
 * Обе цифры ячейки приходят из общего превью остатков, поэтому эта вкладка и
 * «Остатки» не могут разойтись в числах.
 *
 * 🔴 Деньги (реализация, маржа) считаются ПО КАРТОЧКЕ и по складам не делятся —
 * воронка WB не знает, с какого склада уехала единица. Поэтому они живут
 * отдельными колонками справа, а не в ячейках складов.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, pluralRu } from '@/lib/utils';
import { exportToExcel } from '@/lib/utils';
import WbThumb from '@/components/WbThumb';
import { wbProductUrl } from '@/lib/wbMedia';
import type { FbsMatrix, FbsMatrixRow, FbsMatrixWarehouse } from '@/types/api';

const TREND_OPTIONS = [7, 14, 30] as const;

/**
 * Ключ сортировки. Склад кодируется как `wh:<id>` — колонок столько, сколько
 * складов, и перечислить их статически нельзя.
 */
export type SortKey =
    'article' | 'total_wb' | 'total_can' | 'fbo' | 'revenue' | 'margin' | `wh:${number}`;

/**
 * Ключи сортировки строки — КОРТЕЖ, а не одно число.
 *
 * Одного числа не хватало: по колонке склада почти все строки имеют `can = 0`
 * (везти туда нечего), они сходились в ничью, и порядок среди них оставался
 * тот, что пришёл с бэкенда. При прокрутке это читалось как «сортировка не
 * работает, товары появляются вразнобой». Ничьи разбиваем осмысленно:
 *
 *  1. сколько можем довезти (россыпь + коробá) — то, ради чего и сортируют;
 *  2. сколько там уже стоит — строка «13 / 0» содержательнее, чем пустая;
 *  3. общий потенциал по всем складам — стабильный хвост, чтобы порядок не
 *     «плавал» между перерисовками.
 *
 * Склад, у которого ячейки нет вовсе («—»), получает −1 и уходит ниже честного
 * нуля: «позиции там нет» — не то же самое, что «есть, но нечего везти».
 */
function sortKeys(row: FbsMatrixRow, key: SortKey): (number | string)[] {
    if (key === 'article') return [(row.article_seller || row.barcode || '').toLowerCase()];
    if (key === 'total_wb') return [Number(row.total_wb ?? 0)];
    if (key === 'total_can') return [Number(row.total_can ?? 0) + Number(row.total_boxed ?? 0)];
    // FBO неизвестен — вниз при сортировке по убыванию: это «нет данных», а не ноль
    if (key === 'fbo') return [row.fbo == null ? -1 : Number(row.fbo)];
    if (key === 'revenue') return [Number(row.revenue ?? 0)];
    if (key === 'margin') return [row.margin_pct == null ? -Infinity : Number(row.margin_pct)];
    const cell = row.cells?.[key.slice(3)];
    if (!cell) return [-1, -1, Number(row.total_can ?? 0) + Number(row.total_boxed ?? 0)];
    return [
        Number(cell.can ?? 0) + Number(cell.boxed ?? 0),
        Number(cell.wb ?? 0),
        Number(row.total_can ?? 0) + Number(row.total_boxed ?? 0),
    ];
}

/** Сравнение по кортежу; направление применяется ко всем ключам одинаково. */
export function compareRows(a: FbsMatrixRow, b: FbsMatrixRow, key: SortKey, dir: number): number {
    const ka = sortKeys(a, key);
    const kb = sortKeys(b, key);
    for (let i = 0; i < ka.length; i++) {
        const va = ka[i];
        const vb = kb[i];
        const cmp = typeof va === 'string' || typeof vb === 'string'
            ? String(va).localeCompare(String(vb), 'ru')
            : va - vb;
        if (cmp !== 0) return cmp * dir;
    }
    return 0;
}

/** Ячейка склада: слева — что стоит в WB, справа — что можем довезти, ниже — короба. */
function Cell({ wb, can, boxed, boxes }: {
    wb: number | null | undefined;
    can: number;
    boxed: number;
    boxes: number;
}) {
    const has = (wb ?? 0) > 0 || can > 0 || boxed > 0;
    if (!has) return <span className="sc-matrix-cell-empty">—</span>;
    // Пусто в кабинете при живом «можем» — главный сигнал: везти сюда.
    const gap = can > 0 && (wb ?? 0) === 0;
    return (
        <span
            title={`Стоит на FBS: ${wb == null ? 'неизвестно' : formatNumber(wb, 0)}\n`
                + `Можем сейчас (россыпь): ${formatNumber(can, 0)}`
                + (boxed > 0
                    ? `\nЕщё ${formatNumber(boxed, 0)} шт в ${formatNumber(boxes, 0)} коробах — `
                      + 'станут доступны после поштучной приёмки у ФФ. Сортировка колонки — '
                      + `по сумме ${formatNumber(can + boxed, 0)}.`
                    : '')}
        >
            <span style={{ fontWeight: 600, color: gap ? 'var(--color-danger)' : 'var(--color-text)' }}>
                {wb == null ? '—' : formatNumber(wb, 0)}
            </span>
            <span style={{ color: 'var(--color-text-dim)' }}> / </span>
            <span style={{ color: 'var(--color-text-muted)' }}>{formatNumber(can, 0)}</span>
            {boxed > 0 && (
                <span className="sc-matrix-boxed">
                    {' '}+{formatNumber(boxed, 0)} 📦
                </span>
            )}
        </span>
    );
}

/**
 * Срезы «что делать» — от мягкого к жёсткому. Взаимоисключающие: это ответ на
 * вопрос «чем заняться сейчас», а не набор галок, которые надо комбинировать.
 *
 *  `gap`   — можем поставить, а на складе пусто. Обычный недовоз.
 *  `nosale`— то же, и на FBO тоже ноль: товар не продаётся НИГДЕ, хотя он у нас есть.
 *  `boxes` — товар есть только коробами и только на этом ФФ: россыпи нет нигде,
 *            на FBO нет. Пока ФФ не вскроет короб и не примет поштучно, позиция
 *            мертва — а вскрыть больше неоткуда, других остатков нет.
 */
type GapFilter = 'gap' | 'nosale' | 'boxes';

const GAP_FILTER_LABEL: Record<GapFilter, string> = {
    gap: 'Не довезли',
    nosale: 'Нет в продаже',
    boxes: 'Вскрыть коробá',
};

const GAP_FILTER_HINT: Record<GapFilter, string> = {
    gap: 'Можем поставить, а на складе продавца пусто. Классический недовоз.',
    nosale: 'Можем поставить, на складе пусто И на FBO ноль — товар не продаётся '
        + 'нигде, хотя лежит у нас.',
    boxes: 'Товар есть только коробами и только на этом ФФ: россыпи нет ни на одном '
        + 'складе, на FBO тоже ноль. Продаваться начнёт только после поштучной '
        + 'приёмки — и взять его больше негде.',
};

/** Есть ли склад, куда можем везти, а там пусто. */
function hasGap(row: FbsMatrixRow, warehouses: FbsMatrixWarehouse[]): boolean {
    return warehouses.some(w => {
        const c = row.cells?.[String(w.wb_warehouse_id)];
        return !!c && (c.wb ?? 0) === 0 && c.can > 0;
    });
}

export function matchesGapFilter(
    row: FbsMatrixRow, filter: GapFilter, warehouses: FbsMatrixWarehouse[],
): boolean {
    if (filter === 'gap') return hasGap(row, warehouses);
    if (filter === 'nosale') return hasGap(row, warehouses) && row.fbo === 0;
    // «Вскрыть»: коробá есть, россыпи нет НИГДЕ и на FBO пусто — иначе товар
    // продаётся откуда-то ещё и вскрывать не срочно.
    return Number(row.total_boxed ?? 0) > 0
        && Number(row.total_can ?? 0) === 0
        && row.fbo === 0;
}

/**
 * FBO с раскрытием «где лежит» — панелью СБОКУ, а не под числом.
 *
 * Раскрытие внутри ячейки раздвигало строку и ломало сетку матрицы (колонок
 * столько, сколько складов). Панель позиционируется `fixed` по координатам
 * кнопки: `sc-matrix-wrap` прокручивается и обрезал бы любой `absolute`.
 */
function FboCell({ qty, split, open, onToggle }: {
    qty: number | null | undefined;
    split: { name: string; qty: number }[] | undefined;
    open: boolean;
    onToggle: (anchor: DOMRect | null) => void;
}) {
    if (qty == null) return <span className="sc-matrix-cell-empty">—</span>;
    const rows = split ?? [];
    if (!rows.length) {
        return <span style={{ color: 'var(--color-text-muted)' }}>{formatNumber(qty, 0)}</span>;
    }
    return (
        <button
            type="button"
            className="sc-matrix-fbo-btn"
            onClick={e => onToggle(open ? null : e.currentTarget.getBoundingClientRect())}
            title={open ? 'Свернуть список складов' : 'Показать, на каких складах лежит остаток'}
        >
            {formatNumber(qty, 0)}
            <span className="sc-matrix-fbo-caret">{open ? '◀' : '▶'}</span>
        </button>
    );
}

/** Плавающий список складов FBO. Закрывается кликом мимо и по Escape. */
function FboPopover({ rows, anchor, onClose }: {
    rows: { name: string; qty: number }[];
    anchor: DOMRect;
    onClose: () => void;
}) {
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
        window.addEventListener('keydown', onKey);
        window.addEventListener('scroll', onClose, true);
        return () => {
            window.removeEventListener('keydown', onKey);
            window.removeEventListener('scroll', onClose, true);
        };
    }, [onClose]);

    const WIDTH = 240;
    // Не вылезаем за правый край: у крайних колонок открываемся влево от кнопки.
    const spaceRight = window.innerWidth - anchor.right;
    const left = spaceRight > WIDTH + 16 ? anchor.right + 8 : anchor.left - WIDTH - 8;
    const top = Math.min(anchor.top, Math.max(8, window.innerHeight - 260));
    const total = rows.reduce((s, w) => s + w.qty, 0);

    return (
        <>
            <div className="sc-matrix-fbo-backdrop" onClick={onClose} />
            <div className="sc-matrix-fbo-pop" style={{ left, top, width: WIDTH }}>
                <div className="sc-matrix-fbo-pop-head">
                    Остаток на складах WB · {formatNumber(total, 0)} шт
                </div>
                <div className="sc-matrix-fbo-pop-body">
                    {rows.map(w => (
                        <div key={w.name} className="sc-matrix-fbo-row">
                            <span className="sc-matrix-fbo-name" title={w.name}>{w.name}</span>
                            <span>{formatNumber(w.qty, 0)}</span>
                        </div>
                    ))}
                </div>
                <div className="sc-matrix-fbo-pop-foot">
                    без СЦ, «в пути», возвратов, исключённых и сгоревших складов
                </div>
            </div>
        </>
    );
}

function Margin({ pct }: { pct: number | null | undefined }) {
    if (pct == null) return <span className="sc-matrix-cell-empty">—</span>;
    const color = pct < 0 ? 'var(--color-danger)'
        : pct < 15 ? 'var(--color-warning)'
        : 'var(--color-success)';
    return <span style={{ color, fontWeight: 600 }}>{formatNumber(pct, 1)}%</span>;
}

export default function FbsMatrixTab({ refreshTick }: { refreshTick: number }) {
    const [data, setData] = useState<FbsMatrix | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [trendDays, setTrendDays] = useState<7 | 14 | 30>(14);
    const [search, setSearch] = useState('');
    const [gapFilter, setGapFilter] = useState<GapFilter | ''>('');
    const [brand, setBrand] = useState('');
    const [subject, setSubject] = useState('');
    /** Сортировка: по умолчанию — самые крупные недовозы сверху. */
    const [sortKey, setSortKey] = useState<SortKey>('total_can');
    const [sortAsc, setSortAsc] = useState(false);
    /** Раскрытая разбивка FBO: строка + якорь кнопки для плавающей панели. */
    const [openFbo, setOpenFbo] = useState<{ nomId: number; anchor: DOMRect } | null>(null);

    /** Клик по заголовку: первый раз — по убыванию (интереснее всего), потом переворот. */
    const toggleSort = useCallback((key: SortKey) => {
        setSortKey(prev => {
            if (prev === key) {
                setSortAsc(a => !a);
                return prev;
            }
            setSortAsc(key === 'article');  // текст удобнее читать от А, числа — от большого
            return key;
        });
    }, []);

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.getFbsStockMatrix(trendDays);
            if (signal?.aborted) return;
            setData(res);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Не удалось загрузить матрицу');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [trendDays]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load, refreshTick]);

    // useMemo, а не `?? []`: новый литерал на каждый рендер пересобирал бы
    // фильтрацию строк вхолостую (react-hooks/exhaustive-deps).
    const warehouses = useMemo(() => data?.warehouses ?? [], [data]);

    /** Варианты разрезов берём из самих строк — ровно то, что есть в матрице. */
    const facets = useMemo(() => {
        const brands = new Set<string>();
        const subjects = new Set<string>();
        for (const r of data?.rows ?? []) {
            if (r.brand) brands.add(r.brand);
            if (r.subject) subjects.add(r.subject);
        }
        return {
            brands: [...brands].sort((a, b) => a.localeCompare(b, 'ru')),
            subjects: [...subjects].sort((a, b) => a.localeCompare(b, 'ru')),
        };
    }, [data]);

    const rows = useMemo(() => {
        const q = search.trim().toLowerCase();
        const filtered = (data?.rows ?? []).filter(r => {
            if (gapFilter && !matchesGapFilter(r, gapFilter, warehouses)) return false;
            if (brand && (r.brand ?? '') !== brand) return false;
            if (subject && (r.subject ?? '') !== subject) return false;
            if (!q) return true;
            return (
                (r.barcode ?? '').toLowerCase().includes(q)
                || (r.article_seller ?? '').toLowerCase().includes(q)
                || (r.brand ?? '').toLowerCase().includes(q)
                || (r.subject ?? '').toLowerCase().includes(q)
                || String(r.nm_id ?? '').includes(q)
            );
        });
        // Своя сортировка, а не TanStack: матрица — «живая» таблица с колонками
        // по числу складов, её на generic-компонент не переложить.
        const dir = sortAsc ? 1 : -1;
        return [...filtered].sort((a, b) => compareRows(a, b, sortKey, dir));
    }, [data, search, gapFilter, warehouses, brand, subject, sortKey, sortAsc]);

    /**
     * Итоги — по ВИДИМЫМ строкам (`rows`, уже после поиска, срезов и фильтров
     * бренда/предмета), а не по всей матрице: подводить черту под выборкой,
     * которой на экране нет, — прямой способ увести человека не туда.
     *
     * Маржа складывается ВЗВЕШЕННО (Σприбыль / Σреализация), а не средним по
     * строкам: среднее уравняло бы товар с реализацией 200 ₽ и товар с 260 000 ₽.
     */
    const totals = useMemo(() => {
        const byWarehouse: Record<string, { wb: number; can: number; boxed: number }> = {};
        for (const w of warehouses) {
            byWarehouse[String(w.wb_warehouse_id)] = { wb: 0, can: 0, boxed: 0 };
        }
        let totalWb = 0, totalCan = 0, totalBoxed = 0, fbo = 0, revenue = 0, profit = 0;
        for (const r of rows) {
            for (const w of warehouses) {
                const key = String(w.wb_warehouse_id);
                const c = r.cells?.[key];
                if (!c) continue;
                byWarehouse[key].wb += Number(c.wb ?? 0);
                byWarehouse[key].can += Number(c.can ?? 0);
                byWarehouse[key].boxed += Number(c.boxed ?? 0);
            }
            totalWb += Number(r.total_wb ?? 0);
            totalCan += Number(r.total_can ?? 0);
            totalBoxed += Number(r.total_boxed ?? 0);
            fbo += Number(r.fbo ?? 0);
            revenue += Number(r.revenue ?? 0);
            profit += Number(r.profit ?? 0);
        }
        return {
            byWarehouse,
            totalWb,
            totalCan,
            totalBoxed,
            fbo,
            revenue,
            marginPct: revenue > 0 ? (profit / revenue) * 100 : null,
        };
    }, [rows, warehouses]);

    /** Сколько строк под каждым срезом — цифра на чипе, чтобы не кликать вслепую. */
    const gapCounts = useMemo(() => {
        const acc: Record<GapFilter, number> = { gap: 0, nosale: 0, boxes: 0 };
        for (const r of data?.rows ?? []) {
            for (const key of ['gap', 'nosale', 'boxes'] as const) {
                if (matchesGapFilter(r, key, warehouses)) acc[key] += 1;
            }
        }
        return acc;
    }, [data, warehouses]);

    /** Стрелка в заголовке: показывает и направление, и что сортировка вообще есть. */
    const sortMark = useCallback(
        (key: SortKey) => (sortKey === key ? (sortAsc ? ' ↑' : ' ↓') : ' ⇅'),
        [sortKey, sortAsc],
    );

    const handleExport = () => {
        exportToExcel(
            rows.map(r => ({
                Товар: r.article_seller || r.barcode || '',
                Баркод: r.barcode ?? '',
                nmID: r.nm_id ?? '',
                Бренд: r.brand ?? '',
                Предмет: r.subject ?? '',
                ...Object.fromEntries(warehouses.map(w => {
                    const c = r.cells?.[String(w.wb_warehouse_id)];
                    return [w.name || `#${w.wb_warehouse_id}`,
                        `${c?.wb ?? '—'} / ${c?.can ?? 0}`];
                })),
                'Итого стоит': r.total_wb,
                'Итого можем': r.total_can,
                FBO: r.fbo ?? '',
                'FBO по складам': (r.fbo_by_warehouse ?? [])
                    .map(w => `${w.name}: ${w.qty}`).join('; '),
                Реализация: Number(r.revenue ?? 0),
                'Маржа, %': r.margin_pct ?? '',
            })),
            'Матрица FBS-складов',
        );
    };

    if (loading && !data) {
        return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка матрицы…</div>;
    }
    if (error) {
        return (
            <div className="glass-card" style={{ padding: 24 }}>
                <div style={{ color: 'var(--color-danger)', marginBottom: 12 }}>{error}</div>
                <button className="btn btn-secondary btn-sm" onClick={() => load()}>Повторить</button>
            </div>
        );
    }
    if (!warehouses.length) {
        return (
            <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                Нет складов продавца WB со связкой на наши склады.<br />
                Свяжите их на вкладке «Склады» — без привязки поставлять нечего.
            </div>
        );
    }

    return (
        <div className="sc-matrix-card">
            <div className="sc-matrix-filter-bar">
                <input
                    className="sc-matrix-search-input"
                    placeholder="Артикул, баркод, бренд, предмет, nmId"
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                />
                <select
                    className="form-input"
                    style={{ width: 170 }}
                    value={brand}
                    onChange={e => setBrand(e.target.value)}
                    title="Фильтр по бренду"
                >
                    <option value="">бренд: все</option>
                    {facets.brands.map(b => <option key={b} value={b}>{b}</option>)}
                </select>
                <select
                    className="form-input"
                    style={{ width: 170 }}
                    value={subject}
                    onChange={e => setSubject(e.target.value)}
                    title="Фильтр по предмету"
                >
                    <option value="">предмет: все</option>
                    {facets.subjects.map(s => <option key={s} value={s}>{s}</option>)}
                </select>
                <div className="sc-matrix-mode-toggle">
                    {TREND_OPTIONS.map(d => (
                        <button
                            key={d}
                            className={`btn btn-sm ${trendDays === d ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setTrendDays(d)}
                        >
                            {d} дн
                        </button>
                    ))}
                </div>
                <div className="sc-matrix-mode-toggle">
                    {(['gap', 'nosale', 'boxes'] as const).map(key => (
                        <button
                            key={key}
                            className={`btn btn-sm ${gapFilter === key ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setGapFilter(f => (f === key ? '' : key))}
                            title={GAP_FILTER_HINT[key]}
                            disabled={gapCounts[key] === 0 && gapFilter !== key}
                        >
                            {GAP_FILTER_LABEL[key]} · {formatNumber(gapCounts[key], 0)}
                        </button>
                    ))}
                </div>
                <button className="sc-matrix-excel-btn" onClick={handleExport}>📊 Excel</button>
            </div>

            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', margin: '0 0 12px' }}>
                В ячейке: <strong>стоит на FBS</strong> / <span style={{ color: 'var(--color-text-muted)' }}>можем сейчас</span>
                {' '}<span className="sc-matrix-boxed">+N 📦</span> — столько ещё лежит коробами и станет
                доступным после поштучной приёмки у ФФ (продать штуку из невскрытого короба нельзя).
                Красное слева — на складе пусто, а везти есть что.
                {' '}Матрица про физику: настройка «отдаём только то, чего нет на FBO» придерживает
                трансляцию на «Остатках», но «Можем» здесь не режет.
                {' '}Клик по заголовку сортирует; колонки складов и «Можем» — по сумме «сейчас + коробá».
                {data && !data.wb_stock_known && ' Остаток кабинета прочитать не удалось — слева прочерки.'}
                {' '}Реализация и маржа — по карточке за {trendDays} дн, по складам не делятся.
            </div>

            <div className="sc-matrix-wrap">
                <table className="sc-matrix-table sc-matrix-table-sticky">
                    <thead>
                        <tr>
                            <th
                                className="sc-matrix-th-fixed sc-matrix-th-sort"
                                style={{ left: 0, minWidth: 280 }}
                                onClick={() => toggleSort('article')}
                                title="Сортировать по названию товара"
                            >
                                Товар{sortMark('article')}
                            </th>
                            {warehouses.map((w: FbsMatrixWarehouse) => (
                                <th
                                    key={w.wb_warehouse_id}
                                    className="sc-matrix-th-num sc-matrix-th-sort"
                                    style={{ minWidth: 110 }}
                                    onClick={() => toggleSort(`wh:${w.wb_warehouse_id}`)}
                                    title={'Сортировать по тому, сколько можем поставить на склад '
                                        + `«${w.name || w.wb_warehouse_id}»: россыпь + то, что лежит коробами`}
                                >
                                    {w.name || `#${w.wb_warehouse_id}`}{sortMark(`wh:${w.wb_warehouse_id}`)}
                                </th>
                            ))}
                            <th
                                className="sc-matrix-th-num sc-matrix-th-sort"
                                onClick={() => toggleSort('total_wb')}
                                title="Сортировать по тому, что стоит на FBS суммарно"
                            >
                                Стоит{sortMark('total_wb')}
                            </th>
                            <th
                                className="sc-matrix-th-num sc-matrix-th-sort"
                                onClick={() => toggleSort('total_can')}
                                title="Сортировать по тому, сколько можем поставить суммарно: россыпь + коробá"
                            >
                                Можем{sortMark('total_can')}
                            </th>
                            <th
                                className="sc-matrix-th-num sc-matrix-th-sort"
                                onClick={() => toggleSort('fbo')}
                                title={'Остаток на складах WB (FBO). Клик по числу в строке раскрывает, '
                                    + 'на каких именно складах он лежит. Без СЦ, «в пути», возвратов '
                                    + 'от клиентов, исключённых и сгоревших складов'}
                            >
                                FBO{sortMark('fbo')}
                            </th>
                            <th
                                className="sc-matrix-th-num sc-matrix-th-sort"
                                onClick={() => toggleSort('revenue')}
                                title="Сортировать по реализации за период"
                            >
                                Реализация{sortMark('revenue')}
                            </th>
                            <th
                                className="sc-matrix-th-num sc-matrix-th-sort"
                                onClick={() => toggleSort('margin')}
                                title="Сортировать по марже"
                            >
                                Маржа{sortMark('margin')}
                            </th>
                        </tr>
                        {/* Итоги под текущими фильтрами — второй строкой шапки:
                            подводить черту имеет смысл там, где на неё смотрят,
                            а не в конце полутора тысяч строк. Цифры обязаны
                            меняться вместе с выдачей, иначе строка врёт про то,
                            что видно на экране. */}
                        {rows.length > 0 && (
                            <tr className="sc-matrix-totals">
                                <th className="sc-matrix-th-fixed">
                                    Итого · {formatNumber(rows.length, 0)}{' '}
                                    {pluralRu(rows.length, ['товар', 'товара', 'товаров'])}
                                </th>
                                {warehouses.map(w => {
                                    const t = totals.byWarehouse[String(w.wb_warehouse_id)];
                                    return (
                                        <th key={w.wb_warehouse_id} className="sc-matrix-th-num">
                                            <span title={`Стоит на FBS: ${formatNumber(t?.wb ?? 0, 0)}\n`
                                                + `Можем сейчас (россыпь): ${formatNumber(t?.can ?? 0, 0)}`
                                                + ((t?.boxed ?? 0) > 0
                                                    ? `\nЕщё ${formatNumber(t.boxed, 0)} шт коробами`
                                                    : '')}
                                            >
                                                <strong>{formatNumber(t?.wb ?? 0, 0)}</strong>
                                                <span style={{ color: 'var(--color-text-dim)' }}> / </span>
                                                <span style={{ color: 'var(--color-text-muted)' }}>
                                                    {formatNumber(t?.can ?? 0, 0)}
                                                </span>
                                                {(t?.boxed ?? 0) > 0 && (
                                                    <span className="sc-matrix-boxed">
                                                        {' '}+{formatNumber(t.boxed, 0)} 📦
                                                    </span>
                                                )}
                                            </span>
                                        </th>
                                    );
                                })}
                                <th className="sc-matrix-th-num">
                                    <strong>{formatNumber(totals.totalWb, 0)}</strong>
                                </th>
                                <th className="sc-matrix-th-num">
                                    <span style={{ color: 'var(--color-text-muted)' }}>
                                        {formatNumber(totals.totalCan, 0)}
                                    </span>
                                    {totals.totalBoxed > 0 && (
                                        <span
                                            className="sc-matrix-boxed"
                                            title={`+${formatNumber(totals.totalBoxed, 0)} шт лежит коробами — `
                                                + 'станут доступны после поштучной приёмки у ФФ'}
                                        >
                                            {' '}+{formatNumber(totals.totalBoxed, 0)} 📦
                                        </span>
                                    )}
                                </th>
                                <th className="sc-matrix-th-num">{formatNumber(totals.fbo, 0)}</th>
                                <th className="sc-matrix-th-num">{formatNumber(totals.revenue, 0)}</th>
                                <th
                                    className="sc-matrix-th-num"
                                    title="Взвешенно: вся прибыль выборки делённая на всю её реализацию"
                                >
                                    <Margin pct={totals.marginPct} />
                                </th>
                            </tr>
                        )}
                    </thead>
                    <tbody>
                        {rows.map((r: FbsMatrixRow) => (
                            <tr key={r.nomenclature_id}>
                                <td className="sc-matrix-td-fixed" style={{ left: 0 }}>
                                    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                                        {r.nm_id ? (
                                            <a
                                                href={wbProductUrl(r.nm_id)}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                                title="Открыть карточку товара на Wildberries"
                                            >
                                                <WbThumb nmId={r.nm_id} size={36} />
                                            </a>
                                        ) : (
                                            <WbThumb nmId={null} size={36} />
                                        )}
                                        <div style={{ minWidth: 0 }}>
                                            <div style={{ fontWeight: 500 }}>{r.article_seller || r.barcode}</div>
                                            <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                                {r.barcode}{r.brand ? ` · ${r.brand}` : ''}
                                                {r.subject ? ` · ${r.subject}` : ''}
                                            </div>
                                        </div>
                                    </div>
                                </td>
                                {warehouses.map(w => {
                                    const c = r.cells?.[String(w.wb_warehouse_id)];
                                    return (
                                        <td key={w.wb_warehouse_id} className="sc-matrix-td-num">
                                            <Cell
                                                wb={c?.wb}
                                                can={c?.can ?? 0}
                                                boxed={c?.boxed ?? 0}
                                                boxes={c?.boxes ?? 0}
                                            />
                                        </td>
                                    );
                                })}
                                <td className="sc-matrix-td-num">
                                    <strong>{formatNumber(r.total_wb, 0)}</strong>
                                </td>
                                <td className="sc-matrix-td-num">
                                    <span style={{ color: 'var(--color-text-muted)' }}>
                                        {formatNumber(r.total_can, 0)}
                                    </span>
                                    {Number(r.total_boxed ?? 0) > 0 && (
                                        <span
                                            className="sc-matrix-boxed"
                                            title={`+${formatNumber(Number(r.total_boxed), 0)} шт в `
                                                + `${formatNumber(Number(r.total_boxes ?? 0), 0)} коробах — `
                                                + 'станут доступны после поштучной приёмки у ФФ'}
                                        >
                                            {' '}+{formatNumber(Number(r.total_boxed), 0)} 📦
                                        </span>
                                    )}
                                </td>
                                <td className="sc-matrix-td-num">
                                    <FboCell
                                        qty={r.fbo}
                                        split={r.fbo_by_warehouse}
                                        open={openFbo?.nomId === r.nomenclature_id}
                                        onToggle={anchor => setOpenFbo(
                                            anchor ? { nomId: r.nomenclature_id, anchor } : null,
                                        )}
                                    />
                                </td>
                                <td className="sc-matrix-td-num">
                                    {Number(r.revenue ?? 0) > 0
                                        ? formatNumber(Number(r.revenue), 0)
                                        : <span className="sc-matrix-cell-empty">—</span>}
                                </td>
                                <td className="sc-matrix-td-num"><Margin pct={r.margin_pct} /></td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {rows.length === 0 && (
                <div style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Ничего не найдено под текущими фильтрами.
                </div>
            )}

            {openFbo && (
                <FboPopover
                    rows={rows.find(r => r.nomenclature_id === openFbo.nomId)?.fbo_by_warehouse ?? []}
                    anchor={openFbo.anchor}
                    onClose={() => setOpenFbo(null)}
                />
            )}
        </div>
    );
}
