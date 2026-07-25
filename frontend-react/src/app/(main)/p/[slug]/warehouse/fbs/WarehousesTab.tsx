'use client';
/**
 * Вкладка «Склады» — склады продавца WB, их настройки трансляции остатков
 * и привязка к нашим складам (что именно кормит FBS).
 */
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatDateTime, formatNumber } from '@/lib/utils';
import type {
    FbsOffice,
    FbsWarehouse,
    FbsWarehouseMode,
    FbsWarehouseSettingsPayload,
    Warehouse,
} from '@/types/api';
import {
    cargoLabel,
    deliveryLabel,
    giveModeOf,
    isTranslating,
    num,
    warehouseModeLabel,
} from './fbsShared';
import { EnableTranslationModal } from './fbsReconcile';

interface Props {
    warehouses: FbsWarehouse[];
    loading: boolean;
    error: string;
    /**
     * Режим контура разрешает запись в WB. Гасим только действия, которые
     * реально меняют кабинет (создать / переименовать склад). Синк, привязки
     * и настройки трансляции — наши данные, они доступны всегда.
     */
    writeEnabled: boolean;
    /** Текст подсказки на выключенной кнопке: safe-режим или «режим не загружен». */
    writeHint: string;
    onReload: () => void;
    onToast: (msg: string) => void;
}

export default function WarehousesTab({
    warehouses, loading, error, writeEnabled, writeHint, onReload, onToast,
}: Props) {
    const [ourWarehouses, setOurWarehouses] = useState<Warehouse[]>([]);
    const [syncing, setSyncing] = useState(false);
    const [creating, setCreating] = useState(false);
    const [actionError, setActionError] = useState('');

    useEffect(() => {
        const controller = new AbortController();
        api.getWarehouses()
            .then(ws => { if (!controller.signal.aborted) setOurWarehouses(ws); })
            .catch(() => { /* привязка останется без выпадающего списка */ });
        return () => controller.abort();
    }, []);

    const handleSync = async () => {
        setSyncing(true);
        setActionError('');
        try {
            const res = await api.syncFbsWarehouses();
            onToast(
                typeof res?.affected === 'number'
                    ? `Синхронизировано складов WB: ${formatNumber(res.affected, 0)}`
                    : 'Склады синхронизированы',
            );
            onReload();
        } catch (e: unknown) {
            setActionError(e instanceof Error ? e.message : 'Ошибка синхронизации складов');
        } finally {
            setSyncing(false);
        }
    };

    return (
        <>
            <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap', alignItems: 'center' }}>
                <button className="btn btn-secondary btn-sm" onClick={handleSync} disabled={syncing}>
                    {syncing ? 'Синхронизация...' : '🔄 Синхронизировать склады'}
                </button>
                <button
                    className="btn btn-primary btn-sm"
                    onClick={() => setCreating(true)}
                    disabled={!writeEnabled}
                    title={writeEnabled ? undefined : writeHint}
                >
                    + Создать склад в WB
                </button>
                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                    Остатки уходят только с активных складов, у которых есть привязка к нашему складу.
                </span>
            </div>

            {actionError && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-danger)' }}>
                    {actionError}
                </div>
            )}

            {loading ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
            ) : error ? (
                <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>
            ) : warehouses.length === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center' }}>
                    <div style={{ fontSize: 40, marginBottom: 12 }}>🏬</div>
                    <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>Складов продавца WB нет</div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                        Нажмите «Синхронизировать склады», чтобы подтянуть их из кабинета,
                        или создайте новый склад прямо здесь.
                    </div>
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                    {warehouses.map(wh => (
                        <WarehouseCard
                            key={wh.id}
                            wh={wh}
                            ourWarehouses={ourWarehouses}
                            writeEnabled={writeEnabled}
                            writeHint={writeHint}
                            onReload={onReload}
                            onToast={onToast}
                        />
                    ))}
                </div>
            )}

            {creating && (
                <CreateWarehouseModal
                    onClose={() => setCreating(false)}
                    onCreated={() => { setCreating(false); onReload(); onToast('Склад создан в WB'); }}
                />
            )}
        </>
    );
}

// ─── Карточка склада WB ─────────────────────────────────────────────────────

function WarehouseCard({ wh, ourWarehouses, writeEnabled, writeHint, onReload, onToast }: {
    wh: FbsWarehouse;
    ourWarehouses: Warehouse[];
    writeEnabled: boolean;
    writeHint: string;
    onReload: () => void;
    onToast: (msg: string) => void;
}) {
    const [isActive, setIsActive] = useState(wh.is_active);
    /** Наблюдение — штатный режим: считаем и показываем, но в WB ничего не пишем. */
    const [mode, setMode] = useState<FbsWarehouseMode>(
        isTranslating(wh.mode) ? 'translate' : 'observe',
    );
    const [pct, setPct] = useState(String(num(wh.safety_stock_pct)));
    const [abs, setAbs] = useState(String(wh.safety_stock_abs ?? 0));
    const [maxQty, setMaxQty] = useState(String(wh.max_qty_per_sku ?? 0));
    const [saving, setSaving] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [linkId, setLinkId] = useState<number | ''>('');
    const [renaming, setRenaming] = useState(false);
    /**
     * Спрашиваем про сверку ДО первого включения трансляции: остатки,
     * проставленные в кабинете руками, дельта-пуш не обнулит никогда.
     */
    const [askEnable, setAskEnable] = useState(false);

    // Внешнее обновление (после sync / привязки) не должно теряться в локальном стейте.
    //
    // Зависимости — ЗНАЧЕНИЯ, а не объект `wh`: список складов перечитывается
    // автообновлением раз в 3 минуты, и каждый ответ приносит НОВЫЙ объект с теми
    // же полями. По `[wh]` эффект срабатывал бы на каждый тик и стирал бы
    // несохранённые правки формы прямо под руками. По значениям он срабатывает
    // только когда на сервере реально что-то поменялось — тогда сброс и нужен.
    useEffect(() => {
        setIsActive(wh.is_active);
        setMode(isTranslating(wh.mode) ? 'translate' : 'observe');
        setPct(String(num(wh.safety_stock_pct)));
        setAbs(String(wh.safety_stock_abs ?? 0));
        setMaxQty(String(wh.max_qty_per_sku ?? 0));
    }, [wh.is_active, wh.mode, wh.safety_stock_pct, wh.safety_stock_abs, wh.max_qty_per_sku]);

    const doSave = async () => {
        setSaving(true);
        setError('');
        const payload: FbsWarehouseSettingsPayload = {
            is_active: isActive,
            mode,
            // Источник больше не выбирается руками: он определяется по самому складу
            // (есть зеркало WMS → min(наш учёт, зеркало), нет → наш учёт).
            // Нормализуем легаси-значения, чтобы старые карточки не залипали на ledger.
            stock_source: 'min_of_both',
            safety_stock_pct: Number(pct.replace(',', '.')) || 0,
            safety_stock_abs: Number(abs) || 0,
            max_qty_per_sku: Number(maxQty) || 0,
        };
        try {
            await api.updateFbsWarehouseSettings(wh.wb_warehouse_id, payload);
            setAskEnable(false);
            onToast('Настройки склада сохранены');
            onReload();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка сохранения настроек');
        } finally {
            setSaving(false);
        }
    };

    /**
     * Включение трансляции — момент, когда WB начнёт продавать по нашим цифрам.
     * Не блокируем, но сначала показываем, что в кабинете уже может стоять
     * руками проставленный остаток: сам он не обнулится. Переход
     * «Наблюдение → Трансляция» — ровно тот же момент, спрашиваем и о нём.
     */
    const handleSave = () => {
        const startsTranslating = mode === 'translate' && !isTranslating(wh.mode);
        if ((isActive && !wh.is_active) || startsTranslating) {
            // Чистим прошлую ошибку до открытия: модалка показывает `error`,
            // и ошибка привязки читалась бы как отказ включения трансляции.
            setError('');
            setAskEnable(true);
            return;
        }
        doSave();
    };

    const handleLink = async () => {
        if (linkId === '') return;
        setBusy(true);
        setError('');
        try {
            await api.linkFbsWarehouse({ wb_warehouse_id: wh.wb_warehouse_id, warehouse_id: linkId });
            setLinkId('');
            onToast('Склад привязан');
            onReload();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка привязки склада');
        } finally {
            setBusy(false);
        }
    };

    const handleUnlink = async (id: number) => {
        if (!confirm('Отвязать наш склад от склада WB? Его остатки перестанут уходить в FBS.')) return;
        setBusy(true);
        setError('');
        try {
            await api.unlinkFbsWarehouse(id);
            onToast('Привязка удалена');
            onReload();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка удаления привязки');
        } finally {
            setBusy(false);
        }
    };

    const linkedIds = new Set(wh.links.map(l => l.warehouse_id));
    const linkable = ourWarehouses.filter(w => !linkedIds.has(w.id) && w.is_active);

    return (
        <div className="glass-card" style={{ padding: 20 }}>
            {/* Шапка */}
            <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
                <div style={{ fontSize: 16, fontWeight: 600 }}>{wh.name || `Склад #${wh.wb_warehouse_id}`}</div>
                <span
                    className={`badge ${isTranslating(wh.mode) ? 'badge-success' : 'badge-info'}`}
                    title={isTranslating(wh.mode)
                        ? 'Остатки этого склада реально уезжают в кабинет WB'
                        : 'Наблюдение: считаем и показываем, но в WB ничего не пишем'}
                >
                    {warehouseModeLabel(wh.mode)}
                </span>
                <span className={`badge ${wh.is_active ? 'badge-success' : 'badge-secondary'}`}>
                    {wh.is_active ? 'трансляция вкл' : 'трансляция выкл'}
                </span>
                <span
                    className="badge badge-secondary"
                    title="Что отдаём в FBS — переключается на вкладке «Остатки»"
                >
                    {giveModeOf(wh.fbo_max_qty) === 'all' ? 'все остатки' : 'чего нет на FBO'}
                </span>
                {wh.is_processing && (
                    <span className="badge badge-warning" title="WB не принимает остатки, пока склад в обработке">
                        в обработке
                    </span>
                )}
                {wh.is_deleting && <span className="badge badge-danger">удаляется</span>}
                <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                    WB ID {wh.wb_warehouse_id} · офис {wh.office_name || wh.office_id || '—'}
                    {' · '}груз: {cargoLabel(wh.cargo_type)} · доставка: {deliveryLabel(wh.delivery_type)}
                </span>
                <div style={{ flex: 1 }} />
                <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => setRenaming(true)}
                    disabled={!writeEnabled}
                    title={writeEnabled ? undefined : writeHint}
                >
                    Переименовать
                </button>
                {wh.synced_at && (
                    <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                        синк: {formatDateTime(wh.synced_at)}
                    </span>
                )}
            </div>

            {/* Привязки наших складов */}
            <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Наши склады — источник остатка</div>
                {wh.links.length === 0 ? (
                    <div style={{ fontSize: 13, color: 'var(--color-warning)', marginBottom: 8 }}>
                        Привязок нет — остатки на этот склад WB не транслируются.
                    </div>
                ) : (
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                        {wh.links.map(link => (
                            <span
                                key={link.id}
                                style={{
                                    display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 13,
                                    padding: '4px 10px', borderRadius: 24,
                                    border: '1px solid var(--color-border)',
                                    opacity: link.is_active ? 1 : 0.5,
                                }}
                            >
                                <span>
                                    {link.warehouse_name || `Склад #${link.warehouse_id}`}
                                    <span style={{ fontSize: 11, color: 'var(--color-text-dim)', marginLeft: 6 }}>
                                        {link.has_mirror
                                            ? `зеркало ${link.mirror_provider || 'ФФ'} · ${formatNumber(link.mirror_rows, 0)} поз.`
                                            : 'наш складской учёт'}
                                        {link.has_mirror && !link.integration_active && ' · интеграция выключена'}
                                    </span>
                                </span>
                                <button
                                    className="btn btn-sm"
                                    style={{ padding: '0 6px', lineHeight: 1.4 }}
                                    title="Отвязать"
                                    disabled={busy}
                                    onClick={() => handleUnlink(link.id)}
                                >
                                    ✕
                                </button>
                            </span>
                        ))}
                    </div>
                )}
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <select
                        className="form-input"
                        style={{ width: 240 }}
                        value={linkId}
                        onChange={e => setLinkId(e.target.value ? Number(e.target.value) : '')}
                    >
                        <option value="">— выберите наш склад —</option>
                        {linkable.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
                    </select>
                    <button className="btn btn-secondary btn-sm" onClick={handleLink} disabled={linkId === '' || busy}>
                        Привязать
                    </button>
                </div>
            </div>

            {/* Режим склада: наблюдение — штатное состояние, не авария */}
            <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Режим склада</div>
                <div style={{ display: 'flex', gap: 0, marginBottom: 6 }}>
                    <button
                        className={`btn btn-sm ${mode === 'observe' ? 'btn-primary' : 'btn-secondary'}`}
                        style={{ borderTopRightRadius: 0, borderBottomRightRadius: 0 }}
                        onClick={() => setMode('observe')}
                        title="Считаем и показываем, что ушло бы в WB, но ничего не передаём"
                    >
                        👀 Наблюдение
                    </button>
                    <button
                        className={`btn btn-sm ${mode === 'translate' ? 'btn-primary' : 'btn-secondary'}`}
                        style={{ borderTopLeftRadius: 0, borderBottomLeftRadius: 0 }}
                        onClick={() => setMode('translate')}
                        title="Остатки реально уезжают в кабинет WB — товар начнёт продаваться по нашим цифрам"
                    >
                        📤 Трансляция
                    </button>
                </div>
                <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                    {mode === 'observe'
                        ? 'В наблюдении экран «Остатки» работает полностью — расчёт, ручное количество и сверка. '
                          + 'В WB не уходит ничего.'
                        : 'Остатки уезжают в кабинет: WB начнёт продавать по нашим цифрам.'}
                    {' '}Режим применяется после «Сохранить настройки».
                </div>
            </div>

            {/* Настройки трансляции */}
            <div style={{
                display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                gap: 12, alignItems: 'flex-end',
            }}>
                <div className="form-group" style={{ margin: 0 }}>
                    <label className="form-label" style={{ fontSize: 12 }} title="Страховой буфер в % от источника">
                        Буфер, %
                    </label>
                    <input className="form-input" type="number" min={0} max={100} step="0.01"
                        value={pct} onChange={e => setPct(e.target.value)} />
                </div>
                <div className="form-group" style={{ margin: 0 }}>
                    <label className="form-label" style={{ fontSize: 12 }} title="Страховой буфер в штуках">
                        Буфер, шт
                    </label>
                    <input className="form-input" type="number" min={0} step="1"
                        value={abs} onChange={e => setAbs(e.target.value)} />
                </div>
                <div className="form-group" style={{ margin: 0 }}>
                    <label className="form-label" style={{ fontSize: 12 }} title="0 — без ограничения">
                        Максимум на SKU, шт
                    </label>
                    <input className="form-input" type="number" min={0} step="1"
                        value={maxQty} onChange={e => setMaxQty(e.target.value)} />
                </div>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, paddingBottom: 8 }}>
                    <input type="checkbox" checked={isActive} onChange={e => setIsActive(e.target.checked)} />
                    Транслировать остатки
                </label>
                <button className="btn btn-primary btn-sm" onClick={handleSave} disabled={saving}>
                    {saving ? 'Сохранение...' : 'Сохранить настройки'}
                </button>
            </div>

            {error && <div style={{ marginTop: 12, fontSize: 13, color: 'var(--color-danger)' }}>{error}</div>}

            {askEnable && (
                <EnableTranslationModal
                    wbWarehouseId={wh.wb_warehouse_id}
                    warehouseName={wh.name || `Склад #${wh.wb_warehouse_id}`}
                    // Здесь превью нет — «транслировался ли раньше» знает только
                    // вкладка «Остатки». Склад был выключен, значит риск реален.
                    firstTime={null}
                    writeEnabled={writeEnabled}
                    writeHint={writeHint}
                    isProcessing={wh.is_processing}
                    noLinks={wh.links.length === 0}
                    busy={saving}
                    // Ошибка сохранения обязана быть видна НАД оверлеем: блок
                    // с `error` в карточке остаётся под модалкой, и провалившееся
                    // сохранение читается как «кнопка не работает».
                    error={error}
                    onToast={onToast}
                    onConfirm={doSave}
                    onClose={() => setAskEnable(false)}
                />
            )}

            {renaming && (
                <RenameWarehouseModal
                    wh={wh}
                    onClose={() => setRenaming(false)}
                    onRenamed={() => { setRenaming(false); onReload(); onToast('Склад переименован'); }}
                />
            )}
        </div>
    );
}

// ─── Модалка создания склада в WB ───────────────────────────────────────────

function CreateWarehouseModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
    const [offices, setOffices] = useState<FbsOffice[]>([]);
    const [loading, setLoading] = useState(true);
    const [name, setName] = useState('');
    const [officeId, setOfficeId] = useState<number | ''>('');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        const controller = new AbortController();
        (async () => {
            setLoading(true);
            try {
                const res = await api.getFbsOffices();
                if (controller.signal.aborted) return;
                setOffices(res);
            } catch (e: unknown) {
                if (controller.signal.aborted) return;
                setError(e instanceof Error ? e.message : 'Ошибка загрузки списка офисов WB');
            } finally {
                if (!controller.signal.aborted) setLoading(false);
            }
        })();
        return () => controller.abort();
    }, []);

    const handleSave = async () => {
        if (!name.trim() || officeId === '') {
            setError('Укажите название и пункт приёма');
            return;
        }
        setSaving(true);
        setError('');
        try {
            await api.createFbsWarehouse({ name: name.trim(), office_id: officeId });
            onCreated();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка создания склада');
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 520 }}>
                <h2 className="modal-title">Создать склад продавца в WB</h2>
                <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 16 }}>
                    Склад создаётся на стороне WB. Пункт приёма (офис) потом можно менять не чаще раза в сутки.
                </p>

                <div className="form-group">
                    <label className="form-label">Название *</label>
                    <input className="form-input" value={name} maxLength={200}
                        onChange={e => setName(e.target.value)} placeholder="Например: Основной FBS" />
                </div>

                <div className="form-group">
                    <label className="form-label">Пункт приёма (офис WB) *</label>
                    {loading ? (
                        <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Загрузка списка...</div>
                    ) : offices.length === 0 ? (
                        <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                            Список офисов пуст — проверьте ключ «Маркетплейс» в интеграциях.
                        </div>
                    ) : (
                        <select className="form-input" value={officeId}
                            onChange={e => setOfficeId(e.target.value ? Number(e.target.value) : '')}>
                            <option value="">— выберите офис —</option>
                            {offices.map(o => (
                                <option key={o.id} value={o.id}>
                                    {o.name || `Офис #${o.id}`}{o.city ? ` — ${o.city}` : ''}
                                </option>
                            ))}
                        </select>
                    )}
                </div>

                {error && <div style={{ marginBottom: 12, fontSize: 13, color: 'var(--color-danger)' }}>{error}</div>}

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={saving}>Отмена</button>
                    <button className="btn btn-primary" onClick={handleSave} disabled={saving || loading}>
                        {saving ? 'Создание...' : 'Создать'}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ─── Модалка переименования ─────────────────────────────────────────────────

function RenameWarehouseModal({ wh, onClose, onRenamed }: {
    wh: FbsWarehouse;
    onClose: () => void;
    onRenamed: () => void;
}) {
    const [name, setName] = useState(wh.name || '');
    const [officeId, setOfficeId] = useState<number | ''>(wh.office_id ?? '');
    const [offices, setOffices] = useState<FbsOffice[]>([]);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    const loadOffices = useCallback(async (signal: AbortSignal) => {
        try {
            const res = await api.getFbsOffices();
            if (signal.aborted) return;
            setOffices(res);
        } catch {
            /* смена офиса останется недоступной — переименование работает и без списка */
        }
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        loadOffices(controller.signal);
        return () => controller.abort();
    }, [loadOffices]);

    const handleSave = async () => {
        if (!name.trim()) {
            setError('Название обязательно');
            return;
        }
        setSaving(true);
        setError('');
        try {
            await api.renameFbsWarehouse(wh.wb_warehouse_id, {
                name: name.trim(),
                office_id: officeId === '' ? null : officeId,
            });
            onRenamed();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка переименования');
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 480 }}>
                <h2 className="modal-title">Склад WB #{wh.wb_warehouse_id}</h2>
                <div className="form-group">
                    <label className="form-label">Название *</label>
                    <input className="form-input" value={name} maxLength={200} onChange={e => setName(e.target.value)} />
                </div>
                <div className="form-group">
                    <label className="form-label">Пункт приёма (WB меняет не чаще раза в сутки)</label>
                    <select className="form-input" value={officeId}
                        onChange={e => setOfficeId(e.target.value ? Number(e.target.value) : '')}>
                        <option value="">— не менять —</option>
                        {offices.map(o => (
                            <option key={o.id} value={o.id}>
                                {o.name || `Офис #${o.id}`}{o.city ? ` — ${o.city}` : ''}
                            </option>
                        ))}
                    </select>
                </div>
                {error && <div style={{ marginBottom: 12, fontSize: 13, color: 'var(--color-danger)' }}>{error}</div>}
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button className="btn btn-secondary" onClick={onClose} disabled={saving}>Отмена</button>
                    <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
                        {saving ? 'Сохранение...' : 'Сохранить'}
                    </button>
                </div>
            </div>
        </div>
    );
}
