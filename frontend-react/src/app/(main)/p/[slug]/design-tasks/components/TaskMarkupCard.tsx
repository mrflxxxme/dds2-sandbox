'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import InfoTip from '@/components/InfoTip';
import SearchSelect from '@/components/SearchSelect';
import { labelColorClass } from '@/lib/design';
import { DESIGN_UI_HINT } from '@/lib/designHints';
import type {
    DesignAttributeOut,
    DesignLabelOut,
    DesignTaskDetail,
} from '@/types/api';

/**
 * Метки и реквизиты задачи в деталке (волна C).
 *
 * Кто и что может менять — решает бэк флагами can_set_labels / can_set_attributes
 * (§6.9): в терминальных статусах метки ещё меняются, реквизиты уже нет (Р31).
 * Все тексты ошибок показываем от бэка, своих не придумываем.
 */
export default function TaskMarkupCard({ task, onChanged, onError }: {
    task: DesignTaskDetail;
    onChanged: (task: DesignTaskDetail) => void;
    onError: (message: string) => void;
}) {
    const [labels, setLabels] = useState<DesignLabelOut[]>([]);
    const [attributes, setAttributes] = useState<DesignAttributeOut[]>([]);
    const [saving, setSaving] = useState(false);
    const mountedRef = useRef(true);

    useEffect(() => {
        mountedRef.current = true;
        // withUsage=false: карточка рисует имена и цвета, счётчик использования
        // здесь не показывается — незачем гонять GROUP BY по связям на каждом
        // открытии деталки.
        Promise.all([api.listDesignLabels(false, false), api.listDesignAttributes(false, false)])
            .then(([lbs, attrs]) => {
                if (!mountedRef.current) return;
                setLabels(lbs);
                setAttributes(attrs);
            })
            .catch(() => { /* справочники пустые — блок просто не предложит выбор */ });
        return () => { mountedRef.current = false; };
    }, []);

    const currentLabelIds = useMemo(() => new Set(task.labels.map((l) => l.id)), [task.labels]);
    const historyByLabel = useMemo(
        () => new Map(task.label_history.map((h) => [h.label_id, h.times])),
        [task.label_history],
    );

    const apply = useCallback(async (fn: () => Promise<DesignTaskDetail>) => {
        setSaving(true);
        try {
            onChanged(await fn());
        } catch (e) {
            onError(e instanceof Error ? e.message : 'Не удалось сохранить разметку');
        } finally {
            if (mountedRef.current) setSaving(false);
        }
    }, [onChanged, onError]);

    const toggleLabel = (labelId: number) => {
        const next = new Set(currentLabelIds);
        if (next.has(labelId)) next.delete(labelId);
        else next.add(labelId);
        void apply(() => api.setDesignTaskLabels(task.id, [...next]));
    };

    /** Значения реквизитов — REPLACE всего набора, поэтому собираем его целиком. */
    const setAttributeValue = (attributeId: number, valueId: number, isMulti: boolean) => {
        const kept = task.attributes
            .filter((a) => (isMulti ? true : a.attribute_id !== attributeId))
            .map((a) => a.value_id);
        const next = new Set(kept);
        if (isMulti && next.has(valueId)) next.delete(valueId);
        else if (valueId) next.add(valueId);
        void apply(() => api.setDesignTaskAttributes(task.id, [...next]));
    };

    const clearAttribute = (attributeId: number) => {
        const kept = task.attributes.filter((a) => a.attribute_id !== attributeId).map((a) => a.value_id);
        void apply(() => api.setDesignTaskAttributes(task.id, kept));
    };

    const canLabels = task.permissions.can_set_labels;
    const canAttrs = task.permissions.can_set_attributes;
    const activeAttributes = attributes.filter((a) => !a.is_archived);

    // Нечего показать и нечего выбрать — карточку не рисуем вовсе.
    if (
        labels.length === 0 && activeAttributes.length === 0
        && task.labels.length === 0 && task.attributes.length === 0
    ) {
        return null;
    }

    return (
        <div className="glass-card" style={{ opacity: saving ? 0.7 : 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Разметка</h3>
                <InfoTip text={canAttrs ? DESIGN_UI_HINT.taskLabels : DESIGN_UI_HINT.taskAttributes} icon />
            </div>

            {(labels.length > 0 || task.labels.length > 0) && (
                <div style={{ marginBottom: activeAttributes.length > 0 ? 16 : 0 }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 6 }}>Метки</div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        {labels.map((l) => {
                            const on = currentLabelIds.has(l.id);
                            const times = historyByLabel.get(l.id) ?? 0;
                            return (
                                <button
                                    key={l.id}
                                    type="button"
                                    disabled={!canLabels || saving}
                                    onClick={() => toggleLabel(l.id)}
                                    className={`dds-label-chip ${labelColorClass(l.color)}`}
                                    style={{
                                        cursor: canLabels ? 'pointer' : 'default',
                                        opacity: on ? 1 : 0.45,
                                        borderStyle: on ? 'solid' : 'dashed',
                                    }}
                                >
                                    <span className="dds-label-dot" />
                                    {l.name}
                                    {times > 1 && (
                                        // Нейтральная форма вместо «была 5 раза»:
                                        // плюрализацию русского счётного слова ради
                                        // одной подписи заводить не стоит.
                                        <span style={{ color: 'var(--color-text-muted)' }}>
                                            · вешали {formatNumber(times, 0)}×
                                        </span>
                                    )}
                                </button>
                            );
                        })}
                        {/* Метка, снятая из справочника, но оставшаяся на задаче (Р30). */}
                        {task.labels
                            .filter((l) => !labels.some((x) => x.id === l.id))
                            .map((l) => (
                                <span key={l.id} className={`dds-label-chip ${labelColorClass(l.color)}`}>
                                    <span className="dds-label-dot" />
                                    {l.name}
                                    <span style={{ color: 'var(--color-text-muted)' }}>· в архиве</span>
                                </span>
                            ))}
                    </div>
                </div>
            )}

            {activeAttributes.length > 0 && (
                <div>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 6 }}>Реквизиты</div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {activeAttributes.map((a) => {
                            const chosen = task.attributes.filter((x) => x.attribute_id === a.id);
                            return (
                                <div key={a.id} style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                                    <span style={{ fontSize: 13, minWidth: 120 }}>{a.name}</span>
                                    {a.is_multi ? (
                                        <>
                                            {a.values.filter((v) => !v.is_archived).map((v) => {
                                                const on = chosen.some((c) => c.value_id === v.id);
                                                return (
                                                    <button
                                                        key={v.id}
                                                        type="button"
                                                        disabled={!canAttrs || saving}
                                                        className={`btn btn-sm ${on ? 'btn-primary' : 'btn-secondary'}`}
                                                        onClick={() => setAttributeValue(a.id, v.id, true)}
                                                    >
                                                        {v.value}
                                                    </button>
                                                );
                                            })}
                                        </>
                                    ) : (
                                        <SearchSelect
                                            value={chosen[0] ? String(chosen[0].value_id) : ''}
                                            onChange={(v) => (v
                                                ? setAttributeValue(a.id, Number(v), false)
                                                : clearAttribute(a.id))}
                                            options={a.values
                                                .filter((v) => !v.is_archived)
                                                .map((v) => ({ value: String(v.id), label: v.value }))}
                                            placeholder={canAttrs ? 'Не выбрано' : (chosen[0]?.value ?? '—')}
                                            allLabel="Не выбрано"
                                            searchPlaceholder={`${a.name}…`}
                                        />
                                    )}
                                </div>
                            );
                        })}
                    </div>
                    {!canAttrs && (
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 8 }}>
                            {DESIGN_UI_HINT.taskAttributes}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
