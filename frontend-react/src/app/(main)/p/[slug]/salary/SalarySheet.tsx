'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import TanStackDataTable from '@/components/TanStackDataTable';
import KpiCard from '@/components/KpiCard';
import { api } from '@/lib/api';
import { formatDate, pluralRu, type ExcelExtraSheet } from '@/lib/utils';
import type {
    PayrollSheetEmployee,
    PayrollSheetPayout,
    PayrollSheetResponse,
    PayrollSheetTeam,
} from '@/types/api';
import {
    money, ratePct, monthTitle, weekLabel, dayMonthLabel,
    prevMonthIso, shiftMonth, scopeLabel, scopeBadgeClass,
} from './payrollFmt';

const teamTotal = (e: PayrollSheetEmployee): number =>
    e.team_accruals.reduce((s, a) => s + Number(a.amount), 0);

export default function SalarySheet({
    nonce, onNavigate,
}: { nonce: number; onNavigate: (tab: string) => void }) {
    const [month, setMonth] = useState(prevMonthIso());
    const [data, setData] = useState<PayrollSheetResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [markError, setMarkError] = useState('');
    const [marking, setMarking] = useState<Set<string>>(new Set());
    const [txnEmp, setTxnEmp] = useState<PayrollSheetEmployee | null>(null);

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.payrollSheet(month);
            if (signal?.aborted) return;
            setData(res);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [month]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load, nonce]);

    // ─── Отметка выплаты: optimistic + revert on error ───────────────────────
    const patchPaid = (empId: number, payDay: number, paid: boolean) => {
        setData((prev) => prev && {
            ...prev,
            employees: prev.employees.map((e) => (e.employee_id !== empId ? e : {
                ...e,
                payouts: e.payouts.map((p) => (p.pay_day !== payDay ? p : { ...p, paid })),
            })),
        });
    };

    const togglePayout = async (emp: PayrollSheetEmployee, payout: PayrollSheetPayout) => {
        if (!data) return;
        const key = `${emp.employee_id}:${payout.pay_day}`;
        if (marking.has(key)) return;
        const next = !payout.paid;
        setMarking((prev) => new Set(prev).add(key));
        setMarkError('');
        patchPaid(emp.employee_id, payout.pay_day, next);
        try {
            await api.markPayrollPayout({
                employee_id: emp.employee_id,
                month: data.month,
                pay_day: payout.pay_day,
                paid: next,
            });
        } catch (e: unknown) {
            patchPaid(emp.employee_id, payout.pay_day, payout.paid);
            setMarkError(e instanceof Error ? e.message : 'Не удалось сохранить отметку выплаты');
        } finally {
            setMarking((prev) => {
                const s = new Set(prev);
                s.delete(key);
                return s;
            });
        }
    };

    const employees = useMemo(() => data?.employees ?? [], [data]);
    const teams = data?.teams ?? [];
    const isEmpty = !loading && !error && data != null
        && employees.length === 0 && teams.length === 0;

    // Недельный расчёт команд (база/ступень/ставка/на человека) — отдельным
    // листом в Excel-экспорте основной ведомости.
    const teamWeekSheets = useMemo<ExcelExtraSheet[]>(() => {
        if (!data || data.teams.length === 0) return [];
        return [{
            sheetName: 'Команды по неделям',
            data: data.teams.flatMap((t) => [
                ...t.weeks.map((w) => ({
                    'Команда': t.name,
                    'Неделя': weekLabel(w.date_from, w.date_to),
                    'База «Чистая выплата», ₽': Number(w.base_amount),
                    'Ступень (порог от), ₽': w.threshold == null ? '' : Number(w.threshold),
                    'Ставка, %': Number(w.team_rate) * 100,
                    'Команде, ₽': Number(w.team_amount),
                    'На человека, ₽': Number(w.per_member),
                })),
                {
                    'Команда': t.name,
                    'Неделя': 'Итого',
                    'База «Чистая выплата», ₽': '' as const,
                    'Ступень (порог от), ₽': '' as const,
                    'Ставка, %': '' as const,
                    'Команде, ₽': Number(t.total_amount),
                    'На человека, ₽': t.member_names.length > 0
                        ? Number(t.total_amount) / t.member_names.length
                        : '',
                },
            ]),
        }];
    }, [data]);

    const columns = [
        {
            key: 'name', label: 'Сотрудник',
            render: (_v: unknown, r: PayrollSheetEmployee) => (
                <div>
                    <div style={{ fontWeight: 600 }}>{r.name}</div>
                    {(r.position || r.counterparty_id == null) && (
                        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 2 }}>
                            {r.position && (
                                <span className="badge badge-secondary" style={{ fontSize: 10 }}>{r.position}</span>
                            )}
                            {r.counterparty_id == null && (
                                <span
                                    className="badge badge-warning"
                                    style={{ fontSize: 10 }}
                                    title="Контрагент не привязан — официальные выплаты из выписки не подтягиваются"
                                >
                                    ⚠ нет контрагента
                                </span>
                            )}
                        </div>
                    )}
                </div>
            ),
        },
        {
            key: 'teams', label: 'Команды',
            getValue: (r: PayrollSheetEmployee) => r.team_accruals.map((a) => a.team_name).join(', '),
            render: (_v: unknown, r: PayrollSheetEmployee) => r.team_accruals.length === 0
                ? <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                : (
                    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                        {r.team_accruals.map((a) => (
                            <span key={a.team_id} className="badge badge-info">{a.team_name}</span>
                        ))}
                    </div>
                ),
        },
        {
            key: 'team_total', label: 'По командам', align: 'right' as const,
            getValue: (r: PayrollSheetEmployee) => teamTotal(r),
            render: (_v: unknown, r: PayrollSheetEmployee) => `${money(teamTotal(r))} ₽`,
        },
        {
            key: 'fixed_accrual', label: 'Фикс', align: 'right' as const,
            getValue: (r: PayrollSheetEmployee) => Number(r.fixed_accrual),
            render: (_v: unknown, r: PayrollSheetEmployee) => Number(r.fixed_accrual)
                ? `${money(r.fixed_accrual)} ₽`
                : <span style={{ color: 'var(--color-text-dim)' }}>—</span>,
        },
        {
            key: 'accrued_total', label: 'Итого начислено', align: 'right' as const,
            getValue: (r: PayrollSheetEmployee) => Number(r.accrued_total),
            render: (_v: unknown, r: PayrollSheetEmployee) => (
                <span style={{ fontWeight: 700 }}>{money(r.accrued_total)} ₽</span>
            ),
        },
        {
            key: 'official_paid', label: 'Официально', align: 'right' as const,
            headerTitle: 'Факт из банковской выписки по привязанному контрагенту (месяц выплат). Клик — список транзакций.',
            getValue: (r: PayrollSheetEmployee) => Number(r.official_paid),
            render: (_v: unknown, r: PayrollSheetEmployee) => r.official_txns.length > 0
                ? (
                    <button
                        onClick={() => setTxnEmp(r)}
                        title="Показать транзакции из выписки"
                        style={{
                            background: 'none', border: 'none', padding: 0, cursor: 'pointer',
                            color: 'var(--color-accent)', fontWeight: 600, textDecoration: 'underline',
                            textDecorationStyle: 'dotted', textUnderlineOffset: 3,
                        }}
                    >
                        {money(r.official_paid)} ₽
                    </button>
                )
                : <span style={{ color: 'var(--color-text-dim)' }}>{money(r.official_paid)} ₽</span>,
        },
        {
            key: 'unofficial_due', label: 'Неофициально', align: 'right' as const,
            headerTitle: 'Начислено минус официально выплачено',
            getValue: (r: PayrollSheetEmployee) => Number(r.unofficial_due),
            render: (_v: unknown, r: PayrollSheetEmployee) => {
                const v = Number(r.unofficial_due);
                const color = v > 0 ? 'var(--color-warning)' : v < 0 ? 'var(--color-danger)' : 'var(--color-text-dim)';
                return <span style={{ fontWeight: 600, color }}>{money(v)} ₽</span>;
            },
        },
        {
            key: 'payouts', label: 'План выплат', sortable: false,
            headerTitle: 'Выплата 10-го и 25-го следующего месяца 50/50 (фикс — по своему графику). Чекбокс — отметка «выплачено».',
            getValue: (r: PayrollSheetEmployee) =>
                r.payouts.map((p) => `${dayMonthLabel(p.pay_date)}: ${money(p.amount)}${p.paid ? ' ✓' : ''}`).join('; '),
            render: (_v: unknown, r: PayrollSheetEmployee) => r.payouts.length === 0
                ? <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                        {r.payouts.map((p) => {
                            const busy = marking.has(`${r.employee_id}:${p.pay_day}`);
                            return (
                                <label
                                    key={p.pay_day}
                                    title={p.comment ?? undefined}
                                    style={{
                                        display: 'flex', alignItems: 'center', gap: 6,
                                        fontSize: 12.5, cursor: busy ? 'wait' : 'pointer',
                                        whiteSpace: 'nowrap', opacity: busy ? 0.5 : 1,
                                    }}
                                >
                                    <input
                                        type="checkbox"
                                        checked={p.paid}
                                        disabled={busy}
                                        onChange={() => togglePayout(r, p)}
                                    />
                                    <span style={{
                                        color: p.paid ? 'var(--color-success)' : 'var(--color-text)',
                                        minWidth: 72,
                                    }}>
                                        {dayMonthLabel(p.pay_date)}
                                    </span>
                                    <span style={{ fontWeight: 600 }}>
                                        {money(p.paid && p.paid_amount != null ? p.paid_amount : p.amount)} ₽
                                    </span>
                                </label>
                            );
                        })}
                    </div>
                ),
        },
    ];

    return (
        <div>
            {/* ─── Месяц ─────────────────────────────────────────────────────── */}
            <div className="glass-card" style={{ marginBottom: 16, padding: '12px 16px' }}>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => setMonth((m) => shiftMonth(m, -1))} title="Предыдущий месяц">‹</button>
                    <input
                        type="month"
                        className="form-input"
                        style={{ width: 170 }}
                        value={month}
                        onChange={(e) => e.target.value && setMonth(e.target.value)}
                    />
                    <button className="btn btn-secondary btn-sm" onClick={() => setMonth((m) => shiftMonth(m, 1))} title="Следующий месяц">›</button>
                    <span style={{ fontWeight: 700, fontSize: 15 }}>{monthTitle(month)}</span>
                    {data && data.weeks.length > 0 && (
                        <span style={{ marginLeft: 'auto', color: 'var(--color-text-dim)', fontSize: 12.5 }} title="Недели Пн-Вс, привязанные к месяцу по четвергу">
                            {data.weeks.length} {pluralRu(data.weeks.length, ['неделя', 'недели', 'недель'])}:{' '}
                            {data.weeks.map((w) => weekLabel(w.date_from, w.date_to)).join(' · ')}
                        </span>
                    )}
                </div>
            </div>

            {markError && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 16, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span>⚠ {markError}</span>
                    <button className="btn btn-secondary btn-sm" onClick={() => setMarkError('')}>Закрыть</button>
                </div>
            )}

            {error && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between' }}>
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={() => load()}>Повторить</button>
                </div>
            )}

            {loading ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка…</div>
            ) : isEmpty ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                    <div style={{ fontSize: 40, marginBottom: 12 }}>💸</div>
                    <div style={{ fontSize: 16, fontWeight: 600 }}>Ведомость за {monthTitle(month)} пуста</div>
                    <div style={{ color: 'var(--color-text-dim)', marginTop: 6, marginBottom: 16 }}>
                        Заведи сотрудников и команды на соседних табах — начисления посчитаются автоматически.
                    </div>
                    <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
                        <button className="btn btn-primary btn-sm" onClick={() => onNavigate('employees')}>🧑‍💼 К сотрудникам</button>
                        <button className="btn btn-secondary btn-sm" onClick={() => onNavigate('teams')}>👥 К командам</button>
                    </div>
                </div>
            ) : data && (
                <>
                    {/* ─── KPI ───────────────────────────────────────────────── */}
                    <div className="stats-grid" style={{ marginBottom: 20 }}>
                        <KpiCard
                            label="Начислено всего"
                            icon="🧮"
                            value={`${money(data.totals.accrued_total, 0)} ₽`}
                            sub="команды + фикс-оклады за месяц"
                        />
                        <KpiCard
                            label="Официально выплачено"
                            icon="🏦"
                            value={`${money(data.totals.official_total, 0)} ₽`}
                            color="#22c55e"
                            sub="факт из банковской выписки"
                        />
                        <KpiCard
                            label="Неофициально к выплате"
                            icon="✉️"
                            value={`${money(data.totals.unofficial_total, 0)} ₽`}
                            color="#f59e0b"
                            sub="начислено − официально"
                        />
                    </div>

                    {/* ─── Сотрудники ────────────────────────────────────────── */}
                    {employees.length === 0 ? (
                        <div className="glass-card" style={{ padding: 32, textAlign: 'center', marginBottom: 20 }}>
                            <div style={{ fontSize: 32, marginBottom: 8 }}>🧑‍💼</div>
                            <div style={{ fontWeight: 600 }}>Нет сотрудников с начислениями</div>
                            <div style={{ color: 'var(--color-text-dim)', marginTop: 4 }}>
                                Добавь сотрудников на табе «Сотрудники» и включи их в команды.
                            </div>
                        </div>
                    ) : (
                        <div style={{ marginBottom: 24 }}>
                            <TanStackDataTable
                                title={`Ведомость · ${monthTitle(data.month)}`}
                                columns={columns}
                                data={employees}
                                exportName={`payroll-${data.month}`}
                                exportAdditionalSheets={teamWeekSheets}
                                enableSorting
                                enablePagination={false}
                                getRowId={(r: PayrollSheetEmployee) => String(r.employee_id)}
                                renderSubRow={(r: PayrollSheetEmployee) => <EmployeeDetail emp={r} />}
                            />
                        </div>
                    )}

                    {/* ─── Команды за месяц ──────────────────────────────────── */}
                    <div className="section-title" style={{ marginBottom: 12 }}>Команды за месяц</div>
                    {teams.length === 0 ? (
                        <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>
                            <div style={{ fontSize: 32, marginBottom: 8 }}>👥</div>
                            <div style={{ fontWeight: 600 }}>Нет команд с начислениями за {monthTitle(month)}</div>
                            <div style={{ color: 'var(--color-text-dim)', marginTop: 4 }}>
                                Создай команду и привяжи бренды/категории на табе «Команды».
                            </div>
                        </div>
                    ) : (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                            {teams.map((t) => <TeamMonthCard key={t.team_id} team={t} />)}
                        </div>
                    )}
                </>
            )}

            {/* ─── Транзакции из выписки ─────────────────────────────────────── */}
            {txnEmp && (
                <div className="modal-overlay" onClick={() => setTxnEmp(null)}>
                    <div className="modal-card modal-card-solid" style={{ maxHeight: '80vh', overflow: 'auto', minWidth: 420 }} onClick={(e) => e.stopPropagation()}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>🏦 Официальные выплаты · {txnEmp.name}</h3>
                            <button className="btn btn-secondary btn-sm" onClick={() => setTxnEmp(null)}>✕</button>
                        </div>
                        <table className="data-table" style={{ width: '100%' }}>
                            <thead>
                                <tr>
                                    <th>Дата</th>
                                    <th style={{ textAlign: 'right' }}>Сумма</th>
                                    <th>Банк</th>
                                    <th>Назначение</th>
                                </tr>
                            </thead>
                            <tbody>
                                {txnEmp.official_txns.map((tx, i) => (
                                    <tr key={i}>
                                        <td>{formatDate(tx.date)}</td>
                                        <td style={{ textAlign: 'right', fontWeight: 600 }}>{money(tx.amount)} ₽</td>
                                        <td>{tx.bank}</td>
                                        <td style={{ maxWidth: 280, fontSize: 12.5, color: 'var(--color-text-muted)' }}>{tx.purpose ?? '—'}</td>
                                    </tr>
                                ))}
                                <tr>
                                    <td style={{ fontWeight: 700 }}>Итого</td>
                                    <td style={{ textAlign: 'right', fontWeight: 700 }}>{money(txnEmp.official_paid)} ₽</td>
                                    <td colSpan={2} />
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
}

// ─── Детализация сотрудника (раскрытие строки) ───────────────────────────────

function EmployeeDetail({ emp }: { emp: PayrollSheetEmployee }) {
    return (
        <div style={{ display: 'flex', gap: 32, flexWrap: 'wrap', padding: '12px 8px' }}>
            <div style={{ minWidth: 240 }}>
                <div style={{ fontWeight: 600, fontSize: 12.5, color: 'var(--color-text-muted)', marginBottom: 6 }}>НАЧИСЛЕНИЯ</div>
                {emp.team_accruals.map((a) => (
                    <div key={a.team_id} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, fontSize: 13, padding: '2px 0' }}>
                        <span>{a.team_name}</span>
                        <span style={{ fontWeight: 600 }}>{money(a.amount)} ₽</span>
                    </div>
                ))}
                {Number(emp.fixed_accrual) !== 0 && (
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, fontSize: 13, padding: '2px 0' }}>
                        <span>Фикс-оклад</span>
                        <span style={{ fontWeight: 600 }}>{money(emp.fixed_accrual)} ₽</span>
                    </div>
                )}
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, fontSize: 13, padding: '4px 0', borderTop: '1px solid var(--color-border)', marginTop: 4 }}>
                    <span style={{ fontWeight: 700 }}>Итого</span>
                    <span style={{ fontWeight: 700 }}>{money(emp.accrued_total)} ₽</span>
                </div>
            </div>

            <div style={{ minWidth: 280 }}>
                <div style={{ fontWeight: 600, fontSize: 12.5, color: 'var(--color-text-muted)', marginBottom: 6 }}>ПЛАН ВЫПЛАТ</div>
                {emp.payouts.length === 0 ? (
                    <div style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Нет запланированных выплат</div>
                ) : emp.payouts.map((p) => (
                    <div key={p.pay_day} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, fontSize: 13, padding: '2px 0' }}>
                        <span>
                            {p.paid ? '✅' : '⬜'} {dayMonthLabel(p.pay_date)}
                            {p.comment && <span style={{ color: 'var(--color-text-dim)' }}> · {p.comment}</span>}
                        </span>
                        <span style={{ fontWeight: 600 }}>
                            {money(p.amount)} ₽
                            {p.paid && p.paid_amount != null && Number(p.paid_amount) !== Number(p.amount) && (
                                <span style={{ color: 'var(--color-text-dim)' }}> (факт {money(p.paid_amount)} ₽)</span>
                            )}
                        </span>
                    </div>
                ))}
            </div>

            <div style={{ minWidth: 280 }}>
                <div style={{ fontWeight: 600, fontSize: 12.5, color: 'var(--color-text-muted)', marginBottom: 6 }}>ОФИЦИАЛЬНО (ИЗ ВЫПИСКИ)</div>
                {emp.official_txns.length === 0 ? (
                    <div style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>
                        {emp.counterparty_id == null
                            ? 'Контрагент не привязан — привяжи на табе «Сотрудники»'
                            : 'Транзакций за месяц выплат не найдено'}
                    </div>
                ) : emp.official_txns.map((tx, i) => (
                    <div key={i} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, fontSize: 13, padding: '2px 0' }}>
                        <span>{formatDate(tx.date)} · {tx.bank}</span>
                        <span style={{ fontWeight: 600 }}>{money(tx.amount)} ₽</span>
                    </div>
                ))}
            </div>
        </div>
    );
}

// ─── Карточка команды с недельной таблицей ───────────────────────────────────

function TeamMonthCard({ team }: { team: PayrollSheetTeam }) {
    return (
        <div className="glass-card static" style={{ padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
                <span style={{ fontWeight: 700, fontSize: 15 }}>{team.name}</span>
                {team.member_names.length > 0 && (
                    <span style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
                        {team.member_names.join(' + ')}
                    </span>
                )}
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                    {team.scopes.map((s, i) => (
                        <span key={i} className={scopeBadgeClass(s.kind)}>{scopeLabel(s)}</span>
                    ))}
                </div>
                <span style={{ marginLeft: 'auto', fontWeight: 700, fontSize: 15 }}>
                    {money(team.total_amount)} ₽
                </span>
            </div>
            <div style={{ overflowX: 'auto' }}>
                <table className="data-table" style={{ width: '100%' }}>
                    <thead>
                        <tr>
                            <th>Неделя</th>
                            <th style={{ textAlign: 'right' }} title="«Чистая выплата» WB по скоупам команды за неделю">База «Чистая выплата»</th>
                            <th style={{ textAlign: 'right' }} title="Ближайший порог лестницы снизу">Ступень</th>
                            <th style={{ textAlign: 'right' }}>Ставка</th>
                            <th style={{ textAlign: 'right' }}>Команде</th>
                            <th style={{ textAlign: 'right' }}>На человека</th>
                        </tr>
                    </thead>
                    <tbody>
                        {team.weeks.map((w, i) => {
                            const base = Number(w.base_amount);
                            return (
                                <tr key={i}>
                                    <td>{weekLabel(w.date_from, w.date_to)}</td>
                                    <td style={{ textAlign: 'right', color: base < 0 ? 'var(--color-danger)' : undefined }}>
                                        {money(w.base_amount)} ₽
                                    </td>
                                    <td style={{ textAlign: 'right', color: w.threshold == null ? 'var(--color-text-dim)' : undefined }}>
                                        {w.threshold == null ? '—' : `от ${money(w.threshold, 0)} ₽`}
                                    </td>
                                    <td style={{ textAlign: 'right' }}>{ratePct(w.team_rate)}</td>
                                    <td style={{ textAlign: 'right', fontWeight: 600 }}>{money(w.team_amount)} ₽</td>
                                    <td style={{ textAlign: 'right' }}>{money(w.per_member)} ₽</td>
                                </tr>
                            );
                        })}
                        <tr>
                            <td style={{ fontWeight: 700 }}>Итого</td>
                            <td colSpan={3} />
                            <td style={{ textAlign: 'right', fontWeight: 700 }}>{money(team.total_amount)} ₽</td>
                            <td style={{ textAlign: 'right', fontWeight: 700 }}>
                                {team.member_names.length > 0
                                    ? `${money(Number(team.total_amount) / team.member_names.length)} ₽`
                                    : '—'}
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    );
}
