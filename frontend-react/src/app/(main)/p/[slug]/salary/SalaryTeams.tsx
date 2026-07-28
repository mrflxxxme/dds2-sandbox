'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type {
    PayrollEmployee,
    PayrollScopeOptions,
    PayrollTeam,
    PayrollTeamScope,
} from '@/types/api';
import { scopeBadgeClass, scopeLabel } from './payrollFmt';

export default function SalaryTeams({
    nonce, onChanged,
}: { nonce: number; onChanged: () => void }) {
    const [teams, setTeams] = useState<PayrollTeam[] | null>(null);
    const [employees, setEmployees] = useState<PayrollEmployee[]>([]);
    const [options, setOptions] = useState<PayrollScopeOptions>({ brands: [], subjects: [] });
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [editTeam, setEditTeam] = useState<PayrollTeam | null>(null);
    const [showCreate, setShowCreate] = useState(false);
    const [deleting, setDeleting] = useState<number | null>(null);

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const [teamsRes, empsRes, optsRes] = await Promise.all([
                api.listPayrollTeams(),
                api.listPayrollEmployees(),
                api.payrollScopeOptions(),
            ]);
            if (signal?.aborted) return;
            setTeams(teamsRes.items);
            setEmployees(empsRes.items);
            setOptions(optsRes);
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

    const remove = async (team: PayrollTeam) => {
        if (!confirm(`Удалить команду «${team.name}»? Начисления прошлых месяцев не пересчитываются.`)) return;
        setDeleting(team.id);
        setError('');
        try {
            await api.deletePayrollTeam(team.id);
            await load();
            onChanged();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка удаления');
        } finally {
            setDeleting(null);
        }
    };

    const items = teams ?? [];
    // Empty только когда данные реально загрузились: при ошибке items==null,
    // и показывать «Команд пока нет» поверх баннера ошибки нельзя.
    const isEmpty = !loading && !error && teams != null && items.length === 0;

    return (
        <div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 16 }}>
                <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)}>
                    + Создать команду
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
                    <div style={{ fontSize: 40, marginBottom: 12 }}>👥</div>
                    <div style={{ fontSize: 16, fontWeight: 600 }}>Команд пока нет</div>
                    <div style={{ color: 'var(--color-text-dim)', marginTop: 6, marginBottom: 16 }}>
                        Команда (1–2 человека) получает % от недельной «Чистой выплаты» WB по своим брендам и категориям.
                    </div>
                    <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)}>+ Создать команду</button>
                </div>
            ) : items.length > 0 ? (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 16 }}>
                    {items.map((t) => (
                        <div key={t.id} className="glass-card static" style={{ padding: 20, opacity: t.is_active ? 1 : 0.6 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                                <span style={{ fontWeight: 700, fontSize: 15 }}>{t.name}</span>
                                {!t.is_active && <span className="badge badge-secondary">выключена</span>}
                                <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                                    <button className="btn btn-secondary btn-sm" onClick={() => setEditTeam(t)}>✎</button>
                                    <button
                                        className="btn btn-danger btn-sm"
                                        disabled={deleting === t.id}
                                        onClick={() => remove(t)}
                                    >
                                        {deleting === t.id ? '…' : '🗑'}
                                    </button>
                                </div>
                            </div>

                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Скоупы</div>
                            {t.scopes.length === 0 ? (
                                <div style={{ fontSize: 13, color: 'var(--color-warning)', marginBottom: 10 }}>
                                    ⚠ Не выбраны бренды/категории — начислений не будет
                                </div>
                            ) : (
                                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 10 }}>
                                    {t.scopes.map((s, i) => (
                                        <span key={i} className={scopeBadgeClass(s.kind)}>{scopeLabel(s)}</span>
                                    ))}
                                </div>
                            )}

                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>Участники</div>
                            {t.members.length === 0 ? (
                                <div style={{ fontSize: 13, color: 'var(--color-warning)' }}>
                                    ⚠ Нет участников — начисление делить не на кого
                                </div>
                            ) : (
                                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                                    {t.members.map((m) => (
                                        <span key={m.employee_id} className="badge badge-success">{m.name}</span>
                                    ))}
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            ) : null}

            {(showCreate || editTeam) && (
                <TeamFormModal
                    team={editTeam ?? undefined}
                    employees={employees}
                    options={options}
                    onClose={() => { setShowCreate(false); setEditTeam(null); }}
                    onSaved={async () => {
                        setShowCreate(false);
                        setEditTeam(null);
                        await load();
                        onChanged();
                    }}
                    onDirty={() => { load(); }}
                />
            )}
        </div>
    );
}

// ─── Модалка создания/редактирования команды ─────────────────────────────────

function TeamFormModal({
    team, employees, options, onClose, onSaved, onDirty,
}: {
    team?: PayrollTeam;
    employees: PayrollEmployee[];
    options: PayrollScopeOptions;
    onClose: () => void;
    onSaved: () => void | Promise<void>;
    /** Частичное сохранение (создали команду, но упали на скоупах/участниках) — перезагрузить список, не закрывая модалку. */
    onDirty: () => void;
}) {
    const [name, setName] = useState(team?.name ?? '');
    const [isActive, setIsActive] = useState(team?.is_active ?? true);
    const [brands, setBrands] = useState<Set<string>>(
        new Set(team?.scopes.filter((s) => s.kind === 'brand').map((s) => s.value) ?? [])
    );
    const [subjects, setSubjects] = useState<Set<string>>(
        new Set(team?.scopes.filter((s) => s.kind === 'subject').map((s) => s.value) ?? [])
    );
    const [memberIds, setMemberIds] = useState<Set<number>>(
        new Set(team?.members.map((m) => m.employee_id) ?? [])
    );
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    // Ретрай-безопасность: после успешного POST повторный сабмит НЕ должен
    // создавать дубль команды (дублируются начисления!) — храним созданный id
    // и при ретрае идём по PATCH/replace-пути.
    const createdIdRef = useRef<number | null>(null);

    const toggle = <T,>(set: React.Dispatch<React.SetStateAction<Set<T>>>) => (v: T) =>
        set((prev) => {
            const next = new Set(prev);
            if (next.has(v)) next.delete(v); else next.add(v);
            return next;
        });

    const submit = async () => {
        if (!name.trim()) { setError('Укажите название команды'); return; }
        setSaving(true);
        setError('');
        const scopes: PayrollTeamScope[] = [
            ...[...brands].map((value) => ({ kind: 'brand' as const, value })),
            ...[...subjects].map((value) => ({ kind: 'subject' as const, value })),
        ];
        let mutated = false;
        try {
            const existingId = team?.id ?? createdIdRef.current;
            const saved = existingId != null
                ? await api.updatePayrollTeam(existingId, { name: name.trim(), is_active: isActive })
                : await api.createPayrollTeam({ name: name.trim(), is_active: isActive });
            mutated = true;
            if (existingId == null) createdIdRef.current = saved.id;
            await api.replacePayrollTeamScopes(saved.id, scopes);
            await api.replacePayrollTeamMembers(saved.id, [...memberIds]);
            await onSaved();
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : 'Ошибка сохранения';
            setError(!team && createdIdRef.current != null
                ? `Команда создана, но скоупы/участники не сохранились: ${msg}. Нажмите «Сохранить» ещё раз — дубля не будет.`
                : msg);
            setSaving(false);
            // Частично созданная/изменённая команда должна быть видна в списке.
            if (mutated || createdIdRef.current != null) onDirty();
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card modal-card-solid modal-card-wide" style={{ maxHeight: '90vh', overflow: 'auto' }} onClick={(e) => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <h3 style={{ margin: 0, fontSize: 17, fontWeight: 700 }}>
                        {team ? `Команда «${team.name}»` : 'Новая команда'}
                    </h3>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>✕</button>
                </div>

                <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', marginBottom: 16 }}>
                    <div className="form-group" style={{ flex: 1, marginBottom: 0 }}>
                        <label className="form-label">Название *</label>
                        <input className="form-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Настя + Дана" />
                    </div>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer', paddingBottom: 10 }}>
                        <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
                        Активна
                    </label>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16, marginBottom: 8 }}>
                    <MultiPickList
                        title={`Бренды (${brands.size})`}
                        items={options.brands.map((b) => ({ key: b, label: b }))}
                        selected={brands}
                        onToggle={toggle(setBrands)}
                        emptyHint="Брендов не найдено — нужен финотчёт WB"
                    />
                    <MultiPickList
                        title={`Категории (${subjects.size})`}
                        items={options.subjects.map((s) => ({ key: s, label: s }))}
                        selected={subjects}
                        onToggle={toggle(setSubjects)}
                        emptyHint="Категорий не найдено — нужен финотчёт WB"
                    />
                    <MultiPickList
                        title={`Участники (${memberIds.size})`}
                        items={employees.map((e) => ({
                            key: e.id,
                            label: e.is_active ? e.name : `${e.name} (неактивен)`,
                        }))}
                        selected={memberIds}
                        onToggle={toggle(setMemberIds)}
                        emptyHint="Сначала заведи сотрудников на соседнем табе"
                    />
                </div>

                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 12 }}>
                    База команды за неделю = «Чистая выплата» WB по выбранным брендам и категориям. Начисление делится поровну между участниками.
                </div>

                {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 10 }}>{error}</div>}
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button className="btn btn-secondary btn-sm" onClick={onClose} disabled={saving}>Отмена</button>
                    <button className="btn btn-primary btn-sm" onClick={submit} disabled={saving}>
                        {saving ? 'Сохранение…' : 'Сохранить'}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ─── Мультиселект со списком и поиском ───────────────────────────────────────

function MultiPickList<T extends string | number>({
    title, items, selected, onToggle, emptyHint,
}: {
    title: string;
    items: { key: T; label: string }[];
    selected: Set<T>;
    onToggle: (key: T) => void;
    emptyHint: string;
}) {
    const [search, setSearch] = useState('');

    const filtered = useMemo(() => {
        const q = search.trim().toLowerCase();
        if (!q) return items;
        return items.filter((i) => i.label.toLowerCase().includes(q));
    }, [items, search]);

    return (
        <div>
            <label className="form-label">{title}</label>
            <input
                className="form-input"
                placeholder="Поиск…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                style={{ marginBottom: 6 }}
            />
            <div style={{
                border: '1px solid var(--color-border)', borderRadius: 8,
                maxHeight: 220, overflowY: 'auto', padding: 6,
            }}>
                {items.length === 0 ? (
                    <div style={{ fontSize: 12.5, color: 'var(--color-text-dim)', padding: 6 }}>{emptyHint}</div>
                ) : filtered.length === 0 ? (
                    <div style={{ fontSize: 12.5, color: 'var(--color-text-dim)', padding: 6 }}>Ничего не найдено</div>
                ) : filtered.map((i) => (
                    <label
                        key={String(i.key)}
                        style={{
                            display: 'flex', alignItems: 'center', gap: 8, padding: '3px 6px',
                            fontSize: 13, cursor: 'pointer', borderRadius: 6,
                            background: selected.has(i.key) ? 'rgba(0, 113, 227, 0.10)' : undefined,
                        }}
                    >
                        <input type="checkbox" checked={selected.has(i.key)} onChange={() => onToggle(i.key)} />
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{i.label}</span>
                    </label>
                ))}
            </div>
        </div>
    );
}
