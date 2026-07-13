'use client';
import React from 'react';

/** Единый ползунок-тумблер для раздела «Управление рекламой» (зелёный = включено). */
export default function Switch({ on, onClick, disabled = false, size = 'md', ariaLabel }: {
    on: boolean;
    onClick: () => void;
    disabled?: boolean;
    size?: 'sm' | 'md';
    ariaLabel?: string;
}) {
    const w = size === 'sm' ? 30 : 36;
    const h = size === 'sm' ? 16 : 20;
    const knob = h - 4;
    return (
        <button type="button" role="switch" aria-checked={on} aria-label={ariaLabel}
            onClick={e => { e.preventDefault(); e.stopPropagation(); if (!disabled) onClick(); }}
            disabled={disabled}
            style={{
                width: w, height: h, borderRadius: h / 2, border: 'none', padding: 2, flexShrink: 0,
                cursor: disabled ? 'not-allowed' : 'pointer',
                background: on ? '#10b981' : '#d1d5db',
                display: 'inline-flex', alignItems: 'center', justifyContent: on ? 'flex-end' : 'flex-start',
                opacity: disabled ? 0.55 : 1, transition: 'background .15s',
            }}>
            <span style={{ width: knob, height: knob, borderRadius: '50%', background: '#fff', pointerEvents: 'none', boxShadow: '0 1px 2px rgba(0,0,0,.2)' }} />
        </button>
    );
}
