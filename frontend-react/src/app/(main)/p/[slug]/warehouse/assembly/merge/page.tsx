'use client';

import Link from 'next/link';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { api } from '@/lib/api';
import { findDuplicateLanes } from '@/lib/utils/assemblyDraftMerge';
import { formatDate, formatNumber } from '@/lib/utils';
import type { AssemblyDraft, Warehouse } from '@/types/api';

// ─── Types ───────────────────────────────────────────────────────────────────

interface UniqueLane {
    ffName: string;
    wbName: string;
    pieces: number;
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function AssemblyMergePage() {
    const { slug } = useParams<{ slug: string }>();
    const router = useRouter();
    const searchParams = useSearchParams();

    const draftIds = useMemo(() => {
        const raw = searchParams.get('drafts') ?? '';
        return raw.split(',').map(Number).filter(n => n > 0);
    }, [searchParams]);

    const [drafts, setDrafts] = useState<AssemblyDraft[]>([]);
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [merging, setMerging] = useState(false);

    useEffect(() => {
        if (draftIds.length < 2) {
            router.replace(`/p/${slug}/warehouse/assembly`);
            return;
        }
        setLoading(true);
        Promise.all([
            api.listAssemblyDrafts(),
            api.getWarehouses(),
        ])
            .then(([allDrafts, whs]) => {
                const selected = allDrafts.filter(d => draftIds.includes(d.id));
                if (selected.length < 2) {
                    setError('Не удалось найти указанные черновики. Возможно, они были удалены.');
                    return;
                }
                setDrafts(selected);
                setWarehouses(whs.filter(w => w.warehouse_type === 'FULFILLMENT'));
            })
            .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Ошибка загрузки'))
            .finally(() => setLoading(false));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [draftIds.join(','), slug]);

    const nameById = useCallback(
        (id: number) => warehouses.find(w => w.id === id)?.name ?? `Склад ${id}`,
        [warehouses],
    );

    // All duplicate lanes across the selected drafts
    const dupLanes = useMemo(() => findDuplicateLanes(drafts, nameById), [drafts, nameById]);

    // Lanes that will actually consolidate (≥2 of the selected drafts share them)
    const mergingLanes = useMemo(() => {
        const sel = new Set(draftIds);
        return dupLanes.filter(l => l.draftIds.filter(id => sel.has(id)).length >= 2);
    }, [dupLanes, draftIds]);

    // Lanes unique to each draft (not in the merging set) — will stay separate at commit time
    const uniqueLanesPerDraft = useMemo(() => {
        const mergingKeys = new Set(mergingLanes.map(l => `${l.ffId}→${l.wbName}`));
        const result = new Map<number, UniqueLane[]>();
        for (const draft of drafts) {
            const draftLaneMap = new Map<string, { ffId: number; wbName: string; pieces: number }>();
            for (const row of draft.distribution.rows ?? []) {
                for (const [ffStr, srcQty] of Object.entries(row.src ?? {})) {
                    if ((srcQty ?? 0) <= 0) continue;
                    for (const [wb, tgtQty] of Object.entries(row.tgt ?? {})) {
                        if ((tgtQty ?? 0) <= 0) continue;
                        const key = `${ffStr}→${wb}`;
                        const ex = draftLaneMap.get(key);
                        if (ex) ex.pieces += tgtQty;
                        else draftLaneMap.set(key, { ffId: parseInt(ffStr, 10), wbName: wb, pieces: tgtQty });
                    }
                }
            }
            const unique: UniqueLane[] = [];
            for (const [key, { ffId, wbName, pieces }] of draftLaneMap) {
                if (!mergingKeys.has(key)) unique.push({ ffName: nameById(ffId), wbName, pieces });
            }
            if (unique.length > 0) result.set(draft.id, unique);
        }
        return result;
    }, [drafts, mergingLanes, nameById]);

    const totalPieces = mergingLanes.reduce((s, l) => s + l.pieces, 0);

    const handleMerge = async () => {
        setMerging(true);
        try {
            const merged = await api.mergeAssemblyDrafts(draftIds);
            const qs = new URLSearchParams({ draft: String(merged.id), pkg: 'BOX', type: 'all' });
            router.push(`/p/${slug}/warehouse/assembly/distribute/preview?${qs.toString()}`);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка объединения');
            setMerging(false);
        }
    };

    // ─── Loading / Error ──────────────────────────────────────────────────────

    if (loading) {
        return (
            <div className="animate-in">
                <div className="page-header">
                    <h1 className="page-title">Загрузка…</h1>
                </div>
            </div>
        );
    }

    if (error && drafts.length === 0) {
        return (
            <div className="animate-in">
                <div style={{ marginBottom: 16 }}>
                    <Link href={`/p/${slug}/warehouse/assembly`} style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                        ← Черновики
                    </Link>
                </div>
                <div className="glass-card" style={{ padding: 24, color: 'var(--color-danger)' }}>{error}</div>
            </div>
        );
    }

    const backUrl = `/p/${slug}/warehouse/assembly`;

    // ─── Render ───────────────────────────────────────────────────────────────

    return (
        <div className="animate-in">
            {/* Error toast (after partial load) */}
            {error && (
                <div style={{
                    marginBottom: 12, padding: '10px 16px', borderRadius: 12,
                    background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)',
                    color: 'var(--color-danger)', fontSize: 13,
                }}>
                    {error}
                </div>
            )}

            {/* Header */}
            <div className="page-header">
                <div>
                    <Link href={backUrl} style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 6, display: 'block' }}>
                        ← Черновики
                    </Link>
                    <h1 className="page-title">Объединить черновики</h1>
                    <p className="page-subtitle">
                        {drafts.length} черновика → 1 объединённый · {mergingLanes.length} маршрутов сольются
                    </p>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <Link href={backUrl}>
                        <button className="btn btn-secondary">Отмена</button>
                    </Link>
                    <button className="btn btn-primary" onClick={handleMerge} disabled={merging}>
                        {merging ? 'Объединяю…' : 'Объединить и открыть предпросмотр →'}
                    </button>
                </div>
            </div>

            {/* Draft cards */}
            <div style={{
                display: 'grid',
                gridTemplateColumns: `repeat(${Math.min(drafts.length, 3)}, 1fr)`,
                gap: 12,
                marginBottom: 24,
            }}>
                {drafts.map(draft => {
                    const uniqueCount = uniqueLanesPerDraft.get(draft.id)?.length ?? 0;
                    const mergeCount = mergingLanes.filter(l => l.piecesPerDraft[draft.id] != null).length;
                    return (
                        <div key={draft.id} className="glass-card" style={{ padding: 20 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                                <span className="badge badge-info" style={{ fontSize: 11 }}>#{draft.id}</span>
                                <span style={{ fontWeight: 700, fontSize: 15 }}>
                                    {draft.name || `Черновик #${draft.id}`}
                                </span>
                            </div>
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                                обновлён {formatDate(draft.updated_at)}
                            </div>
                            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
                                {draft.distribution.source_warehouse_ids.map(id => (
                                    <span key={id} className="badge badge-secondary" style={{ fontSize: 11 }}>
                                        {nameById(id)}
                                    </span>
                                ))}
                            </div>
                            <div style={{ display: 'flex', gap: 16, fontSize: 13 }}>
                                <div>
                                    <div style={{ fontWeight: 700 }}>{draft.distribution.rows.length}</div>
                                    <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>позиций</div>
                                </div>
                                <div>
                                    <div style={{ fontWeight: 700, color: 'var(--color-success)' }}>{mergeCount}</div>
                                    <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>сольются</div>
                                </div>
                                {uniqueCount > 0 && (
                                    <div>
                                        <div style={{ fontWeight: 700 }}>{uniqueCount}</div>
                                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>уникальных</div>
                                    </div>
                                )}
                            </div>
                        </div>
                    );
                })}
            </div>

            {/* Merging lanes table */}
            {mergingLanes.length > 0 && (
                <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
                        <div>
                            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>
                                Маршруты, которые объединятся
                            </div>
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                Вместо {mergingLanes.length * drafts.length} отдельных отправок — {mergingLanes.length} объединённых
                            </div>
                        </div>
                        <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
                            <span className="badge badge-success">{mergingLanes.length} маршрутов</span>
                            <span className="badge badge-secondary">~{formatNumber(totalPieces)} шт. итого</span>
                        </div>
                    </div>

                    <div style={{ overflowX: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                            <thead>
                                <tr style={{ borderBottom: '2px solid var(--color-border)' }}>
                                    <th style={{
                                        textAlign: 'left', padding: '8px 12px',
                                        fontWeight: 600, color: 'var(--color-text-muted)', fontSize: 12,
                                    }}>
                                        Маршрут ФФ → WB
                                    </th>
                                    {drafts.map(d => (
                                        <th key={d.id} style={{
                                            textAlign: 'right', padding: '8px 12px',
                                            color: 'var(--color-accent)', fontSize: 12, fontWeight: 600,
                                            whiteSpace: 'nowrap',
                                        }}>
                                            #{d.id} {d.name ? `· ${d.name}` : ''}
                                        </th>
                                    ))}
                                    <th style={{
                                        textAlign: 'right', padding: '8px 12px',
                                        fontWeight: 700, fontSize: 12,
                                    }}>
                                        Итого
                                    </th>
                                </tr>
                            </thead>
                            <tbody>
                                {mergingLanes.map((lane, idx) => (
                                    <tr
                                        key={`${lane.ffId}-${lane.wbName}`}
                                        style={{
                                            borderBottom: idx < mergingLanes.length - 1
                                                ? '1px solid var(--color-border)'
                                                : undefined,
                                        }}
                                    >
                                        <td style={{ padding: '10px 12px' }}>
                                            <span style={{ fontWeight: 600 }}>{lane.ffName}</span>
                                            <span style={{ color: 'var(--color-text-muted)', margin: '0 6px' }}>→</span>
                                            <span>{lane.wbName}</span>
                                        </td>
                                        {drafts.map(d => (
                                            <td key={d.id} style={{
                                                textAlign: 'right', padding: '10px 12px',
                                                color: lane.piecesPerDraft[d.id] != null
                                                    ? 'var(--color-text)'
                                                    : 'var(--color-text-dim)',
                                            }}>
                                                {lane.piecesPerDraft[d.id] != null
                                                    ? `~${formatNumber(lane.piecesPerDraft[d.id])} шт.`
                                                    : '—'}
                                            </td>
                                        ))}
                                        <td style={{
                                            textAlign: 'right', padding: '10px 12px',
                                            fontWeight: 700,
                                        }}>
                                            ~{formatNumber(lane.pieces)} шт.
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                            <tfoot>
                                <tr style={{ borderTop: '2px solid var(--color-border)' }}>
                                    <td style={{ padding: '10px 12px', fontWeight: 600, color: 'var(--color-text-muted)', fontSize: 12 }}>
                                        Итого по маршрутам
                                    </td>
                                    {drafts.map(d => {
                                        const draftTotal = mergingLanes
                                            .map(l => l.piecesPerDraft[d.id] ?? 0)
                                            .reduce((a, b) => a + b, 0);
                                        return (
                                            <td key={d.id} style={{
                                                textAlign: 'right', padding: '10px 12px',
                                                fontWeight: 600, color: 'var(--color-text-muted)',
                                            }}>
                                                ~{formatNumber(draftTotal)} шт.
                                            </td>
                                        );
                                    })}
                                    <td style={{ textAlign: 'right', padding: '10px 12px', fontWeight: 700 }}>
                                        ~{formatNumber(totalPieces)} шт.
                                    </td>
                                </tr>
                            </tfoot>
                        </table>
                    </div>
                </div>
            )}

            {/* Unique lanes */}
            {uniqueLanesPerDraft.size > 0 && (
                <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                    <div style={{ marginBottom: 12 }}>
                        <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>
                            Уникальные маршруты
                        </div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                            Есть только в одном черновике — при передаче на ФФ создадут отдельные заявки
                        </div>
                    </div>
                    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                        {[...uniqueLanesPerDraft.entries()].map(([draftId, lanes]) => {
                            const draft = drafts.find(d => d.id === draftId);
                            return (
                                <div key={draftId} style={{ flex: 1, minWidth: 220 }}>
                                    <div style={{
                                        fontSize: 12, fontWeight: 600,
                                        color: 'var(--color-accent)', marginBottom: 8,
                                    }}>
                                        #{draftId}{draft?.name ? ` · ${draft.name}` : ''}
                                    </div>
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                        {lanes.map(lane => (
                                            <div
                                                key={`${lane.ffName}-${lane.wbName}`}
                                                style={{
                                                    display: 'flex', alignItems: 'center', gap: 6,
                                                    padding: '6px 10px',
                                                    background: 'var(--color-bg)',
                                                    borderRadius: 8,
                                                    fontSize: 12,
                                                }}
                                            >
                                                <span style={{ color: 'var(--color-text-muted)' }}>{lane.ffName}</span>
                                                <span style={{ color: 'var(--color-text-dim)' }}>→</span>
                                                <span>{lane.wbName}</span>
                                                <span style={{ marginLeft: 'auto', color: 'var(--color-text-muted)' }}>
                                                    ~{formatNumber(lane.pieces)} шт.
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            {/* No overlap case */}
            {mergingLanes.length === 0 && (
                <div className="glass-card" style={{ padding: 24, marginBottom: 16, textAlign: 'center' }}>
                    <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>Дублирующихся маршрутов нет</div>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                        У этих черновиков нет общих маршрутов. После объединения все маршруты останутся
                        отдельными заявками при передаче на ФФ.
                    </div>
                </div>
            )}

            {/* Bottom action bar */}
            <div style={{
                display: 'flex', gap: 8, justifyContent: 'flex-end',
                padding: '16px 0', borderTop: '1px solid var(--color-border)', marginTop: 8,
            }}>
                <Link href={backUrl}>
                    <button className="btn btn-secondary">Отмена</button>
                </Link>
                <button className="btn btn-primary" onClick={handleMerge} disabled={merging}>
                    {merging ? 'Объединяю…' : 'Объединить и открыть предпросмотр →'}
                </button>
            </div>
        </div>
    );
}
