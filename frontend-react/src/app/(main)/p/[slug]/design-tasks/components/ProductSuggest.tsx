'use client';

import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import WbThumb from '@/components/WbThumb';
import type { DesignProductSuggestion } from '@/types/api';

interface ProductSuggestProps {
    value: string;
    onChange: (v: string) => void;
    /** Выбор подсказки. Заполняет nm_id, но ничего не обязывает (Р2). */
    onPick: (s: DesignProductSuggestion) => void;
    placeholder?: string;
    autoFocus?: boolean;
}

/** Текстовый input с автоподсказкой товара (GET /product-suggest, debounce 300мс). */
export default function ProductSuggest({ value, onChange, onPick, placeholder, autoFocus }: ProductSuggestProps) {
    const [suggestions, setSuggestions] = useState<DesignProductSuggestion[]>([]);
    const [open, setOpen] = useState(false);
    const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        if (timerRef.current) clearTimeout(timerRef.current);
        const q = value.trim();
        if (q.length < 2) {
            setSuggestions([]);
            return;
        }
        let cancelled = false;
        timerRef.current = setTimeout(() => {
            api.designProductSuggest(q.slice(0, 100))
                .then((res) => {
                    if (cancelled) return;
                    setSuggestions(res);
                    setOpen(res.length > 0);
                })
                .catch(() => {
                    if (!cancelled) setSuggestions([]);
                });
        }, 300);
        return () => {
            cancelled = true;
            if (timerRef.current) clearTimeout(timerRef.current);
        };
    }, [value]);

    return (
        <div style={{ position: 'relative' }}>
            <input
                className="form-input"
                style={{ width: '100%' }}
                value={value}
                autoFocus={autoFocus}
                placeholder={placeholder}
                onChange={(e) => onChange(e.target.value)}
                onFocus={() => setOpen(suggestions.length > 0)}
                onBlur={() => setTimeout(() => setOpen(false), 150)}
            />
            {open && suggestions.length > 0 && (
                <div
                    className="glass-card"
                    style={{
                        position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50,
                        marginTop: 4, padding: 8, maxHeight: 280, overflowY: 'auto',
                    }}
                >
                    {suggestions.map((s) => (
                        <button
                            key={s.nm_id}
                            type="button"
                            onMouseDown={(e) => {
                                e.preventDefault();
                                onPick(s);
                                setOpen(false);
                            }}
                            style={{
                                display: 'flex', alignItems: 'center', gap: 10, width: '100%',
                                padding: 8, background: 'transparent', border: 'none',
                                borderRadius: 8, cursor: 'pointer', textAlign: 'left',
                                color: 'var(--color-text)',
                            }}
                        >
                            <WbThumb nmId={s.nm_id} size={28} height={36} />
                            <span style={{ minWidth: 0 }}>
                                <span style={{ display: 'block', fontSize: 13 }}>
                                    {s.article_seller || `#${s.nm_id}`}
                                </span>
                                <span style={{ display: 'block', fontSize: 12, color: 'var(--color-text-muted)' }}>
                                    {[s.brand, s.subject].filter(Boolean).join(' · ') || `nm ${s.nm_id}`}
                                </span>
                            </span>
                            <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--color-text-dim)' }}>{s.nm_id}</span>
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
}
