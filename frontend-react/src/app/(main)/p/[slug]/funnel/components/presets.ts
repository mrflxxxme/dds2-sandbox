import { reconcileLayout, type ColumnLayout, type Shading } from './columns';

/* ─── Пресеты воронки ──────────────────────────────────────────────────────
 * Снимок «как я смотрю на воронку»: фильтры, группировка, набор и порядок
 * колонок. Даты в пресет НЕ кладём — они протухают, период всегда текущий.
 * Хранилище — localStorage на проект (по решению 2026-07-30).
 * ─────────────────────────────────────────────────────────────────────── */

export interface FunnelPreset {
    name: string;
    brand: string;
    subject: string;
    search: string;
    groupBy: string;
    extended: boolean;
    layout: ColumnLayout;
    /** Цепочка группировок пресета — то, что видно в пилюле рядом с «Группировкой».
     *  У пресетов, сохранённых до появления цепочек, её нет: тогда работает groupBy. */
    chain?: string[];
}

const presetsKey = (slug: string) => `funnel_presets_v1:${slug}`;
// v4 — первым блоком «Ключевые метрики» (31.07.2026). Ключ бампаем при каждой смене
// раскладки по умолчанию: сохранённая раскладка иначе перекрывает её у всех, кто
// уже открывал раздел, и правку никто не увидит.
const layoutKey = (slug: string) => `funnel_columns_v4:${slug}`;
const chainKey = (slug: string) => `funnel_chain_v1:${slug}`;
const shadingKey = (slug: string) => `funnel_shading_v1:${slug}`;
const viewKey = (slug: string) => `funnel_view_v1:${slug}`;
const chartKey = (slug: string) => `funnel_chart_metrics_v1:${slug}`;
const filtersKey = (slug: string) => `funnel_filters_v1:${slug}`;

/** Цепочка группировок по умолчанию — день → предмет → артикул. */
export const DEFAULT_CHAIN = ['day', 'subject', 'nm'];

function read<T>(key: string): T | null {
    try {
        const raw = localStorage.getItem(key);
        return raw ? (JSON.parse(raw) as T) : null;
    } catch { return null; }   // SSR / приватный режим / битый JSON
}

function write(key: string, value: unknown): void {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* приватный режим */ }
}

export function loadPresets(slug: string): FunnelPreset[] {
    const list = read<FunnelPreset[]>(presetsKey(slug));
    if (!Array.isArray(list)) return [];
    return list.filter(p => p && typeof p.name === 'string').map(p => ({ ...p, layout: reconcileLayout(p.layout) }));
}

export function savePresets(slug: string, list: FunnelPreset[]): void {
    write(presetsKey(slug), list);
}

/** Текущая раскладка колонок — переживает перезагрузку и не зависит от пресетов. */
export function loadLayout(slug: string): ColumnLayout {
    return reconcileLayout(read<ColumnLayout>(layoutKey(slug)));
}

export function saveLayout(slug: string, layout: ColumnLayout): void {
    write(layoutKey(slug), layout);
}


/** Цепочка группировок: пустая — обычные одноуровневые режимы. */
export function loadChain(slug: string): string[] {
    const saved = read<string[]>(chainKey(slug));
    return Array.isArray(saved) ? saved.filter(k => typeof k === 'string') : [...DEFAULT_CHAIN];
}

export function saveChain(slug: string, chain: string[]): void {
    write(chainKey(slug), chain);
}

/** Режим подсветки величины в ячейке. */
export function loadShading(slug: string): Shading {
    const v = read<Shading>(shadingKey(slug));
    return v === 'text' || v === 'bar' ? v : 'bar';   // прежний режим «Заливка» больше не поддерживается
}

export function saveShading(slug: string, v: Shading): void {
    write(shadingKey(slug), v);
}

/** Режим карточки: таблица группировок или график динамики по дням. */
export type FunnelView = 'table' | 'chart';

export function loadView(slug: string): FunnelView {
    return read<FunnelView>(viewKey(slug)) === 'chart' ? 'chart' : 'table';
}

export function saveView(slug: string, v: FunnelView): void {
    write(viewKey(slug), v);
}

/* ─── Фильтры и период ────────────────────────────────────────────────────
 * Раньше перезагрузка страницы сбрасывала период и все фильтры на дефолт: раскладка
 * колонок и цепочка переживали её, а то, ЧТО именно смотришь, — нет.
 * ─────────────────────────────────────────────────────────────────────── */

export interface FunnelFilterState {
    dateFrom: string;
    dateTo: string;
    brand: string;
    subject: string;
    article: string;
}

export function loadFilterState(slug: string): FunnelFilterState | null {
    const v = read<Partial<FunnelFilterState>>(filtersKey(slug));
    if (!v || typeof v !== 'object') return null;
    const str = (x: unknown) => (typeof x === 'string' ? x : '');
    const state: FunnelFilterState = {
        dateFrom: str(v.dateFrom), dateTo: str(v.dateTo),
        brand: str(v.brand), subject: str(v.subject), article: str(v.article),
    };
    // Без дат состояние бесполезно: период всё равно возьмётся дефолтный
    return state.dateFrom && state.dateTo ? state : null;
}

export function saveFilterState(slug: string, state: FunnelFilterState): void {
    write(filtersKey(slug), state);
}

/** Выбранные линии графика — ключи колонок из каталога. */
export function loadChartMetrics(slug: string, fallback: string[]): string[] {
    const saved = read<string[]>(chartKey(slug));
    if (!Array.isArray(saved)) return [...fallback];
    const clean = saved.filter(k => typeof k === 'string');
    return clean.length ? clean : [...fallback];
}

export function saveChartMetrics(slug: string, keys: string[]): void {
    write(chartKey(slug), keys);
}
