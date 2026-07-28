'use client';

import { useCallback, useEffect, useState } from 'react';
import {
    ResponsiveContainer, ComposedChart, Area, Bar, Line, XAxis, YAxis,
    CartesianGrid, Tooltip, Legend, PieChart, Pie, Cell, BarChart,
} from 'recharts';
import { api } from '@/lib/api';
import KpiCard from '@/components/KpiCard';
import type { LoanDashboard } from '@/types/api';
import { CHART_COLORS, entityLabel, fmtDate, money, monthLabel, ratePct } from './loanFmt';

export default function LoansDashboard({ nonce }: { nonce: number }) {
    const [data, setData] = useState<LoanDashboard | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setData(await api.loanDashboard());
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load, nonce]);

    if (loading) {
        return <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка…</div>;
    }
    if (error) {
        return (
            <div className="glass-card" style={{ padding: 24, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between' }}>
                <span>{error}</span>
                <button className="btn btn-secondary btn-sm" onClick={load}>Повторить</button>
            </div>
        );
    }
    if (!data) return null;

    const k = data.kpis;
    const isEmpty = k.active_count === 0 && data.monthly.length === 0;
    if (isEmpty) {
        return (
            <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                <div style={{ fontSize: 48, marginBottom: 16 }}>💸</div>
                <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>Пока нет активных займов</div>
                <div style={{ color: 'var(--color-text-dim)' }}>Добавьте займ вручную или импортируйте реестр из Excel.</div>
            </div>
        );
    }

    // Точка = календарный месяц (YYYY-MM); текущий месяц помечен is_partial —
    // он посчитан не до конца (только с 1-го числа по сегодня).
    const monthly = data.monthly.map((m) => ({
        month: `${monthLabel(m.month)}${m.is_partial ? ' *' : ''}`,
        isPartial: m.is_partial,
        outstanding: Number(m.outstanding),
        interest: Number(m.interest),
        fee: Number(m.fee),
        cost: Number(m.cost),
        disbursed: Number(m.disbursed),
        repaid: Number(m.repaid),
    }));
    const hasPartialMonth = monthly.some((m) => m.isPartial);
    const byEntity = data.by_entity.map((e) => ({
        name: entityLabel(e.entity_type),
        value: Number(e.outstanding),
        count: e.count,
    }));
    // Деление физлицо/ИП объясняет что-то только там, где заимодавцев много и
    // сущность у них проставлена (реестр частных займов). Там, где кредиторы —
    // пара банков и один ИП, оно вырождается в «ИП против прочерка»: показывать
    // такую разбивку значит выдавать шум за структуру.
    const knownEntities = data.by_entity.filter(
        (e) => e.entity_type && (Number(e.outstanding) > 0 || Number(e.interest_due_period) > 0),
    );
    const showEntitySplit = knownEntities.length >= 2;
    // Стоимость денег за календарный месяц (прогноз/факт) отдельно по ИП и физлицам
    const interestByEntity = data.by_entity
        .filter((e) => Number(e.interest_due_period) > 0 || Number(e.outstanding) > 0)
        .map((e) => ({
            key: e.entity_type ?? 'NONE',
            label: entityLabel(e.entity_type),
            due: Number(e.interest_due_period),
            accrued: Number(e.accrued_interest),
            outstanding: Number(e.outstanding),
        }));
    const byRate = data.by_rate.map((r) => ({
        name: ratePct(r.rate),
        outstanding: Number(r.outstanding),
        count: r.count,
    }));
    const topLenders = data.top_lenders.map((t) => ({
        name: t.name,
        outstanding: Number(t.outstanding),
        accrued: Number(t.accrued_interest),
    }));

    return (
        <div>
            {/* KPI row */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12, marginBottom: 16 }}>
                <KpiCard label="Тело в займах" value={`${money(k.total_outstanding)} ₽`} icon="💼" color="#0071e3" sub={`${k.active_count} активных · ${k.lenders_count} заёмщиков`} />
                <KpiCard label="Средневзв. ставка" value={ratePct(k.weighted_avg_rate)} icon="📈" color="#5e5ce6" sub="годовых по активным" />
                <KpiCard label="Проценты в месяц" value={`${money(k.monthly_interest)} ₽`} icon="🗓️" color="#ff9500" sub="текущая нагрузка" />
                <KpiCard
                    label={`Расходы на займы · ${monthLabel(k.accrual_month)}`}
                    value={`${money(k.accrued_cost)} ₽`}
                    icon="⏳"
                    color="#ff3b30"
                    sub={`% ${money(k.accrued_interest)} ₽ · комиссии ${money(k.accrued_fee)} ₽ · прогноз ${money(k.month_cost_projected)} ₽`}
                />
                <KpiCard
                    label="Стоимость денег"
                    value={ratePct(k.effective_rate)}
                    icon="💹"
                    color="#af52de"
                    sub="во сколько реально обходятся деньги, годовых"
                />
                <KpiCard
                    label="Долг по процентам"
                    value={`${money(k.interest_debt)} ₽`}
                    icon="🧾"
                    color="#ff2d55"
                    sub="начислено за всё время минус уплачено"
                />
                <KpiCard
                    label="Ближайший возврат"
                    value={fmtDate(k.next_maturity_date)}
                    icon="📅"
                    color="#34c759"
                    sub={Number(k.next_maturity_amount) > 0 ? `${money(k.next_maturity_amount)} ₽` : '—'}
                />
            </div>

            {/* Dynamics chart */}
            <div className="glass-card" style={{ marginBottom: 16 }}>
                <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 4px' }}>Динамика тела и стоимости денег по месяцам</h3>
                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 12 }}>
                    Считается по календарным месяцам, по дням — поэтому её можно складывать с ОПиУ. У ВКЛ период начисления — месяц,
                    у частных займов — с 25-го по 25-е, у аннуитета свой график; в одну точку они не складываются.
                    {hasPartialMonth && ' * текущий месяц — не до конца.'}
                </div>
                <ResponsiveContainer width="100%" height={300}>
                    <ComposedChart data={monthly} margin={{ top: 8, right: 12, left: 8, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                        <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                        <YAxis yAxisId="left" tick={{ fontSize: 11 }} tickFormatter={(v) => money(v)} width={70} />
                        <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} tickFormatter={(v) => money(v)} width={60} />
                        <Tooltip formatter={(v: number, n) => [`${money(v)} ₽`, n === 'outstanding' ? 'Остаток тела' : n === 'interest' ? 'Проценты' : n === 'fee' ? 'Комиссии' : n]} />
                        <Legend formatter={(v) => (v === 'outstanding' ? 'Остаток тела' : v === 'interest' ? 'Проценты' : v === 'fee' ? 'Комиссии' : v)} />
                        <Area yAxisId="left" type="monotone" dataKey="outstanding" fill="#0071e3" stroke="#0071e3" fillOpacity={0.15} strokeWidth={2} />
                        <Bar yAxisId="right" dataKey="interest" stackId="cost" fill="#ff9500" barSize={14}>
                            {monthly.map((m, i) => (
                                <Cell key={i} fillOpacity={m.isPartial ? 0.45 : 1} />
                            ))}
                        </Bar>
                        <Bar yAxisId="right" dataKey="fee" stackId="cost" fill="#5e5ce6" barSize={14} radius={[4, 4, 0, 0]}>
                            {monthly.map((m, i) => (
                                <Cell key={i} fillOpacity={m.isPartial ? 0.45 : 1} />
                            ))}
                        </Bar>
                    </ComposedChart>
                </ResponsiveContainer>
            </div>

            {/* Стоимость денег за месяц — раздельно по ИП и физлицам */}
            {showEntitySplit && interestByEntity.length > 0 && (
                <div className="glass-card" style={{ marginBottom: 16 }}>
                    <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 4px' }}>Стоимость денег за месяц по сущности</h3>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 12 }}>
                        Крупная цифра — прогноз за весь календарный месяц; «набежало» — факт с 1-го числа по сегодня.
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
                        {interestByEntity.map((e) => (
                            <div key={e.key} style={{ padding: '12px 16px', border: '1px solid var(--color-border)', borderRadius: 12 }}>
                                <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>{e.label}</div>
                                <div style={{ fontSize: 22, fontWeight: 700 }}>{money(e.due)} ₽</div>
                                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>
                                    набежало {money(e.accrued)} ₽ · тело {money(e.outstanding)} ₽
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Two columns: by entity + by rate */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16, marginBottom: 16 }}>
                {showEntitySplit && <div className="glass-card">
                    <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 12px' }}>Структура: физлица / ИП</h3>
                    <ResponsiveContainer width="100%" height={240}>
                        <PieChart>
                            <Pie data={byEntity} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={88} label={(e) => e.name}>
                                {byEntity.map((_, i) => <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
                            </Pie>
                            <Tooltip formatter={(v: number) => `${money(v)} ₽`} />
                        </PieChart>
                    </ResponsiveContainer>
                </div>}
                <div className="glass-card">
                    <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 12px' }}>Остаток тела по ставкам</h3>
                    <ResponsiveContainer width="100%" height={240}>
                        <BarChart data={byRate} margin={{ top: 8, right: 12, left: 8, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                            <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                            <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => money(v)} width={70} />
                            <Tooltip formatter={(v: number, _n, p) => [`${money(v)} ₽ · ${p?.payload?.count} займ.`, 'Остаток']} />
                            <Bar dataKey="outstanding" fill="#5e5ce6" radius={[4, 4, 0, 0]} />
                        </BarChart>
                    </ResponsiveContainer>
                </div>
            </div>

            {/* Top lenders */}
            <div className="glass-card">
                <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 12px' }}>Топ заёмщиков по остатку</h3>
                <ResponsiveContainer width="100%" height={Math.max(160, topLenders.length * 34)}>
                    <BarChart data={topLenders} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" horizontal={false} />
                        <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={(v) => money(v)} />
                        <YAxis type="category" dataKey="name" tick={{ fontSize: 12 }} width={120} />
                        <Tooltip formatter={(v: number) => `${money(v)} ₽`} />
                        <Bar dataKey="outstanding" fill="#0071e3" radius={[0, 4, 4, 0]} barSize={18} />
                    </BarChart>
                </ResponsiveContainer>
            </div>
        </div>
    );
}
