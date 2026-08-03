'use client';
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type { FfBulkCreateAssemblyResult, FulfillmentStatus, MigfullDraftResponse, MigfullPortalConfig, PackageType } from '@/types/api';
import MigfullModal from '../../[id]/MigfullModal';

/** Провайдеры, для которых работает создание заявки ФФ из DDS (bulk-эндпоинт). */
const FF_PUSH_PROVIDERS = new Set(['skladbot', 'wmscelicom']);

/** Бриф созданной заявки для модалки (собирается родителем из created-groups
 *  черновика либо из ответа предраспределения машины). */
export interface CreatedRequestRow {
    id: number;
    number: string;
    /** ФФ-склад-источник (склад сдачи товара) — нужен для создания заявки ФФ. */
    ffId: number;
    ffName: string;
    /** WB-склад направления. */
    wbName: string | null;
    pkg: PackageType;
    qty: number;
    sku: number;
}

const PKG_ICON: Record<PackageType, string> = { BOX: '📦', MONOPALLET: '📐', SUPERSAFE: '🔒' };
const PKG_LABEL: Record<PackageType, string> = { BOX: 'Короб', MONOPALLET: 'Моно', SUPERSAFE: 'Сейф' };

/** Статус push одной заявки в ФФ/WB (per-row прогресс массовых действий). */
type CellState = { st: 'idle' | 'busy' | 'done' | 'warn' | 'error'; note?: string };

const FF_STATUS_LABEL: Record<FfBulkCreateAssemblyResult['status'], string> = {
    created: 'Создана',
    deficit: 'Нехватка остатков',
    no_warehouse: 'Склад не подобран',
    already_linked: 'Уже связана',
    empty: 'Нет позиций',
    error: 'Ошибка',
};

/** Отправка одной заявки в портал Натали по УЖЕ загруженному драфту (префилл as-is).
 *  Возвращает итог строкой статуса; ⚠ = у описи были предупреждения (россыпь). */
async function sendNataliFromDraft(assemblyId: number, draft: MigfullDraftResponse): Promise<CellState> {
    try {
        const res = await api.migfullPortalSend({ kind: 'assembly', id: assemblyId }, {
            filter_delivery_type: draft.prefill.filter_delivery_type,
            number: draft.prefill.number ?? null,
            shipment_date: draft.prefill.shipment_date ?? null,
            notes: draft.prefill.notes ?? null,
            force_resend: false,
        });
        if (res.ok) return { st: 'done', note: `${res.shipment_number || 'Создана'}${draft.warnings.length ? ' ⚠' : ''}` };
        return { st: 'error', note: res.message || 'Ошибка портала Натали' };
    } catch (e: unknown) {
        const conflict = !!e && typeof e === 'object' && (e as { code?: string }).code === 'conflict';
        if (conflict) return { st: 'warn', note: 'Уже создана — повтор только по строке' };
        return { st: 'error', note: e instanceof Error ? e.message : 'Ошибка портала Натали' };
    }
}

/** Длинные причины (отказы провайдеров) не распирают строку: многоточие,
 *  полный текст — в тултипе. */
const BADGE_CLAMP: CSSProperties = {
    maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis',
    whiteSpace: 'nowrap', display: 'inline-block', verticalAlign: 'bottom',
};

function cellBadge(c: CellState | undefined, doneLabel: string): ReactNode {
    if (!c || c.st === 'idle') return null;
    if (c.st === 'busy') return <span className="badge badge-info">⏳…</span>;
    if (c.st === 'done') return <span className="badge badge-success" style={BADGE_CLAMP} title={c.note}>{c.note || doneLabel}</span>;
    if (c.st === 'warn') return <span className="badge badge-warning" style={BADGE_CLAMP} title={c.note}>{c.note}</span>;
    return <span className="badge badge-danger" style={BADGE_CLAMP} title={c.note}>{c.note || 'Ошибка'}</span>;
}

/**
 * Модалка «Созданные заявки» после коммита черновика / предраспределения машины:
 * список созданных сборок (ASM-номер, ФФ-источник, WB-направление, состав) +
 * создание WB-поставки (преордер кабинета) и заявки ФФ — по строке и массово.
 * Закрытие → родитель делает свой прежний post-commit переход.
 */
export default function CreatedRequestsModal({ slug, rows, listQuery, onClose }: {
    slug: string;
    rows: CreatedRequestRow[];
    /** query для «Открыть в списке» (напр. `src_draft=42&just_created=1,2`). */
    listQuery?: string;
    onClose: () => void;
}) {
    const router = useRouter();
    const [wbState, setWbState] = useState<Map<number, CellState>>(new Map());
    const [ffState, setFfState] = useState<Map<number, CellState>>(new Map());
    const [massBusy, setMassBusy] = useState<'wb' | 'ff' | null>(null);
    // Параметры создаваемых заявок ФФ (общие для всех) — как в FfBulkCreateModal.
    // Дата — в ЛОКАЛЬНОМ поясе: toISOString (UTC) ночью (00:00–03:00 МСК) отдавал
    // вчерашний день → дефолт забора сдвигался на сутки назад.
    const plus3 = () => {
        const d = new Date();
        d.setDate(d.getDate() + 3);
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    };
    const [collectionDate, setCollectionDate] = useState(plus3());
    const [deliveryType, setDeliveryType] = useState<'straight' | 'cross_dock'>('straight');
    // Занос в WB идёт реплеем кабинета — без активной сессии упадёт каждый преордер.
    const [wbSession, setWbSession] = useState<'ACTIVE' | 'EXPIRED' | 'NONE' | null>(null);
    useEffect(() => {
        let cancelled = false;
        api.wbSessionStatus()
            .then(s => { if (!cancelled) setWbSession(s.status); })
            .catch(() => { if (!cancelled) setWbSession(null); });
        return () => { cancelled = true; };
    }, []);

    // Возможности ФФ-складов: провайдер с пушем (skladbot/wmscelicom) → кнопка
    // «Создать»; портальный оператор (Хамза) → сборка уже у него, заявка не нужна;
    // без интеграции (апл) → создание невозможно. null = статус ещё грузится.
    const [ffStatusById, setFfStatusById] = useState<Map<number, FulfillmentStatus> | null>(null);
    useEffect(() => {
        let cancelled = false;
        const ids = [...new Set(rows.map(r => r.ffId))];
        Promise.all(ids.map(id => api.getFulfillmentStatus(id).then(
            st => [id, st] as const,
            // Ошибка статуса → считаем «без интеграции» (кнопку не предлагаем).
            () => [id, { connected: false } as FulfillmentStatus] as const,
        ))).then(pairs => { if (!cancelled) setFfStatusById(new Map(pairs)); });
        return () => { cancelled = true; };
    }, [rows]);
    // Портал ФФ «Натали» (migfull_portal): у склада read-only migfull-интеграция,
    // но заявку МОЖНО создать реплеем их портала — той же MigfullModal, что на
    // странице сборки (превью описи + подтверждение; отправка НЕОБРАТИМА).
    const [migfullCfg, setMigfullCfg] = useState<MigfullPortalConfig | null>(null);
    useEffect(() => {
        let cancelled = false;
        api.migfullPortalConfig()
            .then(c => { if (!cancelled) setMigfullCfg(c); })
            .catch(() => { if (!cancelled) setMigfullCfg(null); });
        return () => { cancelled = true; };
    }, []);
    const [migfullFor, setMigfullFor] = useState<CreatedRequestRow | null>(null);

    const ffMode = useCallback((ffId: number): 'push' | 'natali' | 'portal' | 'provider' | 'none' | 'loading' => {
        if (!ffStatusById) return 'loading';
        const st = ffStatusById.get(ffId);
        if (st?.connected && FF_PUSH_PROVIDERS.has(st.provider || '')) return 'push';
        if (migfullCfg?.configured && migfullCfg.warehouse_id === ffId) return 'natali';
        if (st?.has_portal_operator) return 'portal';
        if (st?.connected) return 'provider'; // интеграция есть, но пуш не поддержан
        return 'none';
    }, [ffStatusById, migfullCfg]);

    const setWb = useCallback((id: number, c: CellState) => setWbState(prev => new Map(prev).set(id, c)), []);
    const setFf = useCallback((id: number, c: CellState) => setFfState(prev => new Map(prev).set(id, c)), []);

    // ─── WB: преордер кабинета (упаковку берёт из package_type заявки) ───────
    const pushWbOne = useCallback(async (row: CreatedRequestRow) => {
        setWb(row.id, { st: 'busy' });
        try {
            const st = await api.wbSupplyCreatePreorder(row.id);
            if (st.sync_status === 'ERROR') setWb(row.id, { st: 'error', note: st.last_error || 'Ошибка WB' });
            else setWb(row.id, { st: 'done', note: st.preorder_id ? `Преордер ${st.preorder_id}` : 'Заведена' });
        } catch (e: unknown) {
            setWb(row.id, { st: 'error', note: e instanceof Error ? e.message : 'Ошибка WB' });
        }
    }, [setWb]);

    // Массовый занос WB — ФОНОВЫЙ джоб на бэке (по одной с паузой ~10с, кабинет
    // не любит частые заносы): стартуем, отпускаем кнопки и поллим статус.
    // Джоб живёт на сервере — модалку МОЖНО закрыть в любой момент (итоги видны
    // в WB-колонке списка); гард `alive` глушит поллинг/сетстейты после анмаунта.
    const alive = useRef(true);
    useEffect(() => () => { alive.current = false; }, []);
    const [wbJobRunning, setWbJobRunning] = useState(false);
    const applyWbJob = useCallback((job: import('@/types/api').WbBulkPreorderStatus) => {
        if (!alive.current) return false;
        setWbState(prev => {
            const next = new Map(prev);
            for (const r of job.results) {
                next.set(r.assembly_id, r.ok ? { st: 'done', note: r.note || 'Заведена' } : { st: 'error', note: r.note || 'Ошибка WB' });
            }
            if (job.running && job.current_assembly_id != null) {
                next.set(job.current_assembly_id, { st: 'busy' });
            } else if (!job.running) {
                // Финал/рестарт сервера: busy-строки без итога отпускаем — иначе
                // wbInFlight навечно блокирует WB-кнопки (тупик после рестарта).
                const withResult = new Set(job.results.map(r => r.assembly_id));
                for (const [id, c] of next) if (c.st === 'busy' && !withResult.has(id)) next.delete(id);
            }
            return next;
        });
        return job.running;
    }, []);
    const pollWbJob = useCallback(async () => {
        setWbJobRunning(true);
        let fails = 0;
        try {
            while (alive.current) {
                await new Promise(res => setTimeout(res, 4000));
                if (!alive.current) return;
                try {
                    const job = await api.wbSupplyBulkPreorderStatus();
                    fails = 0;
                    if (!applyWbJob(job)) return;
                } catch {
                    // Потолок сбоев: связь с бэком потеряна — джоб продолжится на
                    // сервере, статусы видны в списке заявок. Без потолка модалка
                    // зависала бы в вечном поллинге (тупик).
                    if (++fails >= 5) return;
                }
            }
        } finally {
            if (alive.current) setWbJobRunning(false);
        }
    }, [applyWbJob]);
    const pushWbAll = useCallback(async () => {
        const ids = rows.filter(r => { const c = wbState.get(r.id); return !c || (c.st !== 'done' && c.st !== 'busy'); }).map(r => r.id);
        if (ids.length === 0) return;
        setMassBusy('wb'); // только на время старта — дальше модалка свободна
        let started = false;
        try {
            const job = await api.wbSupplyBulkPreorderStart(ids);
            started = applyWbJob(job);
        } catch (e: unknown) {
            // conflict (409) = батч уже идёт — подключаемся к его статусу.
            if (!!e && typeof e === 'object' && (e as { code?: string }).code === 'conflict') started = true;
            else if (alive.current) setWbState(prev => { const n = new Map(prev); for (const id of ids) if (n.get(id)?.st === 'busy') n.delete(id); return n; });
        } finally {
            if (alive.current) setMassBusy(null);
        }
        if (started) void pollWbJob(); // НЕ await — закрытие модалки не блокируется
    }, [rows, wbState, applyWbJob, pollWbJob]);

    // ─── ФФ: заявки провайдеру (bulk-эндпоинт, батч per ФФ-склад) ────────────
    const applyFfResults = useCallback((ids: number[], results: FfBulkCreateAssemblyResult[]) => {
        const byId = new Map(results.map(r => [r.assembly_request_id, r]));
        setFfState(prev => {
            const next = new Map(prev);
            for (const id of ids) {
                const r = byId.get(id);
                if (!r) { next.set(id, { st: 'error', note: 'Нет результата' }); continue; }
                if (r.status === 'created') next.set(id, { st: 'done', note: r.ff_number || 'Создана' });
                else if (r.status === 'error') next.set(id, { st: 'error', note: r.message || FF_STATUS_LABEL.error });
                else next.set(id, { st: 'warn', note: FF_STATUS_LABEL[r.status] });
            }
            return next;
        });
    }, []);

    const pushFfBatch = useCallback(async (ffId: number, batch: CreatedRequestRow[]) => {
        const ids = batch.map(r => r.id);
        setFfState(prev => { const n = new Map(prev); for (const id of ids) n.set(id, { st: 'busy' }); return n; });
        try {
            const res = await api.bulkCreateFfRequests(ffId, {
                assembly_request_ids: ids,
                collection_date: collectionDate,
                delivery_type: deliveryType,
            });
            applyFfResults(ids, res.results);
        } catch (e: unknown) {
            const note = e instanceof Error ? e.message : 'Ошибка ФФ';
            setFfState(prev => { const n = new Map(prev); for (const id of ids) n.set(id, { st: 'error', note }); return n; });
        }
    }, [collectionDate, deliveryType, applyFfResults]);

    // Массовый пуш: bulk-провайдеры (skladbot/wmscelicom) батчами; Натали — через
    // сводное окно (описи всех заявок → выбор состава → необратимая отправка).
    const [nataliBatch, setNataliBatch] = useState<CreatedRequestRow[] | null>(null);
    const ffPushable = useMemo(() => rows.filter(r => ffMode(r.ffId) === 'push'), [rows, ffMode]);
    const ffNatali = useMemo(() => rows.filter(r => ffMode(r.ffId) === 'natali'), [rows, ffMode]);
    const isTodo = useCallback((id: number) => {
        const c = ffState.get(id);
        return !c || (c.st !== 'done' && c.st !== 'busy');
    }, [ffState]);
    const pushFfAll = useCallback(async () => {
        setMassBusy('ff');
        try {
            const byFf = new Map<number, CreatedRequestRow[]>();
            for (const row of ffPushable) {
                if (!isTodo(row.id)) continue;
                byFf.set(row.ffId, [...(byFf.get(row.ffId) ?? []), row]);
            }
            for (const [ffId, batch] of byFf) await pushFfBatch(ffId, batch);
        } finally {
            setMassBusy(null);
        }
        const nataliTodo = ffNatali.filter(r => isTodo(r.id));
        if (nataliTodo.length > 0) setNataliBatch(nataliTodo);
    }, [ffPushable, ffNatali, isTodo, pushFfBatch]);

    const totals = useMemo(() => ({
        qty: rows.reduce((s, r) => s + r.qty, 0),
        wbDone: rows.filter(r => wbState.get(r.id)?.st === 'done').length,
        // Осталось создать заявок ФФ: bulk-провайдеры + Натали (портал).
        ffTodo: [...ffPushable, ...ffNatali].filter(r => ffState.get(r.id)?.st !== 'done').length,
        ffTodoBulk: ffPushable.filter(r => ffState.get(r.id)?.st !== 'done').length,
    }), [rows, ffPushable, ffNatali, wbState, ffState]);

    const busy = massBusy !== null;
    // Реплей кабинета WB не любит параллельные заносы — пока идёт хоть один,
    // остальные WB-кнопки заблокированы (одиночные клики тоже сериализуем).
    const wbInFlight = useMemo(() => [...wbState.values()].some(c => c.st === 'busy'), [wbState]);
    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 100, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}
            onClick={busy ? undefined : onClose}>
            {/* modal-card-solid: сплошной фон — под модалкой таблица заявок, glass просвечивал. */}
            <div className="modal-card modal-card-solid" style={{ width: 860, maxWidth: '100%', maxHeight: '85vh', overflow: 'auto', padding: 20 }} onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
                    <h3 style={{ margin: 0, fontSize: 16 }}>✅ Создано заявок: {formatNumber(rows.length, 0)}</h3>
                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{formatNumber(totals.qty, 0)} шт</span>
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={onClose} disabled={busy}>✕</button>
                </div>
                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                    Отсюда можно сразу завести WB-поставку (преордер кабинета) и заявку ФФ — по строке или для всех.
                </div>
                {wbSession && wbSession !== 'ACTIVE' && (
                    <div style={{ padding: '8px 12px', borderRadius: 12, background: 'color-mix(in srgb, var(--color-warning) 12%, transparent)', color: 'var(--color-warning)', fontSize: 12, marginBottom: 10 }}>
                        ⚠ WB-сессия {wbSession === 'EXPIRED' ? 'протухла' : 'не подключена'} — занос в WB упадёт. Подключи её в панели «Поставка WB» любой заявки и вернись.
                    </div>
                )}
                {/* Параметры заявок ФФ + массовые действия */}
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Дата забора (ФФ)</label>
                        <input className="form-input" type="date" value={collectionDate} onChange={e => setCollectionDate(e.target.value)} style={{ width: 150 }} />
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Доставка (ФФ)</label>
                        <select className="form-input" value={deliveryType} onChange={e => setDeliveryType(e.target.value as 'straight' | 'cross_dock')} style={{ width: 150 }}>
                            <option value="straight">Прямая</option>
                            <option value="cross_dock">Кросс-док</option>
                        </select>
                    </div>
                    <button className="btn btn-primary btn-sm" onClick={pushFfAll}
                        title="skladbot/Целиком — батчем; Натали — последовательно в их портал после подтверждения (необратимо)"
                        disabled={busy || totals.ffTodo === 0 || (totals.ffTodoBulk > 0 && !collectionDate)}>
                        {massBusy === 'ff' ? '⏳ Создаю ФФ…' : `📋 ФФ-заявки для всех (${formatNumber(totals.ffTodo, 0)})`}
                    </button>
                    <button className="btn btn-primary btn-sm" onClick={pushWbAll} disabled={busy || wbInFlight || wbJobRunning || totals.wbDone === rows.length}
                        title="Фоновый занос на сервере: по одной заявке с паузой ~10с — окно можно закрыть, итоги видны в WB-колонке списка">
                        {wbJobRunning || massBusy === 'wb' ? '⏳ Фоновый занос WB…' : `🟣 WB-поставки для всех (${formatNumber(rows.length - totals.wbDone, 0)})`}
                    </button>
                </div>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                    <thead>
                        <tr style={{ color: 'var(--color-text-muted)', fontSize: 11, textAlign: 'left' }}>
                            <th style={{ padding: '6px 8px' }}>Заявка</th>
                            <th style={{ padding: '6px 8px' }}>ФФ (забор)</th>
                            <th style={{ padding: '6px 8px' }}>Склад WB (сдача)</th>
                            <th style={{ padding: '6px 8px', textAlign: 'right' }}>Состав</th>
                            <th style={{ padding: '6px 8px' }}>Заявка ФФ</th>
                            <th style={{ padding: '6px 8px' }}>WB-поставка</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map(row => {
                            const wb = wbState.get(row.id);
                            const ff = ffState.get(row.id);
                            const mode = ffMode(row.ffId);
                            return (
                                <tr key={row.id} style={{ borderTop: '1px solid var(--color-border)' }}>
                                    <td style={{ padding: '7px 8px', whiteSpace: 'nowrap' }}>
                                        <a href={`/p/${slug}/warehouse/assembly/${row.id}`} target="_blank" rel="noreferrer" style={{ fontWeight: 600, color: 'var(--color-accent)' }}>{row.number}</a>
                                        <span title={PKG_LABEL[row.pkg]} style={{ marginLeft: 6, fontSize: 11 }}>{PKG_ICON[row.pkg]}</span>
                                    </td>
                                    <td style={{ padding: '7px 8px' }}>{row.ffName}</td>
                                    <td style={{ padding: '7px 8px' }}>{row.wbName || '—'}</td>
                                    <td style={{ padding: '7px 8px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                        {formatNumber(row.qty, 0)} шт · {formatNumber(row.sku, 0)} SKU
                                    </td>
                                    <td style={{ padding: '7px 8px', whiteSpace: 'nowrap' }}>
                                        {cellBadge(ff, 'Создана') ?? (() => {
                                            if (mode === 'push') return <button className="btn btn-secondary btn-sm" onClick={() => pushFfBatch(row.ffId, [row])} disabled={busy || !collectionDate}>Создать</button>;
                                            if (mode === 'natali') return <button className="btn btn-secondary btn-sm" title="Создать заявку в портале ФФ Натали — превью описи + подтверждение" onClick={() => setMigfullFor(row)} disabled={busy}>Создать (Натали)</button>;
                                            if (mode === 'portal') return <span className="badge badge-info" title="Оператор ФФ работает в нашей системе — сборка уже видна в его портале (+Telegram-уведомление). Отдельная заявка ФФ не нужна.">🖥 портал ФФ</span>;
                                            if (mode === 'provider') return <span className="badge badge-secondary" title="Провайдер этого склада не поддерживает создание заявок из DDS — создай в кабинете ФФ">⚙ вручную у ФФ</span>;
                                            if (mode === 'none') return <span className="badge badge-secondary" title="У склада нет интеграции ФФ — заявка не создаётся">без интеграции</span>;
                                            return <span style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>…</span>;
                                        })()}
                                        {(ff?.st === 'error' || ff?.st === 'warn') && (
                                            // Ретрай: Натали — через превью (там же force_resend для «уже отправлялась»), bulk — повторный пуш.
                                            <button className="btn btn-secondary btn-sm" style={{ marginLeft: 6 }} disabled={busy}
                                                onClick={() => (mode === 'natali' ? setMigfullFor(row) : pushFfBatch(row.ffId, [row]))}>↻</button>
                                        )}
                                    </td>
                                    <td style={{ padding: '7px 8px', whiteSpace: 'nowrap' }}>
                                        {cellBadge(wb, 'Заведена') ?? (
                                            <button className="btn btn-secondary btn-sm" onClick={() => pushWbOne(row)} disabled={busy || wbInFlight || wbJobRunning}>Завести</button>
                                        )}
                                        {wb?.st === 'error' && (
                                            <button className="btn btn-secondary btn-sm" style={{ marginLeft: 6 }} onClick={() => pushWbOne(row)} disabled={busy || wbInFlight || wbJobRunning}>↻</button>
                                        )}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
                <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
                    {listQuery && (
                        <button className="btn btn-secondary btn-sm" onClick={() => router.push(`/p/${slug}/warehouse/assembly?${listQuery}`)} disabled={busy}>
                            Открыть в списке →
                        </button>
                    )}
                    <button className="btn btn-primary btn-sm" style={{ marginLeft: 'auto' }} onClick={onClose} disabled={busy}>Готово</button>
                </div>
            </div>
            {/* Вложенная модалка портала Натали (превью описи + подтверждение). Успех
                помечает строку done; закрыть можно и без отправки. stopPropagation —
                иначе клик по её оверлею всплывает до нашего и закрывает обе модалки. */}
            {migfullFor && (
                <div onClick={e => e.stopPropagation()}>
                    <MigfullModal
                        source={{ kind: 'assembly', id: migfullFor.id }}
                        sourceLabel={`Сборка ${migfullFor.number}`}
                        onClose={() => setMigfullFor(null)}
                        onSuccess={() => setFf(migfullFor.id, { st: 'done', note: 'Создана (Натали)' })}
                    />
                </div>
            )}
            {/* Сводное окно батча в портал Натали: описи всех заявок → выбор → отправка. */}
            {nataliBatch && (
                <div onClick={e => e.stopPropagation()}>
                    <NataliBatchModal rows={nataliBatch} onClose={() => setNataliBatch(null)} onRowResult={setFf} />
                </div>
            )}
        </div>
    );
}

/** Драфт заявки Натали в сводном окне (или ошибка его загрузки). */
type NataliDraft = MigfullDraftResponse | { error: string };

/**
 * Сводное окно батча в портал ФФ Натали: сначала грузим описи ВСЕХ заявок
 * (с прогрессом), затем одна таблица — что едет коробами, где россыпь (и какая),
 * что уже отправлялось. Юзер собирает состав чекбоксами («только коробами» —
 * в один клик) и жмёт явную кнопку отправки; портал не даёт отменить/удалить
 * заявку, поэтому до этой кнопки наружу не уходит НИЧЕГО. Отправка
 * последовательная, прогресс и PVB-номера — в тех же строках.
 */
function NataliBatchModal({ rows, onClose, onRowResult }: {
    rows: CreatedRequestRow[];
    onClose: () => void;
    /** Зеркалим итог строки в основную таблицу модалки (ffState). */
    onRowResult: (id: number, c: CellState) => void;
}) {
    const [phase, setPhase] = useState<'loading' | 'review' | 'sending' | 'done'>('loading');
    const [loadedCount, setLoadedCount] = useState(0);
    const [drafts, setDrafts] = useState<Map<number, NataliDraft>>(new Map());
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [rowState, setRowState] = useState<Map<number, CellState>>(new Map());
    // Гард анмаунта: SPA-навигация посреди отправки прерывает цикл (неотправленные
    // остаются неотправленными — портал необратим) и глушит setState-утечки.
    const alive = useRef(true);
    useEffect(() => () => { alive.current = false; }, []);

    // Пригодна к отправке = eligible, не отправлялась, опись непуста.
    const sendable = useCallback((d: NataliDraft | undefined): d is MigfullDraftResponse =>
        !!d && !('error' in d) && d.eligible && !d.already_sent && d.opis_lines.length > 0, []);
    // Россыпь: есть не-коробные строки описи или предупреждения конвертации.
    const hasLoose = useCallback((d: MigfullDraftResponse) =>
        d.warnings.length > 0 || d.opis_lines.some(l => !l.is_box), []);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            const acc = new Map<number, NataliDraft>();
            for (const row of rows) {
                if (cancelled) return;
                try {
                    acc.set(row.id, await api.migfullPortalDraft({ kind: 'assembly', id: row.id }));
                } catch (e: unknown) {
                    acc.set(row.id, { error: e instanceof Error ? e.message : 'Ошибка загрузки описи' });
                }
                if (!cancelled) { setLoadedCount(acc.size); setDrafts(new Map(acc)); }
            }
            if (cancelled) return;
            setSelected(new Set(rows.filter(r => sendable(acc.get(r.id))).map(r => r.id)));
            setPhase('review');
        })();
        return () => { cancelled = true; };
    }, [rows, sendable]);

    const toggle = useCallback((id: number) => {
        setSelected(prev => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });
    }, []);
    const selectCleanOnly = useCallback(() => {
        setSelected(new Set(rows.filter(r => { const d = drafts.get(r.id); return sendable(d) && !hasLoose(d); }).map(r => r.id)));
    }, [rows, drafts, sendable, hasLoose]);

    const totals = useMemo(() => {
        let boxes = 0, pieces = 0;
        for (const r of rows) {
            if (!selected.has(r.id)) continue;
            const d = drafts.get(r.id);
            if (!sendable(d)) continue;
            boxes += d.total_boxes;
            pieces += d.total_pieces;
        }
        return { boxes, pieces };
    }, [rows, selected, drafts, sendable]);

    const doSend = useCallback(async () => {
        setPhase('sending');
        for (const row of rows) {
            if (!alive.current) return; // страница ушла — остаток батча не шлём
            if (!selected.has(row.id)) continue;
            const d = drafts.get(row.id);
            if (!sendable(d)) continue;
            setRowState(prev => new Map(prev).set(row.id, { st: 'busy' }));
            const res = await sendNataliFromDraft(row.id, d);
            if (!alive.current) return;
            setRowState(prev => new Map(prev).set(row.id, res));
            onRowResult(row.id, res);
        }
        if (alive.current) setPhase('done');
    }, [rows, selected, drafts, sendable, onRowResult]);

    const busySend = phase === 'sending';
    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 110, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}
            onClick={busySend ? undefined : onClose}>
            <div className="modal-card modal-card-solid" style={{ width: 720, maxWidth: '100%', maxHeight: '85vh', overflow: 'auto', padding: 20 }} onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
                    <h3 style={{ margin: 0, fontSize: 16 }}>📋 Отправка в портал ФФ Натали</h3>
                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{formatNumber(rows.length, 0)} заявок</span>
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={onClose} disabled={busySend}>✕</button>
                </div>
                <div style={{ padding: '8px 12px', borderRadius: 12, background: 'color-mix(in srgb, var(--color-warning) 12%, transparent)', color: 'var(--color-warning)', fontSize: 12, marginBottom: 10 }}>
                    ⚠ Отправка необратима: отменить или удалить заявку в портале Натали нельзя. До кнопки «Отправить» наружу ничего не уходит.
                </div>
                {phase === 'loading' ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>
                        ⏳ Готовлю описи… {formatNumber(loadedCount, 0)} / {formatNumber(rows.length, 0)}
                    </div>
                ) : (
                    <>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                            <thead>
                                <tr style={{ color: 'var(--color-text-muted)', fontSize: 11, textAlign: 'left' }}>
                                    <th style={{ padding: '6px 8px' }} />
                                    <th style={{ padding: '6px 8px' }}>Заявка</th>
                                    <th style={{ padding: '6px 8px' }}>Склад WB</th>
                                    <th style={{ padding: '6px 8px', textAlign: 'right' }}>Опись</th>
                                    <th style={{ padding: '6px 8px' }}>Статус</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map(row => {
                                    const d = drafts.get(row.id);
                                    const st = rowState.get(row.id);
                                    const canSend = sendable(d);
                                    const loose = canSend ? d.opis_lines.filter(l => !l.is_box) : [];
                                    const boxes = canSend ? d.opis_lines.filter(l => l.is_box).reduce((s, l) => s + l.quantity, 0) : 0;
                                    const loosePieces = loose.reduce((s, l) => s + l.quantity, 0);
                                    return (
                                        <tr key={row.id} style={{ borderTop: '1px solid var(--color-border)', opacity: canSend || st ? 1 : 0.6 }}>
                                            <td style={{ padding: '7px 8px' }}>
                                                <input type="checkbox" checked={selected.has(row.id)} disabled={!canSend || phase !== 'review'}
                                                    onChange={() => toggle(row.id)} aria-label={`Включить ${row.number}`} />
                                            </td>
                                            <td style={{ padding: '7px 8px', whiteSpace: 'nowrap', fontWeight: 600 }}>{row.number}</td>
                                            <td style={{ padding: '7px 8px' }}>{row.wbName || '—'}</td>
                                            <td style={{ padding: '7px 8px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                {canSend ? (
                                                    <>
                                                        {boxes > 0 && <span>{formatNumber(boxes, 0)} кор</span>}
                                                        {loosePieces > 0 && (
                                                            <span style={{ color: 'var(--color-warning)' }} title={loose.map(l => `${l.name || l.barcode}: ${formatNumber(l.quantity, 0)} шт`).join('\n')}>
                                                                {boxes > 0 ? ' + ' : ''}{formatNumber(loosePieces, 0)} шт россыпью
                                                            </span>
                                                        )}
                                                    </>
                                                ) : '—'}
                                            </td>
                                            <td style={{ padding: '7px 8px', whiteSpace: 'nowrap' }}>
                                                {st ? cellBadge(st, 'Создана')
                                                    : !d || 'error' in d ? <span className="badge badge-danger" title={d && 'error' in d ? d.error : undefined}>ошибка описи</span>
                                                    : d.already_sent ? <span className="badge badge-secondary" title="Повтор — по строке через превью">уже отправлялась{d.sent_number ? ` (${d.sent_number})` : ''}</span>
                                                    : !d.eligible ? <span className="badge badge-secondary">недоступна</span>
                                                    : d.opis_lines.length === 0 ? <span className="badge badge-secondary">пустая опись</span>
                                                    : hasLoose(d) ? <span className="badge badge-warning" title={d.warnings.join('\n') || 'Есть позиции россыпью'}>⚠ россыпь</span>
                                                    : <span className="badge badge-success">коробами</span>}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 14, flexWrap: 'wrap' }}>
                            {phase === 'review' && (
                                <>
                                    <button className="btn btn-secondary btn-sm" onClick={selectCleanOnly} title="Оставить в батче только заявки, где вся опись целыми коробами">✓ Только коробами</button>
                                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                        Выбрано: {formatNumber(selected.size, 0)} · {formatNumber(totals.boxes, 0)} кор · {formatNumber(totals.pieces, 0)} шт
                                    </span>
                                    <span style={{ marginLeft: 'auto', display: 'flex', gap: 10 }}>
                                        <button className="btn btn-secondary btn-sm" onClick={onClose}>Отмена</button>
                                        <button className="btn btn-primary btn-sm" onClick={doSend} disabled={selected.size === 0}>
                                            Отправить выбранные ({formatNumber(selected.size, 0)})
                                        </button>
                                    </span>
                                </>
                            )}
                            {phase === 'sending' && <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>⏳ Отправляю по одной — не закрывай окно…</span>}
                            {phase === 'done' && (
                                <button className="btn btn-primary btn-sm" style={{ marginLeft: 'auto' }} onClick={onClose}>Готово</button>
                            )}
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
