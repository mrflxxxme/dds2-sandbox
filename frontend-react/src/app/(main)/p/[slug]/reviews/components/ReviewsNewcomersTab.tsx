'use client';

import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import KpiCard from '@/components/KpiCard';
import type { Column } from '@/components/DataTable';
import type { ComplaintTerm, NewcomerGroup, NewcomerReview, NewcomersResponse, Review } from '@/types/api';

const DAYS_OPTIONS = [
    { key: 14, label: '2 недели' },
    { key: 30, label: 'Месяц' },
    { key: 60, label: '2 месяца' },
    { key: 90, label: 'Квартал' },
];
const MAX_RATING = 4.6;
const RATING_COLORS: Record<number, string> = { 1: '#ff3b30', 2: '#ff9f0a', 3: '#ffd60a', 4: '#7dd957', 5: '#34c759' };

type GroupMode = 'category' | 'brand' | 'tag';
type SubDim = 'category' | 'brand';

/** Встречная размерность для разворота: у бренда/ярлыка — предмет, у предмета — бренд. */
function subDimOf(mode: GroupMode): SubDim {
    return mode === 'category' ? 'brand' : 'category';
}
function subLabel(dim: SubDim): string {
    return dim === 'category' ? 'предмету' : 'бренду';
}

/** Строка встречного разреза внутри карточки. */
interface SubRow {
    name: string;
    products: number;
    avg: number | null;
}

/** Цвет средней оценки по значению (красный → жёлтый → зелёный). */
function avgColor(v: number | null): string {
    if (v == null) return 'var(--color-text-dim)';
    if (v >= 4.0) return '#7dd957';
    if (v >= 3.0) return '#ff9f0a';
    return '#ff3b30';
}

/** toggle-хелпер для массива-набора. */
function toggle<T>(arr: T[], v: T): T[] {
    return arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v];
}

/** Ссылка на карточку товара WB. */
function wbUrl(nmId: number): string {
    return `https://www.wildberries.ru/catalog/${nmId}/detail.aspx`;
}

/** Балл критичности: ниже рейтинг × больше отзывов × выше доля негатива. */
function severity(it: NewcomerReview): number {
    const rated = it.r1 + it.r2 + it.r3 + it.r4 + it.r5;
    const negShare = rated ? (it.r1 + it.r2) / rated : 0;
    const deficit = Math.max(0, MAX_RATING - (it.avg_rating ?? 5));
    return deficit * Math.log2(1 + it.count) * (1 + negShare);
}

/** Тир критичности по баллу: подпись + цвет. */
function sevTier(s: number): { label: string; color: string } {
    if (s >= 5) return { label: 'критично', color: 'var(--color-danger)' };
    if (s >= 2) return { label: 'внимание', color: 'var(--color-warning)' };
    return { label: 'следить', color: 'var(--color-text-dim)' };
}

/** Сколько 5★-отзывов нужно, чтобы дотянуть средний рейтинг до target. */
function needFiveStars(it: NewcomerReview, target = MAX_RATING): number {
    const rated = it.r1 + it.r2 + it.r3 + it.r4 + it.r5;
    if (!rated) return 0;
    const sum = it.r1 * 1 + it.r2 * 2 + it.r3 * 3 + it.r4 * 4 + it.r5 * 5;
    const k = (target * rated - sum) / (5 - target);
    return k > 0 ? Math.ceil(k) : 0;
}

/** Строка таблицы с производными полями критичности/цели. */
type NewcomerRow = NewcomerReview & { _sev: number; _need: number };

/** Средняя оценка группы товаров по суммарному распределению r1..r5 (rating>0). */
function subBreakdown(products: NewcomerReview[], dim: SubDim): SubRow[] {
    const map = new Map<string, { R: number[]; n: number }>();
    for (const p of products) {
        const key = dim === 'category' ? p.subject : p.brand;
        const acc = map.get(key) ?? { R: [0, 0, 0, 0, 0], n: 0 };
        acc.R[0] += p.r1; acc.R[1] += p.r2; acc.R[2] += p.r3; acc.R[3] += p.r4; acc.R[4] += p.r5;
        acc.n += 1;
        map.set(key, acc);
    }
    return [...map.entries()]
        .map(([name, { R, n }]) => {
            const rated = R[0] + R[1] + R[2] + R[3] + R[4];
            const avg = rated ? (R[0] + R[1] * 2 + R[2] * 3 + R[3] * 4 + R[4] * 5) / rated : null;
            return { name, products: n, avg };
        })
        .sort((a, b) => b.products - a.products);
}

/** Звёзды рейтинга. */
function Stars({ rating }: { rating: number }) {
    const r = Math.max(0, Math.min(5, rating));
    return (
        <span style={{ color: 'var(--color-warning)', letterSpacing: 1 }} title={`${r} / 5`}>
            {'★'.repeat(r)}<span style={{ color: 'var(--color-border)' }}>{'★'.repeat(5 - r)}</span>
        </span>
    );
}

/** Отзывы конкретного товара (ленивая подгрузка при раскрытии строки). Текст под каждым отзывом. */
function ProductReviews({ nmId }: { nmId: number }) {
    const [revs, setRevs] = useState<Review[] | null>(null);
    const [busy, setBusy] = useState(true);
    const [err, setErr] = useState('');

    useEffect(() => {
        let cancelled = false;
        setBusy(true); setErr('');
        api.getReviews({ nmId, take: 50 })
            .then(r => { if (!cancelled) setRevs(r.items); })
            .catch(e => { if (!cancelled) setErr(e instanceof Error ? e.message : 'Не удалось загрузить отзывы'); })
            .finally(() => { if (!cancelled) setBusy(false); });
        return () => { cancelled = true; };
    }, [nmId]);

    if (busy) return <div style={{ padding: 12, color: 'var(--color-text-dim)', fontSize: 13 }}>Загрузка отзывов…</div>;
    if (err) return <div style={{ padding: 12, color: 'var(--color-danger)', fontSize: 13 }}>{err}</div>;
    if (!revs || revs.length === 0) return <div style={{ padding: 12, color: 'var(--color-text-dim)', fontSize: 13 }}>Отзывов нет</div>;

    return (
        <div style={{ padding: '8px 12px 12px', display: 'flex', flexDirection: 'column', gap: 8 }}>
            {revs.map(r => (
                <div key={r.id} style={{ borderLeft: '3px solid var(--color-border)', paddingLeft: 10 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 13 }}>
                        {r.rating > 0 && <Stars rating={r.rating} />}
                        {r.user_name && <span style={{ fontWeight: 600 }}>{r.user_name}</span>}
                        {r.is_answered
                            ? <span className="badge badge-success">Отвечен</span>
                            : <span className="badge badge-warning">Без ответа</span>}
                        <span style={{ marginLeft: 'auto', color: 'var(--color-text-dim)' }}>{r.created_date ? formatDate(r.created_date) : ''}</span>
                    </div>
                    {r.text && <p style={{ margin: '4px 0 0', fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>{r.text}</p>}
                    {(r.pros || r.cons) && (
                        <div style={{ marginTop: 4, fontSize: 13, display: 'flex', flexDirection: 'column', gap: 2 }}>
                            {r.pros && <div><span style={{ color: 'var(--color-success)', fontWeight: 600 }}>+ </span>{r.pros}</div>}
                            {r.cons && <div><span style={{ color: 'var(--color-danger)', fontWeight: 600 }}>− </span>{r.cons}</div>}
                        </div>
                    )}
                    {!r.text && !r.pros && !r.cons && (
                        <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--color-text-dim)' }}>Без текста (только оценка)</p>
                    )}
                </div>
            ))}
        </div>
    );
}

/** Подсветка вхождений term в тексте (регистронезависимо). Через React-узлы <mark>,
 *  сырой HTML не вставляем — XSS-вектора нет. */
function Highlight({ text, term }: { text: string; term: string }) {
    if (!term || !text) return <>{text}</>;
    const lower = text.toLowerCase();
    const t = term.toLowerCase();
    const parts: ReactNode[] = [];
    let i = 0;
    let idx = lower.indexOf(t);
    let key = 0;
    while (idx !== -1) {
        if (idx > i) parts.push(text.slice(i, idx));
        parts.push(
            <mark key={key++} style={{ background: 'rgba(255,159,10,0.4)', padding: '0 2px', borderRadius: 3 }}>
                {text.slice(idx, idx + t.length)}
            </mark>,
        );
        i = idx + t.length;
        idx = lower.indexOf(t, i);
    }
    if (i < text.length) parts.push(text.slice(i));
    return <>{parts}</>;
}

/** Панель: негативные отзывы проблемных новинок, содержащие выбранное слово-жалобу. */
function TermReviews({ term, days }: { term: string; days: number }) {
    const [revs, setRevs] = useState<Review[] | null>(null);
    const [busy, setBusy] = useState(true);
    const [err, setErr] = useState('');

    useEffect(() => {
        let cancelled = false;
        setBusy(true); setErr('');
        api.getComplaintReviews(term, days, MAX_RATING)
            .then(r => { if (!cancelled) setRevs(r.items); })
            .catch(e => { if (!cancelled) setErr(e instanceof Error ? e.message : 'Не удалось загрузить отзывы'); })
            .finally(() => { if (!cancelled) setBusy(false); });
        return () => { cancelled = true; };
    }, [term, days]);

    if (busy) return <div style={{ padding: 12, color: 'var(--color-text-dim)', fontSize: 13 }}>Загрузка отзывов…</div>;
    if (err) return <div style={{ padding: 12, color: 'var(--color-danger)', fontSize: 13 }}>{err}</div>;
    if (!revs || revs.length === 0) return <div style={{ padding: 12, color: 'var(--color-text-dim)', fontSize: 13 }}>Отзывов со словом «{term}» не найдено</div>;

    return (
        <div style={{ marginTop: 12, borderTop: '1px solid var(--color-border)', paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 10, maxHeight: 480, overflowY: 'auto' }}>
            <div style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Отзывы со словом «<b style={{ color: 'var(--color-text)' }}>{term}</b>»: {formatNumber(revs.length, 0)}</div>
            {revs.map(r => (
                <div key={r.id} style={{ borderLeft: '3px solid var(--color-danger)', paddingLeft: 10 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 13 }}>
                        {r.rating > 0 && <Stars rating={r.rating} />}
                        <span style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 360 }} title={r.product_name || ''}>
                            {r.product_name || (r.nm_id ? `nmID ${r.nm_id}` : '')}
                        </span>
                        {r.nm_id != null && (
                            <a href={wbUrl(r.nm_id)} target="_blank" rel="noopener noreferrer" title="Открыть карточку на Wildberries" style={{ color: 'var(--color-accent)', textDecoration: 'none', fontSize: 12 }}>↗ WB</a>
                        )}
                        {!r.is_answered && <span className="badge badge-warning">Без ответа</span>}
                        <span style={{ marginLeft: 'auto', color: 'var(--color-text-dim)' }}>{r.created_date ? formatDate(r.created_date) : ''}</span>
                    </div>
                    {r.text && <p style={{ margin: '4px 0 0', fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}><Highlight text={r.text} term={term} /></p>}
                    {r.cons && <div style={{ marginTop: 4, fontSize: 13 }}><span style={{ color: 'var(--color-danger)', fontWeight: 600 }}>− </span><Highlight text={r.cons} term={term} /></div>}
                </div>
            ))}
        </div>
    );
}

/**
 * Карточка разреза. Клик по телу — добавить/убрать группу из фильтра (объединение).
 * Кнопка «по предмету/бренду» — развернуть встречный разрез; клик по строке — фильтр по (группа ∩ разрез).
 */
function GroupCard({ g, active, onToggle, expanded, onToggleExpand, subDim, subRows, selectedSubNames, onToggleSub }: {
    g: NewcomerGroup;
    active: boolean;
    onToggle: () => void;
    expanded: boolean;
    onToggleExpand: () => void;
    subDim: SubDim;
    subRows: SubRow[];
    selectedSubNames: string[];
    onToggleSub: (sub: string) => void;
}) {
    const color = avgColor(g.avg_rating);
    const counts: Record<number, number> = { 1: g.r1, 2: g.r2, 3: g.r3, 4: g.r4, 5: g.r5 };
    const rated = g.r1 + g.r2 + g.r3 + g.r4 + g.r5;

    return (
        <div
            className="glass-card"
            style={{
                padding: 16,
                borderTop: `3px solid ${color}`,
                outline: active ? '2px solid var(--color-accent)' : 'none',
                outlineOffset: active ? '-2px' : undefined,
            }}
        >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <div
                    onClick={onToggle}
                    role="button"
                    tabIndex={0}
                    onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); } }}
                    title={g.name}
                    style={{ flex: 1, minWidth: 0, fontWeight: 700, fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', cursor: 'pointer' }}
                >
                    {g.name}
                </div>
                <button
                    className="btn btn-secondary btn-sm"
                    onClick={onToggleExpand}
                    title={expanded ? 'Скрыть разрез' : `Разрез по ${subLabel(subDim)}`}
                    style={{ padding: '2px 8px', whiteSpace: 'nowrap' }}
                >
                    {expanded ? '▾' : '▸'} по {subLabel(subDim)}
                </button>
            </div>

            <div onClick={onToggle} style={{ cursor: 'pointer' }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginBottom: 2 }}>
                    <span style={{ fontSize: 26, fontWeight: 700, color }}>{g.avg_rating != null ? formatNumber(Number(g.avg_rating), 2) : '—'}</span>
                    <span style={{ color, fontSize: 15 }}>★</span>
                </div>
                <div style={{ color: 'var(--color-text-dim)', fontSize: 12, marginBottom: 12 }}>
                    Новинок: {formatNumber(g.products, 0)} · Отзывов: {formatNumber(g.count, 0)}
                    {selectedSubNames.length > 0 && <span style={{ color: 'var(--color-accent)' }}> · выбрано {formatNumber(selectedSubNames.length, 0)}</span>}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                    {[5, 4, 3, 2, 1].map(n => {
                        const cnt = counts[n];
                        const pct = rated ? (cnt / rated) * 100 : 0;
                        return (
                            <div key={n} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                                <span style={{ width: 10, color: 'var(--color-text-dim)' }}>{n}</span>
                                <div style={{ flex: 1, height: 6, background: 'var(--color-border)', borderRadius: 3, overflow: 'hidden' }}>
                                    <div style={{ width: `${pct}%`, height: '100%', background: RATING_COLORS[n], borderRadius: 3 }} />
                                </div>
                                <span style={{ width: 54, textAlign: 'right' }}>{formatNumber(cnt, 0)}</span>
                                <span style={{ width: 44, textAlign: 'right', color: 'var(--color-text-dim)' }}>{formatNumber(pct, 1)}%</span>
                            </div>
                        );
                    })}
                </div>
            </div>

            {expanded && (
                <div style={{ marginTop: 12, borderTop: '1px solid var(--color-border)', paddingTop: 8, maxHeight: 240, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 2 }}>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 2 }}>По {subLabel(subDim)}:</div>
                    {subRows.length === 0 ? (
                        <div style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>Нет данных</div>
                    ) : subRows.map(s => {
                        const sel = selectedSubNames.includes(s.name);
                        return (
                            <div
                                key={s.name}
                                onClick={() => onToggleSub(s.name)}
                                role="button"
                                tabIndex={0}
                                onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggleSub(s.name); } }}
                                style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8, padding: '4px 6px', borderRadius: 8, fontSize: 12, background: sel ? 'var(--color-border)' : 'transparent' }}
                            >
                                <input type="checkbox" checked={sel} readOnly style={{ cursor: 'pointer' }} />
                                <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={s.name}>{s.name}</span>
                                <span style={{ color: avgColor(s.avg), fontWeight: 600 }}>{s.avg != null ? `${formatNumber(s.avg, 2)}★` : '—'}</span>
                                <span style={{ color: 'var(--color-text-dim)', width: 62, textAlign: 'right' }}>{formatNumber(s.products, 0)} нов.</span>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

export default function ReviewsNewcomersTab() {
    const [data, setData] = useState<NewcomersResponse | null>(null);
    const [days, setDays] = useState(30);
    const [groupMode, setGroupMode] = useState<GroupMode>('category');
    const [selectedGroups, setSelectedGroups] = useState<string[]>([]); // выбранные группы (объединение)
    const [selectedSubs, setSelectedSubs] = useState<{ group: string; sub: string }[]>([]); // разрезы (группа ∩ встречная)
    const [expanded, setExpanded] = useState<string[]>([]); // развёрнутые карточки
    const [selectedTerm, setSelectedTerm] = useState<string | null>(null); // выбранная тема жалоб
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const resetFilters = useCallback(() => { setSelectedGroups([]); setSelectedSubs([]); setExpanded([]); }, []);
    const changeGroupMode = useCallback((m: GroupMode) => { setGroupMode(m); resetFilters(); }, [resetFilters]);

    const load = useCallback(async (d: number) => {
        setLoading(true);
        setError('');
        try {
            setData(await api.getReviewsNewcomers(d, MAX_RATING));
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить новинки');
            setData(null);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(days); }, [days, load]);

    const items = data?.items ?? [];
    const groups: NewcomerGroup[] = groupMode === 'category'
        ? (data?.by_category ?? [])
        : groupMode === 'brand'
            ? (data?.by_brand ?? [])
            : (data?.by_tag ?? []);

    const subDim = subDimOf(groupMode);

    const matchesGroup = useCallback((it: NewcomerReview, name: string): boolean => {
        if (groupMode === 'category') return it.subject === name;
        if (groupMode === 'brand') return it.brand === name;
        return name === 'Без ярлыка' ? it.tags.length === 0 : it.tags.includes(name);
    }, [groupMode]);

    const matchesSub = useCallback((it: NewcomerReview, sub: string): boolean =>
        subDim === 'category' ? it.subject === sub : it.brand === sub, [subDim]);

    // Товары группы (для встречного разреза в развороте)
    const productsOf = useCallback((name: string): NewcomerReview[] =>
        items.filter(it => matchesGroup(it, name)), [items, matchesGroup]);

    // Список = объединение выбранных групп и выбранных разрезов (группа ∩ встречная). Пусто → все.
    const hasFilter = selectedGroups.length > 0 || selectedSubs.length > 0;
    const shown = !hasFilter ? items : items.filter(it =>
        selectedGroups.some(g => matchesGroup(it, g)) ||
        selectedSubs.some(s => matchesGroup(it, s.group) && matchesSub(it, s.sub))
    );
    // Производные поля + сортировка по критичности (худшие сверху)
    const rows: NewcomerRow[] = useMemo(
        () => shown
            .map(it => ({ ...it, _sev: severity(it), _need: needFiveStars(it) }))
            .sort((a, b) => b._sev - a._sev),
        [shown],
    );

    // KPI по всему набору (не по фильтру)
    const total = data?.total_newcomers ?? 0;
    const problem = items.length;
    const pctProblem = total ? (problem / total) * 100 : 0;
    const critical = items.filter(it => (it.avg_rating ?? 5) < 3.5).length;
    const negUnanswered = items.reduce((a, it) => a + it.neg_unanswered, 0);
    const terms: ComplaintTerm[] = data?.complaint_terms ?? [];
    const maxTerm = terms.length ? terms[0].count : 1;

    const columns: Column[] = useMemo(() => [
        {
            key: 'name', label: 'Товар',
            render: (v: string, row: NewcomerRow) => (
                <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 260 }} title={v}>{v}</div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                        nmID {row.nm_id}
                        <a
                            href={wbUrl(row.nm_id)}
                            target="_blank"
                            rel="noopener noreferrer"
                            title="Открыть карточку на Wildberries"
                            style={{ marginLeft: 6, color: 'var(--color-accent)', textDecoration: 'none' }}
                        >
                            ↗ WB
                        </a>
                    </div>
                </div>
            ),
        },
        {
            key: '_sev', label: 'Критичность',
            render: (v: number, row: NewcomerRow) => {
                const t = sevTier(row._sev);
                return (
                    <span style={{ whiteSpace: 'nowrap' }}>
                        <span style={{ display: 'inline-block', padding: '1px 8px', borderRadius: 24, fontSize: 11, fontWeight: 600, color: '#fff', background: t.color }}>
                            {t.label}
                        </span>
                        <span style={{ marginLeft: 6, color: 'var(--color-text-dim)', fontSize: 12 }}>{formatNumber(row._sev, 1)}</span>
                    </span>
                );
            },
        },
        { key: 'brand', label: 'Бренд' },
        { key: 'subject', label: 'Предмет' },
        {
            key: 'first_date', label: 'Старт продаж',
            render: (v: string, row: NewcomerRow) => (
                <span style={{ fontSize: 13, whiteSpace: 'nowrap' }}>
                    {formatDate(v)}
                    <span style={{ color: 'var(--color-text-dim)' }}> · {formatNumber(row.days_on_sale, 0)} дн</span>
                    {row.date_source === 'review' && (
                        <span
                            title="Дата первой продажи неизвестна — показана дата первого отзыва (приблизительно)"
                            style={{ marginLeft: 6, fontSize: 11, color: 'var(--color-warning)', cursor: 'help' }}
                        >
                            ≈ по отзыву
                        </span>
                    )}
                </span>
            ),
        },
        {
            key: 'avg_rating', label: 'Рейтинг',
            render: (v: number | null) => (
                <span style={{ fontWeight: 700, color: avgColor(v) }}>{v != null ? `${formatNumber(Number(v), 2)} ★` : '—'}</span>
            ),
        },
        { key: 'count', label: 'Отзывов', format: 'number' as const },
        {
            key: 'count_unanswered', label: 'Без ответа',
            render: (v: number, row: NewcomerRow) => (
                <span style={{ whiteSpace: 'nowrap' }}>
                    <span style={v > 0 ? { color: 'var(--color-warning)', fontWeight: 600 } : undefined}>{formatNumber(v, 0)}</span>
                    {row.neg_unanswered > 0 && (
                        <span
                            title="Негативные отзывы (1–2★) без ответа — стоит ответить в первую очередь"
                            style={{ marginLeft: 6, fontSize: 11, color: 'var(--color-danger)', fontWeight: 700, cursor: 'help' }}
                        >
                            🔴 {formatNumber(row.neg_unanswered, 0)} негатив
                        </span>
                    )}
                </span>
            ),
        },
        {
            key: 'r1', label: '★1–2',
            render: (_v: number, row: NewcomerRow) => (
                <span style={{ color: 'var(--color-danger)', fontWeight: 600 }}>{formatNumber(row.r1 + row.r2, 0)}</span>
            ),
        },
        {
            key: '_need', label: 'До 4.6★',
            render: (v: number) => (
                v > 0
                    ? <span title={`Нужно ещё ~${v} отзывов по 5★, чтобы поднять средний рейтинг до ${formatNumber(MAX_RATING, 1)}`} style={{ color: 'var(--color-accent)', fontWeight: 600, cursor: 'help', whiteSpace: 'nowrap' }}>+{formatNumber(v, 0)} × 5★</span>
                    : <span style={{ color: 'var(--color-text-dim)' }}>—</span>
            ),
        },
    ], []);

    return (
        <div>
            <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>На продаже:</span>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                    {DAYS_OPTIONS.map(o => (
                        <button
                            key={o.key}
                            className={`btn btn-sm ${days === o.key ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => { setDays(o.key); resetFilters(); setSelectedTerm(null); }}
                            disabled={loading}
                        >
                            {o.label}
                        </button>
                    ))}
                </div>
                <span style={{ fontSize: 13, color: 'var(--color-text-dim)', marginLeft: 'auto' }}>
                    Рейтинг ниже {formatNumber(MAX_RATING, 1)} ★
                </span>
            </div>

            <div style={{ fontSize: 13, color: 'var(--color-text-dim)', marginBottom: 16 }}>
                Новинки (на продаже меньше выбранного срока) с рейтингом ниже порога — ранний сигнал, что товар собирает плохие отзывы.
                «Старт продаж» — по дате первой продажи, а если она неизвестна — по дате первого отзыва.
            </div>

            {error && (
                <div className="glass-card" style={{ marginBottom: 20, color: 'var(--color-danger)' }}>
                    {error}{' '}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={() => load(days)}>Повторить</button>
                </div>
            )}

            {loading && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48, color: 'var(--color-text-dim)' }}>
                    Загрузка новинок…
                </div>
            )}

            {!loading && !error && data && !data.has_key && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>🔑</div>
                    <h3 style={{ margin: '0 0 8px' }}>WB-ключ не настроен</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>
                        Добавьте API-ключ Wildberries со scope «Вопросы и отзывы» в разделе
                        «Настройка проекта» → Интеграции, затем обновите отзывы.
                    </p>
                </div>
            )}

            {!loading && !error && data && data.has_key && items.length === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>✅</div>
                    <h3 style={{ margin: '0 0 8px' }}>Проблемных новинок нет</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>
                        Среди новинок за выбранный срок нет товаров с рейтингом ниже {formatNumber(MAX_RATING, 1)} ★.
                    </p>
                </div>
            )}

            {!loading && !error && data && data.has_key && items.length > 0 && (
                <>
                    {/* KPI: масштаб проблемы */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 16 }}>
                        <KpiCard label="Новинок всего" value={formatNumber(total, 0)} />
                        <KpiCard label="Проблемных" value={formatNumber(problem, 0)} />
                        <KpiCard label="Доля проблемных" value={`${formatNumber(pctProblem, 0)}%`} />
                        <KpiCard label="Критичных (<3.5★)" value={formatNumber(critical, 0)} />
                        <KpiCard label="Негатив без ответа" value={formatNumber(negUnanswered, 0)} />
                    </div>

                    {/* Частые темы жалоб — из негативных отзывов проблемных новинок */}
                    {terms.length > 0 && (
                        <div className="glass-card" style={{ padding: 16, marginBottom: 20 }}>
                            <h3 style={{ margin: '0 0 4px', fontSize: 16 }}>Частые темы жалоб</h3>
                            <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 10 }}>
                                Слова из негативных отзывов (1–2★) этих новинок — на что чаще всего жалуются. Нажмите слово, чтобы увидеть отзывы с ним.
                            </div>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                                {terms.map(t => {
                                    const w = t.count / maxTerm; // 0..1
                                    const sel = selectedTerm === t.term;
                                    return (
                                        <span
                                            key={t.term}
                                            onClick={() => setSelectedTerm(prev => prev === t.term ? null : t.term)}
                                            role="button"
                                            tabIndex={0}
                                            onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelectedTerm(prev => prev === t.term ? null : t.term); } }}
                                            title={`${t.count} упоминаний — нажмите, чтобы прочитать эти отзывы`}
                                            style={{
                                                display: 'inline-flex', alignItems: 'baseline', gap: 4,
                                                padding: '4px 10px', borderRadius: 24, cursor: 'pointer',
                                                background: `rgba(255, 59, 48, ${0.08 + w * 0.22})`,
                                                color: 'var(--color-text)',
                                                fontSize: 12 + Math.round(w * 6), fontWeight: 500,
                                                outline: sel ? '2px solid var(--color-accent)' : 'none', outlineOffset: sel ? '1px' : undefined,
                                            }}
                                        >
                                            {t.term}
                                            <span style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>{formatNumber(t.count, 0)}</span>
                                        </span>
                                    );
                                })}
                            </div>
                            {selectedTerm && <TermReviews term={selectedTerm} days={days} />}
                        </div>
                    )}

                    {/* Распределение проблемных новинок по разрезам (над таблицей — сразу видно) */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '0 0 12px', flexWrap: 'wrap' }}>
                        <h3 style={{ margin: 0, fontSize: 16 }}>Распределение</h3>
                        <div style={{ display: 'flex', gap: 4 }}>
                            <button className={`btn btn-sm ${groupMode === 'category' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => changeGroupMode('category')}>По предмету</button>
                            <button className={`btn btn-sm ${groupMode === 'brand' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => changeGroupMode('brand')}>По бренду</button>
                            <button className={`btn btn-sm ${groupMode === 'tag' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => changeGroupMode('tag')}>По ярлыку</button>
                        </div>
                        <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                            кликайте карточки, чтобы плюсовать группы; «по {subLabel(subDim)}» — разрез внутри группы
                        </span>
                    </div>
                    {groups.length === 0 ? (
                        <div className="glass-card" style={{ padding: 20, color: 'var(--color-text-dim)', fontSize: 14, marginBottom: 24 }}>Нет данных</div>
                    ) : (
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16, marginBottom: 24 }}>
                            {groups.map(g => (
                                <GroupCard
                                    key={g.name}
                                    g={g}
                                    active={selectedGroups.includes(g.name)}
                                    onToggle={() => setSelectedGroups(prev => toggle(prev, g.name))}
                                    expanded={expanded.includes(g.name)}
                                    onToggleExpand={() => setExpanded(prev => toggle(prev, g.name))}
                                    subDim={subDim}
                                    subRows={expanded.includes(g.name) ? subBreakdown(productsOf(g.name), subDim) : []}
                                    selectedSubNames={selectedSubs.filter(s => s.group === g.name).map(s => s.sub)}
                                    onToggleSub={(sub) => setSelectedSubs(prev => {
                                        const exists = prev.some(s => s.group === g.name && s.sub === sub);
                                        return exists ? prev.filter(s => !(s.group === g.name && s.sub === sub)) : [...prev, { group: g.name, sub }];
                                    })}
                                />
                            ))}
                        </div>
                    )}

                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, margin: '0 0 12px', flexWrap: 'wrap' }}>
                        <h3 style={{ margin: 0, fontSize: 16 }}>Список новинок</h3>
                        {hasFilter && (
                            <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>
                                {selectedGroups.length > 0 && <>группы: <b style={{ color: 'var(--color-text)' }}>{selectedGroups.join(', ')}</b>{selectedSubs.length > 0 ? ' · ' : ' '}</>}
                                {selectedSubs.length > 0 && <>разрезы: <b style={{ color: 'var(--color-text)' }}>{selectedSubs.map(s => `${s.group} › ${s.sub}`).join(', ')}</b> </>}
                                · {formatNumber(shown.length, 0)} из {formatNumber(items.length, 0)}
                                <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={resetFilters}>Сбросить</button>
                            </span>
                        )}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 6 }}>
                        Отсортировано по критичности. Нажмите ▸ слева от товара, чтобы прочитать его отзывы.
                    </div>
                    <TanStackDataTable
                        columns={columns}
                        data={rows}
                        exportName="problem_newcomers"
                        enableSorting
                        enablePagination={rows.length > 50}
                        getRowId={(row: NewcomerRow) => String(row.nm_id)}
                        renderSubRow={(row: NewcomerRow) => <ProductReviews nmId={row.nm_id} />}
                    />
                </>
            )}
        </div>
    );
}
