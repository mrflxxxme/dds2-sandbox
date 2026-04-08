'use client';

import { useState, useRef, useEffect } from 'react';
import { api } from '@/lib/api';

/* ─── Utility functions (exported for reuse) ──────────────────────── */

/** Compute box breakdown: [28, 28, 28, 24] for qty=108, pcsPerBox=28 */
export function autoBoxDetail(qty: number, pcsPerBox: number): number[] {
    if (pcsPerBox <= 0 || qty <= 0) return [];
    const full = Math.floor(qty / pcsPerBox);
    const remainder = qty % pcsPerBox;
    const result: number[] = Array(full).fill(pcsPerBox);
    if (remainder > 0) result.push(remainder);
    return result;
}

/** Format: "7×28 + 1×24" */
export function formatBoxBreakdown(detail: number[]): string {
    if (!detail.length) return '';
    const groups: { count: number; size: number }[] = [];
    for (const size of detail) {
        const last = groups[groups.length - 1];
        if (last && last.size === size) last.count++;
        else groups.push({ count: 1, size });
    }
    return groups.map(g => `${g.count}×${g.size}`).join(' + ');
}

/* ─── Clickable cell ──────────────────────────────────────────────── */

interface BoxDetailCellProps {
    qty: number;
    pcsPerBox?: number;
    boxDetail?: number[] | null;
    expanded?: boolean;
    onToggle?: () => void;
}

/** Clickable "Мест" cell — shows box count, click to expand. */
export default function BoxDetailCell({ qty, pcsPerBox, boxDetail, expanded, onToggle }: BoxDetailCellProps) {
    const ppb = pcsPerBox || 0;
    const boxes = ppb > 0 ? Math.ceil(qty / ppb) : 0;
    const notFull = ppb > 0 && qty % ppb !== 0;

    if (boxes <= 0) return <span>—</span>;

    return (
        <span
            onClick={onToggle}
            style={{
                cursor: onToggle ? 'pointer' : undefined,
                color: notFull ? 'var(--color-warning)' : 'inherit',
                fontWeight: notFull ? 600 : 400,
                textDecoration: onToggle ? 'underline' : undefined,
                textDecorationStyle: 'dotted' as const,
                textUnderlineOffset: 3,
            }}
            title="Показать разбивку по коробкам"
        >
            {boxes}{notFull && ' \u26a0'}{onToggle && (expanded ? ' \u25b4' : ' \u25be')}
        </span>
    );
}

/* ─── Expand row (rendered by parent as <tr>) ─────────────────────── */

interface BoxDetailExpandRowProps {
    qty: number;
    pcsPerBox: number;
    boxDetail?: number[] | null;
    colSpan: number;
    /** If set, show edit button */
    editable?: boolean;
    orderId?: number;
    itemId?: number;
    onSaved?: () => void;
}

/** Expanded row showing box breakdown. Render inside <tr>. */
export function BoxDetailExpandRow({
    qty, pcsPerBox, boxDetail, colSpan, editable, orderId, itemId, onSaved,
}: BoxDetailExpandRowProps) {
    const [editing, setEditing] = useState(false);
    const detail = boxDetail ?? autoBoxDetail(qty, pcsPerBox);
    const breakdown = formatBoxBreakdown(detail);

    if (editing && editable && orderId && itemId) {
        return (
            <td colSpan={colSpan} style={{ padding: '12px 16px', background: 'rgba(59,130,246,0.03)' }}>
                <BoxDetailEditor
                    qty={qty}
                    pcsPerBox={pcsPerBox}
                    boxDetail={boxDetail ?? null}
                    orderId={orderId}
                    itemId={itemId}
                    onClose={() => setEditing(false)}
                    onSaved={() => { setEditing(false); onSaved?.(); }}
                />
            </td>
        );
    }

    return (
        <td colSpan={colSpan} style={{ padding: '8px 16px', background: 'rgba(59,130,246,0.03)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 13 }}>
                <span style={{ color: 'var(--color-text-muted)', fontWeight: 500 }}>Коробки:</span>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {detail.map((count, i) => (
                        <span key={i} style={{
                            padding: '2px 8px',
                            borderRadius: 8,
                            background: count === pcsPerBox ? 'rgba(52,199,89,0.1)' : 'rgba(255,159,10,0.1)',
                            color: count === pcsPerBox ? 'var(--color-success)' : 'var(--color-warning)',
                            fontSize: 12,
                            fontWeight: 600,
                        }}>
                            {count} шт
                        </span>
                    ))}
                </div>
                <span style={{ color: 'var(--color-text-muted)', fontSize: 12 }}>
                    = {breakdown}
                </span>
                {boxDetail && (
                    <span className="badge badge-info" style={{ fontSize: 10, padding: '1px 6px' }}>ручн.</span>
                )}
                {editable && orderId && itemId && (
                    <button
                        onClick={() => setEditing(true)}
                        className="btn btn-secondary btn-sm"
                        style={{ marginLeft: 'auto', fontSize: 11, padding: '2px 10px' }}
                    >
                        Изменить
                    </button>
                )}
            </div>
        </td>
    );
}

/* ─── Inline Editor (inside expand row) ───────────────────────────── */

function BoxDetailEditor({ qty, pcsPerBox, boxDetail, orderId, itemId, onClose, onSaved }: {
    qty: number;
    pcsPerBox: number;
    boxDetail: number[] | null;
    orderId: number;
    itemId: number;
    onClose: () => void;
    onSaved: () => void;
}) {
    const auto = pcsPerBox > 0 ? autoBoxDetail(qty, pcsPerBox) : [qty];
    const [boxes, setBoxes] = useState<number[]>(boxDetail ?? auto);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const total = boxes.reduce((s, v) => s + v, 0);
    const isValid = total === qty && boxes.every(v => v > 0);

    const updateBox = (idx: number, val: number) =>
        setBoxes(prev => prev.map((v, i) => i === idx ? val : v));

    const handleSave = async () => {
        if (!isValid) return;
        setSaving(true);
        setError(null);
        try {
            const isAuto = JSON.stringify(boxes) === JSON.stringify(auto);
            await api.updateFactoryOrderItem(orderId, itemId, {
                box_detail: isAuto ? null : boxes,
            });
            onSaved();
        } catch (e: any) {
            setError(e?.message || 'Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    return (
        <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <span style={{ fontWeight: 600, fontSize: 13 }}>Разбивка по коробкам</span>
                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                    Всего: {qty} шт, по {pcsPerBox} шт/кор
                </span>
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                {boxes.map((val, idx) => (
                    <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                        <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{idx + 1}.</span>
                        <input
                            type="number"
                            min={1}
                            value={val || ''}
                            onChange={e => updateBox(idx, parseInt(e.target.value) || 0)}
                            style={{
                                width: 56, padding: '3px 6px', borderRadius: 8, textAlign: 'right',
                                border: '1px solid var(--color-border)', background: 'var(--color-bg)', fontSize: 13,
                            }}
                        />
                        {boxes.length > 1 && (
                            <button
                                onClick={() => setBoxes(prev => prev.filter((_, i) => i !== idx))}
                                style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'var(--color-danger)', fontSize: 14, padding: 0 }}
                            >&times;</button>
                        )}
                    </div>
                ))}
                <button onClick={() => setBoxes(prev => [...prev, 0])} className="btn btn-secondary btn-sm" style={{ fontSize: 11, padding: '2px 8px' }}>+</button>
                <button onClick={() => { setBoxes(auto); setError(null); }} className="btn btn-secondary btn-sm" style={{ fontSize: 11, padding: '2px 8px' }}>Авто</button>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 8 }}>
                <span style={{
                    fontSize: 12, fontWeight: 600,
                    color: isValid ? 'var(--color-success)' : 'var(--color-danger)',
                }}>
                    Сумма: {total} / {qty}
                    {!isValid && total !== qty && ` (${total > qty ? '+' : ''}${total - qty})`}
                </span>
                {error && <span style={{ fontSize: 12, color: 'var(--color-danger)' }}>{error}</span>}
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                    <button onClick={onClose} className="btn btn-secondary btn-sm" style={{ fontSize: 11 }}>Отмена</button>
                    <button onClick={handleSave} disabled={!isValid || saving} className="btn btn-primary btn-sm"
                        style={{ fontSize: 11, opacity: isValid ? 1 : 0.5 }}>
                        {saving ? '...' : 'Сохранить'}
                    </button>
                </div>
            </div>
        </div>
    );
}

/* ─── Dropdown (click → editable box list) ────────────────────────── */

interface BoxDropdownProps {
    qty: number;
    pcsPerBox: number;
    boxDetail?: number[] | null;
    /** If set, enables save to API */
    orderId?: number;
    itemId?: number;
    onSaved?: () => void;
}

/** Clickable "Мест" with dropdown: view + edit box breakdown. */
export function BoxDropdown({ qty, pcsPerBox, boxDetail, orderId, itemId, onSaved }: BoxDropdownProps) {
    const [open, setOpen] = useState(false);
    const ref = useRef<HTMLSpanElement>(null);

    const numBoxes = pcsPerBox > 0 ? Math.ceil(qty / pcsPerBox) : 0;
    const notFull = pcsPerBox > 0 && qty % pcsPerBox !== 0;

    useEffect(() => {
        if (!open) return;
        const handler = (e: MouseEvent) => {
            if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, [open]);

    if (numBoxes <= 0 || qty <= 0) return <span>—</span>;

    return (
        <span ref={ref} style={{ position: 'relative', display: 'inline-block' }}>
            <span
                onClick={() => setOpen(!open)}
                style={{
                    cursor: 'pointer',
                    color: notFull ? 'var(--color-warning)' : 'var(--color-text)',
                    fontWeight: notFull ? 600 : 400,
                    textDecoration: 'underline',
                    textDecorationStyle: 'dotted' as const,
                    textUnderlineOffset: 3,
                }}
            >
                {numBoxes}{notFull && ' \u26a0'}
            </span>
            {open && (
                <DropdownEdit
                    qty={qty}
                    pcsPerBox={pcsPerBox}
                    boxDetail={boxDetail ?? null}
                    orderId={orderId}
                    itemId={itemId}
                    onSaved={() => { setOpen(false); onSaved?.(); }}
                    onCancel={() => setOpen(false)}
                />
            )}
        </span>
    );
}

/* ─── Dropdown: Edit mode ─────────────────────────────────────────── */

function DropdownEdit({ qty, pcsPerBox, boxDetail, orderId, itemId, onSaved, onCancel }: {
    qty: number; pcsPerBox: number; boxDetail: number[] | null;
    orderId?: number; itemId?: number;
    onSaved: () => void; onCancel: () => void;
}) {
    const auto = pcsPerBox > 0 ? autoBoxDetail(qty, pcsPerBox) : [qty];
    const [boxes, setBoxes] = useState<number[]>(boxDetail ?? auto);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const total = boxes.reduce((s, v) => s + v, 0);
    const isValid = total === qty && boxes.every(v => v > 0);
    const canSave = !!orderId && !!itemId;

    const updateBox = (idx: number, val: number) =>
        setBoxes(prev => prev.map((v, i) => i === idx ? val : v));

    const handleSave = async () => {
        if (!isValid || !canSave) return;
        setSaving(true);
        setError(null);
        try {
            const isAuto = JSON.stringify(boxes) === JSON.stringify(auto);
            await api.updateFactoryOrderItem(orderId!, itemId!, { box_detail: isAuto ? null : boxes });
            onSaved();
        } catch (e: any) {
            setError(e?.message || 'Ошибка');
        } finally {
            setSaving(false);
        }
    };

    return (
        <div style={{
            position: 'absolute', top: '100%', right: 0, marginTop: 4,
            padding: '10px 14px',
            background: 'var(--color-bg-card)', backdropFilter: 'blur(24px) saturate(180%)',
            border: '1px solid var(--color-border)', borderRadius: 12,
            boxShadow: '0 8px 32px rgba(0,0,0,0.12)', zIndex: 50,
            minWidth: 200, fontSize: 12,
        }}>
            <div style={{ fontWeight: 600, color: 'var(--color-text-muted)', fontSize: 11, marginBottom: 6 }}>
                Редактирование ({qty} шт, по {pcsPerBox})
            </div>
            <div style={{ maxHeight: 220, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 4 }}>
                {boxes.map((val, idx) => (
                    <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ color: 'var(--color-text-muted)', width: 42, fontSize: 11 }}>Кор {idx + 1}</span>
                        <input
                            type="number" min={1} value={val || ''}
                            onChange={e => updateBox(idx, parseInt(e.target.value) || 0)}
                            style={{
                                flex: 1, padding: '4px 6px', borderRadius: 8, textAlign: 'right',
                                border: `1px solid ${val === pcsPerBox ? 'var(--color-success)' : val > 0 ? 'var(--color-warning)' : 'var(--color-danger)'}`,
                                background: 'var(--color-bg)', fontSize: 12, width: 60,
                            }}
                        />
                        <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>шт</span>
                        {boxes.length > 1 && (
                            <button onClick={() => setBoxes(prev => prev.filter((_, i) => i !== idx))}
                                style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'var(--color-danger)', fontSize: 13, padding: 0 }}>
                                &times;
                            </button>
                        )}
                    </div>
                ))}
            </div>
            <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                <button onClick={() => setBoxes(prev => [...prev, 0])} className="btn btn-secondary btn-sm" style={{ fontSize: 10, padding: '2px 8px' }}>+ Кор</button>
                <button onClick={() => { setBoxes(auto); setError(null); }} className="btn btn-secondary btn-sm" style={{ fontSize: 10, padding: '2px 8px' }}>Авто</button>
            </div>
            <div style={{
                marginTop: 6, padding: '4px 8px', borderRadius: 8, fontSize: 11, fontWeight: 600,
                background: isValid ? 'rgba(52,199,89,0.08)' : 'rgba(255,59,48,0.08)',
                color: isValid ? 'var(--color-success)' : 'var(--color-danger)',
            }}>
                Сумма: {total} / {qty} {!isValid && total !== qty && `(${total > qty ? '+' : ''}${total - qty})`}
            </div>
            {error && <div style={{ fontSize: 11, color: 'var(--color-danger)', marginTop: 4 }}>{error}</div>}
            <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                <button onClick={onCancel} className="btn btn-secondary btn-sm" style={{ flex: 1, fontSize: 11 }}>Назад</button>
                {canSave && (
                    <button onClick={handleSave} disabled={!isValid || saving} className="btn btn-primary btn-sm"
                        style={{ flex: 1, fontSize: 11, opacity: isValid ? 1 : 0.5 }}>
                        {saving ? '...' : 'Сохранить'}
                    </button>
                )}
            </div>
        </div>
    );
}
