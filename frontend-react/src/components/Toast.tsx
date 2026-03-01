'use client';

import { useState, useEffect } from 'react';

interface ToastProps {
    message: string;
    type?: 'success' | 'error' | 'info';
    onClose: () => void;
    duration?: number;
}

/**
 * Toast — auto-dismissing notification.
 *
 * Usage:
 * ```tsx
 * {msg && <Toast message={msg} onClose={() => setMsg('')} />}
 * ```
 */
export default function Toast({ message, type = 'success', onClose, duration = 4000 }: ToastProps) {
    useEffect(() => {
        const timer = setTimeout(onClose, duration);
        return () => clearTimeout(timer);
    }, [duration, onClose]);

    const colors = {
        success: { bg: 'rgba(34, 197, 94, 0.15)', border: 'rgba(34, 197, 94, 0.3)', color: 'var(--color-success)' },
        error: { bg: 'rgba(239, 68, 68, 0.15)', border: 'rgba(239, 68, 68, 0.3)', color: 'var(--color-danger)' },
        info: { bg: 'rgba(99, 102, 241, 0.15)', border: 'rgba(99, 102, 241, 0.3)', color: 'var(--color-accent)' },
    };
    const c = colors[type];

    return (
        <div style={{
            position: 'fixed', bottom: 24, right: 24, zIndex: 200,
            background: c.bg, border: `1px solid ${c.border}`, color: c.color,
            padding: '12px 20px', borderRadius: 10, fontSize: 14, fontWeight: 500,
            display: 'flex', alignItems: 'center', gap: 10,
            animation: 'fadeIn 0.2s ease-out',
            backdropFilter: 'blur(12px)',
        }}>
            <span>{message}</span>
            <span style={{ cursor: 'pointer', opacity: 0.7 }} onClick={onClose}>✕</span>
        </div>
    );
}
