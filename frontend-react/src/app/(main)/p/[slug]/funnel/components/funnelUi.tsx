'use client';
import React from 'react';

/* ─── Единая стилистика раздела «Воронка продаж» ──────────────────────────────
 * Референс — «Управление рекламой» (`ads-manager`): фикс-высота экрана, тёмная
 * липкая шапка таблиц, тулбар-полоса внутри карточки, сегментированные вкладки,
 * поисковые селекты. Токены держим здесь, чтобы четыре таблицы воронки и обе
 * соседние вкладки не разъезжались. Инлайн-стили — как в разделе-референсе.
 * ────────────────────────────────────────────────────────────────────────── */

/** Оболочка страницы: заголовок/фильтры/тулбар закреплены, скроллится только контент.
 *  Поля не трогаем — раздел живёт в тех же полях .main-content (40px 48px), что и
 *  «Управление рекламой»: края таблицы совпадают с рекламой слева и справа. */
export const PAGE_SHELL: React.CSSProperties = {
    height: 'calc(100vh - 56px)', display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden',
};

/** Заголовок раздела (28/700 с иконкой) — как h1 в рекламе. */
export const PAGE_H1: React.CSSProperties = {
    display: 'flex', alignItems: 'center', gap: 10, fontSize: 28, fontWeight: 700, margin: 0,
};

/** Карточка с таблицей: занимает остаток экрана, скроллятся только строки. */
export const TABLE_CARD: React.CSSProperties = {
    padding: 0, overflow: 'hidden', flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column',
};

/** Тулбар-полоса в шапке карточки (поиск, кнопки, переключатель группировки). */
export const CARD_TOOLBAR: React.CSSProperties = {
    padding: '7px 16px', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap',
    borderBottom: '1px solid #e5e7eb', background: '#f9fafb', flexShrink: 0,
};

/** Подвал карточки со счётчиком строк. */
export const CARD_FOOTER: React.CSSProperties = {
    padding: '4px 16px', borderTop: '1px solid #e5e7eb', fontSize: 11.5,
    color: 'var(--color-text-dim)', background: '#f9fafb', flexShrink: 0,
};

/* ─── Шапка таблиц: тёмная, липкая (как список кампаний в рекламе) ───────── */

/** Однорядная шапка (ABC, «Топ товаров») — тот же тёмный вид. */
export const thFlat: React.CSSProperties = {
    position: 'sticky', top: 0, zIndex: 20, background: '#374151', color: '#e5e7eb',
    fontSize: 10.5, fontWeight: 700, padding: '6px 10px', textAlign: 'right',
    borderBottom: '1px solid #4b5563', whiteSpace: 'nowrap',
};

export function Segmented<T extends string>({ value, options, onChange, compact }: {
    value: T;
    options: { key: T; label: string; title?: string }[];
    onChange: (key: T) => void;
    compact?: boolean;
}) {
    return (
        <span style={{ display: 'inline-flex', gap: 3, background: '#f3f4f6', borderRadius: 8, padding: 3 }}>
            {options.map(o => (
                <button key={o.key} type="button" title={o.title} onClick={() => onChange(o.key)}
                    style={{
                        fontSize: compact ? 12 : 13, padding: compact ? '4px 10px' : '5px 14px', borderRadius: 6,
                        border: 'none', cursor: 'pointer', whiteSpace: 'nowrap',
                        background: value === o.key ? '#fff' : 'transparent',
                        color: value === o.key ? '#1e3a8a' : '#6b7280',
                        fontWeight: value === o.key ? 600 : 500,
                        boxShadow: value === o.key ? '0 1px 2px rgba(0,0,0,.1)' : undefined,
                    }}>
                    {o.label}
                </button>
            ))}
        </span>
    );
}

/* ─── Кнопка-тумблер тулбара (вкл/выкл режима) ───────────────────────────── */

export function ToggleBtn({ on, disabled, title, onClick, children }: {
    on: boolean; disabled?: boolean; title?: string; onClick: () => void; children: React.ReactNode;
}) {
    return (
        <button type="button" title={title} disabled={disabled} onClick={onClick}
            style={{
                display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: on ? 600 : 500,
                padding: '5px 11px', borderRadius: 8, whiteSpace: 'nowrap',
                cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.45 : 1,
                border: `1px solid ${on && !disabled ? 'var(--color-accent)' : 'var(--color-border)'}`,
                background: on && !disabled ? '#eff6ff' : '#fff',
                color: on && !disabled ? '#1e3a8a' : '#374151',
            }}>
            {children}
        </button>
    );
}

/* ─── Компактная карточка-метрика (шапка воронки и вкладок) ──────────────── */

export function StatCard({ label, value, color, active, hint, onClick }: {
    label: string; value: string; color: string; active?: boolean; hint?: React.ReactNode; onClick?: () => void;
}) {
    return (
        <div className="glass-card static" onClick={onClick} title={onClick ? 'Клик — показать метрику на графике' : undefined}
            style={{
                padding: '8px 12px', textAlign: 'center', borderRadius: 12, cursor: onClick ? 'pointer' : 'default',
                border: `1px solid ${active ? color : 'var(--color-border)'}`,
                boxShadow: active ? `0 1px 6px ${color}33` : undefined,
                background: active ? `${color}0d` : undefined,
            }}>
            <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginBottom: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</div>
            <div style={{ fontSize: 18, fontWeight: 700, color, fontVariantNumeric: 'tabular-nums' }}>{value}</div>
            {hint}
        </div>
    );
}
