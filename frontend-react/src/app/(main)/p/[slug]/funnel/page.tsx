'use client';
import React, { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { exportToExcel } from '@/lib/utils';
import FunnelChart, { DEFAULT_CHART_SERIES } from './components/FunnelChart';
import { DayAnalysisTab } from './components/DayAnalysisTab';
import { AdsTab } from './components/AdsTab';
// Контролы и иконки берём из раздела-референса «Управление рекламой» —
// один и тот же элемент не должен выглядеть в двух разделах по-разному.
import SearchSelect from '../ads-manager/components/SearchSelect';
import AdsPeriodPicker from '../ads-manager/components/AdsPeriodPicker';
import { IcChart, IcDownload, IcColumns, IcSliders, IcSun, IcX, IcRefresh } from '../ads-manager/components/icons';
import WbThumb from '@/components/WbThumb';
import { PAGE_SHELL, PAGE_H1, TABLE_CARD, CARD_TOOLBAR, CARD_FOOTER, Segmented, ToggleBtn } from './components/funnelUi';
import FunnelTable, { MONO, buildSections, type SortState } from './components/FunnelTable';
import ColumnsPanel from './components/ColumnsPanel';
import GroupingPopover, { type Dim } from './components/GroupingPopover';
import ShadingPopover from './components/ShadingPopover';
import { COLUMN_BY_KEY, defaultLayout, type ColumnLayout, type Row, type Shading } from './components/columns';
import {
    loadLayout, saveLayout, loadPresets, savePresets, loadChain, saveChain, loadShading, saveShading,
    loadView, saveView, loadChartMetrics, saveChartMetrics, loadFilterState, saveFilterState,
    DEFAULT_CHAIN, type FunnelPreset, type FunnelView,
} from './components/presets';
import type { FunnelDayRow, FunnelSkuRow, FunnelGroupRow, FunnelAbcRow, FunnelSummary, FunnelFilters } from '@/types/api';

type GroupBy = 'day' | 'sku' | 'brand' | 'subject' | 'tag' | 'imt' | 'size' | 'abc';

// Короткие подписи: пилюля стоит вплотную к кнопке «Группировка», приставка «По»
// в каждом пункте только съедала ширину и выталкивала тулбар на вторую строку.
// dims — цепочка группировок за этим видом (1–4 уровня, состав задал Денис 31.07):
// пресет собирается той же механикой, что и «Группировка», поэтому после выбора видно,
// из чего он собран, и его можно достроить. Последний уровень везде «По артикулам» —
// строка раскрывается до товара (у склейки это ещё и источник миниатюр карточек).
const GROUP_TABS: { key: GroupBy; label: string; title: string; dims: string[] }[] = [
    { key: 'day', label: 'Дни', title: 'По дням', dims: ['day', 'subject', 'nm'] },
    { key: 'sku', label: 'Артикулы', title: 'По артикулам', dims: ['subject', 'day', 'nm'] },
    { key: 'brand', label: 'Бренды', title: 'По брендам', dims: ['brand', 'day', 'nm'] },
    { key: 'size', label: 'Размеры', title: 'По размеру', dims: ['subject', 'day', 'size', 'nm'] },
    { key: 'tag', label: 'Ярлыки', title: 'По ярлыкам', dims: ['subject', 'tag', 'day', 'nm'] },
    { key: 'imt', label: 'Склейки', title: 'По склейкам', dims: ['day', 'imt', 'nm'] },
    { key: 'abc', label: 'ABC', title: 'ABC анализ', dims: ['abc', 'subject', 'day', 'nm'] },
];

/** Измерения-отрезки времени: у них свой порядок по умолчанию — от свежих к старым. */
const TIME_DIMS = new Set(['day', 'week', 'month']);

/** Заголовок первой колонки по измерению верхнего уровня (в единственном числе). */
const DIM_HEADERS: Record<string, string> = {
    day: 'ДЕНЬ', week: 'НЕДЕЛЯ', month: 'МЕСЯЦ', subject: 'ПРЕДМЕТ', brand: 'БРЕНД',
    nm: 'АРТИКУЛ', size: 'РАЗМЕР', tag: 'ЯРЛЫК', imt: 'СКЛЕЙКА', abc: 'КАТЕГОРИЯ ABC',
};

const WD = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
/** «ср, 29.07» из ISO-даты. */
const dayLabel = (iso: string) => {
    const [y, m, d] = (iso || '').split('-').map(Number);
    if (!y) return iso;
    const dt = new Date(y, m - 1, d);
    return `${WD[dt.getDay()]}, ${String(d).padStart(2, '0')}.${String(m).padStart(2, '0')}`;
};
const isWeekend = (iso: string) => {
    const [y, m, d] = (iso || '').split('-').map(Number);
    if (!y) return false;
    const wd = new Date(y, m - 1, d).getDay();
    return wd === 0 || wd === 6;
};

/** Склейка — это набор карточек WB, а не число: вместо `#505511536` показываем
 *  миниатюры её товаров. nm_id берём из уже загруженных детей строки; если детей
 *  нет (или это не артикулы — например, уровень дней в цепочке), остаётся подпись. */
const GLUE_THUMBS = 5;
/** Товары склейки. В дереве их присылает бэк (`nm_ids` — дети приезжают отдельно и
 *  на момент отрисовки их ещё нет); в старых одноуровневых режимах берём из детей. */
const glueNmIds = (r: Row): number[] => {
    const fromApi = (r as { nm_ids?: number[] }).nm_ids;
    if (fromApi?.length) return fromApi;
    const seen = new Set<number>();
    const walk = (node: Row) => {
        const nm = (node as { nm_id?: number }).nm_id;
        if (typeof nm === 'number') seen.add(nm);
        for (const ch of node.children || []) walk(ch);
    };
    for (const ch of r.children || []) walk(ch);
    return Array.from(seen);
};

const GlueLabel = ({ row, label, depth }: { row: Row; label: string; depth: number }) => {
    const nms = useMemo(() => glueNmIds(row), [row]);
    const total = (row as { nm_total?: number }).nm_total ?? nms.length;
    // Алиас склейки (если задан) оставляем — он несёт смысл; голый «#id» заменяют картинки
    const named = !!label && !/^#\d+$/.test(label);
    if (nms.length === 0) {
        return (
            <span style={{ fontSize: 12, fontWeight: depth ? 500 : 600 }}>{label || '—'}</span>
        );
    }
    const rest = total - GLUE_THUMBS;
    return (
        <span title={`${label} · товаров: ${total}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
            {nms.slice(0, GLUE_THUMBS).map(nm => <WbThumb key={nm} nmId={nm} size={20} rounded={4} />)}
            {rest > 0 && <span style={{ fontSize: 10, color: '#94a3b8', fontWeight: 600 }}>+{rest}</span>}
            {named && <span style={{ fontSize: 12, fontWeight: depth ? 500 : 600, marginLeft: 4, overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</span>}
        </span>
    );
};

/** Строка товара: фото карточки WB + артикул продавца.
 *  Клик уводит в «Управление рекламой» с фильтром по этому артикулу; настройки
 *  группировки и фильтры воронки лежат в проекте, поэтому возврат ничего не теряет. */
const ArticleLabel = ({ nmId, text, depth, slug }: { nmId?: number; text: string; depth: number; slug: string }) => {
    const inner = (
        <>
            <WbThumb nmId={nmId} size={18} rounded={4} />
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{text}</span>
        </>
    );
    const base: React.CSSProperties = {
        display: 'inline-flex', alignItems: 'center', gap: 6, minWidth: 0,
        fontSize: 12, fontWeight: depth ? 500 : 600,
    };
    if (nmId == null) return <span style={base}>{inner}</span>;
    return (
        <Link href={`/p/${slug}/ads-manager?nm=${nmId}`} className="fn-article"
            title={`${text} · открыть рекламные кампании артикула`}
            onClick={e => e.stopPropagation()}   // клик по товару не должен ещё и сворачивать строку
            style={{ ...base, color: 'inherit', textDecoration: 'none' }}>
            {inner}
        </Link>
    );
};

/** Подпись фильтра над полем — как в референсе.
 *  Именно <div>, а не <label>: label пересылает клик на первый контрол внутри себя,
 *  из-за чего клик по фону выпадающего списка закрывал его и тут же открывал заново. */
const Field = ({ label, children }: { label: string; children: React.ReactNode }) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '.06em', textTransform: 'uppercase', color: '#94a3b8' }}>{label}</span>
        {children}
    </div>
);

/* ─── Main page ──────────────────────────────────────────────── */

export default function FunnelPage() {
    const routeParams = useParams();
    const slug = typeof routeParams?.slug === 'string' ? routeParams.slug : Array.isArray(routeParams?.slug) ? routeParams.slug[0] : '';

    const [tab, setTab] = useState<'funnel' | 'day-analysis' | 'ads'>('funnel');
    const [data, setData] = useState<FunnelDayRow[]>([]);
    const [detailed, setDetailed] = useState(false);
    const [summary, setSummary] = useState<FunnelSummary | null>(null);
    const [filters, setFilters] = useState<FunnelFilters>({ brands: [], subjects: [], vendor_codes: [], min_date: null, max_date: null });
    const [loading, setLoading] = useState(false);
    const [initDone, setInitDone] = useState(false);
    const [lastSyncAt, setLastSyncAt] = useState<string | null>(null);
    const [syncing, setSyncing] = useState(false);
    const [syncNote, setSyncNote] = useState('');       // фаза синка рядом с кнопкой
    const syncPoll = useRef<ReturnType<typeof setInterval> | null>(null);

    // Group by mode
    const [groupBy, setGroupBy] = useState<GroupBy>('day');
    const [skuData, setSkuData] = useState<FunnelSkuRow[]>([]);
    const [groupData, setGroupData] = useState<FunnelGroupRow[]>([]);
    const [abcData, setAbcData] = useState<FunnelAbcRow[]>([]);

    // Filters
    const [dateFrom, setDateFrom] = useState('');
    const [dateTo, setDateTo] = useState('');
    const [brand, setBrand] = useState('');
    const [subject, setSubject] = useState('');
    const [article, setArticle] = useState('');   // nm_id строкой; в списке показываем артикул продавца
    const [splitSubcat, setSplitSubcat] = useState(false); // «разбить по под-категории» на вкладке «По размеру»
    // Каталог артикулов (nm_id → артикул/предмет/бренд) — источник каскада фильтров.
    // Тот же эндпоинт, что и в «Управлении рекламой»: он строится по данным воронки.
    const [catalog, setCatalog] = useState<{ nm_id: number; vendor_code: string; subject: string; brand: string }[]>([]);

    // Цепочка группировок: [] = старые одноуровневые режимы (groupBy),
    // непустая — дерево с бэка (/funnel/tree) любой глубины и порядка
    // Базовая цепочка: день → предмет → артикул. Выбор пользователя переживает перезагрузку.
    const [chain, setChain] = useState<string[]>(DEFAULT_CHAIN);
    const [chainRows, setChainRows] = useState<Row[]>([]);
    /* Дерево грузится по уровню: сразу приезжает только верхний, ветки — по клику.
     * Полное дерево на реальных данных — ~25 тыс. узлов и ~20 МБ, из которых видно
     * тридцать строк; ключ кэша — путь ключей узлов от корня. */
    const [subtrees, setSubtrees] = useState<Map<string, Row[]>>(new Map());
    const loadingPaths = useRef<Set<string>>(new Set());
    const [dimCatalog, setDimCatalog] = useState<Dim[]>([]);
    const [maxChain, setMaxChain] = useState(4);
    const [groupingOpen, setGroupingOpen] = useState(false);
    const [presetsOpen, setPresetsOpen] = useState(false);
    const groupBtnRef = useRef<HTMLButtonElement>(null);   // якорь выезжающей панели группировки
    const thrBtnRef = useRef<HTMLButtonElement>(null);     // якорь панели подсветки
    const [thresholdsOpen, setThresholdsOpen] = useState(false);
    const [shading, setShading] = useState<Shading>('bar');

    // Отображение: раскладка колонок, сортировка, панель колонок, режим карточки
    const [layout, setLayout] = useState<ColumnLayout>(() => defaultLayout());
    const [sort, setSort] = useState<SortState | null>(null);
    const [columnsOpen, setColumnsOpen] = useState(false);
    const [presets, setPresets] = useState<FunnelPreset[]>([]);
    const [activePreset, setActivePreset] = useState('');

    // Карточка показывает либо таблицу группировок, либо график динамики по дням
    const [view, setView] = useState<FunnelView>('table');
    const [chartMetrics, setChartMetrics] = useState<string[]>(DEFAULT_CHART_SERIES);
    const [chartRows, setChartRows] = useState<FunnelDayRow[]>([]);
    const [chartLoading, setChartLoading] = useState(false);

    // Раскладка колонок и пресеты живут в localStorage проекта (грузим в эффекте — SSR)
    useEffect(() => {
        if (!slug) return;
        setLayout(loadLayout(slug));
        setPresets(loadPresets(slug));
        setChain(loadChain(slug));
        setShading(loadShading(slug));
        setView(loadView(slug));
        setChartMetrics(loadChartMetrics(slug, DEFAULT_CHART_SERIES));
    }, [slug]);
    const applyLayout = (next: ColumnLayout) => { setLayout(next); if (slug) saveLayout(slug, next); };
    const applyShading = (next: Shading) => { setShading(next); if (slug) saveShading(slug, next); };
    const applyView = (next: FunnelView) => { setView(next); if (slug) saveView(slug, next); };
    const toggleChartMetric = (key: string) => setChartMetrics(prev => {
        const next = prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key];
        if (slug) saveChartMetrics(slug, next);
        return next;
    });

    /* Остатки и себестоимость склада едут отдельным запросом — включаем их не кнопкой,
     * а по факту: в раскладке выбрана хотя бы одна складская колонка. В группировке
     * «по дням» они бессмысленны (остаток — снимок, а не поток), там запрос не шлём. */
    const wantsStock = useMemo(() => layout.visible.some(k => COLUMN_BY_KEY[k]?.extendedOnly), [layout]);
    const stockAvailable = !(chain.length === 0 && groupBy === 'day');
    const extended = wantsStock && stockAvailable;

    const loadFilters = useCallback(async () => {
        try {
            const f = await api.getFunnelFilters();
            setFilters(f);
            return f;
        } catch { return null; }
    }, []);

    /** Дерево по цепочке измерений. Пустая цепочка = обычные одноуровневые режимы. */
    /** at — фильтры «прямо сейчас»: на первом заходе состояние ещё не применилось
     *  реактом, а восстановленные из хранилища значения уже нужны запросу. */
    const loadTree = useCallback(async (dims: string[], df?: string, dt?: string,
                                        at?: { brand: string; subject: string; article: string }) => {
        const from = df || dateFrom;
        const to = dt || dateTo;
        if (!from || !to || dims.length === 0) return;
        setLoading(true);
        try {
            const res = await api.getFunnelTree({
                dims, date_from: from, date_to: to,
                brand: at?.brand ?? brand, subject: at?.subject ?? subject, vendor_code: at?.article ?? article,
                extended,
            });
            setChainRows((res.data || []) as Row[]);
            setSubtrees(new Map());   // фильтры/цепочка изменились — загруженные ветки протухли
        } catch (e: unknown) {
            console.error(e);
            setChainRows([]);
        } finally {
            setLoading(false);
        }
    }, [dateFrom, dateTo, brand, subject, article, extended]);

    /** Дети узла по пути: один узкий запрос вместо выгрузки всего дерева. */
    const loadBranch = useCallback(async (path: string[]) => {
        const key = path.join('\u0000');
        if (!dateFrom || !dateTo || loadingPaths.current.has(key)) return;
        loadingPaths.current.add(key);
        try {
            const res = await api.getFunnelTree({
                dims: chain, date_from: dateFrom, date_to: dateTo, brand, subject, vendor_code: article,
                extended, path, depth: 1,
            });
            setSubtrees(prev => new Map(prev).set(key, (res.data || []) as Row[]));
        } catch (e: unknown) {
            console.error(e);
            setSubtrees(prev => new Map(prev).set(key, []));   // не зависаем на «Загрузка…»
        } finally {
            loadingPaths.current.delete(key);
        }
    }, [chain, dateFrom, dateTo, brand, subject, article, extended]);

    const loadData = useCallback(async (df?: string, dt?: string, gb?: GroupBy,
                                        at?: { brand: string; subject: string; article: string }) => {
        const from = df || dateFrom;
        const to = dt || dateTo;
        const mode = gb || groupBy;
        const br = at?.brand ?? brand, sj = at?.subject ?? subject, ar = at?.article ?? article;
        if (!from || !to) return [];
        setLoading(true);
        try {
            const [res, sum] = await Promise.all([
                api.getFunnelData({ date_from: from, date_to: to, brand: br, vendor_code: ar, subject: sj, group_by: mode, extended, subcat: splitSubcat }),
                api.getFunnelSummary(from, to, br, sj),
            ]);
            if (mode === 'abc') {
                setAbcData((res.data || []) as unknown as FunnelAbcRow[]);
                setData([]); setSkuData([]); setGroupData([]);
            } else if (mode === 'sku') {
                setSkuData((res.data || []) as FunnelSkuRow[]);
                setData([]); setGroupData([]); setAbcData([]);
            } else if (mode === 'brand' || mode === 'subject' || mode === 'tag' || mode === 'imt' || mode === 'size') {
                setGroupData((res.data || []) as FunnelGroupRow[]);
                setData([]); setSkuData([]); setAbcData([]);
            } else {
                setData((res.data || []) as FunnelDayRow[]);
                setSkuData([]); setGroupData([]); setAbcData([]);
            }
            setDetailed(res.detailed || false);
            setSummary(sum);
            return res.data || [];
        } catch (e: unknown) {
            console.error(e);
            return [];
        } finally {
            setLoading(false);
        }
    }, [dateFrom, dateTo, brand, subject, article, groupBy, extended, splitSubcat]);

    /** Данные графика — всегда по дням: динамика не зависит от того, как сгруппирована таблица.
     *  Фильтры и период — общие, поэтому цифры графика сходятся со строкой ИТОГО. */
    const loadChartData = useCallback(async (df?: string, dt?: string) => {
        const from = df || dateFrom;
        const to = dt || dateTo;
        if (!from || !to) return;
        setChartLoading(true);
        try {
            const res = await api.getFunnelData({
                date_from: from, date_to: to, brand, vendor_code: article, subject, group_by: 'day',
                extended: false,
            });
            setChartRows((res.data || []) as FunnelDayRow[]);
        } catch (e: unknown) {
            console.error(e);
            setChartRows([]);
        } finally {
            setChartLoading(false);
        }
    }, [dateFrom, dateTo, brand, subject, article]);

    const loadSyncStatus = useCallback(async () => {
        try {
            const s = await api.getSyncStatus();
            if (s.last_syncs?.length > 0) {
                const last = s.last_syncs[0];
                setLastSyncAt(last.finished_at || last.started_at || null);
            }
        } catch { /* статус синка необязателен */ }
    }, []);

    /* ─── Синхронизация по кнопке ─────────────────────────────────────────────
     * Тот же единый синк, что и на вкладке «Реклама» этого раздела: запуск в фоне
     * + опрос прогресса. По завершении перечитываем таблицу и время синка.
     * ─────────────────────────────────────────────────────────────────── */
    const stopSyncPoll = () => {
        if (syncPoll.current) { clearInterval(syncPoll.current); syncPoll.current = null; }
    };

    const reloadCurrent = useCallback(() => {
        if (chain.length > 0) loadTree(chain); else loadData();
        if (view === 'chart') loadChartData();
    }, [chain, view, loadTree, loadData, loadChartData]);

    const handleSync = async () => {
        if (!dateFrom || !dateTo || syncing) return;
        setSyncing(true);
        setSyncNote('запуск…');
        try {
            await api.unifiedSync(dateFrom, dateTo);
        } catch {
            setSyncing(false); setSyncNote('');
            return;
        }
        stopSyncPoll();
        const poll = setInterval(async () => {
            try {
                const p = await api.getUnifiedSyncProgress();
                if (p.phase === 'done' || p.phase === 'error' || p.phase === 'idle') {
                    stopSyncPoll();
                    setSyncing(false);
                    setSyncNote(p.phase === 'error' ? 'ошибка синка' : '');
                    await loadSyncStatus();
                    reloadCurrent();
                    if (p.phase === 'error') setTimeout(() => setSyncNote(''), 8000);
                    return;
                }
                setSyncNote(
                    p.phase === 'campaigns' ? 'кампании'
                        : p.phase === 'budgets' ? `бюджеты ${p.budgets_done ?? 0}/${p.budgets_total ?? '?'}`
                            : p.phase === 'funnel' ? `дни ${p.funnel_days_done ?? 0}/${p.funnel_days_total ?? '?'}`
                                : 'обновление…');
            } catch { /* ошибки опроса не роняют синк */ }
        }, 5000);
        syncPoll.current = poll;
        // Страховка: не крутим спиннер вечно, если фон завис
        setTimeout(() => { if (syncPoll.current === poll) { stopSyncPoll(); setSyncing(false); setSyncNote(''); } }, 600000);
    };

    useEffect(() => () => stopSyncPoll(), []);

    // Init: load filters → set dates from DB range → load data
    useEffect(() => {
        (async () => {
            const f = await loadFilters();
            await loadSyncStatus();
            if (f?.min_date && f?.max_date) {
                // Default: last 30 days excluding today (funnel data for today is incomplete)
                const today = new Date();
                const yesterday = new Date(today);
                yesterday.setDate(today.getDate() - 1);
                const thirtyDaysAgo = new Date(today);
                thirtyDaysAgo.setDate(today.getDate() - 30);
                const defaultFrom = thirtyDaysAgo.toISOString().slice(0, 10);
                const yesterdayStr = yesterday.toISOString().slice(0, 10);
                const defaultTo = yesterdayStr < f.max_date ? yesterdayStr : f.max_date;

                // Период и фильтры прошлого захода: перезагрузка не должна сбрасывать
                // то, ЧТО смотришь. Даты подрезаем под доступный диапазон — сохранённый
                // период мог протухнуть, пока раздел не открывали.
                const saved = loadFilterState(slug);
                const startFrom = saved ? (saved.dateFrom < f.min_date ? f.min_date : saved.dateFrom) : defaultFrom;
                const startTo = saved ? (saved.dateTo > f.max_date ? f.max_date : saved.dateTo) : defaultTo;
                setDateFrom(startFrom);
                setDateTo(startTo);
                if (saved) { setBrand(saved.brand); setSubject(saved.subject); setArticle(saved.article); }
                const startChain = loadChain(slug);
                if (startChain.length > 0) await loadTree(startChain, startFrom, startTo, saved ?? undefined);
                else await loadData(startFrom, startTo, undefined, saved ?? undefined);
            }
            setInitDone(true);
        })();
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        (async () => {
            try {
                const rows = await api.getAdArticleCatalog();
                setCatalog(rows.map(r => ({ nm_id: r.nm_id, vendor_code: r.vendor_code || String(r.nm_id), subject: r.subject || '', brand: r.brand || '' })));
            } catch { /* без каталога фильтры просто не сужаются */ }
        })();
    }, []);

    // Каталог измерений для конструктора цепочки
    useEffect(() => {
        (async () => {
            try {
                const res = await api.getFunnelDimensions();
                setDimCatalog(res.dimensions || []);
                if (res.max_chain) setMaxChain(res.max_chain);
            } catch { /* конструктор просто не откроется */ }
        })();
    }, []);

    useEffect(() => {
        if (!initDone || !dateFrom || !dateTo) return;
        if (chain.length > 0) loadTree(chain); else loadData();
    }, [dateFrom, dateTo, brand, subject, article, extended, splitSubcat, chain]); // eslint-disable-line react-hooks/exhaustive-deps

    // Период и фильтры переживают перезагрузку
    useEffect(() => {
        if (!initDone || !slug || !dateFrom || !dateTo) return;
        saveFilterState(slug, { dateFrom, dateTo, brand, subject, article });
    }, [initDone, slug, dateFrom, dateTo, brand, subject, article]);

    // График грузим только когда он открыт: закрытая вкладка не должна дёргать бэк
    useEffect(() => {
        if (!initDone || view !== 'chart' || !dateFrom || !dateTo) return;
        loadChartData();
    }, [initDone, view, dateFrom, dateTo, brand, subject, article]); // eslint-disable-line react-hooks/exhaustive-deps

    /** Короткая метка синка для тулбара: сегодняшний — только время, иначе «дд.мм ЧЧ:ММ».
     *  Полная дата остаётся в подсказке — иначе тулбар не влезает в одну строку. */
    const syncShort = (iso: string | null) => {
        if (!iso) return '—';
        try {
            const d = new Date(iso);
            const time = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
            const today = new Date();
            const sameDay = d.toDateString() === today.toDateString();
            return sameDay ? time : `${d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })} ${time}`;
        } catch { return '—'; }
    };

    const formatSyncDate = (iso: string | null) => {
        if (!iso) return '—';
        try {
            return new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
        } catch { return iso; }
    };

    /* ─── Период ─────────────────────────────────────────────── */

    // Пустой выбор в пикере возвращает дефолт «30 дней по вчера» — без дат таблица пуста
    const applyPeriod = (f: string, t: string) => {
        if (!f || !t) {
            const today = new Date();
            const y = new Date(today); y.setDate(today.getDate() - 1);
            const from30 = new Date(today); from30.setDate(today.getDate() - 30);
            const yStr = y.toISOString().slice(0, 10);
            setDateFrom(from30.toISOString().slice(0, 10));
            setDateTo(filters.max_date && yStr > filters.max_date ? filters.max_date : yStr);
            return;
        }
        const lo = filters.min_date || '';
        const hi = filters.max_date || '';
        setDateFrom(lo && f < lo ? lo : f);
        setDateTo(hi && t > hi ? hi : t);
    };

    const periodDays = useMemo(() => {
        if (!dateFrom || !dateTo) return 0;
        return Math.round((new Date(dateTo).getTime() - new Date(dateFrom).getTime()) / 86400000) + 1;
    }, [dateFrom, dateTo]);
    const plural = (n: number, one: string, few: string, many: string) => {
        const m10 = n % 10, m100 = n % 100;
        if (m10 === 1 && m100 !== 11) return one;
        if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
        return many;
    };

    const anyFilter = !!(brand || subject || article);
    const resetFilters = () => {
        setBrand(''); setSubject(''); setArticle('');
        setActivePreset('');
    };

    /* ─── Пресеты ────────────────────────────────────────────── */

    /** Пресет хранит цепочку группировок — после его выбора видно, из чего собран вид
     *  («По дням → По предметам → По артикулам» в пилюле рядом с «Группировкой»). */
    const savePreset = () => {
        const name = window.prompt('Название пресета')?.trim();
        if (!name) return;
        const preset: FunnelPreset = { name, brand, subject, search: article, groupBy, extended, layout, chain };
        const next = [...presets.filter(p => p.name !== name), preset];
        setPresets(next); savePresets(slug, next); setActivePreset(name);
    };
    const applyPreset = (p: FunnelPreset) => {
        setBrand(p.brand); setSubject(p.subject); setArticle(p.search);
        applyLayout(p.layout);              // остатки едут вместе с раскладкой: складские колонки включены — грузим
        setActivePreset(p.name);
        setSort(null);
        // Пресеты старого формата цепочки не знают — у них остаётся одноуровневый режим
        const nextChain = p.chain ?? [];
        setChain(nextChain); saveChain(slug, nextChain);
        if (nextChain.length > 0) {
            loadTree(nextChain);
        } else {
            setGroupBy(p.groupBy as GroupBy);
            loadData(undefined, undefined, p.groupBy as GroupBy);
        }
    };
    const deletePreset = (name: string) => {
        const next = presets.filter(p => p.name !== name);
        setPresets(next); savePresets(slug, next);
        if (activePreset === name) setActivePreset('');
    };

    /* ─── Строки таблицы под текущую группировку ─────────────── */

    const abcGroups = useMemo(() => {
        if (groupBy !== 'abc') return [];
        const labels: Record<string, string> = { A: 'Категория A (80% выручки)', B: 'Категория B (15% выручки)', C: 'Категория C (5% выручки)' };
        return (['A', 'B', 'C'] as const).map(cat => {
            const items = abcData.filter(r => r.abc_revenue === cat) as unknown as Row[];
            const sum = (k: keyof Row) => items.reduce((s, r) => s + (Number(r[k]) || 0), 0);
            const ordersSum = sum('orders_sum_rub'), revenue = sum('revenue'), adv = sum('adv_sum');
            const views = sum('adv_views'), clicks = sum('adv_clicks'), orders = sum('orders_count');
            const openCard = sum('open_card'), cart = sum('add_to_cart');
            // СПП взвешиваем суммой заказов, выкуп — числом заказов (как в accumulate)
            const w = (rate: keyof Row, weight: keyof Row) => {
                let acc = 0, wt = 0;
                for (const r of items) { const x = Number(r[rate]) || 0, y = Number(r[weight]) || 0; if (x) { acc += x * y; wt += y; } }
                return wt > 0 ? acc / wt : 0;
            };
            return {
                abc_label: labels[cat], abc: cat, children: items,
                open_card: openCard, add_to_cart: cart, orders_count: orders, orders_sum_rub: ordersSum,
                revenue, adv_sum: adv, adv_views: views, adv_clicks: clicks,
                tax: sum('tax'), commission: sum('commission'), cost_total: sum('cost_total'), profit: sum('profit'),
                margin: revenue ? (sum('profit') / revenue) * 100 : 0,
                drr: ordersSum ? (adv / ordersSum) * 100 : 0,
                ctr: views ? (clicks / views) * 100 : 0,
                cpc: clicks ? adv / clicks : 0,
                cpm: views ? (adv / views) * 1000 : 0,
                avg_price: orders ? ordersSum / orders : 0,
                add_to_cart_pct: openCard ? (cart / openCard) * 100 : 0,
                cart_to_order_pct: cart ? (orders / cart) * 100 : 0,
                spp_rate: w('spp_rate', 'orders_sum_rub'), buyout_percent: w('buyout_percent', 'orders_count'),
                commission_rate: revenue ? (sum('commission') / revenue) * 100 : 0,
                wb_stock_cost: sum('wb_stock_cost'), own_stock_cost: sum('own_stock_cost'), wb_stock_qty: sum('wb_stock_qty'),
            } as Row & { abc_label: string; abc: string };
        });
    }, [groupBy, abcData]);

    const rows: Row[] = useMemo(() => {
        if (chain.length > 0) return chainRows;
        if (groupBy === 'day') return data as Row[];
        if (groupBy === 'sku') return skuData as Row[];
        if (groupBy === 'abc') return abcGroups as Row[];
        return groupData as Row[];   // brand / subject / tag / imt / size
    }, [chain, chainRows, groupBy, data, skuData, abcGroups, groupData]);

    /* ─── Каскад фильтров: предмет ↔ бренд ↔ артикул ───────────────────────
     * Каждый список сужается выбором в двух других, а выбор артикула сам
     * подставляет его предмет и бренд. Смена предмета/бренда сбрасывает
     * артикул, если он к ним больше не относится.
     * ─────────────────────────────────────────────────────────────────── */
    const uniqSorted = (arr: string[]) => Array.from(new Set(arr.filter(Boolean))).sort((a, b) => a.localeCompare(b, 'ru'));
    const subjectOptions = useMemo(() =>
        uniqSorted(catalog.filter(t => (!brand || t.brand === brand) && (!article || String(t.nm_id) === article)).map(t => t.subject)),
        [catalog, brand, article]);
    const brandOptions = useMemo(() =>
        uniqSorted(catalog.filter(t => (!subject || t.subject === subject) && (!article || String(t.nm_id) === article)).map(t => t.brand)),
        [catalog, subject, article]);
    const articleOptions = useMemo(() =>
        catalog.filter(t => (!subject || t.subject === subject) && (!brand || t.brand === brand))
            .sort((a, b) => a.vendor_code.localeCompare(b.vendor_code, 'ru')),
        [catalog, subject, brand]);

    const onArticle = (v: string) => {
        setArticle(v);
        if (v) { const t = catalog.find(x => String(x.nm_id) === v); if (t) { setSubject(t.subject); setBrand(t.brand); } }
    };
    const onSubject = (v: string) => {
        setSubject(v);
        if (article) { const t = catalog.find(x => String(x.nm_id) === article); if (t && v && t.subject !== v) setArticle(''); }
    };
    const onBrand = (v: string) => {
        setBrand(v);
        if (article) { const t = catalog.find(x => String(x.nm_id) === article); if (t && v && t.brand !== v) setArticle(''); }
    };

    const dimLabel = (key: string) => dimCatalog.find(d => d.key === key)?.label ?? key;

    /* Подпись кнопки «Пресеты» идёт от ТЕКУЩЕЙ цепочки, а не от того, что выбрали
     * когда-то: иначе имя пресета залипало на кнопке после ручной пересборки
     * группировки и врало, каким видом смотришь. */
    const quickView = useMemo(
        () => GROUP_TABS.find(g => g.dims.join('>') === chain.join('>')),
        [chain]);
    const savedPreset = presets.find(p => p.name === activePreset);
    const presetMatches = !!savedPreset && (savedPreset.chain ?? []).join('>') === chain.join('>');
    const presetLabel = presetMatches ? activePreset
        : quickView ? quickView.title
        : chain.length > 0 ? 'Своя группировка'
        : 'Пресеты';

    const groupTitle = chain.length > 0 ? (DIM_HEADERS[chain[0]] ?? dimLabel(chain[0]).toUpperCase())
        : groupBy === 'brand' ? 'БРЕНД' : groupBy === 'tag' ? 'ЯРЛЫК' : groupBy === 'imt' ? 'СКЛЕЙКА'
        : groupBy === 'size' ? 'КАТЕГОРИЯ → РАЗМЕР' : groupBy === 'subject' ? 'КАТЕГОРИЯ'
            : groupBy === 'sku' ? 'ТОВАР' : groupBy === 'abc' ? 'КАТЕГОРИЯ ABC' : 'ДЕНЬ';

    const groupLabelOf = (r: Row): string =>
        groupBy === 'brand' ? (r.brand || '—') : groupBy === 'tag' ? (r.tag || '—')
            : groupBy === 'imt' ? (r.imt_group || '—') : (r.subject || r.size || '—');

    const labelCell = (r: Row, depth: number): React.ReactNode => {
        // Дерево цепочки: вид подписи задаёт измерение уровня, а не режим страницы
        const dim = (r as { dim?: string }).dim;
        if (chain.length > 0 && dim) {
            const label = (r as { label?: string }).label ?? '';
            if (dim === 'day') return <span style={{ fontFamily: MONO, fontSize: 12 }}>{dayLabel(label)}</span>;
            if (TIME_DIMS.has(dim)) return <span style={{ fontFamily: MONO, fontSize: 12 }}>{label}</span>;
            // Только артикул продавца: бренд и предмет во вложенной строке — повтор
            // родительских уровней, они съедали вторую строку в каждой строке дерева
            if (dim === 'nm') return <ArticleLabel nmId={r.nm_id} text={label} depth={depth} slug={slug} />;
            if (dim === 'imt') return <GlueLabel row={r} label={label} depth={depth} />;
            const kids = r.children?.length || 0;
            return (
                <span style={{ fontSize: 12, fontWeight: depth ? 500 : 600 }}>
                    {label}
                    {kids > 0 && <span style={{ fontSize: 10, color: '#94a3b8', fontWeight: 400, marginLeft: 5 }}>({kids})</span>}
                </span>
            );
        }
        if (groupBy === 'day') {
            return (
                <span>
                    <span style={{ fontFamily: MONO, fontSize: 12, fontWeight: 500 }}>{dayLabel(r.date || '')}</span>
                    {detailed && r.vendor_code && <span style={{ fontSize: 11, color: '#94a3b8', marginLeft: 8 }}>{r.vendor_code}</span>}
                </span>
            );
        }
        if (groupBy === 'sku' || (depth > 0 && r.nm_id)) {
            return <ArticleLabel nmId={r.nm_id} text={String(r.vendor_code || r.nm_id)} depth={depth} slug={slug} />;
        }
        if (groupBy === 'abc') {
            const g = r as Row & { abc?: string; abc_label?: string };
            const color = g.abc === 'A' ? '#16a34a' : g.abc === 'B' ? '#f59e0b' : '#ef4444';
            return (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ width: 26, textAlign: 'center', borderRadius: 6, fontWeight: 700, fontSize: 12, color: '#fff', background: color }}>{g.abc}</span>
                    <span style={{ fontSize: 12, fontWeight: 600 }}>{g.abc_label}</span>
                </span>
            );
        }
        if (groupBy === 'imt') return <GlueLabel row={r} label={r.imt_group || '—'} depth={depth} />;
        const kids = r.children?.length || 0;
        return (
            <span style={{ fontSize: 12, fontWeight: depth ? 500 : 600 }}>
                {groupLabelOf(r)}
                {kids > 0 && <span style={{ fontSize: 10, color: '#94a3b8', fontWeight: 400, marginLeft: 5 }}>({kids})</span>}
            </span>
        );
    };

    /** Порядок по умолчанию, отдельно для каждого уровня дерева:
     *  уровень дат — от ближайшей к дальней, любой другой — по выручке вниз. */
    const defaultOrder = useCallback((rs: Row[]): Row[] => {
        if (rs.length < 2) return rs;
        const first = rs[0] as { dim?: string };
        const isTime = first.dim ? TIME_DIMS.has(first.dim) : chain.length === 0 && groupBy === 'day';
        if (isTime) {
            const key = (r: Row) => String((r as { sort_key?: string; label?: string }).sort_key ?? (r as { label?: string }).label ?? r.date ?? '');
            return [...rs].sort((a, b) => key(b).localeCompare(key(a)));
        }
        const rev = COLUMN_BY_KEY['revenue'];
        return [...rs].sort((a, b) => (rev.value(b) ?? 0) - (rev.value(a) ?? 0));
    }, [chain, groupBy]);

    const rowKey = (r: Row, i: number, depth: number) => {
        const n = r as { dim?: string; label?: string; abc?: string };
        if (chain.length > 0) return `${depth}:${n.dim ?? ''}:${n.label ?? ''}:${i}`;
        return `${depth}:${r.date ?? ''}${r.nm_id ?? ''}${n.abc ?? ''}${groupLabelOf(r)}:${i}`;
    };

    /* ─── Excel: ровно те колонки, что видит пользователь ────── */

    const [exporting, setExporting] = useState(false);

    const handleExportFunnel = async () => {
        const sections = buildSections(layout, extended);
        const cols = sections.flatMap(s => s.cols);
        const label = chain.length > 0 ? chain.map(dimLabel).join(' → ') : (GROUP_TABS.find(g => g.key === groupBy)?.title ?? '');
        // В дереве строки на экране — только верхний уровень (остальное грузится по клику).
        // Для выгрузки просим все уровни разом: это единственное место, где нужно дерево целиком.
        let src = rows;
        if (chain.length > 0) {
            setExporting(true);
            try {
                const res = await api.getFunnelTree({
                    dims: chain, date_from: dateFrom, date_to: dateTo, brand, subject, vendor_code: article,
                    extended, depth: chain.length,
                });
                src = (res.data || []) as Row[];
            } catch (e: unknown) {
                console.error(e);
                alert('Не удалось выгрузить полное дерево — попробуйте ещё раз');
                setExporting(false);
                return;
            }
            setExporting(false);
        }
        const flat: Record<string, string | number>[] = [];
        const labelOf = (r: Row) => String((r as { label?: string }).label ?? r.vendor_code ?? r.size ?? r.subcategory ?? r.nm_id ?? '');
        const push = (r: Row, prefix: string) => {
            const rec: Record<string, string | number> = { [groupTitle]: prefix };
            for (const c of cols) rec[c.label] = c.value(r) ?? '';
            flat.push(rec);
            (r.children || []).forEach(ch => push(ch, `${prefix} → ${labelOf(ch)}`));
        };
        src.forEach(r => push(r, chain.length > 0 ? labelOf(r) : groupBy === 'day' ? (r.date || '') : groupLabelOf(r)));
        if (flat.length === 0) { alert('Нет данных для экспорта'); return; }
        exportToExcel(flat, `Воронка_${label}${dateFrom && dateTo ? `_${dateFrom}_${dateTo}` : ''}`);
    };

    /* ─── Тулбар таблицы ─────────────────────────────────────── */

    const toolbar = (
        <div style={CARD_TOOLBAR}>
            {/* Группировка задаёт только таблицу: график всегда по дням за период */}
            {view === 'chart' ? (
                <span style={{ fontSize: 13, color: 'var(--color-text-dim)', whiteSpace: 'nowrap' }}>
                    Динамика по дням{chartLoading ? ' · загрузка…' : ''}
                </span>
            ) : (<>
            {/* Конструктор цепочки: пока цепочка пуста, работают быстрые одноуровневые режимы */}
            <button ref={groupBtnRef} onClick={() => setGroupingOpen(o => !o)} title="Собрать цепочку группировок любой глубины"
                style={{
                    display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 13, fontWeight: 600,
                    padding: '6px 12px', borderRadius: 20, cursor: 'pointer', whiteSpace: 'nowrap',
                    border: `1px solid ${chain.length ? '#7c3aed' : 'var(--color-border)'}`,
                    background: chain.length ? '#f5f3ff' : '#fff',
                    color: chain.length ? '#4c1d95' : '#374151',
                }}>
                <IcSliders size={14} />Группировка
                {chain.length > 0 && (
                    <span style={{ minWidth: 18, height: 18, borderRadius: 9, background: '#7c3aed', color: '#fff', fontSize: 11, fontWeight: 700, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: '0 5px' }}>{chain.length}</span>
                )}
            </button>
            {chain.length > 0 ? (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--color-text-dim)', border: '1px solid var(--color-border)', borderRadius: 20, padding: '5px 12px', background: '#fff', whiteSpace: 'nowrap' }}>
                    {chain.map(dimLabel).join(' → ')}
                    <span onClick={() => { setChain([]); saveChain(slug, []); setSort(null); loadData(); }} title="Вернуться к обычным режимам"
                        style={{ display: 'inline-flex', color: '#94a3b8', cursor: 'pointer' }}><IcX size={13} /></span>
                </span>
            ) : (
                // Быстрые виды переехали в дропдаун «Пресеты» — здесь остаётся только текущий
                <span style={{ fontSize: 13, fontWeight: 600, color: '#374151', whiteSpace: 'nowrap' }}>
                    {GROUP_TABS.find(g => g.key === groupBy)?.title ?? ''}
                </span>
            )}
            {chain.length === 0 && groupBy === 'size' && (
                <ToggleBtn on={splitSubcat} onClick={() => setSplitSubcat(v => !v)}
                    title="Добавить уровень под-категории (винтаж/обычные) под размером">
                    Под-категории
                </ToggleBtn>
            )}
            </>)}
            {/* Время последнего синка + кнопка «Обновить» — как в «Управлении рекламой» */}
            <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 10, alignItems: 'center', fontSize: 12 }}>
                <span title={`Последняя синхронизация: ${formatSyncDate(lastSyncAt)}`}
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: 'var(--color-text-dim)', cursor: 'default', whiteSpace: 'nowrap' }}>
                    {syncShort(lastSyncAt)}{syncNote && <span style={{ color: '#b45309' }}>· {syncNote}</span>}
                </span>
                <button onClick={handleSync} disabled={syncing || loading} className="btn btn-secondary btn-sm"
                    title="Догрузить данные воронки и рекламы за выбранный период"
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, whiteSpace: 'nowrap' }}>
                    <IcRefresh size={14} />{syncing ? 'Обновление…' : 'Обновить'}
                </button>
                {view === 'table' && (<>
                <ToggleBtn on={columnsOpen} onClick={() => setColumnsOpen(v => !v)} title="Настроить колонки и их группы">
                    <IcColumns size={14} />Колонки
                </ToggleBtn>
                <button ref={thrBtnRef} type="button" onClick={() => setThresholdsOpen(v => !v)}
                    title="Подсветка значений: цвет цифр и как показывать величину"
                    style={{
                        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 28, height: 28,
                        borderRadius: 8, cursor: 'pointer',
                        border: `1px solid ${thresholdsOpen ? 'var(--color-accent)' : 'var(--color-border)'}`,
                        background: thresholdsOpen ? '#eff6ff' : '#fff',
                        color: thresholdsOpen ? '#1e3a8a' : '#374151',
                    }}>
                    <IcSun size={15} />
                </button>
                <button onClick={handleExportFunnel} disabled={exporting} className="btn btn-secondary btn-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                    <IcDownload size={14} />{exporting ? 'Готовим…' : 'Excel'}
                </button>
                </>)}
                {/* Переключатель режима — крайний справа и в таблице, и в графике: иначе он
                    прыгал бы при скрытии соседних кнопок. Вид — как в «Управлении рекламой». */}
                <Segmented value={view} compact onChange={applyView} options={[
                    { key: 'table', label: 'По дням', title: 'Таблица по выбранной группировке' },
                    { key: 'chart', label: 'График', title: 'Динамика метрик по дням за период' },
                ]} />
            </span>
        </div>
    );

    return (
        <div className="animate-in" style={PAGE_SHELL}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10, flexShrink: 0 }}>
                <h1 style={PAGE_H1}><IcChart size={26} />Воронка продаж</h1>
                {periodDays > 0 && (
                    <span style={{ fontSize: 12, fontWeight: 600, color: '#64748b', background: '#f1f5f9', border: '1px solid var(--color-border)', borderRadius: 20, padding: '3px 12px', fontFamily: MONO }}>
                        {periodDays} {plural(periodDays, 'день', 'дня', 'дней')}
                    </span>
                )}
                {/* Прежняя воронка осталась отдельным маршрутом — на время привыкания */}
                <Link href={`/p/${slug}/funnel/legacy`} className="btn btn-secondary btn-sm"
                    title="Открыть прежний вид раздела"
                    style={{ marginLeft: 'auto', fontSize: 12, textDecoration: 'none', whiteSpace: 'nowrap' }}>
                    Старый вариант
                </Link>
                <span>
                    <Segmented value={tab} onChange={key => {
                        if (key === 'funnel') {
                            setTab('funnel');
                            // Funnel: exclude today (incomplete data from WB API)
                            const yesterday = new Date();
                            yesterday.setDate(yesterday.getDate() - 1);
                            const yesterdayStr = yesterday.toISOString().slice(0, 10);
                            if (dateTo > yesterdayStr) setDateTo(yesterdayStr);
                        } else if (key === 'ads') {
                            setTab('ads');
                            // Ads: include today (ad budgets update every 10 min)
                            const today = new Date();
                            const d30 = new Date(today);
                            d30.setDate(today.getDate() - 30);
                            const from30 = d30.toISOString().slice(0, 10);
                            const toNow = filters.max_date || today.toISOString().slice(0, 10);
                            if (dateFrom < from30) setDateFrom(from30);
                            setDateTo(toNow);
                        } else {
                            setTab('day-analysis');
                        }
                    }} options={[
                        { key: 'funnel', label: 'Воронка' },
                        { key: 'day-analysis', label: 'Анализ дня' },
                        { key: 'ads', label: 'Реклама' },
                    ]} />
                </span>
            </div>

            {/* ─── Фильтры: один голый ряд, как в «Управлении рекламой» ───
                Подписи над полями и отдельные ряды пресетов и синхронизации убраны —
                они съедали три ряда высоты. Название поля теперь в самом контроле
                («Предмет: все»), пресеты — дропдаун в этом же ряду, статус синка —
                справа в тулбаре таблицы. */}
            {(tab === 'funnel' || tab === 'ads') && (
                <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexShrink: 0, alignItems: 'center', flexWrap: 'wrap' }}>
                    <AdsPeriodPicker from={dateFrom} to={dateTo} placeholder="Период" minWidth={235} onApply={applyPeriod} />
                    <SearchSelect value={subject} onChange={onSubject} placeholder="Предмет: все" allLabel="Все предметы" minWidth={150} maxWidth={230}
                        options={subjectOptions.map(s => ({ value: s, label: s }))} />
                    <SearchSelect value={brand} onChange={onBrand} placeholder="Бренд: все" allLabel="Все бренды" minWidth={140} maxWidth={200}
                        options={brandOptions.map(b => ({ value: b, label: b }))} />
                    <SearchSelect value={article} onChange={onArticle} placeholder="Артикул: все" allLabel="Все артикулы" minWidth={160} maxWidth={240}
                        options={articleOptions.map(t => ({ value: String(t.nm_id), label: t.vendor_code }))} />
                    {/* Пресеты — дропдаун в том же ряду (в рекламе он устроен так же).
                        Внутри и быстрые виды (Дни/Артикулы/…/ABC), и сохранённые пресеты:
                        в тулбаре они занимали целый ряд, а переключают одно и то же — вид таблицы. */}
                    <div style={{ position: 'relative' }}>
                        <button type="button" className={`filter-trigger${presetMatches ? ' is-active' : ''}`} aria-expanded={presetsOpen} onClick={() => setPresetsOpen(o => !o)}
                            style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, minWidth: 130, background: 'var(--color-bg-card)', borderRadius: 8, padding: '6px 10px', fontSize: 13, color: presetMatches ? '#1e3a8a' : 'var(--color-text)', cursor: 'pointer' }}>
                            <span title={chain.length > 0 ? chain.map(dimLabel).join(' → ') : undefined}
                                style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{presetLabel}</span>
                            <span style={{ color: 'var(--color-text-dim)', fontSize: 11, flexShrink: 0 }}>⌄</span>
                        </button>
                        {presetsOpen && (<>
                            <div style={{ position: 'fixed', inset: 0, zIndex: 40 }} onClick={() => setPresetsOpen(false)} />
                            <div style={{ position: 'absolute', left: 0, top: '100%', marginTop: 6, zIndex: 41, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, boxShadow: '0 8px 24px rgba(0,0,0,0.12)', padding: 6, minWidth: 230 }}>
                                <div style={{ padding: '6px 8px 4px', fontSize: 10, fontWeight: 700, letterSpacing: '.05em', textTransform: 'uppercase', color: '#94a3b8' }}>Быстрые виды</div>
                                {GROUP_TABS.map(g => {
                                    const on = chain.join('>') === g.dims.join('>');
                                    return (
                                        <div key={g.key} className="menu-row" title={g.title}
                                            onClick={() => {
                                                setPresetsOpen(false);
                                                setSort(null);
                                                // Быстрый вид — это цепочка из одного уровня: она видна в пилюле
                                                // рядом с «Группировкой» и достраивается следующими уровнями
                                                setActivePreset('');
                                                setChain(g.dims); saveChain(slug, g.dims);
                                                loadTree(g.dims);
                                            }}
                                            style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 8px', borderRadius: 8, cursor: 'pointer', fontSize: 13, fontWeight: on ? 600 : 400, color: on ? '#1e3a8a' : undefined, background: on ? '#eff6ff' : undefined }}>
                                            {g.title}
                                        </div>
                                    );
                                })}
                                <div style={{ padding: '10px 8px 4px', borderTop: '1px solid #f1f2f4', marginTop: 4, fontSize: 10, fontWeight: 700, letterSpacing: '.05em', textTransform: 'uppercase', color: '#94a3b8' }}>Мои пресеты</div>
                                {presets.length === 0 && <div style={{ padding: '8px 10px', fontSize: 12, color: '#9ca3af' }}>Сохранённых пресетов пока нет</div>}
                                {presets.map(p => (
                                    <div key={p.name} onClick={() => { applyPreset(p); setPresetsOpen(false); }} className="menu-row"
                                        style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 8px', borderRadius: 8, cursor: 'pointer', fontSize: 13, background: presetMatches && activePreset === p.name ? '#eff6ff' : undefined }}>
                                        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</span>
                                        <span onClick={e => { e.stopPropagation(); deletePreset(p.name); }} title="Удалить пресет"
                                            style={{ display: 'inline-flex', color: '#94a3b8' }}><IcX size={13} /></span>
                                    </div>
                                ))}
                                <div onClick={() => { setPresetsOpen(false); savePreset(); }} className="menu-row"
                                    style={{ padding: '7px 8px', borderTop: '1px solid #f1f2f4', marginTop: 4, borderRadius: 8, cursor: 'pointer', fontSize: 13, color: 'var(--color-accent)', fontWeight: 600 }}>
                                    + Сохранить текущий вид
                                </div>
                            </div>
                        </>)}
                    </div>

                    {anyFilter && (
                        <button className="btn btn-secondary btn-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12 }} onClick={resetFilters}>
                            <IcX size={13} />Сбросить
                        </button>
                    )}
                </div>
            )}

            {tab === 'funnel' && (
                <>
                    <div className="glass-card static" style={TABLE_CARD}>
                        {toolbar}
                        {view === 'chart' ? (
                            <div style={{ flex: 1, minHeight: 0 }}>
                                <FunnelChart rows={chartRows as Row[]} layout={layout} selected={chartMetrics} onToggle={toggleChartMetric} />
                            </div>
                        ) : (
                        <FunnelTable
                            rows={rows}
                            layout={layout}
                            extended={extended}
                            loading={loading}
                            timeSeries={chain.length > 0 ? TIME_DIMS.has(chain[0]) : groupBy === 'day'}
                            shading={shading}
                            defaultOrder={defaultOrder}
                            labelHeader={groupTitle}
                            labelWidth={chain.length > 0 ? 244 : groupBy === 'day' ? 140 : 205}
                            labelCell={labelCell}
                            rowKey={rowKey}
                            childrenOf={chain.length > 0 ? undefined : (r => r.children)}
                            nodeKey={chain.length > 0 ? (r => String((r as { sort_key?: string; label?: string }).sort_key ?? (r as { label?: string }).label ?? '')) : undefined}
                            hasChildren={chain.length > 0 ? (r => !!(r as { has_children?: boolean }).has_children) : undefined}
                            childrenAt={chain.length > 0 ? (path => subtrees.get(path.join('\u0000'))) : undefined}
                            onExpandPath={chain.length > 0 ? loadBranch : undefined}
                            onPrefetchPath={chain.length > 0 ? loadBranch : undefined}
                            labelValue={chain.length > 0
                                ? (r => (r as { label?: string }).label || '')
                                : groupBy === 'day' ? (r => r.date || '') : (r => groupLabelOf(r))}
                            labelColor={chain.length > 0
                                ? (r => ((r as { dim?: string }).dim === 'day' && isWeekend((r as { label?: string }).label || '') ? '#dc2626' : undefined))
                                : groupBy === 'day' ? (r => (isWeekend(r.date || '') ? '#dc2626' : undefined)) : undefined}
                            sort={sort}
                            onSort={key => setSort(prev => (prev?.key !== key ? { key, dir: 'desc' } : prev.dir === 'desc' ? { key, dir: 'asc' } : null))}
                            emptyText={groupBy === 'day' ? 'Данные загружаются автоматически. Ожидайте синхронизации.' : 'Нет данных за выбранный период'}
                            footer={<div style={CARD_FOOTER}>
                                {chain.length > 0 ? `${dimLabel(chain[0])}: ${rows.length} · вложенность ${chain.map(dimLabel).join(' → ')}`
                                    : groupBy === 'day' ? `Всего дней: ${rows.length}${detailed ? ' · детализация по артикулам' : ''}`
                                    : groupBy === 'sku' ? `Всего товаров: ${rows.length} (топ-500 по сумме заказов)`
                                        : groupBy === 'abc' ? `Всего товаров: ${abcData.length}`
                                            : `Всего строк: ${rows.length}`}
                                {' · '}колонок: {buildSections(layout, extended).reduce((n, s) => n + s.cols.length, 0)} из {Object.keys(COLUMN_BY_KEY).length}
                            </div>}
                        />
                        )}
                    </div>
                </>
            )}

            {/* Соседние вкладки — стопка карточек: прокручиваем их внутри фикс-высоты экрана */}
            {tab !== 'funnel' && (
                <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', paddingRight: 2 }}>
                    {tab === 'day-analysis' && <DayAnalysisTab brand={brand} subject={subject} />}
                    {tab === 'ads' && <AdsTab dateFrom={dateFrom} dateTo={dateTo} brand={brand} subject={subject} />}
                </div>
            )}

            {columnsOpen && <ColumnsPanel layout={layout} onChange={applyLayout} onClose={() => setColumnsOpen(false)} />}
            {thresholdsOpen && (
                <ShadingPopover anchor={thrBtnRef.current} value={shading}
                    onChange={applyShading} onClose={() => setThresholdsOpen(false)} />
            )}
            {groupingOpen && (
                <GroupingPopover anchor={groupBtnRef.current} all={dimCatalog} active={chain} maxChain={maxChain}
                    onApply={dims => { setChain(dims); saveChain(slug, dims); setSort(null); setActivePreset(''); loadTree(dims); }}
                    onClose={() => setGroupingOpen(false)} />
            )}
        </div>
    );
}
