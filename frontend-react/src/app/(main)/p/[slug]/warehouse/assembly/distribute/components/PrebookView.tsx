'use client';

import { useEffect, useMemo, useState } from 'react';
import { formatNumber } from '@/lib/utils';
import type { PackageType } from '@/types/api';

/** Одна позиция предброни (SKU × ФФ-источник) в рамках направления. */
export interface PrebookRowItem {
    nm_id: number;
    vendor_code: string;
    ff: string;              // имя ФФ-склада-источника
    boxes: number;
    qty: number;
}

/** Возможность дозабора направления (минимально, с одного ФФ). */
export interface PrebookTopUp {
    ff: string;              // с какого ФФ добираем
    needBoxes: number;       // сколько коробов не хватает до целых паллет
    pallets: number;         // сколько целых паллет соберётся
    /** Чем именно дозаберётся (предварительно, до проверки приёмки при клике). */
    candidates: { vendor: string; boxes: number }[];
}

/** Группа предброни = одна ОТГРУЗКА: направление × ФФ-источник × упаковка.
 *  Паллета собирается с ОДНОГО ФФ (короб с двух складов не собрать) — поэтому
 *  заполнение и дозабор считаются per-ФФ, а не по всему направлению. */
export interface PrebookGroup {
    pkg: PackageType;
    wb: string;
    ff: string;              // имя ФФ-склада-источника
    ffId: number;
    items: PrebookRowItem[];
    boxes: number;
    qty: number;
    /** Полный footprint в паллетах (≥1 = уже собрана целая паллета + хвост). */
    footprint: number;
    /** Заполнение паллеты 0..1 (доля объёма от целой). */
    fillPct: number;
    /** Дозабор возможен → инфо; иначе null (нечем). */
    topUp: PrebookTopUp | null;
}

interface Props {
    groups: PrebookGroup[];
    toppingUpKey: string | null;   // `${pkg}::${wb}::${ffId}` пока идёт дозабор
    shipAsIsKey: string | null;    // `${pkg}::${wb}::${ffId}` пока идёт отгрузка «как есть»
    onTopUp: (pkg: PackageType, wb: string, ffId: number) => void;
    /** Отгрузить неполную паллету направления в черновик как есть (без дозабора). */
    onShipAsIs: (pkg: PackageType, wb: string, ffId: number) => void;
    onDelete: (nm_id: number, wb: string, pkg: PackageType) => void;
}

const PKG_LABEL: Record<string, string> = { BOX: 'Короб', MONOPALLET: 'Моно', SUPERSAFE: 'Сейф' };

/** Порог готовности паллеты (%) — паллета «готова» к отгрузке при заполнении ≥ порога;
 *  ниже — подсвечиваем. Ред-мое, хранится локально (одно значение на всю предбронь). */
const THRESHOLD_KEY = 'dds.prebook.readyThresholdPct';
const DEFAULT_THRESHOLD = 60;

export default function PrebookView({ groups, toppingUpKey, shipAsIsKey, onTopUp, onShipAsIs, onDelete }: Props) {
    const pkgsPresent = useMemo(() => {
        const order: PackageType[] = ['BOX', 'MONOPALLET', 'SUPERSAFE'];
        return order.filter(p => groups.some(g => g.pkg === p));
    }, [groups]);
    const [pkg, setPkg] = useState<PackageType>('BOX');
    const activePkg = pkgsPresent.includes(pkg) ? pkg : (pkgsPresent[0] ?? 'BOX');

    // Порог готовности: ред-мое поле, персист в localStorage (клиент).
    const [threshold, setThreshold] = useState<number>(DEFAULT_THRESHOLD);
    useEffect(() => {
        const raw = typeof window !== 'undefined' ? window.localStorage.getItem(THRESHOLD_KEY) : null;
        const n = raw != null ? Number(raw) : NaN;
        if (Number.isFinite(n) && n >= 0 && n <= 100) setThreshold(n);
    }, []);
    const updateThreshold = (v: number) => {
        const clamped = Math.max(0, Math.min(100, Math.round(v)));
        setThreshold(clamped);
        if (typeof window !== 'undefined') window.localStorage.setItem(THRESHOLD_KEY, String(clamped));
    };

    const shown = useMemo(
        () => groups.filter(g => g.pkg === activePkg).sort((a, b) => b.fillPct - a.fillPct),
        [groups, activePkg],
    );
    const totals = useMemo(() => {
        const g = groups;
        const canDirs = g.filter(x => x.topUp).length;
        const resultPallets = g.reduce((s, x) => s + (x.topUp?.pallets || 0), 0);
        // «Ниже порога» = направление БЕЗ ни одной целой паллеты, где единственная
        // неполная паллета заполнена меньше порога (целую паллету «слишком пусто» не зовём).
        const lowDirs = g.filter(x => {
            const whole = Math.floor(x.footprint + 1e-9);
            return whole === 0 && x.footprint > 0 && Math.round(x.footprint * 100) < threshold;
        }).length;
        return {
            boxes: g.reduce((s, x) => s + x.boxes, 0),
            qty: g.reduce((s, x) => s + x.qty, 0),
            dirs: g.length,
            canDirs,
            noDirs: g.length - canDirs,
            resultPallets,
            lowDirs,
        };
    }, [groups, threshold]);

    if (groups.length === 0) {
        return (
            <div className="glass-card">
                <div className="empty-state">
                    <div className="empty-state-text">
                        Предбронь пуста. Сюда попадают целые коробы, не собравшие паллету, при «Заполнить черновик из потребности».
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div>
            {/* KPI */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 16 }}>
                <div className="glass-card" style={{ padding: '14px 16px' }}>
                    <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>В предброни</div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>{formatNumber(totals.boxes, 0)} кор</div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{formatNumber(totals.qty, 0)} шт · {formatNumber(totals.dirs, 0)} напр.</div>
                </div>
                <div className="glass-card" style={{ padding: '14px 16px', borderLeft: '3px solid var(--color-success)' }}>
                    <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>Можно дозабрать</div>
                    <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--color-success)' }}>{formatNumber(totals.canDirs, 0)} напр.</div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>соберётся {formatNumber(totals.resultPallets, 0)} паллет</div>
                </div>
                <div className="glass-card" style={{ padding: '14px 16px' }}>
                    <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>Нечем дозабрать</div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>{formatNumber(totals.noDirs, 0)} напр.</div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>нет свободного ФФ</div>
                </div>
            </div>

            {/* Порог готовности + под-вкладки упаковки */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
                <div style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap' }}>
                    {pkgsPresent.map(p => (
                        <button key={p}
                            className={`btn btn-sm ${p === activePkg ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setPkg(p)}>
                            {PKG_LABEL[p] || p} · {groups.filter(g => g.pkg === p).length} напр.
                        </button>
                    ))}
                </div>
                <label style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text-muted)' }}>
                    Паллета готова при ≥
                    <input
                        type="number" min={0} max={100} value={threshold}
                        onChange={e => updateThreshold(Number(e.target.value))}
                        style={{ width: 56, padding: '3px 6px', borderRadius: 8, border: '1px solid var(--color-border)', background: 'var(--color-bg-card)', color: 'var(--color-text)', textAlign: 'right', fontSize: 13 }}
                    />
                    %
                    {totals.lowDirs > 0 && (
                        <span style={{ color: 'var(--color-warning)', fontWeight: 600 }}>· ниже: {formatNumber(totals.lowDirs, 0)}</span>
                    )}
                </label>
            </div>

            {/* Карточки направлений */}
            {shown.map(g => {
                const key = `${g.pkg}::${g.wb}::${g.ffId}`;
                const busyTop = toppingUpKey === key;
                const busyShip = shipAsIsKey === key;
                const busy = busyTop || busyShip;
                // Полный footprint: целые паллеты (уже собраны) + дробь последней.
                const whole = Math.floor(g.footprint + 1e-9);
                const fracPct = Math.max(0, Math.min(100, Math.round((g.footprint - whole) * 100)));
                const low = whole === 0 && g.footprint > 0 && Math.round(g.footprint * 100) < threshold;
                const fillLabel = whole >= 1
                    ? `${formatNumber(whole, 0)} ${whole === 1 ? 'целая паллета' : 'целых паллеты'} + ${fracPct}% ещё одной`
                    : `заполнено ${fracPct}% паллеты`;
                return (
                    <div key={key} className="glass-card"
                        style={{ padding: '14px 16px', marginBottom: 12, borderLeft: low ? '3px solid var(--color-warning)' : undefined }}>
                        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
                            <span style={{ fontSize: 15, fontWeight: 700 }}>→ {g.wb}</span>
                            <span style={{ fontSize: 12, padding: '2px 8px', borderRadius: 6, background: 'rgba(148,163,184,0.15)', color: 'var(--color-text-muted)' }}>ФФ: {g.ff}</span>
                            <span style={{ fontSize: 12, padding: '2px 8px', borderRadius: 6, background: 'rgba(59,130,246,0.10)', color: 'var(--color-accent)' }}>{PKG_LABEL[g.pkg] || g.pkg}</span>
                            <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>{formatNumber(g.boxes, 0)} кор · {formatNumber(g.qty, 0)} шт{whole >= 1 ? ` · ≈${g.footprint.toFixed(1)} пал` : ''}</span>
                            {low && (
                                <span style={{ fontSize: 12, fontWeight: 600, padding: '2px 8px', borderRadius: 6, background: 'rgba(245,158,11,0.15)', color: 'var(--color-warning)' }}>⚠ ниже порога</span>
                            )}
                            <span style={{ marginLeft: 'auto', fontSize: 13, fontWeight: (low || whole >= 1) ? 700 : 400, color: low ? 'var(--color-warning)' : whole >= 1 ? 'var(--color-success)' : 'var(--color-text-muted)' }}>{fillLabel}</span>
                        </div>
                        {/* Сегментированный бар: N целых зелёных паллет + последняя неполная. */}
                        <div style={{ display: 'flex', gap: 3, height: 6, marginBottom: 10 }}>
                            {Array.from({ length: Math.min(whole, 8) }).map((_, i) => (
                                <div key={`full-${i}`} style={{ flex: 1, background: 'var(--color-success)', borderRadius: 3 }} title="целая паллета" />
                            ))}
                            {(fracPct > 0 || whole === 0) && (
                                <div style={{ flex: 1, background: 'rgba(148,163,184,0.20)', borderRadius: 3, overflow: 'hidden' }} title="неполная паллета">
                                    <div style={{ width: `${fracPct}%`, height: '100%', background: low ? 'var(--color-warning)' : 'var(--color-success)' }} />
                                </div>
                            )}
                        </div>

                        {g.topUp ? (
                            <div style={{ padding: '8px 10px', background: 'rgba(34,197,94,0.10)', borderRadius: 8, marginBottom: 10 }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                                    <span style={{ fontSize: 13, color: 'var(--color-success)' }}>
                                        Не хватает <b>{formatNumber(g.topUp.needBoxes, 0)} кор</b> — есть в свободном ФФ <b>«{g.topUp.ff}»</b>. Дозабор → <b>{formatNumber(g.topUp.pallets, 0)} целых паллет</b>.
                                    </span>
                                    <div style={{ marginLeft: 'auto', display: 'inline-flex', gap: 8, flexWrap: 'wrap' }}>
                                        <button className="btn btn-secondary btn-sm" disabled={busy}
                                            onClick={() => onShipAsIs(g.pkg, g.wb, g.ffId)}>
                                            {busyShip ? '…' : '📦 Оставить так'}
                                        </button>
                                        <button className="btn btn-success btn-sm" disabled={busy}
                                            onClick={() => onTopUp(g.pkg, g.wb, g.ffId)}>
                                            {busyTop ? '…' : `🧩 Дозабить из «${g.topUp.ff}»`}
                                        </button>
                                    </div>
                                </div>
                                {g.topUp.candidates.length > 0 && (
                                    <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'baseline' }}>
                                        <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>дозаберётся:</span>
                                        {g.topUp.candidates.map((c, i) => (
                                            <span key={`${c.vendor}-${i}`} style={{ fontSize: 11, padding: '1px 7px', borderRadius: 5, background: 'rgba(34,197,94,0.15)', color: 'var(--color-success)' }}>
                                                {c.vendor} ×{formatNumber(c.boxes, 0)} кор
                                            </span>
                                        ))}
                                        <span style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>· приёмка проверится при клике</span>
                                    </div>
                                )}
                            </div>
                        ) : (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px', background: 'rgba(245,158,11,0.10)', borderRadius: 8, marginBottom: 10, flexWrap: 'wrap' }}>
                                <span style={{ fontSize: 13, color: 'var(--color-warning)' }}>
                                    Нет свободного ФФ на это направление — дозабрать нечем. Отгрузить как есть или удалить.
                                </span>
                                <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} disabled={busy}
                                    onClick={() => onShipAsIs(g.pkg, g.wb, g.ffId)}>
                                    {busyShip ? '…' : '📦 Оставить так'}
                                </button>
                            </div>
                        )}

                        <div style={{ overflowX: 'auto' }}>
                            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                                <thead>
                                    <tr style={{ textAlign: 'left', color: 'var(--color-text-muted)', fontSize: 11 }}>
                                        <th style={{ padding: '3px 6px' }}>Артикул</th>
                                        <th style={{ padding: '3px 6px', textAlign: 'right' }}>Коробов</th>
                                        <th style={{ padding: '3px 6px', textAlign: 'right' }}>Шт</th>
                                        <th style={{ padding: '3px 6px' }} />
                                    </tr>
                                </thead>
                                <tbody>
                                    {g.items.map((it, idx) => (
                                        <tr key={`${it.nm_id}-${idx}`} style={{ borderTop: '1px solid var(--color-border)' }}>
                                            <td style={{ padding: '5px 6px' }}>{it.vendor_code || `nm ${it.nm_id}`}</td>
                                            <td style={{ padding: '5px 6px', textAlign: 'right', fontWeight: 600 }}>{formatNumber(it.boxes, 0)}</td>
                                            <td style={{ padding: '5px 6px', textAlign: 'right' }}>{formatNumber(it.qty, 0)}</td>
                                            <td style={{ padding: '5px 6px', textAlign: 'right' }}>
                                                <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px' }}
                                                    title="Убрать из предброни — коробы останутся на ФФ"
                                                    onClick={() => onDelete(it.nm_id, g.wb, g.pkg)}>✕</button>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                );
            })}
        </div>
    );
}
