'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import WbThumb from '@/components/WbThumb';
import InfoTip from '@/components/InfoTip';
import Tooltip from '@/components/Tooltip';
import {
    DESIGN_STATUS_BADGE,
    DESIGN_STATUS_LABEL,
    DESIGN_WORK_TYPE_LABEL,
} from '@/lib/design';
import { DESIGN_CHIP_HINT, DESIGN_MARK_HINT } from '@/lib/designHints';
import type { DesignTaskListItem, DesignTaskStatus, DesignWorkType } from '@/types/api';

type Chip = 'all' | 'NEW' | 'ASSIGNED' | 'IN_PROGRESS' | 'REVIEW' | 'REVISION' | 'ON_HOLD' | 'overdue';

const CHIPS: { key: Chip; label: string }[] = [
    { key: 'all', label: 'Все' },
    { key: 'NEW', label: 'Новые' },
    { key: 'ASSIGNED', label: 'Назначены' },
    { key: 'IN_PROGRESS', label: 'В работе' },
    { key: 'REVIEW', label: 'На проверке' },
    { key: 'REVISION', label: 'Правки' },
    { key: 'ON_HOLD', label: 'Отложены' },
    { key: 'overdue', label: 'Просрочено' },
];

const WORK_TYPES = Object.keys(DESIGN_WORK_TYPE_LABEL) as DesignWorkType[];

/** Список задач (второй вид): чипы со счётчиками → фильтры → таблица, сортировка по сроку. */
export default function ListView({ slug }: { slug: string }) {
    const router = useRouter();
    const [tasks, setTasks] = useState<DesignTaskListItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [chip, setChip] = useState<Chip>('all');
    const [assignee, setAssignee] = useState('');
    const [workType, setWorkType] = useState<DesignWorkType | ''>('');
    const [urgentOnly, setUrgentOnly] = useState(false);
    const [q, setQ] = useState('');
    const mountedRef = useRef(true);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await api.listDesignTasks({ limit: 200 });
            if (!mountedRef.current) return;
            setTasks(res);
        } catch (e) {
            if (!mountedRef.current) return;
            setError(e instanceof Error ? e.message : 'Не удалось загрузить задачи');
        } finally {
            if (mountedRef.current) setLoading(false);
        }
    }, []);

    useEffect(() => {
        mountedRef.current = true;
        void load();
        return () => { mountedRef.current = false; };
    }, [load]);

    const chipCount = useCallback(
        (c: Chip) => {
            if (c === 'all') return tasks.length;
            if (c === 'overdue') return tasks.filter((t) => t.is_overdue).length;
            return tasks.filter((t) => t.status === c).length;
        },
        [tasks],
    );

    const assignees = useMemo(
        () => [...new Set(tasks.map((t) => t.assignee_name).filter((n): n is string => !!n))].sort(),
        [tasks],
    );

    const visible = useMemo(() => {
        const needle = q.trim().toLowerCase();
        const filtered = tasks.filter((t) => {
            if (chip === 'overdue') { if (!t.is_overdue) return false; }
            else if (chip !== 'all' && t.status !== chip) return false;
            if (assignee && t.assignee_name !== assignee) return false;
            if (workType && t.work_type !== workType) return false;
            if (urgentOnly && !t.is_urgent) return false;
            if (needle && !t.title.toLowerCase().includes(needle) && !t.number.toLowerCase().includes(needle)) return false;
            return true;
        });
        // Сортировка по сроку (без срока — в конец), затем по номеру.
        return filtered.sort((a, b) => {
            if (a.due_date && b.due_date) return a.due_date.localeCompare(b.due_date);
            if (a.due_date) return -1;
            if (b.due_date) return 1;
            return a.number.localeCompare(b.number);
        });
    }, [tasks, chip, assignee, workType, urgentOnly, q]);

    if (loading) {
        return <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка…</div>;
    }
    if (error) {
        return (
            <div className="glass-card" style={{ color: 'var(--color-danger)' }}>
                {error} <button className="btn btn-sm btn-secondary" onClick={() => void load()}>Повторить</button>
            </div>
        );
    }

    return (
        <>
            <div className="glass-card" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                {CHIPS.map(({ key, label }) => (
                    <Tooltip key={key} text={DESIGN_CHIP_HINT[key]}>
                        <button
                            className={`btn btn-sm ${chip === key ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setChip(key)}
                        >
                            {label} {formatNumber(chipCount(key), 0)}
                        </button>
                    </Tooltip>
                ))}
            </div>

            <div className="glass-card" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <select className="form-input" style={{ width: 180 }} value={assignee} onChange={(e) => setAssignee(e.target.value)}>
                    <option value="">Все исполнители</option>
                    {assignees.map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
                <select className="form-input" style={{ width: 180 }} value={workType} onChange={(e) => setWorkType(e.target.value as DesignWorkType | '')}>
                    <option value="">Все типы</option>
                    {WORK_TYPES.map((w) => <option key={w} value={w}>{DESIGN_WORK_TYPE_LABEL[w]}</option>)}
                </select>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                    <input type="checkbox" checked={urgentOnly} onChange={(e) => setUrgentOnly(e.target.checked)} />
                    Только срочные
                </label>
                <input
                    className="form-input"
                    style={{ width: 220, marginLeft: 'auto' }}
                    placeholder="Поиск: название или номер"
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                />
            </div>

            {visible.length === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: 48 }}>
                    {tasks.length === 0
                        ? 'Заявок ещё нет. Создайте первую: «+ Новая заявка».'
                        : 'Нет задач под выбранные фильтры.'}
                </div>
            )}

            {visible.length > 0 && (
                <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid var(--color-border)', color: 'var(--color-text-muted)', fontSize: 12 }}>
                                <th style={{ textAlign: 'left', padding: '12px 16px' }}>Номер</th>
                                <th style={{ textAlign: 'left', padding: '12px 16px' }}>Товар</th>
                                <th style={{ textAlign: 'left', padding: '12px 16px' }}>Тип</th>
                                <th style={{ textAlign: 'left', padding: '12px 16px' }}>Сложность</th>
                                <th style={{ textAlign: 'left', padding: '12px 16px' }}>Срочно</th>
                                <th style={{ textAlign: 'left', padding: '12px 16px' }}>Исполнитель</th>
                                <th style={{ textAlign: 'left', padding: '12px 16px' }}>Срок</th>
                                <th style={{ textAlign: 'right', padding: '12px 16px' }}>Версий</th>
                                <th style={{ textAlign: 'left', padding: '12px 16px' }}>Статус</th>
                            </tr>
                        </thead>
                        <tbody>
                            {visible.map((t) => (
                                <tr
                                    key={t.id}
                                    onClick={() => router.push(`/p/${slug}/design-tasks/${t.id}`)}
                                    style={{ borderBottom: '1px solid var(--color-border)', cursor: 'pointer' }}
                                >
                                    <td style={{ padding: '12px 16px', whiteSpace: 'nowrap' }}>
                                        {t.unviewed && (
                                            <InfoTip text={DESIGN_MARK_HINT.unviewed}>
                                                <span role="img" aria-label="Не просмотрено ведущим" style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: 'var(--color-danger)', marginRight: 6 }} />
                                            </InfoTip>
                                        )}
                                        {t.number}
                                    </td>
                                    <td style={{ padding: '12px 16px' }}>
                                        <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                                            <WbThumb nmId={t.nm_id} size={32} height={42} />
                                            <span>{t.title}</span>
                                        </span>
                                    </td>
                                    <td style={{ padding: '12px 16px', whiteSpace: 'nowrap' }}>{DESIGN_WORK_TYPE_LABEL[t.work_type]}</td>
                                    <td style={{ padding: '12px 16px' }}>{t.complexity}</td>
                                    <td style={{ padding: '12px 16px' }}>
                                        {t.is_urgent
                                            ? <Tooltip text={DESIGN_MARK_HINT.urgent}><span className="badge badge-danger">Срочно</span></Tooltip>
                                            : '—'}
                                    </td>
                                    <td style={{ padding: '12px 16px', whiteSpace: 'nowrap' }}>{t.assignee_name ?? '—'}</td>
                                    <td style={{ padding: '12px 16px', whiteSpace: 'nowrap', color: t.is_overdue ? 'var(--color-danger)' : undefined, fontWeight: t.is_overdue ? 600 : undefined }}>
                                        {formatDate(t.due_date)}
                                    </td>
                                    <td style={{ padding: '12px 16px', textAlign: 'right' }}>{formatNumber(t.versions_count, 0)}</td>
                                    <td style={{ padding: '12px 16px' }}>
                                        <span className={`badge ${DESIGN_STATUS_BADGE[t.status]}`}>{DESIGN_STATUS_LABEL[t.status]}</span>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </>
    );
}
