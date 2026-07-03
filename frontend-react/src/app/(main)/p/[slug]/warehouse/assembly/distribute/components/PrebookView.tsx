'use client';

import { useEffect, useMemo, useState } from 'react';
import { formatNumber } from '@/lib/utils';
import type { PackageType } from '@/types/api';
import { isPrebookingByLimit } from '@/lib/assembly/prebookFootprint';

/** Одна позиция предброни (SKU × ФФ-источник) в рамках направления. */
export interface PrebookRowItem {
    nm_id: number;
    vendor_code: string;
    ff: string;              // имя ФФ-склада-источника
    boxes: number;           // ЦЕЛЫХ коробов (floor(qty/ppb)) — везём только целые
    qty: number;             // всего штук (целые коробы + остаток)
    /** Остаток россыпью (`qty % ppb`) — не кратен коробу, не едет целым коробом. */
    looseUnits: number;
    /** Кратность короба (0 — не задана). */
    ppb: number;
    /** Свободно штук этого SKU на ЭТОМ ФФ (сток − занятое черновиком/предбронью) —
     *  объясняет, почему остаток не добит: добивается автоматом только когда
     *  свободного хватает до целого короба. */
    freeUnits: number;
}

/** Одна моно-паллета направления (целая или частичная): какие SKU и сколько.
 *  Считается тем же авторитетом (packMonoPallets), что и footprint/бронь —
 *  показ на карточке совпадает с тем, что реально забронируется. */
export interface PrebookMonoPallet {
    /** Заполнение паллеты 0..1 (Σ units_i/upp_i по SKU паллеты). */
    fillPct: number;
    items: { nm_id: number; vendor: string; boxes: number; units: number }[];
}

/** Возможность дозабора направления (минимально, с одного ФФ). */
export interface PrebookTopUp {
    ff: string;              // с какого ФФ добираем
    needBoxes: number;       // сколько коробов не хватает до целых паллет
    pallets: number;         // сколько целых паллет соберётся
    /** Чем именно дозаберётся (предварительно, до проверки приёмки при клике). */
    candidates: { vendor: string; boxes: number }[];
}

/** Группа предброни = одна ОТГРУЗКА: направление × ФФ-источник × упаковка.
 *  Паллета собирается с ОДНОГО ФФ (короб с двух складов не собрать) — поэтому
 *  заполнение и дозабор считаются per-ФФ, а не по всему направлению. */
export interface PrebookGroup {
    pkg: PackageType;
    wb: string;
    ff: string;              // имя ФФ-склада-источника
    ffId: number;
    items: PrebookRowItem[];
    boxes: number;           // Σ целых коробов направления
    qty: number;
    /** Σ остатка россыпью направления (штук не кратно коробу). */
    looseUnits: number;
    /** Полный footprint в паллетах (≥1 = уже собрана целая паллета + хвост). */
    footprint: number;
    /** Заполнение паллеты 0..1 (доля объёма от целой). */
    fillPct: number;
    /** Дозабор возможен → инфо; иначе null (нечем). */
    topUp: PrebookTopUp | null;
    /** МОНО-хвост поверх целых паллет (footprint≥1, дробь>0): чем дозабрать до ещё
     *  одной целой в предброни (для под-вкладки «Предзаявка»). null — нечем/нет хвоста. */
    tailTopUp?: PrebookTopUp | null;
    /** МОНО: попаллетная раскладка ЦЕЛЫХ паллет (какой SKU в какой паллете поедет). */
    monoPallets?: PrebookMonoPallet[];
    /** МОНО: недобор в структуре паллет ≤3 арт — ЧАСТИЧНЫЕ паллеты (хвосты, нарезанные
     *  по 3 крупнейших). Каждая <100%; «Дозабить до целой» целит в первую (крупнейшую). */
    monoPartials?: PrebookMonoPallet[];
    /** МОНО: сырой ОБЪЁМ недобора в паллетах (Σ хвостов/upp), для внутренней математики. */
    monoTailFrac?: number;
}

/** Метка приёмки WB для направления предброни (per упаковка группы). Зеркало
 *  ⌛-логики «Потребности по складам», но БЕЗ priority-схлопа: карточка предброни
 *  уже одного типа (g.pkg) → берём meta именно его (моно → mono_meta), поэтому
 *  моно-лимит виден корректно (в «Потребности» bi-modal SKU схлопывался к коробу). */
export interface PrebookAcceptanceMark {
    checked: boolean;         // есть ли данные приёмки
    open: boolean;            // WB принимает эту упаковку на складе
    closed: boolean;          // WB не принимает вообще (все типы закрыты)
    freeDays?: number;        // свободных дней приёмки/14 (для этого типа)
    paidDays?: number;        // платных дней приёмки/14
    noLimit: boolean;         // открыт по options, но лимита нет (⌛ — нужна предзаявка)
}

interface Props {
    groups: PrebookGroup[];
    toppingUpKey: string | null;   // `${pkg}::${wb}::${ffId}` пока идёт дозабор
    shipAsIsKey: string | null;    // `${pkg}::${wb}::${ffId}` пока идёт отгрузка «как есть»
    deletingKey: string | null;    // `${pkg}::${wb}::${ffId}` пока идёт удаление направления
    prebookingKey: string | null;  // `${pkg}::${wb}::${ffId}` пока создаётся предзаявка
    tailTopUpKey: string | null;   // `${pkg}::${wb}::${ffId}` пока идёт дозабор хвоста в предброни
    trimTailKey: string | null;    // `${pkg}::${wb}::${ffId}` пока убирается неполная паллета
    /** Метки приёмки WB по ключу `${pkg}::${wb}::${ffId}` (⌛ / открыт / закрыт). */
    acceptanceMarks: Map<string, PrebookAcceptanceMark>;
    acceptanceLoading: boolean;    // идёт фоновая проверка приёмки предброни
    /** Склады, где предзаявка разрешена без лимита приёмки (whitelist) — кнопка активна. */
    preorderWbs: Set<string>;
    onTopUp: (pkg: PackageType, wb: string, ffId: number) => void;
    /** Отгрузить неполную паллету направления в черновик как есть (без дозабора). */
    onShipAsIs: (pkg: PackageType, wb: string, ffId: number) => void;
    onDelete: (nm_id: number, wb: string, pkg: PackageType) => void;
    /** Удалить ВСЁ направление (упаковка × склад × ФФ) из предброни — коробы на ФФ. */
    onDeleteDirection: (pkg: PackageType, wb: string, ffId: number) => void;
    /** Создать предзаявку (бронь) на моно: готовые целые ⌛-паллеты → заявки с пометкой. */
    onCreatePrebooking: (pkg: PackageType, wb: string, ffId: number) => void;
    /** Дозабрать МОНО-хвост в предброни до ещё одной целой паллеты (результат остаётся
     *  в предброни под предзаявку, НЕ в черновик). */
    onTopUpPrebook: (wb: string, ffId: number) => void;
    /** Убрать неполную МОНО-паллету (хвост) — оставить только целые; хвост на ФФ. */
    onTrimTail: (wb: string, ffId: number) => void;
    /** Предзаявка из выбранных паллет раскладки ОДНОЙ заявкой (одна или несколько;
     *  частичные — по явному решению юзера). */
    onBookPallets: (wb: string, ffId: number, pallets: { pallet: PrebookMonoPallet; palletNo: number }[]) => void;
    /** Освободить выбранные паллеты раскладки на ФФ (убрать из предброни; остальные
     *  паллеты направления остаются). */
    onReleasePallets: (wb: string, ffId: number, pallets: { pallet: PrebookMonoPallet; palletNo: number }[]) => void;
    /** `${wb}::${ffId}::${palletNo}` (или `::#1+3+5` для пачки) пока идёт попаллетная операция. */
    palletOpKey: string | null;
}

const PKG_LABEL: Record<string, string> = { BOX: 'Короб', MONOPALLET: 'Моно', SUPERSAFE: 'Сейф' };

/** Порог готовности паллеты (%) — паллета «готова» к отгрузке при заполнении ≥ порога;
 *  ниже — подсвечиваем. Ред-мое, хранится локально (одно значение на всю предбронь). */
const THRESHOLD_KEY = 'dds.prebook.readyThresholdPct';
const DEFAULT_THRESHOLD = 60;

export default function PrebookView({ groups, toppingUpKey, shipAsIsKey, deletingKey, prebookingKey, tailTopUpKey, trimTailKey, acceptanceMarks, acceptanceLoading, preorderWbs, onTopUp, onShipAsIs, onDelete, onDeleteDirection, onCreatePrebooking, onTopUpPrebook, onTrimTail, onBookPallets, onReleasePallets, palletOpKey }: Props) {
    const pkgsPresent = useMemo(() => {
        const order: PackageType[] = ['BOX', 'MONOPALLET', 'SUPERSAFE'];
        return order.filter(p => groups.some(g => g.pkg === p));
    }, [groups]);
    const [pkg, setPkg] = useState<PackageType>('BOX');
    const activePkg = pkgsPresent.includes(pkg) ? pkg : (pkgsPresent[0] ?? 'BOX');

    // Порог готовности: ред-мое поле, персист в localStorage (клиент).
    const [threshold, setThreshold] = useState<number>(DEFAULT_THRESHOLD);
    useEffect(() => {
        const raw = typeof window !== 'undefined' ? window.localStorage.getItem(THRESHOLD_KEY) : null;
        const n = raw != null ? Number(raw) : NaN;
        if (Number.isFinite(n) && n >= 0 && n <= 100) setThreshold(n);
    }, []);
    const updateThreshold = (v: number) => {
        const clamped = Math.max(0, Math.min(100, Math.round(v)));
        setThreshold(clamped);
        if (typeof window !== 'undefined') window.localStorage.setItem(THRESHOLD_KEY, String(clamped));
    };

    // Мультивыбор паллет раскладки (моно): ключ `${wb}::${ffId}::${palletNo}`.
    // Сбрасывается при любом пересчёте groups: раскладка перепаковывается и номера
    // паллет могут перетасоваться — выбор по старым номерам стал бы враньём.
    const [palletSel, setPalletSel] = useState<Set<string>>(new Set());
    useEffect(() => { setPalletSel(new Set()); }, [groups]);
    const togglePallet = (k: string) => setPalletSel(prev => {
        const next = new Set(prev);
        if (next.has(k)) next.delete(k); else next.add(k);
        return next;
    });

    const shown = useMemo(
        () => groups.filter(g => g.pkg === activePkg).sort((a, b) => b.fillPct - a.fillPct),
        [groups, activePkg],
    );
    // Моно split: «Дозабить» (footprint<1 — неполные, нужно добить) / «Предзаявка»
    // (footprint≥1 — готовые целые ⌛-паллеты, сдаются бронью). Короб/сейф — без split.
    const isMono = activePkg === 'MONOPALLET';
    const [monoView, setMonoView] = useState<'topup' | 'prebooking'>('topup');
    // Split по ЛИМИТУ приёмки, НЕ по footprint: ⌛ без лимита (noLimit) → «Предзаявка»
    // (и неполные-дозабираемые, и целые паллеты); с лимитом / без данных приёмки →
    // «Дозабить» (можно добить и сразу в черновик). Карточку без данных не теряем.
    const monoReady = useMemo(() => shown.filter(g => isPrebookingByLimit(acceptanceMarks.get(`${g.pkg}::${g.wb}::${g.ffId}`))), [shown, acceptanceMarks]);
    const monoTopup = useMemo(() => shown.filter(g => !isPrebookingByLimit(acceptanceMarks.get(`${g.pkg}::${g.wb}::${g.ffId}`))), [shown, acceptanceMarks]);
    const visible = !isMono ? shown : (monoView === 'prebooking' ? monoReady : monoTopup);
    const totals = useMemo(() => {
        const g = groups;
        const canDirs = g.filter(x => x.topUp).length;
        const resultPallets = g.reduce((s, x) => s + (x.topUp?.pallets || 0), 0);
        // «Ниже порога» = направление БЕЗ ни одной целой паллеты, где единственная
        // неполная паллета заполнена меньше порога (целую паллету «слишком пусто» не зовём).
        const lowDirs = g.filter(x => {
            const whole = Math.floor(x.footprint + 1e-9);
            return whole === 0 && x.footprint > 0 && Math.round(x.footprint * 100) < threshold;
        }).length;
        return {
            boxes: g.reduce((s, x) => s + x.boxes, 0),
            qty: g.reduce((s, x) => s + x.qty, 0),
            dirs: g.length,
            canDirs,
            noDirs: g.length - canDirs,
            resultPallets,
            lowDirs,
            looseUnits: g.reduce((s, x) => s + (x.looseUnits || 0), 0),
            partialDirs: g.filter(x => (x.looseUnits || 0) > 0).length,
        };
    }, [groups, threshold]);

    if (groups.length === 0) {
        return (
            <div className="glass-card">
                <div className="empty-state">
                    <div className="empty-state-text">
                        Предбронь пуста. Сюда попадают целые коробы, не собравшие паллету, при «Заполнить черновик из потребности».
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div>
            {/* KPI */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 16 }}>
                <div className="glass-card" style={{ padding: '14px 16px' }}>
                    <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>В предброни</div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>{formatNumber(totals.boxes, 0)} кор</div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{formatNumber(totals.qty, 0)} шт · {formatNumber(totals.dirs, 0)} напр.</div>
                    {totals.looseUnits > 0 && (
                        <div style={{ fontSize: 12, color: 'var(--color-warning)', fontWeight: 600, marginTop: 2 }} title="Штук не кратно коробу — неполные коробы (остатки). Едут только целые коробы.">
                            ⚠ остатки {formatNumber(totals.looseUnits, 0)} шт · {formatNumber(totals.partialDirs, 0)} напр.
                        </div>
                    )}
                </div>
                <div className="glass-card" style={{ padding: '14px 16px', borderLeft: '3px solid var(--color-success)' }}>
                    <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>Можно дозабрать</div>
                    <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--color-success)' }}>{formatNumber(totals.canDirs, 0)} напр.</div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>соберётся {formatNumber(totals.resultPallets, 0)} паллет</div>
                </div>
                <div className="glass-card" style={{ padding: '14px 16px' }}>
                    <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 4 }}>Нечем дозабрать</div>
                    <div style={{ fontSize: 22, fontWeight: 700 }}>{formatNumber(totals.noDirs, 0)} напр.</div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>нет свободного ФФ</div>
                </div>
            </div>

            {/* Порог готовности + под-вкладки упаковки */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
                <div style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap' }}>
                    {pkgsPresent.map(p => (
                        <button key={p}
                            className={`btn btn-sm ${p === activePkg ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setPkg(p)}>
                            {PKG_LABEL[p] || p} · {groups.filter(g => g.pkg === p).length} напр.
                        </button>
                    ))}
                </div>
                {isMono && (
                    <div style={{ display: 'inline-flex', gap: 6 }}>
                        <button className={`btn btn-sm ${monoView === 'topup' ? 'btn-primary' : 'btn-secondary'}`}
                            title="Неполные моно-паллеты — нужно добить до целой" onClick={() => setMonoView('topup')}>
                            🧩 Дозабить · {monoTopup.length}
                        </button>
                        <button className={`btn btn-sm ${monoView === 'prebooking' ? 'btn-primary' : 'btn-secondary'}`}
                            title="Готовые целые моно-паллеты на склад без лимита приёмки (⌛) — сдаются предзаявкой (бронью)"
                            onClick={() => setMonoView('prebooking')}>
                            📋 Предзаявка · {monoReady.length}
                        </button>
                    </div>
                )}
                <label style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--color-text-muted)' }}>
                    Паллета готова при ≥
                    <input
                        type="number" min={0} max={100} value={threshold}
                        onChange={e => updateThreshold(Number(e.target.value))}
                        style={{ width: 56, padding: '3px 6px', borderRadius: 8, border: '1px solid var(--color-border)', background: 'var(--color-bg-card)', color: 'var(--color-text)', textAlign: 'right', fontSize: 13 }}
                    />
                    %
                    {totals.lowDirs > 0 && (
                        <span style={{ color: 'var(--color-warning)', fontWeight: 600 }}>· ниже: {formatNumber(totals.lowDirs, 0)}</span>
                    )}
                </label>
            </div>

            {visible.length === 0 && (
                <div className="glass-card" style={{ padding: 16, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>
                    {isMono && monoView === 'prebooking'
                        ? 'Нет готовых целых моно-паллет для предзаявки (появятся, когда моно на складе без лимита ⌛ соберётся в целую паллету).'
                        : isMono ? 'Нет неполных моно-направлений для дозабора.' : 'Нет направлений.'}
                </div>
            )}

            {/* Карточки направлений */}
            {visible.map(g => {
                const key = `${g.pkg}::${g.wb}::${g.ffId}`;
                const busyTop = toppingUpKey === key;
                const busyShip = shipAsIsKey === key;
                const busyDel = deletingKey === key;
                const busyPrebook = prebookingKey === key;
                const busyTailTop = tailTopUpKey === key;
                const busyTrim = trimTailKey === key;
                const busy = busyTop || busyShip || busyDel || busyPrebook || busyTailTop || busyTrim;
                const preorderOk = preorderWbs.has(g.wb);
                // Предзаявка (бронь → отдельная заявка на сборку) нужна ТОЛЬКО для ⌛ моно
                // (приём открыт, но лимита нет). Для моно с ОТКРЫТЫМ лимитом предзаявка не
                // нужна — оно едет в черновик как короб: целые паллеты авто-уезжают
                // консолидацией, недобор → «Оставить так»/«Дозабить»/«На ФФ». Поэтому все
                // кнопки предзаявки скрываем на не-⌛ направлениях (тот же предикат, что split).
                const preorderByLimit = isPrebookingByLimit(acceptanceMarks.get(key));
                // Полный footprint: целые паллеты (уже собраны) + дробь последней.
                const whole = Math.floor(g.footprint + 1e-9);
                // ЧЕСТНЫЙ floor (не round): недобранная паллета (0.997) НЕ должна показываться
                // «100%» — упаковщик её целой не считает (keep=floor<1, остаётся в предброни).
                // Показ 99% + дозабор консистентен: добить 1 короб → станет целой → в черновик.
                const fracPct = Math.max(0, Math.min(99, Math.floor((g.footprint - whole) * 100)));
                // МОНО: недобор живёт в СТРУКТУРЕ паллет ≤3 арт (частичные), не «объёмом».
                const partials = g.pkg === 'MONOPALLET' ? (g.monoPartials ?? []) : [];
                // Честный floor (не round): частичная паллета не должна показываться «100%».
                const topPartialPct = Math.min(99, Math.floor((partials[0]?.fillPct ?? 0) * 100));
                const low = g.pkg === 'MONOPALLET'
                    ? whole === 0 && partials.length > 0 && topPartialPct < threshold
                    : whole === 0 && g.footprint > 0 && Math.round(g.footprint * 100) < threshold;
                const fillLabel = g.pkg === 'MONOPALLET'
                    ? [
                        whole >= 1 ? `${formatNumber(whole, 0)} ${whole === 1 ? 'целая' : 'целых'}` : '',
                        partials.length > 0 ? `${formatNumber(partials.length, 0)} частичн. (макс ${topPartialPct}%)` : '',
                    ].filter(Boolean).join(' + ') || 'пусто'
                    : whole >= 1
                        ? `${formatNumber(whole, 0)} ${whole === 1 ? 'целая паллета' : 'целых паллеты'}${fracPct > 0 ? ` + ${fracPct}% ещё одной` : ''}`
                        : `заполнено ${fracPct}% паллеты`;
                return (
                    <div key={key} className="glass-card"
                        style={{ padding: '14px 16px', marginBottom: 12, borderLeft: low ? '3px solid var(--color-warning)' : undefined }}>
                        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
                            <span style={{ fontSize: 15, fontWeight: 700 }}>→ {g.wb}</span>
                            <span style={{ fontSize: 12, padding: '2px 8px', borderRadius: 6, background: 'rgba(148,163,184,0.15)', color: 'var(--color-text-muted)' }}>ФФ: {g.ff}</span>
                            <span style={{ fontSize: 12, padding: '2px 8px', borderRadius: 6, background: 'rgba(59,130,246,0.10)', color: 'var(--color-accent)' }}>{PKG_LABEL[g.pkg] || g.pkg}</span>
                            {(() => {
                                const acc = acceptanceMarks.get(key);
                                const pill = (bg: string, color: string, text: string, tip: string) => (
                                    <span title={tip} style={{ fontSize: 12, fontWeight: 600, padding: '2px 8px', borderRadius: 6, background: bg, color }}>{text}</span>
                                );
                                if (!acc?.checked) {
                                    return acceptanceLoading
                                        ? <span style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>⏳ приёмка…</span>
                                        : null;
                                }
                                if (acc.closed) return pill('rgba(239,68,68,0.14)', 'var(--color-danger)', '⛔ приём закрыт', `WB не принимает на «${g.wb}» в ближайшие 14 дней`);
                                if (!acc.open) return null;   // тип этой упаковки не принимается, но склад открыт для другого — не пугаем
                                const kind = PKG_LABEL[g.pkg]?.toLowerCase() || 'упаковку';
                                const icon = g.pkg === 'MONOPALLET' ? '📐' : g.pkg === 'SUPERSAFE' ? '🔒' : '📦';
                                if (acc.noLimit) {
                                    return pill('rgba(245,158,11,0.15)', 'var(--color-warning)', `${icon} ⌛ нужна предзаявка`,
                                        `Принимает ${kind}, но лимита приёмки нет (свободно ${acc.freeDays ?? 0}/14 дн) — ⌛ нужна предзаявка`);
                                }
                                return pill('rgba(34,197,94,0.14)', 'var(--color-success)', `${icon} приём открыт${acc.freeDays ? ` · ${acc.freeDays} дн` : ''}`,
                                    `Принимает ${kind} — свободно ${acc.freeDays ?? 0}/14 дн${acc.paidDays ? `, платно ${acc.paidDays}` : ''}`);
                            })()}
                            <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>{formatNumber(g.boxes, 0)} кор · {formatNumber(g.qty - g.looseUnits, 0)} шт{whole >= 1 ? ` · ≈${g.footprint.toFixed(1)} пал` : ''}</span>
                            {low && (
                                <span style={{ fontSize: 12, fontWeight: 600, padding: '2px 8px', borderRadius: 6, background: 'rgba(245,158,11,0.15)', color: 'var(--color-warning)' }}>⚠ ниже порога</span>
                            )}
                            {g.looseUnits > 0 && (
                                <span style={{ fontSize: 12, fontWeight: 600, padding: '2px 8px', borderRadius: 6, background: 'rgba(245,158,11,0.15)', color: 'var(--color-warning)' }}
                                    title="Штук не кратно коробу — неполный короб (остатки). Едут только целые коробы; остаток остаётся на ФФ.">⚠ остаток {formatNumber(g.looseUnits, 0)} шт</span>
                            )}
                            {g.pkg === 'MONOPALLET' && (g.monoPartials?.length ?? 0) > 0 && (
                                <span style={{ fontSize: 12, fontWeight: 600, padding: '2px 8px', borderRadius: 6, background: 'rgba(245,158,11,0.15)', color: 'var(--color-warning)' }}
                                    title="Недобор в структуре паллет: хвосты нарезаны по ≤3 артикула (правило WB для моно). «Дозабить до целой» добирает коробами крупнейшую частичную; состав — в «Раскладке по паллетам».">
                                    ⚠ недобор: {formatNumber(g.monoPartials?.length ?? 0, 0)} частичн. паллет
                                </span>
                            )}
                            <span style={{ marginLeft: 'auto', fontSize: 13, fontWeight: (low || whole >= 1) ? 700 : 400, color: low ? 'var(--color-warning)' : whole >= 1 ? 'var(--color-success)' : 'var(--color-text-muted)' }}>{fillLabel}</span>
                            {isMono && whole >= 1 && preorderByLimit && (
                                <button className="btn btn-primary btn-sm" style={{ padding: '2px 8px' }} disabled={busy || !preorderOk}
                                    title={preorderOk
                                        ? `Создать предзаявку (бронь) — заявки на сборку с пометкой «предзаявка на моно». В бронь уйдут только ЦЕЛЫЕ паллеты (${whole}); неполный хвост останется в предброни`
                                        : 'Склад не в списке разрешённых для предзаявки без лимита — добавьте его в «Настройки складов»'}
                                    onClick={() => onCreatePrebooking(g.pkg, g.wb, g.ffId)}>
                                    {busyPrebook ? '…' : preorderOk ? `📋 Создать предзаявку · ${formatNumber(whole, 0)} цел.` : '📋 Предзаявка не разрешена'}
                                </button>
                            )}
                            <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px' }} disabled={busy}
                                title="Удалить всё направление из предброни — коробы останутся на ФФ"
                                onClick={() => onDeleteDirection(g.pkg, g.wb, g.ffId)}>
                                {busyDel ? '…' : '🗑 Удалить направление'}
                            </button>
                        </div>
                        {/* Сегментированный бар: N целых зелёных паллет; МОНО — по сегменту на
                            КАЖДУЮ частичную (≤3 арт), короб — одна неполная (объём). */}
                        <div style={{ display: 'flex', gap: 3, height: 6, marginBottom: 10 }}>
                            {Array.from({ length: Math.min(whole, 8) }).map((_, i) => (
                                <div key={`full-${i}`} style={{ flex: 1, background: 'var(--color-success)', borderRadius: 3 }} title="целая паллета" />
                            ))}
                            {g.pkg === 'MONOPALLET'
                                ? partials.slice(0, 8).map((p, i) => (
                                    <div key={`part-${i}`} style={{ flex: 1, background: 'rgba(148,163,184,0.20)', borderRadius: 3, overflow: 'hidden' }} title={`частичная паллета ${Math.round(p.fillPct * 100)}% (≤3 арт)`}>
                                        <div style={{ width: `${Math.round(p.fillPct * 100)}%`, height: '100%', background: low ? 'var(--color-warning)' : 'var(--color-success)' }} />
                                    </div>
                                ))
                                : (fracPct > 0 || whole === 0) && (
                                    <div style={{ flex: 1, background: 'rgba(148,163,184,0.20)', borderRadius: 3, overflow: 'hidden' }} title="неполная паллета">
                                        <div style={{ width: `${fracPct}%`, height: '100%', background: low ? 'var(--color-warning)' : 'var(--color-success)' }} />
                                    </div>
                                )}
                        </div>

                        {isMono && monoView === 'prebooking' ? (
                            partials.length > 0 ? (
                                <div style={{ padding: '8px 10px', background: 'rgba(59,130,246,0.08)', borderRadius: 8, marginBottom: 10 }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                                        <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                            {whole >= 1
                                                ? <>Готово к предзаявке: <b style={{ color: 'var(--color-success)' }}>{formatNumber(whole, 0)} {whole === 1 ? 'целая' : 'целых'}</b> · сверху {formatNumber(partials.length, 0)} частичн. (крупнейшая <b>{topPartialPct}%</b>, ≤3 арт) — дозабрать до целой или убрать.</>
                                                : <>Целых паллет пока нет · <b>{formatNumber(partials.length, 0)} частичн.</b> (крупнейшая <b>{topPartialPct}%</b>, ≤3 арт) — дозабрать до целой или забронировать частичную (кнопки в раскладке ниже).</>}
                                        </span>
                                        <div style={{ marginLeft: 'auto', display: 'inline-flex', gap: 8, flexWrap: 'wrap' }}>
                                            {g.tailTopUp && (
                                                <button className="btn btn-success btn-sm" disabled={busy}
                                                    title={`Добрать целыми коробами из свободного ФФ «${g.tailTopUp.ff}» до ещё одной целой паллеты — останется в предброни под предзаявку`}
                                                    onClick={() => onTopUpPrebook(g.wb, g.ffId)}>
                                                    {busyTailTop ? '…' : '🧩 Дозабить до целой'}
                                                </button>
                                            )}
                                            <button className="btn btn-secondary btn-sm" disabled={busy}
                                                title="Убрать неполную паллету — оставить только целые; хвост освободится на ФФ"
                                                onClick={() => onTrimTail(g.wb, g.ffId)}>
                                                {busyTrim ? '…' : '✂ Убрать неполную'}
                                            </button>
                                        </div>
                                    </div>
                                    {g.tailTopUp && g.tailTopUp.candidates.length > 0 ? (
                                        <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'baseline' }}>
                                            <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>дозаберётся:</span>
                                            {g.tailTopUp.candidates.map((c, i) => (
                                                <span key={`${c.vendor}-${i}`} style={{ fontSize: 11, padding: '1px 7px', borderRadius: 5, background: 'rgba(34,197,94,0.15)', color: 'var(--color-success)' }}>
                                                    {c.vendor} ×{formatNumber(c.boxes, 0)} кор
                                                </span>
                                            ))}
                                        </div>
                                    ) : (
                                        <div style={{ marginTop: 6, fontSize: 11, color: 'var(--color-text-dim)' }}>
                                            Дозабрать нечем — нет свободного ФФ на эти артикулы. Можно убрать неполную и оставить только целые.
                                        </div>
                                    )}
                                </div>
                            ) : (
                                <div style={{ padding: '8px 10px', background: 'rgba(34,197,94,0.10)', borderRadius: 8, marginBottom: 10 }}>
                                    <span style={{ fontSize: 13, color: 'var(--color-success)' }}>
                                        {formatNumber(whole, 0)} {whole === 1 ? 'целая паллета' : 'целых паллет'} — готово к предзаявке, без неполного хвоста.
                                    </span>
                                </div>
                            )
                        ) : g.topUp ? (
                            <div style={{ padding: '8px 10px', background: 'rgba(34,197,94,0.10)', borderRadius: 8, marginBottom: 10 }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                                    <span style={{ fontSize: 13, color: 'var(--color-success)' }}>
                                        Не хватает <b>{formatNumber(g.topUp.needBoxes, 0)} кор</b> — есть в свободном ФФ <b>«{g.topUp.ff}»</b>. Дозабор → <b>{formatNumber(g.topUp.pallets, 0)} целых паллет</b>.
                                    </span>
                                    <div style={{ marginLeft: 'auto', display: 'inline-flex', gap: 8, flexWrap: 'wrap' }}>
                                        <button className="btn btn-secondary btn-sm" disabled={busy}
                                            onClick={() => onShipAsIs(g.pkg, g.wb, g.ffId)}>
                                            {busyShip ? '…' : '📦 Оставить так'}
                                        </button>
                                        <button className="btn btn-success btn-sm" disabled={busy}
                                            onClick={() => onTopUp(g.pkg, g.wb, g.ffId)}>
                                            {busyTop ? '…' : `🧩 Дозабить из «${g.topUp.ff}»`}
                                        </button>
                                    </div>
                                </div>
                                {g.topUp.candidates.length > 0 && (
                                    <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'baseline' }}>
                                        <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>дозаберётся:</span>
                                        {g.topUp.candidates.map((c, i) => (
                                            <span key={`${c.vendor}-${i}`} style={{ fontSize: 11, padding: '1px 7px', borderRadius: 5, background: 'rgba(34,197,94,0.15)', color: 'var(--color-success)' }}>
                                                {c.vendor} ×{formatNumber(c.boxes, 0)} кор
                                            </span>
                                        ))}
                                        <span style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>· приёмка проверится при клике</span>
                                    </div>
                                )}
                            </div>
                        ) : (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px', background: 'rgba(245,158,11,0.10)', borderRadius: 8, marginBottom: 10, flexWrap: 'wrap' }}>
                                <span style={{ fontSize: 13, color: 'var(--color-warning)' }}>
                                    Нет свободного ФФ на это направление — дозабрать нечем. Отгрузить как есть или удалить.
                                </span>
                                <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} disabled={busy}
                                    onClick={() => onShipAsIs(g.pkg, g.wb, g.ffId)}>
                                    {busyShip ? '…' : '📦 Оставить так'}
                                </button>
                            </div>
                        )}

                        {/* МОНО: попаллетная раскладка — какой SKU в какой паллете поедет.
                            Тот же авторитет (packMonoPallets), что и бронь: показ == бронь.
                            Недобор — ТОЖЕ паллетами (частичные, ≤3 арт), не «объёмом». */}
                        {g.pkg === 'MONOPALLET' && ((g.monoPallets?.length ?? 0) + partials.length) > 0 && (() => {
                            // Все паллеты раскладки (целые + частичные) с их номерами — единый
                            // список для чекбоксов мультивыбора и разрешения выбранных при действии.
                            const allPallets = [
                                ...(g.monoPallets ?? []).map((p, i) => ({ pallet: p, palletNo: i + 1, partial: false })),
                                ...partials.map((p, i) => ({ pallet: p, palletNo: (g.monoPallets?.length ?? 0) + i + 1, partial: true })),
                            ];
                            const selKey = (no: number) => `${g.wb}::${g.ffId}::${no}`;
                            const selected = allPallets.filter(x => palletSel.has(selKey(x.palletNo)));
                            const selUnits = selected.reduce((s, x) => s + x.pallet.items.reduce((a, it) => a + it.units, 0), 0);
                            const allSelected = selected.length === allPallets.length && allPallets.length > 0;
                            const bulkBusy = palletOpKey != null && palletOpKey.startsWith(`${g.wb}::${g.ffId}::#`);
                            return (
                            <div style={{ marginBottom: 10, display: 'flex', flexDirection: 'column', gap: 4 }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                                    <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>📐 Раскладка по паллетам (≤3 артикулов на каждой):</span>
                                    <label style={{ fontSize: 11, color: 'var(--color-text-muted)', display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                                        <input type="checkbox" checked={allSelected} disabled={palletOpKey != null}
                                            onChange={() => setPalletSel(prev => {
                                                const next = new Set(prev);
                                                for (const x of allPallets) { if (allSelected) next.delete(selKey(x.palletNo)); else next.add(selKey(x.palletNo)); }
                                                return next;
                                            })} />
                                        выбрать все
                                    </label>
                                </div>
                                {selected.length > 0 && (
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', padding: '6px 10px', background: 'rgba(59,130,246,0.10)', borderRadius: 8 }}>
                                        <span style={{ fontSize: 12, fontWeight: 600 }}>
                                            Выбрано {formatNumber(selected.length, 0)} паллет · {formatNumber(selUnits, 0)} шт
                                        </span>
                                        <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 6 }}>
                                            {preorderByLimit && (
                                                <button className="btn btn-primary btn-sm" style={{ padding: '2px 9px', fontSize: 11 }}
                                                    disabled={busy || palletOpKey != null || !preorderOk}
                                                    title={preorderOk ? 'Создать ОДНУ предзаявку из всех выбранных паллет — остальные паллеты направления останутся в предброни' : 'Склад не в списке разрешённых для предзаявки — добавьте в «Настройки складов»'}
                                                    onClick={() => onBookPallets(g.wb, g.ffId, selected.map(({ pallet, palletNo }) => ({ pallet, palletNo })))}>
                                                    {bulkBusy ? '…' : '📋 Предзаявка одной заявкой'}
                                                </button>
                                            )}
                                            <button className="btn btn-secondary btn-sm" style={{ padding: '2px 9px', fontSize: 11 }}
                                                disabled={busy || palletOpKey != null}
                                                title="Оставить выбранные паллеты на ФФ — убрать из предброни, коробы освободятся; остальные паллеты направления останутся"
                                                onClick={() => onReleasePallets(g.wb, g.ffId, selected.map(({ pallet, palletNo }) => ({ pallet, palletNo })))}>
                                                {bulkBusy ? '…' : '✂ На ФФ'}
                                            </button>
                                        </span>
                                    </div>
                                )}
                                {allPallets.map(({ pallet: p, palletNo, partial }) => {
                                    const opKey = `${g.wb}::${g.ffId}::${palletNo}`;
                                    const checked = palletSel.has(selKey(palletNo));
                                    const busyPallet = palletOpKey === opKey || (bulkBusy && checked);
                                    return (
                                        <div key={`pal-${palletNo}`} style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', fontSize: 12, padding: '4px 8px', background: partial ? 'rgba(245,158,11,0.08)' : 'rgba(148,163,184,0.08)', borderRadius: 8, outline: checked ? '1px solid var(--color-accent)' : undefined }}>
                                            <input type="checkbox" checked={checked} disabled={palletOpKey != null}
                                                title="Выбрать паллету для группового действия (предзаявка одной заявкой / на ФФ)"
                                                onChange={() => togglePallet(selKey(palletNo))} style={{ cursor: 'pointer' }} />
                                            <b style={partial ? { color: 'var(--color-warning)' } : undefined}>{partial ? `Частичная ${palletNo}` : `Паллета ${palletNo}`}</b>
                                            <span style={{ fontSize: 11, color: partial ? 'var(--color-warning)' : p.fillPct >= 0.999 ? 'var(--color-success)' : 'var(--color-text-dim)' }}>{Math.round(p.fillPct * 100)}%</span>
                                            {p.items.map((it, j) => (
                                                <span key={`pi-${j}`} style={{ fontSize: 11, padding: '1px 7px', borderRadius: 5, background: partial ? 'rgba(245,158,11,0.12)' : 'rgba(59,130,246,0.10)', color: partial ? 'var(--color-warning)' : 'var(--color-accent)' }}>
                                                    {it.vendor}{it.boxes > 0 ? ` · ${formatNumber(it.boxes, 0)} кор` : ''} · {formatNumber(it.units, 0)} шт
                                                </span>
                                            ))}
                                            <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 4 }}>
                                                {preorderByLimit && (
                                                    <button className="btn btn-primary btn-sm" style={{ padding: '1px 7px', fontSize: 11 }}
                                                        disabled={busy || palletOpKey != null || !preorderOk}
                                                        title={preorderOk ? (partial ? `Создать предзаявку из этой ЧАСТИЧНОЙ паллеты (${Math.round(p.fillPct * 100)}%) — по вашему решению` : 'Создать предзаявку ТОЛЬКО из этой паллеты — остальные останутся в предброни') : 'Склад не в списке разрешённых для предзаявки — добавьте в «Настройки складов»'}
                                                        onClick={() => onBookPallets(g.wb, g.ffId, [{ pallet: p, palletNo }])}>
                                                        {busyPallet ? '…' : '📋 Предзаявка'}
                                                    </button>
                                                )}
                                                <button className="btn btn-secondary btn-sm" style={{ padding: '1px 7px', fontSize: 11 }}
                                                    disabled={busy || palletOpKey != null}
                                                    title="Оставить эту паллету на ФФ — убрать её из предброни, коробы освободятся"
                                                    onClick={() => onReleasePallets(g.wb, g.ffId, [{ pallet: p, palletNo }])}>
                                                    {busyPallet ? '…' : '✂ На ФФ'}
                                                </button>
                                            </span>
                                        </div>
                                    );
                                })}
                                {partials.length > 0 && (
                                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                                        автоматически частичные в бронь не идут — управляйте точечно: «📋 Предзаявка» бронирует паллету, «✂ На ФФ» освобождает, «Дозабить до целой» добирает крупнейшую частичную; галочками можно выбрать несколько паллет — предзаявка уйдёт ОДНОЙ заявкой
                                    </div>
                                )}
                            </div>
                            );
                        })()}
                        <div style={{ overflowX: 'auto' }}>
                            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                                <thead>
                                    <tr style={{ textAlign: 'left', color: 'var(--color-text-muted)', fontSize: 11 }}>
                                        <th style={{ padding: '3px 6px' }}>Артикул</th>
                                        <th style={{ padding: '3px 6px', textAlign: 'right' }}>Коробов</th>
                                        <th style={{ padding: '3px 6px', textAlign: 'right' }}>Шт</th>
                                        <th style={{ padding: '3px 6px' }} />
                                    </tr>
                                </thead>
                                <tbody>
                                    {g.items.map((it, idx) => {
                                        const onlyLoose = it.boxes === 0 && it.looseUnits > 0;
                                        // Почему остаток не добит до короба: свободного стока этого SKU
                                        // на ЭТОМ ФФ меньше гэпа (добор автоматический, когда хватает).
                                        const gap = it.ppb > 0 ? it.ppb - (it.looseUnits % it.ppb) : 0;
                                        const noStock = it.looseUnits > 0 && it.ppb > 0 && it.freeUnits < gap;
                                        const looseTitle = it.ppb > 0
                                            ? (noStock
                                                ? `Целый короб не собирается: короб = ${formatNumber(it.ppb, 0)} шт, до короба не хватает ${formatNumber(gap, 0)}, а свободно на ФФ всего ${formatNumber(it.freeUnits, 0)} шт. Остаток закреплён за направлением: добьётся автоматически при пополнении ФФ, «Оставить так» повезёт его россыпью, ✕ — вернёт на ФФ.`
                                                : `Не кратно коробу — остаток россыпью (короб = ${formatNumber(it.ppb, 0)} шт). Добьётся автоматически при ближайшей синхронизации (свободно ${formatNumber(it.freeUnits, 0)} шт).`)
                                            : 'Кратность короба не задана — остаток россыпью, остаётся на ФФ.';
                                        return (
                                        <tr key={`${it.nm_id}-${idx}`} style={{ borderTop: '1px solid var(--color-border)', background: onlyLoose ? 'rgba(245,158,11,0.06)' : undefined }}>
                                            <td style={{ padding: '5px 6px' }}>
                                                {it.vendor_code || `nm ${it.nm_id}`}
                                                {it.looseUnits > 0 && (
                                                    <span style={{ marginLeft: 6, fontSize: 11, padding: '1px 6px', borderRadius: 5, background: 'rgba(245,158,11,0.15)', color: 'var(--color-warning)' }}
                                                        title={looseTitle}>
                                                        остаток {formatNumber(it.looseUnits, 0)} шт{noStock ? ` · добить нечем (свободно ${formatNumber(it.freeUnits, 0)})` : ''}
                                                    </span>
                                                )}
                                            </td>
                                            <td style={{ padding: '5px 6px', textAlign: 'right', fontWeight: 600, color: onlyLoose ? 'var(--color-text-muted)' : undefined }}>{onlyLoose ? '—' : formatNumber(it.boxes, 0)}</td>
                                            <td style={{ padding: '5px 6px', textAlign: 'right', color: onlyLoose ? 'var(--color-text-muted)' : undefined }}>{onlyLoose ? '—' : formatNumber(it.qty - it.looseUnits, 0)}</td>
                                            <td style={{ padding: '5px 6px', textAlign: 'right' }}>
                                                <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px' }}
                                                    title="Убрать из предброни — коробы останутся на ФФ"
                                                    onClick={() => onDelete(it.nm_id, g.wb, g.pkg)}>✕</button>
                                            </td>
                                        </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    </div>
                );
            })}
        </div>
    );
}
