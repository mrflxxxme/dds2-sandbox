'use client';
import { useState, useEffect, useCallback, useMemo, Fragment } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { parseBoxSize, boxesPerPallet, canonBoxSize, DEFAULT_MAX_PALLET_HEIGHT_CM } from '@/lib/utils/boxPallet';
import type { BoxMultiplicityRow } from '@/types/api';

/** Одна строка справочника «размер коробки → коробок на паллету». */
interface SizeRow {
    canon: string;            // канонический ключ (60x40x50)
    display: string;          // как занесено (первое встреченное написание)
    skuCount: number;         // на скольких SKU встречается этот размер (после фильтров)
    computed: number | null;  // расчётное кор/пал (геометрия @ 1.8 м), null — нет габаритов
    skus: Array<{ nm_id: number; vendor: string }>; // товары с этим размером (после фильтров)
}

export default function PalletSizesPage() {
    const { slug } = useParams() as { slug: string };
    const [items, setItems] = useState<BoxMultiplicityRow[]>([]);
    const [edit, setEdit] = useState<Record<string, string>>({}); // canon → введённое значение
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);
    const [msg, setMsg] = useState('');
    const [hasChanges, setHasChanges] = useState(false);
    const [expanded, setExpanded] = useState<Set<string>>(new Set()); // canon → раскрыт список товаров
    // Фильтры
    const [search, setSearch] = useState('');
    const [brandFilter, setBrandFilter] = useState('');
    const [subjectFilter, setSubjectFilter] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            // Размеры — обязательны; override — best-effort (его сбой не должен ронять страницу).
            const bm = await api.getBoxMultiplicity();
            setItems(bm.items);
            const ov = await api.getPalletBoxesBySize().catch(() => ({} as Record<string, number>));
            const e: Record<string, string> = {};
            for (const [k, val] of Object.entries(ov)) e[k] = String(val);
            setEdit(e);
            setHasChanges(false);
        } catch {
            setError('Не удалось загрузить размеры коробок');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const brandOptions = useMemo(
        () => Array.from(new Set(items.map(i => i.brand).filter(Boolean) as string[])).sort((a, b) => a.localeCompare(b)),
        [items],
    );
    const subjectOptions = useMemo(
        () => Array.from(new Set(items.map(i => i.subject).filter(Boolean) as string[])).sort((a, b) => a.localeCompare(b)),
        [items],
    );

    // Размеры из системы, сгруппированные по канону, с учётом фильтров (бренд/
    // категория/поиск действуют на ТОВАРЫ — размер показываем, если есть хоть
    // один подходящий SKU).
    const rows = useMemo<SizeRow[]>(() => {
        const q = search.trim().toLowerCase();
        const byCanon = new Map<string, { display: string; skus: Map<number, string> }>();
        for (const item of items) {
            if (brandFilter && item.brand !== brandFilter) continue;
            if (subjectFilter && item.subject !== subjectFilter) continue;
            if (q) {
                const hay = `${item.vendor_code || ''} ${item.barcode} ${item.nm_id}`.toLowerCase();
                if (!hay.includes(q)) continue;
            }
            for (const pw of item.per_warehouse) {
                if (!pw.box_size || !pw.box_size.trim()) continue;
                const c = canonBoxSize(pw.box_size);
                if (!c) continue;
                const e = byCanon.get(c) ?? { display: pw.box_size.trim(), skus: new Map<number, string>() };
                if (!e.skus.has(item.nm_id)) e.skus.set(item.nm_id, item.vendor_code || String(item.nm_id));
                byCanon.set(c, e);
            }
        }
        return Array.from(byCanon.entries())
            .map(([canon, v]) => {
                const dims = parseBoxSize(v.display);
                return {
                    canon,
                    display: v.display,
                    skuCount: v.skus.size,
                    computed: dims ? boxesPerPallet(dims, DEFAULT_MAX_PALLET_HEIGHT_CM) : null,
                    skus: Array.from(v.skus.entries()).map(([nm_id, vendor]) => ({ nm_id, vendor })),
                };
            })
            .sort((a, b) => b.skuCount - a.skuCount || a.canon.localeCompare(b.canon));
    }, [items, search, brandFilter, subjectFilter]);

    const setOverride = (canon: string, val: string) => {
        setEdit(p => ({ ...p, [canon]: val.replace(/[^0-9]/g, '') }));
        setHasChanges(true);
    };

    const save = async () => {
        setSaving(true);
        setMsg('');
        try {
            const sizes: Record<string, number> = {};
            for (const [canon, val] of Object.entries(edit)) {
                const n = parseInt(val, 10);
                if (Number.isFinite(n) && n > 0) sizes[canon] = n;
            }
            const { sizes: saved } = await api.setPalletBoxesBySize(sizes);
            const e: Record<string, string> = {};
            for (const [k, v] of Object.entries(saved)) e[k] = String(v);
            setEdit(e);
            setHasChanges(false);
            setMsg('✅ Сохранено');
        } catch {
            setMsg('❌ Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    const overrideCount = Object.values(edit).filter(v => parseInt(v, 10) > 0).length;
    const hasFilters = Boolean(search || brandFilter || subjectFilter);

    return (
        <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
            <Link href={`/p/${slug}/warehouse/box-multiplicity`} style={{ fontSize: 13, color: 'var(--color-accent)' }}>
                ← Кратность коробок
            </Link>
            <h1 className="page-title" style={{ margin: '8px 0 4px' }}>📐 Паллеты по размерам коробок</h1>
            <p style={{ color: 'var(--color-text-muted)', margin: '0 0 16px', fontSize: 14 }}>
                Все размеры коробок из системы. Для каждого — расчётное число коробок на евро-паллету (120×80, высота{' '}
                {DEFAULT_MAX_PALLET_HEIGHT_CM / 100} м). Можно задать <strong>своё</strong> «коробок на паллету» — оно
                перебивает расчёт и используется в распределении (паллетный режим «Потребность по складам»).
            </p>

            {loading ? (
                <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Загрузка размеров…
                </div>
            ) : error ? (
                <div className="glass-card" style={{ padding: 24, color: 'var(--color-danger)' }}>
                    {error}{' '}
                    <button className="btn btn-secondary btn-sm" onClick={load} style={{ marginLeft: 8 }}>Повторить</button>
                </div>
            ) : items.length === 0 ? (
                <div className="empty-state">
                    <div className="empty-state-icon">📐</div>
                    <div>В системе нет товаров с заданным размером коробки.</div>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 6 }}>
                        Задайте размеры на странице «Кратность коробок» (колонка «Размер»).
                    </div>
                </div>
            ) : (
                <>
                    {/* Фильтры: бренд + категория + поиск по артикулу */}
                    <div className="glass-card" style={{ padding: 12, marginBottom: 12, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                        <select
                            value={brandFilter}
                            onChange={e => setBrandFilter(e.target.value)}
                            style={{ padding: '6px 10px', fontSize: 13, border: '1px solid var(--color-border)', borderRadius: 8, minWidth: 160 }}
                        >
                            <option value="">Все бренды ({brandOptions.length})</option>
                            {brandOptions.map(b => <option key={b} value={b}>{b}</option>)}
                        </select>
                        <select
                            value={subjectFilter}
                            onChange={e => setSubjectFilter(e.target.value)}
                            style={{ padding: '6px 10px', fontSize: 13, border: '1px solid var(--color-border)', borderRadius: 8, minWidth: 160 }}
                        >
                            <option value="">Все категории ({subjectOptions.length})</option>
                            {subjectOptions.map(s => <option key={s} value={s}>{s}</option>)}
                        </select>
                        <input
                            type="text"
                            placeholder="Поиск по артикулу / barcode / nm_id"
                            value={search}
                            onChange={e => setSearch(e.target.value)}
                            style={{ padding: '6px 10px', fontSize: 13, border: '1px solid var(--color-border)', borderRadius: 8, flex: 1, minWidth: 200 }}
                        />
                        {hasFilters && (
                            <button
                                className="btn btn-secondary btn-sm"
                                onClick={() => { setBrandFilter(''); setSubjectFilter(''); setSearch(''); }}
                            >✕ Сброс</button>
                        )}
                    </div>

                    <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
                        <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                            Размеров: <strong>{formatNumber(rows.length, 0)}</strong>
                        </span>
                        {overrideCount > 0 && (
                            <span style={{ fontSize: 13, color: 'var(--color-accent)', fontWeight: 500 }}>
                                ✏️ Переопределено: {formatNumber(overrideCount, 0)}
                            </span>
                        )}
                        <span style={{ flex: 1 }} />
                        <button className="btn btn-primary" onClick={save} disabled={saving || !hasChanges}>
                            {saving ? 'Сохранение…' : '💾 Сохранить'}
                        </button>
                        {msg && <span style={{ fontSize: 14 }}>{msg}</span>}
                    </div>

                    {rows.length === 0 ? (
                        <div className="empty-state">
                            <div className="empty-state-icon">{hasFilters ? '🔍' : '📐'}</div>
                            <div>{hasFilters ? 'Под текущие фильтры размеров не нашлось.' : 'В системе нет товаров с заданным размером коробки.'}</div>
                        </div>
                    ) : (
                        <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                                <thead>
                                    <tr style={{ color: 'var(--color-text-muted)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.05, textAlign: 'left' }}>
                                        <th style={{ padding: '10px 14px' }}>Размер коробки</th>
                                        <th style={{ padding: '10px 14px', textAlign: 'right' }}>SKU</th>
                                        <th style={{ padding: '10px 14px', textAlign: 'right' }}>Расчёт (≈{DEFAULT_MAX_PALLET_HEIGHT_CM / 100} м)</th>
                                        <th style={{ padding: '10px 14px', textAlign: 'right' }}>Кор/пал (своё)</th>
                                        <th style={{ padding: '10px 14px', textAlign: 'right' }}>Эффективно</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {rows.map(r => {
                                        const raw = edit[r.canon] ?? '';
                                        const ov = parseInt(raw, 10);
                                        const hasOv = Number.isFinite(ov) && ov > 0;
                                        const effective = hasOv ? ov : r.computed;
                                        const isOpen = expanded.has(r.canon);
                                        const toggle = () => setExpanded(prev => {
                                            const next = new Set(prev);
                                            if (next.has(r.canon)) next.delete(r.canon); else next.add(r.canon);
                                            return next;
                                        });
                                        return (
                                            <Fragment key={r.canon}>
                                                <tr style={{ borderTop: '1px solid var(--color-border)' }}>
                                                    <td style={{ padding: '8px 14px', fontFamily: 'monospace', fontWeight: 500 }} title={r.display !== r.canon ? `как занесено: ${r.display}` : undefined}>
                                                        {r.canon}
                                                    </td>
                                                    <td style={{ padding: '8px 14px', textAlign: 'right' }}>
                                                        <button
                                                            type="button"
                                                            onClick={toggle}
                                                            title="Показать товары с этим размером"
                                                            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: 'var(--color-accent)', fontSize: 14 }}
                                                        >
                                                            {isOpen ? '▾ ' : '▸ '}{formatNumber(r.skuCount, 0)}
                                                        </button>
                                                    </td>
                                                    <td style={{ padding: '8px 14px', textAlign: 'right' }}>
                                                        {r.computed !== null ? (
                                                            <span>{formatNumber(r.computed, 0)} <span style={{ color: 'var(--color-text-muted)' }}>кор</span></span>
                                                        ) : (
                                                            <span style={{ color: 'var(--color-warning)' }} title="Габариты не парсятся или коробка крупногабаритная">—</span>
                                                        )}
                                                    </td>
                                                    <td style={{ padding: '8px 14px', textAlign: 'right' }}>
                                                        <input
                                                            type="text"
                                                            inputMode="numeric"
                                                            value={raw}
                                                            onChange={e => setOverride(r.canon, e.target.value)}
                                                            placeholder={r.computed !== null ? String(r.computed) : '—'}
                                                            style={{
                                                                width: 70, padding: '4px 8px', fontSize: 13, textAlign: 'right',
                                                                border: `1px solid ${hasOv ? 'var(--color-accent)' : 'var(--color-border)'}`,
                                                                borderRadius: 6, background: 'var(--color-bg-card)', color: 'var(--color-text)',
                                                            }}
                                                        />
                                                    </td>
                                                    <td style={{ padding: '8px 14px', textAlign: 'right', fontWeight: 600, color: hasOv ? 'var(--color-accent)' : undefined }}>
                                                        {effective !== null ? `${formatNumber(effective, 0)} кор` : <span style={{ color: 'var(--color-text-dim)' }}>—</span>}
                                                    </td>
                                                </tr>
                                                {isOpen && (
                                                    <tr>
                                                        <td colSpan={5} style={{ padding: '0 14px 12px 14px', background: 'var(--color-bg)' }}>
                                                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                                                                {r.skus.slice(0, 60).map(s => (
                                                                    <span
                                                                        key={s.nm_id}
                                                                        title={`nm_id ${s.nm_id}`}
                                                                        style={{ padding: '2px 8px', borderRadius: 6, border: '1px solid var(--color-border)', background: 'var(--color-bg-card)', fontFamily: 'monospace' }}
                                                                    >
                                                                        {s.vendor}
                                                                    </span>
                                                                ))}
                                                                {r.skus.length > 60 && <span>…ещё {formatNumber(r.skus.length - 60, 0)}</span>}
                                                            </div>
                                                        </td>
                                                    </tr>
                                                )}
                                            </Fragment>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}

                    <p style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 12 }}>
                        Своё значение применяется ко всем складам (без учёта высоты). Пустое поле — используется расчёт по
                        геометрии (он зависит от высоты конкретного склада, поэтому здесь показан для типовых{' '}
                        {DEFAULT_MAX_PALLET_HEIGHT_CM / 100} м).
                    </p>
                </>
            )}
        </div>
    );
}
