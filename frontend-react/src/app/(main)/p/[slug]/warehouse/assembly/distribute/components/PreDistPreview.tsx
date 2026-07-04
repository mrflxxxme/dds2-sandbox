'use client';
import { useMemo, useState } from 'react';
import { exportToExcel, formatNumber } from '@/lib/utils';
import KpiCard from '@/components/KpiCard';
import type { AssemblyDraftRow, PackageType } from '@/types/api';
import {
    buildPreviewLines,
    groupByWb,
    sumQty,
    reqCountOf,
    skuCountOf,
    PKG_LABEL_RU,
    type PreviewLine,
} from '@/lib/utils/assemblyPreview';
import { palletsForLines, maxPalletHeightCm, type PalletLine } from '@/lib/utils/boxPallet';

const PKG_ORDER: PackageType[] = ['BOX', 'MONOPALLET', 'SUPERSAFE'];
const PKG_EMOJI: Record<string, string> = { BOX: '📦', MONOPALLET: '🟫', SUPERSAFE: '🔒' };

/** Сколько ЦЕЛЫХ коробов из штук при кратности `ppb` (0 — не задана). */
const boxesOf = (qty: number, ppb: number | null | undefined): number =>
    ppb && ppb > 0 ? Math.ceil(qty / ppb) : 0;

interface Props {
    /** Что реально уедет в заявки (авто-раскладка + промоутнутая предбронь). */
    shipRows: AssemblyDraftRow[];
    newcomerNmIds: Set<number>;
    nmPpb: Map<number, number | null>;
    nmBoxSize: Map<number, string | null>;
    palletOverrides: Record<string, number>;
    vehicleNo: string;
    /** «Создать заявки» — createPreDistribution в родителе. */
    onSubmit: () => void;
    submitting: boolean;
}

/**
 * «Предпросмотр заявок» экрана машины — весь список того, что уедет в заявки
 * (PRE_DISTRIBUTED): сводка (заявок/штук/коробов/паллет/SKU) + группировка по упаковке →
 * WB-складу (город). Зеркалит раздельный «Предпросмотр заявок» черновика, но источник —
 * раскладка машины. Переиспользует чистые хелперы `assemblyPreview` (как `DraftPreview`).
 */
export default function PreDistPreview({ shipRows, newcomerNmIds, nmPpb, nmBoxSize, palletOverrides, vehicleNo, onSubmit, submitting }: Props) {
    const [view, setView] = useState<'cards' | 'table'>('cards');

    const lines = useMemo(() => buildPreviewLines(shipRows, newcomerNmIds), [shipRows, newcomerNmIds]);

    const palletLine = (l: PreviewLine): PalletLine => ({ units: l.qty, boxQty: nmPpb.get(l.nmId), boxSize: nmBoxSize.get(l.nmId) ?? null });

    // Сводка: заявок / штук / коробов / паллет / SKU / неполных коробов.
    const kpi = useMemo(() => {
        const totalBoxes = lines.reduce((s, l) => s + boxesOf(l.qty, nmPpb.get(l.nmId)), 0);
        const partial = lines.filter(l => { const p = nmPpb.get(l.nmId); return !!(p && p > 0) && l.qty % p !== 0; }).length;
        // Паллеты — по (WB × упаковка), у каждого своя высота/тип (как в footer раскладки).
        let pallets = 0;
        const byKey = new Map<string, { wb: string; pkg: PackageType; lines: PalletLine[] }>();
        for (const l of lines) {
            const key = `${l.wbName}::${l.pkg}`;
            const g = byKey.get(key) ?? { wb: l.wbName, pkg: l.pkg, lines: [] };
            g.lines.push(palletLine(l));
            byKey.set(key, g);
        }
        for (const g of byKey.values()) pallets += palletsForLines(g.lines, maxPalletHeightCm(g.wb), g.pkg === 'BOX' ? 'box' : 'mono', palletOverrides).pallets;
        return { reqs: reqCountOf(lines), qty: sumQty(lines), boxes: totalBoxes, pallets, skus: skuCountOf(lines), partial };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [lines, nmPpb, nmBoxSize, palletOverrides]);

    // Секции по упаковке → карточки направлений (WB-склад = город).
    const sections = useMemo(() => {
        const byPkg = new Map<PackageType, PreviewLine[]>();
        for (const l of lines) { const a = byPkg.get(l.pkg) ?? []; a.push(l); byPkg.set(l.pkg, a); }
        return PKG_ORDER.filter(p => byPkg.has(p)).map(pkg => ({
            pkg,
            qty: sumQty(byPkg.get(pkg)!),
            dirs: groupByWb(byPkg.get(pkg)!),
        }));
    }, [lines]);

    const exportAll = () => {
        exportToExcel(
            lines.map(l => ({
                'Упаковка': PKG_LABEL_RU[l.pkg] ?? l.pkg,
                'WB-склад': l.wbName,
                'Товар': l.vendor,
                'ШК': l.barcode,
                'Коробов': boxesOf(l.qty, nmPpb.get(l.nmId)),
                'Штук': l.qty,
            })),
            `Заявки_${vehicleNo}`,
        );
    };

    if (lines.length === 0) {
        return (
            <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-muted)' }}>
                Нечего отправлять — раскладка пуста (или всё оставлено на машине / в предброни).
            </div>
        );
    }

    return (
        <div>
            {/* Сводка + действия */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
                <h3 style={{ margin: 0, fontSize: 16 }}>Предпросмотр заявок</h3>
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={exportAll}>📥 Выгрузить в Excel</button>
                    <button className="btn btn-primary btn-sm" onClick={onSubmit} disabled={submitting}>
                        {submitting ? 'Создание…' : `✓ Создать заявки (${formatNumber(kpi.reqs, 0)})`}
                    </button>
                </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 16 }}>
                <KpiCard label="Заявок" value={formatNumber(kpi.reqs, 0)} />
                <KpiCard label="Штук" value={formatNumber(kpi.qty, 0)} />
                <KpiCard label="Коробов" value={formatNumber(kpi.boxes, 0)} />
                <KpiCard label="Паллет" value={formatNumber(kpi.pallets, 0)} />
                <KpiCard label="SKU" value={formatNumber(kpi.skus, 0)} />
                <KpiCard label="Неполных коробов" value={formatNumber(kpi.partial, 0)} />
            </div>

            {/* Переключатель вида */}
            <div style={{ display: 'flex', gap: 6, marginBottom: 16 }}>
                <span style={{ fontSize: 12, color: 'var(--color-muted)', alignSelf: 'center' }}>Вид:</span>
                <button className={`btn btn-sm ${view === 'cards' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setView('cards')}>📇 Карточки</button>
                <button className={`btn btn-sm ${view === 'table' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setView('table')}>📋 Таблица</button>
            </div>

            {view === 'cards' ? (
                sections.map(sec => (
                    <div key={sec.pkg} style={{ marginBottom: 20 }}>
                        <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 8 }}>
                            {PKG_EMOJI[sec.pkg]} {PKG_LABEL_RU[sec.pkg] ?? sec.pkg}
                            <span style={{ color: 'var(--color-muted)', fontWeight: 400, fontSize: 13, marginLeft: 8 }}>
                                Σ {formatNumber(sec.qty, 0)} шт · {formatNumber(sec.dirs.length, 0)} направл.
                            </span>
                        </div>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 12 }}>
                            {sec.dirs.map(({ wb, items }) => {
                                const boxes = items.reduce((s, l) => s + boxesOf(l.qty, nmPpb.get(l.nmId)), 0);
                                return (
                                    <div key={wb} className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                                        <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--color-border)', display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
                                            <span style={{ fontWeight: 600 }}>→ {wb}</span>
                                            <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--color-muted)' }}>
                                                {formatNumber(sumQty(items), 0)} шт · 📦 {formatNumber(boxes, 0)} · {formatNumber(items.length, 0)} SKU
                                            </span>
                                        </div>
                                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                                            <tbody>
                                                {items.map((l, i) => (
                                                    <tr key={`${l.barcode}-${i}`} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                                        <td style={{ padding: '5px 14px' }}>
                                                            {l.vendor}{l.isNew && <span className="badge" style={{ marginLeft: 6, background: 'rgba(168,85,247,0.16)', color: '#a855f7', fontSize: 9, padding: '0 5px' }}>🆕</span>}
                                                        </td>
                                                        <td style={{ padding: '5px 14px', textAlign: 'right', color: 'var(--color-muted)' }}>{formatNumber(boxesOf(l.qty, nmPpb.get(l.nmId)), 0)} кор</td>
                                                        <td style={{ padding: '5px 14px', textAlign: 'right', fontWeight: 600 }}>{formatNumber(l.qty, 0)}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                ))
            ) : (
                <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid var(--color-border)', color: 'var(--color-muted)', textAlign: 'right' }}>
                                <th style={{ padding: '8px 12px', textAlign: 'left' }}>Упаковка</th>
                                <th style={{ padding: '8px 12px', textAlign: 'left' }}>WB-склад</th>
                                <th style={{ padding: '8px 12px', textAlign: 'left' }}>Товар</th>
                                <th style={{ padding: '8px 12px', textAlign: 'left' }}>ШК</th>
                                <th style={{ padding: '8px 12px' }}>Коробов</th>
                                <th style={{ padding: '8px 12px' }}>Штук</th>
                            </tr>
                        </thead>
                        <tbody>
                            {lines.slice().sort((a, b) => a.wbName.localeCompare(b.wbName, 'ru') || b.qty - a.qty).map((l, i) => (
                                <tr key={`${l.barcode}-${l.wbName}-${i}`} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                    <td style={{ padding: '6px 12px' }}>{PKG_EMOJI[l.pkg]} {PKG_LABEL_RU[l.pkg] ?? l.pkg}</td>
                                    <td style={{ padding: '6px 12px' }}>{l.wbName}</td>
                                    <td style={{ padding: '6px 12px' }}>{l.vendor}</td>
                                    <td style={{ padding: '6px 12px', color: 'var(--color-muted)' }}>{l.barcode}</td>
                                    <td style={{ padding: '6px 12px', textAlign: 'right' }}>{formatNumber(boxesOf(l.qty, nmPpb.get(l.nmId)), 0)}</td>
                                    <td style={{ padding: '6px 12px', textAlign: 'right', fontWeight: 600 }}>{formatNumber(l.qty, 0)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
