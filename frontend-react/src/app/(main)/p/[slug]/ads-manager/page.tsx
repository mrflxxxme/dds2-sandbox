'use client';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { exportToExcel } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { IcMegaphone, IcRefresh, IcDownload, IcSliders, IcColumns, IcPause, IcPlay, IcClock, IcGear, IcHistory, IcExternal, IcSearch, IcX, IcCopy } from './components/icons';
import SearchSelect from './components/SearchSelect';
import AdSections from './components/AdSections';
import GlueTable from './components/GlueTable';
import ScheduleModal from './components/ScheduleModal';
import BudgetLedgerModal from './components/BudgetLedgerModal';
import WbThumb from './components/WbThumb';
import AdsPeriodPicker from './components/AdsPeriodPicker';
import InfoTip from './components/InfoTip';
import Tooltip from './components/Tooltip';
import { useToast } from './components/Toasts';
import { fmt, num, fmtPct, iso, STATUS_BADGE, thStyle, thLeft, tdStyle, tdLeft, wbCampaignUrl, campaignTypeBadge, scheduleLabel, humanizeAdsError } from './components/adsShared';
import type { AdsManagerCampaign, AdsScheduleSetting } from '@/types/api';

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
    { key: 'bid', label: 'Ставка ₽', sort: 'default_bid', title: 'Ставка кампании по активной зоне (Поиск). Клик — редактировать, запись сразу в WB. Реальные деньги. Только для готовых/активных/приостановленных.', w: 72 },
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
    { key: 'schedule', label: 'Расписание', title: 'Пауза по расписанию: ДДС глушит кампанию в окне «плохих» часов (после долива ВБ в 00:00 МСК) и запускает обратно', align: 'center', w: 100 },
    { key: 'wb', label: 'WB', align: 'center', w: 44 },
];
const CAMP_TOGGLE_KEYS = CAMP_COLS.filter(c => !c.fixed).map(c => c.key);
// Разделы экрана: список кампаний (по умолчанию) + вернувшиеся аналитические разделы.
// Кнопка-дропдаун после «Артикул» переключает основную область между ними.
const AD_VIEWS = [
    { key: 'campaigns', label: 'Кампании' },
    { key: 'high-drr', label: 'Высокий ДРР' },
    { key: 'no-ads', label: 'Не работает реклама' },
    { key: 'no-organic', label: 'Нет органики' },
    { key: 'budget-gap', label: 'Нехватка бюджета' },
] as const;
// 'glue' — вкладка «Склейки» (карточки WB), живёт не в дропдауне «Пресеты», а в
// переключателе рядом с фильтрами: это второй основной режим экрана, наравне с кампаниями
type AdView = typeof AD_VIEWS[number]['key'] | 'glue';
const MAIN_TABS: { key: AdView; label: string }[] = [
    { key: 'campaigns', label: 'Кампании' },
    { key: 'glue', label: 'Склейки' },
];
// Типы кампаний для создания по выбранным товарам без рекламы (селектор в полосе выбора).
// name — как называется кампания при создании (по канону: CPM единая=Авто, CPM ручная=Поиск+рек, CPC=Поиск).
const CREATABLE = [
    { key: 'cpm-auto', label: 'CPM единая · Авто', payment: 'cpm', bid: 'unified', zones: 'search,recommendations' },
    { key: 'cpm-manual', label: 'CPM ручная · Поиск+рек', payment: 'cpm', bid: 'manual', zones: 'search,recommendations' },
    { key: 'cpc', label: 'CPC · Поиск', payment: 'cpc', bid: 'manual', zones: 'search' },
] as const;
type CreateType = typeof CREATABLE[number]['key'];
// Фильтры списка переживают уход в кампанию и возврат. sessionStorage: живёт в рамках
// вкладки (переход/назад/refresh сохраняют), не липнет между сессиями и вкладками.
const ADS_FILTERS_SS_KEY = 'ads_manager_filters';
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
    // Активный раздел экрана (кнопка-дропдаун после «Артикул»)
    const [view, setView] = useState<AdView>('campaigns');
    const [sectionsMenuOpen, setSectionsMenuOpen] = useState(false);
    // Выбор товаров в разделе (nm → есть ли у товара кампания). «Подтвердить»: с кампаниями →
    // отфильтрованный список кампаний; без кампаний → создание пачкой.
    const [selectedNms, setSelectedNms] = useState<Map<number, boolean>>(() => new Map());
    // Подтверждённый фильтр списка кампаний по nm_id выбранных товаров ([] = не активен)
    const [campNmFilter, setCampNmFilter] = useState<number[]>([]);
    // Выбранный тип кампании для создания по товарам без рекламы
    const [createType, setCreateType] = useState<CreateType>('cpm-auto');

    // Кампании
    const [campaigns, setCampaigns] = useState<AdsManagerCampaign[]>([]);
    const [campSort, setCampSort] = useState<{ field: keyof AdsManagerCampaign; dir: SortDir } | null>(null);
    const [syncing, setSyncing] = useState(false);
    const [syncProgress, setSyncProgress] = useState('');  // «N/M» бюджетов во время синка
    const [lastSyncAt, setLastSyncAt] = useState<string | null>(null);  // ISO UTC последнего успешного синка
    const [nowTs, setNowTs] = useState(() => Date.now());  // тикает раз в минуту для «N мин назад»
    const [schedule, setSchedule] = useState<Record<string, AdsScheduleSetting>>({});
    const [scheduleModal, setScheduleModal] = useState<AdsManagerCampaign | null>(null);
    const [createMenuOpen, setCreateMenuOpen] = useState(false);
    const [budgetLogModal, setBudgetLogModal] = useState<AdsManagerCampaign | null>(null);
    const [stateBusy, setStateBusy] = useState<number | null>(null);  // кампания в процессе смены статуса
    const [bidBusy, setBidBusy] = useState<number | null>(null);  // кампания в процессе записи ставки в WB
    const [bidDraft, setBidDraft] = useState<{ key: string; text: string } | null>(null);  // текст редактируемой ячейки ставки
    const [bidPending, setBidPending] = useState<{ cid: number; value: number } | null>(null);  // ставка ждёт встроенного подтверждения
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
    // Черновик набора в полях ДРР-плана: пока поле редактируется, показываем сырую строку —
    // иначе контролируемый input, чей value = String(number), съедал точку («8.» → «8»,
    // и набрать 8.5 с клавиатуры было невозможно). Ключ: 'header' либо String(campaign_id).
    const [drrDraft, setDrrDraft] = useState<{ key: string; text: string } | null>(null);
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
            const [list, sched] = await Promise.all([api.getAdCampaignsList(df, dt), api.getCampaignsSchedule().catch(() => ({}))]);
            setCampaigns(list);
            setSchedule(sched);
        }
        catch (e) { setError(e instanceof Error ? e.message : 'Ошибка загрузки'); }
        finally { setLoading(false); }
    }, []);

    const saveSchedule = useCallback(async (campaignId: number, s: AdsScheduleSetting) => {
        // Сейв только пишет настройку: паузу/запуск делает scheduler-тик (раз в 15 минут)
        const res = await api.setCampaignSchedule(campaignId, s);
        setSchedule(res.settings);
    }, []);

    const toggleCampaignState = useCallback(async (c: AdsManagerCampaign) => {
        const active = c.status !== 9;  // не активна → запускаем, активна → пауза
        setStateBusy(c.campaign_id);
        try {
            await api.setCampaignState(c.campaign_id, active);
            toast.success(active ? `Кампания «${c.name || c.campaign_id}» запущена` : `Кампания «${c.name || c.campaign_id}» приостановлена`);
            await loadCampaigns(dateFrom, dateTo);
        } catch (e) {
            toast.error(humanizeAdsError(e, 'Не удалось изменить статус кампании'));
        } finally { setStateBusy(null); }
    }, [loadCampaigns, dateFrom, dateTo, toast]);

    // Оптимистично проставить ставку кампании в таблице (без ре-синка).
    const patchBid = useCallback((cid: number, bid: number) => {
        setCampaigns(prev => prev.map(x => x.campaign_id === cid ? { ...x, default_bid: bid } : x));
    }, []);

    // Шаг 1: «взвести» изменённую ставку — показать встроенное подтверждение (не пишем сразу).
    const armBid = useCallback((c: AdsManagerCampaign, text: string) => {
        setBidDraft(null);
        const v = Math.round(parseFloat(text.replace(',', '.')));
        const cur = c.default_bid ?? null;
        if (!isFinite(v) || v <= 0) { setBidPending(null); return; }        // пусто/мусор — отмена
        if (cur != null && v === Math.round(cur)) { setBidPending(null); return; }  // без изменений
        setBidPending({ cid: c.campaign_id, value: v });
    }, []);

    // Шаг 2: запись в WB после клика ✓. Можно ввести любую ставку — если она ниже
    // аукционного минимума WB, бэкенд сам поднимает до минимума (res.adjusted).
    const applyBid = useCallback(async (c: AdsManagerCampaign, value: number) => {
        setBidPending(null);
        setBidBusy(c.campaign_id);
        try {
            const res = await api.setCampaignBid(c.campaign_id, value);
            if (res.ok && res.bid != null) {
                patchBid(c.campaign_id, res.bid);
                if (res.adjusted) toast.warning(`Ниже минимума WB — поставлен минимум ${res.bid} ₽`);
                else toast.success(`Ставка «${c.name || c.campaign_id}» → ${res.bid} ₽`);
            } else {
                toast.error(humanizeAdsError(res.error, 'Не удалось изменить ставку'));
            }
        } catch (e) {
            toast.error(humanizeAdsError(e, 'Не удалось изменить ставку'));
        } finally { setBidBusy(null); }
    }, [patchBid, toast]);

    // Каталог артикулов для каскадных фильтров (nm_id → предмет/бренд/название).
    const loadCatalog = useCallback(async () => {
        try {
            // Полный каталог артикулов (без топ-лимита/фильтра активности) — иначе часть артикулов выпадает из фильтра
            const rows = await api.getAdArticleCatalog();
            setCatalog(rows.map(r => ({ nm_id: r.nm_id, vendor_code: r.vendor_code || String(r.nm_id), subject: r.subject || '', brand: r.brand || '' })));
        } catch { /* каталог не критичен — фильтры просто будут пустыми */ }
    }, []);

    useEffect(() => { loadCatalog(); }, [loadCatalog]);  // каталог — один раз

    // Время последнего синка: тот же endpoint прогресса, но при обычной загрузке страницы
    // (бэк отдаёт last_sync_at и при status=idle).
    const refreshLastSync = useCallback(async () => {
        try {
            const p = await api.getSyncCampaignsProgress();
            setLastSyncAt(p.last_sync_at ?? null);
        } catch { /* отметка не критична — просто не покажем «обновлено» */ }
    }, []);
    useEffect(() => { refreshLastSync(); }, [refreshLastSync]);
    // Тик раз в минуту, чтобы «5 мин назад» не застывало на открытой вкладке
    useEffect(() => { const t = setInterval(() => setNowTs(Date.now()), 60_000); return () => clearInterval(t); }, []);
    // Список кампаний — при входе и при смене периода календаря (метрики за выбранный день/диапазон)
    useEffect(() => { loadCampaigns(dateFrom, dateTo); }, [periodFrom, periodTo]);  // eslint-disable-line react-hooks/exhaustive-deps

    // Восстановление фильтров списка после возврата из кампании (sessionStorage).
    // Гидрируем в эффекте (не ломаем SSR-разметку); клиентские фильтры применяются к уже
    // загруженному списку, восстановленный период сам триггерит перезагрузку выше.
    useEffect(() => {
        try {
            const raw = sessionStorage.getItem(ADS_FILTERS_SS_KEY);
            if (!raw) return;
            const f = JSON.parse(raw);
            if (typeof f.brand === 'string') setBrand(f.brand);
            if (typeof f.subject === 'string') setSubject(f.subject);
            if (typeof f.article === 'string') setArticle(f.article);
            if (typeof f.search === 'string') setSearch(f.search);
            if (f.campaignType === '' || f.campaignType === 'cpm' || f.campaignType === 'cpc') setCampaignType(f.campaignType);
            if (f.bidMode === '' || f.bidMode === 'unified' || f.bidMode === 'manual') setBidMode(f.bidMode);
            if (typeof f.statusFilter === 'string') setStatusFilter(f.statusFilter);
            if (typeof f.page === 'number' && f.page >= 1) setPage(f.page);
            if (typeof f.periodFrom === 'string') setPeriodFrom(f.periodFrom);
            if (typeof f.periodTo === 'string') setPeriodTo(f.periodTo);
            if (f.campSort === null || (f.campSort && typeof f.campSort === 'object')) setCampSort(f.campSort);
            if (f.view === 'glue' || AD_VIEWS.some(v => v.key === f.view)) setView(f.view);
            // Фильтр «по выбранным товарам» (приходит со вкладки «Склейки») — тоже часть
            // состояния списка: без него возврат из кампании ронял отбор по склейке,
            // оставляя только предмет/бренд
            if (Array.isArray(f.campNmFilter)) setCampNmFilter(f.campNmFilter.filter((n: unknown) => typeof n === 'number'));
        } catch { /* битый JSON / SSR — игнор */ }
    }, []);

    // Сохранение фильтров при каждом изменении. skip-first: пропускаем маунт-прогон,
    // иначе дефолты затёрли бы сохранённое ДО гидрации (эффект выше).
    const filtersFirstRun = useRef(true);
    useEffect(() => {
        if (filtersFirstRun.current) { filtersFirstRun.current = false; return; }
        try {
            sessionStorage.setItem(ADS_FILTERS_SS_KEY, JSON.stringify(
                { brand, subject, article, search, campaignType, bidMode, statusFilter, page, periodFrom, periodTo, campSort, view, campNmFilter },
            ));
        } catch { /* SSR / quota — игнор */ }
    }, [brand, subject, article, search, campaignType, bidMode, statusFilter, page, periodFrom, periodTo, campSort, view, campNmFilter]);

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
                // «Ставка» (инлайн-правка) — показать один раз
                if (!localStorage.getItem('ads_camp_cols_bid')) {
                    set.add('bid');
                    localStorage.setItem('ads_camp_cols_bid', '1');
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
        setSyncing(true); setSyncProgress('');
        try {
            // Приоритет синка — кампании под активным фильтром страницы (их бюджеты бэк
            // тянет первыми). Гейтим на «срез уже полного списка» (любой фильтр: поиск,
            // бренд/предмет/артикул, пресеты, статус) — без сужения шлём пустой список,
            // и бэк держит прежний порядок по расходу.
            const priorityIds = visibleCampaigns.length < campaigns.length
                ? visibleCampaigns.map(c => c.campaign_id) : [];
            await api.syncAdCampaigns(priorityIds);
            // Наблюдаем прогресс до ФАКТИЧЕСКОГО завершения (бэк тянет бюджеты по 0.5с/кампанию,
            // TIME_BUDGET ~600с). Раньше цикл обрывался на 120с → loadCampaigns садился на
            // частично записанное зеркало, часть бюджетов «догоняла» лишь на следующем синке.
            for (let i = 0; i < 300; i++) {
                await new Promise(r => setTimeout(r, 2000));
                const p = await api.getSyncCampaignsProgress();
                if (p.budgets_total) setSyncProgress(`${p.budgets_done ?? 0}/${p.budgets_total}`);
                if (p.status === 'done' || p.status === 'error' || p.status === 'idle') break;
            }
            // Перечитываем С ТЕКУЩИМ периодом календаря: вызов без дат садил список
            // на бэкенд-дефолт (7 дней), а календарь продолжал показывать выбранный диапазон
            await loadCampaigns(dateFrom, dateTo);
        } catch (e) { setError(e instanceof Error ? e.message : 'Ошибка синхронизации'); }
        finally { setSyncing(false); setSyncProgress(''); setNowTs(Date.now()); refreshLastSync(); }
    };

    // «Обновлено N мин назад» рядом с кнопкой синка — чтобы понимать актуальность цифр.
    // Свежесть считаем от nowTs (тикает раз в минуту), точное время — в подсказке, МСК.
    const lastSyncLabel = (() => {
        if (!lastSyncAt) return null;
        const ts = new Date(lastSyncAt).getTime();
        if (Number.isNaN(ts)) return null;
        const mins = Math.max(0, Math.floor((nowTs - ts) / 60000));
        const rel = mins < 1 ? 'только что'
            : mins < 60 ? `${mins} мин назад`
            : mins < 60 * 24 ? `${Math.floor(mins / 60)} ч назад`
            : `${Math.floor(mins / 1440)} дн назад`;
        const exact = new Date(ts).toLocaleString('ru-RU', {
            day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow',
        });
        return { rel, exact, stale: mins >= 60 * 3 };
    })();

    // Поиск по ID / названию кампании / nm_id её товаров
    const q = search.trim().toLowerCase();
    const matchCampaign = (name: string | null, campaignId: number, nmIds: number[]) =>
        !q || (name || '').toLowerCase().includes(q) || String(campaignId).includes(q) || nmIds.some(id => String(id).includes(q));

    // ─── Каскадные фильтры (предмет ↔ бренд ↔ артикул) по рекламируемым товарам ───
    const advNmSet = useMemo(() => new Set(campaigns.flatMap(c => c.nm_ids)), [campaigns]);
    // Карта кампания → её первый nm_id (для миниатюр товара в разделе «Нехватка бюджета»,
    // где у строки-кампании своего nm_id нет — берём из загруженного списка кампаний).
    const campNm = useMemo(() => {
        const m: Record<number, number> = {};
        for (const c of campaigns) if (c.nm_ids[0] != null) m[c.campaign_id] = c.nm_ids[0];
        return m;
    }, [campaigns]);
    // Предметы/бренды товаров кампании — для фильтра «Нехватки бюджета» по предмету/бренду
    // (её эндпоинт не принимает эти фильтры, поэтому фильтруем на клиенте по данным кампаний).
    const campMeta = useMemo(() => {
        const m: Record<number, { subjects: string[]; brands: string[] }> = {};
        for (const c of campaigns) m[c.campaign_id] = { subjects: c.subjects || [], brands: c.brands || [] };
        return m;
    }, [campaigns]);

    // Все nm с хоть одной НЕзавершённой кампанией — честный hasCampaign для кликов в разделах
    // (раньше «Не работает реклама» всегда слала false → «Создать» плодил дубли кампаний,
    // а «Нет органики» всегда true → «К кампаниям» мог открыть пустой список).
    const nmsWithCampaigns = useMemo(() => {
        const s = new Set<number>();
        for (const c of campaigns) if (c.status !== 7) for (const nm of c.nm_ids) s.add(nm);
        return s;
    }, [campaigns]);

    // Клик по товару в разделе: всегда копим в выбор (запоминаем, есть ли у товара кампания).
    const onProductClick = (nmId: number, hasCampaign: boolean) => {
        setSelectedNms(prev => {
            const n = new Map(prev);
            if (n.has(nmId)) n.delete(nmId); else n.set(nmId, hasCampaign);
            return n;
        });
    };
    // Выбор/снятие всей склейки разом — одним setState, а не N вызовами onProductClick:
    // иначе каждый товар порождал бы свой рендер, а на склейке их бывает под три десятка.
    const onToggleGlue = (nmIds: number[], select: boolean) => {
        setSelectedNms(prev => {
            const n = new Map(prev);
            for (const nm of nmIds) {
                if (select) n.set(nm, nmsWithCampaigns.has(nm)); else n.delete(nm);
            }
            return n;
        });
    };
    // Разбивка выбора: товары с кампаниями (в фильтр кампаний) и без (в создание).
    const selWithCamp = [...selectedNms].filter(([, has]) => has).map(([nm]) => nm);
    const selNoCamp = [...selectedNms].filter(([, has]) => !has).map(([nm]) => nm);
    // «К кампаниям» — фильтруем список кампаний по выбранным товарам с кампаниями.
    const goToCampaigns = () => {
        if (selWithCamp.length === 0) return;
        setCampNmFilter(selWithCamp);
        setSelectedNms(new Map());
        setView('campaigns');
    };
    // «Создать» — страница создания, предзаполненная выбранными товарами без кампаний + выбранным типом.
    const goToCreate = () => {
        if (selNoCamp.length === 0) return;
        const t = CREATABLE.find(c => c.key === createType) || CREATABLE[0];
        router.push(`/p/${slug}/ads-manager/create?nm=${selNoCamp.join(',')}&payment=${t.payment}&bid=${t.bid}&zones=${t.zones}`);
    };
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
    // campNmFilter тоже снимаем: он переживает уход в кампанию, и без этого «Сбросить»
    // оставлял бы список молча отфильтрованным по товарам склейки
    const resetFilters = () => { setBrand(''); setSubject(''); setArticle(''); setSearch(''); setCampaignType(''); setBidMode(''); setCampNmFilter([]); };

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
        .filter(c => campNmFilter.length === 0 || c.nm_ids.some(n => campNmFilter.includes(n)))
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

    // Копирование артикула (nm_id) из ячейки кампании
    const copyNm = (nmId: number) => {
        navigator.clipboard?.writeText(String(nmId))
            .then(() => toast.success(`Артикул ${nmId} скопирован`))
            .catch(() => toast.error('Не удалось скопировать — буфер обмена недоступен'));
    };

    const campaignHref = (c: AdsManagerCampaign) => `/p/${slug}/ads-manager/campaign/${c.campaign_id}`;
    const openCampaign = (c: AdsManagerCampaign) => router.push(campaignHref(c));

    // Клик по строке ведёт в кампанию, но модификаторы отдаём браузеру (Cmd/Ctrl — новая вкладка),
    // а вложенные кнопки/ссылки гасят всплытие сами.
    const onRowClick = (e: React.MouseEvent, c: AdsManagerCampaign) => {
        if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        // Клик по интерактивному контролу ИЛИ по его ячейке (правка ставки/ДРР%, пуск-пауза,
        // расписание, ссылка WB) не должен проваливаться в карточку. Проверяем реальную
        // цель клика — не полагаемся только на stopPropagation дочерних контролов (мимо мелкого
        // инпута легко попасть в пустое поле ячейки, где всплытие уже не погашено).
        if ((e.target as HTMLElement).closest?.('button, a, input, select, textarea, [role="button"], [data-nonav]')) return;
        openCampaign(c);
    };
    // Средняя кнопка мыши по строке — новая вкладка (нативно так работают только ссылки).
    const onRowAux = (e: React.MouseEvent, c: AdsManagerCampaign) => {
        if (e.button !== 1) return;
        e.preventDefault();
        window.open(campaignHref(c), '_blank', 'noopener');
    };

    const visibleCampColumns = CAMP_COLS.filter(col => col.fixed || visibleCols.has(col.key));
    // Колонки с интерактивными контролами: клик в любом месте такой ячейки остаётся в ней,
    // а не открывает кампанию (см. onRowClick — проверка [data-nonav]).
    const NONAV_COLS = new Set(['status', 'bid', 'drr_plan', 'schedule', 'wb']);
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
                        {c.nm_ids[0] != null && (
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }} title="Артикул товара">
                                <span style={{ fontWeight: 700, color: '#374151' }}>{c.nm_ids[0]}</span>
                                <button onClick={e => { e.preventDefault(); e.stopPropagation(); copyNm(c.nm_ids[0]); }} title="Скопировать артикул" aria-label="Скопировать артикул"
                                    style={{ border: 'none', background: 'none', cursor: 'pointer', padding: 0, display: 'inline-flex', lineHeight: 1, color: '#9ca3af' }}>
                                    <IcCopy size={11} />
                                </button>
                            </span>
                        )}
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
            case 'bid': {
                // WB принимает ставку только у статусов 4/9/11 — иначе показываем значение без правки.
                const editable = c.status === 4 || c.status === 9 || c.status === 11;
                const cur = c.default_bid;
                if (!editable) return <span style={{ color: '#9ca3af' }}>{cur != null ? fmt(cur) : '—'}</span>;
                const busy = bidBusy === c.campaign_id;
                const pending = bidPending?.cid === c.campaign_id ? bidPending : null;
                // Шаг 2: встроенное подтверждение — реальные деньги пишутся только по клику ✓
                if (pending) {
                    return (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end' }}
                            onClick={e => e.stopPropagation()} onMouseDown={e => e.stopPropagation()}>
                            <span style={{ fontWeight: 700, color: '#7c3aed', whiteSpace: 'nowrap' }}>{pending.value} ₽?</span>
                            <button aria-label="Применить ставку" title={`Применить ${pending.value} ₽`} onClick={e => { e.stopPropagation(); applyBid(c, pending.value); }}
                                style={{ border: '1px solid #10b981', background: '#ecfdf5', color: '#059669', borderRadius: 4, cursor: 'pointer', width: 20, height: 20, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: 0, fontSize: 12 }}>✓</button>
                            <button aria-label="Отменить" title="Отменить" onClick={e => { e.stopPropagation(); setBidPending(null); }}
                                style={{ border: '1px solid #e5e7eb', background: '#fff', color: '#6b7280', borderRadius: 4, cursor: 'pointer', width: 20, height: 20, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', padding: 0, fontSize: 12 }}>✕</button>
                        </span>
                    );
                }
                const isDraft = bidDraft?.key === String(c.campaign_id);
                const curText = cur != null ? String(Math.round(cur)) : '';
                // Шаг 1: правка значения; Enter/уход с поля — «взводит» подтверждение (armBid)
                return (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, justifyContent: 'flex-end' }}>
                        <input type="text" inputMode="decimal" aria-label="Ставка кампании ₽" disabled={busy}
                            value={isDraft ? bidDraft!.text : curText} placeholder="—"
                            onClick={e => e.stopPropagation()} onMouseDown={e => e.stopPropagation()}
                            onKeyDown={e => {
                                e.stopPropagation();
                                if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                                else if (e.key === 'Escape') { setBidDraft({ key: String(c.campaign_id), text: curText }); (e.target as HTMLInputElement).blur(); }
                            }}
                            onFocus={() => setBidDraft({ key: String(c.campaign_id), text: curText })}
                            onChange={e => setBidDraft({ key: String(c.campaign_id), text: e.target.value })}
                            onBlur={e => armBid(c, e.currentTarget.value)}
                            style={{ width: 52, padding: '1px 4px', fontSize: 12, textAlign: 'right', borderRadius: 4, fontWeight: 600, color: busy ? '#9ca3af' : '#111827', border: '1px solid #e5e7eb', background: busy ? '#f9fafb' : '#fff' }} />
                        <span style={{ fontSize: 10.5, color: '#9ca3af', width: 8 }}>{busy ? '…' : '₽'}</span>
                    </span>
                );
            }
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
                            value={drrDraft?.key === String(c.campaign_id) ? drrDraft.text : (effPct > 0 ? String(effPct) : '')}
                            placeholder={String(targetDrr || '')}
                            onClick={e => e.stopPropagation()} onMouseDown={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()}
                            onChange={e => { setDrrDraft({ key: String(c.campaign_id), text: e.target.value }); setCampDrrPct(c.campaign_id, e.target.value); }}
                            onBlur={() => setDrrDraft(null)}
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
            case 'schedule': {
                const sc = schedule[String(c.campaign_id)];
                // Зелёный — включено; серый — не настраивалось (выключенные записи бэк не хранит)
                const title = sc?.enabled ? `Пауза по расписанию: ${scheduleLabel(sc)} МСК` : 'Пауза по расписанию не настроена';
                return (
                    <span style={{ whiteSpace: 'nowrap' }}>
                        <Tooltip text={title}>
                            <button onClick={e => { e.stopPropagation(); setScheduleModal(c); }} aria-label={title}
                                style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, color: sc?.enabled ? '#10b981' : '#9ca3af', fontWeight: sc?.enabled ? 600 : 400, whiteSpace: 'nowrap', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                                {sc?.enabled
                                    ? <><IcPause size={13} />{scheduleLabel(sc)}</>
                                    : <><IcGear size={13} />настроить</>}
                            </button>
                        </Tooltip>
                        <Tooltip text="История бюджета">
                            <button onClick={e => { e.stopPropagation(); setBudgetLogModal(c); }} aria-label="История бюджета"
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
                    {/* Основные режимы экрана: список кампаний ↔ карточки-склейки WB */}
                    <span style={{ display: 'inline-flex', gap: 3, background: '#f3f4f6', borderRadius: 8, padding: 3 }}>
                        {MAIN_TABS.map(t => (
                            <button key={t.key} type="button" onClick={() => setView(t.key)}
                                style={{ fontSize: 13, padding: '5px 14px', borderRadius: 6, border: 'none', cursor: 'pointer', whiteSpace: 'nowrap',
                                    background: view === t.key ? '#fff' : 'transparent',
                                    color: view === t.key ? '#1e3a8a' : '#6b7280',
                                    fontWeight: view === t.key ? 600 : 500,
                                    boxShadow: view === t.key ? '0 1px 2px rgba(0,0,0,.1)' : undefined }}>
                                {t.label}
                            </button>
                        ))}
                    </span>
                    {/* Разделы: кнопка-дропдаун после «Артикул» — переключает основную область */}
                    <div style={{ position: 'relative' }}>
                        <button type="button" onClick={() => setSectionsMenuOpen(o => !o)}
                            style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, minWidth: 160, maxWidth: 240, background: 'var(--color-bg-card)', border: `1px solid ${sectionsMenuOpen ? 'var(--color-accent)' : 'var(--color-border)'}`, borderRadius: 8, padding: '6px 10px', fontSize: 13, color: 'var(--color-text)', cursor: 'pointer' }}>
                            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{AD_VIEWS.find(v => v.key === view && v.key !== 'campaigns') ? `Пресеты: ${AD_VIEWS.find(v => v.key === view)?.label}` : 'Пресеты'}</span>
                            <span style={{ color: 'var(--color-text-dim)', fontSize: 11, flexShrink: 0 }}>⌄</span>
                        </button>
                        {sectionsMenuOpen && (<>
                            <div style={{ position: 'fixed', inset: 0, zIndex: 40 }} onClick={() => setSectionsMenuOpen(false)} />
                            <div style={{ position: 'absolute', left: 0, top: '100%', marginTop: 6, zIndex: 41, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, boxShadow: '0 8px 24px rgba(0,0,0,0.12)', padding: 8, minWidth: 210 }}>
                                {AD_VIEWS.map(v => (
                                    <div key={v.key} onClick={() => { setView(v.key); setSectionsMenuOpen(false); }}
                                        style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 8px', borderRadius: 8, cursor: 'pointer', fontSize: 13, color: '#111827', background: view === v.key ? '#eff6ff' : undefined }}
                                        className="menu-row">{v.label}</div>
                                ))}
                            </div>
                        </>)}
                    </div>
                    {view === 'campaigns' && campNmFilter.length > 0 && (
                        <button onClick={() => setCampNmFilter([])} title="Список отфильтрован по выбранным товарам — нажмите, чтобы сбросить"
                            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, background: '#eff6ff', color: '#1e3a8a', border: '1px solid #bfdbfe', borderRadius: 8, padding: '6px 10px', cursor: 'pointer' }}>
                            По выбранным товарам: {campNmFilter.length} <IcX size={13} />
                        </button>
                    )}
                    {/* Календарь = период метрик по всем кампаниям (день/диапазон). Пусто → последние 30 дней. Сдвинут вправо. */}
                    <span style={{ marginLeft: 'auto', display: 'inline-flex' }}>
                        <AdsPeriodPicker from={periodFrom} to={periodTo} placeholder="календарь" minWidth={230} align="right"
                            onApply={(f, t) => { setPeriodFrom(f); setPeriodTo(t); }} />
                    </span>
                    {(brand || subject || article || search || campaignType || bidMode || campNmFilter.length > 0) && (
                        <button className="btn btn-secondary btn-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12 }} onClick={resetFilters}><IcX size={13} />Сбросить</button>
                    )}
                </div>

                {view !== 'campaigns' && selectedNms.size > 0 && (
                    <div className="glass-card static" style={{ padding: '10px 16px', marginBottom: 12, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', border: '1px solid var(--color-accent)' }}>
                        <span style={{ fontSize: 13, fontWeight: 600, color: '#1e3a8a' }}>Выбрано товаров: {selectedNms.size}</span>
                        <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                            {selWithCamp.length > 0 && `с кампаниями: ${selWithCamp.length}`}{selWithCamp.length > 0 && selNoCamp.length > 0 && ' · '}{selNoCamp.length > 0 && `без кампаний: ${selNoCamp.length}`}
                        </span>
                        <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 8 }}>
                            <button className="btn btn-secondary btn-sm" style={{ fontSize: 13 }} onClick={() => setSelectedNms(new Map())}>Очистить</button>
                            {selWithCamp.length > 0 && (
                                <button className="btn btn-primary btn-sm" style={{ fontSize: 13 }} onClick={goToCampaigns}>К кампаниям ({selWithCamp.length})</button>
                            )}
                            {selNoCamp.length > 0 && (
                                <>
                                    <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>тип:</span>
                                    <span style={{ display: 'inline-flex', gap: 3, background: '#f3f4f6', borderRadius: 8, padding: 3 }}>
                                        {CREATABLE.map(t => (
                                            <button key={t.key} onClick={() => setCreateType(t.key)}
                                                style={{ fontSize: 12, padding: '4px 10px', borderRadius: 6, border: 'none', cursor: 'pointer', whiteSpace: 'nowrap',
                                                    background: createType === t.key ? '#fff' : 'transparent',
                                                    color: createType === t.key ? '#1e3a8a' : '#6b7280',
                                                    fontWeight: createType === t.key ? 600 : 500,
                                                    boxShadow: createType === t.key ? '0 1px 2px rgba(0,0,0,.1)' : undefined }}>
                                                {t.label}
                                            </button>
                                        ))}
                                    </span>
                                    <button className="btn btn-primary btn-sm" style={{ fontSize: 13 }} onClick={goToCreate}>Создать ({selNoCamp.length})</button>
                                </>
                            )}
                        </span>
                    </div>
                )}

                {view === 'campaigns' && error && (
                    <div className="glass-card" style={{ padding: '12px 20px', marginBottom: 12, border: '1px solid var(--color-danger)' }}>
                        <span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {error}</span>
                    </div>
                )}

                {/* ─── Основная область: список кампаний ИЛИ вернувшийся аналитический раздел ─── */}
                {view === 'campaigns' ? (
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
                            <IcRefresh />{syncing ? (syncProgress ? `Синхронизация ${syncProgress}` : 'Синхронизация…') : 'Синхронизировать'}
                        </button>
                        {lastSyncLabel && !syncing && (
                            <Tooltip text={`Кампании и бюджеты обновлялись ${lastSyncLabel.exact} (МСК)`}>
                                <span style={{ fontSize: 12, color: lastSyncLabel.stale ? '#b45309' : 'var(--color-text-dim)', whiteSpace: 'nowrap', cursor: 'default' }}>
                                    Обновлено {lastSyncLabel.rel}
                                </span>
                            </Tooltip>
                        )}
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
                                                            <input type="text" inputMode="decimal"
                                                                value={drrDraft?.key === 'header' ? drrDraft.text : (targetDrr || '')}
                                                                onClick={e => e.stopPropagation()} onMouseDown={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()}
                                                                onChange={e => { setDrrDraft({ key: 'header', text: e.target.value }); onTargetDrrChange(e.target.value); }}
                                                                onBlur={() => setDrrDraft(null)} aria-label="Целевой ДРР, %"
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
                                                    return <td key={col.key} style={base} data-nonav={NONAV_COLS.has(col.key) ? '' : undefined}>{renderCampCell(col.key, c)}</td>;
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
                ) : view === 'glue' ? (
                    <GlueTable slug={slug} dateFrom={dateFrom} dateTo={dateTo} brand={brand} subject={subject} article={article}
                        selectedNms={selectedNms} onProductClick={onProductClick} onToggleGlue={onToggleGlue}
                        nmsWithCampaigns={nmsWithCampaigns} />
                ) : (
                    <div className="glass-card static" style={{ padding: 0, overflow: 'hidden', flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
                        <AdSections view={view} dateFrom={dateFrom} dateTo={dateTo} brand={brand} subject={subject} campNm={campNm}
                            selectedNms={selectedNms} onProductClick={onProductClick} campMeta={campMeta}
                            nmsWithCampaigns={nmsWithCampaigns} />
                    </div>
                )}

                {scheduleModal && (
                    <ScheduleModal
                        campaign={scheduleModal}
                        initial={schedule[String(scheduleModal.campaign_id)]}
                        onClose={() => setScheduleModal(null)}
                        onSave={s => saveSchedule(scheduleModal.campaign_id, s)}
                    />
                )}
                {budgetLogModal && (
                    <BudgetLedgerModal
                        campaign={budgetLogModal}
                        onClose={() => setBudgetLogModal(null)}
                    />
                )}
            </div>
        </PageGuard>
    );
}
