'use client';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { DayPicker } from 'react-day-picker';
import type { DateRange } from 'react-day-picker';
import { ru } from 'date-fns/locale';
import { subDays, subMonths } from 'date-fns';
import 'react-day-picker/style.css';

function ymd(d: Date): string {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
}
function fromIso(iso: string): Date | undefined {
    if (!iso) return undefined;
    const [y, m, d] = iso.split('-').map(Number);
    if (!y || !m || !d) return undefined;
    return new Date(y, m - 1, d);
}
function fmtRu(iso: string): string {
    const d = fromIso(iso);
    if (!d) return '';
    return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
}
// dd.mm.yyyy → ISO (для ручного ввода в нижних полях)
function parseRu(s: string): Date | undefined {
    const m = s.trim().match(/^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})$/);
    if (!m) return undefined;
    const d = Number(m[1]), mo = Number(m[2]); let y = Number(m[3]);
    if (y < 100) y += 2000;
    if (mo < 1 || mo > 12 || d < 1 || d > 31) return undefined;
    return new Date(y, mo - 1, d);
}

/** Пресеты периода (как в референсе). Возвращают [from, to] относительно сегодня. */
function preset(kind: string): DateRange {
    const now = new Date();
    switch (kind) {
        case 'today': return { from: now, to: now };
        case 'yesterday': { const y = subDays(now, 1); return { from: y, to: y }; }
        case '30d': return { from: subDays(now, 29), to: now };
        case '3m': return { from: subMonths(now, 3), to: now };
        case '6m': return { from: subMonths(now, 6), to: now };
        case '12m': return { from: subMonths(now, 12), to: now };
        default: return { from: now, to: now };
    }
}
const PRESETS: { key: string; label: string }[] = [
    { key: 'today', label: 'Сегодня' },
    { key: 'yesterday', label: 'Вчера' },
    { key: '30d', label: '30 дней' },
    { key: '3m', label: '3 месяца' },
    { key: '6m', label: '6 месяцев' },
    { key: '12m', label: '12 месяцев' },
];

interface Props {
    from: string;        // ISO yyyy-mm-dd, '' = не задано
    to: string;
    onApply: (from: string, to: string) => void;  // '' , '' = сброс фильтра
    placeholder?: string;
    minWidth?: number;
    align?: 'left' | 'right';  // сторона раскрытия попапа (right — когда пикер у правого края)
}

/** Пикер периода как в референсе: 2 месяца, пресеты справа, ручной ввод дд.мм.гггг, «Сбросить/Готово».
 *  Поддерживает пустое состояние (для фильтра «Дата добавления»). */
export default function AdsPeriodPicker({ from, to, onApply, placeholder = 'Выберите период', minWidth = 210, align = 'left' }: Props) {
    const [open, setOpen] = useState(false);
    const wrapRef = useRef<HTMLDivElement>(null);
    const [pending, setPending] = useState<DateRange | undefined>(() => {
        const f = fromIso(from); const t = fromIso(to);
        return f ? { from: f, to: t ?? f } : undefined;
    });
    const [fromText, setFromText] = useState(fmtRu(from));
    const [toText, setToText] = useState(fmtRu(to));

    useEffect(() => {
        const f = fromIso(from); const t = fromIso(to);
        setPending(f ? { from: f, to: t ?? f } : undefined);
        setFromText(fmtRu(from)); setToText(fmtRu(to));
    }, [from, to]);

    useEffect(() => {
        if (!open) return;
        const onDoc = (e: MouseEvent) => { if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false); };
        document.addEventListener('mousedown', onDoc);
        return () => document.removeEventListener('mousedown', onDoc);
    }, [open]);

    const setRange = (r: DateRange | undefined) => {
        setPending(r);
        setFromText(r?.from ? r.from.toLocaleDateString('ru-RU') : '');
        setToText(r?.to ? r.to.toLocaleDateString('ru-RU') : '');
    };

    const apply = () => {
        if (!pending?.from) { onApply('', ''); setOpen(false); return; }
        onApply(ymd(pending.from), ymd(pending.to ?? pending.from));
        setOpen(false);
    };
    const clear = () => { setRange(undefined); onApply('', ''); setOpen(false); };

    const defaultMonth = useMemo(() => pending?.from ?? new Date(), [pending]);
    const hasValue = !!(from && to);

    return (
        <div className="apk-wrap" ref={wrapRef} style={{ minWidth }}>
            <button type="button" className="apk-trigger" onClick={() => setOpen(!open)} aria-expanded={open}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden>
                    <rect x="3" y="5" width="18" height="16" rx="2" stroke="currentColor" strokeWidth="1.6" />
                    <path d="M3 9h18M8 3v4M16 3v4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                </svg>
                <span className={hasValue ? 'apk-dates' : 'apk-ph'}>{hasValue ? `${fmtRu(from)} — ${fmtRu(to)}` : placeholder}</span>
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" aria-hidden style={{ marginLeft: 'auto' }}>
                    <path d="M7 10l5 5 5-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
            </button>
            {open && (
                <div className="apk-pop" role="dialog" style={align === 'right' ? { left: 'auto', right: 0 } : undefined}>
                    <div className="apk-body">
                        <DayPicker mode="range" selected={pending} onSelect={setRange} defaultMonth={defaultMonth}
                            locale={ru} weekStartsOn={1} numberOfMonths={2} />
                        <div className="apk-presets">
                            {PRESETS.map(p => (
                                <button key={p.key} type="button" className="apk-preset" onClick={() => setRange(preset(p.key))}>{p.label}</button>
                            ))}
                        </div>
                    </div>
                    <div className="apk-footer">
                        <span className="apk-manual">
                            <input value={fromText} placeholder="дд.мм.гггг" onChange={e => setFromText(e.target.value)}
                                onBlur={() => { const d = parseRu(fromText); if (d) setPending(prev => ({ from: d, to: prev?.to ?? d })); }} />
                            <span className="apk-dash">—</span>
                            <input value={toText} placeholder="дд.мм.гггг" onChange={e => setToText(e.target.value)}
                                onBlur={() => { const d = parseRu(toText); if (d) setPending(prev => ({ from: prev?.from ?? d, to: d })); }} />
                        </span>
                        <span className="apk-actions">
                            <button type="button" className="btn btn-secondary btn-sm" onClick={clear}>Сбросить</button>
                            <button type="button" className="btn btn-primary btn-sm" onClick={apply}>Готово</button>
                        </span>
                    </div>
                </div>
            )}
            <style>{`
                .apk-wrap { position: relative; display: inline-block; }
                .apk-trigger { display: inline-flex; align-items: center; gap: 8px; width: 100%;
                    padding: 6px 12px; border-radius: 10px; border: 1px solid var(--color-border);
                    background: var(--color-bg-card); color: var(--color-text); font-size: 13px; font-weight: 500;
                    cursor: pointer; font-variant-numeric: tabular-nums; transition: border-color .15s, box-shadow .15s; }
                .apk-trigger:hover { border-color: var(--color-accent); }
                .apk-trigger[aria-expanded="true"] { border-color: var(--color-accent); box-shadow: 0 0 0 3px rgba(0,113,227,.1); }
                .apk-ph { color: var(--color-text-muted); }
                .apk-pop { position: absolute; top: calc(100% + 6px); left: 0; z-index: 200;
                    background: #fff; border: 1px solid var(--color-border); border-radius: 14px;
                    box-shadow: 0 16px 48px rgba(0,0,0,.16); padding: 12px; }
                .apk-body { display: flex; gap: 12px; align-items: flex-start; }
                .apk-presets { display: flex; flex-direction: column; gap: 6px; padding-left: 12px;
                    border-left: 1px solid var(--color-border); min-width: 116px; }
                .apk-preset { padding: 7px 12px; border-radius: 10px; border: 1px solid var(--color-border);
                    background: transparent; color: var(--color-text); font-size: 13px; cursor: pointer; text-align: center;
                    transition: all .15s; }
                .apk-preset:hover { border-color: var(--color-accent); color: var(--color-accent); background: rgba(0,113,227,.05); }
                .apk-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px;
                    margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--color-border); }
                .apk-manual { display: inline-flex; align-items: center; gap: 8px; }
                .apk-manual input { width: 108px; padding: 6px 8px; border: 1px solid var(--color-border); border-radius: 8px;
                    font-size: 13px; color: var(--color-text); background: var(--color-bg-card); font-variant-numeric: tabular-nums; }
                .apk-dash { color: var(--color-text-muted); }
                .apk-actions { display: inline-flex; gap: 8px; }
                .apk-pop .rdp-root { --rdp-accent-color: var(--color-accent); --rdp-accent-background-color: rgba(0,113,227,.12);
                    --rdp-day-height: 34px; --rdp-day-width: 34px; --rdp-font-family: inherit; --rdp-nav-height: 30px; margin: 0; }
                .apk-pop .rdp-months { display: flex !important; flex-direction: row !important; gap: 20px; flex-wrap: nowrap; }
                .apk-pop .rdp-month_caption { font-size: 14px; font-weight: 600; padding: 4px 0; text-transform: uppercase; }
                .apk-pop .rdp-weekday { font-size: 12px; font-weight: 500; color: var(--color-text-muted); }
                .apk-pop .rdp-day_button { color: var(--color-text); font-size: 13px; border-radius: 8px; width: 34px; height: 34px; padding: 0; }
                .apk-pop .rdp-selected .rdp-day_button { background: var(--color-accent) !important; color: #fff !important; }
                .apk-pop .rdp-range_middle .rdp-day_button { background: rgba(0,113,227,.14) !important; color: var(--color-text) !important; }
                .apk-pop .rdp-outside { opacity: .35; }
            `}</style>
        </div>
    );
}
