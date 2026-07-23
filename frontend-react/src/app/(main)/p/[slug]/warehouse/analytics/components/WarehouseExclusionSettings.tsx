'use client';
import { useState, useEffect } from 'react';
import { api } from '@/lib/api';
import type { PalletCategoryCompat } from '@/types/api';

export function WarehouseExclusionSettings() {
    const [warehouses, setWarehouses] = useState<Array<{ name: string; is_sorting_center?: boolean }>>([]);
    const [excluded, setExcluded] = useState<string[]>([]);
    const [preorderAllowed, setPreorderAllowed] = useState<string[]>([]);
    // 🔥 сгоревшие/потерянные склады: остатки не учитываются в расчётах; независим от excluded
    const [stockIgnored, setStockIgnored] = useState<string[]>([]);
    const [compat, setCompat] = useState<PalletCategoryCompat>({ enabled: false, groups: [] });
    const [compatError, setCompatError] = useState('');
    const [allCategories, setAllCategories] = useState<string[]>([]);
    const [rfDefaultDays, setRfDefaultDays] = useState<number>(8);
    const [rfDefaultDaysSaved, setRfDefaultDaysSaved] = useState<number>(8);
    const [savingRf, setSavingRf] = useState(false);
    const [rfMsg, setRfMsg] = useState('');
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [msg, setMsg] = useState('');
    const [hasChanges, setHasChanges] = useState(false);

    useEffect(() => {
        (async () => {
            try {
                const [wh, ex, pa, si, rf, compatRes, cats] = await Promise.all([
                    api.getAllWbWarehouses(),
                    api.getExcludedWarehouses(),
                    api.getPreorderAllowedWarehouses(),
                    // best-effort: в окне деплой-скью нового эндпоинта 404 не должен ронять весь экран
                    api.getStockIgnoredWarehouses().catch(() => [] as string[]),
                    api.getForecastRfDefaultDays(),
                    // best-effort: сбой правил/списка категорий не должен ломать остальные секции
                    api.getPalletCategoryCompat().catch(() => null),
                    Promise.all([
                        api.getBoxMultiplicity()
                            .then(r => r.items.map(i => (i.subject || '').trim()).filter(Boolean))
                            .catch(() => [] as string[]),
                        api.getCategoryOverrides()
                            .then(o => Object.values(o).map(v => (v || '').trim()).filter(Boolean))
                            .catch(() => [] as string[]),
                    ]).then(([subjects, overrides]) =>
                        Array.from(new Set([...subjects, ...overrides])).sort((a, b) => a.localeCompare(b, 'ru'))
                    ),
                ]);
                setWarehouses(wh);
                setExcluded(ex);
                setPreorderAllowed(pa);
                setStockIgnored(si);
                setRfDefaultDays(rf.days);
                setRfDefaultDaysSaved(rf.days);
                if (compatRes) {
                    setCompat(compatRes);
                } else {
                    setCompatError('Не удалось загрузить правила совместимости категорий');
                }
                setAllCategories(cats);
            } catch {
                setMsg('Ошибка загрузки складов');
            } finally {
                setLoading(false);
            }
        })();
    }, []);

    const saveRfDefaultDays = async () => {
        setSavingRf(true);
        setRfMsg('');
        try {
            const { days } = await api.setForecastRfDefaultDays(rfDefaultDays);
            setRfDefaultDaysSaved(days);
            setRfDefaultDays(days);
            setRfMsg('✅ Сохранено');
        } catch {
            setRfMsg('❌ Ошибка сохранения');
        } finally {
            setSavingRf(false);
        }
    };

    const toggle = (name: string) => {
        setExcluded(prev => {
            const next = prev.includes(name) ? prev.filter(n => n !== name) : [...prev, name];
            return next;
        });
        setHasChanges(true);
    };

    const togglePreorder = (name: string) => {
        setPreorderAllowed(prev => {
            const next = prev.includes(name) ? prev.filter(n => n !== name) : [...prev, name];
            return next;
        });
        setHasChanges(true);
    };

    const toggleStockIgnored = (name: string) => {
        setStockIgnored(prev => (prev.includes(name) ? prev.filter(n => n !== name) : [...prev, name]));
        setHasChanges(true);
    };

    // ─── Совместимость категорий на паллете ───────────────────────────
    const mutateCompat = (updater: (prev: PalletCategoryCompat) => PalletCategoryCompat) => {
        setCompat(updater);
        setHasChanges(true);
    };

    const toggleCompatEnabled = () => mutateCompat(prev => ({ ...prev, enabled: !prev.enabled }));

    const addCompatGroup = () => mutateCompat(prev => ({
        ...prev,
        groups: [...prev.groups, { name: null, categories: [] }],
    }));

    const removeCompatGroup = (idx: number) => mutateCompat(prev => ({
        ...prev,
        groups: prev.groups.filter((_, i) => i !== idx),
    }));

    const renameCompatGroup = (idx: number, name: string) => mutateCompat(prev => ({
        ...prev,
        groups: prev.groups.map((g, i) => (i === idx ? { ...g, name: name || null } : g)),
    }));

    const addCompatCategory = (idx: number, cat: string) => {
        if (!cat) return;
        mutateCompat(prev => ({
            ...prev,
            groups: prev.groups.map((g, i) =>
                i === idx && !g.categories.includes(cat) ? { ...g, categories: [...g.categories, cat] } : g
            ),
        }));
    };

    const removeCompatCategory = (idx: number, cat: string) => mutateCompat(prev => ({
        ...prev,
        groups: prev.groups.map((g, i) => (i === idx ? { ...g, categories: g.categories.filter(c => c !== cat) } : g)),
    }));

    const save = async () => {
        setSaving(true);
        setMsg('');
        try {
            const [, , , savedCompat] = await Promise.all([
                api.setExcludedWarehouses(excluded),
                api.setPreorderAllowedWarehouses(preorderAllowed),
                api.setStockIgnoredWarehouses(stockIgnored),
                // не перезаписываем правила дефолтом, если их не удалось загрузить
                compatError ? Promise.resolve(null) : api.setPalletCategoryCompat(compat),
            ]);
            if (savedCompat) {
                // сервер нормализует (trim, выкидывает пустые группы) — синхронизируем стейт
                setCompat({ enabled: savedCompat.enabled, groups: savedCompat.groups });
            }
            setMsg('✅ Сохранено');
            setHasChanges(false);
        } catch {
            setMsg('❌ Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    if (loading) return <div className="glass-card" style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-muted)' }}>Загрузка складов...</div>;

    const active = warehouses.filter(w => !excluded.includes(w.name));
    const excludedList = warehouses.filter(w => excluded.includes(w.name));

    return (
        <div>
            <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                <h3 style={{ margin: '0 0 8px' }}>📦 Время РФ → WB по умолчанию</h3>
                <p style={{ color: 'var(--color-text-muted)', margin: '0 0 16px', fontSize: 14 }}>
                    Используется в прогнозе остатков, если у склада не заполнена вкладка «Время доставки» (сборка + доставка + приёмка WB). Текущее значение применяется ко всем фулфилмент-складам без расписания.
                </p>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14 }}>
                        <span>Дней до прибытия на WB:</span>
                        <input
                            type="number"
                            min={0}
                            max={365}
                            value={rfDefaultDays}
                            onChange={(e) => setRfDefaultDays(Math.max(0, Math.min(365, parseInt(e.target.value || '0', 10))))}
                            style={{
                                width: 80,
                                padding: '8px 12px',
                                borderRadius: 8,
                                border: '1px solid var(--color-border)',
                                background: 'var(--color-bg-card)',
                                color: 'var(--color-text)',
                                fontSize: 14,
                            }}
                        />
                    </label>
                    <button
                        className="btn btn-primary btn-sm"
                        onClick={saveRfDefaultDays}
                        disabled={savingRf || rfDefaultDays === rfDefaultDaysSaved}
                    >
                        {savingRf ? 'Сохранение...' : '💾 Сохранить'}
                    </button>
                    {rfMsg && <span style={{ fontSize: 14 }}>{rfMsg}</span>}
                </div>
            </div>

            <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                <h3 style={{ margin: '0 0 8px' }}>🏭 Исключение складов</h3>
                <p style={{ color: 'var(--color-text-muted)', margin: '0 0 16px', fontSize: 14 }}>
                    Исключённые склады не участвуют в расчёте потребностей. Заказы из их регионов перераспределяются на ближайшие оставшиеся склады.
                    {' '}Тумблер 🔥 — отдельный флаг «остатки не учитывать» (склад сгорел/потерян, но WB продолжает отдавать его остатки как живые):
                    расчёты (потребность/запас/срочность) перестают верить остаткам такого склада, а страницы «факта»
                    (Остатки по складам WB, Сводные остатки) показывают их как есть.
                </p>

                <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                        Активных: <strong>{active.length}</strong> / {warehouses.length}
                    </span>
                    {excluded.length > 0 && (
                        <span style={{ fontSize: 13, color: 'var(--color-danger)', fontWeight: 500 }}>
                            ⛔ Исключено: {excluded.length}
                        </span>
                    )}
                    {stockIgnored.length > 0 && (
                        <span style={{ fontSize: 13, color: '#f97316', fontWeight: 500 }}>
                            🔥 Остатки игнорируются: {stockIgnored.length}
                        </span>
                    )}
                    <span style={{ flex: 1 }} />
                    <button
                        className="btn btn-secondary btn-sm"
                        type="button"
                        onClick={() => {
                            const scNames = warehouses
                                .filter(w => w.is_sorting_center || w.name.startsWith('СЦ '))
                                .map(w => w.name);
                            const allExcluded = scNames.length > 0 && scNames.every(n => excluded.includes(n));
                            if (allExcluded) {
                                setExcluded(prev => prev.filter(n => !scNames.includes(n)));
                            } else {
                                setExcluded(prev => Array.from(new Set([...prev, ...scNames])));
                            }
                            setHasChanges(true);
                        }}
                        title="Сортировочные центры (СЦ) — это транзитные пункты WB, на них нельзя долго хранить запас"
                    >
                        🛂 Все СЦ {(() => {
                            const scNames = warehouses.filter(w => w.is_sorting_center || w.name.startsWith('СЦ ')).map(w => w.name);
                            const allExcluded = scNames.length > 0 && scNames.every(n => excluded.includes(n));
                            return allExcluded ? '— включить' : '— исключить';
                        })()}
                    </button>
                </div>

                <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
                    gap: 8,
                }}>
                    {warehouses.map(w => {
                        const isExcluded = excluded.includes(w.name);
                        const isStockIgnored = stockIgnored.includes(w.name);
                        return (
                            <label
                                key={w.name}
                                style={{
                                    display: 'flex', alignItems: 'center', gap: 8,
                                    padding: '8px 12px',
                                    borderRadius: 8,
                                    border: `1px solid ${isExcluded ? 'var(--color-danger)' : 'var(--color-border)'}`,
                                    background: isExcluded ? 'rgba(239,68,68,0.08)' : 'var(--color-bg-card)',
                                    cursor: 'pointer',
                                    transition: 'all 0.15s',
                                    opacity: isExcluded ? 0.7 : 1,
                                }}
                            >
                                <input
                                    type="checkbox"
                                    checked={!isExcluded}
                                    onChange={() => toggle(w.name)}
                                    style={{ accentColor: 'var(--color-accent)' }}
                                />
                                <span style={{
                                    fontSize: 14,
                                    textDecoration: isExcluded ? 'line-through' : 'none',
                                    color: isExcluded ? 'var(--color-danger)' : 'var(--color-text)',
                                }}>
                                    {w.name}
                                </span>
                                <button
                                    type="button"
                                    // карточка — <label>: без preventDefault клик по кнопке флипнет excluded-чекбокс
                                    onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggleStockIgnored(w.name); }}
                                    title="Остатки не учитывать (сгорел/потерян)"
                                    aria-pressed={isStockIgnored}
                                    style={{
                                        marginLeft: 'auto',
                                        padding: '2px 6px',
                                        borderRadius: 6,
                                        border: `1px solid ${isStockIgnored ? '#f97316' : 'transparent'}`,
                                        background: isStockIgnored ? 'rgba(249,115,22,0.12)' : 'transparent',
                                        cursor: 'pointer',
                                        fontSize: 14,
                                        lineHeight: 1,
                                        opacity: isStockIgnored ? 1 : 0.35,
                                        transition: 'all 0.15s',
                                    }}
                                >
                                    🔥
                                </button>
                            </label>
                        );
                    })}
                </div>
            </div>

            <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                <h3 style={{ margin: '0 0 8px' }}>⌛ Предзаявка без лимита — разрешённые склады</h3>
                <p style={{ color: 'var(--color-text-muted)', margin: '0 0 16px', fontSize: 14 }}>
                    Склад без приёмочного лимита WB (⌛, нет ни одного дня приёмки по box/mono/super) по умолчанию <strong>исключается</strong> из расчёта потребности и новинок — его qty уходит на ближайший открытый склад. Отметьте склады, на которые предзаявку без лимита делать <strong>можно</strong> — они останутся в расчёте.
                </p>

                <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                        Разрешено под предзаявку: <strong>{preorderAllowed.length}</strong> / {warehouses.length}
                    </span>
                </div>

                <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
                    gap: 8,
                }}>
                    {warehouses.map(w => {
                        const isAllowed = preorderAllowed.includes(w.name);
                        return (
                            <label
                                key={w.name}
                                style={{
                                    display: 'flex', alignItems: 'center', gap: 8,
                                    padding: '8px 12px',
                                    borderRadius: 8,
                                    border: `1px solid ${isAllowed ? 'var(--color-success)' : 'var(--color-border)'}`,
                                    background: isAllowed ? 'rgba(34,197,94,0.08)' : 'var(--color-bg-card)',
                                    cursor: 'pointer',
                                    transition: 'all 0.15s',
                                }}
                            >
                                <input
                                    type="checkbox"
                                    checked={isAllowed}
                                    onChange={() => togglePreorder(w.name)}
                                    style={{ accentColor: 'var(--color-success)' }}
                                />
                                <span style={{
                                    fontSize: 14,
                                    color: isAllowed ? 'var(--color-success)' : 'var(--color-text)',
                                    fontWeight: isAllowed ? 500 : 400,
                                }}>
                                    {w.name}
                                </span>
                            </label>
                        );
                    })}
                </div>
            </div>

            <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                <h3 style={{ margin: '0 0 8px' }}>🧩 Совместимость категорий на паллете</h3>
                <p style={{ color: 'var(--color-text-muted)', margin: '0 0 16px', fontSize: 14 }}>
                    По умолчанию (при включённых правилах) каждая категория едет отдельной паллетой.
                    Группа разрешает перечисленным категориям грузиться на одну.
                    Действует только на короб; моно-паллеты не затрагиваются.
                </p>

                {compatError ? (
                    <p style={{ color: 'var(--color-danger)', fontSize: 14, margin: 0 }}>⚠️ {compatError}</p>
                ) : (
                    <>
                        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, cursor: 'pointer', width: 'fit-content' }}>
                            <input
                                type="checkbox"
                                checked={compat.enabled}
                                onChange={toggleCompatEnabled}
                                style={{ accentColor: 'var(--color-accent)' }}
                            />
                            <span style={{ fontWeight: 500 }}>Применять правила</span>
                        </label>
                        {!compat.enabled && (
                            <p style={{ color: 'var(--color-text-muted)', fontSize: 13, margin: '6px 0 0' }}>
                                выключено: любые категории миксуются на паллете (как раньше)
                            </p>
                        )}

                        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 16 }}>
                            {compat.groups.length === 0 && (
                                <p style={{ color: 'var(--color-text-muted)', fontSize: 13, margin: 0 }}>
                                    Групп нет — при включённых правилах каждая категория едет отдельной паллетой.
                                </p>
                            )}
                            {compat.groups.map((g, idx) => {
                                const usedElsewhere = new Set(compat.groups.flatMap((og, oi) => (oi === idx ? [] : og.categories)));
                                const available = allCategories.filter(c => !usedElsewhere.has(c) && !g.categories.includes(c));
                                return (
                                    <div
                                        key={idx}
                                        style={{
                                            border: '1px solid var(--color-border)',
                                            borderRadius: 12,
                                            padding: 12,
                                            background: 'var(--color-bg-card)',
                                        }}
                                    >
                                        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
                                            <input
                                                type="text"
                                                value={g.name ?? ''}
                                                placeholder={`Группа ${idx + 1}`}
                                                onChange={(e) => renameCompatGroup(idx, e.target.value)}
                                                style={{
                                                    flex: 1,
                                                    minWidth: 120,
                                                    padding: '6px 10px',
                                                    borderRadius: 8,
                                                    border: '1px solid var(--color-border)',
                                                    background: 'var(--color-bg-card)',
                                                    color: 'var(--color-text)',
                                                    fontSize: 14,
                                                }}
                                            />
                                            <button
                                                className="btn btn-danger btn-sm"
                                                type="button"
                                                onClick={() => removeCompatGroup(idx)}
                                                title="Удалить группу"
                                            >
                                                🗑 Удалить
                                            </button>
                                        </div>
                                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
                                            {g.categories.length === 0 && (
                                                <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                                    Категории не выбраны — пустая группа не сохранится
                                                </span>
                                            )}
                                            {g.categories.map(cat => (
                                                <span
                                                    key={cat}
                                                    className="badge badge-info"
                                                    style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
                                                >
                                                    {cat}
                                                    <button
                                                        type="button"
                                                        onClick={() => removeCompatCategory(idx, cat)}
                                                        aria-label={`Убрать категорию ${cat}`}
                                                        style={{
                                                            background: 'none',
                                                            border: 'none',
                                                            padding: 0,
                                                            cursor: 'pointer',
                                                            color: 'inherit',
                                                            fontSize: 12,
                                                            lineHeight: 1,
                                                        }}
                                                    >
                                                        ✕
                                                    </button>
                                                </span>
                                            ))}
                                        </div>
                                        <select
                                            value=""
                                            onChange={(e) => addCompatCategory(idx, e.target.value)}
                                            disabled={available.length === 0}
                                            style={{
                                                padding: '6px 10px',
                                                borderRadius: 8,
                                                border: '1px solid var(--color-border)',
                                                background: 'var(--color-bg-card)',
                                                color: 'var(--color-text)',
                                                fontSize: 13,
                                                maxWidth: '100%',
                                            }}
                                        >
                                            <option value="">
                                                {allCategories.length === 0
                                                    ? 'Список категорий недоступен'
                                                    : available.length === 0
                                                        ? 'Все категории уже распределены'
                                                        : '+ добавить категорию'}
                                            </option>
                                            {available.map(c => (
                                                <option key={c} value={c}>{c}</option>
                                            ))}
                                        </select>
                                    </div>
                                );
                            })}
                            <div>
                                <button className="btn btn-secondary btn-sm" type="button" onClick={addCompatGroup}>
                                    + Новая группа
                                </button>
                            </div>
                        </div>
                    </>
                )}
            </div>

            <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                <button
                    className="btn btn-primary"
                    onClick={save}
                    disabled={saving || !hasChanges}
                >
                    {saving ? 'Сохранение...' : '💾 Сохранить'}
                </button>
                {msg && <span style={{ fontSize: 14 }}>{msg}</span>}
            </div>

            {(excludedList.length > 0 || stockIgnored.length > 0) && (
                <div className="glass-card" style={{ padding: 16, marginTop: 16 }}>
                    {excludedList.length > 0 && (
                        <p style={{ fontSize: 13, color: 'var(--color-text-muted)', margin: 0 }}>
                            ⚠️ Исключены: <strong>{excludedList.map(w => w.name).join(', ')}</strong>.
                            Заказы из этих регионов будут распределены на ближайшие активные склады.
                        </p>
                    )}
                    {stockIgnored.length > 0 && (
                        <p style={{ fontSize: 13, color: 'var(--color-text-muted)', margin: excludedList.length > 0 ? '8px 0 0' : 0 }}>
                            🔥 Остатки не учитываются: <strong>{stockIgnored.join(', ')}</strong>.
                            Расчёты (потребность/запас/срочность) считают остатки этих складов нулевыми;
                            страницы «факта» показывают их как есть.
                        </p>
                    )}
                </div>
            )}
        </div>
    );
}
