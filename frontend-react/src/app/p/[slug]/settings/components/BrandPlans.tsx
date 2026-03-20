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
                row[MONTH_NAMES[m - 1]] = getPlan(brand, m)?.plan_amount ?? 0;
            });
            return row;
        });
        exportToExcel(rows as Record<string, string | number>[], `plan_${year}`);
    };

    const brandTotal = (brand: string) =>
        planMonths.reduce((s, m) => s + (getPlan(brand, m)?.plan_amount ?? 0), 0);

    const monthTotal = (month: number) =>
        planBrands.reduce((s, b) => s + (getPlan(b, month)?.plan_amount ?? 0), 0);

    const grandTotal = planBrands.reduce((s, b) => s + brandTotal(b), 0);

    if (loading && plans.length === 0) return <div className="text-center py-8 text-secondary">Загрузка...</div>;
    if (error) return <div className="text-center py-8 text-danger">{error}</div>;

    return (
        <div>
            <div className="d-flex align-items-center gap-3 mb-4">
                <h5 className="mb-0">План по брендам</h5>
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
                <button className="btn btn-outline-secondary btn-sm ms-auto" onClick={handleExport}>
                    Excel
                </button>
            </div>

            {planBrands.length === 0 ? (
                <div className="text-center py-8 text-secondary">
                    Нет планов. Добавьте бренд из выпадающего списка ниже.
                </div>
            ) : (
                <div className="table-responsive">
                    <table className="table table-sm table-bordered">
                        <thead>
                            <tr>
                                <th>Бренд</th>
                                {planMonths.map(m => (
                                    <th key={m} className="text-center">{MONTH_NAMES[m - 1]}</th>
                                ))}
                                <th className="text-end">Итого</th>
                                <th style={{ width: 40 }}></th>
                            </tr>
                        </thead>
                        <tbody>
                            {planBrands.map(brand => (
                                <tr key={brand}>
                                    <td className="fw-medium">{brand}</td>
                                    {planMonths.map(m => {
                                        const plan = getPlan(brand, m);
                                        const key = `${brand}-${m}`;
                                        return (
                                            <td key={m} className="text-end" style={{ minWidth: 120 }}>
                                                <input
                                                    type="number"
                                                    className="form-control form-control-sm text-end"
                                                    value={plan?.plan_amount ?? ''}
                                                    placeholder="0"
                                                    onChange={e => handleAmountChange(brand, m, e.target.value)}
                                                    style={{ opacity: saving[key] ? 0.5 : 1 }}
                                                />
                                            </td>
                                        );
                                    })}
                                    <td className="text-end fw-bold">
                                        {formatNumber(brandTotal(brand), 0)}
                                    </td>
                                    <td>
                                        <button
                                            className="btn btn-sm text-danger p-0"
                                            title="Удалить бренд"
                                            onClick={() => deleteBrand(brand)}
                                        >
                                            &times;
                                        </button>
                                    </td>
                                </tr>
                            ))}
                            <tr className="fw-bold">
                                <td>Итого</td>
                                {planMonths.map(m => (
                                    <td key={m} className="text-end">
                                        {formatNumber(monthTotal(m), 0)}
                                    </td>
                                ))}
                                <td className="text-end">{formatNumber(grandTotal, 0)}</td>
                                <td></td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            )}

            <div className="d-flex align-items-center gap-2 mt-3">
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
