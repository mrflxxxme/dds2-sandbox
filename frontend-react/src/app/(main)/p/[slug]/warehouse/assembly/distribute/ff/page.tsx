'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { exportToExcel, formatDate, formatNumber } from '@/lib/utils';
import { Toast } from '@/components';
import KpiCard from '@/components/KpiCard';
import type { AssemblyDraft, AssemblyDraftUnitRef, AssemblyRequest, AssemblyStatus, PackageType, Warehouse } from '@/types/api';
import {
    buildFfUnits,
    excelSheetName,
    PKG_LABEL_RU,
    PREVIEW_EXPORT_COLUMNS,
    type DraftUnit,
} from '@/lib/utils/assemblyPreview';

const STATUS_BADGE: Record<string, { label: string; bg: string; color: string }> = {
    draft: { label: 'Черновик', bg: 'rgba(142,142,147,0.16)', color: '#6e6e73' },
    handed: { label: 'Передан на ФФ', bg: 'rgba(245,158,11,0.18)', color: '#a16207' },
};

// Статусы созданной заявки на сборку (для блока «История»). Зеркало STATUS_MAP
// со страницы warehouse/assembly.
const ASM_STATUS: Record<AssemblyStatus, { label: string; className: string }> = {
    PENDING:          { label: 'В сборке',      className: 'badge-info' },
    IN_PROGRESS:      { label: 'В сборке',      className: 'badge-info' },
    READY:            { label: 'Готово',        className: 'badge-success' },
    VEHICLE_ASSIGNED: { label: 'Машина',        className: 'badge-info' },
    SHIPPED:          { label: 'Отгружена',     className: 'badge-success' },
    DELIVERED:        { label: 'Принята WB',    className: 'badge-success' },
    CANCELLED:        { label: 'Отменена',      className: 'badge-secondary' },
};

const unitRef = (u: DraftUnit): AssemblyDraftUnitRef => ({
    source_ff_id: u.ffId,
    target_wb_name: u.wbName,
    package_type: u.pkg,
});

export default function AssemblyFfPage() {
    const params = useParams();
    const router = useRouter();
    const searchParams = useSearchParams();
    const slug = params.slug as string;

    const draftId = Number(searchParams.get('draft')) || null;
    const ffId = Number(searchParams.get('ff')) || null;
    const pkgTab: PackageType = searchParams.get('pkg') === 'MONOPALLET' ? 'MONOPALLET' : 'BOX';

    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const [draft, setDraft] = useState<AssemblyDraft | null>(null);
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [nmPpb, setNmPpb] = useState<Map<number, number | null>>(new Map());
    const [history, setHistory] = useState<AssemblyRequest[]>([]);

    const load = useCallback(async () => {
        if (!draftId) { setError('Не указан ID черновика'); setLoading(false); return; }
        setLoading(true);
        setError(null);
        try {
            const [d, whs, hist] = await Promise.all([
                api.getAssemblyDraft(draftId),
                api.getWarehouses(),
                // История: заявки на сборку, созданные из ЭТОГО черновика (раздела) и
                // склада-источника (свежие сверху). source_draft_id проставляется на commit.
                ffId != null
                    ? api.getAssemblyRequests({ draft_id: draftId, warehouse_id: ffId, limit: 200 })
                    : api.getAssemblyRequests({ draft_id: draftId, limit: 200 }),
            ]);
            setDraft(d);
            setWarehouses(whs);
            setHistory([...hist.items].sort((a, b) => b.created_at.localeCompare(a.created_at)));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [draftId, ffId]);
    useEffect(() => { load(); }, [load]);

    useEffect(() => {
        let cancelled = false;
        api.getBoxMultiplicity()
            .then(resp => {
                if (cancelled) return;
                const m = new Map<number, number | null>();
                for (const r of resp.items) {
                    let ppb: number | null = null;
                    if (r.box_qty_override && r.box_qty_override > 0 && r.use_box_multiplicity) {
                        ppb = r.box_qty_override;
                    } else {
                        let best = 0;
                        for (const p of r.per_warehouse) {
                            if (p.box_qty && p.box_qty > 0 && p.use_box_multiplicity && (best === 0 || p.box_qty < best)) best = p.box_qty;
                        }
                        ppb = best > 0 ? best : null;
                    }
                    m.set(r.nm_id, ppb);
                }
                setNmPpb(m);
            })
            .catch(() => { /* best-effort */ });
        return () => { cancelled = true; };
    }, []);

    const warehouseNameById = useCallback(
        (id: number) => warehouses.find(w => w.id === id)?.name ?? `Склад ${id}`,
        [warehouses],
    );
    const newcomerNmIds = useMemo(() => new Set(draft?.newcomer_nm_ids ?? []), [draft]);

    // Срез по упаковке (короб/моно). Новинки и обычные внутри склада не разделяем.
    const filtered = useMemo(() => {
        const allRows = draft?.distribution.rows ?? [];
        const allHanded = draft?.distribution.handed_units ?? [];
        const pkgOkRow = (p?: PackageType) => pkgTab === 'MONOPALLET' ? p === 'MONOPALLET' : p !== 'MONOPALLET';
        const rows = allRows.filter(r => pkgOkRow(r.package_type));
        const handed = allHanded.filter(h => pkgOkRow(h.package_type));
        return { rows, handed };
    }, [draft, pkgTab]);

    const units = useMemo(
        () => ffId == null ? [] : buildFfUnits(filtered.rows, filtered.handed, newcomerNmIds, ffId),
        [filtered, newcomerNmIds, ffId],
    );
    const ffName = ffId != null ? warehouseNameById(ffId) : '';

    // Коробок = ⌈шт / K⌉: неполный короб считается коробом (товар всё равно кладётся в короб).
    const boxesOf = useCallback((nmId: number, qty: number) => {
        const k = nmPpb.get(nmId);
        return k && k > 0 ? Math.ceil(qty / k) : 0;
    }, [nmPpb]);
    const unitBoxes = useCallback((u: DraftUnit) => u.items.reduce((s, it) => s + boxesOf(it.nmId, it.qty), 0), [boxesOf]);

    const backToPreview = useCallback(() => {
        const qs = new URLSearchParams({ draft: String(draftId ?? ''), pkg: pkgTab });
        router.push(`/p/${slug}/warehouse/assembly/distribute/preview?${qs.toString()}`);
    }, [draftId, pkgTab, router, slug]);

    const openUnit = useCallback((u: DraftUnit) => {
        const qs = new URLSearchParams({
            draft: String(draftId ?? ''), ff: String(u.ffId), wb: u.wbName, pkg: u.pkg,
        });
        router.push(`/p/${slug}/warehouse/assembly/distribute/request?${qs.toString()}`);
    }, [draftId, router, slug]);

    const handleHandOff = useCallback(async (u: DraftUnit) => {
        if (!draftId) return;
        setBusy(true);
        try {
            const d = await api.handOffDraftUnit(draftId, unitRef(u));
            setDraft(d);
            setToast({ message: `«${u.wbName}» передан на ФФ`, type: 'success' });
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка', type: 'error' });
        } finally {
            setBusy(false);
        }
    }, [draftId]);

    const handleRevert = useCallback(async (u: DraftUnit) => {
        if (!draftId) return;
        setBusy(true);
        try {
            const d = await api.revertDraftUnit(draftId, unitRef(u));
            setDraft(d);
            setToast({ message: `«${u.wbName}» возвращён в черновик`, type: 'success' });
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка', type: 'error' });
        } finally {
            setBusy(false);
        }
    }, [draftId]);

    const handleCommitUnit = useCallback(async (u: DraftUnit) => {
        if (!draftId) return;
        setBusy(true);
        try {
            const resp = await api.commitDraftUnit(draftId, unitRef(u));
            const id = resp.created_request_ids[0];
            setToast({ message: `Заявка на сборку создана${id ? ` (#${id})` : ''}`, type: 'success' });
            await load();
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка', type: 'error' });
        } finally {
            setBusy(false);
        }
    }, [draftId, load]);

    const handleDeleteUnit = useCallback(async (u: DraftUnit) => {
        if (!draftId) return;
        if (!window.confirm(`Удалить заявку «${ffName} → ${u.wbName}»? Товар останется на ФФ.`)) return;
        setBusy(true);
        try {
            const d = await api.deleteDraftUnit(draftId, unitRef(u));
            setDraft(d);
            setToast({ message: 'Заявка удалена', type: 'success' });
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка', type: 'error' });
        } finally {
            setBusy(false);
        }
    }, [draftId, ffName]);

    const exportFf = useCallback(() => {
        const data = units.flatMap(u => u.items.map(it => {
            const k = nmPpb.get(it.nmId);
            return {
                vendor: it.vendor, barcode: it.barcode, wb: u.wbName, qty: it.qty,
                boxes: k && k > 0 ? Math.ceil(it.qty / k) : '',
                box_qty: k && k > 0 ? k : '',
                pkg: PKG_LABEL_RU[u.pkg] || u.pkg,
                type: it.isNew ? 'Новинка' : 'Обычный',
            };
        })).sort((a, b) => a.wb.localeCompare(b.wb) || a.vendor.localeCompare(b.vendor));
        if (!data.length) return;
        exportToExcel(data, `Сборка_${excelSheetName(ffName)}_${new Date().toISOString().slice(0, 10)}`, PREVIEW_EXPORT_COLUMNS);
    }, [units, nmPpb, ffName]);

    if (loading) {
        return <div className="animate-in"><div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка…</div></div>;
    }
    if (error || ffId == null) {
        return (
            <div className="animate-in"><div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>
                {error || 'Не указан склад'}
                <div style={{ marginTop: 12 }}><button className="btn btn-secondary btn-sm" onClick={backToPreview}>← К предпросмотру</button></div>
            </div></div>
        );
    }

    const totalQty = units.reduce((s, u) => s + u.qty, 0);
    const totalBoxes = units.reduce((s, u) => s + unitBoxes(u), 0);
    const allItems = units.flatMap(u => u.items);
    const distinctSku = new Set(allItems.map(it => it.nmId)).size;
    const draftCount = units.filter(u => u.status === 'draft').length;
    const handedCount = units.filter(u => u.status === 'handed').length;
    const partialBoxes = allItems.filter(it => { const k = nmPpb.get(it.nmId); return !!k && k > 0 && it.qty % k !== 0; }).length;
    const looseUnits = allItems.reduce((s, it) => { const k = nmPpb.get(it.nmId); return s + (k && k > 0 && it.qty % k !== 0 ? it.qty % k : 0); }, 0);

    return (
        <div className="animate-in">
            {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}

            <div className="page-header" style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1 }}>
                    <button className="btn btn-secondary btn-sm" onClick={backToPreview}>← Назад</button>
                    <div>
                        <h1 className="page-title" style={{ margin: 0 }}>📦 {ffName}</h1>
                        <p className="page-subtitle" style={{ margin: 0 }}>
                            Заявки склада{handedCount > 0 ? ` · ${handedCount} передан на ФФ` : ''}
                        </p>
                    </div>
                </div>
                <button className="btn btn-secondary" onClick={exportFf} disabled={units.length === 0}>📥 Выгрузить склад</button>
            </div>

            {/* Сводка по складу */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 16 }}>
                <KpiCard label="Заявок" value={units.length} icon="📋" color="var(--color-accent)" sub={`${draftCount} черновик${handedCount > 0 ? ` · ${handedCount} на ФФ` : ''}`} />
                <KpiCard label="Штук" value={totalQty} icon="🔢" color="var(--color-success)" />
                <KpiCard label="Коробок" value={totalBoxes} icon="📦" color="var(--color-accent)" />
                <KpiCard label="SKU" value={distinctSku} icon="🏷️" color="var(--color-warning)" sub="уникальных" />
                <KpiCard
                    label="Неполных коробов"
                    value={partialBoxes}
                    icon="🟧"
                    color={partialBoxes > 0 ? 'var(--color-warning)' : 'var(--color-text-muted)'}
                    sub={partialBoxes > 0 ? `${partialBoxes} арт. · ${formatNumber(looseUnits, 0)} шт` : 'все короба полные'}
                />
            </div>

            {units.length === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Нет заявок этого склада в выбранном срезе.
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={backToPreview}>← К предпросмотру</button>
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {units.map(u => {
                        const badge = STATUS_BADGE[u.status];
                        return (
                            <div key={`${u.wbName}-${u.pkg}`} className="glass-card" style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                                <div style={{ flex: 1, minWidth: 220, cursor: 'pointer' }} onClick={() => openUnit(u)}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                        <span style={{ fontWeight: 700, fontSize: 15 }}>→ {u.wbName}</span>
                                        {u.hasNewcomer && <span title="В заявке есть товары-новинки" style={{ padding: '1px 6px', borderRadius: 6, background: '#a855f7', color: '#fff', fontSize: 9, fontWeight: 700 }}>🆕 новинки</span>}
                                        <span style={{ padding: '1px 6px', borderRadius: 6, background: 'rgba(59,130,246,0.14)', color: '#1d4ed8', fontSize: 9, fontWeight: 700 }}>
                                            {PKG_LABEL_RU[u.pkg] || u.pkg}
                                        </span>
                                        <span style={{ padding: '2px 8px', borderRadius: 24, background: badge.bg, color: badge.color, fontSize: 11, fontWeight: 700 }}>
                                            {badge.label}
                                        </span>
                                    </div>
                                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2 }}>
                                        Σ {formatNumber(u.qty, 0)} шт · {formatNumber(unitBoxes(u), 0)} кор · {u.items.length} SKU
                                    </div>
                                </div>
                                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                                    <button className="btn btn-secondary btn-sm" onClick={() => openUnit(u)}>Открыть →</button>
                                    {u.status === 'draft' ? (
                                        <>
                                            <button className="btn btn-danger btn-sm" onClick={() => handleDeleteUnit(u)} disabled={busy} title="Удалить заявку (товар останется на ФФ)">🗑</button>
                                            <button className="btn btn-sm" style={{ borderColor: '#a16207', color: '#a16207' }} onClick={() => handleHandOff(u)} disabled={busy}>
                                                Передать на ФФ
                                            </button>
                                        </>
                                    ) : (
                                        <>
                                            <button className="btn btn-secondary btn-sm" onClick={() => handleRevert(u)} disabled={busy}>Вернуть</button>
                                            <button className="btn btn-primary btn-sm" onClick={() => handleCommitUnit(u)} disabled={busy}>В сборку</button>
                                        </>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}

            {/* История: юниты ЭТОГО раздела (черновика), по которым создана заявка на сборку (ASM). */}
            <div style={{ marginTop: 28 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                    <h2 style={{ fontSize: 15, fontWeight: 700, margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
                        🗂 История — в сборке
                        <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-muted)' }}>{history.length}</span>
                    </h2>
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => router.push(`/p/${slug}/warehouse/assembly`)}>
                        Вся история →
                    </button>
                </div>
                {history.length === 0 ? (
                    <div className="glass-card" style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>
                        Пока нет заявок на сборку из этого раздела. Жми «В сборку» у переданного юнита — он появится здесь.
                    </div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                        {history.map(asm => {
                            const st = ASM_STATUS[asm.status] ?? { label: asm.status, className: 'badge-secondary' };
                            const qty = asm.items.reduce((s, it) => s + it.quantity, 0);
                            const target = asm.effective_wb_warehouse || asm.wb_warehouse_name_manual || asm.wb_warehouse_name || '—';
                            const openAsm = () => router.push(`/p/${slug}/warehouse/assembly/${asm.id}`);
                            return (
                                <div key={asm.id} className="glass-card" style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', cursor: 'pointer' }} onClick={openAsm}>
                                    <div style={{ flex: 1, minWidth: 220 }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                                            <span style={{ fontWeight: 700, fontSize: 15 }}>{asm.number}</span>
                                            <span style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>→ {target}</span>
                                            {asm.package_type && (
                                                <span style={{ padding: '1px 6px', borderRadius: 6, background: 'rgba(59,130,246,0.14)', color: '#1d4ed8', fontSize: 9, fontWeight: 700 }}>
                                                    {PKG_LABEL_RU[asm.package_type] || asm.package_type}
                                                </span>
                                            )}
                                            <span className={`badge ${st.className}`} style={{ fontSize: 11 }}>{st.label}</span>
                                        </div>
                                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2 }}>
                                            Σ {formatNumber(qty, 0)} шт · {asm.items.length} SKU · {formatDate(asm.created_at)}
                                        </div>
                                    </div>
                                    <button className="btn btn-secondary btn-sm" onClick={openAsm}>Открыть →</button>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>
        </div>
    );
}
