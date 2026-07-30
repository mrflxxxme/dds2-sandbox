'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import PageHeader from '@/components/PageHeader';
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

// Сортировки биржи (подтверждены на живом WB): feedbacksCount/rating/totalPrice.
// «Новые/Старые» (поле даты) не включены — точная строка field будет подтверждена
// на прод-смоуке и добавлена одной строкой.
const SORT_OPTIONS: { value: string; label: string }[] = [
    { value: 'feedbacksCount:desc', label: 'Больше отзывов' },
    { value: 'feedbacksCount:asc', label: 'Меньше отзывов' },
    { value: 'rating:desc', label: 'С высоким рейтингом' },
    { value: 'rating:asc', label: 'С низким рейтингом' },
    { value: 'totalPrice:asc', label: 'Дешевле' },
    { value: 'totalPrice:desc', label: 'Дороже' },
];

const OUR_MODES: { value: '' | CardExchangeOurMode; label: string; hint: string }[] = [
    { value: '', label: 'Все объявления', hint: 'Вся биржа' },
    { value: 'categories', label: 'Наши категории', hint: 'Объявления в предметах наших товаров' },
    { value: 'exact', label: 'Точно наши', hint: 'Наши артикулы на бирже (скан выдачи)' },
];

function money(v: number | null): string {
    if (v == null) return '—';
    return `${formatNumber(Number(v), 0)} ₽`;
}

export default function CardExchangePage() {
    // Собственная сессия биржи (отдельный слот от сессии поставок).
    const [session, setSession] = useState<ExchangeSessionStatus | null>(null);
    const [tokenInput, setTokenInput] = useState('');
    const [savingToken, setSavingToken] = useState(false);
    const [tokenError, setTokenError] = useState<string | null>(null);

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

    // фильтры
    const [search, setSearch] = useState('');
    const [searchInput, setSearchInput] = useState('');
    const [sort, setSort] = useState('feedbacksCount:desc');
    const [rootCategory, setRootCategory] = useState('');
    const [ourMode, setOurMode] = useState<'' | CardExchangeOurMode>('');
    const [inStockOnly, setInStockOnly] = useState(false);

    // debounce поиска
    useEffect(() => {
        const t = setTimeout(() => setSearch(searchInput), 400);
        return () => clearTimeout(t);
    }, [searchInput]);

    useEffect(() => {
        api.getCardExchangeSessionStatus()
            .then(setSession)
            .catch(() => setSession({ status: 'NONE' }));
        api.getCardExchangeCategories()
            .then(setCategories)
            .catch((e) => setActionError(e instanceof Error ? e.message : 'Не удалось загрузить справочник категорий'));
    }, []);

    const sessionOk = session?.status === 'ACTIVE';

    const saveToken = async () => {
        setTokenError(null);
        setSavingToken(true);
        try {
            const st = await api.setCardExchangeSession(tokenInput.trim());
            setSession(st);
            setTokenInput('');
            void load(true);
        } catch (e) {
            setTokenError(e instanceof Error ? e.message : 'Не удалось сохранить доступ');
        } finally {
            setSavingToken(false);
        }
    };

    const takeFromSupply = async () => {
        setTokenError(null);
        setSavingToken(true);
        try {
            const st = await api.useCardExchangeSessionFromSupply();
            setSession(st);
            void load(true);
        } catch (e) {
            setTokenError(e instanceof Error ? e.message : 'Не удалось взять доступ из поставок');
        } finally {
            setSavingToken(false);
        }
    };

    // Одна команда для консоли кабинета WB: собирает доступ (токен + cookie) в буфер.
    // Одного authorizev3 бирже мало — WB отвечает 401 без анти-бот-cookie.
    const GRAB_SNIPPET =
        "copy(JSON.stringify({authorizev3:localStorage['wb-eu-passport-v2.access-token']," +
        "cookies:document.cookie.split('; ').map(p=>{const i=p.indexOf('=');" +
        "return{name:p.slice(0,i),value:p.slice(i+1),domain:'.wildberries.ru',path:'/'}})}))";

    const categoryOptions = useMemo(
        () => categories.map((c) => ({ value: c.category, label: `${c.category} (${c.subject_count})` })),
        [categories],
    );

    // Сквозной id запроса: применяем ТОЛЬКО ответ последнего. Смена фильтров и debounce
    // поиска (а в dev — двойной монтаж StrictMode) держат несколько запросов в полёте,
    // и медленный ранний ответ иначе перетирает свежий (см. learnings.md).
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
            if (myReq !== reqIdRef.current) return;  // устаревший ответ — игнорируем
            setAds((prev) => (reset ? res.ads : [...prev, ...res.ads]));
            setCursor(res.next_cursor);
            setHasMore(res.has_more);
            setUnmatched(res.unmatched_subjects || []);
            setScanNote(
                res.scanned_pages != null
                    ? `Просканировано страниц: ${res.scanned_pages}${res.scan_truncated ? ' (упёрлись в лимит — показаны не все)' : ''}`
                    : null,
            );
            // Корзину синхронизируем и при догрузке: иначе уже лежащие в корзине карточки
            // следующих страниц покажут «Добавить» и клик уйдёт в повторный add.
            const inCartIds = res.ads.filter((a) => a.has_in_cart).map((a) => a.ad_id);
            setCart((prev) => (reset ? new Set(inCartIds) : new Set([...prev, ...inCartIds])));
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

    // перезагрузка страницы 1 при смене любого фильтра; без активной сессии витрину не зовём
    useEffect(() => {
        if (!sessionOk) return;
        void load(true);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [search, rootCategory, ourMode, inStockOnly, sort, sessionOk]);

    const toggleCart = async (ad: ShowcaseAd) => {
        setActionError(null);
        setBusyAd(ad.ad_id);
        const inCart = cart.has(ad.ad_id);
        try {
            if (inCart) {
                await api.deleteCardsFromCart([ad.ad_id]);
                setCart((prev) => { const n = new Set(prev); n.delete(ad.ad_id); return n; });
            } else {
                await api.addCardToCart(ad.ad_id);
                setCart((prev) => new Set(prev).add(ad.ad_id));
            }
        } catch (e) {
            setActionError(e instanceof Error ? e.message : 'Ошибка корзины');
        } finally {
            setBusyAd(null);
        }
    };

    return (
        <PageGuard page="card-exchange">
            <PageHeader
                title="🃏 Биржа карточек товаров"
                subtitle="Перенос готовых карточек WB (с отзывами и рейтингом). Просмотр, фильтры по категориям и нашим товарам, сбор в корзину."
            />

            {/* Доступ к бирже — отдельная сессия WB, независимая от сессии поставок */}
            {session && session.status !== 'ACTIVE' && (
                <div className="glass-card" style={{ marginBottom: 16 }}>
                    <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--color-text)', marginBottom: 4 }}>
                        {session.status === 'EXPIRED' ? 'Доступ к бирже истёк' : 'Нужен доступ к бирже WB'}
                    </div>
                    <div style={{ fontSize: 13, color: 'var(--color-muted)', marginBottom: 12 }}>
                        У биржи нет публичного API WB — она работает на том же доступе к кабинету, что и «Поставки».
                        Если он уже настроен, возьмите его одной кнопкой (доступ скопируется в отдельный слот биржи).
                    </div>
                    <button className="btn btn-sm btn-primary" onClick={() => void takeFromSupply()} disabled={savingToken}>
                        {savingToken ? 'Проверка…' : 'Взять доступ из поставок'}
                    </button>

                    <div style={{ fontSize: 13, color: 'var(--color-muted)', margin: '20px 0 8px' }}>
                        Способ 2 — вручную. Откройте <b>seller.wildberries.ru</b>, нажмите <b>F12</b> → вкладка <b>Console</b>,
                        вставьте эту команду и нажмите Enter — доступ скопируется в буфер. Затем вставьте его в поле ниже.
                    </div>
                    <div style={{ position: 'relative', marginBottom: 10 }}>
                        <code style={{ display: 'block', background: 'var(--color-bg-hover)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '10px 12px', fontSize: 11, lineHeight: 1.5, color: 'var(--color-text)', overflowX: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                            {GRAB_SNIPPET}
                        </code>
                        <button
                            className="btn btn-sm btn-secondary"
                            style={{ marginTop: 8 }}
                            onClick={() => void navigator.clipboard.writeText(GRAB_SNIPPET)}
                        >
                            Скопировать команду
                        </button>
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
                        <input
                            value={tokenInput}
                            onChange={(e) => setTokenInput(e.target.value)}
                            placeholder="Вставьте сюда скопированный доступ"
                            style={{ flex: '1 1 320px', minWidth: 240, background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, color: 'var(--color-text)' }}
                        />
                        <button className="btn btn-sm btn-primary" onClick={() => void saveToken()} disabled={savingToken || !tokenInput.trim()}>
                            {savingToken ? 'Проверка…' : 'Сохранить доступ'}
                        </button>
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--color-muted)', marginTop: 10 }}>
                        Доступ биржи хранится отдельно от доступа поставок — его обновление не затрагивает поставки.
                        Одного токена бирже мало: WB требует ещё cookie, поэтому команда выше собирает всё сразу.
                    </div>
                    {tokenError && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 10 }}>{tokenError}</div>}
                </div>
            )}

            {!session && (
                <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-muted)', padding: 32 }}>Загрузка…</div>
            )}

            {sessionOk && (<>
            {/* Панель фильтров */}
            <div className="glass-card" style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center', marginBottom: 16 }}>
                <input
                    value={searchInput}
                    onChange={(e) => setSearchInput(e.target.value)}
                    placeholder="Название, артикул WB или продавца"
                    style={{ flex: '1 1 240px', minWidth: 220, background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, color: 'var(--color-text)' }}
                />
                <SearchSelect value={sort} onChange={setSort} options={SORT_OPTIONS} placeholder="Сортировка" allLabel="Больше отзывов" />
                <SearchSelect value={rootCategory} onChange={setRootCategory} options={categoryOptions} placeholder="Корневая категория" allLabel="Все категории" />
                <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text)' }}>
                    <Switch on={inStockOnly} onClick={() => setInStockOnly((v) => !v)} size="sm" ariaLabel="Только в наличии" />
                    В наличии
                </label>
            </div>

            {/* Режимы «наши товары» */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
                {OUR_MODES.map((m) => (
                    <button
                        key={m.value || 'all'}
                        className={`btn btn-sm ${ourMode === m.value ? 'btn-primary' : 'btn-secondary'}`}
                        title={m.hint}
                        onClick={() => setOurMode(m.value)}
                    >
                        {m.label}
                    </button>
                ))}
                <span style={{ marginLeft: 'auto', alignSelf: 'center', fontSize: 13, color: 'var(--color-muted)' }}>
                    {session?.supplier_id && <>Кабинет: {session.supplier_id} · </>}
                    В корзине: {formatNumber(cart.size, 0)}
                </span>
            </div>

            {unmatched.length > 0 && (
                <div className="glass-card" style={{ marginBottom: 12, fontSize: 12, color: 'var(--color-muted)' }}>
                    Предметов из справочника нет на бирже WB: {formatNumber(unmatched.length, 0)} — они пропущены в фильтре.
                </div>
            )}
            {scanNote && (
                <div className="glass-card" style={{ marginBottom: 12, fontSize: 12, color: 'var(--color-muted)' }}>{scanNote}</div>
            )}
            {actionError && (
                <div className="glass-card" style={{ marginBottom: 12, color: 'var(--color-danger)' }}>{actionError}</div>
            )}

            {loading && (
                <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-muted)', padding: 32 }}>Загрузка…</div>
            )}
            {error && !loading && (
                <div className="glass-card" style={{ color: 'var(--color-danger)' }}>
                    {error} <button className="btn btn-sm btn-secondary" onClick={() => void load(true)}>Повторить</button>
                </div>
            )}
            {!loading && !error && ads.length === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-muted)', padding: 48 }}>
                    Ничего не найдено. Измените фильтры или запрос.
                </div>
            )}

            {!loading && !error && ads.length > 0 && (
                <>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 16 }}>
                        {ads.map((ad) => {
                            const inCart = cart.has(ad.ad_id);
                            return (
                                <div key={ad.ad_id} className="glass-card" style={{ display: 'flex', flexDirection: 'column', padding: 12, gap: 8 }}>
                                    <div style={{ position: 'relative', aspectRatio: '3 / 4', background: 'var(--color-bg-hover)', borderRadius: 8, overflow: 'hidden' }}>
                                        {ad.photo
                                            // eslint-disable-next-line @next/next/no-img-element -- внешняя CDN-картинка биржи WB, как в WbThumb
                                            ? <img src={ad.photo} alt={ad.title ?? ''} loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                                            : <div style={{ width: '100%', height: '100%' }} />}
                                        {(ad.imt_count ?? 0) > 1 && (
                                            <span style={{ position: 'absolute', left: 8, bottom: 8, background: 'rgba(124,58,237,0.9)', color: '#fff', fontSize: 11, padding: '2px 8px', borderRadius: 6 }}>
                                                {formatNumber(ad.imt_count!, 0)} вариантов товара
                                            </span>
                                        )}
                                        {ad.is_ours && (
                                            <span style={{ position: 'absolute', right: 8, top: 8, background: '#10b981', color: '#fff', fontSize: 11, padding: '2px 8px', borderRadius: 6 }}>Наша</span>
                                        )}
                                    </div>
                                    <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--color-text)' }}>{money(ad.total_price)}</div>
                                    <div style={{ fontSize: 13, color: 'var(--color-text)', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                                        {[ad.brand, ad.title].filter(Boolean).join(' / ')}
                                    </div>
                                    <div style={{ fontSize: 12, color: 'var(--color-muted)' }}>
                                        ⭐ {Number(ad.rating).toFixed(1)} · {formatNumber(ad.feedbacks_count, 0)} отзывов
                                    </div>
                                    <div style={{ fontSize: 12, color: 'var(--color-muted)' }}>
                                        Остатки: {ad.stock_qty ? `${formatNumber(ad.stock_qty, 0)} шт` : 'нет'}
                                    </div>
                                    <div style={{ fontSize: 12, color: 'var(--color-muted)' }}>
                                        Поставщики: {ad.contact_countries?.length ? ad.contact_countries.join(', ') : 'не указаны'}
                                    </div>
                                    <button
                                        className={`btn btn-sm ${inCart ? 'btn-secondary' : 'btn-primary'}`}
                                        style={{ marginTop: 'auto' }}
                                        disabled={busyAd === ad.ad_id || ad.is_card_owner}
                                        onClick={() => void toggleCart(ad)}
                                        title={ad.is_card_owner ? 'Это ваше объявление' : undefined}
                                    >
                                        {ad.is_card_owner ? 'Ваше объявление' : busyAd === ad.ad_id ? '…' : inCart ? 'Удалить из корзины' : 'Добавить'}
                                    </button>
                                </div>
                            );
                        })}
                    </div>

                    {hasMore && (
                        <div style={{ textAlign: 'center', marginTop: 20 }}>
                            <button className="btn btn-secondary" onClick={() => void load(false)} disabled={loadingMore}>
                                {loadingMore ? 'Загрузка…' : 'Показать ещё'}
                            </button>
                        </div>
                    )}
                </>
            )}
            </>)}
        </PageGuard>
    );
}
