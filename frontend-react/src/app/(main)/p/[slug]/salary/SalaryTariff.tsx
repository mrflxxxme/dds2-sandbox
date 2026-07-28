'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate } from '@/lib/utils';
import type { PayrollTariffResponse } from '@/types/api';
import { money, ratePct } from './payrollFmt';

const todayIso = () => new Date().toISOString().slice(0, 10);

interface EditRow { threshold: string; companyPct: string; teamPct: string; }

export default function SalaryTariff({
    nonce, onChanged,
}: { nonce: number; onChanged: () => void }) {
    const [data, setData] = useState<PayrollTariffResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const [editing, setEditing] = useState(false);
    const [rows, setRows] = useState<EditRow[]>([]);
    const [validFrom, setValidFrom] = useState(todayIso());
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState('');
    // Сохранили версию с valid_from в будущем: показываем действующую лестницу
    // с пометкой, что записанная вступит в силу позже.
    const [pendingFrom, setPendingFrom] = useState<string | null>(null);

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.payrollTariff();
            if (signal?.aborted) return;
            setData(res);
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

    const steps = [...(data?.steps ?? [])].sort((a, b) => Number(a.threshold) - Number(b.threshold));

    const startEdit = () => {
        setRows(steps.length > 0
            ? steps.map((s) => ({
                threshold: String(Number(s.threshold)),
                companyPct: String(+(Number(s.company_rate) * 100).toFixed(3)),
                teamPct: String(+(Number(s.team_rate) * 100).toFixed(3)),
            }))
            : [{ threshold: '', companyPct: '', teamPct: '' }]);
        setValidFrom(todayIso());
        setSaveError('');
        setEditing(true);
    };

    const setRow = (i: number, patch: Partial<EditRow>) =>
        setRows((rs) => rs.map((r, ri) => (ri === i ? { ...r, ...patch } : r)));

    const save = async () => {
        if (rows.length === 0) { setSaveError('Добавьте хотя бы одну ступень'); return; }
        const parsed = rows.map((r) => ({
            threshold: Number(r.threshold),
            company_rate: Number(r.companyPct) / 100,
            team_rate: Number(r.teamPct) / 100,
        }));
        if (parsed.some((s) => !(s.threshold > 0))) { setSaveError('Порог каждой ступени — число больше 0'); return; }
        if (new Set(parsed.map((s) => s.threshold)).size !== parsed.length) { setSaveError('Пороги не должны повторяться'); return; }
        if (parsed.some((s) => !Number.isFinite(s.company_rate) || s.company_rate < 0 || s.company_rate > 1
            || !Number.isFinite(s.team_rate) || s.team_rate < 0 || s.team_rate > 1)) {
            setSaveError('Ставки — проценты от 0 до 100');
            return;
        }
        if (!validFrom) { setSaveError('Укажите дату начала действия'); return; }
        setSaving(true);
        setSaveError('');
        try {
            await api.replacePayrollTariff({ valid_from: validFrom, steps: parsed });
            // PUT возвращает набор для ЗАПИСАННОГО valid_from (возможно будущего) —
            // действующую лестницу перечитываем отдельным GET без on_date.
            setPendingFrom(validFrom > todayIso() ? validFrom : null);
            setEditing(false);
            onChanged();
            await load();
        } catch (e: unknown) {
            setSaveError(e instanceof Error ? e.message : 'Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    return (
        <div>
            {error && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between' }}>
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={() => load()}>Повторить</button>
                </div>
            )}

            {loading ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка…</div>
            ) : editing ? (
                <div className="glass-card static" style={{ padding: 20, maxWidth: 640 }}>
                    <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 12 }}>Новая версия тарифной лестницы</div>

                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 36px', gap: 8, alignItems: 'center', marginBottom: 6 }}>
                        <span className="form-label" style={{ marginBottom: 0 }}>Порог недели, ₽ (от)</span>
                        <span className="form-label" style={{ marginBottom: 0 }}>% компании</span>
                        <span className="form-label" style={{ marginBottom: 0 }}>% команды</span>
                        <span />
                    </div>
                    {rows.map((r, i) => (
                        <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 36px', gap: 8, alignItems: 'center', marginBottom: 6 }}>
                            <input
                                type="number" min={0} className="form-input" placeholder="1 000 000"
                                value={r.threshold} onChange={(e) => setRow(i, { threshold: e.target.value })}
                            />
                            <input
                                type="number" min={0} max={100} step={0.001} className="form-input" placeholder="1.944"
                                value={r.companyPct} onChange={(e) => setRow(i, { companyPct: e.target.value })}
                            />
                            <input
                                type="number" min={0} max={100} step={0.001} className="form-input" placeholder="0.972"
                                value={r.teamPct} onChange={(e) => setRow(i, { teamPct: e.target.value })}
                            />
                            <button
                                className="btn btn-secondary btn-sm"
                                title="Удалить ступень"
                                onClick={() => setRows((rs) => rs.filter((_, ri) => ri !== i))}
                            >
                                ✕
                            </button>
                        </div>
                    ))}
                    <button
                        className="btn btn-secondary btn-sm"
                        style={{ marginTop: 4 }}
                        onClick={() => setRows((rs) => [...rs, { threshold: '', companyPct: '', teamPct: '' }])}
                    >
                        + Добавить ступень
                    </button>

                    <div className="form-group" style={{ marginTop: 16, maxWidth: 220 }}>
                        <label className="form-label">Действует с</label>
                        <input type="date" className="form-input" value={validFrom} onChange={(e) => setValidFrom(e.target.value)} />
                    </div>

                    {saveError && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 10 }}>{saveError}</div>}
                    <div style={{ display: 'flex', gap: 8 }}>
                        <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
                            {saving ? 'Сохранение…' : 'Сохранить лестницу'}
                        </button>
                        <button className="btn btn-secondary btn-sm" onClick={() => setEditing(false)} disabled={saving}>
                            Отмена
                        </button>
                    </div>
                </div>
            ) : steps.length === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                    <div style={{ fontSize: 40, marginBottom: 12 }}>📶</div>
                    <div style={{ fontSize: 16, fontWeight: 600 }}>Тарифная лестница не настроена</div>
                    <div style={{ color: 'var(--color-text-dim)', marginTop: 6, marginBottom: 16 }}>
                        Без неё командные начисления считаются по нулевой ставке.
                        {pendingFrom && <> Сохранённая версия вступит в силу {formatDate(pendingFrom)}.</>}
                    </div>
                    <button className="btn btn-primary btn-sm" onClick={startEdit}>Настроить лестницу</button>
                </div>
            ) : (
                <div className="glass-card static" style={{ padding: 20, maxWidth: 640 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
                        <span style={{ fontWeight: 700, fontSize: 15 }}>Действующая лестница</span>
                        {data?.valid_from && (
                            <span className="badge badge-info">с {formatDate(data.valid_from)}</span>
                        )}
                        {pendingFrom && (
                            <span className="badge badge-warning" title="Сохранённая версия начнёт действовать с этой даты; до неё работает текущая">
                                новая версия вступит в силу {formatDate(pendingFrom)}
                            </span>
                        )}
                        <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={startEdit}>
                            ✎ Редактировать
                        </button>
                    </div>
                    <table className="data-table" style={{ width: '100%' }}>
                        <thead>
                            <tr>
                                <th title="«Чистая выплата» команды за неделю от этой суммы">Порог недели, ₽ (от)</th>
                                <th style={{ textAlign: 'right' }}>% компании</th>
                                <th style={{ textAlign: 'right' }}>% команды</th>
                            </tr>
                        </thead>
                        <tbody>
                            {steps.map((s) => (
                                <tr key={s.id}>
                                    <td style={{ fontWeight: 600 }}>{money(s.threshold, 0)} ₽</td>
                                    <td style={{ textAlign: 'right' }}>{ratePct(s.company_rate)}</td>
                                    <td style={{ textAlign: 'right', fontWeight: 600 }}>{ratePct(s.team_rate)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    <div style={{ fontSize: 12.5, color: 'var(--color-text-dim)', marginTop: 12, lineHeight: 1.5 }}>
                        Ступень недели — ближайший порог снизу от «Чистой выплаты» команды.
                        Ниже минимального порога действует минимальная ставка; отрицательная неделя — 0.
                        Начисление команды делится поровну между участниками.
                    </div>
                </div>
            )}
        </div>
    );
}
