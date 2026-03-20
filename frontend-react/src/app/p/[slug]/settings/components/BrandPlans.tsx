'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import type { BrandPlan } from '@/types/api';

const MONTH_NAMES = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн', 'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек'];

export function BrandPlans() {
    const [plans, setPlans] = useState<BrandPlan[]>([]);
    const [brands, setBrands] = useState<string[]>([]);
    const [year, setYear] = useState(new Date().getFullYear());
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [saving, setSaving] = useState<Record<string, boolean>>({});
    const [newBrand, setNewBrand] = useState('');
    const debounceTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

    const loadData = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [p, b] = await Promise.all([
                api.getBrandPlans(year),
                api.getWbBrands(),
            ]);
            setPlans(p);
            setBrands(b);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Error');
        } finally {
            setLoading(false);
        }
    }, [year]);

    useEffect(() => { loadData(); }, [loadData]);

    const planBrands = [...new Set(plans.map(p => p.brand))].sort();
    const planMonths = [...new Set(plans.map(p => p.month))].sort((a, b) => a - b);
    const availableBrands = brands.filter(b => !planBrands.includes(b));

    const getPlan = (brand: string, month: number) =>
        plans.find(p => p.brand === brand && p.month === month);

    const handleAmountChange = (brand: string, month: number, value: string) => {
        const amount = parseFloat(value) || 0;
        const key = `${brand}-${month}`;

        setPlans(prev => {
            const idx = prev.findIndex(p => p.brand === brand && p.month === month);
            if (idx >= 0) {
                const updated = [...prev];
                updated[idx] = { ...updated[idx], plan_amount: amount };
                return updated;
            }
            return [...prev, { brand, year, month, plan_amount: amount }];
        });

        clearTimeout(debounceTimers.current[key]);
        debounceTimers.current[key] = setTimeout(async () => {
            setSaving(prev => ({ ...prev, [key]: true }));
            try {
                const existing = plans.find(p => p.brand === brand && p.month === month);
                await api.upsertBrandPlan({
                    id: existing?.id,
                    brand, year, month,
                    plan_amount: amount,
                });
                await loadData();
            } finally {
                setSaving(prev => ({ ...prev, [key]: false }));
            }
        }, 500);
    };

    const addBrand = async () => {
        if (!newBrand) return;
        const month = new Date().getMonth() + 1;
        await api.upsertBrandPlan({ brand: newBrand, year, month, plan_amount: 0 });
        setNewBrand('');
        await loadData();
    };

    const addMonth = async () => {
        const existing = new Set(planMonths);
        const next = Array.from({ length: 12 }, (_, i) => i + 1).find(m => !existing.has(m));
        if (!next || planBrands.length === 0) return;
        await api.upsertBrandPlan({ brand: planBrands[0], year, month: next, plan_amount: 0 });
        await loadData();
    };

    const deleteBrand = async (brand: string) => {
        const brandPlans = plans.filter(p => p.brand === brand && p.id);
        for (const p of brandPlans) {
            if (p.id) await api.deleteBrandPlan(p.id);
        }
        await loadData();
    };

    const handleExport = () => {
        const rows = planBrands.map(brand => {
            const row: Record<string, unknown> = { 'Бренд': brand };
            planMonths.forEach(m => {
                row[MONTH_NAMES[m - 1]] = Number(getPlan(brand, m)?.plan_amount ?? 0);
            });
            row['Итого'] = brandTotal(brand);
            return row;
        });
        exportToExcel(rows as Record<string, string | number>[], `plan_${year}`);
    };

    const brandTotal = (brand: string) =>
        planMonths.reduce((s, m) => s + Number(getPlan(brand, m)?.plan_amount ?? 0), 0);

    const monthTotal = (month: number) =>
        planBrands.reduce((s, b) => s + Number(getPlan(b, month)?.plan_amount ?? 0), 0);

    const grandTotal = planBrands.reduce((s, b) => s + brandTotal(b), 0);

    if (loading && plans.length === 0) return <div className="text-center py-8 text-secondary">Загрузка...</div>;
    if (error) return <div className="text-center py-8 text-danger">{error}</div>;

    return (
        <div>
            <div className="table-toolbar" style={{ marginBottom: 16 }}>
                <h5 style={{ margin: 0 }}>План по брендам</h5>
                <select
                    className="form-select form-select-sm"
                    style={{ width: 100 }}
                    value={year}
                    onChange={e => setYear(Number(e.target.value))}
                >
                    {[year - 1, year, year + 1].map(y => (
                        <option key={y} value={y}>{y}</option>
                    ))}
                </select>
                <button className="btn btn-outline-primary btn-sm" onClick={addMonth}>
                    + Месяц
                </button>
                <div style={{ flex: 1 }} />
                <button className="btn btn-outline-secondary btn-sm" onClick={handleExport}>
                    Excel
                </button>
            </div>

            {planBrands.length === 0 ? (
                <div className="text-center py-8 text-secondary">
                    Нет планов. Добавьте бренд из выпадающего списка ниже.
                </div>
            ) : (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Бренд</th>
                                {planMonths.map(m => (
                                    <th key={m} style={{ textAlign: 'right' }}>{MONTH_NAMES[m - 1]}</th>
                                ))}
                                <th style={{ textAlign: 'right' }}>Итого</th>
                                <th style={{ width: 40 }}></th>
                            </tr>
                        </thead>
                        <tbody>
                            {planBrands.map(brand => (
                                <tr key={brand}>
                                    <td style={{ fontWeight: 500 }}>{brand}</td>
                                    {planMonths.map(m => {
                                        const plan = getPlan(brand, m);
                                        const key = `${brand}-${m}`;
                                        return (
                                            <td key={m} style={{ textAlign: 'right', minWidth: 130 }}>
                                                <input
                                                    type="number"
                                                    className="form-control form-control-sm"
                                                    style={{
                                                        textAlign: 'right',
                                                        opacity: saving[key] ? 0.5 : 1,
                                                        maxWidth: 130,
                                                    }}
                                                    value={plan?.plan_amount ?? ''}
                                                    placeholder="0"
                                                    onChange={e => handleAmountChange(brand, m, e.target.value)}
                                                />
                                            </td>
                                        );
                                    })}
                                    <td style={{ textAlign: 'right', fontWeight: 600 }}>
                                        {formatNumber(brandTotal(brand), 0)}
                                    </td>
                                    <td style={{ textAlign: 'center' }}>
                                        <button
                                            className="btn btn-sm text-danger"
                                            style={{ padding: '2px 6px', lineHeight: 1 }}
                                            title="Удалить бренд"
                                            onClick={() => deleteBrand(brand)}
                                        >
                                            &times;
                                        </button>
                                    </td>
                                </tr>
                            ))}
                            <tr style={{ fontWeight: 700, borderTop: '2px solid var(--color-border)' }}>
                                <td>Итого</td>
                                {planMonths.map(m => (
                                    <td key={m} style={{ textAlign: 'right' }}>
                                        {formatNumber(monthTotal(m), 0)}
                                    </td>
                                ))}
                                <td style={{ textAlign: 'right' }}>{formatNumber(grandTotal, 0)}</td>
                                <td></td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            )}

            <div className="table-toolbar" style={{ marginTop: 16 }}>
                <select
                    className="form-select form-select-sm"
                    style={{ width: 250 }}
                    value={newBrand}
                    onChange={e => setNewBrand(e.target.value)}
                >
                    <option value="">Выберите бренд...</option>
                    {availableBrands.map(b => (
                        <option key={b} value={b}>{b}</option>
                    ))}
                </select>
                <button
                    className="btn btn-primary btn-sm"
                    disabled={!newBrand}
                    onClick={addBrand}
                >
                    + Добавить бренд
                </button>
            </div>
        </div>
    );
}
