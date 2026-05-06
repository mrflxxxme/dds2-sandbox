'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { DayPicker } from 'react-day-picker';
import type { DateRange } from 'react-day-picker';
import { ru } from 'date-fns/locale';
import 'react-day-picker/style.css';

interface Props {
    /** ISO-даты (yyyy-mm-dd) с реальными данными — подсвечиваются точкой. */
    availableDates?: Set<string>;
    /** ISO-дата начала диапазона. */
    from: string;
    /** ISO-дата конца диапазона. */
    to: string;
    /** Колбэк: оба значения ISO. Триггерится при клике «Применить». */
    onApply: (from: string, to: string) => void;
}

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
    if (!d) return '—';
    return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

const PRESETS: { label: string; days: number }[] = [
    { label: '7 дней', days: 7 },
    { label: '14 дней', days: 14 },
    { label: '30 дней', days: 30 },
    { label: '91 день', days: 91 },
];

export default function DateRangePicker({ availableDates, from, to, onApply }: Props) {
    const [open, setOpen] = useState(false);
    const wrapRef = useRef<HTMLDivElement>(null);

    // Локальное состояние — то что пользователь набирает в открытом календаре.
    // Применяется в onApply только после клика «Применить».
    const [pending, setPending] = useState<DateRange | undefined>(() => {
        const f = fromIso(from);
        const t = fromIso(to);
        return f && t ? { from: f, to: t } : undefined;
    });

    // Когда внешние props меняются (закрыли popup без apply) — синхронизируем.
    useEffect(() => {
        const f = fromIso(from);
        const t = fromIso(to);
        setPending(f && t ? { from: f, to: t } : undefined);
    }, [from, to]);

    // Закрытие по клику вне.
    useEffect(() => {
        if (!open) return;
        const onDoc = (e: MouseEvent) => {
            if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
        };
        document.addEventListener('mousedown', onDoc);
        return () => document.removeEventListener('mousedown', onDoc);
    }, [open]);

    const apply = () => {
        if (!pending?.from) return;
        onApply(ymd(pending.from), ymd(pending.to ?? pending.from));
        setOpen(false);
    };

    const applyPreset = (days: number) => {
        const now = new Date();
        const f = new Date(now.getTime() - days * 86400000);
        setPending({ from: f, to: now });
    };

    const reset = () => {
        const f = fromIso(from);
        const t = fromIso(to);
        setPending(f && t ? { from: f, to: t } : undefined);
    };

    // Точка-индикатор «есть данные» под числом дня
    const modifiers = useMemo(
        () => ({
            hasData: (d: Date) => !!availableDates?.has(ymd(d)),
        }),
        [availableDates],
    );

    // Стартуем с месяца начала диапазона — чтобы пользователь сразу видел
    // оба конца (from-month + следующий = to-month при 2 numberOfMonths).
    const defaultMonth = useMemo(() => {
        if (pending?.from) return pending.from;
        if (pending?.to) return pending.to;
        return new Date();
    }, [pending]);

    return (
        <div className="drp2-wrap" ref={wrapRef}>
            <button
                type="button"
                className="drp2-trigger"
                onClick={() => setOpen(!open)}
                aria-expanded={open}
            >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
                    <rect x="3" y="5" width="18" height="16" rx="2" stroke="currentColor" strokeWidth="1.6" />
                    <path d="M3 9h18M8 3v4M16 3v4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                </svg>
                <span className="drp2-dates">
                    {fmtRu(from)} <span className="drp2-sep">—</span> {fmtRu(to)}
                </span>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden>
                    <path d="M7 10l5 5 5-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
            </button>
            {open && (
                <div className="drp2-pop" role="dialog">
                    <div className="drp2-presets">
                        {PRESETS.map(p => (
                            <button key={p.days} type="button" className="drp2-preset" onClick={() => applyPreset(p.days)}>
                                {p.label}
                            </button>
                        ))}
                    </div>
                    <DayPicker
                        mode="range"
                        selected={pending}
                        onSelect={setPending}
                        defaultMonth={defaultMonth}
                        locale={ru}
                        weekStartsOn={1}
                        numberOfMonths={2}
                        modifiers={modifiers}
                        modifiersClassNames={{ hasData: 'drp2-has-data' }}
                    />
                    <div className="drp2-footer">
                        {availableDates !== undefined && (
                            <span className="drp2-hint">
                                <span className="drp2-dot" /> — день с данными ({availableDates.size})
                            </span>
                        )}
                        <div className="drp2-actions">
                            <button type="button" className="btn btn-secondary btn-sm" onClick={reset}>
                                Сбросить
                            </button>
                            <button
                                type="button"
                                className="btn btn-primary btn-sm"
                                onClick={apply}
                                disabled={!pending?.from || !pending?.to}
                            >
                                Применить
                            </button>
                        </div>
                    </div>
                </div>
            )}
            <style>{`
                .drp2-wrap { position: relative; display: inline-block; }
                .drp2-trigger {
                    display: inline-flex; align-items: center; gap: 8px;
                    padding: 0.55rem 0.85rem; border-radius: 10px;
                    border: 1px solid var(--color-border);
                    background: var(--color-bg-card); color: var(--color-text);
                    font-size: 0.9rem; font-weight: 500; cursor: pointer;
                    font-variant-numeric: tabular-nums;
                    transition: border-color 0.15s, box-shadow 0.15s;
                }
                .drp2-trigger:hover { border-color: var(--color-accent); }
                .drp2-trigger[aria-expanded="true"] {
                    border-color: var(--color-accent);
                    box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.1);
                }
                .drp2-dates { display: inline-flex; gap: 6px; align-items: center; }
                .drp2-sep { color: var(--color-text-muted); }

                .drp2-pop {
                    position: absolute; top: calc(100% + 6px); left: 0; z-index: 50;
                    background: var(--color-bg-card);
                    border: 1px solid var(--color-border);
                    border-radius: 12px;
                    box-shadow: 0 12px 40px rgba(0,0,0,0.12);
                    padding: 10px 12px 12px;
                    backdrop-filter: blur(20px);
                }
                .drp2-presets {
                    display: flex; flex-wrap: wrap; gap: 4px;
                    padding-bottom: 8px; margin-bottom: 4px;
                    border-bottom: 1px solid var(--color-border);
                }
                .drp2-preset {
                    padding: 4px 10px; border-radius: 6px;
                    border: 1px solid var(--color-border);
                    background: transparent; color: var(--color-text);
                    font-size: 12px; cursor: pointer;
                    transition: all 0.15s;
                }
                .drp2-preset:hover {
                    border-color: var(--color-accent); color: var(--color-accent);
                    background: rgba(0, 113, 227, 0.05);
                }

                .drp2-footer {
                    display: flex; align-items: center; justify-content: space-between;
                    padding-top: 8px; margin-top: 4px;
                    border-top: 1px solid var(--color-border);
                    gap: 12px;
                }
                .drp2-hint {
                    font-size: 11px; color: var(--color-text-muted);
                    display: inline-flex; align-items: center; gap: 6px;
                }
                .drp2-dot {
                    display: inline-block; width: 5px; height: 5px; border-radius: 50%;
                    background: var(--color-success);
                }
                .drp2-actions { display: flex; gap: 6px; }

                /* DayPicker tweaks — компактный размер */
                .drp2-pop .rdp-root {
                    --rdp-accent-color: var(--color-accent);
                    --rdp-accent-background-color: rgba(0, 113, 227, 0.12);
                    --rdp-day-height: 28px;
                    --rdp-day-width: 28px;
                    --rdp-font-family: inherit;
                    --rdp-nav-height: 28px;
                    margin: 0;
                }
                /* Размещаем 2 месяца горизонтально */
                .drp2-pop .rdp-months {
                    display: flex !important;
                    flex-direction: row !important;
                    gap: 16px;
                    flex-wrap: nowrap;
                }
                .drp2-pop .rdp-month_caption { font-size: 13px; font-weight: 600; padding: 4px 0; }
                .drp2-pop .rdp-weekday { font-size: 11px; font-weight: 500; color: var(--color-text-muted); padding: 2px 0; }
                .drp2-pop .rdp-day { padding: 1px; }
                .drp2-pop .rdp-day_button {
                    color: var(--color-text);
                    font-size: 12px;
                    border-radius: 6px;
                    width: 28px; height: 28px;
                    padding: 0;
                }
                /* Точка под днём с данными */
                .drp2-pop .drp2-has-data .rdp-day_button {
                    position: relative;
                }
                .drp2-pop .drp2-has-data .rdp-day_button::after {
                    content: '';
                    position: absolute;
                    bottom: 2px; left: 50%; transform: translateX(-50%);
                    width: 3px; height: 3px; border-radius: 50%;
                    background: var(--color-success);
                }
                .drp2-pop .rdp-selected .rdp-day_button {
                    background: var(--color-accent) !important;
                    color: white !important;
                }
                .drp2-pop .rdp-range_middle .rdp-day_button {
                    background: rgba(0, 113, 227, 0.14) !important;
                    color: var(--color-text) !important;
                }
                .drp2-pop .rdp-outside { opacity: 0.35; }
            `}</style>
        </div>
    );
}
