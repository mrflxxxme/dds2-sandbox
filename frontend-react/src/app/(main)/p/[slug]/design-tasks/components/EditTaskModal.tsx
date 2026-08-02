'use client';

import { useState } from 'react';
import { api } from '@/lib/api';
import WbThumb from '@/components/WbThumb';
import { DESIGN_COMPLEXITY_LABEL, DESIGN_WORK_TYPE_LABEL, isValidHttpUrl } from '@/lib/design';
import type { DesignComplexity, DesignTaskDetail, DesignTaskUpdatePayload, DesignWorkType } from '@/types/api';
import ModalShell from './ModalShell';
import ProductSuggest from './ProductSuggest';

const WORK_TYPES = Object.keys(DESIGN_WORK_TYPE_LABEL) as DesignWorkType[];
const COMPLEXITIES = Object.keys(DESIGN_COMPLEXITY_LABEL) as DesignComplexity[];

interface EditTaskModalProps {
    task: DesignTaskDetail;
    onSaved: (task: DesignTaskDetail) => void;
    onClose: () => void;
}

/** Редактирование заявки (PATCH-семантика — шлём только изменённые поля). */
export default function EditTaskModal({ task, onSaved, onClose }: EditTaskModalProps) {
    const [title, setTitle] = useState(task.title);
    const [description, setDescription] = useState(task.description);
    const [sheetUrl, setSheetUrl] = useState(task.sheet_url);
    const [workType, setWorkType] = useState<DesignWorkType>(task.work_type);
    const [complexity, setComplexity] = useState<DesignComplexity>(task.complexity);
    const [isUrgent, setIsUrgent] = useState(task.is_urgent);
    const [isOutsourced, setIsOutsourced] = useState(task.is_outsourced);
    const [dueDate, setDueDate] = useState(task.due_date ?? '');
    const [nmId, setNmId] = useState<number | null>(task.nm_id);
    const [nmQuery, setNmQuery] = useState('');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    const save = async () => {
        if (!title.trim()) { setError('Название не может быть пустым'); return; }
        if (description.trim().length < 10) { setError('Суть — минимум 10 символов'); return; }
        if (!isValidHttpUrl(sheetUrl.trim())) { setError('Ссылка на ТЗ-таблицу должна быть корректным http(s)-URL'); return; }

        const payload: DesignTaskUpdatePayload = {};
        if (title.trim() !== task.title) payload.title = title.trim();
        if (description.trim() !== task.description) payload.description = description.trim();
        if (sheetUrl.trim() !== task.sheet_url) payload.sheet_url = sheetUrl.trim();
        if (workType !== task.work_type) payload.work_type = workType;
        if (isUrgent !== task.is_urgent) payload.is_urgent = isUrgent;
        if ((dueDate || null) !== task.due_date) payload.due_date = dueDate || null;
        if (nmId !== task.nm_id && nmId != null) payload.nm_id = nmId;
        if (task.permissions.can_set_complexity && complexity !== task.complexity) payload.complexity = complexity;
        if (task.permissions.can_set_outsource && isOutsourced !== task.is_outsourced) payload.is_outsourced = isOutsourced;

        if (Object.keys(payload).length === 0) { onClose(); return; }

        setError('');
        setSaving(true);
        try {
            const updated = await api.updateDesignTask(task.id, payload);
            onSaved(updated);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось сохранить');
        } finally {
            setSaving(false);
        }
    };

    return (
        <ModalShell title={`Редактировать ${task.number}`} onClose={onClose} width={560}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <div>
                    <label style={{ display: 'block', fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 4 }}>Название *</label>
                    <input className="form-input" style={{ width: '100%' }} value={title} maxLength={300} onChange={(e) => setTitle(e.target.value)} />
                </div>
                <div>
                    <label style={{ display: 'block', fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 4 }}>Суть * (мин. 10 символов)</label>
                    <textarea className="form-input" style={{ width: '100%', minHeight: 80, resize: 'vertical' }} value={description} maxLength={5000} onChange={(e) => setDescription(e.target.value)} />
                </div>
                <div>
                    <label style={{ display: 'block', fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 4 }}>Ссылка на ТЗ-таблицу *</label>
                    <input className="form-input" style={{ width: '100%' }} value={sheetUrl} onChange={(e) => setSheetUrl(e.target.value)} />
                </div>
                <div>
                    <label style={{ display: 'block', fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 4 }}>Товар</label>
                    {nmId != null ? (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <WbThumb nmId={nmId} size={28} height={36} />
                            <span style={{ fontSize: 13 }}>Артикул <b>{nmId}</b></span>
                            {nmId !== task.nm_id && (
                                <button className="btn btn-sm btn-secondary" onClick={() => setNmId(task.nm_id)}>Вернуть прежний</button>
                            )}
                        </div>
                    ) : (
                        <ProductSuggest
                            value={nmQuery}
                            onChange={setNmQuery}
                            placeholder="Привязать товар (поиск по артикулу/названию)"
                            onPick={(s) => { setNmId(s.nm_id); setNmQuery(''); }}
                        />
                    )}
                </div>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                    <div>
                        <label style={{ display: 'block', fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 4 }}>Тип работы</label>
                        <select className="form-input" value={workType} onChange={(e) => setWorkType(e.target.value as DesignWorkType)}>
                            {WORK_TYPES.map((w) => <option key={w} value={w}>{DESIGN_WORK_TYPE_LABEL[w]}</option>)}
                        </select>
                    </div>
                    {task.permissions.can_set_complexity && (
                        <div>
                            <label style={{ display: 'block', fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 4 }}>Сложность</label>
                            <select className="form-input" value={complexity} onChange={(e) => setComplexity(e.target.value as DesignComplexity)}>
                                {COMPLEXITIES.map((c) => <option key={c} value={c}>{DESIGN_COMPLEXITY_LABEL[c]}</option>)}
                            </select>
                        </div>
                    )}
                    <div>
                        <label style={{ display: 'block', fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 4 }}>Срок</label>
                        <input className="form-input" type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 16 }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 14, cursor: 'pointer' }}>
                        <input type="checkbox" checked={isUrgent} onChange={(e) => setIsUrgent(e.target.checked)} />
                        🔥 Срочно
                    </label>
                    {task.permissions.can_set_outsource && (
                        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 14, cursor: 'pointer' }}>
                            <input type="checkbox" checked={isOutsourced} onChange={(e) => setIsOutsourced(e.target.checked)} />
                            Аутсорс
                        </label>
                    )}
                </div>

                {error && <div style={{ color: 'var(--color-danger)', fontSize: 13 }}>{error}</div>}

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={saving}>Отмена</button>
                    <button className="btn btn-primary" onClick={() => void save()} disabled={saving}>
                        {saving ? 'Сохранение…' : '💾 Сохранить'}
                    </button>
                </div>
            </div>
        </ModalShell>
    );
}
