'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import TanStackDataTable from '@/components/TanStackDataTable';
import { api } from '@/lib/api';
import type {
    CounterpartyListItem,
    PayrollEmployee,
    PayrollEmployeeUpdate,
    PayrollPayDayShare,
} from '@/types/api';
import { money, payScheduleLabel } from './payrollFmt';

export default function SalaryEmployees({
    nonce, onChanged,
}: { nonce: number; onChanged: () => void }) {
    const [items, setItems] = useState<PayrollEmployee[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [editEmp, setEditEmp] = useState<PayrollEmployee | null>(null);
    const [showCreate, setShowCreate] = useState(false);
    const [deleting, setDeleting] = useState<number | null>(null);

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.listPayrollEmployees();
            if (signal?.aborted) return;
            setItems(res.items);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load, nonce]);

    const remove = async (emp: PayrollEmployee) => {
        if (!confirm(`Удалить сотрудника «${emp.name}»?`)) return;
        setDeleting(emp.id);
        setError('');
        try {
            await api.deletePayrollEmployee(emp.id);
            await load();
            onChanged();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка удаления');
        } finally {
            setDeleting(null);
        }
    };

    // Подсказки должностей: пресеты + уникальные значения уже заведённых сотрудников.
    const positionOptions = useMemo(() => {
        // Без пресета «Менеджер»: в ОПиУ он бы встал вплотную к строке
        // «Менеджеры» (процент команд) — две почти одинаковые строки в P&L.
        const set = new Set(['Бухгалтер', 'Логист']);
        (items ?? []).forEach((e) => { if (e.position) set.add(e.position); });
        return [...set];
    }, [items]);

    const columns = [
        {
            key: 'name', label: 'ФИО',
            render: (v: string, r: PayrollEmployee) => (
                <span style={{ fontWeight: 600, opacity: r.is_active ? 1 : 0.5 }}>{v}</span>
            ),
        },
        {
            key: 'position', label: 'Должность',
            headerTitle: 'Группирует фикс-оклад в подстроках «ФОТ (начислено)» ОПиУ',
            getValue: (r: PayrollEmployee) => r.position ?? '',
            render: (_v: unknown, r: PayrollEmployee) => r.position
                ? r.position
                : <span style={{ color: 'var(--color-text-dim)' }}>—</span>,
        },
        {
            key: 'counterparty_name', label: 'Контрагент',
            headerTitle: 'По контрагенту из банковской выписки считается официальная часть зарплаты',
            getValue: (r: PayrollEmployee) => r.counterparty_name ?? '',
            render: (_v: unknown, r: PayrollEmployee) => r.counterparty_id == null
                ? (
                    <span className="badge badge-warning" title="Без привязки официальные выплаты не считаются">
                        ⚠ не привязан
                    </span>
                )
                : (r.counterparty_name ?? `#${r.counterparty_id}`),
        },
        {
            key: 'fixed_salary', label: 'Фикс-оклад', align: 'right' as const,
            getValue: (r: PayrollEmployee) => Number(r.fixed_salary ?? 0),
            render: (_v: unknown, r: PayrollEmployee) => r.fixed_salary == null
                ? <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                : `${money(r.fixed_salary)} ₽/мес`,
        },
        {
            key: 'schedule', label: 'График фикса',
            getValue: (r: PayrollEmployee) => (r.fixed_salary == null ? '' : payScheduleLabel(r.fixed_pay_days)),
            render: (_v: unknown, r: PayrollEmployee) => r.fixed_salary == null
                ? <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                : <span style={{ fontSize: 12.5 }}>{payScheduleLabel(r.fixed_pay_days)}</span>,
        },
        {
            key: 'team_names', label: 'Команды',
            getValue: (r: PayrollEmployee) => r.team_names.join(', '),
            render: (_v: unknown, r: PayrollEmployee) => r.team_names.length === 0
                ? <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                : (
                    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                        {r.team_names.map((n) => <span key={n} className="badge badge-info">{n}</span>)}
                    </div>
                ),
        },
        {
            key: 'is_active', label: 'Статус',
            getValue: (r: PayrollEmployee) => (r.is_active ? 'активен' : 'выключен'),
            render: (_v: unknown, r: PayrollEmployee) => (
                <span className={`badge ${r.is_active ? 'badge-success' : 'badge-secondary'}`}>
                    {r.is_active ? 'активен' : 'выключен'}
                </span>
            ),
        },
        {
            key: 'actions', label: '', sortable: false,
            exportValue: () => '',
            render: (_v: unknown, r: PayrollEmployee) => (
                <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => setEditEmp(r)}>✎</button>
                    <button
                        className="btn btn-danger btn-sm"
                        disabled={deleting === r.id}
                        onClick={() => remove(r)}
                    >
                        {deleting === r.id ? '…' : '🗑'}
                    </button>
                </div>
            ),
        },
    ];

    const rows = items ?? [];
    // Empty только когда данные реально загрузились: при ошибке items==null,
    // и показывать «Сотрудников пока нет» поверх баннера ошибки нельзя.
    const isEmpty = !loading && !error && items != null && rows.length === 0;

    return (
        <div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 16 }}>
                <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)}>
                    + Добавить сотрудника
                </button>
            </div>

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
                    <div style={{ fontSize: 40, marginBottom: 12 }}>🧑‍💼</div>
                    <div style={{ fontSize: 16, fontWeight: 600 }}>Сотрудников пока нет</div>
                    <div style={{ color: 'var(--color-text-dim)', marginTop: 6, marginBottom: 16 }}>
                        Добавь сотрудников, привяжи контрагентов из выписки и включи их в команды.
                    </div>
                    <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)}>+ Добавить сотрудника</button>
                </div>
            ) : rows.length > 0 ? (
                <TanStackDataTable
                    columns={columns}
                    data={rows}
                    exportName="payroll-employees"
                    enableSorting
                    enablePagination={false}
                />
            ) : null}

            {(showCreate || editEmp) && (
                <EmployeeFormModal
                    employee={editEmp ?? undefined}
                    positionOptions={positionOptions}
                    onClose={() => { setShowCreate(false); setEditEmp(null); }}
                    onSaved={async () => {
                        setShowCreate(false);
                        setEditEmp(null);
                        await load();
                        onChanged();
                    }}
                />
            )}
        </div>
    );
}

// ─── Модалка создания/редактирования сотрудника ──────────────────────────────

type ScheduleMode = 'default' | 'once15' | 'custom';

const DEFAULT_SCHEDULE: PayrollPayDayShare[] = [
    { day: 10, share: 0.5 },
    { day: 25, share: 0.5 },
];

function detectMode(days: PayrollPayDayShare[] | null | undefined): ScheduleMode {
    if (!days || days.length === 0) return 'default';
    if (days.length === 1 && days[0].day === 15 && Number(days[0].share) === 1) return 'once15';
    const sorted = [...days].sort((a, b) => a.day - b.day);
    if (
        sorted.length === 2 &&
        sorted[0].day === 10 && sorted[1].day === 25 &&
        Number(sorted[0].share) === 0.5 && Number(sorted[1].share) === 0.5
    ) return 'default';
    return 'custom';
}

interface CustomRow { day: string; sharePct: string; }

function EmployeeFormModal({
    employee, positionOptions, onClose, onSaved,
}: {
    employee?: PayrollEmployee;
    positionOptions: string[];
    onClose: () => void;
    onSaved: () => void | Promise<void>;
}) {
    const [name, setName] = useState(employee?.name ?? '');
    const [position, setPosition] = useState(employee?.position ?? '');
    const [isActive, setIsActive] = useState(employee?.is_active ?? true);
    const [notes, setNotes] = useState(employee?.notes ?? '');
    const [fixedSalary, setFixedSalary] = useState(
        employee?.fixed_salary != null ? String(Number(employee.fixed_salary)) : ''
    );
    const [mode, setMode] = useState<ScheduleMode>(detectMode(employee?.fixed_pay_days));
    const [customRows, setCustomRows] = useState<CustomRow[]>(
        employee?.fixed_pay_days?.length
            ? employee.fixed_pay_days.map((d) => ({
                day: String(d.day),
                sharePct: String(+(Number(d.share) * 100).toFixed(1)),
            }))
            : [{ day: '10', sharePct: '50' }, { day: '25', sharePct: '50' }]
    );

    // Поиск контрагента — существующий counterparty API (server-side, debounced).
    const [cpId, setCpId] = useState<number | null>(employee?.counterparty_id ?? null);
    const [cpName, setCpName] = useState(employee?.counterparty_name ?? '');
    const [cpSearch, setCpSearch] = useState('');
    const [cps, setCps] = useState<CounterpartyListItem[]>([]);
    const [cpError, setCpError] = useState('');

    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        // Контрагент уже привязан — поиск не нужен (и лишнего запроса на маунте нет).
        if (cpId != null) return;
        const controller = new AbortController();
        const handle = setTimeout(() => {
            api.listCounterparties({ q: cpSearch || undefined, limit: 300 })
                .then((r) => {
                    if (controller.signal.aborted) return;
                    setCps(r.items);
                    setCpError('');
                })
                .catch(() => {
                    if (controller.signal.aborted) return;
                    setCpError('Не удалось загрузить контрагентов');
                });
        }, 250);
        return () => {
            controller.abort();
            clearTimeout(handle);
        };
    }, [cpSearch, cpId]);

    const buildSchedule = (): { schedule: PayrollPayDayShare[] | null; err?: string } => {
        if (mode === 'default') return { schedule: employee ? DEFAULT_SCHEDULE : null };
        if (mode === 'once15') return { schedule: [{ day: 15, share: 1 }] };
        if (customRows.length === 0) return { schedule: null, err: 'Добавьте хотя бы одну строку графика' };
        const rows = customRows.map((r) => ({ day: Number(r.day), share: Number(r.sharePct) / 100 }));
        if (rows.some((r) => !Number.isInteger(r.day) || r.day < 1 || r.day > 28)) {
            return { schedule: null, err: 'День выплаты — целое число от 1 до 28' };
        }
        if (new Set(rows.map((r) => r.day)).size !== rows.length) {
            return { schedule: null, err: 'Дни выплат не должны повторяться' };
        }
        if (rows.some((r) => !(r.share > 0) || r.share > 1)) {
            return { schedule: null, err: 'Доля каждой выплаты — от 0 до 100%' };
        }
        const total = rows.reduce((s, r) => s + r.share, 0);
        if (Math.abs(total - 1) > 0.001) {
            return { schedule: null, err: `Сумма долей должна быть 100% (сейчас ${(total * 100).toFixed(1)}%)` };
        }
        return { schedule: rows };
    };

    const submit = async () => {
        if (!name.trim()) { setError('Укажите ФИО'); return; }
        const salary = fixedSalary.trim() === '' ? null : Number(fixedSalary);
        if (salary != null && (!Number.isFinite(salary) || salary < 0)) {
            setError('Фикс-оклад — неотрицательное число');
            return;
        }
        const { schedule, err } = buildSchedule();
        if (salary != null && err) { setError(err); return; }

        setSaving(true);
        setError('');
        try {
            if (employee) {
                const payload: PayrollEmployeeUpdate = {
                    name: name.trim(),
                    is_active: isActive,
                    notes: notes.trim() || null,
                };
                if (position.trim()) payload.position = position.trim();
                else if (employee.position != null) payload.clear_position = true;
                if (cpId != null) payload.counterparty_id = cpId;
                else if (employee.counterparty_id != null) payload.clear_counterparty = true;
                if (salary != null) {
                    payload.fixed_salary = salary;
                    payload.fixed_pay_days = schedule;
                } else if (employee.fixed_salary != null) {
                    payload.clear_fixed_salary = true;
                }
                await api.updatePayrollEmployee(employee.id, payload);
            } else {
                await api.createPayrollEmployee({
                    name: name.trim(),
                    position: position.trim() || null,
                    counterparty_id: cpId,
                    fixed_salary: salary,
                    fixed_pay_days: salary != null ? schedule : null,
                    is_active: isActive,
                    notes: notes.trim() || null,
                });
            }
            await onSaved();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
            setSaving(false);
        }
    };

    const setRow = (i: number, patch: Partial<CustomRow>) =>
        setCustomRows((rows) => rows.map((r, ri) => (ri === i ? { ...r, ...patch } : r)));

    const customTotal = customRows.reduce((s, r) => s + (Number(r.sharePct) || 0), 0);

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card modal-card-solid modal-card-wide" style={{ maxHeight: '90vh', overflow: 'auto' }} onClick={(e) => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <h3 style={{ margin: 0, fontSize: 17, fontWeight: 700 }}>
                        {employee ? `Сотрудник «${employee.name}»` : 'Новый сотрудник'}
                    </h3>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>✕</button>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <div className="form-group">
                        <label className="form-label">ФИО *</label>
                        <input className="form-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Иванова Анна" />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Должность</label>
                        <input
                            className="form-input"
                            list="payroll-position-options"
                            maxLength={100}
                            placeholder="Менеджер / Бухгалтер / Логист…"
                            value={position}
                            onChange={(e) => setPosition(e.target.value)}
                        />
                        <datalist id="payroll-position-options">
                            {positionOptions.map((p) => <option key={p} value={p} />)}
                        </datalist>
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>
                            Группирует фикс-оклад в ФОТ ОПиУ; процент от команд всегда идёт строкой «Менеджеры».
                        </div>
                    </div>

                    <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                        <label className="form-label">
                            Контрагент (для официальной части из выписки)
                        </label>
                        {cpId != null ? (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <span className="badge badge-success" style={{ fontSize: 13 }}>
                                    {cpName || `#${cpId}`}
                                </span>
                                <button className="btn btn-secondary btn-sm" onClick={() => { setCpId(null); setCpName(''); }}>
                                    Отвязать
                                </button>
                            </div>
                        ) : (
                            <>
                                <input
                                    className="form-input"
                                    placeholder="Поиск контрагента по имени / ИНН…"
                                    value={cpSearch}
                                    onChange={(e) => setCpSearch(e.target.value)}
                                    style={{ marginBottom: 6 }}
                                />
                                {cpError && <div style={{ color: 'var(--color-danger)', fontSize: 12.5, marginBottom: 6 }}>{cpError}</div>}
                                <select
                                    className="form-input"
                                    value=""
                                    onChange={(e) => {
                                        const id = Number(e.target.value);
                                        if (!id) return;
                                        setCpId(id);
                                        setCpName(cps.find((c) => c.id === id)?.name ?? '');
                                    }}
                                >
                                    <option value="">— выбрать контрагента —</option>
                                    {cps.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                                </select>
                                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>
                                    Без привязки официальные выплаты из банковской выписки не считаются.
                                </div>
                            </>
                        )}
                    </div>

                    <div className="form-group">
                        <label className="form-label">Фикс-оклад, ₽/мес (опционально)</label>
                        <input
                            type="number"
                            min={0}
                            className="form-input"
                            placeholder="50 000"
                            value={fixedSalary}
                            onChange={(e) => setFixedSalary(e.target.value)}
                        />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Заметки</label>
                        <input className="form-input" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="бухгалтер / логист…" />
                    </div>

                    {fixedSalary.trim() !== '' && (
                        <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                            <label className="form-label">График выплат фикса</label>
                            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 8 }}>
                                {([
                                    ['default', '50/50 на 10-е и 25-е'],
                                    ['once15', 'разово 15-го'],
                                    ['custom', 'произвольный'],
                                ] as [ScheduleMode, string][]).map(([m, label]) => (
                                    <label key={m} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                                        <input type="radio" name="schedule-mode" checked={mode === m} onChange={() => setMode(m)} />
                                        {label}
                                    </label>
                                ))}
                            </div>
                            {mode === 'custom' && (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                                    {customRows.map((r, i) => (
                                        <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                                            <input
                                                type="number" min={1} max={28} className="form-input" style={{ width: 100 }}
                                                value={r.day} onChange={(e) => setRow(i, { day: e.target.value })}
                                            />
                                            <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>-го числа —</span>
                                            <input
                                                type="number" min={0} max={100} step={0.1} className="form-input" style={{ width: 100 }}
                                                value={r.sharePct} onChange={(e) => setRow(i, { sharePct: e.target.value })}
                                            />
                                            <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>%</span>
                                            <button
                                                className="btn btn-secondary btn-sm"
                                                onClick={() => setCustomRows((rows) => rows.filter((_, ri) => ri !== i))}
                                            >
                                                ✕
                                            </button>
                                        </div>
                                    ))}
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                                        <button
                                            className="btn btn-secondary btn-sm"
                                            onClick={() => setCustomRows((rows) => [...rows, { day: '', sharePct: '' }])}
                                        >
                                            + Добавить выплату
                                        </button>
                                        <span style={{
                                            fontSize: 12.5,
                                            color: Math.abs(customTotal - 100) <= 0.1 ? 'var(--color-success)' : 'var(--color-danger)',
                                        }}>
                                            Сумма долей: {money(customTotal, 1)}%
                                        </span>
                                    </div>
                                </div>
                            )}
                        </div>
                    )}
                </div>

                {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 10 }}>{error}</div>}
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 16 }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer', marginRight: 'auto' }}>
                        <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
                        Активен
                    </label>
                    <button className="btn btn-secondary btn-sm" onClick={onClose} disabled={saving}>Отмена</button>
                    <button className="btn btn-primary btn-sm" onClick={submit} disabled={saving}>
                        {saving ? 'Сохранение…' : 'Сохранить'}
                    </button>
                </div>
            </div>
        </div>
    );
}
