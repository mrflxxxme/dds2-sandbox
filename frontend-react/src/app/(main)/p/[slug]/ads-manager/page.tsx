'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { exportToExcel } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { IcMegaphone, IcRefresh, IcDownload, IcSliders, IcColumns, IcPause, IcPlay, IcClock, IcGear, IcHistory, IcExternal, IcSearch, IcX } from './components/icons';
import SearchSelect from './components/SearchSelect';
import AutopayModal from './components/AutopayModal';
import AutopayLogModal from './components/AutopayLogModal';
import WbThumb from './components/WbThumb';
import AdsPeriodPicker from './components/AdsPeriodPicker';
import InfoTip from './components/InfoTip';
import Tooltip from './components/Tooltip';
import { useToast } from './components/Toasts';
import { fmt, num, fmtPct, iso, STATUS_BADGE, thStyle, thLeft, tdStyle, tdLeft, wbCampaignUrl, campaignTypeBadge, autopayLabel } from './components/adsShared';
import type { AdsManagerCampaign, AdsAutopaySetting } from '@/types/api';

type SortDir = 'asc' | 'desc';

// Каталог артикулов для каскадных фильтров (предмет ↔ бренд ↔ артикул)
type CatalogItem = { nm_id: number; vendor_code: string; subject: string; brand: string };

// Фильтр по статусу (завершённые скрыты по умолчанию)
const STATUS_FILTERS: { key: string; label: string }[] = [
    { key: 'not_completed', label: 'Все, кроме завершённых' },
    { key: 'active', label: 'Активные' },
    { key: 'paused', label: 'Приостановленные' },
    { key: 'completed', label: 'Завершённые' },
    { key: 'all', label: 'Все' },
];

// Фильтр по виду рекламы: каждая опция задаёт тип оплаты + режим ставки (CPM)
const TYPE_FILTERS: { key: string; label: string; type: '' | 'cpm' | 'cpc'; mode: '' | 'unified' | 'manual' }[] = [
    { key: 'all', label: 'Все', type: '', mode: '' },
    { key: 'cpc', label: 'CPC', type: 'cpc', mode: '' },
    { key: 'cpm_unified', label: 'CPM единая', type: 'cpm', mode: 'unified' },
    { key: 'cpm_manual', label: 'CPM ручная', type: 'cpm', mode: 'manual' },
];

// Колонки списка кампаний (для настройки видимости и рендера).
// blockStart — начало логического блока: слева рисуем тонкую полупрозрачную линию-разделитель.
// w — ширина колонки (px) для table-layout: fixed; таблица всегда = ширине окна, без гориз. прокрутки
const CAMP_COLS: { key: string; label: string; sort?: keyof AdsManagerCampaign; align?: 'left' | 'center' | 'right'; title?: string; fixed?: boolean; blockStart?: boolean; w: number }[] = [
    { key: 'name', label: 'Кампания', align: 'left', fixed: true, w: 200 },
    { key: 'status', label: 'Статус', align: 'left', w: 96 },
    { key: 'photo', label: 'Товар', align: 'center', fixed: true, w: 44 },
    { key: 'budget', label: 'Остаток бюджета ₽', sort: 'budget', blockStart: true, title: 'Сверху — текущий остаток, снизу — бюджет за сегодня (остаток + расход)', w: 84 },
    { key: 'spend', label: 'Расход ₽', sort: 'spend_period', title: 'Сверху — расход за сегодня, снизу — за выбранный период (по умолчанию — вчера)', w: 84 },
    { key: 'clicks_period', label: 'Клики', sort: 'clicks_period', blockStart: true, title: 'Клики по рекламе кампании за период', w: 58 },
    { key: 'ctr', label: 'CTR', sort: 'ctr', title: 'Конверсия из показа в клик', w: 50 },
    { key: 'cpc', label: 'CPC ₽', sort: 'cpc', title: 'Стоимость 1 клика: расход кампании / клики за период', w: 50 },
    { key: 'cpl', label: 'CPL ₽', sort: 'cpl', title: 'Стоимость 1 корзины: расход кампании / корзины её товаров за период', w: 50 },
    { key: 'cpo', label: 'CPO ₽', sort: 'cpo', title: 'Стоимость 1 заказа: расход кампании / заказы её товаров за период', w: 54 },
    { key: 'ad_click_share', label: 'Рекл. клики %', sort: 'ad_click_share', title: 'Доля рекламных кликов от всех переходов товаров кампании. ≥50% — органика слабеет, ≥60% — критично', w: 68 },
    { key: 'drr', label: 'ДРР', sort: 'drr', blockStart: true, title: 'ДРР за вчера: расход кампании вчера / сумма заказов её товаров вчера', w: 54 },
    { key: 'spend_per_hour', label: 'Затраты/час ₽', sort: 'spend_per_hour', title: 'Средний расход в час: расход кампании за период / (число дней × 24 ч). Показывает, сколько денег кампания в среднем скручивает за час.', w: 78 },
    { key: 'budget_gap', label: 'Недобор бюджета ₽', sort: 'budget_gap', title: 'Сколько долить, чтобы кампания крутилась до конца дня, по скорости расхода до момента остановки. Только активные кампании, у которых сегодня кончился бюджет; минимум пополнения WB — 1000 ₽', w: 92 },
    { key: 'drr_plan', label: 'ДРР план ₽', sort: 'rev_yesterday', title: 'Рекомендованное пополнение на сегодня = сумма заказов ВЧЕРА × целевой ДРР (задаётся в шапке). Пример: 100 000 × 8% = 8 000', w: 122 },
    { key: 'nm_count', label: 'Товаров', sort: 'nm_count', blockStart: true, w: 56 },
    { key: 'autopay', label: 'Автопополнение', align: 'center', w: 100 },
    { key: 'wb', label: 'WB', align: 'center', w: 44 },
];
const CAMP_TOGGLE_KEYS = CAMP_COLS.filter(c => !c.fixed).map(c => c.key);
// Тонкая полупрозрачная линия-разделитель блоков
const BLOCK_DIVIDER = '1px solid rgba(17,24,39,0.08)';
// Тёмно-серая шапка таблицы кампаний (выделяется на фоне данных)
const cThStyle: React.CSSProperties = { ...thStyle, background: '#374151', color: '#e5e7eb', borderBottom: '1px solid #4b5563', position: 'sticky', top: 0, zIndex: 3 };
const cThLeft: React.CSSProperties = { ...cThStyle, textAlign: 'left' };

// Компактный список номеров страниц с многоточиями: 1 … 4 5 [6] 7 8 … 21
function buildPageList(current: number, total: number): (number | '…')[] {
    if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
    const nums = [...new Set([1, 2, current - 1, current, current + 1, total - 1, total])].filter(n => n >= 1 && n <= total).sort((a, b) => a - b);
    const out: (number | '…')[] = [];
    let prev = 0;
    for (const n of nums) { if (n - prev > 1) out.push('…'); out.push(n); prev = n; }
    return out;
}

export default function AdsManagerPage() {
    const routeParams = useParams();
    const router = useRouter();
    const toast = useToast();
    const slug = typeof routeParams?.slug === 'string' ? routeParams.slug : Array.isArray(routeParams?.slug) ? routeParams.slug[0] : '';
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    // Фильтры: каскад предмет ↔ бренд ↔ артикул + поиск по ID/названию
    const [brand, setBrand] = useState('');
    const [subject, setSubject] = useState('');
    const [article, setArticle] = useState('');   // выбранный nm_id (строкой) в фильтре «Артикул»
    const [catalog, setCatalog] = useState<CatalogItem[]>([]);
    const [search, setSearch] = useState('');
    // Фильтр по типу кампании: CPM/CPC, а для CPM — режим ставки
    const [campaignType, setCampaignType] = useState<'' | 'cpm' | 'cpc'>('');
    const [bidMode, setBidMode] = useState<'' | 'unified' | 'manual'>('');

    // Кампании
    const [campaigns, setCampaigns] = useState<AdsManagerCampaign[]>([]);
    const [campSort, setCampSort] = useState<{ field: keyof AdsManagerCampaign; dir: SortDir } | null>(null);
    const [syncing, setSyncing] = useState(false);
    const [autopay, setAutopay] = useState<Record<string, AdsAutopaySetting>>({});
    const [autopayModal, setAutopayModal] = useState<AdsManagerCampaign | null>(null);
    const [createMenuOpen, setCreateMenuOpen] = useState(false);
    const [autopayLogModal, setAutopayLogModal] = useState<AdsManagerCampaign | null>(null);
    const [stateBusy, setStateBusy] = useState<number | null>(null);  // кампания в процессе смены статуса
    // Тулбар списка: фильтр статуса (завершённые скрыты), видимость колонок, открытое меню
    const [statusFilter, setStatusFilter] = useState('not_completed');
    const [visibleCols, setVisibleCols] = useState<Set<string>>(() => new Set(CAMP_TOGGLE_KEYS));
    // Целевой ДРР для столбца «ДРР план» (%). Дефолт 8; грузится из localStorage в эффекте (чтобы не ломать SSR-гидрацию).
    const [targetDrr, setTargetDrr] = useState<number>(8);
    const onTargetDrrChange = (val: string) => {
        const n = Number(val.replace(',', '.').replace(/[^0-9.]/g, ''));
        const v = Number.isFinite(n) ? n : 0;
        setTargetDrr(v);
        try { localStorage.setItem('ads_drr_plan_pct', String(v)); } catch { /* SSR */ }
    };
    // Ручной ДРР% по кампаниям для «ДРР план»: базово = % из шапки; свой % отвязывает кампанию
    // от шапки (живёт сам), сумма (синим слева) = выручка вчера × этот %. ✕ сбрасывает к шапке.
    const [drrPct, setDrrPct] = useState<Record<number, number>>({});
    const setCampDrrPct = (cid: number, val: string) => {
        const cleaned = val.replace(',', '.').replace(/[^0-9.]/g, '');
        const n = Number(cleaned);
        setDrrPct(prev => {
            const next = { ...prev };
            if (cleaned === '' || !Number.isFinite(n)) delete next[cid];  // пусто → снова % из шапки
            else next[cid] = n;
            try { localStorage.setItem('ads_drr_plan_pct_overrides', JSON.stringify(next)); } catch { /* SSR */ }
            return next;
        });
    };
    const resetCampDrrPct = (cid: number) => setDrrPct(prev => {
        const next = { ...prev }; delete next[cid];
        try { localStorage.setItem('ads_drr_plan_pct_overrides', JSON.stringify(next)); } catch { /* SSR */ }
        return next;
    });
    const [openMenu, setOpenMenu] = useState<'filter' | 'cols' | null>(null);
    const [page, setPage] = useState(1);
    const [pageInput, setPageInput] = useState('');
    const PER_PAGE = 50;

    // Календарь = период метрик по ВСЕМ кампаниям (день или диапазон). Пусто → дефолт «вчера».
    const [periodFrom, setPeriodFrom] = useState('');
    const [periodTo, setPeriodTo] = useState('');
    const dateFrom = periodFrom || iso(new Date(Date.now() - 86400_000));
    const dateTo = periodTo || iso(new Date(Date.now() - 86400_000));

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
        setStateBusy(c.campaign_id);
        try {
            await api.setCampaignState(c.campaign_id, active);
            toast.success(active ? `Кампания «${c.name || c.campaign_id}» запущена` : `Кампания «${c.name || c.campaign_id}» приостановлена`);
            await loadCampaigns(dateFrom, dateTo);
        } catch (e) {
            toast.error(e instanceof Error ? e.message : 'Не удалось изменить статус кампании');
        } finally { setStateBusy(null); }
    }, [loadCampaigns, dateFrom, dateTo, toast]);

    // Каталог артикулов для каскадных фильтров (nm_id → предмет/бренд/название).
    const loadCatalog = useCallback(async () => {
        try {
            // Полный каталог артикулов (без топ-лимита/фильтра активности) — иначе часть артикулов выпадает из фильтра
            const rows = await api.getAdArticleCatalog();
            setCatalog(rows.map(r => ({ nm_id: r.nm_id, vendor_code: r.vendor_code || String(r.nm_id), subject: r.subject || '', brand: r.brand || '' })));
        } catch { /* каталог не критичен — фильтры просто будут пустыми */ }
    }, []);

    useEffect(() => { loadCatalog(); }, [loadCatalog]);  // каталог — один раз
    // Список кампаний — при входе и при смене периода календаря (метрики за выбранный день/диапазон)
    useEffect(() => { loadCampaigns(dateFrom, dateTo); }, [periodFrom, periodTo]);  // eslint-disable-line react-hooks/exhaustive-deps

    // Видимость колонок — переживает перезагрузку
    useEffect(() => {
        try {
            const raw = localStorage.getItem('ads_camp_cols');
            if (raw) {
                const set = new Set<string>(JSON.parse(raw));
                // Миграция: старые колонки расхода объединены в 'spend'
                if (set.has('spend_period') || set.has('spend_today')) set.add('spend');
                // Новые колонки CPL/CPO — один раз показываем тем, у кого набор сохранён до их появления
                // (маркер обязателен: без него скрытые колонки возвращались бы при каждой загрузке)
                if (!localStorage.getItem('ads_camp_cols_cpl_cpo')) {
                    set.add('cpl'); set.add('cpo');
                    localStorage.setItem('ads_camp_cols_cpl_cpo', '1');
                    localStorage.setItem('ads_camp_cols', JSON.stringify([...set]));
                }
                // Новые столбцы «Недобор бюджета» / «ДРР план» (заменили конв. корзина/заказ) — показать один раз
                if (!localStorage.getItem('ads_camp_cols_drrplan_gap')) {
                    set.add('budget_gap'); set.add('drr_plan');
                    localStorage.setItem('ads_camp_cols_drrplan_gap', '1');
                    localStorage.setItem('ads_camp_cols', JSON.stringify([...set]));
                }
                // «Затраты/час» (заменил маржинальность) — показать один раз
                if (!localStorage.getItem('ads_camp_cols_spendhr')) {
                    set.add('spend_per_hour');
                    localStorage.setItem('ads_camp_cols_spendhr', '1');
                    localStorage.setItem('ads_camp_cols', JSON.stringify([...set]));
                }
                setVisibleCols(set);
            }
            const drr = Number(localStorage.getItem('ads_drr_plan_pct'));
            if (Number.isFinite(drr) && drr > 0) setTargetDrr(drr);
            const rawOv = localStorage.getItem('ads_drr_plan_pct_overrides');
            if (rawOv) { const o = JSON.parse(rawOv); if (o && typeof o === 'object') setDrrPct(o); }
        } catch { /* SSR / битый JSON */ }
    }, []);
    const toggleCol = (k: string) => setVisibleCols(prev => {
        const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k);
        try { localStorage.setItem('ads_camp_cols', JSON.stringify([...n])); } catch { /* SSR */ }
        return n;
    });

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

    // Поиск по ID / названию кампании / nm_id её товаров
    const q = search.trim().toLowerCase();
    const matchCampaign = (name: string | null, campaignId: number, nmIds: number[]) =>
        !q || (name || '').toLowerCase().includes(q) || String(campaignId).includes(q) || nmIds.some(id => String(id).includes(q));

    // ─── Каскадные фильтры (предмет ↔ бренд ↔ артикул) по рекламируемым товарам ───
    const advNmSet = useMemo(() => new Set(campaigns.flatMap(c => c.nm_ids)), [campaigns]);
    const advTuples = useMemo(() => catalog.filter(t => advNmSet.has(t.nm_id)), [catalog, advNmSet]);
    const uniqSorted = (arr: string[]) => Array.from(new Set(arr.filter(Boolean))).sort((a, b) => a.localeCompare(b, 'ru'));
    // Опции каждого фильтра сужаются выбором в двух других
    const subjectOptions = useMemo(() =>
        uniqSorted(advTuples.filter(t => (!brand || t.brand === brand) && (!article || String(t.nm_id) === article)).map(t => t.subject)),
        [advTuples, brand, article]);
    const brandOptions = useMemo(() =>
        uniqSorted(advTuples.filter(t => (!subject || t.subject === subject) && (!article || String(t.nm_id) === article)).map(t => t.brand)),
        [advTuples, subject, article]);
    const articleOptions = useMemo(() =>
        advTuples.filter(t => (!subject || t.subject === subject) && (!brand || t.brand === brand))
            .sort((a, b) => a.vendor_code.localeCompare(b.vendor_code, 'ru')),
        [advTuples, subject, brand]);

    // Выбор артикула автоматически подставляет его предмет и бренд
    const onArticle = (v: string) => {
        setArticle(v);
        if (v) { const t = advTuples.find(x => String(x.nm_id) === v); if (t) { setSubject(t.subject); setBrand(t.brand); } }
    };
    // Смена предмета/бренда сбрасывает артикул, если он больше не подходит
    const onSubject = (v: string) => {
        setSubject(v);
        if (article) { const t = advTuples.find(x => String(x.nm_id) === article); if (t && v && t.subject !== v) setArticle(''); }
    };
    const onBrand = (v: string) => {
        setBrand(v);
        if (article) { const t = advTuples.find(x => String(x.nm_id) === article); if (t && v && t.brand !== v) setArticle(''); }
    };
    const resetFilters = () => { setBrand(''); setSubject(''); setArticle(''); setSearch(''); setCampaignType(''); setBidMode(''); };

    // Кампании: бренд/предмет — по товарам кампании; артикул — по nm_id (клиентский фильтр)
    const visibleCampaigns = campaigns
        .filter(c => matchCampaign(c.name, c.campaign_id, c.nm_ids))
        .filter(c => statusFilter === 'all' ? true
            : statusFilter === 'completed' ? c.status === 7
            : statusFilter === 'active' ? c.status === 9
            : statusFilter === 'paused' ? c.status === 11
            : c.status !== 7)  // not_completed (по умолчанию)
        .filter(c => !brand || c.brands.includes(brand))
        .filter(c => !subject || c.subjects.includes(subject))
        .filter(c => !article || c.nm_ids.includes(Number(article)))
        .filter(c => !campaignType || (c.campaign_type || '').toLowerCase() === campaignType)
        .filter(c => !bidMode || (c.bid_mode || '') === bidMode)
        .sort((a, b) => {
            // Базово (колонку не выбрали) — сначала созданные позже. По created_at (может быть
            // null → в конец), а при равных датах — по campaign_id: advertID у WB монотонно
            // растёт, поэтому больший ID = создан позже. Это же спасает, когда у пачки кампаний
            // created_at вырожден в момент синка (одинаков) — внутри сортируем по свежести ID.
            if (!campSort) {
                const byDate = (b.created_at || '').localeCompare(a.created_at || '');
                return byDate !== 0 ? byDate : b.campaign_id - a.campaign_id;
            }
            const av = Number(a[campSort.field]) || 0;
            const bv = Number(b[campSort.field]) || 0;
            return campSort.dir === 'asc' ? av - bv : bv - av;
        });
    const campFiltered = !!(q || brand || subject || article || campaignType || bidMode);

    // Пагинация: 50 на страницу. Сброс на 1-ю при смене фильтров/выборки.
    const pageCount = Math.max(1, Math.ceil(visibleCampaigns.length / PER_PAGE));
    useEffect(() => { setPage(1); }, [q, brand, subject, article, statusFilter, campaignType, bidMode, campSort]);
    const safePage = Math.min(page, pageCount);
    const pageCampaigns = visibleCampaigns.slice((safePage - 1) * PER_PAGE, safePage * PER_PAGE);
    const toggleCampSort = (field: keyof AdsManagerCampaign) =>
        setCampSort(prev => ({ field, dir: prev?.field === field && prev.dir === 'desc' ? 'asc' : 'desc' }));
    const campArrow = (field: keyof AdsManagerCampaign) => campSort?.field === field ? (campSort.dir === 'desc' ? ' ↓' : ' ↑') : '';

    // Excel-экспорт: выгружаем то, что видно (с учётом фильтров и сортировки)
    const exportCampaigns = () => exportToExcel(visibleCampaigns.map(c => ({
        'Кампания': c.name || `#${c.campaign_id}`, 'ID': c.campaign_id,
        'Тип': (c.campaign_type || '').toUpperCase() + (c.bid_mode === 'unified' ? ' · единая' : c.bid_mode === 'manual' ? ' · ручная' : ''),
        'Статус': c.status_label, 'Остаток бюджета ₽': num(c.budget), 'Расход сегодня ₽': num(c.spend_today),
        'Расход за период ₽': num(c.spend_period), 'Клики': num(c.clicks_period), 'CTR %': num(c.ctr), 'CPC ₽': num(c.cpc),
        'Рекл. клики %': num(c.ad_click_share), 'ДРР %': num(c.drr), 'Маржа %': num(c.margin),
        'Конв. корзина %': num(c.cr_cart), 'Конв. заказ %': num(c.cr_order), 'Товаров': c.nm_count, 'Бренды': c.brands.join(', '),
    })), `ads-campaigns_${dateFrom}_${dateTo}`);

    const campaignHref = (c: AdsManagerCampaign) => `/p/${slug}/ads-manager/campaign/${c.campaign_id}`;
    const openCampaign = (c: AdsManagerCampaign) => router.push(campaignHref(c));

    // Клик по строке ведёт в кампанию, но модификаторы отдаём браузеру (Cmd/Ctrl — новая вкладка),
    // а вложенные кнопки/ссылки гасят всплытие сами.
    const onRowClick = (e: React.MouseEvent, c: AdsManagerCampaign) => {
        if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        openCampaign(c);
    };
    // Средняя кнопка мыши по строке — новая вкладка (нативно так работают только ссылки).
    const onRowAux = (e: React.MouseEvent, c: AdsManagerCampaign) => {
        if (e.button !== 1) return;
        e.preventDefault();
        window.open(campaignHref(c), '_blank', 'noopener');
    };

    const visibleCampColumns = CAMP_COLS.filter(col => col.fixed || visibleCols.has(col.key));
    const renderCampCell = (key: string, c: AdsManagerCampaign): React.ReactNode => {
        switch (key) {
            case 'name': return (
                // Настоящая ссылка: правый клик даёт «Открыть в новой вкладке», Cmd/Ctrl+клик — тоже.
                <Link href={campaignHref(c)} onClick={e => e.stopPropagation()}
                    style={{ display: 'block', maxWidth: 240, color: 'inherit', textDecoration: 'none' }}>
                    <div style={{ fontWeight: 600, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.name || `#${c.campaign_id}`}</div>
                    <div style={{ fontSize: 10, color: '#9ca3af', display: 'flex', alignItems: 'center', gap: 5 }}>
                        #{c.campaign_id}
                        {(() => { const b = campaignTypeBadge(c); return <span style={{ padding: '0 5px', borderRadius: 4, background: b.bg, color: b.color, fontWeight: 700, fontSize: 9.5, letterSpacing: 0.2 }}>{b.label}</span>; })()}
                    </div>
                </Link>
            );
            case 'photo': return <WbThumb nmId={c.nm_ids[0]} size={38} />;
            case 'status': return (
                // Бейдж фиксированной ширины → иконки play/pause выстраиваются в ровный столбик
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className={`badge ${STATUS_BADGE[c.status] || 'badge-secondary'}`}
                        style={{ fontSize: 10, padding: '2px 6px', minWidth: 52, textAlign: 'center', display: 'inline-block' }}>{c.status_label}</span>
                    <span style={{ width: 24, display: 'inline-flex', justifyContent: 'center' }}>
                        {(c.status === 9 || c.status === 11) && (
                            <button onClick={e => { e.stopPropagation(); toggleCampaignState(c); }} disabled={stateBusy === c.campaign_id}
                                title={c.status === 9 ? 'Поставить на паузу' : 'Запустить кампанию'}
                                style={{ width: 22, height: 22, borderRadius: '50%', border: `1.5px solid ${c.status === 9 ? '#d97706' : '#059669'}`, background: '#fff', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: c.status === 9 ? '#d97706' : '#059669', padding: 0 }}>
                                {stateBusy === c.campaign_id ? '…' : c.status === 9 ? <IcPause size={12} /> : <IcPlay size={12} />}
                            </button>
                        )}
                    </span>
                </div>
            );
            case 'budget': {
                // Сверху — остаток бюджета, снизу полупрозрачным — бюджет за сегодня (остаток + расход сегодня)
                const todayTotal = num(c.budget) + num(c.spend_today);
                return (
                    <div style={{ lineHeight: 1.25 }}>
                        <div style={{ fontWeight: 600, color: c.budget <= 0 && c.status === 9 ? '#ef4444' : '#111827' }}>{fmt(c.budget)}</div>
                        <div style={{ fontSize: 10.5, color: '#9ca3af' }} title="Бюджет за сегодня: остаток + расход сегодня">{fmt(todayTotal)}</div>
                    </div>
                );
            }
            case 'spend': return (
                <div style={{ lineHeight: 1.25 }}>
                    <div style={{ fontWeight: 600 }} title="Расход сегодня">{fmt(c.spend_today)}</div>
                    <div style={{ fontSize: 10.5, color: '#9ca3af' }} title="Расход за выбранный период (по умолчанию — вчера)">{fmt(c.spend_period)}</div>
                </div>
            );
            case 'clicks_period': return fmt(c.clicks_period);
            case 'ctr': return fmtPct(c.ctr);
            case 'cpc': return c.cpc > 0 ? fmt(c.cpc) : '—';
            case 'cpl': return c.cpl > 0 ? fmt(c.cpl) : '—';
            case 'cpo': return c.cpo > 0 ? fmt(c.cpo) : '—';
            case 'ad_click_share': return <span style={{ fontWeight: 600, color: c.ad_click_share >= 60 ? '#ef4444' : c.ad_click_share >= 50 ? '#f59e0b' : c.ad_click_share > 0 ? '#374151' : '#9ca3af' }}>{c.ad_click_share > 0 ? fmtPct(c.ad_click_share) : '—'}</span>;
            case 'drr': return <span style={{ fontWeight: 600, color: c.drr > 30 ? '#ef4444' : c.drr > 7 ? '#f59e0b' : c.drr > 0 ? '#10b981' : '#9ca3af' }}>{c.drr > 0 ? fmtPct(c.drr) : '—'}</span>;
            case 'spend_per_hour': return num(c.spend_per_hour) > 0 ? fmt(c.spend_per_hour) : '—';
            case 'budget_gap': return num(c.budget_gap) > 0 ? <span style={{ fontWeight: 600, color: '#f59e0b' }}>{fmt(c.budget_gap)}</span> : '—';
            case 'drr_plan': {
                // Синим слева — сумма = выручка вчера × ДРР%; справа — редактируемый % кампании.
                // Базово % = из шапки; свой % отвязывает кампанию от шапки (фиолетовый), ✕ сбрасывает.
                const rev = num(c.rev_yesterday);
                const effPct = drrPct[c.campaign_id] ?? targetDrr;
                const isOv = drrPct[c.campaign_id] != null;
                const sum = rev > 0 && effPct > 0 ? Math.round(rev * effPct / 100) : 0;
                return (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, justifyContent: 'flex-end' }}>
                        {isOv && (
                            <button onClick={e => { e.stopPropagation(); resetCampDrrPct(c.campaign_id); }} aria-label="Сбросить ДРР% к шапке"
                                style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#9ca3af', fontSize: 13, lineHeight: 1, padding: 0 }}>✕</button>
                        )}
                        <span style={{ fontWeight: 600, color: '#3b82f6', minWidth: 40, textAlign: 'right' }}>{sum > 0 ? fmt(sum) : '—'}</span>
                        <input type="text" inputMode="decimal" aria-label="ДРР% кампании"
                            value={effPct > 0 ? String(effPct) : ''} placeholder={String(targetDrr || '')}
                            onClick={e => e.stopPropagation()} onMouseDown={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()}
                            onChange={e => setCampDrrPct(c.campaign_id, e.target.value)}
                            style={{
                                width: 34, padding: '1px 3px', fontSize: 12, textAlign: 'right', borderRadius: 4,
                                fontWeight: isOv ? 700 : 500, color: isOv ? '#7c3aed' : '#6b7280',
                                border: `1px solid ${isOv ? '#c4b5fd' : 'transparent'}`, background: isOv ? '#f5f3ff' : 'transparent',
                            }} />
                        <span style={{ fontSize: 11, color: '#9ca3af' }}>%</span>
                    </span>
                );
            }
            case 'nm_count': return c.nm_count;
            case 'autopay': {
                const ap = autopay[String(c.campaign_id)];
                // Зелёный — включено; красный — настроено, но выключено; серый — ни разу не настраивалось
                const color = ap ? (ap.enabled ? '#10b981' : '#ef4444') : '#9ca3af';
                const title = ap ? (ap.enabled ? 'Автопополнение включено' : 'Автопополнение настроено, но выключено') : 'Автопополнение ни разу не настраивалось';
                return (
                    <span style={{ whiteSpace: 'nowrap' }}>
                        <Tooltip text={title}>
                            <button onClick={e => { e.stopPropagation(); setAutopayModal(c); }} aria-label={title}
                                style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, color, fontWeight: ap?.enabled ? 600 : 400, whiteSpace: 'nowrap', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                                {ap?.enabled
                                    ? <><IcClock size={13} />{autopayLabel(ap)}</>
                                    : <><IcGear size={13} />{ap ? 'выключено' : 'настроить'}</>}
                            </button>
                        </Tooltip>
                        <Tooltip text="История пополнений">
                            <button onClick={e => { e.stopPropagation(); setAutopayLogModal(c); }} aria-label="История пополнений"
                                style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#6b7280', padding: '0 4px', display: 'inline-flex', alignItems: 'center' }}><IcHistory size={14} /></button>
                        </Tooltip>
                    </span>
                );
            }
            case 'wb': return (
                <Tooltip text="Открыть кампанию в кабинете WB">
                    <a href={wbCampaignUrl(c, { from: dateFrom, to: dateTo })} target="_blank" rel="noreferrer" aria-label="Открыть кампанию в кабинете WB" onClick={e => e.stopPropagation()}
                        style={{ color: 'var(--color-accent)', textDecoration: 'none', display: 'inline-flex', alignItems: 'center' }}><IcExternal size={15} /></a>
                </Tooltip>
            );
            default: return null;
        }
    };

    return (
        <PageGuard page="ads-manager">
            {/* Фикс-высота + flex-колонка: заголовок/фильтры/тулбар/шапка таблицы закреплены,
                скроллятся ТОЛЬКО строки таблицы. Устойчиво к масштабу (без JS-замера окна). */}
            <div className="animate-in" style={{ height: 'calc(100vh - 80px)', display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
                <div style={{ marginBottom: 16, flexShrink: 0 }}>
                    <h1 style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 28, fontWeight: 700, margin: 0 }}><IcMegaphone size={26} />Управление рекламой</h1>
                </div>

                {/* Каскад: предмет ↔ бренд ↔ артикул (взаимно сужаются) — с поиском */}
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12, flexShrink: 0 }}>
                    <SearchSelect value={subject} onChange={onSubject} placeholder="Предмет: все" maxWidth={260}
                        options={subjectOptions.map(s => ({ value: s, label: s }))} />
                    <SearchSelect value={brand} onChange={onBrand} placeholder="Бренд: все" maxWidth={220}
                        options={brandOptions.map(b => ({ value: b, label: b }))} />
                    <SearchSelect value={article} onChange={onArticle} placeholder="Артикул: все" maxWidth={280}
                        options={articleOptions.map(t => ({ value: String(t.nm_id), label: t.vendor_code }))} />
                    {/* Календарь = период метрик по всем кампаниям (день/диапазон). Пусто → последние 30 дней */}
                    <AdsPeriodPicker from={periodFrom} to={periodTo} placeholder="календарь" minWidth={230}
                        onApply={(f, t) => { setPeriodFrom(f); setPeriodTo(t); }} />
                    {(brand || subject || article || search || campaignType || bidMode) && (
                        <button className="btn btn-secondary btn-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12 }} onClick={resetFilters}><IcX size={13} />Сбросить</button>
                    )}
                </div>

                {error && (
                    <div className="glass-card" style={{ padding: '12px 20px', marginBottom: 12, border: '1px solid var(--color-danger)' }}>
                        <span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {error}</span>
                    </div>
                )}

                {/* ─── Список кампаний ─── */}
                <div className="glass-card static" style={{ padding: 0, overflow: 'hidden', flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
                    <div style={{ padding: '10px 16px', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', borderBottom: '1px solid #e5e7eb', background: '#f9fafb', flexShrink: 0 }}>
                        <div style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
                            <span style={{ position: 'absolute', left: 9, color: '#9ca3af', display: 'inline-flex' }}><IcSearch size={15} /></span>
                            <input placeholder="Поиск по ID или названию" value={search} onChange={e => setSearch(e.target.value)}
                                style={{ background: '#fff', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px 6px 30px', color: 'var(--color-text)', fontSize: 13, width: 240 }} />
                        </div>
                        <div style={{ position: 'relative' }}>
                            <button onClick={() => setCreateMenuOpen(o => !o)} className="btn btn-primary btn-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                                + Создать кампанию <span style={{ fontSize: 10 }}>{createMenuOpen ? '▲' : '▼'}</span>
                            </button>
                            {createMenuOpen && (
                                <>
                                    <div style={{ position: 'fixed', inset: 0, zIndex: 40 }} onClick={() => setCreateMenuOpen(false)} />
                                    <div style={{ position: 'absolute', top: '100%', left: 0, marginTop: 4, minWidth: 190, background: '#fff', border: '1px solid var(--color-border)', borderRadius: 10, boxShadow: '0 8px 24px rgba(0,0,0,.12)', zIndex: 41, overflow: 'hidden' }}>
                                        <div onClick={() => { setCreateMenuOpen(false); router.push(`/p/${slug}/ads-manager/create`); }}
                                            style={{ padding: '10px 14px', fontSize: 13, cursor: 'pointer' }} className="menu-row">Одну</div>
                                        <div onClick={() => { setCreateMenuOpen(false); router.push(`/p/${slug}/ads-manager/create?bulk=1`); }}
                                            style={{ padding: '10px 14px', fontSize: 13, cursor: 'pointer', borderTop: '1px solid #f1f2f4' }} className="menu-row">Много кампаний</div>
                                    </div>
                                </>
                            )}
                        </div>
                        <button onClick={handleSync} disabled={syncing} className="btn btn-secondary btn-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                            <IcRefresh />{syncing ? 'Синхронизация…' : 'Синхронизировать'}
                        </button>
                        <Tooltip text="Выгрузить таблицу в Excel (с учётом фильтров)"><button className="btn btn-secondary btn-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }} onClick={exportCampaigns} disabled={visibleCampaigns.length === 0}>
                            <IcDownload />Excel
                        </button></Tooltip>
                        <span style={{ fontSize: 12, color: 'var(--color-text-dim)', marginLeft: 'auto' }}>
                            Показано: {visibleCampaigns.length} из {campaigns.length}
                        </span>
                        {/* Фильтр по статусу */}
                        <div style={{ position: 'relative' }}>
                            <button onClick={() => setOpenMenu(openMenu === 'filter' ? null : 'filter')} className="btn btn-secondary btn-sm"
                                style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: (statusFilter !== 'not_completed' || campaignType || bidMode) ? 700 : 500 }}><IcSliders />Фильтр</button>
                            {openMenu === 'filter' && (<>
                                <div style={{ position: 'fixed', inset: 0, zIndex: 40 }} onClick={() => setOpenMenu(null)} />
                                <div style={{ position: 'absolute', right: 0, top: '100%', marginTop: 6, zIndex: 41, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, boxShadow: '0 8px 24px rgba(0,0,0,0.12)', padding: 8, minWidth: 230 }}>
                                    <div style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', padding: '4px 8px' }}>СТАТУС КАМПАНИЙ</div>
                                    {STATUS_FILTERS.map(sf => (
                                        <div key={sf.key} onClick={() => { setStatusFilter(sf.key); setOpenMenu(null); }}
                                            style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', borderRadius: 8, cursor: 'pointer', fontSize: 13, color: '#111827', background: statusFilter === sf.key ? '#eff6ff' : undefined }}>
                                            <span style={{ width: 15, height: 15, borderRadius: '50%', flexShrink: 0, border: `2px solid ${statusFilter === sf.key ? '#3b82f6' : '#cbd5e1'}`, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
                                                {statusFilter === sf.key && <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#3b82f6' }} />}
                                            </span>
                                            {sf.label}
                                        </div>
                                    ))}
                                    <div style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', padding: '4px 8px', marginTop: 6, borderTop: '1px solid #f1f2f4' }}>ВИД РЕКЛАМЫ</div>
                                    {TYPE_FILTERS.map(tf => {
                                        const active = campaignType === tf.type && bidMode === tf.mode;
                                        return (
                                            <div key={tf.key} onClick={() => { setCampaignType(tf.type); setBidMode(tf.mode); setOpenMenu(null); }}
                                                style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', borderRadius: 8, cursor: 'pointer', fontSize: 13, color: '#111827', background: active ? '#eff6ff' : undefined }}>
                                                <span style={{ width: 15, height: 15, borderRadius: '50%', flexShrink: 0, border: `2px solid ${active ? '#3b82f6' : '#cbd5e1'}`, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
                                                    {active && <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#3b82f6' }} />}
                                                </span>
                                                {tf.label}
                                            </div>
                                        );
                                    })}
                                </div>
                            </>)}
                        </div>
                        {/* Настройки колонок */}
                        <div style={{ position: 'relative' }}>
                            <Tooltip text="Настройка колонок"><button onClick={() => setOpenMenu(openMenu === 'cols' ? null : 'cols')} className="btn btn-secondary btn-sm" style={{ display: 'inline-flex', alignItems: 'center', fontSize: 14 }} aria-label="Настройка колонок"><IcColumns /></button></Tooltip>
                            {openMenu === 'cols' && (<>
                                <div style={{ position: 'fixed', inset: 0, zIndex: 40 }} onClick={() => setOpenMenu(null)} />
                                <div style={{ position: 'absolute', right: 0, top: '100%', marginTop: 6, zIndex: 41, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, boxShadow: '0 8px 24px rgba(0,0,0,0.12)', padding: 8, minWidth: 220, maxHeight: 380, overflowY: 'auto' }}>
                                    <div style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', padding: '4px 8px' }}>КОЛОНКИ</div>
                                    {CAMP_COLS.filter(col => !col.fixed).map(col => (
                                        <label key={col.key} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 8px', borderRadius: 8, cursor: 'pointer', fontSize: 13, color: '#111827' }}>
                                            <input type="checkbox" checked={visibleCols.has(col.key)} onChange={() => toggleCol(col.key)} />
                                            {col.label}
                                        </label>
                                    ))}
                                </div>
                            </>)}
                        </div>
                    </div>
                    <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden' }}>
                        {loading ? <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div> :
                            visibleCampaigns.length === 0 ? <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>{campFiltered ? 'Ничего не найдено по заданным фильтрам' : 'Кампаний нет — нажмите «Синхронизировать»'}</div> : (
                                <table className="data-table" style={{ width: '100%', tableLayout: 'fixed', borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                                    <thead><tr>
                                        {visibleCampColumns.map(col => {
                                            const b = col.align === 'left' ? cThLeft : col.align === 'center' ? { ...cThStyle, textAlign: 'center' as const } : cThStyle;
                                            const base = { ...(col.blockStart ? { ...b, borderLeft: BLOCK_DIVIDER } : b), width: col.w, padding: '5px 5px', whiteSpace: 'normal' as const, lineHeight: 1.15 };
                                            return (
                                                <th key={col.key} style={col.sort ? { ...base, cursor: 'pointer' } : base}
                                                    onClick={col.sort ? () => toggleCampSort(col.sort!) : undefined}>
                                                    {col.key === 'drr_plan' ? (
                                                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, justifyContent: base.textAlign === 'left' ? 'flex-start' : 'flex-end' }}>
                                                            <InfoTip text={col.title!}>ДРР план</InfoTip>
                                                            <input type="text" inputMode="decimal" value={targetDrr || ''}
                                                                onClick={e => e.stopPropagation()} onMouseDown={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()}
                                                                onChange={e => onTargetDrrChange(e.target.value)} aria-label="Целевой ДРР, %"
                                                                style={{ width: 32, padding: '1px 3px', fontSize: 11, fontWeight: 600, border: '1px solid #9ca3af', borderRadius: 4, textAlign: 'center', background: '#fff', color: '#111827' }} />
                                                            <span>%</span>
                                                        </span>
                                                    ) : col.title ? <InfoTip text={col.title}>{col.label}</InfoTip> : col.label}{col.sort ? campArrow(col.sort) : ''}
                                                </th>
                                            );
                                        })}
                                    </tr></thead>
                                    <tbody>
                                        {pageCampaigns.map(c => (
                                            <tr key={c.campaign_id} style={{ color: '#111827', cursor: 'pointer' }}
                                                onClick={e => onRowClick(e, c)}
                                                onAuxClick={e => onRowAux(e, c)}
                                                title="Открыть кампанию: метрики по дням и кластеризатор">
                                                {visibleCampColumns.map(col => {
                                                    const b = col.align === 'left' ? tdLeft : col.align === 'center' ? { ...tdStyle, textAlign: 'center' as const } : tdStyle;
                                                    const base = { ...(col.blockStart ? { ...b, borderLeft: BLOCK_DIVIDER } : b), width: col.w, padding: '3px 5px', overflow: 'hidden' as const, textOverflow: 'ellipsis' as const };
                                                    return <td key={col.key} style={base}>{renderCampCell(col.key, c)}</td>;
                                                })}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            )}
                    </div>
                    {/* Пагинация: 50 на страницу, номера + переход по номеру */}
                    {!loading && visibleCampaigns.length > PER_PAGE && (
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, flexWrap: 'wrap', padding: '10px 16px', borderTop: '1px solid #e5e7eb', background: '#f9fafb', flexShrink: 0 }}>
                            <span style={{ fontSize: 12, color: 'var(--color-text-dim)', marginRight: 'auto' }}>
                                {(safePage - 1) * PER_PAGE + 1}–{Math.min(safePage * PER_PAGE, visibleCampaigns.length)} из {visibleCampaigns.length}
                            </span>
                            <button className="btn btn-secondary btn-sm" style={{ fontSize: 13 }} disabled={safePage <= 1} onClick={() => setPage(safePage - 1)}>←</button>
                            {buildPageList(safePage, pageCount).map((it, i) => it === '…'
                                ? <span key={`e${i}`} style={{ padding: '0 4px', color: '#9ca3af', fontSize: 13 }}>…</span>
                                : <button key={it} onClick={() => setPage(it)}
                                    className={`btn btn-sm ${it === safePage ? 'btn-primary' : 'btn-secondary'}`}
                                    style={{ fontSize: 13, minWidth: 34, fontWeight: it === safePage ? 700 : 500 }}>{it}</button>
                            )}
                            <button className="btn btn-secondary btn-sm" style={{ fontSize: 13 }} disabled={safePage >= pageCount} onClick={() => setPage(safePage + 1)}>→</button>
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--color-text-dim)', marginLeft: 8 }}>
                                Стр.
                                <input type="number" min={1} max={pageCount} value={pageInput} placeholder={String(safePage)}
                                    onChange={e => setPageInput(e.target.value)}
                                    onKeyDown={e => { if (e.key === 'Enter') { const n = Number(pageInput); if (n >= 1 && n <= pageCount) setPage(n); setPageInput(''); (e.target as HTMLInputElement).blur(); } }}
                                    onBlur={() => { const n = Number(pageInput); if (pageInput && n >= 1 && n <= pageCount) setPage(n); setPageInput(''); }}
                                    style={{ width: 52, textAlign: 'center', border: '1px solid var(--color-border)', borderRadius: 8, padding: '4px 6px', fontSize: 13, background: '#fff', color: 'var(--color-text)' }} />
                                из {pageCount}
                            </span>
                        </div>
                    )}
                </div>

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
            </div>
        </PageGuard>
    );
}
