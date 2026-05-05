'use client';
import { useState, useEffect } from 'react';
import { api } from '@/lib/api';

export function WarehouseExclusionSettings() {
    const [warehouses, setWarehouses] = useState<Array<{ name: string; is_sorting_center?: boolean }>>([]);
    const [excluded, setExcluded] = useState<string[]>([]);
    const [rfDefaultDays, setRfDefaultDays] = useState<number>(8);
    const [rfDefaultDaysSaved, setRfDefaultDaysSaved] = useState<number>(8);
    const [savingRf, setSavingRf] = useState(false);
    const [rfMsg, setRfMsg] = useState('');
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [msg, setMsg] = useState('');
    const [hasChanges, setHasChanges] = useState(false);

    useEffect(() => {
        (async () => {
            try {
                const [wh, ex, rf] = await Promise.all([
                    api.getWarehouses(),
                    api.getExcludedWarehouses(),
                    api.getForecastRfDefaultDays(),
                ]);
                setWarehouses(wh);
                setExcluded(ex);
                setRfDefaultDays(rf.days);
                setRfDefaultDaysSaved(rf.days);
            } catch {
                setMsg('Ошибка загрузки складов');
            } finally {
                setLoading(false);
            }
        })();
    }, []);

    const saveRfDefaultDays = async () => {
        setSavingRf(true);
        setRfMsg('');
        try {
            const { days } = await api.setForecastRfDefaultDays(rfDefaultDays);
            setRfDefaultDaysSaved(days);
            setRfDefaultDays(days);
            setRfMsg('✅ Сохранено');
        } catch {
            setRfMsg('❌ Ошибка сохранения');
        } finally {
            setSavingRf(false);
        }
    };

    const toggle = (name: string) => {
        setExcluded(prev => {
            const next = prev.includes(name) ? prev.filter(n => n !== name) : [...prev, name];
            return next;
        });
        setHasChanges(true);
    };

    const save = async () => {
        setSaving(true);
        setMsg('');
        try {
            await api.setExcludedWarehouses(excluded);
            setMsg('✅ Сохранено');
            setHasChanges(false);
        } catch {
            setMsg('❌ Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    if (loading) return <div className="glass-card" style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-muted)' }}>Загрузка складов...</div>;

    const active = warehouses.filter(w => !excluded.includes(w.name));
    const excludedList = warehouses.filter(w => excluded.includes(w.name));

    return (
        <div>
            <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                <h3 style={{ margin: '0 0 8px' }}>📦 Время РФ → WB по умолчанию</h3>
                <p style={{ color: 'var(--color-text-muted)', margin: '0 0 16px', fontSize: 14 }}>
                    Используется в прогнозе остатков, если у склада не заполнена вкладка «Время доставки» (сборка + доставка + приёмка WB). Текущее значение применяется ко всем фулфилмент-складам без расписания.
                </p>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14 }}>
                        <span>Дней до прибытия на WB:</span>
                        <input
                            type="number"
                            min={0}
                            max={365}
                            value={rfDefaultDays}
                            onChange={(e) => setRfDefaultDays(Math.max(0, Math.min(365, parseInt(e.target.value || '0', 10))))}
                            style={{
                                width: 80,
                                padding: '8px 12px',
                                borderRadius: 8,
                                border: '1px solid var(--color-border)',
                                background: 'var(--color-bg-card)',
                                color: 'var(--color-text)',
                                fontSize: 14,
                            }}
                        />
                    </label>
                    <button
                        className="btn btn-primary btn-sm"
                        onClick={saveRfDefaultDays}
                        disabled={savingRf || rfDefaultDays === rfDefaultDaysSaved}
                    >
                        {savingRf ? 'Сохранение...' : '💾 Сохранить'}
                    </button>
                    {rfMsg && <span style={{ fontSize: 14 }}>{rfMsg}</span>}
                </div>
            </div>

            <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                <h3 style={{ margin: '0 0 8px' }}>🏭 Исключение складов</h3>
                <p style={{ color: 'var(--color-text-muted)', margin: '0 0 16px', fontSize: 14 }}>
                    Исключённые склады не участвуют в расчёте потребностей. Заказы из их регионов перераспределяются на ближайшие оставшиеся склады.
                </p>

                <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                        Активных: <strong>{active.length}</strong> / {warehouses.length}
                    </span>
                    {excluded.length > 0 && (
                        <span style={{ fontSize: 13, color: 'var(--color-danger)', fontWeight: 500 }}>
                            ⛔ Исключено: {excluded.length}
                        </span>
                    )}
                    <span style={{ flex: 1 }} />
                    <button
                        className="btn btn-secondary btn-sm"
                        type="button"
                        onClick={() => {
                            const scNames = warehouses
                                .filter(w => w.is_sorting_center || w.name.startsWith('СЦ '))
                                .map(w => w.name);
                            const allExcluded = scNames.length > 0 && scNames.every(n => excluded.includes(n));
                            if (allExcluded) {
                                setExcluded(prev => prev.filter(n => !scNames.includes(n)));
                            } else {
                                setExcluded(prev => Array.from(new Set([...prev, ...scNames])));
                            }
                            setHasChanges(true);
                        }}
                        title="Сортировочные центры (СЦ) — это транзитные пункты WB, на них нельзя долго хранить запас"
                    >
                        🛂 Все СЦ {(() => {
                            const scNames = warehouses.filter(w => w.is_sorting_center || w.name.startsWith('СЦ ')).map(w => w.name);
                            const allExcluded = scNames.length > 0 && scNames.every(n => excluded.includes(n));
                            return allExcluded ? '— включить' : '— исключить';
                        })()}
                    </button>
                </div>

                <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
                    gap: 8,
                }}>
                    {warehouses.map(w => {
                        const isExcluded = excluded.includes(w.name);
                        return (
                            <label
                                key={w.name}
                                style={{
                                    display: 'flex', alignItems: 'center', gap: 8,
                                    padding: '8px 12px',
                                    borderRadius: 8,
                                    border: `1px solid ${isExcluded ? 'var(--color-danger)' : 'var(--color-border)'}`,
                                    background: isExcluded ? 'rgba(239,68,68,0.08)' : 'var(--color-bg-card)',
                                    cursor: 'pointer',
                                    transition: 'all 0.15s',
                                    opacity: isExcluded ? 0.7 : 1,
                                }}
                            >
                                <input
                                    type="checkbox"
                                    checked={!isExcluded}
                                    onChange={() => toggle(w.name)}
                                    style={{ accentColor: 'var(--color-primary)' }}
                                />
                                <span style={{
                                    fontSize: 14,
                                    textDecoration: isExcluded ? 'line-through' : 'none',
                                    color: isExcluded ? 'var(--color-danger)' : 'var(--color-text)',
                                }}>
                                    {w.name}
                                </span>
                            </label>
                        );
                    })}
                </div>
            </div>

            <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                <button
                    className="btn btn-primary"
                    onClick={save}
                    disabled={saving || !hasChanges}
                >
                    {saving ? 'Сохранение...' : '💾 Сохранить'}
                </button>
                {msg && <span style={{ fontSize: 14 }}>{msg}</span>}
            </div>

            {excludedList.length > 0 && (
                <div className="glass-card" style={{ padding: 16, marginTop: 16 }}>
                    <p style={{ fontSize: 13, color: 'var(--color-text-muted)', margin: 0 }}>
                        ⚠️ Исключены: <strong>{excludedList.map(w => w.name).join(', ')}</strong>.
                        Заказы из этих регионов будут распределены на ближайшие активные склады.
                    </p>
                </div>
            )}
        </div>
    );
}
