'use client';
import React, { useState } from 'react';
import { productImageUrl } from '@/lib/wbMedia';

/** Миниатюра главного фото товара WB по nm_id. При 404/ошибке — нейтральный плейсхолдер.
 *  height — для непрямоугольных миниатюр (по умолчанию квадрат size×size). */
export default function WbThumb({ nmId, size = 40, height, rounded = 8 }: { nmId: number | null | undefined; size?: number; height?: number; rounded?: number }) {
    const [failed, setFailed] = useState(false);
    const box: React.CSSProperties = {
        width: size, height: height ?? size, borderRadius: rounded, flexShrink: 0,
        border: '1px solid var(--color-border)', background: 'var(--color-bg-hover)', objectFit: 'cover',
        display: 'inline-block', verticalAlign: 'middle',
    };
    if (nmId == null || failed) {
        return (
            <span style={{ ...box, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: 'var(--color-text-dim)' }} title={nmId != null ? `#${nmId}` : undefined}>
                <svg width={Math.round(size * 0.5)} height={Math.round(size * 0.5)} viewBox="0 0 24 24" fill="none" aria-hidden>
                    <rect x="3" y="4" width="18" height="16" rx="2" stroke="currentColor" strokeWidth="1.6" />
                    <circle cx="8.5" cy="9.5" r="1.5" fill="currentColor" />
                    <path d="M4 17l5-5 4 4 3-3 4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
            </span>
        );
    }
    return (
        <img src={productImageUrl(nmId)} alt={`#${nmId}`} loading="lazy"
            style={box} onError={() => setFailed(true)} />
    );
}
