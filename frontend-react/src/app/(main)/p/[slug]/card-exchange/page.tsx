'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import PageGuard from '@/components/PageGuard';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type {
    CardExchangeOurMode,
    ExchangeSessionStatus,
    RootCategory,
    ShowcaseAd,
    ShowcaseCursor,
} from '@/types/api';
import SearchSelect from '../ads-manager/components/SearchSelect';
import Switch from '../ads-manager/components/Switch';
import { Ic } from '../ads-manager/components/icons';
import { cThLeft, cThStyle, tdLeft, tdStyle } from '../ads-manager/components/adsShared';

/** Иконка раздела — та же, что в сайдбаре (карточка со встречными стрелками). */
const IcExchange = (p: { size?: number }) => (
    <Ic {...p}><rect width="18" height="14" x="3" y="5" rx="2" /><path d="M7 10h7l-2-2" /><path d="M17 14h-7l2 2" /></Ic>
);

// Сортировки биржи (подтверждены на живом WB).
const SORT_OPTIONS: { value: string; label: string }[] = [
    { value: 'feedbacksCount:desc', label: 'Больше отзывов' },
    { value: 'feedbacksCount:asc', label: 'Меньше отзывов' },
    { value: 'rating:desc', label: 'С высоким рейтингом' },
    { value: 'rating:asc', label: 'С низким рейтингом' },
    { value: 'totalPrice:asc', label: 'Дешевле' },
    { value: 'totalPrice:desc', label: 'Дороже' },
];

const OUR_MODES: { key: '' | CardExchangeOurMode; label: string; hint: string }[] = [
    { key: '', label: 'Вся биржа', hint: 'Все объявления биржи' },
    { key: 'categories', label: 'Корневые категории', hint: 'Объявления в корневых категориях наших товаров' },
    { key: 'exact', label: 'Предмет', hint: 'Наши артикулы на бирже — поиск по всей выдаче' },
];

const VIEW_TABS: { key: 'grid' | 'list'; label: string }[] = [
    { key: 'grid', label: 'Плитка' },
    { key: 'list', label: 'Список' },
];

/** Сегментированный переключатель — тот же вид, что у вкладок «Управления рекламой». */
function Segmented<T extends string>({ tabs, value, onChange }: {
    tabs: { key: T; label: string; hint?: string }[];
    value: T;
    onChange: (v: T) => void;
}) {
    return (
        <span style={{ display: 'inline-flex', gap: 3, background: '#f3f4f6', borderRadius: 8, padding: 3 }}>
            {tabs.map(t => (
                <button key={t.key} type="button" onClick={() => onChange(t.key)} title={t.hint}
                    style={{
                        fontSize: 13, padding: '5px 14px', borderRadius: 6, border: 'none', cursor: 'pointer', whiteSpace: 'nowrap',
                        background: value === t.key ? '#fff' : 'transparent',
                        color: value === t.key ? '#1e3a8a' : '#6b7280',
                        fontWeight: value === t.key ? 600 : 500,
                    }}>
                    {t.label}
                </button>
            ))}
        </span>
    );
}

const money = (v: number | null) => (v == null ? '—' : `${formatNumber(Number(v), 0)} ₽`);

/** Демо-наполнение для работы над вёрсткой, когда доступа к бирже нет.
 *  ТОЛЬКО в dev: на проде без доступа показывается пустое состояние, а не выдуманные карточки. */
const IS_DEV = process.env.NODE_ENV === 'development';

const DEMO_ADS: ShowcaseAd[] = [
    ['Компрессор автомобильный 12V, 150 PSI', 'AUTOPROFI', 'ИП Смирнов А. В.', 148_000, 4.8, 31_204, 393, 3, ['Китай'], true],
    ['Насос автомобильный электрический', 'CARFORT', 'ООО «Карфорт»', 96_500, 4.7, 12_880, 46, 1, ['Китай'], false],
    ['Менажница деревянная 30 см, бук', 'Kucher`s', 'ИП Кучеров Д. С.', 1_222_050, 4.9, 29_339, 83, 30, ['Российская Федерация'], false],
    ['Наушники проводные с микрофоном 3,5 Jack', 'VOLGA MARKET', 'ИП Жуков Д. А.', 106_200, 4.6, 20_341, 124, 14, ['Китай'], false],
    ['Органайзер для косметики с зеркалом', 'Opt-Family', 'ООО «Опт-Фэмили»', 601_042, 4.9, 32_451, 42, 2, null, false],
    ['Светодиодная лента RGB 5 м с пультом', 'Lentа Light', 'ИП Орлов П. Н.', 1_358_031, 4.7, 45_277, 51, 3, null, true],
    ['Кухонные весы электронные до 10 кг', 'EcoFit home', 'ООО «ЭкоФит»', 1_624_767, 4.8, 40_633, 47, 2, ['Китай'], false],
    ['Набор ключей комбинированных 12 шт', 'ToolMaster', 'ИП Белов И. И.', 254_300, 4.6, 8_412, 0, 1, ['Китай'], false],
    ['База под макияж выравнивающая', 'JOMTAM', 'ООО «Джомтам»', 10_031_371, 4.9, 156_020, 31_371, 5, ['Китай'], false],
    ['Носки набор чёрные высокие 10 пар', 'Leora', 'ИП Леонова О. К.', 1_280_000, 4.8, 64_101, 0, 8, null, false],
    ['Аэрогриль 12 л с таймером', 'HomeChef', 'ООО «ХоумШеф»', 2_140_500, 4.5, 5_902, 212, 4, ['Китай'], false],
    ['Термокружка 500 мл, нержавейка', 'DrinkGo', 'ИП Гусев Р. А.', 480_900, 4.7, 18_744, 640, 6, ['Китай'], false],
].map(([title, brand, supplier, price, rating, feedbacks, stock, variants, countries, ours], i) => ({
    ad_id: 900_001 + i,
    nm_id: 240_000_000 + i * 137,
    imt_id: 2_880_000_000 + i,
    title: title as string,
    brand: brand as string,
    supplier_name: supplier as string,
    imt_count: variants as number,
    stock_qty: stock as number,
    photo: null,
    contact_countries: countries as string[] | null,
    is_kiz: false,
    total_price: price as number,
    rating: rating as number,
    feedbacks_count: feedbacks as number,
    has_in_cart: false,
    is_card_owner: false,
    is_ours: ours as boolean,
}));

export default function CardExchangePage() {
    // Доступ к бирже — отдельный слот от доступа поставок. Пользователь его НЕ вводит:
    // раздел сам подхватывает уже настроенный доступ WB (см. useEffect ниже).
    const [session, setSession] = useState<ExchangeSessionStatus | null>(null);
    const [noAccess, setNoAccess] = useState<string | null>(null);

    const [categories, setCategories] = useState<RootCategory[]>([]);
    const [ads, setAds] = useState<ShowcaseAd[]>([]);
    const [cursor, setCursor] = useState<ShowcaseCursor | null>(null);
    const [hasMore, setHasMore] = useState(false);
    const [unmatched, setUnmatched] = useState<string[]>([]);
    const [scanNote, setScanNote] = useState<string | null>(null);

    const [loading, setLoading] = useState(true);
    const [loadingMore, setLoadingMore] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);
    const [busyAd, setBusyAd] = useState<number | null>(null);
    const [cart, setCart] = useState<Set<number>>(new Set());

    // фильтры и вид
    const [search, setSearch] = useState('');
    const [searchInput, setSearchInput] = useState('');
    const [sort, setSort] = useState('feedbacksCount:desc');
    const [rootCategory, setRootCategory] = useState('');
    const [ourMode, setOurMode] = useState<'' | CardExchangeOurMode>('');
    const [inStockOnly, setInStockOnly] = useState(false);
    const [view, setView] = useState<'grid' | 'list'>('grid');

    useEffect(() => {
        const t = setTimeout(() => setSearch(searchInput), 400);
        return () => clearTimeout(t);
    }, [searchInput]);

    // Доступ добываем сами: берём уже настроенный доступ WB (тот же, что у «Поставок»).
    // Пользователю не показываем ни токенов, ни консольных команд — только результат.
    useEffect(() => {
        let alive = true;
        (async () => {
            let st: ExchangeSessionStatus;
            try {
                st = await api.getCardExchangeSessionStatus();
            } catch {
                st = { status: 'NONE' };
            }
            if (st.status !== 'ACTIVE') {
                try {
                    st = await api.useCardExchangeSessionFromSupply();
                } catch (e) {
                    if (alive) setNoAccess(e instanceof Error ? e.message : 'Нет доступа к бирже WB');
                }
            }
            if (alive) setSession(st);
        })();
        api.getCardExchangeCategories().then(c => { if (alive) setCategories(c); })
            .catch(e => { if (alive) setActionError(e instanceof Error ? e.message : 'Не удалось загрузить справочник категорий'); });
        return () => { alive = false; };
    }, []);

    const sessionOk = session?.status === 'ACTIVE';
    // Нет доступа + dev → показываем интерфейс на демо-карточках, чтобы можно было
    // работать над вёрсткой. На проде демо не включается никогда.
    const demo = !!session && !sessionOk && IS_DEV;
    const showUi = sessionOk || demo;

    const categoryOptions = useMemo(
        () => categories.map(c => ({ value: c.category, label: `${c.category} (${c.subject_count})` })),
        [categories],
    );

    // Сквозной id запроса: применяем ТОЛЬКО ответ последнего (смена фильтров, debounce,
    // двойной монтаж StrictMode держат несколько запросов в полёте).
    const reqIdRef = useRef(0);

    const load = useCallback(async (reset: boolean) => {
        const [sortField, sortOrder] = sort.split(':');
        const myReq = ++reqIdRef.current;
        if (reset) { setLoading(true); setError(null); } else { setLoadingMore(true); }
        try {
            const res = await api.getCardExchangeShowcase({
                search: search.trim() || null,
                root_categories: rootCategory ? [rootCategory] : null,
                our_mode: ourMode || null,
                has_stocks: inStockOnly ? true : null,
                sort_field: sortField,
                sort_order: sortOrder,
                cursor: reset ? null : cursor,
            });
            if (myReq !== reqIdRef.current) return;
            setAds(prev => (reset ? res.ads : [...prev, ...res.ads]));
            setCursor(res.next_cursor);
            setHasMore(res.has_more);
            setUnmatched(res.unmatched_subjects || []);
            setScanNote(res.scanned_pages != null
                ? `Просканировано страниц: ${res.scanned_pages}${res.scan_truncated ? ' (упёрлись в лимит — показаны не все)' : ''}`
                : null);
            const inCartIds = res.ads.filter(a => a.has_in_cart).map(a => a.ad_id);
            setCart(prev => (reset ? new Set(inCartIds) : new Set([...prev, ...inCartIds])));
        } catch (e) {
            if (myReq !== reqIdRef.current) return;
            if (reset) setError(e instanceof Error ? e.message : 'Не удалось загрузить биржу');
            else setActionError(e instanceof Error ? e.message : 'Не удалось догрузить');
        } finally {
            if (myReq === reqIdRef.current) {
                if (reset) setLoading(false); else setLoadingMore(false);
            }
        }
    }, [search, rootCategory, ourMode, inStockOnly, sort, cursor]);

    useEffect(() => {
        if (!sessionOk) return;
        void load(true);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [search, rootCategory, ourMode, inStockOnly, sort, sessionOk]);

    const toggleCart = async (ad: ShowcaseAd) => {
        setActionError(null);
        const inCart = cart.has(ad.ad_id);
        if (demo) {  // демо-режим: корзина живёт только в состоянии страницы
            setCart(prev => { const n = new Set(prev); if (inCart) n.delete(ad.ad_id); else n.add(ad.ad_id); return n; });
            return;
        }
        setBusyAd(ad.ad_id);
        try {
            if (inCart) {
                await api.deleteCardsFromCart([ad.ad_id]);
                setCart(prev => { const n = new Set(prev); n.delete(ad.ad_id); return n; });
            } else {
                await api.addCardToCart(ad.ad_id);
                setCart(prev => new Set(prev).add(ad.ad_id));
            }
        } catch (e) {
            setActionError(e instanceof Error ? e.message : 'Ошибка корзины');
        } finally { setBusyAd(null); }
    };

    // Сортировка в списке — серверная (WB умеет только эти три поля), поэтому клик по
    // заголовку меняет общий sort и перезагружает выдачу с начала: иначе при курсорной
    // пагинации отсортировалась бы лишь загруженная часть.
    const [sortField, sortOrder] = sort.split(':');
    const toggleSort = (field: string) => {
        setSort(sortField === field ? `${field}:${sortOrder === 'asc' ? 'desc' : 'asc'}` : `${field}:desc`);
    };
    const sortArrow = (field: string) => (sortField === field ? (sortOrder === 'asc' ? ' ↑' : ' ↓') : '');
    /** Заголовок сортируемой колонки — вид и поведение как в «Управлении рекламой». */
    const sortableTh = (field: string, label: string, style: React.CSSProperties) => (
        <th style={{ ...style, cursor: 'pointer' }} onClick={() => toggleSort(field)}
            title="Сортировать (данные перезапрашиваются с биржи)">
            {label}{sortArrow(field)}
        </th>
    );

    // В демо запросов нет — сортируем прямо на месте, чтобы клики были видны.
    const demoSorted = useMemo(() => {
        const val = (a: ShowcaseAd) => sortField === 'totalPrice' ? Number(a.total_price ?? 0)
            : sortField === 'rating' ? Number(a.rating) : Number(a.feedbacks_count);
        return [...DEMO_ADS].sort((a, b) => (sortOrder === 'asc' ? val(a) - val(b) : val(b) - val(a)));
    }, [sortField, sortOrder]);

    const visibleAds = demo ? demoSorted : ads;
    // В демо запросов нет — начальный loading=true не должен прятать карточки.
    const busy = loading && !demo;

    const cartBtn = (ad: ShowcaseAd, compact = false) => {
        const inCart = cart.has(ad.ad_id);
        return (
            <button className={`btn btn-sm ${inCart ? 'btn-secondary' : 'btn-primary'}`}
                style={compact ? { padding: '3px 10px', fontSize: 12 } : { marginTop: 'auto' }}
                disabled={busyAd === ad.ad_id || ad.is_card_owner}
                onClick={e => { e.stopPropagation(); void toggleCart(ad); }}
                title={ad.is_card_owner ? 'Это ваше объявление' : undefined}>
                {ad.is_card_owner ? 'Ваше' : busyAd === ad.ad_id ? '…' : inCart ? 'Убрать' : 'Добавить'}
            </button>
        );
    };

    return (
        <PageGuard page="card-exchange">
            <div className="animate-in">
                <div style={{ marginBottom: 16 }}>
                    <h1 style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 28, fontWeight: 700, margin: 0 }}>
                        <IcExchange size={26} />Биржа карточек товаров
                    </h1>
                </div>

                {/* Нет доступа — короткое сообщение без технических подробностей. */}
                {session && !sessionOk && !demo && (
                    <div className="glass-card" style={{ textAlign: 'center', padding: 40 }}>
                        <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--color-text)', marginBottom: 6 }}>
                            Нет доступа к бирже WB
                        </div>
                        <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                            {noAccess || 'Доступ к кабинету WB не настроен или истёк.'}
                            <br />Обновите доступ WB в разделе «Поставки» — биржа подхватит его автоматически.
                        </div>
                    </div>
                )}

                {!session && (
                    <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: 32 }}>Загрузка…</div>
                )}

                {showUi && (<>
                    {/* Фильтры — в одну строку, как в «Управлении рекламой» */}
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
                        <SearchSelect value={rootCategory} onChange={setRootCategory} options={categoryOptions}
                            placeholder="Категория: все" allLabel="Все категории" maxWidth={280} />
                        <SearchSelect value={sort} onChange={setSort} options={SORT_OPTIONS}
                            placeholder="Больше отзывов" allLabel="Больше отзывов" maxWidth={220} />
                        <Segmented tabs={OUR_MODES.map(m => ({ key: m.key, label: m.label, hint: m.hint }))}
                            value={ourMode} onChange={setOurMode} />
                        <Segmented tabs={VIEW_TABS} value={view} onChange={setView} />
                        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text)' }}>
                            <Switch on={inStockOnly} onClick={() => setInStockOnly(v => !v)} size="sm" ariaLabel="Только в наличии" />
                            В наличии
                        </label>
                    </div>

                    {/* Поиск и счётчики */}
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
                        <input value={searchInput} onChange={e => setSearchInput(e.target.value)}
                            placeholder="Название, артикул WB или продавца"
                            style={{ flex: '1 1 280px', maxWidth: 380, background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '7px 12px', fontSize: 13, color: 'var(--color-text)' }} />
                        <span style={{ marginLeft: 'auto', fontSize: 12.5, color: 'var(--color-text-muted)' }}>
                            {session?.supplier_id && <>Кабинет: {session.supplier_id} · </>}
                            Показано: {formatNumber(visibleAds.length, 0)} · В корзине: {formatNumber(cart.size, 0)}
                        </span>
                    </div>

                    {unmatched.length > 0 && (
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 10 }}>
                            Предметов категории нет на бирже: {formatNumber(unmatched.length, 0)} — по ним объявлений нет.
                        </div>
                    )}
                    {scanNote && <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 10 }}>{scanNote}</div>}
                    {actionError && <div className="glass-card" style={{ marginBottom: 12, color: 'var(--color-danger)' }}>{actionError}</div>}

                    {busy && <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: 32 }}>Загрузка…</div>}
                    {error && !busy && !demo && (
                        <div className="glass-card" style={{ color: 'var(--color-danger)' }}>
                            {error} <button className="btn btn-sm btn-secondary" onClick={() => void load(true)}>Повторить</button>
                        </div>
                    )}
                    {!busy && !error && visibleAds.length === 0 && (
                        <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: 48 }}>
                            Ничего не найдено. Измените фильтры или запрос.
                        </div>
                    )}

                    {/* Плитка */}
                    {!busy && !error && visibleAds.length > 0 && view === 'grid' && (
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 14 }}>
                            {visibleAds.map(ad => (
                                <div key={ad.ad_id} className="glass-card" style={{ display: 'flex', flexDirection: 'column', padding: 12, gap: 6 }}>
                                    <div style={{ position: 'relative', aspectRatio: '3 / 4', background: 'var(--color-bg-hover)', borderRadius: 8, overflow: 'hidden' }}>
                                        {ad.photo
                                            // eslint-disable-next-line @next/next/no-img-element -- внешняя CDN-картинка биржи WB, как в WbThumb
                                            ? <img src={ad.photo} alt={ad.title ?? ''} loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                                            : <div style={{ width: '100%', height: '100%' }} />}
                                        {(ad.imt_count ?? 0) > 1 && (
                                            <span className="badge badge-secondary" style={{ position: 'absolute', left: 8, bottom: 8, fontSize: 10.5 }}>
                                                {formatNumber(ad.imt_count!, 0)} вариантов
                                            </span>
                                        )}
                                        {ad.is_ours && (
                                            <span className="badge badge-success" style={{ position: 'absolute', right: 8, top: 8, fontSize: 10.5 }}>Наша</span>
                                        )}
                                    </div>
                                    <div style={{ fontSize: 17, fontWeight: 700, color: 'var(--color-text)' }}>{money(ad.total_price)}</div>
                                    <div style={{ fontSize: 12.5, color: 'var(--color-text)', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                                        {[ad.brand, ad.title].filter(Boolean).join(' / ')}
                                    </div>
                                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                        ★ {Number(ad.rating).toFixed(1)} · {formatNumber(ad.feedbacks_count, 0)} отзывов
                                    </div>
                                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                        Остатки: {ad.stock_qty ? `${formatNumber(ad.stock_qty, 0)} шт` : 'нет'}
                                    </div>
                                    {cartBtn(ad)}
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Список — таблица в стиле «Управления рекламой» */}
                    {!busy && !error && visibleAds.length > 0 && view === 'list' && (
                        <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                            <div style={{ overflowX: 'auto' }}>
                                <table className="data-table" style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, backgroundColor: '#fff' }}>
                                    <thead><tr>
                                        <th style={{ ...cThLeft, width: 46 }} />
                                        <th style={cThLeft}>ТОВАР</th>
                                        <th style={cThLeft}>ПРОДАВЕЦ</th>
                                        {sortableTh('totalPrice', 'ЦЕНА ₽', { ...cThStyle, width: 110 })}
                                        {sortableTh('rating', 'РЕЙТИНГ', { ...cThStyle, width: 70 })}
                                        {sortableTh('feedbacksCount', 'ОТЗЫВЫ', { ...cThStyle, width: 90 })}
                                        <th style={{ ...cThStyle, width: 90 }}>ОСТАТКИ</th>
                                        <th style={{ ...cThStyle, width: 80 }}>ВАРИАНТОВ</th>
                                        <th style={{ ...cThLeft, width: 110 }}>СТРАНА</th>
                                        <th style={{ ...cThStyle, width: 96 }} />
                                    </tr></thead>
                                    <tbody>
                                        {visibleAds.map(ad => (
                                            <tr key={ad.ad_id} style={{ color: '#111827' }}>
                                                <td style={{ ...tdLeft, padding: '3px 6px' }}>
                                                    {ad.photo
                                                        // eslint-disable-next-line @next/next/no-img-element -- внешняя CDN-картинка биржи WB
                                                        ? <img src={ad.photo} alt="" loading="lazy" style={{ width: 34, height: 44, objectFit: 'cover', borderRadius: 6, border: '1px solid var(--color-border)' }} />
                                                        : <div style={{ width: 34, height: 44, borderRadius: 6, background: 'var(--color-bg-hover)' }} />}
                                                </td>
                                                <td style={{ ...tdLeft, whiteSpace: 'normal', lineHeight: 1.25 }}>
                                                    <div style={{ fontWeight: 600 }}>{ad.title ?? '—'}</div>
                                                    <div style={{ fontSize: 11, color: '#6b7280' }}>
                                                        {ad.brand ?? '—'}{ad.nm_id ? ` · ${ad.nm_id}` : ''}
                                                        {ad.is_ours && <span className="badge badge-success" style={{ marginLeft: 6, fontSize: 10 }}>наша</span>}
                                                    </div>
                                                </td>
                                                <td style={{ ...tdLeft, whiteSpace: 'normal' }}>{ad.supplier_name ?? '—'}</td>
                                                <td style={{ ...tdStyle, fontWeight: 700 }}>{money(ad.total_price)}</td>
                                                <td style={tdStyle}>{Number(ad.rating).toFixed(1)}</td>
                                                <td style={tdStyle}>{formatNumber(ad.feedbacks_count, 0)}</td>
                                                <td style={tdStyle}>{ad.stock_qty ? formatNumber(ad.stock_qty, 0) : '—'}</td>
                                                <td style={tdStyle}>{ad.imt_count ? formatNumber(ad.imt_count, 0) : '—'}</td>
                                                <td style={tdLeft}>{ad.contact_countries?.length ? ad.contact_countries.join(', ') : '—'}</td>
                                                <td style={{ ...tdStyle, textAlign: 'center' }}>{cartBtn(ad, true)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    )}

                    {!busy && !error && hasMore && (
                        <div style={{ textAlign: 'center', marginTop: 20 }}>
                            <button className="btn btn-secondary" onClick={() => void load(false)} disabled={loadingMore}>
                                {loadingMore ? 'Загрузка…' : 'Показать ещё'}
                            </button>
                        </div>
                    )}
                </>)}
            </div>
        </PageGuard>
    );
}
