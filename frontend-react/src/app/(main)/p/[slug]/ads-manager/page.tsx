'use client';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { exportToExcel } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import { useParams, useRouter } from 'next/navigation';
import { IcMegaphone, IcRefresh, IcDownload, IcSliders, IcColumns, IcPause, IcPlay, IcClock, IcGear, IcHistory, IcExternal, IcSearch, IcX } from './components/icons';
import SearchSelect from './components/SearchSelect';
import AutopayModal from './components/AutopayModal';
import AutopayLogModal from './components/AutopayLogModal';
import WbThumb from './components/WbThumb';
import AdsPeriodPicker from './components/AdsPeriodPicker';
import InfoTip from './components/InfoTip';
import { fmt, num, fmtPct, iso, STATUS_BADGE, thStyle, thLeft, tdStyle, tdLeft, wbCampaignUrl, campaignTypeBadge } from './components/adsShared';
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

// Колонки списка кампаний (для настройки видимости и рендера).
// blockStart — начало логического блока: слева рисуем тонкую полупрозрачную линию-разделитель.
const CAMP_COLS: { key: string; label: string; sort?: keyof AdsManagerCampaign; align?: 'left' | 'center' | 'right'; title?: string; fixed?: boolean; blockStart?: boolean }[] = [
    { key: 'name', label: 'Кампания', align: 'left', fixed: true },
    { key: 'status', label: 'Статус', align: 'left' },
    { key: 'photo', label: 'Товар', align: 'center', fixed: true },
    { key: 'budget', label: 'Остаток бюджета ₽', sort: 'budget', blockStart: true, title: 'Сверху — бюджет за сегодня (остаток + расход), снизу — текущий остаток' },
    { key: 'spend', label: 'Расход ₽', sort: 'spend_period', title: 'Сверху — расход за период (30 дней), снизу — сегодня' },
    { key: 'clicks_period', label: 'Клики', sort: 'clicks_period', blockStart: true, title: 'Клики по рекламе кампании за период' },
    { key: 'ctr', label: 'CTR', sort: 'ctr', title: 'Конверсия из показа в клик' },
    { key: 'cpc', label: 'CPC ₽', sort: 'cpc', title: 'Стоимость 1 клика: расход кампании / клики за период' },
    { key: 'cpl', label: 'CPL ₽', sort: 'cpl', title: 'Стоимость 1 корзины: расход кампании / корзины её товаров за период' },
    { key: 'cpo', label: 'CPO ₽', sort: 'cpo', title: 'Стоимость 1 заказа: расход кампании / заказы её товаров за период' },
    { key: 'ad_click_share', label: 'Рекл. клики %', sort: 'ad_click_share', title: 'Доля рекламных кликов от всех переходов товаров кампании. ≥50% — органика слабеет, ≥60% — критично' },
    { key: 'drr', label: 'ДРР', sort: 'drr', blockStart: true, title: 'Соотношение затрат к заказам: расход кампании / сумма заказов её товаров за период' },
    { key: 'margin', label: 'Маржинальность', sort: 'margin', title: 'Маржинальность за период по товарам кампании: прибыль / выручка' },
    { key: 'cr_cart', label: 'Конв. корзина', sort: 'cr_cart', title: 'Конверсия переход→корзина по товарам кампании' },
    { key: 'cr_order', label: 'Конв. заказ', sort: 'cr_order', title: 'Конверсия корзина→заказ по товарам кампании' },
    { key: 'nm_count', label: 'Товаров', sort: 'nm_count', blockStart: true },
    { key: 'autopay', label: 'Автопополнение', align: 'center' },
    { key: 'wb', label: 'WB', align: 'center' },
];
const CAMP_TOGGLE_KEYS = CAMP_COLS.filter(c => !c.fixed).map(c => c.key);
// Тонкая полупрозрачная линия-разделитель блоков
const BLOCK_DIVIDER = '1px solid rgba(17,24,39,0.08)';
// Тёмно-серая шапка таблицы кампаний (выделяется на фоне данных)
const cThStyle: React.CSSProperties = { ...thStyle, background: '#374151', color: '#e5e7eb', borderBottom: '1px solid #4b5563' };
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
    const [autopayLogModal, setAutopayLogModal] = useState<AdsManagerCampaign | null>(null);
    const [stateBusy, setStateBusy] = useState<number | null>(null);  // кампания в процессе смены статуса
    // Тулбар списка: фильтр статуса (завершённые скрыты), видимость колонок, открытое меню
    const [statusFilter, setStatusFilter] = useState('not_completed');
    const [visibleCols, setVisibleCols] = useState<Set<string>>(() => new Set(CAMP_TOGGLE_KEYS));
    const [openMenu, setOpenMenu] = useState<'filter' | 'cols' | null>(null);
    const [page, setPage] = useState(1);
    const [pageInput, setPageInput] = useState('');
    const PER_PAGE = 50;

    // Календарь = период метрик по ВСЕМ кампаниям (день или диапазон). Пусто → дефолт последние 30 дней.
    const [periodFrom, setPeriodFrom] = useState('');
    const [periodTo, setPeriodTo] = useState('');
    const dateFrom = periodFrom || iso(new Date(Date.now() - 29 * 86400_000));
    const dateTo = periodTo || iso(new Date());

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
        setStateBusy(c.campaign_id); setError('');
        try {
            await api.setCampaignState(c.campaign_id, active);
            await loadCampaigns(dateFrom, dateTo);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось изменить статус кампании');
        } finally { setStateBusy(null); }
    }, [loadCampaigns, dateFrom, dateTo]);

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
                setVisibleCols(set);
            }
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
            if (!campSort) return 0; // без сортировки — порядок бэка (активные выше)
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

    const openCampaign = (c: AdsManagerCampaign) => router.push(`/p/${slug}/ads-manager/campaign/${c.campaign_id}`);

    const visibleCampColumns = CAMP_COLS.filter(col => col.fixed || visibleCols.has(col.key));
    const renderCampCell = (key: string, c: AdsManagerCampaign): React.ReactNode => {
        switch (key) {
            case 'name': return (
                <div style={{ maxWidth: 240 }}>
                    <div style={{ fontWeight: 600, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.name || `#${c.campaign_id}`}</div>
                    <div style={{ fontSize: 10, color: '#9ca3af', display: 'flex', alignItems: 'center', gap: 5 }}>
                        #{c.campaign_id}
                        {(() => { const b = campaignTypeBadge(c); return <span style={{ padding: '0 5px', borderRadius: 4, background: b.bg, color: b.color, fontWeight: 700, fontSize: 9.5, letterSpacing: 0.2 }}>{b.label}</span>; })()}
                    </div>
                </div>
            );
            case 'photo': return <WbThumb nmId={c.nm_ids[0]} size={38} />;
            case 'status': return (
                <>
                    <span className={`badge ${STATUS_BADGE[c.status] || 'badge-secondary'}`} style={{ fontSize: 10, padding: '2px 7px' }}>{c.status_label}</span>
                    {(c.status === 9 || c.status === 11) && (
                        <button onClick={e => { e.stopPropagation(); toggleCampaignState(c); }} disabled={stateBusy === c.campaign_id}
                            title={c.status === 9 ? 'Поставить на паузу' : 'Запустить кампанию'}
                            style={{ marginLeft: 6, border: 'none', background: 'none', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', color: c.status === 9 ? '#f59e0b' : '#10b981' }}>
                            {stateBusy === c.campaign_id ? '…' : c.status === 9 ? <IcPause size={13} /> : <IcPlay size={13} />}
                        </button>
                    )}
                </>
            );
            case 'budget': {
                // Сверху полупрозрачным — бюджет за сегодня (остаток + расход сегодня), снизу — остаток
                const todayTotal = num(c.budget) + num(c.spend_today);
                return (
                    <div style={{ lineHeight: 1.25 }}>
                        <div style={{ fontSize: 10.5, color: '#9ca3af' }} title="Бюджет за сегодня: остаток + расход сегодня">{fmt(todayTotal)}</div>
                        <div style={{ fontWeight: 600, color: c.budget <= 0 && c.status === 9 ? '#ef4444' : '#111827' }}>{fmt(c.budget)}</div>
                    </div>
                );
            }
            case 'spend': return (
                <div style={{ lineHeight: 1.25 }}>
                    <div style={{ fontWeight: 600 }} title="Расход за период (30 дней)">{fmt(c.spend_period)}</div>
                    <div style={{ fontSize: 10.5, color: '#9ca3af' }} title="Расход сегодня">{fmt(c.spend_today)}</div>
                </div>
            );
            case 'clicks_period': return fmt(c.clicks_period);
            case 'ctr': return fmtPct(c.ctr);
            case 'cpc': return c.cpc > 0 ? fmt(c.cpc) : '—';
            case 'cpl': return c.cpl > 0 ? fmt(c.cpl) : '—';
            case 'cpo': return c.cpo > 0 ? fmt(c.cpo) : '—';
            case 'ad_click_share': return <span style={{ fontWeight: 600, color: c.ad_click_share >= 60 ? '#ef4444' : c.ad_click_share >= 50 ? '#f59e0b' : c.ad_click_share > 0 ? '#374151' : '#9ca3af' }}>{c.ad_click_share > 0 ? fmtPct(c.ad_click_share) : '—'}</span>;
            case 'drr': return <span style={{ fontWeight: 600, color: c.drr > 30 ? '#ef4444' : c.drr > 7 ? '#f59e0b' : c.drr > 0 ? '#10b981' : '#9ca3af' }}>{c.drr > 0 ? fmtPct(c.drr) : '—'}</span>;
            case 'margin': return <span style={{ fontWeight: 600, color: c.margin > 20 ? '#10b981' : c.margin > 0 ? '#65a30d' : c.margin < 0 ? '#ef4444' : '#9ca3af' }}>{c.margin !== 0 ? fmtPct(c.margin) : '—'}</span>;
            case 'cr_cart': return c.cr_cart > 0 ? fmtPct(c.cr_cart) : '—';
            case 'cr_order': return c.cr_order > 0 ? fmtPct(c.cr_order) : '—';
            case 'nm_count': return c.nm_count;
            case 'autopay': {
                const ap = autopay[String(c.campaign_id)];
                // Зелёный — включено; красный — настроено, но выключено; серый — ни разу не настраивалось
                const color = ap ? (ap.enabled ? '#10b981' : '#ef4444') : '#9ca3af';
                const title = ap ? (ap.enabled ? 'Автопополнение включено' : 'Автопополнение настроено, но выключено') : 'Автопополнение ни разу не настраивалось';
                return (
                    <span style={{ whiteSpace: 'nowrap' }}>
                        <button onClick={e => { e.stopPropagation(); setAutopayModal(c); }} title={title}
                            style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, color, fontWeight: ap?.enabled ? 600 : 400, whiteSpace: 'nowrap', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                            {ap?.enabled
                                ? <><IcClock size={13} />{`${String(ap.hour).padStart(2, '0')}:00 · ${fmt(ap.amount)}₽`}</>
                                : <><IcGear size={13} />{ap ? 'выключено' : 'настроить'}</>}
                        </button>
                        <button onClick={e => { e.stopPropagation(); setAutopayLogModal(c); }} title="История пополнений"
                            style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#6b7280', padding: '0 4px', display: 'inline-flex', alignItems: 'center' }}><IcHistory size={14} /></button>
                    </span>
                );
            }
            case 'wb': return (
                <a href={wbCampaignUrl(c, { from: dateFrom, to: dateTo })} target="_blank" rel="noreferrer" title="Открыть кампанию в кабинете WB" onClick={e => e.stopPropagation()}
                    style={{ color: 'var(--color-accent)', textDecoration: 'none', display: 'inline-flex', alignItems: 'center' }}><IcExternal size={15} /></a>
            );
            default: return null;
        }
    };

    return (
        <PageGuard page="ads-manager">
            <div className="animate-in">
                <div style={{ marginBottom: 16 }}>
                    <h1 style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 28, fontWeight: 700, margin: 0 }}><IcMegaphone size={26} />Управление рекламой</h1>
                </div>

                {/* Каскад: предмет ↔ бренд ↔ артикул (взаимно сужаются) — с поиском */}
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
                    <SearchSelect value={subject} onChange={onSubject} placeholder="Предмет: все" maxWidth={260}
                        options={subjectOptions.map(s => ({ value: s, label: s }))} />
                    <SearchSelect value={brand} onChange={onBrand} placeholder="Бренд: все" maxWidth={220}
                        options={brandOptions.map(b => ({ value: b, label: b }))} />
                    <SearchSelect value={article} onChange={onArticle} placeholder="Артикул: все" maxWidth={280}
                        options={articleOptions.map(t => ({ value: String(t.nm_id), label: t.vendor_code }))} />
                    {/* Календарь = период метрик по всем кампаниям (день/диапазон). Пусто → последние 30 дней */}
                    <AdsPeriodPicker from={periodFrom} to={periodTo} placeholder="календарь" minWidth={230}
                        onApply={(f, t) => { setPeriodFrom(f); setPeriodTo(t); }} />
                    {(brand || subject || article || search) && (
                        <button className="btn btn-secondary btn-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12 }} onClick={resetFilters}><IcX size={13} />Сбросить</button>
                    )}
                </div>

                {error && (
                    <div className="glass-card" style={{ padding: '12px 20px', marginBottom: 12, border: '1px solid var(--color-danger)' }}>
                        <span style={{ fontSize: 13, color: 'var(--color-danger)' }}>⚠️ {error}</span>
                    </div>
                )}

                {/* ─── Список кампаний ─── */}
                <div className="glass-card static" style={{ padding: 0, overflow: 'hidden' }}>
                    <div style={{ padding: '10px 16px', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', borderBottom: '1px solid #e5e7eb', background: '#f9fafb' }}>
                        <div style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
                            <span style={{ position: 'absolute', left: 9, color: '#9ca3af', display: 'inline-flex' }}><IcSearch size={15} /></span>
                            <input placeholder="Поиск по ID или названию" value={search} onChange={e => setSearch(e.target.value)}
                                style={{ background: '#fff', border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px 6px 30px', color: 'var(--color-text)', fontSize: 13, width: 240 }} />
                        </div>
                        <button onClick={handleSync} disabled={syncing} className="btn btn-secondary btn-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                            <IcRefresh />{syncing ? 'Синхронизация…' : 'Синхронизировать'}
                        </button>
                        <button className="btn btn-secondary btn-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }} onClick={exportCampaigns} disabled={visibleCampaigns.length === 0} title="Выгрузить таблицу в Excel (с учётом фильтров)">
                            <IcDownload />Excel
                        </button>
                        <span style={{ fontSize: 12, color: 'var(--color-text-dim)', marginLeft: 'auto' }}>
                            Показано: {visibleCampaigns.length} из {campaigns.length}
                        </span>
                        {/* Фильтр по статусу */}
                        <div style={{ position: 'relative' }}>
                            <button onClick={() => setOpenMenu(openMenu === 'filter' ? null : 'filter')} className="btn btn-secondary btn-sm"
                                style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: statusFilter !== 'not_completed' ? 700 : 500 }}><IcSliders />Фильтр</button>
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
                                </div>
                            </>)}
                        </div>
                        {/* Настройки колонок */}
                        <div style={{ position: 'relative' }}>
                            <button onClick={() => setOpenMenu(openMenu === 'cols' ? null : 'cols')} className="btn btn-secondary btn-sm" style={{ display: 'inline-flex', alignItems: 'center', fontSize: 14 }} title="Настройка колонок"><IcColumns /></button>
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
                    <div style={{ overflowX: 'auto' }}>
                        {loading ? <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div> :
                            visibleCampaigns.length === 0 ? <div style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>{campFiltered ? 'Ничего не найдено по заданным фильтрам' : 'Кампаний нет — нажмите «Синхронизировать»'}</div> : (
                                <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                                    <thead><tr>
                                        {visibleCampColumns.map(col => {
                                            const b = col.align === 'left' ? cThLeft : col.align === 'center' ? { ...cThStyle, textAlign: 'center' as const } : cThStyle;
                                            const base = col.blockStart ? { ...b, borderLeft: BLOCK_DIVIDER } : b;
                                            return (
                                                <th key={col.key} style={col.sort ? { ...base, cursor: 'pointer' } : base}
                                                    onClick={col.sort ? () => toggleCampSort(col.sort!) : undefined}>
                                                    {col.title ? <InfoTip text={col.title}>{col.label}</InfoTip> : col.label}{col.sort ? campArrow(col.sort) : ''}
                                                </th>
                                            );
                                        })}
                                    </tr></thead>
                                    <tbody>
                                        {pageCampaigns.map(c => (
                                            <tr key={c.campaign_id} style={{ color: '#111827', cursor: 'pointer' }}
                                                onClick={() => openCampaign(c)}
                                                title="Открыть кампанию: метрики по дням и кластеризатор">
                                                {visibleCampColumns.map(col => {
                                                    const b = col.align === 'left' ? tdLeft : col.align === 'center' ? { ...tdStyle, textAlign: 'center' as const } : tdStyle;
                                                    const base = col.blockStart ? { ...b, borderLeft: BLOCK_DIVIDER } : b;
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
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, flexWrap: 'wrap', padding: '10px 16px', borderTop: '1px solid #e5e7eb', background: '#f9fafb' }}>
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
