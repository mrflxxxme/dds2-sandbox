'use client';

/**
 * Карта СПП: по каждой категории — лесенка своих цен и живой СПП на каждой ступени.
 *
 * Источник — часовой снимок витрины (card-API), а не среднее из финотчёта:
 * только он реагирует на сегодняшнюю цену. Красная граница — обрыв, выше
 * которого ВБ перестаёт доплачивать. Строка раскрывается в список артикулов.
 * Колонка «Другие категории» — ориентир по тем же ценам в остальном портфеле:
 * ступени ВБ живут в цене, а не в категории.
 *
 * Оформление — общие токены «Воронки продаж» (`funnelUi`), которая повторяет
 * «Управление рекламой».
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import { MONO } from '../../funnel/components/FunnelTable';
import { CARD_TOOLBAR, TABLE_CARD, thFlat } from '../../funnel/components/funnelUi';
import AdsPeriodPicker from '../../ads-manager/components/AdsPeriodPicker';
import type { SppMapResponse, SppCategory, SppLevel } from '@/types/api';

const money = (n: number | null | undefined) => (n == null ? '—' : formatNumber(n, 0));
const pct = (n: number | null | undefined) => (n == null ? '—' : formatNumber(n, 1) + '%');

/** Цвет ступени: чем ближе СПП к лучшему в категории, тем зеленее. */
const levelColor = (spp: number, best: number) => {
    if (best <= 0) return '#6b7280';
    const share = spp / best;
    return share > 0.95 ? '#059669' : share > 0.6 ? '#d97706' : '#dc2626';
};

/** Числовая ячейка «как в воронке»: моно, tabular-nums, вправо, плотная. */
const num = (extra?: React.CSSProperties): React.CSSProperties => ({
    textAlign: 'right', verticalAlign: 'middle', padding: '3px 10px',
    fontSize: 12, lineHeight: '15px', fontFamily: MONO, fontVariantNumeric: 'tabular-nums',
    whiteSpace: 'nowrap', borderBottom: '1px solid #f3f4f6', ...extra,
});

const STEP_PRESETS = [25, 50, 100, 250, 500, 1000];
const isoToday = () => new Date().toISOString().slice(0, 10);

export default function SppLadder({ dateFrom, dateTo }: { dateFrom: string; dateTo: string }) {
    void dateFrom;
    void dateTo; // карта строится по снимкам витрины — период отчёта на неё не влияет

    const [resp, setResp] = useState<SppMapResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [from, setFrom] = useState(isoToday);
    const [to, setTo] = useState(isoToday);
    const [step, setStep] = useState('100');
    const [observing, setObserving] = useState(false);
    const [openCat, setOpenCat] = useState<string | null>(null);

    const reqRef = useRef(0);
    const load = useCallback(async () => {
        const myReq = ++reqRef.current;
        setLoading(true);
        setError('');
        try {
            const res = await api.getSppMap({
                date_from: from, date_to: to, step: Math.max(10, Number(step) || 100),
            });
            if (reqRef.current !== myReq) return;
            setResp(res);
            setOpenCat((prev) => prev ?? res.categories[0]?.category ?? null);
        } catch (e) {
            if (reqRef.current !== myReq) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (reqRef.current === myReq) setLoading(false);
        }
    }, [from, to, step]);

    useEffect(() => {
        const t = setTimeout(load, 350);
        return () => clearTimeout(t);
    }, [load]);

    const doObserve = async () => {
        setObserving(true);
        try {
            await api.observeSpp(0);
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        } finally {
            setObserving(false);
        }
    };

    const cats = resp?.categories ?? [];
    const stats = resp?.stats;
    const current = useMemo(() => cats.find((c) => c.category === openCat) ?? cats[0], [cats, openCat]);

    const doExport = () =>
        exportToExcel(
            cats.flatMap((c) =>
                c.levels.flatMap((lv) =>
                    lv.items.map((it) => ({
                        'Категория': c.category,
                        'Уровень цены': lv.price,
                        'СПП уровня %': lv.spp,
                        'Артикул': it.vendor_code ?? '',
                        'nm_id': it.nm_id,
                        'Цена до СПП': it.price,
                        'СПП %': it.spp,
                        'Цена клиенту': it.buyer_price,
                        'Обрыв выше': c.cliffs.some((cl) => cl.keep_below === lv.price) ? 'да' : '',
                        'Опустить до': lv.hint_down ? lv.hint_down.price : '',
                        'Цена клиенту станет': lv.hint_down ? lv.hint_down.buyer_price : '',
                        'Поднять до': lv.hint_up ? lv.hint_up.price : '',
                        'Цена клиенту при подъёме': lv.hint_up ? lv.hint_up.buyer_price : '',
                    })),
                ),
            ),
            'spp_map',
        );

    return (
        <div style={{ marginTop: 10 }}>
            <div className="glass-card" style={{ ...TABLE_CARD, marginBottom: 10 }}>
                <div style={{ ...CARD_TOOLBAR, borderBottom: 'none', flexWrap: 'nowrap', overflow: 'hidden' }}>
                    <AdsPeriodPicker from={from} to={to} minWidth={215}
                        onApply={(f, t) => { setFrom(f || isoToday()); setTo(t || isoToday()); }} />
                    <span style={{ fontSize: 12, color: '#6b7280', marginLeft: 6 }}>Шаг сетки</span>
                    <input
                        list="spp-step-presets"
                        value={step}
                        onChange={(e) => setStep(e.target.value.replace(/[^\d]/g, ''))}
                        inputMode="numeric"
                        title="Выберите из списка или впишите своё значение, ₽"
                        style={{
                            width: 92, padding: '5px 8px', borderRadius: 8, fontSize: 12.5,
                            border: '1px solid var(--color-border)', background: '#fff',
                            fontFamily: MONO, fontVariantNumeric: 'tabular-nums',
                        }}
                    />
                    <datalist id="spp-step-presets">
                        {STEP_PRESETS.map((s) => <option key={s} value={s} />)}
                    </datalist>

                    {stats && (
                        <span style={{ fontSize: 11.5, color: '#9ca3af', marginLeft: 6, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            {formatNumber(stats.categories_count, 0)} категорий · {formatNumber(stats.points, 0)} точек
                            {stats.last_snapshot_on ? ` · последний срез ${stats.last_snapshot_on}` : ' · срезов за период нет'}
                        </span>
                    )}

                    <div style={{ flex: 1 }} />
                    <button className="btn btn-sm btn-secondary" onClick={doExport} disabled={!cats.length}>Excel</button>
                    <button className="btn btn-sm btn-primary" onClick={doObserve} disabled={observing}>
                        {observing ? 'Снимаю…' : 'Снять срез'}
                    </button>
                </div>
            </div>

            {error && <div style={{ padding: 16, color: 'var(--color-danger)' }}>{error}</div>}
            {loading && !resp && <div style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка…</div>}
            {!error && !loading && !cats.length && (
                <div style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                    Срезов ещё нет — нажмите «Снять срез».
                </div>
            )}

            {!!cats.length && (
                <div className="glass-card" style={{ ...TABLE_CARD, flexDirection: 'row', maxHeight: 720 }}>
                    <div style={{ width: 236, borderRight: '1px solid #e5e7eb', overflowY: 'auto', flexShrink: 0, background: '#f9fafb' }}>
                        {cats.map((c) => {
                            const on = c.category === current?.category;
                            return (
                                <button key={c.category} type="button" onClick={() => setOpenCat(c.category)}
                                    style={{
                                        display: 'block', width: '100%', textAlign: 'left', border: 'none', cursor: 'pointer',
                                        padding: '6px 12px', fontSize: 12.5, whiteSpace: 'nowrap', overflow: 'hidden',
                                        textOverflow: 'ellipsis', borderBottom: '1px solid #f3f4f6',
                                        background: on ? '#fff' : 'transparent',
                                        color: on ? '#1e3a8a' : '#374151', fontWeight: on ? 600 : 500,
                                        borderLeft: on ? '3px solid var(--color-accent)' : '3px solid transparent',
                                    }}>
                                    {c.category}
                                    <span style={{ color: '#9ca3af', fontFamily: MONO, fontSize: 11 }}> {c.nm_count}</span>
                                    {!!c.cliffs.length && (
                                        <span style={{ color: '#dc2626', fontFamily: MONO, fontSize: 11 }}> · {c.cliffs.length}</span>
                                    )}
                                </button>
                            );
                        })}
                    </div>
                    {current && <CategoryLadder cat={current} />}
                </div>
            )}
        </div>
    );
}

function Hint({ level }: { level: SppLevel }) {
    const d = level.hint_down;
    const u = level.hint_up;
    if (!d && !u) return <span style={{ color: '#d1d5db' }}>—</span>;
    return (
        <span style={{ fontSize: 11.5, display: 'inline-flex', gap: 10, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
            {d && (
                <span style={{ color: '#059669' }}
                    title={`Опустить цену на ${money(level.price - d.price)} ₽ — клиент сэкономит ${money(d.gain)} ₽ (рычаг ×${formatNumber(d.leverage ?? 0, 1)}). По данным: ${d.categories.join(', ')}`}>
                    опустить до <b>{money(d.price)} ₽</b> → клиенту {money(d.buyer_price)} ₽
                </span>
            )}
            {u && (
                <span style={{ color: '#1e3a8a' }}
                    title={`Поднять цену на ${money(u.gain)} ₽ — цена клиента почти не изменится (${u.buyer_delta >= 0 ? '+' : ''}${money(u.buyer_delta)} ₽). По данным: ${u.categories.join(', ')}`}>
                    поднять до <b>{money(u.price)} ₽</b> → клиенту {money(u.buyer_price)} ₽
                </span>
            )}
        </span>
    );
}

function CategoryLadder({ cat }: { cat: SppCategory }) {
    const best = Math.max(...cat.levels.map((l) => l.spp), 0);
    const cliffAbove = new Set(cat.cliffs.map((c) => c.keep_below));
    const [open, setOpen] = useState<Set<number>>(new Set());
    const toggle = (price: number) =>
        setOpen((prev) => {
            const n = new Set(prev);
            if (n.has(price)) n.delete(price);
            else n.add(price);
            return n;
        });

    return (
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, background: '#fff' }}>
                    <thead>
                        <tr>
                            <th style={{ ...thFlat, textAlign: 'left', paddingLeft: 14 }}>{cat.category}</th>
                            <th style={thFlat}>СПП</th>
                            <th style={thFlat}>Цена клиенту</th>
                            <th style={thFlat}>Разброс</th>
                            <th style={{ ...thFlat, textAlign: 'right' }}>Что можно сделать</th>
                            <th style={{ ...thFlat, paddingRight: 14 }}>Артикулов</th>
                        </tr>
                    </thead>
                    <tbody>
                        {cat.levels.map((lv) => {
                            const isOpen = open.has(lv.price);
                            const cliff = cliffAbove.has(lv.price);
                            const edge = cliff ? '2px solid #dc2626' : '1px solid #f3f4f6';
                            return (
                                <React.Fragment key={lv.price}>
                                    <tr onClick={() => toggle(lv.price)}
                                        style={{ cursor: 'pointer', background: isOpen ? '#f8fafc' : undefined }}>
                                        <td style={num({ textAlign: 'left', paddingLeft: 14, fontWeight: 700, fontSize: 12.5, borderBottom: edge })}>
                                            <span style={{ color: '#9ca3af', marginRight: 6 }}>{isOpen ? '▾' : '▸'}</span>
                                            {money(lv.price)} ₽
                                        </td>
                                        <td style={num({ fontWeight: 700, color: levelColor(lv.spp, best), borderBottom: edge })}>{pct(lv.spp)}</td>
                                        <td style={num({ borderBottom: edge })}>{money(lv.buyer_price)} ₽</td>
                                        <td style={num({ color: '#9ca3af', borderBottom: edge })}>
                                            {lv.spp_max - lv.spp_min > 0.2 ? `${pct(lv.spp_min)} – ${pct(lv.spp_max)}` : '—'}
                                        </td>
                                        <td style={num({ borderBottom: edge, fontFamily: 'inherit', whiteSpace: 'normal', maxWidth: 360 })}><Hint level={lv} /></td>
                                        <td style={num({ color: '#6b7280', paddingRight: 14, borderBottom: edge })}>{lv.n}</td>
                                    </tr>
                                    {isOpen && lv.items.map((it) => (
                                        <tr key={it.nm_id} style={{ background: '#fcfcfd' }}>
                                            <td style={num({ textAlign: 'left', paddingLeft: 34, fontFamily: 'inherit', fontSize: 11.5 })}>
                                                <a href={`https://www.wildberries.ru/catalog/${it.nm_id}/detail.aspx`}
                                                    target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}
                                                    onClick={(e) => e.stopPropagation()}>
                                                    {it.vendor_code || it.nm_id}
                                                </a>
                                                <span style={{ color: '#9ca3af', fontFamily: MONO }}> {it.nm_id}</span>
                                            </td>
                                            <td style={num({ fontSize: 11.5, color: levelColor(it.spp, best), fontWeight: 600 })}>{pct(it.spp)}</td>
                                            <td style={num({ fontSize: 11.5 })}>{money(it.buyer_price)} ₽</td>
                                            <td style={num({ fontSize: 11.5, color: '#9ca3af' })}>{money(it.price)} ₽</td>
                                            <td />
                                            <td style={num({ paddingRight: 14 })} />
                                        </tr>
                                    ))}
                                </React.Fragment>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
