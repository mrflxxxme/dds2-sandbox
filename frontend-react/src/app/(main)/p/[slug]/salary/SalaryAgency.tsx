'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import TanStackDataTable from '@/components/TanStackDataTable';
import KpiCard from '@/components/KpiCard';
import { api } from '@/lib/api';
import { type ExcelExtraSheet } from '@/lib/utils';
import type {
    PayrollAgencySheetClient,
    PayrollAgencySheetResponse,
    PayrollBillingMode,
    PayrollClientEntryKind,
    PayrollClientProject,
    PayrollClientProjectUpdate,
    PayrollProjectOption,
    PayrollTeam,
} from '@/types/api';
import { money, ratePct, monthTitle, weekLabel, prevMonthIso, shiftMonth } from './payrollFmt';

const MODE_LABEL: Record<PayrollBillingMode, string> = {
    fixed: 'Фикс',
    percent: 'Процент',
    profit_share: 'От ЧП',
};

const MODE_BADGE: Record<PayrollBillingMode, string> = {
    fixed: 'badge badge-secondary',
    percent: 'badge badge-info',
    profit_share: 'badge badge-success',
};

const entryKey = (clientId: number, kind: string, dateFrom: string) => `${clientId}:${kind}:${dateFrom}`;

export default function SalaryAgency({
    nonce, onChanged,
}: { nonce: number; onChanged: () => void }) {
    const [month, setMonth] = useState(prevMonthIso());
    const [sheet, setSheet] = useState<PayrollAgencySheetResponse | null>(null);
    const [sheetLoading, setSheetLoading] = useState(true);
    const [sheetError, setSheetError] = useState('');

    const [clients, setClients] = useState<PayrollClientProject[] | null>(null);
    const [teams, setTeams] = useState<PayrollTeam[]>([]);
    const [projectOptions, setProjectOptions] = useState<PayrollProjectOption[]>([]);
    const [regLoading, setRegLoading] = useState(true);
    const [regError, setRegError] = useState('');

    const [editClient, setEditClient] = useState<PayrollClientProject | null>(null);
    const [showCreate, setShowCreate] = useState(false);
    const [deleting, setDeleting] = useState<number | null>(null);

    // Ручной ввод (ЧП месяца / недельные базы): драфты + optimistic commit.
    const [drafts, setDrafts] = useState<Record<string, string>>({});
    const [pendingEntries, setPendingEntries] = useState<Set<string>>(new Set());
    const [entryError, setEntryError] = useState('');

    const loadSheet = useCallback(async (signal?: AbortSignal, quiet = false) => {
        if (!quiet) setSheetLoading(true);
        setSheetError('');
        try {
            const res = await api.payrollAgencySheet(month);
            if (signal?.aborted) return;
            setSheet(res);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setSheetError(e instanceof Error ? e.message : 'Ошибка загрузки расчёта');
        } finally {
            if (!quiet && !signal?.aborted) setSheetLoading(false);
        }
    }, [month]);

    const loadRegistry = useCallback(async (signal?: AbortSignal) => {
        setRegLoading(true);
        setRegError('');
        try {
            const [clientsRes, teamsRes, optionsRes] = await Promise.all([
                api.listPayrollAgencyClients(),
                api.listPayrollTeams(),
                api.payrollAgencyProjectOptions(),
            ]);
            if (signal?.aborted) return;
            setClients(clientsRes.items);
            setTeams(teamsRes.items);
            setProjectOptions(optionsRes.items);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setRegError(e instanceof Error ? e.message : 'Ошибка загрузки клиентов');
        } finally {
            if (!signal?.aborted) setRegLoading(false);
        }
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        loadSheet(controller.signal);
        return () => controller.abort();
    }, [loadSheet, nonce]);

    useEffect(() => {
        const controller = new AbortController();
        loadRegistry(controller.signal);
        return () => controller.abort();
    }, [loadRegistry, nonce]);

    // ─── Ручной ввод: optimistic (драфт остаётся в инпуте) + revert on error ──
    const commitEntry = async (
        clientId: number, kind: PayrollClientEntryKind, dateFrom: string,
        raw: string, prevServer: number | string | null,
    ) => {
        const key = entryKey(clientId, kind, dateFrom);
        if (pendingEntries.has(key)) return;
        const trimmed = raw.trim();
        const prevStr = prevServer == null ? '' : String(Number(prevServer));
        const clearDraft = () => setDrafts((d) => {
            const next = { ...d };
            delete next[key];
            return next;
        });
        if (trimmed === prevStr) { clearDraft(); return; }
        const amount = trimmed === '' ? null : Number(trimmed);
        if (amount != null && (!Number.isFinite(amount) || amount < 0)) {
            setEntryError('Сумма — неотрицательное число');
            return; // драфт остаётся — юзер поправит
        }
        setPendingEntries((s) => new Set(s).add(key));
        setEntryError('');
        try {
            if (amount == null) {
                await api.deletePayrollAgencyEntry(clientId, kind, dateFrom);
            } else {
                await api.upsertPayrollAgencyEntry(clientId, { kind, date_from: dateFrom, amount });
            }
            await loadSheet(undefined, true); // тихо подтянуть пересчитанные fee
            clearDraft();
        } catch (e: unknown) {
            clearDraft(); // revert к серверному значению
            setEntryError(e instanceof Error ? e.message : 'Не удалось сохранить сумму');
        } finally {
            setPendingEntries((s) => {
                const next = new Set(s);
                next.delete(key);
                return next;
            });
        }
    };

    const setDraft = (key: string, v: string) => setDrafts((d) => ({ ...d, [key]: v }));

    const removeClient = async (client: PayrollClientProject) => {
        if (!confirm(`Удалить клиента «${client.name}»? Введённые суммы и расчёты пропадут из будущих месяцев.`)) return;
        setDeleting(client.id);
        setRegError('');
        try {
            await api.deletePayrollAgencyClient(client.id);
            await Promise.all([loadRegistry(), loadSheet(undefined, true)]);
            onChanged();
        } catch (e: unknown) {
            setRegError(e instanceof Error ? e.message : 'Ошибка удаления');
        } finally {
            setDeleting(null);
        }
    };

    // ─── Excel: недельный расчёт percent-клиентов доп. листом ────────────────
    const weekSheets = useMemo<ExcelExtraSheet[]>(() => {
        if (!sheet) return [];
        const rows = sheet.clients
            .filter((c) => c.billing_mode === 'percent' && c.weeks.length > 0)
            .flatMap((c) => c.weeks.map((w) => ({
                'Клиент': c.name,
                'Неделя': weekLabel(w.date_from, w.date_to),
                'База, ₽': Number(w.base_amount),
                'Руками': w.manual ? 'да' : '',
                'Ступень (порог от), ₽': w.threshold == null ? '' : Number(w.threshold),
                'Ставка, %': Number(w.team_rate) * 100,
                'Fee, ₽': Number(w.fee),
            })));
        return rows.length > 0 ? [{ sheetName: 'Недели percent-клиентов', data: rows }] : [];
    }, [sheet]);

    const sheetClients = sheet?.clients ?? [];
    const registryEmpty = !regLoading && !regError && clients != null && clients.length === 0;

    const columns = [
        {
            key: 'name', label: 'Клиент',
            render: (v: string) => <span style={{ fontWeight: 600 }}>{v}</span>,
        },
        {
            key: 'billing_mode', label: 'Режим',
            getValue: (r: PayrollAgencySheetClient) => MODE_LABEL[r.billing_mode],
            render: (_v: unknown, r: PayrollAgencySheetClient) => (
                <span className={MODE_BADGE[r.billing_mode]}>{MODE_LABEL[r.billing_mode]}</span>
            ),
        },
        {
            key: 'team_name', label: 'Команда',
            getValue: (r: PayrollAgencySheetClient) => r.team_name ?? '',
            render: (_v: unknown, r: PayrollAgencySheetClient) => r.team_name
                ? <span className="badge badge-info">{r.team_name}</span>
                : (
                    <span className="badge badge-warning" title="Без команды доля менеджеров никому не начисляется">
                        ⚠ команда не привязана
                    </span>
                ),
        },
        {
            key: 'profit_amount', label: 'ЧП месяца', align: 'right' as const, sortable: false,
            headerTitle: 'Чистая прибыль клиента за месяц (вводится руками для режима «От ЧП»)',
            getValue: (r: PayrollAgencySheetClient) => r.profit_amount == null ? '' : Number(r.profit_amount),
            render: (_v: unknown, r: PayrollAgencySheetClient) => {
                if (r.billing_mode === 'profit_share') {
                    if (!sheet) return null;
                    const dateFrom = `${sheet.month}-01`;
                    const key = entryKey(r.client_id, 'month_profit', dateFrom);
                    const shown = drafts[key] ?? (r.profit_amount == null ? '' : String(Number(r.profit_amount)));
                    const busy = pendingEntries.has(key);
                    return (
                        <input
                            type="number" min={0} className="form-input"
                            style={{ width: 130, textAlign: 'right', opacity: busy ? 0.5 : 1 }}
                            placeholder="ЧП, ₽"
                            disabled={busy}
                            value={shown}
                            onChange={(e) => setDraft(key, e.target.value)}
                            onBlur={() => commitEntry(r.client_id, 'month_profit', dateFrom, shown, r.profit_amount)}
                            onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
                        />
                    );
                }
                // Внешний percent-кабинет: признак — из самого sheet (manual-недели),
                // без склейки с реестром (реестр мог не загрузиться).
                if (r.billing_mode === 'percent' && r.weeks.some((w) => w.manual)) {
                    return <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>базы — в раскрытии</span>;
                }
                return <span style={{ color: 'var(--color-text-dim)' }}>—</span>;
            },
        },
        {
            key: 'fee_total', label: 'Fee', align: 'right' as const,
            getValue: (r: PayrollAgencySheetClient) => Number(r.fee_total),
            render: (_v: unknown, r: PayrollAgencySheetClient) => (
                <span style={{ fontWeight: 700 }}>{money(r.fee_total)} ₽</span>
            ),
        },
        {
            key: 'manager_amount', label: 'Менеджерам', align: 'right' as const,
            headerTitle: 'Доля команды — попадает в Ведомость и ФОТ «Менеджеры»',
            getValue: (r: PayrollAgencySheetClient) => Number(r.manager_amount),
            render: (_v: unknown, r: PayrollAgencySheetClient) => (
                <div>
                    <div style={{ fontWeight: 600 }}>{money(r.manager_amount)} ₽</div>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>{ratePct(r.manager_share)}</div>
                </div>
            ),
        },
        {
            key: 'agency_amount', label: 'Агентству', align: 'right' as const,
            headerTitle: 'Остаток агентству (пока справочно, в ОПиУ не включается)',
            getValue: (r: PayrollAgencySheetClient) => Number(r.agency_amount),
            render: (_v: unknown, r: PayrollAgencySheetClient) => `${money(r.agency_amount)} ₽`,
        },
        {
            key: 'warnings', label: 'Предупреждения', sortable: false,
            getValue: (r: PayrollAgencySheetClient) => r.warnings.join('; '),
            render: (_v: unknown, r: PayrollAgencySheetClient) => r.warnings.length === 0
                ? <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                : (
                    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                        {r.warnings.map((w, i) => (
                            <span key={i} className="badge badge-warning" style={{ fontSize: 10 }}>{w}</span>
                        ))}
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
                    <button className="btn btn-primary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => setShowCreate(true)}>
                        + Добавить клиента
                    </button>
                </div>
            </div>

            {entryError && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 16, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span>⚠ {entryError}</span>
                    <button className="btn btn-secondary btn-sm" onClick={() => setEntryError('')}>Закрыть</button>
                </div>
            )}

            {(sheetError || regError) && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between' }}>
                    <span>{sheetError || regError}</span>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => { if (sheetError) loadSheet(); if (regError) loadRegistry(); }}
                    >
                        Повторить
                    </button>
                </div>
            )}

            {sheetLoading || regLoading ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка…</div>
            ) : registryEmpty ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                    <div style={{ fontSize: 40, marginBottom: 12 }}>💼</div>
                    <div style={{ fontSize: 16, fontWeight: 600 }}>Заведи первого клиента агентства</div>
                    <div style={{ color: 'var(--color-text-dim)', marginTop: 6, marginBottom: 16 }}>
                        Клиент платит фикс, процент от «Чистой выплаты» кабинета или долю от чистой прибыли.
                        {' '}Доля менеджеров автоматически попадёт в Ведомость и ФОТ.
                    </div>
                    <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)}>+ Добавить клиента</button>
                </div>
            ) : (
                <>
                    {/* ─── KPI ───────────────────────────────────────────────── */}
                    {sheet && (
                        <div className="stats-grid" style={{ marginBottom: 20 }}>
                            <KpiCard
                                label="Выручка агентства"
                                icon="💼"
                                value={`${money(sheet.totals_fee, 0)} ₽`}
                                sub="fee всех клиентов за месяц"
                            />
                            <KpiCard
                                label="Менеджерам"
                                icon="🧑‍💼"
                                value={`${money(sheet.totals_manager, 0)} ₽`}
                                color="#22c55e"
                                sub="уходит командам — в Ведомость и ФОТ"
                            />
                            <KpiCard
                                label="Агентству"
                                icon="🏛️"
                                value={`${money(sheet.totals_agency, 0)} ₽`}
                                color="#a78bfa"
                                sub="остаток — пока справочно"
                            />
                        </div>
                    )}

                    {/* ─── Расчёт по клиентам ────────────────────────────────── */}
                    {sheet && sheetClients.length === 0 ? (
                        <div className="glass-card" style={{ padding: 32, textAlign: 'center', marginBottom: 24 }}>
                            <div style={{ fontSize: 32, marginBottom: 8 }}>📭</div>
                            <div style={{ fontWeight: 600 }}>За {monthTitle(month)} расчёта нет</div>
                            <div style={{ color: 'var(--color-text-dim)', marginTop: 4 }}>
                                Активные клиенты появятся здесь автоматически.
                            </div>
                        </div>
                    ) : sheet && (
                        <div style={{ marginBottom: 24 }}>
                            <TanStackDataTable
                                title={`Агентство · ${monthTitle(sheet.month)}`}
                                columns={columns}
                                data={sheetClients}
                                exportName={`agency-${sheet.month}`}
                                exportAdditionalSheets={weekSheets}
                                enableSorting
                                enablePagination={false}
                                getRowId={(r: PayrollAgencySheetClient) => String(r.client_id)}
                                renderSubRow={(r: PayrollAgencySheetClient) => (
                                    <ClientDetail
                                        client={r}
                                        drafts={drafts}
                                        pending={pendingEntries}
                                        onDraft={setDraft}
                                        onCommit={commitEntry}
                                    />
                                )}
                            />
                        </div>
                    )}

                    {/* ─── Клиенты (реестр) ──────────────────────────────────── */}
                    <div className="section-title" style={{ marginBottom: 12 }}>Клиенты агентства</div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 16 }}>
                        {(clients ?? []).map((c) => (
                            <div key={c.id} className="glass-card static" style={{ padding: 20, opacity: c.is_active ? 1 : 0.6 }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                                    <span style={{ fontWeight: 700, fontSize: 15 }}>{c.name}</span>
                                    <span className={MODE_BADGE[c.billing_mode]}>{MODE_LABEL[c.billing_mode]}</span>
                                    {!c.is_active && <span className="badge badge-secondary">выключен</span>}
                                    <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                                        <button className="btn btn-secondary btn-sm" onClick={() => setEditClient(c)}>✎</button>
                                        <button
                                            className="btn btn-danger btn-sm"
                                            disabled={deleting === c.id}
                                            onClick={() => removeClient(c)}
                                        >
                                            {deleting === c.id ? '…' : '🗑'}
                                        </button>
                                    </div>
                                </div>
                                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                                    {c.team_name
                                        ? <span className="badge badge-info">{c.team_name}</span>
                                        : (
                                            <span className="badge badge-warning" title="Без команды доля менеджеров никому не начисляется">
                                                ⚠ команда не привязана
                                            </span>
                                        )}
                                    {c.billing_mode === 'percent' && (
                                        c.linked_project_id != null
                                            ? <span className="badge badge-success">кабинет: {c.linked_project_name ?? `#${c.linked_project_id}`}</span>
                                            : <span className="badge badge-secondary">внешний (базы руками)</span>
                                    )}
                                    {c.billing_mode === 'fixed' && c.fixed_amount != null && (
                                        <span className="badge badge-secondary">фикс {money(c.fixed_amount, 0)} ₽/мес</span>
                                    )}
                                    {c.billing_mode === 'profit_share' && c.fee_percent != null && (
                                        <span className="badge badge-secondary">{ratePct(c.fee_percent)} от ЧП</span>
                                    )}
                                    {Number(c.manager_share) !== 0.45 && (
                                        <span className="badge badge-secondary" title="Нестандартная доля команды (дефолт 45%)">
                                            менеджерам {ratePct(c.manager_share)}
                                        </span>
                                    )}
                                </div>
                                {c.notes && (
                                    <div style={{ fontSize: 12.5, color: 'var(--color-text-dim)', marginTop: 8 }}>{c.notes}</div>
                                )}
                            </div>
                        ))}
                    </div>
                </>
            )}

            {(showCreate || editClient) && (
                <ClientFormModal
                    client={editClient ?? undefined}
                    teams={teams}
                    projectOptions={projectOptions}
                    onClose={() => { setShowCreate(false); setEditClient(null); }}
                    onSaved={async () => {
                        setShowCreate(false);
                        setEditClient(null);
                        await Promise.all([loadRegistry(), loadSheet(undefined, true)]);
                        onChanged();
                    }}
                />
            )}
        </div>
    );
}

// ─── Раскрытие клиента: недели percent / формула ────────────────────────────

function ClientDetail({
    client, drafts, pending, onDraft, onCommit,
}: {
    client: PayrollAgencySheetClient;
    drafts: Record<string, string>;
    pending: Set<string>;
    onDraft: (key: string, v: string) => void;
    onCommit: (
        clientId: number, kind: PayrollClientEntryKind, dateFrom: string,
        raw: string, prevServer: number | string | null,
    ) => void;
}) {
    // Внешний кабинет — признак из sheet-ответа (manual-недели), не из реестра.
    const external = client.weeks.some((w) => w.manual);
    if (client.billing_mode === 'fixed') {
        return (
            <div style={{ padding: '10px 8px', fontSize: 13, color: 'var(--color-text-muted)' }}>
                Фикс {money(client.fee_total)} ₽/мес. Менеджерам — {ratePct(client.manager_share)} ({money(client.manager_amount)} ₽), остальное агентству ({money(client.agency_amount)} ₽).
            </div>
        );
    }
    if (client.billing_mode === 'profit_share') {
        return (
            <div style={{ padding: '10px 8px', fontSize: 13, color: 'var(--color-text-muted)' }}>
                Fee = ЧП месяца ({client.profit_amount == null ? 'не введена' : `${money(client.profit_amount)} ₽`}) × {ratePct(client.fee_percent)} = {money(client.fee_total)} ₽.
                {' '}Менеджерам — {ratePct(client.manager_share)} ({money(client.manager_amount)} ₽), остальное агентству ({money(client.agency_amount)} ₽).
            </div>
        );
    }
    // percent — недельная таблица
    if (client.weeks.length === 0) {
        return (
            <div style={{ padding: '10px 8px', fontSize: 13, color: 'var(--color-text-dim)' }}>
                Недель в расчётном месяце нет.
            </div>
        );
    }
    return (
        <div style={{ padding: '8px 4px', overflowX: 'auto' }}>
            {external && (
                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 6 }}>
                    Внешний кабинет: введи «Чистую выплату» за каждую неделю — Enter или клик мимо сохраняет, пустое поле очищает.
                </div>
            )}
            <table className="data-table" style={{ width: '100%' }}>
                <thead>
                    <tr>
                        <th>Неделя</th>
                        <th style={{ textAlign: 'right' }}>База «Чистая выплата»</th>
                        <th style={{ textAlign: 'right' }} title="Ближайший порог лестницы снизу">Ступень</th>
                        <th style={{ textAlign: 'right' }}>Ставка</th>
                        <th style={{ textAlign: 'right' }}>Fee</th>
                    </tr>
                </thead>
                <tbody>
                    {client.weeks.map((w, i) => {
                        const key = entryKey(client.client_id, 'week_base', w.date_from);
                        // Записи нет → пустой инпут (не «0»): видно невведённые недели,
                        // а явный ввод «0» создаёт запись (no-op сравнение — от has_entry).
                        const serverValue = w.has_entry ? w.base_amount : null;
                        const shown = drafts[key] ?? (serverValue == null ? '' : String(Number(serverValue)));
                        const busy = pending.has(key);
                        return (
                            <tr key={i}>
                                <td>{weekLabel(w.date_from, w.date_to)}</td>
                                <td style={{ textAlign: 'right' }}>
                                    {w.manual ? (
                                        <input
                                            type="number" min={0} className="form-input"
                                            style={{ width: 140, textAlign: 'right', opacity: busy ? 0.5 : 1 }}
                                            placeholder="не введено"
                                            disabled={busy}
                                            value={shown}
                                            onChange={(e) => onDraft(key, e.target.value)}
                                            onBlur={() => onCommit(client.client_id, 'week_base', w.date_from, shown, serverValue)}
                                            onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
                                        />
                                    ) : (
                                        <>{money(w.base_amount)} ₽</>
                                    )}
                                </td>
                                <td style={{ textAlign: 'right', color: w.threshold == null ? 'var(--color-text-dim)' : undefined }}>
                                    {w.threshold == null ? '—' : `от ${money(w.threshold, 0)} ₽`}
                                </td>
                                <td style={{ textAlign: 'right' }}>{ratePct(w.team_rate)}</td>
                                <td style={{ textAlign: 'right', fontWeight: 600 }}>{money(w.fee)} ₽</td>
                            </tr>
                        );
                    })}
                    <tr>
                        <td style={{ fontWeight: 700 }}>Итого</td>
                        <td colSpan={3} />
                        <td style={{ textAlign: 'right', fontWeight: 700 }}>{money(client.fee_total)} ₽</td>
                    </tr>
                </tbody>
            </table>
        </div>
    );
}

// ─── Модалка клиента ────────────────────────────────────────────────────────

function ClientFormModal({
    client, teams, projectOptions, onClose, onSaved,
}: {
    client?: PayrollClientProject;
    teams: PayrollTeam[];
    projectOptions: PayrollProjectOption[];
    onClose: () => void;
    onSaved: () => void | Promise<void>;
}) {
    const [name, setName] = useState(client?.name ?? '');
    const [mode, setMode] = useState<PayrollBillingMode>(client?.billing_mode ?? 'percent');
    const [teamId, setTeamId] = useState<number | null>(client?.team_id ?? null);
    const [cabinet, setCabinet] = useState<'linked' | 'external'>(
        client == null || client.linked_project_id != null ? 'linked' : 'external'
    );
    const [linkedProjectId, setLinkedProjectId] = useState<number | null>(client?.linked_project_id ?? null);
    const [fixedAmount, setFixedAmount] = useState(
        client?.fixed_amount != null ? String(Number(client.fixed_amount)) : ''
    );
    const [feePct, setFeePct] = useState(
        client?.fee_percent != null ? String(+(Number(client.fee_percent) * 100).toFixed(2)) : ''
    );
    const [managerPct, setManagerPct] = useState(
        client != null ? String(+(Number(client.manager_share) * 100).toFixed(2)) : '45'
    );
    const [showAdvanced, setShowAdvanced] = useState(
        client != null && Number(client.manager_share) !== 0.45
    );
    const [isActive, setIsActive] = useState(client?.is_active ?? true);
    const [notes, setNotes] = useState(client?.notes ?? '');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    const submit = async () => {
        if (!name.trim()) { setError('Укажите имя клиента'); return; }
        const fixed = fixedAmount.trim() === '' ? null : Number(fixedAmount);
        if (mode === 'fixed' && (fixed == null || !Number.isFinite(fixed) || fixed < 0)) {
            setError('Укажите сумму фикса (неотрицательное число)');
            return;
        }
        const fee = feePct.trim() === '' ? null : Number(feePct);
        if (mode === 'profit_share' && (fee == null || !Number.isFinite(fee) || fee < 0 || fee > 100)) {
            setError('Процент от ЧП — число от 0 до 100');
            return;
        }
        if (mode === 'percent' && cabinet === 'linked' && linkedProjectId == null) {
            setError('Выберите кабинет клиента в системе (или переключите на «внешний»)');
            return;
        }
        const manager = Number(managerPct);
        if (!Number.isFinite(manager) || manager < 0 || manager > 100) {
            setError('Доля менеджеров — число от 0 до 100');
            return;
        }

        setSaving(true);
        setError('');
        const wantLinked = mode === 'percent' && cabinet === 'linked' ? linkedProjectId : null;
        try {
            if (client) {
                const payload: PayrollClientProjectUpdate = {
                    name: name.trim(),
                    billing_mode: mode,
                    manager_share: manager / 100,
                    is_active: isActive,
                    notes: notes.trim() || null,
                };
                if (teamId != null) payload.team_id = teamId;
                else if (client.team_id != null) payload.clear_team = true;
                if (wantLinked != null) payload.linked_project_id = wantLinked;
                else if (client.linked_project_id != null) payload.clear_linked_project = true;
                if (mode === 'fixed' && fixed != null) payload.fixed_amount = fixed;
                if (mode === 'profit_share' && fee != null) payload.fee_percent = fee / 100;
                await api.updatePayrollAgencyClient(client.id, payload);
            } else {
                await api.createPayrollAgencyClient({
                    name: name.trim(),
                    billing_mode: mode,
                    team_id: teamId,
                    linked_project_id: wantLinked,
                    fixed_amount: mode === 'fixed' ? fixed : null,
                    fee_percent: mode === 'profit_share' && fee != null ? fee / 100 : null,
                    manager_share: manager / 100,
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

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card modal-card-solid modal-card-wide" style={{ maxHeight: '90vh', overflow: 'auto' }} onClick={(e) => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <h3 style={{ margin: 0, fontSize: 17, fontWeight: 700 }}>
                        {client ? `Клиент «${client.name}»` : 'Новый клиент агентства'}
                    </h3>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>✕</button>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <div className="form-group">
                        <label className="form-label">Имя клиента *</label>
                        <input className="form-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="ООО Ромашка" />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Команда</label>
                        <select
                            className="form-input"
                            value={teamId ?? ''}
                            onChange={(e) => setTeamId(e.target.value ? Number(e.target.value) : null)}
                        >
                            <option value="">— без команды —</option>
                            {teams.map((t) => (
                                <option key={t.id} value={t.id}>{t.name}{t.is_active ? '' : ' (выключена)'}</option>
                            ))}
                        </select>
                        {teamId == null && (
                            <div style={{ fontSize: 12, color: 'var(--color-warning)', marginTop: 4 }}>
                                Без команды доля менеджеров никому не начисляется.
                            </div>
                        )}
                    </div>

                    <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                        <label className="form-label">Режим оплаты</label>
                        <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
                            {([
                                ['fixed', 'Фикс/мес'],
                                ['percent', 'Процент от «Чистой выплаты» кабинета'],
                                ['profit_share', '% от чистой прибыли'],
                            ] as [PayrollBillingMode, string][]).map(([m, label]) => (
                                <label key={m} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                                    <input type="radio" name="billing-mode" checked={mode === m} onChange={() => setMode(m)} />
                                    {label}
                                </label>
                            ))}
                        </div>
                    </div>

                    {mode === 'fixed' && (
                        <div className="form-group">
                            <label className="form-label">Фикс, ₽/мес *</label>
                            <input
                                type="number" min={0} className="form-input" placeholder="100 000"
                                value={fixedAmount} onChange={(e) => setFixedAmount(e.target.value)}
                            />
                        </div>
                    )}

                    {mode === 'percent' && (
                        <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                            <label className="form-label">Кабинет клиента</label>
                            <div style={{ display: 'flex', gap: 14, marginBottom: 8 }}>
                                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                                    <input type="radio" name="cabinet-kind" checked={cabinet === 'linked'} onChange={() => setCabinet('linked')} />
                                    кабинет в системе (базы считаются сами)
                                </label>
                                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                                    <input type="radio" name="cabinet-kind" checked={cabinet === 'external'} onChange={() => setCabinet('external')} />
                                    внешний (базы руками)
                                </label>
                            </div>
                            {cabinet === 'linked' && (
                                <select
                                    className="form-input"
                                    value={linkedProjectId ?? ''}
                                    onChange={(e) => setLinkedProjectId(e.target.value ? Number(e.target.value) : null)}
                                >
                                    <option value="">— выберите проект —</option>
                                    {projectOptions.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                                </select>
                            )}
                            <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>
                                Fee недели = «Чистая выплата» кабинета × ставка «Команда» тарифной лестницы.
                            </div>
                        </div>
                    )}

                    {mode === 'profit_share' && (
                        <div className="form-group">
                            <label className="form-label">% от чистой прибыли *</label>
                            <input
                                type="number" min={0} max={100} step={0.1} className="form-input" placeholder="10"
                                value={feePct} onChange={(e) => setFeePct(e.target.value)}
                            />
                            <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>
                                ЧП месяца вводится руками в расчёте.
                            </div>
                        </div>
                    )}

                    <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                        <label className="form-label">Заметки</label>
                        <input className="form-input" value={notes} onChange={(e) => setNotes(e.target.value)} />
                    </div>

                    <div className="form-group" style={{ gridColumn: '1 / -1' }}>
                        <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => setShowAdvanced((v) => !v)}
                        >
                            {showAdvanced ? '▾ Дополнительно' : '▸ Дополнительно'}
                        </button>
                        {showAdvanced && (
                            <div style={{ marginTop: 8, maxWidth: 260 }}>
                                <label className="form-label">Доля менеджеров, %</label>
                                <input
                                    type="number" min={0} max={100} step={0.1} className="form-input"
                                    value={managerPct} onChange={(e) => setManagerPct(e.target.value)}
                                />
                                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>
                                    Дефолт 45% — уходит команде, остаток агентству.
                                </div>
                            </div>
                        )}
                    </div>
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
