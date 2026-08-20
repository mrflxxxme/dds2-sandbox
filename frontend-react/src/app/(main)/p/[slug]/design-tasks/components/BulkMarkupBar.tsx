'use client';

import { useCallback, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { labelColorClass } from '@/lib/design';
import type { DesignAttributeOut, DesignBulkResultOut, DesignLabelOut } from '@/types/api';

/**
 * Панель массового проставления разметки (WC §7, Р33).
 *
 * Появляется, когда в списке отмечена хотя бы одна строка. «Проставить»
 * добавляет выбранное к тому, что уже стоит на задаче, «Снять» — убирает;
 * REPLACE-семантика ручки задачи здесь намеренно не используется, иначе
 * массовое действие затирало бы чужую разметку на попавших в выборку задачах.
 *
 * Итог показываем целиком: бэк отвечает 200 и на частичный успех
 * (`skipped` — задачи без прав, `errors` — всё остальное), и молчаливое
 * «готово» скрыло бы от пользователя, что часть задач не изменилась.
 */
export default function BulkMarkupBar({
    selectedIds,
    labels,
    attributes,
    onDone,
    onClear,
}: {
    selectedIds: number[];
    labels: DesignLabelOut[];
    attributes: DesignAttributeOut[];
    /** Разметка изменилась — список нужно перечитать. */
    onDone: (summary: string) => void;
    onClear: () => void;
}) {
    const [labelId, setLabelId] = useState('');
    const [valueId, setValueId] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const activeLabels = useMemo(() => labels.filter((l) => !l.is_archived), [labels]);
    const chosenLabel = useMemo(
        () => activeLabels.find((l) => String(l.id) === labelId),
        [activeLabels, labelId],
    );
    /** Плоский список «Поле — Значение»: одним селектом покрываем все реквизиты. */
    const valueOptions = useMemo(
        () => attributes
            .filter((a) => !a.is_archived)
            .flatMap((a) => a.values
                .filter((v) => !v.is_archived)
                .map((v) => ({ id: v.id, text: `${a.name} — ${v.value}` }))),
        [attributes],
    );

    const describe = useCallback((res: DesignBulkResultOut, verb: string) => {
        const parts = [`${verb}: ${formatNumber(res.updated, 0)}`];
        if (res.skipped > 0) parts.push(`без прав — ${formatNumber(res.skipped, 0)}`);
        if (res.errors.length > 0) parts.push(`с ошибкой — ${formatNumber(res.errors.length, 0)}`);
        return parts.join(', ');
    }, []);

    const run = useCallback(async (mode: 'add' | 'remove') => {
        setBusy(true);
        setError(null);
        try {
            const verb = mode === 'add' ? 'Проставлено' : 'Снято';
            //  Итог собирается и отдаётся ОДИН раз, даже если выбраны и метка,
            //  и реквизит: два вызова onDone дали бы два тоста подряд и две
            //  перезагрузки списка, из которых видна только вторая.
            const summaries: string[] = [];
            if (labelId) {
                summaries.push(describe(await api.bulkDesignLabels(selectedIds, [Number(labelId)], mode), verb));
            }
            if (valueId) {
                summaries.push(describe(await api.bulkDesignAttributes(selectedIds, [Number(valueId)], mode), verb));
            }
            if (summaries.length > 0) onDone(summaries.join('; '));
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось применить разметку');
        } finally {
            setBusy(false);
        }
    }, [labelId, valueId, selectedIds, onDone, describe]);

    const nothingChosen = !labelId && !valueId;

    return (
        <div
            className="glass-card"
            style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}
        >
            <strong style={{ fontSize: 14 }}>Выбрано {formatNumber(selectedIds.length, 0)}</strong>

            <select
                className="form-input"
                style={{ width: 200 }}
                value={labelId}
                onChange={(e) => setLabelId(e.target.value)}
                aria-label="Метка"
            >
                <option value="">Метка…</option>
                {activeLabels.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
            {chosenLabel && (
                <span className={`dds-label-chip ${labelColorClass(chosenLabel.color)}`}>
                    <span className="dds-label-dot" />
                    {chosenLabel.name}
                </span>
            )}

            <select
                className="form-input"
                style={{ width: 240 }}
                value={valueId}
                onChange={(e) => setValueId(e.target.value)}
                aria-label="Значение реквизита"
            >
                <option value="">Реквизит…</option>
                {valueOptions.map((v) => <option key={v.id} value={v.id}>{v.text}</option>)}
            </select>

            <button
                className="btn btn-sm btn-primary"
                disabled={busy || nothingChosen}
                onClick={() => void run('add')}
            >
                Проставить
            </button>
            <button
                className="btn btn-sm btn-secondary"
                disabled={busy || nothingChosen}
                onClick={() => void run('remove')}
            >
                Снять
            </button>
            <button className="btn btn-sm btn-secondary" style={{ marginLeft: 'auto' }} onClick={onClear}>
                Сбросить выбор
            </button>

            {error && (
                <div style={{ flexBasis: '100%', color: 'var(--color-danger)', fontSize: 13 }}>{error}</div>
            )}
        </div>
    );
}
