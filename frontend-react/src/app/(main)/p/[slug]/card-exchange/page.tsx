'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import PageHeader from '@/components/PageHeader';
import PageGuard from '@/components/PageGuard';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type {
    CardExchangeOurMode,
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
        api.getCardExchangeCategories()
            .then(setCategories)
            .catch(() => { /* справочник не критичен для витрины */ });
    }, []);

    const categoryOptions = useMemo(
        () => categories.map((c) => ({ value: c.category, label: `${c.category} (${c.subject_count})` })),
        [categories],
    );

    const load = useCallback(async (reset: boolean) => {
        const [sortField, sortOrder] = sort.split(':');
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
            setAds((prev) => (reset ? res.ads : [...prev, ...res.ads]));
            setCursor(res.next_cursor);
            setHasMore(res.has_more);
            setUnmatched(res.unmatched_subjects || []);
            setScanNote(
                res.scanned_pages != null
                    ? `Просканировано страниц: ${res.scanned_pages}${res.scan_truncated ? ' (упёрлись в лимит — показаны не все)' : ''}`
                    : null,
            );
            if (reset) {
                // синхронизируем локальную корзину с состоянием карточек
                setCart(new Set(res.ads.filter((a) => a.has_in_cart).map((a) => a.ad_id)));
            }
        } catch (e) {
            if (reset) setError(e instanceof Error ? e.message : 'Не удалось загрузить биржу');
            else setActionError(e instanceof Error ? e.message : 'Не удалось догрузить');
        } finally {
            if (reset) setLoading(false); else setLoadingMore(false);
        }
    }, [search, rootCategory, ourMode, inStockOnly, sort, cursor]);

    // перезагрузка страницы 1 при смене любого фильтра
    useEffect(() => {
        void load(true);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [search, rootCategory, ourMode, inStockOnly, sort]);

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
        </PageGuard>
    );
}
