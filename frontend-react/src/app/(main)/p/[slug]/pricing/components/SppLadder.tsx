'use client';

/**
 * Карта СПП: по каждой категории — лесенка своих цен и живой СПП на каждой ступени.
 *
 * Источник — снимок витрины (card-API), а не среднее из финотчёта: только он
 * реагирует на сегодняшнюю цену. Красная граница — ОБРЫВ: выше него ВБ
 * перестаёт доплачивать. Строка раскрывается в список артикулов уровня —
 * разброс СПП на одной цене видно поимённо.
 *
 * Оформление — общие токены раздела «Воронка продаж» (`funnelUi`), который сам
 * повторяет «Управление рекламой»: тёмная липкая шапка, тулбар-полоса внутри
 * карточки, моноширинные цифры, сегментированные переключатели.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import { MONO } from '../../funnel/components/FunnelTable';
import {
    CARD_FOOTER,
    CARD_TOOLBAR,
    Segmented,
    StatCard,
    TABLE_CARD,
    thFlat,
} from '../../funnel/components/funnelUi';
import type { SppMapResponse, SppCategory } from '@/types/api';

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

const DAYS = [
    { key: '1', label: 'Сегодня' },
    { key: '3', label: '3 дня' },
    { key: '7', label: 'Неделя' },
    { key: '30', label: 'Месяц' },
] as const;
const STEPS = [
    { key: '50', label: '50 ₽' },
    { key: '100', label: '100 ₽' },
    { key: '250', label: '250 ₽' },
    { key: '500', label: '500 ₽' },
] as const;

export default function SppLadder({ dateFrom, dateTo }: { dateFrom: string; dateTo: string }) {
    void dateFrom;
    void dateTo; // карта строится по снимкам витрины — период отчёта на неё не влияет

    const [resp, setResp] = useState<SppMapResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [days, setDays] = useState('1');
    const [step, setStep] = useState('100');
    const [observing, setObserving] = useState(false);
    const [obsMsg, setObsMsg] = useState('');
    const [openCat, setOpenCat] = useState<string | null>(null);

    const reqRef = useRef(0);
    const load = useCallback(async () => {
        const myReq = ++reqRef.current;
        setLoading(true);
        setError('');
        try {
            const res = await api.getSppMap({ days: Number(days), step: Number(step) });
            if (reqRef.current !== myReq) return;
            setResp(res);
            setOpenCat((prev) => prev ?? res.categories[0]?.category ?? null);
        } catch (e) {
            if (reqRef.current !== myReq) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (reqRef.current === myReq) setLoading(false);
        }
    }, [days, step]);

    useEffect(() => {
        const t = setTimeout(load, 250);
        return () => clearTimeout(t);
    }, [load]);

    const doObserve = async () => {
        setObserving(true);
        setObsMsg('');
        try {
            const r = await api.observeSpp(0);
            setObsMsg(
                `снято ${r.snapshot.written} точек из ${r.snapshot.requested}` +
                    (r.snapshot.stale ? ` · ${r.snapshot.stale} пропущено: витрина ушла вперёд синка цен` : ''),
            );
            await load();
        } catch (e) {
            setObsMsg(e instanceof Error ? e.message : 'Ошибка');
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
                        'Клиент платит': it.buyer_price,
                        'Обрыв выше': c.cliffs.some((cl) => cl.keep_below === lv.price) ? 'да' : '',
                    })),
                ),
            ),
            'spp_map',
        );

    return (
        <div style={{ marginTop: 14 }}>
            <div className="glass-card" style={{ ...TABLE_CARD, marginBottom: 14 }}>
                <div style={CARD_TOOLBAR}>
                    <span style={{ fontSize: 12, color: '#6b7280' }}>Снимки за</span>
                    <Segmented value={days} options={DAYS.map((d) => ({ key: d.key, label: d.label }))} onChange={setDays} compact />
                    <span style={{ fontSize: 12, color: '#6b7280', marginLeft: 8 }}>Шаг сетки</span>
                    <Segmented value={step} options={STEPS.map((s) => ({ key: s.key, label: s.label }))} onChange={setStep} compact />
                    <div style={{ flex: 1 }} />
                    <button className="btn btn-sm btn-secondary" onClick={doExport} disabled={!cats.length}>📥 Excel</button>
                    <button className="btn btn-sm btn-primary" onClick={doObserve} disabled={observing}
                        title="Опросить витрину и записать СПП по всем артикулам прямо сейчас">
                        {observing ? '⏳ Снимаю…' : '🔄 Снять срез сейчас'}
                    </button>
                </div>

                <div style={{ padding: '10px 16px', fontSize: 12.5, lineHeight: 1.5, color: '#374151' }}>
                    <b>Как это читать.</b> Для каждой категории — уровни цен, на которых реально стоят наши товары, и СПП,
                    который ВБ даёт на каждом уровне <b>сейчас</b> (опрос витрины, не среднее из финотчёта). Красная
                    граница — <b>обрыв</b>: выше неё ВБ резко перестаёт доплачивать. Сравнивать цены имеет смысл только
                    внутри одной категории. Разброс в строке значит, что на одном уровне СПП у товаров разный, —
                    разверните строку и увидите, у каких именно.
                </div>

                {obsMsg && <div style={CARD_FOOTER}>{obsMsg}</div>}
            </div>

            {stats && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 8, marginBottom: 14 }}>
                    <StatCard label="Категорий" value={formatNumber(stats.categories_count, 0)} color="#1e3a8a"
                        hint={<div style={{ fontSize: 11, color: '#6b7280' }}>с обрывами {stats.with_cliffs}</div>} />
                    <StatCard label="Точек в срезе" value={formatNumber(stats.points, 0)} color="#374151" />
                    <StatCard label="Последний срез" value={stats.last_snapshot_on ?? '—'} color="#059669"
                        hint={<div style={{ fontSize: 11, color: '#6b7280' }}>{stats.source === 'card' ? 'витрина' : 'заказы'}</div>} />
                </div>
            )}

            {error && <div style={{ padding: 16, color: 'var(--color-danger)' }}>❌ {error}</div>}
            {loading && !resp && <div style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка…</div>}
            {!error && !loading && !cats.length && (
                <div style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                    🪜 Срезов ещё нет — нажмите «Снять срез сейчас».
                </div>
            )}

            {!!cats.length && (
                <div className="glass-card" style={{ ...TABLE_CARD, flexDirection: 'row', maxHeight: 660 }}>
                    <div style={{ width: 250, borderRight: '1px solid #e5e7eb', overflowY: 'auto', flexShrink: 0, background: '#f9fafb' }}>
                        {cats.map((c) => {
                            const on = c.category === current?.category;
                            return (
                                <button key={c.category} type="button" onClick={() => setOpenCat(c.category)}
                                    style={{
                                        display: 'block', width: '100%', textAlign: 'left', border: 'none', cursor: 'pointer',
                                        padding: '7px 12px', fontSize: 12.5, whiteSpace: 'nowrap', overflow: 'hidden',
                                        textOverflow: 'ellipsis', borderBottom: '1px solid #f3f4f6',
                                        background: on ? '#fff' : 'transparent',
                                        color: on ? '#1e3a8a' : '#374151', fontWeight: on ? 600 : 500,
                                        borderLeft: on ? '3px solid var(--color-accent)' : '3px solid transparent',
                                    }}>
                                    {c.category}
                                    <span style={{ color: '#9ca3af', fontFamily: MONO, fontSize: 11 }}> {c.nm_count}</span>
                                    {!!c.cliffs.length && <span style={{ color: '#dc2626', fontWeight: 700 }}> ⚠{c.cliffs.length}</span>}
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
            <div style={{ ...CARD_TOOLBAR, gap: 8 }}>
                <span style={{ fontSize: 14, fontWeight: 700 }}>{cat.category}</span>
                <span style={{ fontSize: 11.5, color: '#6b7280' }}>{cat.nm_count} артикулов в срезе</span>
            </div>

            {!!cat.cliffs.length && (
                <div style={{ padding: '8px 16px 2px', display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {cat.cliffs.map((c) => (
                        <div key={c.breaks_at}
                            style={{
                                padding: '6px 10px', fontSize: 12.5, lineHeight: 1.45, borderRadius: 8,
                                background: '#fef2f2', borderLeft: '3px solid #dc2626', color: '#374151',
                            }}>
                            Держать цену <b>≤ {money(c.keep_below)} ₽</b>: там СПП {pct(c.spp_below)}, а уже на{' '}
                            {money(c.breaks_at)} ₽ — {pct(c.spp_above)} (−{formatNumber(c.drop, 1)} п.п.).
                            {c.leverage != null && (
                                <> Уступаем {money(c.seller_gives)} ₽ — клиент выигрывает{' '}
                                    <b style={{ color: '#059669' }}>{money(c.buyer_gains)} ₽</b>, рычаг ×{formatNumber(c.leverage, 1)}.</>
                            )}
                        </div>
                    ))}
                </div>
            )}

            <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: '8px 0 0' }}>
                <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, background: '#fff' }}>
                    <thead>
                        <tr>
                            <th style={{ ...thFlat, textAlign: 'left', paddingLeft: 16 }}>Цена до СПП</th>
                            <th style={thFlat}>СПП</th>
                            <th style={thFlat}>Клиент платит</th>
                            <th style={thFlat}>Разброс СПП</th>
                            <th style={{ ...thFlat, paddingRight: 16 }}>Артикулов</th>
                        </tr>
                    </thead>
                    <tbody>
                        {cat.levels.map((lv) => {
                            const isOpen = open.has(lv.price);
                            const cliff = cliffAbove.has(lv.price);
                            return (
                                <React.Fragment key={lv.price}>
                                    <tr onClick={() => toggle(lv.price)}
                                        style={{ cursor: 'pointer', background: isOpen ? '#f8fafc' : undefined }}>
                                        <td style={num({
                                            textAlign: 'left', paddingLeft: 16, fontWeight: 700, fontSize: 12.5,
                                            borderBottom: cliff ? '2px solid #dc2626' : '1px solid #f3f4f6',
                                        })}>
                                            <span style={{ color: '#9ca3af', marginRight: 6 }}>{isOpen ? '▾' : '▸'}</span>
                                            {money(lv.price)} ₽
                                        </td>
                                        <td style={num({
                                            fontWeight: 700, color: levelColor(lv.spp, best),
                                            borderBottom: cliff ? '2px solid #dc2626' : '1px solid #f3f4f6',
                                        })}>{pct(lv.spp)}</td>
                                        <td style={num({ borderBottom: cliff ? '2px solid #dc2626' : '1px solid #f3f4f6' })}>
                                            {money(lv.buyer_price)} ₽
                                        </td>
                                        <td style={num({
                                            color: '#9ca3af',
                                            borderBottom: cliff ? '2px solid #dc2626' : '1px solid #f3f4f6',
                                        })}>
                                            {lv.spp_max - lv.spp_min > 0.2 ? `${pct(lv.spp_min)} – ${pct(lv.spp_max)}` : '—'}
                                        </td>
                                        <td style={num({
                                            color: '#6b7280', paddingRight: 16,
                                            borderBottom: cliff ? '2px solid #dc2626' : '1px solid #f3f4f6',
                                        })}>{lv.n}</td>
                                    </tr>
                                    {isOpen && lv.items.map((it) => (
                                        <tr key={it.nm_id} style={{ background: '#fcfcfd' }}>
                                            <td style={num({ textAlign: 'left', paddingLeft: 38, fontFamily: 'inherit', fontSize: 11.5 })}>
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
                                            <td style={num({ paddingRight: 16 })} />
                                        </tr>
                                    ))}
                                </React.Fragment>
                            );
                        })}
                    </tbody>
                </table>
            </div>

            {!!cat.gaps.length && (
                <div style={CARD_FOOTER}>
                    Не проверены уровни: {cat.gaps.map((g) => `${g} ₽`).join(', ')} — там нет ни одного нашего товара,
                    и узнать СПП можно только поставив туда цену.
                </div>
            )}
        </div>
    );
}
