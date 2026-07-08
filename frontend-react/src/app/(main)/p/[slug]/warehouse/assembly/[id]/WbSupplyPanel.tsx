'use client';
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type {
    AssemblyRequestItem,
    WbBox,
    WbDriver,
    WbPortalStatus,
    WbSupplyState,
    WbSupplySyncStatus,
} from '@/types/api';

const STATUS_LABEL: Record<WbSupplySyncStatus, { label: string; className: string }> = {
    NONE: { label: 'Не заведена', className: 'badge-secondary' },
    DRAFT: { label: 'Черновик', className: 'badge-info' },
    PREORDER: { label: 'Преордер — нужна бронь даты', className: 'badge-warning' },
    BOOKED: { label: 'Дата забронирована', className: 'badge-info' },
    BOXED: { label: 'Короба занесены', className: 'badge-info' },
    PASSED: { label: 'Пропуск занесён', className: 'badge-success' },
    ERROR: { label: 'Ошибка', className: 'badge-danger' },
};

type Tab = 'goods' | 'boxes' | 'pass';

interface Props {
    assemblyId: number;
    items: AssemblyRequestItem[];
}

export default function WbSupplyPanel({ assemblyId, items }: Props) {
    const [state, setState] = useState<WbSupplyState | null>(null);
    const [session, setSession] = useState<WbPortalStatus | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState('');
    const [tab, setTab] = useState<Tab>('goods');

    // Session token input
    const [tokenInput, setTokenInput] = useState('');

    // Pass form
    const [driverFirst, setDriverFirst] = useState('');
    const [driverLast, setDriverLast] = useState('');
    const [driverPhone, setDriverPhone] = useState('');
    const [carModel, setCarModel] = useState('');
    const [carNumber, setCarNumber] = useState('');
    const [pallets, setPallets] = useState<number | ''>('');
    const [drivers, setDrivers] = useState<WbDriver[]>([]);

    // Box editor (local)
    const [boxes, setBoxes] = useState<WbBox[]>([]);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [st, ses] = await Promise.all([
                api.wbSupplyGetState(assemblyId),
                api.wbSessionStatus(),
            ]);
            setState(st);
            setSession(ses);
            setBoxes(st.boxes ?? []);
            setDriverFirst(st.pass_driver_first ?? '');
            setDriverLast(st.pass_driver_last ?? '');
            setDriverPhone(st.pass_driver_phone ?? '');
            setCarModel(st.pass_car_model ?? '');
            setCarNumber(st.pass_car_number ?? '');
            setPallets(st.pass_pallets ?? '');
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [assemblyId]);

    useEffect(() => {
        load();
    }, [load]);

    const run = useCallback(
        async (key: string, fn: () => Promise<WbSupplyState>) => {
            setBusy(key);
            setError('');
            try {
                const st = await fn();
                setState(st);
                setBoxes(st.boxes ?? []);
            } catch (e: unknown) {
                setError(e instanceof Error ? e.message : 'Ошибка');
            } finally {
                setBusy('');
            }
        },
        [],
    );

    const saveSession = useCallback(async () => {
        if (!tokenInput.trim()) return;
        setBusy('session');
        setError('');
        try {
            const ses = await api.wbSessionSet(tokenInput.trim());
            setSession(ses);
            setTokenInput('');
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Токен не принят');
        } finally {
            setBusy('');
        }
    }, [tokenInput]);

    const loadDrivers = useCallback(async () => {
        try {
            setDrivers(await api.wbSupplyDrivers(assemblyId));
        } catch {
            /* автозаполнение опционально */
        }
    }, [assemblyId]);

    // Авто-раскладка: каждый баркод — в свой короб (как в кабинете WB).
    const autoLayout = useCallback(() => {
        setBoxes(items.map((it) => ({ boxcode: null, items: [{ barcode: it.barcode, quantity: it.quantity }] })));
    }, [items]);

    if (loading) return <div className="glass-card">Загрузка поставки WB…</div>;

    const st = state;
    const sessionActive = session?.status === 'ACTIVE';
    const statusCfg = st ? STATUS_LABEL[st.sync_status] : STATUS_LABEL.NONE;

    return (
        <div className="glass-card animate-in" style={{ marginTop: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                <h3 style={{ margin: 0 }}>Поставка WB</h3>
                {st && <span className={`badge ${statusCfg.className}`}>{statusCfg.label}</span>}
            </div>

            {error && <div className="badge badge-danger" style={{ marginBottom: 12 }}>{error}</div>}
            {st?.last_error && st.sync_status === 'ERROR' && (
                <div className="badge badge-danger" style={{ marginBottom: 12 }}>{st.last_error}</div>
            )}

            {/* Сессия кабинета */}
            {!sessionActive && (
                <div className="badge badge-warning" style={{ display: 'block', marginBottom: 12, padding: 12 }}>
                    {session?.status === 'EXPIRED'
                        ? 'Доступ к кабинету WB истёк. Вставьте свежий authorizev3.'
                        : 'Доступ к кабинету WB не задан. Вставьте authorizev3 (из DevTools кабинета).'}
                    <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                        <input
                            className="input"
                            style={{ flex: 1 }}
                            placeholder="authorizev3 …"
                            value={tokenInput}
                            onChange={(e) => setTokenInput(e.target.value)}
                        />
                        <button className="btn btn-primary btn-sm" disabled={busy === 'session'} onClick={saveSession}>
                            {busy === 'session' ? '…' : 'Сохранить'}
                        </button>
                    </div>
                </div>
            )}

            {/* Вкладки */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 16, borderBottom: '1px solid var(--color-border)' }}>
                {([['goods', 'Наполнение'], ['boxes', 'Упаковка и печать ШК'], ['pass', 'Пропуск']] as [Tab, string][]).map(
                    ([key, label]) => (
                        <button
                            key={key}
                            className={tab === key ? 'btn btn-primary btn-sm' : 'btn btn-secondary btn-sm'}
                            onClick={() => {
                                setTab(key);
                                if (key === 'pass') loadDrivers();
                            }}
                        >
                            {label}
                        </button>
                    ),
                )}
            </div>

            {/* ── Наполнение ── */}
            {tab === 'goods' && (
                <div>
                    {items.length === 0 ? (
                        <div className="text-muted">В заявке нет товаров</div>
                    ) : (
                        <table style={{ width: '100%', marginBottom: 12 }}>
                            <thead>
                                <tr>
                                    <th style={{ textAlign: 'left' }}>Баркод</th>
                                    <th style={{ textAlign: 'right' }}>Кол-во</th>
                                </tr>
                            </thead>
                            <tbody>
                                {items.map((it) => (
                                    <tr key={it.id}>
                                        <td>{it.barcode}</td>
                                        <td style={{ textAlign: 'right' }}>{formatNumber(it.quantity, 0)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <button
                            className="btn btn-primary btn-sm"
                            disabled={!sessionActive || busy === 'preorder' || items.length === 0}
                            onClick={() => run('preorder', () => api.wbSupplyCreatePreorder(assemblyId))}
                        >
                            {busy === 'preorder' ? 'Создаю…' : 'Создать преордер в WB'}
                        </button>
                        {st?.preorder_id && (
                            <button
                                className="btn btn-secondary btn-sm"
                                disabled={busy === 'sync'}
                                onClick={() => run('sync', () => api.wbSupplySyncSupply(assemblyId))}
                            >
                                {busy === 'sync' ? '…' : 'Синхронизировать бронь'}
                            </button>
                        )}
                    </div>
                    {st?.preorder_id && !st.supply_id && (
                        <div className="text-muted" style={{ marginTop: 8, fontSize: 13 }}>
                            Преордер {formatNumber(st.preorder_id, 0)} создан. Подтвердите дату в кабинете WB, затем
                            нажмите «Синхронизировать бронь».
                        </div>
                    )}
                </div>
            )}

            {/* ── Упаковка ── */}
            {tab === 'boxes' && (
                <div>
                    <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                        <button className="btn btn-secondary btn-sm" onClick={autoLayout}>
                            Авто-раскладка (баркод = короб)
                        </button>
                        <button
                            className="btn btn-secondary btn-sm"
                            disabled={busy === 'saveboxes' || boxes.length === 0}
                            onClick={() => run('saveboxes', () => api.wbSupplySaveBoxes(assemblyId, { boxes }))}
                        >
                            {busy === 'saveboxes' ? '…' : 'Сохранить раскладку'}
                        </button>
                    </div>
                    {boxes.length === 0 ? (
                        <div className="text-muted">Раскладка коробов не задана — нажмите «Авто-раскладка»</div>
                    ) : (
                        <table style={{ width: '100%', marginBottom: 12 }}>
                            <thead>
                                <tr>
                                    <th style={{ textAlign: 'left' }}>Короб</th>
                                    <th style={{ textAlign: 'left' }}>Содержимое</th>
                                </tr>
                            </thead>
                            <tbody>
                                {boxes.map((b, i) => (
                                    <tr key={b.boxcode ?? i}>
                                        <td>{b.boxcode ?? `№${i + 1} (новый)`}</td>
                                        <td>
                                            {b.items
                                                .map((it) => `${it.barcode} × ${formatNumber(it.quantity, 0)}`)
                                                .join(', ')}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                    <button
                        className="btn btn-primary btn-sm"
                        disabled={!sessionActive || busy === 'pushboxes' || !st?.supply_id || boxes.length === 0}
                        onClick={() => run('pushboxes', () => api.wbSupplyPushBoxes(assemblyId))}
                    >
                        {busy === 'pushboxes' ? 'Заношу…' : 'Занести короба в WB'}
                    </button>
                    {!st?.supply_id && (
                        <div className="text-muted" style={{ marginTop: 8, fontSize: 13 }}>
                            Сначала забронируйте дату в кабинете WB и нажмите «Синхронизировать бронь».
                        </div>
                    )}
                </div>
            )}

            {/* ── Пропуск ── */}
            {tab === 'pass' && (
                <div style={{ display: 'grid', gap: 12, maxWidth: 520 }}>
                    <datalist id="wb-drivers">
                        {drivers.map((d, i) => (
                            <option key={i} value={`${d.firstName} ${d.lastName}`} />
                        ))}
                    </datalist>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                        <label>
                            Имя
                            <input className="input" list="wb-drivers" value={driverFirst} onChange={(e) => setDriverFirst(e.target.value)} />
                        </label>
                        <label>
                            Фамилия
                            <input className="input" value={driverLast} onChange={(e) => setDriverLast(e.target.value)} />
                        </label>
                        <label>
                            Телефон
                            <input className="input" value={driverPhone} onChange={(e) => setDriverPhone(e.target.value)} />
                        </label>
                        <label>
                            Кол-во паллет
                            <input
                                className="input"
                                type="number"
                                value={pallets}
                                onChange={(e) => setPallets(e.target.value === '' ? '' : Number(e.target.value))}
                            />
                        </label>
                        <label>
                            Марка авто
                            <input className="input" value={carModel} onChange={(e) => setCarModel(e.target.value)} />
                        </label>
                        <label>
                            Госномер
                            <input className="input" value={carNumber} onChange={(e) => setCarNumber(e.target.value)} />
                        </label>
                    </div>
                    <div style={{ display: 'flex', gap: 8 }}>
                        <button
                            className="btn btn-secondary btn-sm"
                            disabled={busy === 'savepass'}
                            onClick={() =>
                                run('savepass', () =>
                                    api.wbSupplySavePass(assemblyId, {
                                        driver_first: driverFirst,
                                        driver_last: driverLast,
                                        driver_phone: driverPhone,
                                        car_model: carModel,
                                        car_number: carNumber,
                                        pallets: pallets === '' ? null : pallets,
                                    }),
                                )
                            }
                        >
                            {busy === 'savepass' ? '…' : 'Сохранить'}
                        </button>
                        <button
                            className="btn btn-primary btn-sm"
                            disabled={!sessionActive || busy === 'pushpass' || !st?.supply_id}
                            onClick={() => run('pushpass', () => api.wbSupplyPushPass(assemblyId))}
                        >
                            {busy === 'pushpass' ? 'Заношу…' : 'Занести пропуск в WB'}
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}
