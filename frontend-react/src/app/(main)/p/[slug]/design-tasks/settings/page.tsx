'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import PageHeader from '@/components/PageHeader';
import Toast from '@/components/Toast';
import InfoTip from '@/components/InfoTip';
import { LABEL_COLORS, LABEL_COLOR_LABEL, labelColorClass } from '@/lib/design';
import { DESIGN_UI_HINT } from '@/lib/designHints';
import type {
    DesignAttributeOut,
    DesignLabelColor,
    DesignLabelOut,
} from '@/types/api';
import DesignTabs from '../components/DesignTabs';
import { useDesignBoardPermissions } from '../components/useDesignBoardPermissions';

/**
 * Что именно правится прямо сейчас. Одна строка за раз: параллельное
 * редактирование двух записей потребовало бы словаря черновиков, а перезагрузка
 * после сохранения всё равно сбрасывает форму.
 */
type Editing =
    | { kind: 'label'; id: number; name: string; color: DesignLabelColor }
    | { kind: 'attribute'; id: number; name: string; isMulti: boolean }
    | { kind: 'value'; id: number; value: string }
    | null;

/** Шаг нумерации порядка: оставляет зазор под ручные правки sort_order из БД. */
const ORDER_STEP = 10;

/** Вкладка «Настройки» (Р30): справочники меток и реквизитов ведёт ведущий дизайнер. */
export default function DesignSettingsPage() {
    const params = useParams<{ slug: string }>();
    // Право на ведение справочника считает бэк (§6.9), роль фронт не проверяет.
    // Через хук, а не своим GET /board: тот тянет всю доску (до 1200 карточек)
    // ради одного бита — и делал это после КАЖДОЙ мутации справочника.
    const canManage = useDesignBoardPermissions().can_manage_refs;

    const [labels, setLabels] = useState<DesignLabelOut[] | null>(null);
    const [attributes, setAttributes] = useState<DesignAttributeOut[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    const [labelName, setLabelName] = useState('');
    const [labelColor, setLabelColor] = useState<DesignLabelColor>('red');
    const [attrName, setAttrName] = useState('');
    const [attrMulti, setAttrMulti] = useState(false);
    const [newValue, setNewValue] = useState<Record<number, string>>({});
    const [editing, setEditing] = useState<Editing>(null);
    const [dragLabelId, setDragLabelId] = useState<number | null>(null);
    const mountedRef = useRef(true);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [lbs, attrs] = await Promise.all([
                api.listDesignLabels(),
                api.listDesignAttributes(),
            ]);
            if (!mountedRef.current) return;
            setLabels(lbs);
            setAttributes(attrs);
        } catch (e) {
            if (mountedRef.current) setError(e instanceof Error ? e.message : 'Не удалось загрузить справочники');
        } finally {
            if (mountedRef.current) setLoading(false);
        }
    }, []);

    useEffect(() => {
        mountedRef.current = true;
        void load();
        return () => { mountedRef.current = false; };
    }, [load]);

    /** Любая мутация: показать текст ошибки БЭКА, своих формулировок не придумывать.
     *  Возвращает признак успеха — по нему решается, чистить ли поле ввода:
     *  иначе при ошибке пользователь терял бы набранное вместе с тостом. */
    const run = useCallback(async (fn: () => Promise<unknown>, ok: string): Promise<boolean> => {
        try {
            await fn();
            setToast({ type: 'success', message: ok });
            await load();
            return true;
        } catch (e) {
            setToast({ type: 'error', message: e instanceof Error ? e.message : 'Не удалось сохранить' });
            return false;
        }
    }, [load]);

    const confirmArchive = (what: string, usage: number) =>
        window.confirm(
            usage > 0
                ? `${what}\n\nИспользуется в ${formatNumber(usage, 0)} задачах — там она останется видимой, но для новых задач станет недоступна.`
                : `${what}\n\nЗапись уйдёт в архив.`,
        );

    /**
     * Перетаскивание метки: бросок НА метку = вставить перед ней (та же
     * семантика, что у карточек доски). Отдельной ручки reorder нет — пишем
     * sort_order каждой СДВИНУВШЕЙСЯ метки обычным PUT, остальных не трогаем.
     */
    const reorderLabel = useCallback(async (dragId: number, beforeId: number) => {
        if (!labels || dragId === beforeId) return;
        const rest = labels.filter((l) => l.id !== dragId);
        const moved = labels.find((l) => l.id === dragId);
        const at = rest.findIndex((l) => l.id === beforeId);
        if (!moved || at < 0) return;
        const next = [...rest.slice(0, at), moved, ...rest.slice(at)];
        const changed = next.filter((l, i) => l.sort_order !== i * ORDER_STEP);
        if (changed.length === 0) return;
        await run(async () => {
            for (const l of changed) {
                await api.updateDesignLabel(l.id, {
                    name: l.name,
                    color: l.color,
                    sort_order: next.indexOf(l) * ORDER_STEP,
                });
            }
        }, 'Порядок меток сохранён');
    }, [labels, run]);

    if (loading) {
        return (
            <PageGuard page="design-tasks">
                <PageHeader title="🖌️ Дизайн карточек — настройки" actions={<DesignTabs slug={params.slug} active="settings" canManageRefs={canManage} />} />
                <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка…</div>
            </PageGuard>
        );
    }
    if (error) {
        return (
            <PageGuard page="design-tasks">
                <PageHeader title="🖌️ Дизайн карточек — настройки" actions={<DesignTabs slug={params.slug} active="settings" canManageRefs={canManage} />} />
                <div className="glass-card" style={{ color: 'var(--color-danger)' }}>
                    {error} <button className="btn btn-sm btn-secondary" onClick={() => void load()}>Повторить</button>
                </div>
            </PageGuard>
        );
    }

    return (
        <PageGuard page="design-tasks">
            <PageHeader
                title="🖌️ Дизайн карточек — настройки"
                subtitle="Цветные метки и поля-реквизиты проекта"
                actions={<DesignTabs slug={params.slug} active="settings" canManageRefs={canManage} />}
            />

            {!canManage && (
                <div className="glass-card" style={{ marginBottom: 16, color: 'var(--color-text-muted)', fontSize: 13 }}>
                    Справочники ведёт ведущий дизайнер — здесь они доступны только для просмотра.
                </div>
            )}

            {/* ── Метки ── */}
            <div className="glass-card" style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                    <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Цветные метки</h3>
                    <InfoTip text={DESIGN_UI_HINT.refsLabels} icon />
                </div>

                {canManage && (
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
                        <input
                            className="form-input"
                            style={{ width: 220 }}
                            placeholder="Название метки"
                            maxLength={60}
                            value={labelName}
                            onChange={(e) => setLabelName(e.target.value)}
                        />
                        <select
                            className="form-input"
                            style={{ width: 160 }}
                            value={labelColor}
                            onChange={(e) => setLabelColor(e.target.value as DesignLabelColor)}
                        >
                            {LABEL_COLORS.map((c) => (
                                <option key={c} value={c}>{LABEL_COLOR_LABEL[c]}</option>
                            ))}
                        </select>
                        <span className={`dds-label-chip ${labelColorClass(labelColor)}`}>
                            <span className="dds-label-dot" />
                            {labelName.trim() || 'образец'}
                        </span>
                        <button
                            className="btn btn-primary btn-sm"
                            disabled={!labelName.trim()}
                            onClick={() => void run(
                                () => api.createDesignLabel({ name: labelName.trim(), color: labelColor }),
                                'Метка создана',
                            ).then((ok) => { if (ok) setLabelName(''); })}
                        >
                            + Добавить метку
                        </button>
                    </div>
                )}

                {labels && labels.length === 0 && (
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
                        Меток пока нет. Заведите те, которыми команда реально размечает задачи.
                    </div>
                )}
                {canManage && labels && labels.length > 1 && (
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 8 }}>
                        Перетащите метку за ⠿, чтобы изменить порядок в списках выбора.
                    </div>
                )}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {(labels ?? []).map((l) => {
                        const edit = editing?.kind === 'label' && editing.id === l.id ? editing : null;
                        return (
                            <div
                                key={l.id}
                                draggable={canManage && !edit}
                                onDragStart={(e) => {
                                    e.dataTransfer.effectAllowed = 'move';
                                    e.dataTransfer.setData('text/plain', String(l.id));
                                    setDragLabelId(l.id);
                                }}
                                onDragOver={(e) => { if (dragLabelId !== null) e.preventDefault(); }}
                                onDrop={(e) => {
                                    e.preventDefault();
                                    const from = dragLabelId;
                                    setDragLabelId(null);
                                    if (from !== null) void reorderLabel(from, l.id);
                                }}
                                onDragEnd={() => setDragLabelId(null)}
                                style={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: 10,
                                    opacity: dragLabelId === l.id ? 0.5 : 1,
                                }}
                            >
                                {canManage && (
                                    <span
                                        aria-hidden
                                        style={{ cursor: 'grab', color: 'var(--color-text-dim)', fontSize: 14 }}
                                    >
                                        ⠿
                                    </span>
                                )}

                                {edit ? (
                                    <>
                                        <input
                                            className="form-input"
                                            style={{ width: 200 }}
                                            maxLength={60}
                                            value={edit.name}
                                            aria-label={`Название метки ${l.name}`}
                                            onChange={(e) => setEditing({ ...edit, name: e.target.value })}
                                        />
                                        <select
                                            className="form-input"
                                            style={{ width: 150 }}
                                            value={edit.color}
                                            aria-label={`Цвет метки ${l.name}`}
                                            onChange={(e) => setEditing({ ...edit, color: e.target.value as DesignLabelColor })}
                                        >
                                            {LABEL_COLORS.map((c) => (
                                                <option key={c} value={c}>{LABEL_COLOR_LABEL[c]}</option>
                                            ))}
                                        </select>
                                        <span className={`dds-label-chip ${labelColorClass(edit.color)}`}>
                                            <span className="dds-label-dot" />
                                            {edit.name.trim() || 'образец'}
                                        </span>
                                        <button
                                            className="btn btn-sm btn-primary"
                                            style={{ marginLeft: 'auto' }}
                                            disabled={!edit.name.trim()}
                                            onClick={() => void run(
                                                // sort_order передаём текущий: PUT принимает
                                                // объект целиком, и без него метка уехала бы
                                                // в конец списка на дефолтном значении.
                                                () => api.updateDesignLabel(l.id, {
                                                    name: edit.name.trim(),
                                                    color: edit.color,
                                                    sort_order: l.sort_order,
                                                }),
                                                'Метка сохранена',
                                            ).then((ok) => { if (ok) setEditing(null); })}
                                        >
                                            Сохранить
                                        </button>
                                        <button className="btn btn-sm btn-secondary" onClick={() => setEditing(null)}>
                                            Отмена
                                        </button>
                                    </>
                                ) : (
                                    <>
                                        <span className={`dds-label-chip ${labelColorClass(l.color)}`}>
                                            <span className="dds-label-dot" />
                                            {l.name}
                                        </span>
                                        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                            в {formatNumber(l.usage_count, 0)} задачах
                                        </span>
                                        {canManage && (
                                            <>
                                                <button
                                                    className="btn btn-sm btn-secondary"
                                                    style={{ marginLeft: 'auto' }}
                                                    onClick={() => setEditing({ kind: 'label', id: l.id, name: l.name, color: l.color })}
                                                >
                                                    Переименовать
                                                </button>
                                                <button
                                                    className="btn btn-sm btn-danger"
                                                    onClick={() => {
                                                        if (!confirmArchive(`Убрать метку «${l.name}» из справочника?`, l.usage_count)) return;
                                                        void run(() => api.archiveDesignLabel(l.id), 'Метка убрана в архив');
                                                    }}
                                                >
                                                    Убрать
                                                </button>
                                            </>
                                        )}
                                    </>
                                )}
                            </div>
                        );
                    })}
                </div>
            </div>

            {/* ── Реквизиты ── */}
            <div className="glass-card">
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                    <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Реквизиты</h3>
                    <InfoTip text={DESIGN_UI_HINT.refsAttributes} icon />
                </div>

                {canManage && (
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
                        <input
                            className="form-input"
                            style={{ width: 220 }}
                            placeholder="Название поля, например «Бренд»"
                            maxLength={60}
                            value={attrName}
                            onChange={(e) => setAttrName(e.target.value)}
                        />
                        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                            <input type="checkbox" checked={attrMulti} onChange={(e) => setAttrMulti(e.target.checked)} />
                            Несколько значений
                        </label>
                        <button
                            className="btn btn-primary btn-sm"
                            disabled={!attrName.trim()}
                            onClick={() => void run(
                                () => api.createDesignAttribute({ name: attrName.trim(), is_multi: attrMulti }),
                                'Поле создано',
                            ).then((ok) => { if (ok) { setAttrName(''); setAttrMulti(false); } })}
                        >
                            + Добавить поле
                        </button>
                    </div>
                )}

                {attributes && attributes.length === 0 && (
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
                        Полей пока нет. Поле — это список значений: бренд, кабинет ВБ, площадка.
                    </div>
                )}

                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                    {(attributes ?? []).map((a) => {
                        const edit = editing?.kind === 'attribute' && editing.id === a.id ? editing : null;
                        return (
                            <div key={a.id} style={{ border: '1px solid var(--color-border)', borderRadius: 12, padding: 12 }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                                    {edit ? (
                                        <>
                                            <input
                                                className="form-input"
                                                style={{ width: 220 }}
                                                maxLength={60}
                                                value={edit.name}
                                                aria-label={`Название поля ${a.name}`}
                                                onChange={(e) => setEditing({ ...edit, name: e.target.value })}
                                            />
                                            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
                                                <input
                                                    type="checkbox"
                                                    checked={edit.isMulti}
                                                    onChange={(e) => setEditing({ ...edit, isMulti: e.target.checked })}
                                                />
                                                Несколько значений
                                            </label>
                                            <button
                                                className="btn btn-sm btn-primary"
                                                style={{ marginLeft: 'auto' }}
                                                disabled={!edit.name.trim()}
                                                onClick={() => void run(
                                                    () => api.updateDesignAttribute(a.id, {
                                                        name: edit.name.trim(),
                                                        is_multi: edit.isMulti,
                                                        sort_order: a.sort_order,
                                                    }),
                                                    'Поле сохранено',
                                                ).then((ok) => { if (ok) setEditing(null); })}
                                            >
                                                Сохранить
                                            </button>
                                            <button className="btn btn-sm btn-secondary" onClick={() => setEditing(null)}>
                                                Отмена
                                            </button>
                                        </>
                                    ) : (
                                        <>
                                            <span style={{ fontWeight: 600, fontSize: 14 }}>{a.name}</span>
                                            {a.is_multi && <span className="badge badge-info" style={{ fontSize: 10 }}>несколько значений</span>}
                                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                                в {formatNumber(a.usage_count, 0)} задачах
                                            </span>
                                            {canManage && (
                                                <>
                                                    <button
                                                        className="btn btn-sm btn-secondary"
                                                        style={{ marginLeft: 'auto' }}
                                                        onClick={() => setEditing({ kind: 'attribute', id: a.id, name: a.name, isMulti: a.is_multi })}
                                                    >
                                                        Переименовать
                                                    </button>
                                                    <button
                                                        className="btn btn-sm btn-danger"
                                                        onClick={() => {
                                                            if (!confirmArchive(`Убрать поле «${a.name}» из справочника?`, a.usage_count)) return;
                                                            void run(() => api.archiveDesignAttribute(a.id), 'Поле убрано в архив');
                                                        }}
                                                    >
                                                        Убрать
                                                    </button>
                                                </>
                                            )}
                                        </>
                                    )}
                                </div>

                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: canManage ? 8 : 0 }}>
                                    {a.values.length === 0 && (
                                        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Значений пока нет.</span>
                                    )}
                                    {a.values.map((v) => {
                                        const vEdit = editing?.kind === 'value' && editing.id === v.id ? editing : null;
                                        if (vEdit) {
                                            return (
                                                <span key={v.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                                                    <input
                                                        className="form-input"
                                                        style={{ width: 180 }}
                                                        maxLength={120}
                                                        value={vEdit.value}
                                                        aria-label={`Значение ${v.value}`}
                                                        onChange={(e) => setEditing({ ...vEdit, value: e.target.value })}
                                                    />
                                                    <button
                                                        className="btn btn-sm btn-primary"
                                                        disabled={!vEdit.value.trim()}
                                                        onClick={() => void run(
                                                            () => api.updateDesignAttributeValue(v.id, {
                                                                value: vEdit.value.trim(),
                                                                sort_order: v.sort_order,
                                                            }),
                                                            'Значение сохранено',
                                                        ).then((ok) => { if (ok) setEditing(null); })}
                                                    >
                                                        ОК
                                                    </button>
                                                    <button className="btn btn-sm btn-secondary" onClick={() => setEditing(null)}>
                                                        Отмена
                                                    </button>
                                                </span>
                                            );
                                        }
                                        return (
                                            <span
                                                key={v.id}
                                                className="badge badge-secondary"
                                                style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
                                            >
                                                {v.value}
                                                <span style={{ color: 'var(--color-text-dim)' }}>{formatNumber(v.usage_count, 0)}</span>
                                                {canManage && (
                                                    <>
                                                        <button
                                                            aria-label={`Переименовать значение ${v.value}`}
                                                            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-muted)', padding: 0 }}
                                                            onClick={() => setEditing({ kind: 'value', id: v.id, value: v.value })}
                                                        >
                                                            ✎
                                                        </button>
                                                        <button
                                                            aria-label={`Убрать значение ${v.value}`}
                                                            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-danger)', padding: 0 }}
                                                            onClick={() => {
                                                                if (!confirmArchive(`Убрать значение «${v.value}»?`, v.usage_count)) return;
                                                                void run(() => api.archiveDesignAttributeValue(v.id), 'Значение убрано в архив');
                                                            }}
                                                        >
                                                            ✕
                                                        </button>
                                                    </>
                                                )}
                                            </span>
                                        );
                                    })}
                                </div>

                                {canManage && (
                                    <div style={{ display: 'flex', gap: 8 }}>
                                        <input
                                            className="form-input"
                                            style={{ width: 220 }}
                                            placeholder="Новое значение"
                                            maxLength={120}
                                            value={newValue[a.id] ?? ''}
                                            onChange={(e) => setNewValue((prev) => ({ ...prev, [a.id]: e.target.value }))}
                                        />
                                        <button
                                            className="btn btn-secondary btn-sm"
                                            disabled={!(newValue[a.id] ?? '').trim()}
                                            onClick={() => void run(
                                                () => api.createDesignAttributeValue(a.id, { value: (newValue[a.id] ?? '').trim() }),
                                                'Значение добавлено',
                                            ).then((ok) => { if (ok) setNewValue((prev) => ({ ...prev, [a.id]: '' })); })}
                                        >
                                            + Добавить значение
                                        </button>
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            </div>

            {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
        </PageGuard>
    );
}
