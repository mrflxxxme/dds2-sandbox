'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import MonthField from './MonthField';
import type {
    PayrollEmployee,
    PayrollScopeOptions,
    PayrollTeam,
    PayrollTeamMember,
    PayrollTeamMemberIn,
    PayrollTeamScope,
} from '@/types/api';
import { monthGenLabel, monthTitle, scopeBadgeClass, scopeLabel } from './payrollFmt';

/** «с июля 2026, по июнь 2027» — границы участия в команде. */
const memberBoundsLabel = (m: PayrollTeamMember): string => {
    const parts: string[] = [];
    if (m.from_month) parts.push(`с ${monthGenLabel(m.from_month)}`);
    if (m.to_month) parts.push(`по ${monthTitle(m.to_month).toLowerCase()}`);
    return parts.join(', ');
};

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
                                        <span key={i} className={scopeBadgeClass(s)}>{scopeLabel(s)}</span>
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
                                    {t.members.map((m) => {
                                        const bounds = memberBoundsLabel(m);
                                        return (
                                            <span key={m.employee_id} className="badge badge-success">
                                                {m.name}{bounds ? ` (${bounds})` : ''}
                                            </span>
                                        );
                                    })}
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

/** Строка редактора скоупов; uid — стабильный ключ. */
interface ScopeRowDraft { uid: number; brand: string; subject: string; }

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
    const [scopeRows, setScopeRows] = useState<ScopeRowDraft[]>(() =>
        (team?.scopes ?? []).map((s, i) => ({ uid: i, brand: s.brand ?? '', subject: s.subject ?? '' }))
    );
    const scopeUidRef = useRef(team?.scopes.length ?? 0);
    const [memberIds, setMemberIds] = useState<Set<number>>(
        new Set(team?.members.map((m) => m.employee_id) ?? [])
    );
    // Границы участия по месяцам ('' = без границы, to — включительно).
    const [memberBounds, setMemberBounds] = useState<Record<number, { from: string; to: string }>>(() => {
        const acc: Record<number, { from: string; to: string }> = {};
        (team?.members ?? []).forEach((m) => {
            acc[m.employee_id] = { from: m.from_month ?? '', to: m.to_month ?? '' };
        });
        return acc;
    });
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    // Ретрай-безопасность: после успешного POST повторный сабмит НЕ должен
    // создавать дубль команды (дублируются начисления!) — храним созданный id
    // и при ретрае идём по PATCH/replace-пути.
    const createdIdRef = useRef<number | null>(null);

    const toggleMember = (id: number) =>
        setMemberIds((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id); else next.add(id);
            return next;
        });

    const setScopeRow = (uid: number, patch: Partial<Omit<ScopeRowDraft, 'uid'>>) =>
        setScopeRows((rows) => rows.map((r) => (r.uid === uid ? { ...r, ...patch } : r)));

    const setBound = (id: number, patch: Partial<{ from: string; to: string }>) =>
        setMemberBounds((prev) => {
            const cur = prev[id] ?? { from: '', to: '' };
            return { ...prev, [id]: { ...cur, ...patch } };
        });

    const submit = async () => {
        if (!name.trim()) { setError('Укажите название команды'); return; }
        const scopes: PayrollTeamScope[] = [];
        for (const r of scopeRows) {
            const brand = r.brand.trim();
            const subject = r.subject.trim();
            if (!brand && !subject) {
                setError('В каждом скоупе укажите бренд и/или категорию (пустую строку удалите)');
                return;
            }
            scopes.push({ brand: brand || null, subject: subject || null });
        }
        const members: PayrollTeamMemberIn[] = [];
        for (const id of memberIds) {
            const b = memberBounds[id] ?? { from: '', to: '' };
            if (b.from && b.to && b.from > b.to) {
                const emp = employees.find((e) => e.id === id);
                setError(`У «${emp?.name ?? id}» граница «с» позже границы «по»`);
                return;
            }
            members.push({ employee_id: id, from_month: b.from || null, to_month: b.to || null });
        }
        setSaving(true);
        setError('');
        let mutated = false;
        try {
            const existingId = team?.id ?? createdIdRef.current;
            const saved = existingId != null
                ? await api.updatePayrollTeam(existingId, { name: name.trim(), is_active: isActive })
                : await api.createPayrollTeam({ name: name.trim(), is_active: isActive });
            mutated = true;
            if (existingId == null) createdIdRef.current = saved.id;
            await api.replacePayrollTeamScopes(saved.id, scopes);
            await api.replacePayrollTeamMembers(saved.id, members);
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

                {/* ─── Скоупы: бренд / категория / бренд × категория ─────────── */}
                <div className="form-group" style={{ marginBottom: 16 }}>
                    <label className="form-label">Скоупы ({scopeRows.length})</label>
                    <datalist id="payroll-scope-brands">
                        {options.brands.map((b) => <option key={b} value={b} />)}
                    </datalist>
                    <datalist id="payroll-scope-subjects">
                        {options.subjects.map((s) => <option key={s} value={s} />)}
                    </datalist>
                    {scopeRows.length === 0 && (
                        <div style={{ fontSize: 12.5, color: 'var(--color-warning)', marginBottom: 6 }}>
                            ⚠ Без скоупов начислений не будет.
                        </div>
                    )}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 6 }}>
                        {scopeRows.map((r) => {
                            const brand = r.brand.trim();
                            const subject = r.subject.trim();
                            const preview = brand || subject
                                ? scopeLabel({ brand: brand || null, subject: subject || null })
                                : null;
                            return (
                                <div key={r.uid} style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                                    <input
                                        className="form-input" style={{ width: 200 }}
                                        list="payroll-scope-brands" placeholder="бренд (опционально)"
                                        value={r.brand}
                                        onChange={(e) => setScopeRow(r.uid, { brand: e.target.value })}
                                    />
                                    <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>×</span>
                                    <input
                                        className="form-input" style={{ width: 220 }}
                                        list="payroll-scope-subjects" placeholder="категория (опционально)"
                                        value={r.subject}
                                        onChange={(e) => setScopeRow(r.uid, { subject: e.target.value })}
                                    />
                                    {preview && (
                                        <span className={scopeBadgeClass({ brand: brand || null, subject: subject || null })}>
                                            {preview}
                                        </span>
                                    )}
                                    <button
                                        className="btn btn-secondary btn-sm"
                                        title="Удалить скоуп"
                                        onClick={() => setScopeRows((rows) => rows.filter((x) => x.uid !== r.uid))}
                                    >
                                        ✕
                                    </button>
                                </div>
                            );
                        })}
                    </div>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => setScopeRows((rows) => [...rows, { uid: scopeUidRef.current++, brand: '', subject: '' }])}
                    >
                        + Добавить скоуп
                    </button>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>
                        Заполнены оба поля — композит «бренд × категория»: пересечение уходит этой команде,
                        из общих скоупов других команд оно вычитается автоматически.
                    </div>
                </div>

                {/* ─── Участники + границы участия ───────────────────────────── */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.2fr', gap: 16, marginBottom: 8 }}>
                    <MultiPickList
                        title={`Участники (${memberIds.size})`}
                        items={employees.map((e) => ({
                            key: e.id,
                            label: e.is_active ? e.name : `${e.name} (неактивен)`,
                        }))}
                        selected={memberIds}
                        onToggle={toggleMember}
                        emptyHint="Сначала заведи сотрудников на соседнем табе"
                    />
                    <div>
                        <label className="form-label">Границы участия (опционально)</label>
                        {memberIds.size === 0 ? (
                            <div style={{ fontSize: 12.5, color: 'var(--color-text-dim)', padding: 6 }}>
                                Отметь участников слева.
                            </div>
                        ) : (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                                {employees.filter((e) => memberIds.has(e.id)).map((e) => {
                                    const b = memberBounds[e.id] ?? { from: '', to: '' };
                                    return (
                                        <div key={e.id} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, flexWrap: 'wrap' }}>
                                            <span style={{ minWidth: 110, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.name}</span>
                                            <span style={{ color: 'var(--color-text-muted)' }}>с</span>
                                            <MonthField
                                                value={b.from} onChange={(v) => setBound(e.id, { from: v })}
                                            />
                                            <span style={{ color: 'var(--color-text-muted)' }}>по</span>
                                            <MonthField
                                                value={b.to} onChange={(v) => setBound(e.id, { to: v })}
                                            />
                                        </div>
                                    );
                                })}
                                <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                                    Пусто — без границы; «по» — включительно.
                                </div>
                            </div>
                        )}
                    </div>
                </div>

                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 12 }}>
                    База команды за неделю = «Чистая выплата» WB по скоупам. Начисление делится поровну между участниками месяца.
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
