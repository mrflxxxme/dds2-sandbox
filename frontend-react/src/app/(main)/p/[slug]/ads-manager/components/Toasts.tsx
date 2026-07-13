'use client';
import React, { createContext, useCallback, useContext, useRef, useState } from 'react';
import { IcCheckCircle, IcXCircle, IcAlertTriangle, IcX } from './icons';

export type ToastType = 'success' | 'error' | 'warning';
type Toast = { id: number; type: ToastType; message: string };

type ToastApi = {
    success: (message: string) => void;
    error: (message: string) => void;
    warning: (message: string) => void;
};

const ToastCtx = createContext<ToastApi | null>(null);

/** Хук уведомлений раздела рекламы. Вызывай `toast.success('Кампания завершена')` и т.п. */
export function useToast(): ToastApi {
    const ctx = useContext(ToastCtx);
    if (!ctx) throw new Error('useToast должен вызываться внутри <ToastProvider>');
    return ctx;
}

const STYLE: Record<ToastType, { icon: React.ReactNode; color: string; bar: string }> = {
    success: { icon: <IcCheckCircle size={20} />, color: '#059669', bar: '#10b981' },
    error: { icon: <IcXCircle size={20} />, color: '#dc2626', bar: '#ef4444' },
    warning: { icon: <IcAlertTriangle size={20} />, color: '#d97706', bar: '#f59e0b' },
};
// error держим дольше — его важно прочитать; success/warning короче
const DURATION: Record<ToastType, number> = { success: 4000, warning: 5000, error: 7000 };

/** Провайдер + контейнер стека уведомлений (правый верхний угол). Оборачивает раздел. */
export default function ToastProvider({ children }: { children: React.ReactNode }) {
    const [items, setItems] = useState<Toast[]>([]);
    const idRef = useRef(0);
    const timers = useRef<Record<number, ReturnType<typeof setTimeout>>>({});

    const remove = useCallback((id: number) => {
        setItems(prev => prev.filter(t => t.id !== id));
        const t = timers.current[id];
        if (t) { clearTimeout(t); delete timers.current[id]; }
    }, []);

    const push = useCallback((type: ToastType, message: string) => {
        const id = ++idRef.current;
        setItems(prev => [...prev, { id, type, message }]);
        timers.current[id] = setTimeout(() => remove(id), DURATION[type]);
    }, [remove]);

    const api: ToastApi = {
        success: useCallback((m: string) => push('success', m), [push]),
        error: useCallback((m: string) => push('error', m), [push]),
        warning: useCallback((m: string) => push('warning', m), [push]),
    };

    return (
        <ToastCtx.Provider value={api}>
            {children}
            <div style={{ position: 'fixed', top: 16, right: 16, zIndex: 3000, display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 380, pointerEvents: 'none' }}>
                {items.map(t => {
                    const s = STYLE[t.type];
                    return (
                        <div key={t.id} role="alert" className="ads-toast"
                            style={{
                                pointerEvents: 'auto', display: 'flex', alignItems: 'flex-start', gap: 10,
                                background: '#fff', borderRadius: 12, padding: '13px 14px',
                                boxShadow: '0 10px 32px rgba(17,24,39,.16)', borderLeft: `4px solid ${s.bar}`,
                            }}>
                            <span style={{ color: s.color, display: 'inline-flex', flexShrink: 0, marginTop: 1 }}>{s.icon}</span>
                            <span style={{ fontSize: 13.5, color: '#1f2937', lineHeight: 1.4, flex: 1 }}>{t.message}</span>
                            <button onClick={() => remove(t.id)} aria-label="Закрыть"
                                style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#9ca3af', display: 'inline-flex', flexShrink: 0, padding: 0 }}>
                                <IcX size={16} />
                            </button>
                        </div>
                    );
                })}
            </div>
            <style>{`
                @keyframes adsToastIn { from { opacity: 0; transform: translateX(24px); } to { opacity: 1; transform: translateX(0); } }
                .ads-toast { animation: adsToastIn .22s cubic-bezier(.16,1,.3,1); }
            `}</style>
        </ToastCtx.Provider>
    );
}
