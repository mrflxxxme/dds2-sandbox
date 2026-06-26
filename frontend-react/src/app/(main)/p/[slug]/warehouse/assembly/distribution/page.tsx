'use client';

/**
 * Страница «Распределение» — черновики, партии на предпросмотре и
 * дублирующиеся маршруты. Выделена из warehouse/assembly/page.tsx
 * (там осталась компактная полоска-сводка со ссылкой сюда).
 *
 * Создание нового распределения живёт в «Аналитике остатков»
 * (Потребность складов → «В сборку») — CTA в шапке ведёт туда.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDateTime, formatNumber, pluralRu } from '@/lib/utils';
import { PageHeader, Toast } from '@/components';
import KpiCard from '@/components/KpiCard';
import type { AssemblyDraft, CreatedAssemblyGroup, Warehouse } from '@/types/api';
import { findDuplicateLanes } from '@/lib/utils/assemblyDraftMerge';

export default function AssemblyDistributionPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;

    // ─── Data ─────────────────────────────────────────────────────────────
    const [drafts, setDrafts] = useState<AssemblyDraft[]>([]);
    const [createdGroups, setCreatedGroups] = useState<CreatedAssemblyGroup[]>([]);
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    // UI state
    const [selectedDraftIds, setSelectedDraftIds] = useState<Set<number>>(new Set());
    const [expandedGroups, setExpandedGroups] = useState<Set<number>>(new Set());

    // Гонка focus-reload с удалением: применяем результат только ПОСЛЕДНЕГО
    // начатого запроса (delete тоже бампает счётчик), иначе GET, стартовавший
    // до DELETE, «воскрешает» удалённый черновик.
    const loadSeqRef = useRef(0);

    const load = useCallback(async (signal?: AbortSignal) => {
        const seq = ++loadSeqRef.current;
        setLoading(true);
        setError('');
        try {
            const [draftList, groups] = await Promise.all([
                api.listAssemblyDrafts(),
                api.getCreatedAssemblyGroups(),
            ]);
            if (signal?.aborted || seq !== loadSeqRef.current) return;
            setDrafts(draftList);
            setCreatedGroups(groups);
            // Фантомный выбор: чистим id, которых больше нет в списке черновиков
            setSelectedDraftIds(prev => {
                const next = new Set([...prev].filter(id => draftList.some(d => d.id === id)));
                return next.size === prev.size ? prev : next;
            });
        } catch (e: unknown) {
            if (signal?.aborted || seq !== loadSeqRef.current) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (!signal?.aborted && seq === loadSeqRef.current) setLoading(false);
        }
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    // Имена ФФ-складов — для отображения маршрутов в дублях (не критично при ошибке)
    useEffect(() => {
        const controller = new AbortController();
        api.getWarehouses()
            .then(whs => {
                if (controller.signal.aborted) return;
                setWarehouses(whs.filter(w => w.warehouse_type === 'FULFILLMENT'));
            })
            .catch(() => {});
        return () => controller.abort();
    }, []);

    // Обновляем данные при возврате на вкладку (например, после commit на /distribute)
    useEffect(() => {
        const handler = () => { load(); };
        window.addEventListener('focus', handler);
        return () => window.removeEventListener('focus', handler);
    }, [load]);

    const warehouseNameById = useCallback(
        (id: number) => warehouses.find(w => w.id === id)?.name ?? `Склад ${id}`,
        [warehouses],
    );

    // Дубли направлений: пары (ff→wb), которые встречаются в ≥2 открытых черновиках
    const duplicateLanes = useMemo(
        () => drafts.length >= 2 ? findDuplicateLanes(drafts, warehouseNameById) : [],
        [drafts, warehouseNameById],
    );

    // ─── Handlers ─────────────────────────────────────────────────────────

    const handleDeleteDraft = useCallback(async (draftId: number) => {
        if (!confirm('Удалить черновик?')) return;
        const dropLocal = () => {
            // Инвалидируем in-flight load(), стартовавший до удаления
            loadSeqRef.current += 1;
            setDrafts(prev => prev.filter(d => d.id !== draftId));
            setSelectedDraftIds(prev => {
                if (!prev.has(draftId)) return prev;
                const next = new Set(prev);
                next.delete(draftId);
                return next;
            });
        };
        try {
            await api.deleteAssemblyDraft(draftId);
            dropLocal();
            setToast({ message: 'Черновик удалён', type: 'success' });
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : 'Ошибка удаления';
            // 404 → черновик уже удалён в БД (commit / параллельная вкладка); идемпотентно убираем из UI
            if (/not found|404/i.test(msg)) {
                dropLocal();
                setToast({ message: 'Черновик уже был удалён', type: 'success' });
            } else {
                setToast({ message: msg, type: 'error' });
            }
        }
    }, []);

    const goMerge = useCallback((draftIds: number[]) => {
        const qs = new URLSearchParams({ drafts: draftIds.join(',') });
        router.push(`/p/${slug}/warehouse/assembly/merge?${qs.toString()}`);
    }, [router, slug]);

    const toggleDraftSelected = useCallback((draftId: number, checked: boolean) => {
        setSelectedDraftIds(prev => {
            const next = new Set(prev);
            if (checked) next.add(draftId); else next.delete(draftId);
            return next;
        });
    }, []);

    const toggleGroupExpanded = useCallback((draftId: number) => {
        setExpandedGroups(prev => {
            const next = new Set(prev);
            if (next.has(draftId)) next.delete(draftId); else next.add(draftId);
            return next;
        });
    }, []);

    // ─── Render ───────────────────────────────────────────────────────────

    const isEmpty = !loading && !error && drafts.length === 0 && createdGroups.length === 0;

    return (
        <div className="animate-in">
            {toast && (
                <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} duration={toast.type === 'error' ? 4000 : 2500} />
            )}

            <PageHeader
                title="Распределение"
                subtitle="Черновики, партии и маршруты"
                actions={
                    <>
                        <Link href={`/p/${slug}/warehouse/assembly`}>
                            <button className="btn btn-secondary">Заявки на сборку →</button>
                        </Link>
                        <Link href={`/p/${slug}/warehouse/analytics`}>
                            <button className="btn btn-primary" title="Аналитика остатков → Потребность складов → «В сборку»">
                                Распределить остатки
                            </button>
                        </Link>
                    </>
                }
            />

            {/* Loading */}
            {loading && (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
            )}

            {/* Error */}
            {!loading && error && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)' }}>
                    {error}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 12 }} onClick={() => load()}>
                        Повторить
                    </button>
                </div>
            )}

            {/* Empty — onboarding */}
            {isEmpty && (
                <div className="glass-card" style={{ padding: 64, textAlign: 'center' }}>
                    <div style={{ fontSize: 48, marginBottom: 16 }}>🧩</div>
                    <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>Распределений пока нет</div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14, maxWidth: 480, margin: '0 auto 20px' }}>
                        Распределение превращает остатки ваших складов в черновики отправок:
                        выбираете товары в «Аналитике остатков», раскидываете по складам WB,
                        а отсюда доводите черновики до заявок на сборку.
                    </div>
                    <Link href={`/p/${slug}/warehouse/analytics`}>
                        <button className="btn btn-primary">Начать распределение</button>
                    </Link>
                </div>
            )}

            {/* Data */}
            {!loading && !error && !isEmpty && (
                <>
                    {/* KPI strip */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12, marginBottom: 16 }}>
                        <KpiCard label="Черновиков" value={formatNumber(drafts.length, 0)} icon="📝" color="var(--color-accent)" sub="незавершённых распределений" />
                        <KpiCard label="Партий на предпросмотре" value={formatNumber(createdGroups.length, 0)} icon="📦" color="var(--color-success)" sub="созданы и ещё в сборке" />
                        <KpiCard
                            label="Дублей маршрутов"
                            value={formatNumber(duplicateLanes.length, 0)}
                            icon="⚠️"
                            color={duplicateLanes.length > 0 ? 'var(--color-warning)' : 'var(--color-text-muted)'}
                            sub={duplicateLanes.length > 0 ? 'стоит объединить черновики' : 'пересечений нет'}
                        />
                    </div>

                    {/* Duplicate lanes */}
                    {duplicateLanes.length > 0 && (
                        <div className="glass-card" style={{ padding: 16, marginBottom: 16, borderLeft: '3px solid var(--color-warning)' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, marginBottom: 4 }}>
                                <span>⚠️</span>
                                <span style={{ fontWeight: 600, color: 'var(--color-text)' }}>Дублирующиеся маршруты</span>
                            </div>
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                                Один и тот же маршрут встречается в нескольких черновиках — при создании заявок
                                получатся отдельные отправки. Объедините черновики, чтобы передать на ФФ одной.
                            </div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                                {duplicateLanes.map(lane => (
                                    <div
                                        key={`${lane.ffId}-${lane.wbName}`}
                                        style={{
                                            display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                                            padding: '8px 12px',
                                            border: '1px solid var(--color-border)',
                                            borderRadius: 12,
                                            background: 'var(--color-bg)',
                                            fontSize: 13,
                                        }}
                                    >
                                        <span style={{ fontWeight: 600 }}>{lane.ffName} → {lane.wbName}</span>
                                        <span style={{ color: 'var(--color-text-muted)', fontSize: 12 }}>
                                            {formatNumber(lane.pieces, 0)} шт.
                                        </span>
                                        <span style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                                            {lane.draftIds.map(id => (
                                                <span
                                                    key={id}
                                                    className="badge badge-warning"
                                                    style={{ fontSize: 11 }}
                                                    title={`Черновик #${id} · ~${formatNumber(lane.piecesPerDraft[id] ?? 0, 0)} шт.`}
                                                >
                                                    #{id}
                                                </span>
                                            ))}
                                        </span>
                                        <span style={{ flex: 1 }} />
                                        <button className="btn btn-primary btn-sm" onClick={() => goMerge(lane.draftIds)}>
                                            Объединить →
                                        </button>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Drafts */}
                    <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text)' }}>
                                Незавершённые черновики{drafts.length > 0 ? ` (${drafts.length})` : ''}
                            </div>
                            {selectedDraftIds.size >= 2 && (
                                <button className="btn btn-primary btn-sm" onClick={() => goMerge([...selectedDraftIds])}>
                                    Объединить {selectedDraftIds.size} {pluralRu(selectedDraftIds.size, ['черновик', 'черновика', 'черновиков'])} →
                                </button>
                            )}
                        </div>
                        {drafts.length === 0 ? (
                            <div style={{ padding: '16px 0', textAlign: 'center' }}>
                                <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                                    Черновиков нет — начните с распределения остатков
                                </div>
                                <Link href={`/p/${slug}/warehouse/analytics`}>
                                    <button className="btn btn-secondary btn-sm">Начать распределение</button>
                                </Link>
                            </div>
                        ) : (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                                {drafts.map(draft => {
                                    const isSelected = selectedDraftIds.has(draft.id);
                                    return (
                                        <div
                                            key={draft.id}
                                            style={{
                                                display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                                                padding: '10px 12px',
                                                border: `1px solid ${isSelected ? 'var(--color-accent)' : 'var(--color-border)'}`,
                                                borderRadius: 12,
                                                background: isSelected ? 'color-mix(in srgb, var(--color-accent) 8%, var(--color-bg))' : 'var(--color-bg)',
                                                fontSize: 12,
                                                transition: 'border-color 0.2s, background 0.2s',
                                            }}
                                        >
                                            <input
                                                type="checkbox"
                                                checked={isSelected}
                                                onChange={e => toggleDraftSelected(draft.id, e.target.checked)}
                                                style={{ accentColor: 'var(--color-accent)', cursor: 'pointer' }}
                                                title="Выбрать для объединения"
                                            />
                                            <div style={{ display: 'flex', flexDirection: 'column', gap: 2, flex: 1, minWidth: 160 }}>
                                                <span style={{ fontWeight: 600, fontSize: 13 }}>{draft.name || `Черновик #${draft.id}`}</span>
                                                <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                                    {draft.distribution.rows.length} {pluralRu(draft.distribution.rows.length, ['позиция', 'позиции', 'позиций'])} · обновлён {formatDateTime(draft.updated_at)}
                                                </span>
                                            </div>
                                            <button
                                                className="btn btn-secondary btn-sm"
                                                onClick={() => router.push(`/p/${slug}/warehouse/assembly/distribute?draft=${draft.id}`)}
                                                title="Открыть черновик сборки — редактор и создание заявок"
                                            >
                                                Открыть →
                                            </button>
                                            <button
                                                className="btn btn-secondary btn-sm"
                                                onClick={() => router.push(`/p/${slug}/warehouse/assembly/distribute?draft=${draft.id}`)}
                                                title="Изменить распределение (редактировать черновик)"
                                            >
                                                ✏️
                                            </button>
                                            <button
                                                className="btn btn-danger btn-sm"
                                                onClick={() => handleDeleteDraft(draft.id)}
                                                title="Удалить черновик"
                                            >
                                                ×
                                            </button>
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                    </div>

                    {/* Created groups — preview */}
                    {createdGroups.length > 0 && (
                        <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text)', marginBottom: 4 }}>
                                Созданные партии — предпросмотр ({createdGroups.length})
                            </div>
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                                Заявки, созданные из черновика и ещё в сборке. Только просмотр — правьте через карточку заявки.
                            </div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                                {createdGroups.map(g => {
                                    const open = expandedGroups.has(g.draft_id);
                                    return (
                                        <div key={g.draft_id} style={{ border: '1px solid var(--color-border)', borderRadius: 12, background: 'var(--color-bg)' }}>
                                            <button
                                                onClick={() => toggleGroupExpanded(g.draft_id)}
                                                style={{ display: 'flex', alignItems: 'center', gap: 10, width: '100%', padding: '10px 14px', background: 'transparent', border: 'none', cursor: 'pointer', textAlign: 'left' }}
                                            >
                                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{open ? '▾' : '▸'}</span>
                                                <span style={{ fontWeight: 600, fontSize: 13 }}>{g.draft_name || `Черновик #${g.draft_id}`}</span>
                                                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                                    {g.request_count} {pluralRu(g.request_count, ['заявка', 'заявки', 'заявок'])} · {formatNumber(g.total_qty, 0)} шт · {g.total_sku} SKU
                                                </span>
                                            </button>
                                            {open && (
                                                <div style={{ borderTop: '1px solid var(--color-border)', padding: '6px 14px 10px' }}>
                                                    {g.requests.map(r => (
                                                        <div key={r.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: '1px solid var(--color-border)', fontSize: 13 }}>
                                                            <span style={{ flex: 1, minWidth: 0 }}>
                                                                <span style={{ fontWeight: 600 }}>{r.ff_name} → {r.wb_name || '—'}</span>
                                                                <span style={{ color: 'var(--color-text-muted)', marginLeft: 8, fontSize: 12 }}>
                                                                    {r.package_type === 'MONOPALLET' ? 'Моно' : r.package_type === 'SUPERSAFE' ? 'Сейф' : 'Короб'} · {formatNumber(r.qty, 0)} шт · {r.sku} SKU · {r.number}
                                                                </span>
                                                            </span>
                                                            <button className="btn btn-secondary btn-sm" onClick={() => router.push(`/p/${slug}/warehouse/assembly/${r.id}`)}>Открыть →</button>
                                                        </div>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    )}
                </>
            )}
        </div>
    );
}
