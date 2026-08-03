'use client';

/**
 * Модалка «Распределение по категориям»: выбор эффективных категорий (override ??
 * предмет WB) + опционально один ФФ-источник → создаётся ОТДЕЛЬНЫЙ черновик со
 * скоупом (`distribution.category_scope` / `scope_ff_id`). Раскладку среза считает
 * авто-синк «Ручной раскладки»; содержимое нового черновика резервирует сток от
 * других черновиков (эндпоинт /assembly/drafts/reserved).
 */
import { useMemo, useState } from 'react';
import { formatNumber } from '@/lib/utils';
import type { StockNeedArticle, Warehouse } from '@/types/api';

interface Props {
    /** Статьи потребности (уже с вычтенным резервом других черновиков). */
    articles: Pick<StockNeedArticle, 'nm_id' | 'total_need' | 'rf_stocks'>[];
    /** Эффективная категория per nm (override ?? subject ?? «Без категории»). */
    categoryOf: (nm: number) => string;
    warehouses: Warehouse[];
    /** Категории, уже занятые другими категорийными черновиками (дизейбл + подпись). */
    existingScopes: string[];
    onCreate: (categories: string[], ffId: number | null) => Promise<void> | void;
    onClose: () => void;
}

export default function CategoryDraftModal({ articles, categoryOf, warehouses, existingScopes, onCreate, onClose }: Props) {
    const [query, setQuery] = useState('');
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [ffId, setFfId] = useState<number | ''>('');
    const [busy, setBusy] = useState(false);

    const taken = useMemo(() => new Set(existingScopes.map(s => s.trim()).filter(Boolean)), [existingScopes]);

    // Категории с суммарной потребностью и свободным остатком ФФ (для выбора
    // «что выделять») + разбивка свободного ПО СКЛАДАМ (byFf) — видно, где
    // категория реально лежит, и как ляжет выбор «Только <склад>».
    const categories = useMemo(() => {
        const agg = new Map<string, { need: number; free: number; byFf: Map<number, number> }>();
        for (const a of articles) {
            const cat = categoryOf(a.nm_id);
            const e = agg.get(cat) ?? { need: 0, free: 0, byFf: new Map<number, number>() };
            e.need += a.total_need || 0;
            for (const [ff, st] of Object.entries(a.rf_stocks || {})) {
                const avail = st?.available || 0;
                if (avail <= 0) continue;
                e.free += avail;
                const fid = Number(ff);
                e.byFf.set(fid, (e.byFf.get(fid) ?? 0) + avail);
            }
            agg.set(cat, e);
        }
        return [...agg.entries()]
            .map(([cat, v]) => ({ cat, ...v }))
            .filter(c => c.need > 0 || c.free > 0)
            .sort((x, y) => y.free - x.free || x.cat.localeCompare(y.cat, 'ru'));
    }, [articles, categoryOf]);

    const ffNameById = useMemo(() => new Map(warehouses.map(w => [w.id, w.name])), [warehouses]);

    const visible = useMemo(() => {
        const q = query.trim().toLowerCase();
        return q ? categories.filter(c => c.cat.toLowerCase().includes(q)) : categories;
    }, [categories, query]);

    const ffOptions = useMemo(
        () => warehouses.filter(w => w.warehouse_type === 'FULFILLMENT' && w.is_active !== false),
        [warehouses],
    );

    const toggle = (cat: string) => {
        setSelected(prev => {
            const next = new Set(prev);
            if (next.has(cat)) next.delete(cat); else next.add(cat);
            return next;
        });
    };

    const handleCreate = async () => {
        if (selected.size === 0 || busy) return;
        setBusy(true);
        try {
            await onCreate([...selected], ffId === '' ? null : Number(ffId));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 100, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}
            onClick={onClose}>
            {/* modal-card-solid: под модалкой матрица — glass просвечивал (жалоба юзера). */}
            <div className="modal-card modal-card-solid" style={{ width: 680, maxWidth: '100%', maxHeight: '85vh', overflow: 'auto', padding: 20 }} onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12 }}>
                    <h3 style={{ margin: 0, fontSize: 16 }}>🗂 Распределение по категориям</h3>
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={onClose}>✕</button>
                </div>
                <input className="form-input" placeholder="🔎 Поиск категории" value={query}
                    onChange={e => setQuery(e.target.value)} style={{ width: '100%', marginBottom: 10 }} />
                {categories.length === 0 ? (
                    <div style={{ padding: 16, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>
                        Нет категорий с потребностью или остатком — проверьте загрузку потребности.
                    </div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 12, maxHeight: 440, overflow: 'auto' }}>
                        {visible.map(c => {
                            const busy2 = taken.has(c.cat);
                            // «свободно» под выбранный ФФ-источник: срез будет брать
                            // только его сток — показываем ровно то, что достанется.
                            const freeShown = ffId === '' ? c.free : (c.byFf.get(Number(ffId)) ?? 0);
                            const byFfSorted = [...c.byFf.entries()].sort((a, b) => b[1] - a[1]);
                            return (
                                <label key={c.cat} style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: 13, padding: '4px 6px', borderRadius: 8, opacity: busy2 ? 0.5 : 1, cursor: busy2 ? 'not-allowed' : 'pointer' }}
                                    title={busy2 ? 'Категория уже выделена в другой категорийный черновик' : undefined}>
                                    <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                        <input type="checkbox" checked={selected.has(c.cat)} disabled={busy2} onChange={() => toggle(c.cat)} />
                                        <span style={{ fontWeight: 500 }}>{c.cat}</span>
                                        {busy2 && <span style={{ fontSize: 11, color: 'var(--color-warning)' }}>уже в другом черновике</span>}
                                        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}
                                            title={ffId === '' ? undefined : `Свободно на выбранном складе; всего по всем: ${formatNumber(c.free, 0)}`}>
                                            потр. {formatNumber(c.need, 0)} · свободно {formatNumber(freeShown, 0)}
                                        </span>
                                    </span>
                                    {byFfSorted.length > 0 && (
                                        <span style={{ paddingLeft: 24, fontSize: 11, color: 'var(--color-text-dim)', lineHeight: 1.35 }}>
                                            {byFfSorted.map(([fid, q], i) => (
                                                <span key={fid} style={fid === ffId ? { color: 'var(--color-accent)', fontWeight: 600 } : undefined}>
                                                    {i > 0 && ' · '}{ffNameById.get(fid) || `ФФ ${fid}`} {formatNumber(q, 0)}
                                                </span>
                                            ))}
                                        </span>
                                    )}
                                </label>
                            );
                        })}
                        {visible.length === 0 && (
                            <div style={{ padding: 10, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 12 }}>Ничего не найдено.</div>
                        )}
                    </div>
                )}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, fontSize: 13 }}>
                    <span style={{ color: 'var(--color-text-muted)' }}>ФФ-источник</span>
                    <select className="form-input" style={{ flex: 1 }} value={ffId === '' ? '' : String(ffId)}
                        onChange={e => setFfId(e.target.value === '' ? '' : Number(e.target.value))}>
                        <option value="">Все склады</option>
                        {ffOptions.map(w => <option key={w.id} value={String(w.id)}>Только {w.name}</option>)}
                    </select>
                </div>
                <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                    Создаст отдельный черновик; раскладка среза считается на вкладке «✏️ Ручная раскладка».
                    Занятое им уйдёт из доступного в других черновиках; после создания заявок учтётся как «В сборке».
                </div>
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>Отмена</button>
                    <button className="btn btn-primary btn-sm" disabled={selected.size === 0 || busy} onClick={handleCreate}>
                        {busy ? 'Создание…' : `Создать и заполнить${selected.size ? ` (${formatNumber(selected.size, 0)})` : ''}`}
                    </button>
                </div>
            </div>
        </div>
    );
}
