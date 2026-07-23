'use client';
import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { downloadBoxStickers, downloadPassSticker } from '@/lib/wbBoxStickers';
import type {
    AssemblyRequestItem,
    PackageType,
    WbBox,
    WbCabinetBoxes,
    WbCabinetPass,
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

// WB boxTypeID → человекочитаемый тип (для показа созданного преордера).
// Прямая ссылка на карточку поставки в кабинете WB (номер преордера/поставки).
const wbSupplyUrl = (preorderId: number, supplyId?: number | null) => {
    const p = new URLSearchParams({ preorderId: String(preorderId), supplyId: supplyId ? String(supplyId) : '' });
    return `https://seller.wildberries.ru/supplies-management/all-supplies/supply-detail?${p.toString()}`;
};

const PKG_OPTIONS: [PackageType, string][] = [
    ['BOX', 'Короб'],
    ['MONOPALLET', 'Монопаллета'],
    ['SUPERSAFE', 'Суперсейф'],
];

// Русские госномера: кабинет WB хранит латиницей (B331YT69), логист вводит
// кириллицей (В331УТ69) — это ОДИН номер. Плюс `vehicle_info` заявки часто несёт
// ещё и ФИО водителя. Поэтому сравниваем нормализованные номера (гомоглифы
// кириллица→латиница, только буквы/цифры), а сам номер вынимаем из строки заявки.
const PLATE_HOMOGLYPH: Record<string, string> = {
    А: 'A', В: 'B', Е: 'E', К: 'K', М: 'M', Н: 'H',
    О: 'O', Р: 'P', С: 'C', Т: 'T', У: 'Y', Х: 'X',
};
const PLATE_RE = /[АВЕКМНОРСТУХABEKMHOPCTYX]\d{3}[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\d{2,3}/i;
const normalizePlate = (s: string): string =>
    s.toUpperCase().split('').map((c) => PLATE_HOMOGLYPH[c] ?? c).join('').replace(/[^A-Z0-9]/g, '');
const extractPlate = (s: string): string => s.match(PLATE_RE)?.[0] ?? s.trim();
const onlyDigits = (s: string): string => s.replace(/\D/g, '');
/** Телефон РФ к канону 79XXXXXXXXX (8→7, +7→7) — как нормализует бэк для WB.
 *  Иначе сравнение пропуск↔машина по сырым цифрам даёт ложное «79…» ≠ «89…». */
const normalizeRuPhone = (s: string): string => {
    const d = onlyDigits(s);
    if (d.length === 11 && d[0] === '8') return `7${d.slice(1)}`;
    if (d.length === 10) return `7${d}`;
    return d;
};

/** Звёздочка «поле обязательно» — WB не примет пропуск без всех полей. */
const Req = () => <span style={{ color: 'var(--color-danger)', marginLeft: 2 }}>*</span>;

/** Красная рамка у незаполненного обязательного поля. */
const emptyStyle = (isEmpty: boolean) =>
    isEmpty ? { borderColor: 'var(--color-danger)' } : undefined;

interface Props {
    assemblyId: number;
    items: AssemblyRequestItem[];
    defaultPackageType?: PackageType;
    /** Сообщить родителю о новом состоянии поставки (шапка вкладки, баннеры
     *  страницы читают устаревший снимок и без этого не обновляются до F5). */
    onStateChange?: (state: WbSupplyState) => void;
}

export default function WbSupplyPanel({ assemblyId, items, defaultPackageType, onStateChange }: Props) {
    const [state, setState] = useState<WbSupplyState | null>(null);
    const [session, setSession] = useState<WbPortalStatus | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState('');
    const [tab, setTab] = useState<Tab>('goods');

    // Session token input
    const [tokenInput, setTokenInput] = useState('');

    // Тип упаковки для преордера — по умолчанию тип заявки (с экрана «Распределить»).
    const [pkg, setPkg] = useState<PackageType>(defaultPackageType ?? 'BOX');

    // Pass form
    const [driverFirst, setDriverFirst] = useState('');
    const [driverLast, setDriverLast] = useState('');
    const [driverPhone, setDriverPhone] = useState('');
    const [carModel, setCarModel] = useState('');
    const [carNumber, setCarNumber] = useState('');
    const [pallets, setPallets] = useState<number | ''>('');
    const [passAsBoxes, setPassAsBoxes] = useState(false);
    const [drivers, setDrivers] = useState<WbDriver[]>([]);

    // Box editor (local)
    const [boxes, setBoxes] = useState<WbBox[]>([]);
    // Короба из кабинета WB (read-only, с содержимым) + раскрытый короб.
    const [cabinetBoxes, setCabinetBoxes] = useState<WbCabinetBoxes | null>(null);
    const [cabinetLoading, setCabinetLoading] = useState(false);
    const [expandedBox, setExpandedBox] = useState<string | null>(null);
    // Существующий пропуск из кабинета WB (когда завели прямо там).
    const [cabinetPass, setCabinetPass] = useState<WbCabinetPass | null>(null);

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
            // Способ отгрузки: сохранённый в пропуске, иначе дефолт из единицы заявки.
            setPassAsBoxes(st.pass_as_boxes ?? st.assembly_shipped_as_boxes ?? false);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [assemblyId]);

    useEffect(() => {
        load();
    }, [load]);

    // Пробрасываем актуальное состояние наверх при любом его изменении (загрузка,
    // синк брони, занос коробов/пропуска) — родитель обновляет шапку вкладки и
    // баннеры. `onStateChange` (setWbState) стабилен, цикла нет.
    useEffect(() => {
        if (state) onStateChange?.(state);
    }, [state, onStateChange]);

    const run = useCallback(
        async (key: string, fn: () => Promise<WbSupplyState>) => {
            setBusy(key);
            setError('');
            try {
                const st = await fn();
                // Ответ мутации НЕ несёт read-only зеркала машины заявки
                // (`assembly_*` домешивается только в get_state) — сохраняем
                // прежние значения, иначе префилл пропуска и подсветка
                // расхождений пропадут до перезагрузки. Остальные поля —
                // реальные колонки, берём из свежего ответа.
                setState((prev) => (prev ? {
                    ...st,
                    assembly_vehicle_info: st.assembly_vehicle_info ?? prev.assembly_vehicle_info,
                    assembly_vehicle_brand: st.assembly_vehicle_brand ?? prev.assembly_vehicle_brand,
                    assembly_driver_phone: st.assembly_driver_phone ?? prev.assembly_driver_phone,
                    assembly_pallets_count: st.assembly_pallets_count ?? prev.assembly_pallets_count,
                } : st));
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

    // Короба из кабинета WB (с содержимым) — для вкладки «Упаковка».
    const loadCabinetBoxes = useCallback(async () => {
        if (!state?.supply_id) {
            setCabinetBoxes(null);
            return;
        }
        setCabinetLoading(true);
        try {
            setCabinetBoxes(await api.wbSupplyCabinetBoxes(assemblyId));
        } catch {
            setCabinetBoxes(null);
        } finally {
            setCabinetLoading(false);
        }
    }, [assemblyId, state?.supply_id]);

    // Существующий пропуск из кабинета WB — для вкладки «Пропуск». Если поля
    // локально пусты, а в кабинете пропуск уже заведён — подставляем его.
    const loadCabinetPass = useCallback(async () => {
        if (!state?.supply_id) {
            setCabinetPass(null);
            return;
        }
        try {
            const p = await api.wbSupplyCabinetPass(assemblyId);
            setCabinetPass(p);
            if (p.has_pass) {
                setDriverFirst((v) => v || p.driver_first || '');
                setDriverLast((v) => v || p.driver_last || '');
                setDriverPhone((v) => v || p.driver_phone || '');
                setCarModel((v) => v || p.car_model || '');
                setCarNumber((v) => v || p.car_number || '');
                setPallets((v) => (v === '' && p.pallets != null ? p.pallets : v));
            }
        } catch {
            setCabinetPass(null);
        }
    }, [assemblyId, state?.supply_id]);

    // Авто-раскладка: каждый баркод — в свой короб (как в кабинете WB).
    const autoLayout = useCallback(() => {
        setBoxes(items.map((it) => ({ boxcode: null, items: [{ barcode: it.barcode, quantity: it.quantity }] })));
    }, [items]);

    // Скачать PDF со стикерами коробов (QR + Code128) — целиком на клиенте.
    const downloadStickers = useCallback(async () => {
        const codes = (state?.boxes ?? [])
            .map((b) => b.boxcode)
            .filter((c): c is string => !!c);
        if (codes.length === 0) return;
        setBusy('stickers');
        setError('');
        try {
            await downloadBoxStickers(codes, `ШК_короба_${assemblyId}.pdf`);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Не удалось сгенерировать ШК коробов');
        } finally {
            setBusy('');
        }
    }, [state, assemblyId]);

    // ШК пропуска: у поставки WB нет собственного штрихкода — на складе сканируют
    // ШК ПРОПУСКА (`WB-GI-<barcode_id>`) и ШК коробов. barcode_id приходит из
    // кабинета (trn_details) либо проставляется при заносе пропуска.
    const passBarcode = state?.barcode_id != null
        ? `WB-GI-${state.barcode_id}`
        : (cabinetPass?.barcode_id != null
            ? `${cabinetPass.barcode_prefix ?? 'WB-GI-'}${cabinetPass.barcode_id}`
            : null);

    const downloadPass = useCallback(async () => {
        if (!passBarcode) return;
        setBusy('pass-sticker');
        setError('');
        try {
            await downloadPassSticker({ passBarcode, fileName: `ШК_пропуск_${assemblyId}.pdf` });
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Не удалось сгенерировать ШК пропуска');
        } finally {
            setBusy('');
        }
    }, [passBarcode, assemblyId]);

    // Префилл полей пропуска из назначенной машины заявки (только непустые значения).
    const fillFromVehicle = useCallback(() => {
        // Из строки машины заявки вынимаем сам госномер (в ней часто ещё ФИО).
        if (state?.assembly_vehicle_info) setCarNumber(extractPlate(state.assembly_vehicle_info));
        if (state?.assembly_vehicle_brand) setCarModel(state.assembly_vehicle_brand);
        if (state?.assembly_driver_phone) setDriverPhone(state.assembly_driver_phone);
    }, [state]);

    if (loading) return <div className="glass-card">Загрузка поставки WB…</div>;

    const st = state;
    // Значения назначенной машины заявки — для префилла и подсветки расхождений пропуска.
    const vehInfo = st?.assembly_vehicle_info ?? '';
    const vehBrand = st?.assembly_vehicle_brand ?? '';
    const vehPhone = st?.assembly_driver_phone ?? '';
    const hasVehicleData = !!(vehInfo || vehBrand || vehPhone);
    // Расхождение поля пропуска с машиной заявки: оба непустые и различаются.
    const mismatch = (field: string, veh: string) =>
        !!field.trim() && !!veh.trim() && field.trim().toLowerCase() !== veh.trim().toLowerCase();
    // Госномер: сравниваем нормализованные (кириллица→латиница), номер вынимаем из
    // строки заявки — иначе «B331YT69» ≠ «В331УТ69 Иванов» давало ложное расхождение.
    const carNumberMismatch =
        !!carNumber.trim() && !!vehInfo.trim()
        && normalizePlate(carNumber) !== normalizePlate(extractPlate(vehInfo));
    const carModelMismatch = mismatch(carModel, vehBrand);
    // Телефон — по нормализованному канону (8/+7 → 7), иначе нормализованный
    // пропуск «79…» ложно расходится с сырым «8-…» из машины заявки.
    const driverPhoneMismatch =
        !!driverPhone.trim() && !!vehPhone.trim()
        && normalizeRuPhone(driverPhone) !== normalizeRuPhone(vehPhone);
    // Паллеты пропуска ≠ паллетам заявки (F2): оба заданы и различаются.
    const asmPallets = st?.assembly_pallets_count ?? null;
    const palletsMismatch = pallets !== '' && asmPallets != null && Number(pallets) !== asmPallets;
    // Пропуск: WB требует ВСЕ поля — иначе setTRNDetails не примет. Считаем, чего
    // не хватает, чтобы подсветить поля и объяснить, почему занос заблокирован.
    const missingPass = [
        !driverFirst.trim() && 'Имя',
        !driverLast.trim() && 'Фамилия',
        !driverPhone.trim() && 'Телефон',
        (pallets === '' || Number(pallets) <= 0) && (passAsBoxes ? 'Кол-во коробов' : 'Кол-во паллет'),
        !carModel.trim() && 'Марка авто',
        !carNumber.trim() && 'Госномер',
    ].filter((v): v is string => typeof v === 'string');
    const passComplete = missingPass.length === 0;
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

            {/* Ошибки поставки из кабинета WB (supplyDetails.rejectReason): «Не заполнены
                ШК коробов…», «Не заполнен пропуск…». Показываем как в кабинете —
                списком строк. WB присылает их одним текстом с переводами строк. */}
            {st?.reject_reason && (
                <div
                    className="glass-card"
                    style={{
                        padding: '12px 14px', marginBottom: 12,
                        borderLeft: '3px solid var(--color-danger)', fontSize: 13,
                    }}
                >
                    <div style={{ fontWeight: 600, marginBottom: 6, color: 'var(--color-danger)' }}>
                        Исправьте ошибки
                    </div>
                    {st.reject_reason.split('\n').map((line, i) => (
                        line.trim() ? (
                            <div key={i} className="text-muted" style={{ marginTop: 2 }}>{line.trim()}</div>
                        ) : null
                    ))}
                </div>
            )}

            {/* Сессия кабинета */}
            {!sessionActive && (
                <div className="badge badge-warning" style={{ display: 'block', marginBottom: 12, padding: 12 }}>
                    {session?.status === 'EXPIRED'
                        ? 'Доступ к кабинету WB истёк. Вставьте свежий authorizev3.'
                        : 'Доступ к кабинету WB не задан. Вставьте authorizev3 (из DevTools кабинета).'}
                    <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                        <input
                            className="form-input"
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
                                if (key === 'pass') { loadDrivers(); loadCabinetPass(); }
                                if (key === 'boxes') loadCabinetBoxes();
                            }}
                        >
                            {label}
                        </button>
                    ),
                )}
            </div>

            {/* ── Наполнение ── */}
            {tab === 'goods' && (
                <div style={{ display: 'grid', gap: 16 }}>
                    <section>
                        <div className="section-title">Состав заявки</div>
                        {items.length === 0 ? (
                            <div className="text-muted">В заявке нет товаров</div>
                        ) : (
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>Баркод</th>
                                        <th>Артикул</th>
                                        <th style={{ textAlign: 'right' }}>Кол-во</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {items.map((it) => (
                                        <tr key={it.id}>
                                            <td>{it.barcode}</td>
                                            <td style={{ fontFamily: 'monospace', fontSize: 13 }}>{it.article || '—'}</td>
                                            <td style={{ textAlign: 'right' }}>{formatNumber(it.quantity, 0)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </section>
                    <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
                        <div className="form-group" style={{ minWidth: 200 }}>
                            <label className="form-label">Тип упаковки</label>
                            <select
                                className="form-input"
                                value={pkg}
                                disabled={busy === 'preorder' || !!st?.preorder_id}
                                onChange={(e) => setPkg(e.target.value as PackageType)}
                            >
                                {PKG_OPTIONS.map(([value, label]) => (
                                    <option key={value} value={value}>{label}</option>
                                ))}
                            </select>
                        </div>
                        <button
                            className="btn btn-primary btn-sm"
                            disabled={!sessionActive || busy === 'preorder' || items.length === 0 || !!st?.preorder_id}
                            onClick={() => run('preorder', () => api.wbSupplyCreatePreorder(assemblyId, pkg))}
                        >
                            {busy === 'preorder' ? 'Создаю…' : 'Создать преордер в WB'}
                        </button>
                        {st?.preorder_id && (
                            <button
                                className="btn btn-secondary btn-sm"
                                disabled={!sessionActive || busy === 'pushgoods'}
                                onClick={() => run('pushgoods', () => api.wbSupplyPushGoods(assemblyId))}
                            >
                                {busy === 'pushgoods' ? 'Обновляю…' : 'Обновить наполнение в WB'}
                            </button>
                        )}
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
                            <a
                                href={wbSupplyUrl(st.preorder_id, st.supply_id)}
                                target="_blank"
                                rel="noopener noreferrer"
                                style={{ color: 'var(--color-accent)' }}
                            >
                                Преордер {st.preorder_id}
                            </a>{' '}
                            создан. Подтвердите дату в кабинете WB, затем нажмите «Синхронизировать бронь».
                        </div>
                    )}
                </div>
            )}

            {/* ── Упаковка ── */}
            {tab === 'boxes' && (
                <div style={{ display: 'grid', gap: 16 }}>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
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
                        <button
                            className="btn btn-secondary btn-sm"
                            disabled={busy === 'stickers' || !(st?.boxes ?? []).some((b) => b.boxcode)}
                            title={
                                (st?.boxes ?? []).some((b) => b.boxcode)
                                    ? 'Скачать PDF со стикерами коробов (QR + ШК)'
                                    : 'Сначала занесите короба в WB'
                            }
                            onClick={downloadStickers}
                        >
                            {busy === 'stickers' ? 'Готовлю…' : '📄 Скачать ШК коробов'}
                        </button>
                    </div>

                    {/* Короба из кабинета WB (как в «Упаковке» кабинета): список с раскрытием. */}
                    {st?.supply_id && (
                        <section>
                            <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <span>Короба в кабинете</span>
                                <button
                                    className="btn btn-secondary btn-sm"
                                    onClick={loadCabinetBoxes}
                                    disabled={cabinetLoading}
                                    style={{ marginLeft: 'auto' }}
                                >
                                    {cabinetLoading ? 'Загрузка…' : 'Обновить'}
                                </button>
                            </div>
                            {cabinetBoxes && cabinetBoxes.total_boxes > 0 ? (
                                <>
                                    <div className="text-muted" style={{ marginBottom: 8, fontSize: 13 }}>
                                        {formatNumber(cabinetBoxes.total_boxes, 0)} коробов ·{' '}
                                        {formatNumber(cabinetBoxes.total_barcodes, 0)} баркодов ·{' '}
                                        {formatNumber(cabinetBoxes.total_units, 0)} шт
                                    </div>
                                    <div style={{ display: 'grid', gap: 6 }}>
                                        {cabinetBoxes.boxes.map((b) => {
                                            const units = b.items.reduce((s, it) => s + it.quantity, 0);
                                            const open = expandedBox === b.boxcode;
                                            return (
                                                <div key={b.boxcode} className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                                                    <button
                                                        onClick={() => setExpandedBox(open ? null : b.boxcode)}
                                                        style={{
                                                            width: '100%', display: 'flex', alignItems: 'center', gap: 8,
                                                            padding: '10px 12px', background: 'none', border: 'none',
                                                            cursor: 'pointer', textAlign: 'left',
                                                        }}
                                                    >
                                                        <span style={{ fontWeight: 600 }}>{open ? '▾' : '▸'} {b.boxcode}</span>
                                                        <span className="text-muted" style={{ marginLeft: 'auto', fontSize: 13 }}>
                                                            {formatNumber(b.items.length, 0)} баркод · {formatNumber(units, 0)} шт
                                                        </span>
                                                    </button>
                                                    {open && (
                                                        <div style={{ padding: '0 12px 8px' }}>
                                                            {b.items.map((it) => (
                                                                <div
                                                                    key={it.barcode}
                                                                    style={{
                                                                        display: 'flex', gap: 10, alignItems: 'center',
                                                                        padding: '8px 0', borderTop: '1px solid var(--color-border)',
                                                                    }}
                                                                >
                                                                    {it.img_src && (
                                                                        // eslint-disable-next-line @next/next/no-img-element
                                                                        <img
                                                                            src={it.img_src}
                                                                            alt=""
                                                                            width={40}
                                                                            height={40}
                                                                            style={{ objectFit: 'cover', borderRadius: 4, flexShrink: 0 }}
                                                                        />
                                                                    )}
                                                                    <div style={{ flex: 1, minWidth: 0 }}>
                                                                        <div style={{ fontSize: 13, fontWeight: 500 }}>{it.imt_name ?? '—'}</div>
                                                                        <div className="text-muted" style={{ fontSize: 12 }}>
                                                                            {[it.brand, it.sa_nm, it.color_name].filter(Boolean).join(' · ')}
                                                                        </div>
                                                                        <div className="text-muted" style={{ fontSize: 12 }}>Баркод: {it.barcode}</div>
                                                                    </div>
                                                                    <div style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>
                                                                        {formatNumber(it.quantity, 0)} шт
                                                                    </div>
                                                                </div>
                                                            ))}
                                                        </div>
                                                    )}
                                                </div>
                                            );
                                        })}
                                    </div>
                                </>
                            ) : cabinetLoading ? (
                                <div className="text-muted">Загрузка коробов из кабинета…</div>
                            ) : (
                                <div className="text-muted">В кабинете пока нет коробов для этой поставки.</div>
                            )}
                        </section>
                    )}

                    <section>
                        <div className="section-title">Раскладка коробов</div>
                        {boxes.length === 0 ? (
                            <div className="text-muted">Раскладка коробов не задана — нажмите «Авто-раскладка»</div>
                        ) : (
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>Короб</th>
                                        <th>Содержимое</th>
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
                    </section>
                    <div>
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
                </div>
            )}

            {/* ── Пропуск ── */}
            {tab === 'pass' && (
                <div style={{ display: 'grid', gap: 16, maxWidth: 560 }}>
                    {cabinetPass?.has_pass && (
                        <div
                            className="glass-card"
                            style={{ padding: '10px 14px', borderLeft: '3px solid var(--color-info)', fontSize: 13 }}
                        >
                            <div style={{ fontWeight: 600, marginBottom: 4 }}>Пропуск уже заведён в кабинете WB</div>
                            <div className="text-muted">
                                {[cabinetPass.driver_first, cabinetPass.driver_last].filter(Boolean).join(' ')}
                                {cabinetPass.car_number ? ` · ${cabinetPass.car_number}` : ''}
                                {cabinetPass.pallets != null ? ` · ${formatNumber(cabinetPass.pallets, 0)} паллет` : ''}
                            </div>
                            {(cabinetPass.barcode_id || cabinetPass.date_from) && (
                                <div className="text-muted" style={{ marginTop: 2 }}>
                                    {cabinetPass.barcode_id ? `ШК: ${cabinetPass.barcode_prefix ?? 'WB-GI-'}${cabinetPass.barcode_id}` : ''}
                                    {cabinetPass.date_from ? ` · действует ${cabinetPass.date_from.slice(0, 10)}—${(cabinetPass.date_to ?? '').slice(0, 10)}` : ''}
                                </div>
                            )}
                            <div className="text-muted" style={{ marginTop: 4 }}>Поля ниже подставлены из кабинета — правьте и «Занести пропуск в WB» при необходимости.</div>
                        </div>
                    )}
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                        <div className="section-title" style={{ margin: 0 }}>Данные водителя и машины</div>
                        {hasVehicleData && (
                            <button
                                className="btn btn-secondary btn-sm"
                                type="button"
                                title="Заполнить госномер, марку и телефон из назначенной машины заявки"
                                onClick={fillFromVehicle}
                            >
                                Подставить из машины заявки
                            </button>
                        )}
                    </div>
                    <datalist id="wb-drivers">
                        {drivers.map((d, i) => (
                            <option key={i} value={`${d.firstName} ${d.lastName}`} />
                        ))}
                    </datalist>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                        <div className="form-group">
                            <label className="form-label">Имя<Req /></label>
                            <input className="form-input" list="wb-drivers" value={driverFirst} onChange={(e) => setDriverFirst(e.target.value)} style={emptyStyle(!driverFirst.trim())} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Фамилия<Req /></label>
                            <input className="form-input" value={driverLast} onChange={(e) => setDriverLast(e.target.value)} style={emptyStyle(!driverLast.trim())} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">
                                Телефон<Req />
                                {driverPhoneMismatch && (
                                    <span title={`В машине заявки: ${vehPhone}`} style={{ marginLeft: 6, color: 'var(--color-warning)', cursor: 'help' }}>⚠</span>
                                )}
                            </label>
                            <input
                                className="form-input"
                                value={driverPhone}
                                onChange={(e) => setDriverPhone(e.target.value)}
                                style={driverPhoneMismatch ? { borderColor: 'var(--color-warning)' } : emptyStyle(!driverPhone.trim())}
                            />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Способ отгрузки</label>
                            <select
                                className="form-input"
                                value={passAsBoxes ? 'box' : 'pallets'}
                                onChange={(e) => setPassAsBoxes(e.target.value === 'box')}
                            >
                                <option value="pallets">На паллете</option>
                                <option value="box">Отдельные короба</option>
                            </select>
                        </div>
                        <div className="form-group">
                            <label className="form-label">
                                {passAsBoxes ? 'Кол-во коробов' : 'Кол-во паллет'}<Req />
                                {palletsMismatch && (
                                    <span title={`В заявке: ${formatNumber(asmPallets ?? 0, 0)} ${passAsBoxes ? 'коробов' : 'паллет'}`} style={{ marginLeft: 6, color: 'var(--color-warning)', cursor: 'help' }}>⚠</span>
                                )}
                            </label>
                            <input
                                className="form-input"
                                type="number"
                                value={pallets}
                                onChange={(e) => setPallets(e.target.value === '' ? '' : Number(e.target.value))}
                                style={palletsMismatch ? { borderColor: 'var(--color-warning)' } : emptyStyle(pallets === '' || Number(pallets) <= 0)}
                            />
                        </div>
                        <div className="form-group">
                            <label className="form-label">
                                Марка авто<Req />
                                {carModelMismatch && (
                                    <span title={`В машине заявки: ${vehBrand}`} style={{ marginLeft: 6, color: 'var(--color-warning)', cursor: 'help' }}>⚠</span>
                                )}
                            </label>
                            <input
                                className="form-input"
                                value={carModel}
                                onChange={(e) => setCarModel(e.target.value)}
                                style={carModelMismatch ? { borderColor: 'var(--color-warning)' } : emptyStyle(!carModel.trim())}
                            />
                        </div>
                        <div className="form-group">
                            <label className="form-label">
                                Госномер<Req />
                                {carNumberMismatch && (
                                    <span title={`В машине заявки: ${vehInfo}`} style={{ marginLeft: 6, color: 'var(--color-warning)', cursor: 'help' }}>⚠</span>
                                )}
                            </label>
                            <input
                                className="form-input"
                                value={carNumber}
                                onChange={(e) => setCarNumber(e.target.value)}
                                style={carNumberMismatch ? { borderColor: 'var(--color-warning)' } : emptyStyle(!carNumber.trim())}
                            />
                        </div>
                    </div>

                    {!passComplete && (
                        <div className="text-muted" style={{ fontSize: 13 }}>
                            WB не примет пропуск без всех полей. Не заполнено: {missingPass.join(', ')}.
                        </div>
                    )}
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
                                        as_boxes: passAsBoxes,
                                    }),
                                )
                            }
                        >
                            {busy === 'savepass' ? '…' : 'Сохранить'}
                        </button>
                        <button
                            className="btn btn-primary btn-sm"
                            disabled={!sessionActive || busy === 'pushpass' || !st?.supply_id || !passComplete}
                            title={
                                !passComplete
                                    ? `Заполните все поля пропуска: ${missingPass.join(', ')}`
                                    : !st?.supply_id
                                        ? 'Сначала забронируйте дату в кабинете WB'
                                        : 'Занести пропуск в кабинет WB'
                            }
                            onClick={() => run('pushpass', () => api.wbSupplyPushPass(assemblyId))}
                        >
                            {busy === 'pushpass' ? 'Заношу…' : 'Занести пропуск в WB'}
                        </button>
                        <button
                            className="btn btn-secondary btn-sm"
                            disabled={busy === 'pass-sticker' || !passBarcode}
                            title={passBarcode
                                ? `Скачать PDF со ШК пропуска (${passBarcode})`
                                : 'ШК пропуска появится после заноса пропуска в WB'}
                            onClick={downloadPass}
                        >
                            {busy === 'pass-sticker' ? 'Готовлю…' : '📄 Скачать ШК пропуска'}
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}
