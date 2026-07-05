'use client';
import { useState, useEffect } from 'react';
import { api } from '@/lib/api';

/**
 * Настройка «Вес коробки (кг)» — одно число на проект.
 * Прибавляется к нетто товаров × число коробов при авто-расчёте веса отгрузки.
 * Тара паллеты не учитывается. Self-contained: сам грузит/сохраняет.
 */
export function BoxWeightSetting() {
    const [value, setValue] = useState('');      // сырой ввод инпута
    const [saved, setSaved] = useState<number | null>(null); // последнее сохранённое значение
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);
    const [msg, setMsg] = useState('');

    useEffect(() => {
        (async () => {
            setLoading(true);
            setError('');
            try {
                const { weight_kg } = await api.getBoxWeight();
                setSaved(weight_kg);
                setValue(weight_kg != null ? String(weight_kg) : '');
            } catch {
                setError('Не удалось загрузить вес коробки');
            } finally {
                setLoading(false);
            }
        })();
    }, []);

    const save = async () => {
        const num = parseFloat(value.replace(',', '.'));
        if (!Number.isFinite(num)) {
            setMsg('❌ Введите число');
            return;
        }
        setSaving(true);
        setMsg('');
        try {
            const { weight_kg } = await api.setBoxWeight(num);
            setSaved(weight_kg);
            setValue(String(weight_kg));
            setMsg('✅ Сохранено');
        } catch {
            setMsg('❌ Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    // Изменилось ли значение относительно сохранённого (нормализуем через parseFloat).
    const parsed = parseFloat(value.replace(',', '.'));
    const dirty = Number.isFinite(parsed) ? parsed !== saved : value.trim() !== (saved != null ? String(saved) : '');

    return (
        <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
            <h3 style={{ margin: '0 0 8px' }}>⚖️ Вес коробки (кг)</h3>
            <p style={{ color: 'var(--color-text-muted)', margin: '0 0 16px', fontSize: 14 }}>
                Прибавляется к нетто товаров × число коробов при авто-расчёте веса отгрузки. Тара паллеты не учитывается.
            </p>
            {loading ? (
                <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>Загрузка…</div>
            ) : error ? (
                <div style={{ color: 'var(--color-danger)', fontSize: 14 }}>{error}</div>
            ) : (
                <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14 }}>
                        <span>Вес одной коробки, кг:</span>
                        <input
                            type="number"
                            min={0}
                            step={0.1}
                            value={value}
                            placeholder="не задан"
                            onChange={(e) => setValue(e.target.value)}
                            onKeyDown={(e) => { if (e.key === 'Enter' && dirty && !saving) save(); }}
                            style={{
                                width: 100,
                                padding: '8px 12px',
                                borderRadius: 8,
                                border: '1px solid var(--color-border)',
                                background: 'var(--color-bg-card)',
                                color: 'var(--color-text)',
                                fontSize: 14,
                            }}
                        />
                    </label>
                    <button className="btn btn-primary btn-sm" onClick={save} disabled={saving || !dirty}>
                        {saving ? 'Сохранение…' : '💾 Сохранить'}
                    </button>
                    {msg && <span style={{ fontSize: 14 }}>{msg}</span>}
                </div>
            )}
        </div>
    );
}
