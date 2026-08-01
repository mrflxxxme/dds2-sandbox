'use client';

/**
 * Карта СПП: по каждой категории — лесенка своих цен и живой СПП на каждой ступени.
 *
 * Источник — часовой снимок витрины (card-API), а не среднее из финотчёта:
 * только он реагирует на сегодняшнюю цену. Красная граница — обрыв, выше
 * которого ВБ перестаёт доплачивать. Строка раскрывается в список артикулов.
 * «Цена до СПП» — наша цена (у уровня медиана, т.к. сам уровень округлён по шагу
 * сетки), «Разброс СПП» — min–max СПП внутри уровня: широкий разброс значит, что
 * ступенька применена не ко всем.
 *
 * Оформление — общие токены «Воронки продаж» (`funnelUi`), которая повторяет
 * «Управление рекламой».
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import { MONO } from '../../funnel/components/FunnelTable';
import WbThumb from '@/components/WbThumb';
import { wbProductUrl } from '@/lib/wbMedia';
import { CARD_TOOLBAR, TABLE_CARD, thFlat } from '../../funnel/components/funnelUi';
import AdsPeriodPicker from '../../ads-manager/components/AdsPeriodPicker';
import SearchSelect from '../../ads-manager/components/SearchSelect';
import type { SppMapResponse, SppCategory, SppHint, SppThreshold } from '@/types/api';

const money = (n: number | null | undefined) => (n == null ? '—' : formatNumber(n, 0));
const pct = (n: number | null | undefined) => (n == null ? '—' : formatNumber(n, 1) + '%');

/** Медиана — для строки уровня показываем реальную цену товаров, а не округлённую корзину. */
const median = (xs: number[]) => {
    if (!xs.length) return null;
    const s = [...xs].sort((a, b) => a - b);
    const m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

/** Разброс СПП внутри уровня: меньше 0.2 п.п. — считаем, что ступенька одна на всех. */
const SPREAD_MIN = 0.2;

/** Что будет с ценой ДЛЯ КЛИЕНТА — прямым текстом, без «почти не изменится». */
const buyerMove = (delta: number) =>
    delta === 0
        ? 'цена для клиента не изменится'
        : delta < 0
            ? `цена для клиента снизится на ${money(-delta)} ₽`
            : `цена для клиента вырастет на ${money(delta)} ₽`;

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

const STEP_PRESETS = [1, 5, 10, 25, 50, 100, 250, 500, 1000];
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
    const [pickedTh, setPickedTh] = useState<SppThreshold | null>(null);  // выбранный порог — подсветка категорий

    const reqRef = useRef(0);
    const load = useCallback(async () => {
        const myReq = ++reqRef.current;
        setLoading(true);
        setError('');
        try {
            const res = await api.getSppMap({
                // шаг 1 ₽ = каждая цена сама себе уровень; это законный режим «без корзин»
                date_from: from, date_to: to, step: Math.max(1, Number(step) || 100),
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
    const thCats = useMemo(() => new Set(pickedTh?.categories ?? []), [pickedTh]);
    const thConfirm = useMemo(() => new Set(pickedTh?.confirmed_by ?? []), [pickedTh]);

    const doExport = () =>
        exportToExcel(
            cats.flatMap((c) =>
                c.levels.flatMap((lv) =>
                    lv.items.map((it) => ({
                        'Категория': c.category,
                        'Уровень цены': lv.price,
                        'СПП уровня %': lv.spp,
                        'СПП уровня min %': lv.spp_min,
                        'СПП уровня max %': lv.spp_max,
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
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', height: 'calc(100vh - 250px)', minHeight: 420 }}>
            {/* overflow: visible на КАРТОЧКЕ и полосе обязателен — TABLE_CARD режет по
                своей рамке, и выпадашки календаря и шага сетки выглядят «не открылись» */}
            <div className="glass-card" style={{ ...TABLE_CARD, marginBottom: 10, flex: '0 0 auto', overflow: 'visible' }}>
                <div style={{ ...CARD_TOOLBAR, borderBottom: 'none', flexWrap: 'nowrap', overflow: 'visible' }}>
                    <AdsPeriodPicker from={from} to={to} minWidth={215}
                        onApply={(f, t) => { setFrom(f || isoToday()); setTo(t || isoToday()); }} />
                    <span style={{ fontSize: 12, color: '#9ca3af' }} title="Шаг сетки цен, ₽">шаг</span>
                    <SearchSelect
                        value={step}
                        onChange={(v) => setStep(v || '100')}
                        // свой шаг живёт в списке наравне с пресетами — иначе кнопка покажет голое число
                        options={[...new Set([...STEP_PRESETS, Number(step) || 100])]
                            .sort((a, b) => a - b)
                            .map((s) => ({ value: String(s), label: `${formatNumber(s, 0)} ₽` }))}
                        placeholder="Шаг"
                        showAll={false}
                        searchPlaceholder="Свой шаг, ₽"
                        customOption={(q) => {
                            const n = Number(q.replace(/[^\d]/g, ''));
                            return q && n >= 1 && !STEP_PRESETS.includes(n)
                                ? { value: String(n), label: `Свой шаг: ${formatNumber(n, 0)} ₽` }
                                : null;
                        }}
                        minWidth={104}
                        maxWidth={140}
                    />

                    {/* цифры без слов: подробности — в подсказке, чтобы полоса не превращалась в абзац */}
                    {stats && (
                        <span style={{ fontSize: 11.5, color: '#9ca3af', marginLeft: 6, whiteSpace: 'nowrap', fontFamily: MONO }}
                            title={`${formatNumber(stats.points, 0)} точек в ${formatNumber(stats.categories_count, 0)} категориях. `
                                + (stats.last_snapshot_on ? `Последний срез: ${formatDate(stats.last_snapshot_on)}.` : 'Срезов за период нет.')}>
                            {formatNumber(stats.points, 0)} / {formatNumber(stats.categories_count, 0)}
                            {stats.last_snapshot_on ? ` · ${formatDate(stats.last_snapshot_on)}` : ' · нет среза'}
                        </span>
                    )}

                    <div style={{ flex: 1 }} />
                    <button className="btn btn-sm btn-secondary" onClick={doExport} disabled={!cats.length}>Excel</button>
                    <button className="btn btn-sm btn-primary" onClick={doObserve} disabled={observing}>
                        {observing ? 'Снимаю…' : 'Снять срез'}
                    </button>
                </div>
            </div>

            <ThresholdsBar rows={resp?.thresholds ?? []} active={pickedTh} onPick={setPickedTh} />

            {error && <div style={{ padding: 16, color: 'var(--color-danger)' }}>{error}</div>}
            {loading && !resp && <div style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка…</div>}
            {!error && !loading && !cats.length && (
                <div style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                    Срезов ещё нет — нажмите «Снять срез».
                </div>
            )}

            {!!cats.length && (
                <div className="glass-card" style={{ ...TABLE_CARD, flexDirection: 'row', flex: 1, minHeight: 0 }}>
                    <div style={{ width: 236, borderRight: '1px solid #e5e7eb', overflowY: 'auto', flexShrink: 0, background: '#f9fafb' }}>
                        {cats.map((c) => {
                            const on = c.category === current?.category;
                            // выбран порог — подсвечиваем всех, кто его собрал (и жирным — кто подтвердил)
                            const inTh = thCats.has(c.category);
                            const confirms = thConfirm.has(c.category);
                            return (
                                <button key={c.category} type="button" onClick={() => setOpenCat(c.category)}
                                    title={confirms ? 'Порог подтверждается товарами этой категории по обе стороны' : undefined}
                                    style={{
                                        display: 'block', width: '100%', textAlign: 'left', border: 'none', cursor: 'pointer',
                                        padding: '6px 12px', fontSize: 12.5, whiteSpace: 'nowrap', overflow: 'hidden',
                                        textOverflow: 'ellipsis', borderBottom: '1px solid #f3f4f6',
                                        background: on ? '#fff' : inTh ? '#eff6ff' : 'transparent',
                                        color: on ? '#1e3a8a' : inTh ? '#1e40af' : '#374151',
                                        fontWeight: on || confirms ? 600 : 500,
                                        opacity: pickedTh && !inTh ? 0.45 : 1,
                                        borderLeft: on ? '3px solid var(--color-accent)'
                                            : confirms ? '3px solid #93c5fd' : '3px solid transparent',
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

/** Пороги цен: список цен, на которых СПП скачком меняется, — по всему портфелю.
 *
 * Отдельно от лесенки категории, потому что порог — свойство ЦЕНЫ: он один и тот
 * же для ковров и кружек, и от шага сетки не зависит. Место порога показываем
 * интервалом: между 1 499 и 1 502 ₽ наблюдений нет, и где ВБ проводит черту —
 * из данных не видно.
 */
function ThresholdsBar({ rows, active, onPick }: {
    rows: SppThreshold[];
    active: SppThreshold | null;
    onPick: (t: SppThreshold | null) => void;
}) {
    const [open, setOpen] = useState(true);
    if (!rows.length) return null;
    const key = (t: SppThreshold) => `${t.up_to}-${t.from_price}`;
    return (
        <div className="glass-card" style={{ ...TABLE_CARD, marginBottom: 10, flex: '0 0 auto' }}>
            <div style={{ ...CARD_TOOLBAR, borderBottom: open ? '1px solid #f3f4f6' : 'none', cursor: 'pointer', gap: 6 }}
                onClick={() => setOpen((o) => !o)}
                title="Цены, на которых СПП меняется скачком — по всему портфелю, вне зависимости от категории и шага сетки. Клик по порогу подсветит категории, из которых он собран.">
                <span style={{ color: '#9ca3af', fontSize: 11 }}>{open ? '▾' : '▸'}</span>
                <span style={{ fontSize: 12.5, fontWeight: 600, color: '#374151' }}>Пороги цен</span>
                <span style={{ fontFamily: MONO, fontSize: 12, color: '#9ca3af' }}>{rows.length}</span>
            </div>
            {open && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, padding: '8px 10px', maxHeight: 120, overflowY: 'auto' }}>
                    {rows.map((t) => {
                        const on = active != null && key(active) === key(t);
                        const tone = t.jump > 0 ? '#1e3a8a' : '#dc2626';  // дороже выгоднее / обрыв
                        return (
                            <button key={key(t)} type="button"
                                onClick={() => onPick(on ? null : t)}
                                title={[
                                    `СПП ${pct(t.spp_below)} держится на ${money(t.band_from)}–${money(t.up_to)} ₽ (${t.n_below} арт.),`,
                                    `с ${money(t.from_price)} ₽ и до ${money(t.band_to)} ₽ — уже ${pct(t.spp_above)} (${t.n_above} арт.).`,
                                    t.fuzzy
                                        ? `Между ${money(t.up_to)} и ${money(t.from_price)} ₽ товаров нет — где именно проходит порог, из данных не видно.`
                                        : '',
                                    t.confirmed_by?.length ? `Подтверждают: ${t.confirmed_by.join(', ')}.` : '',
                                    `Всего категорий: ${t.categories_count}.`,
                                ].filter(Boolean).join(' ')}
                                style={{
                                    display: 'inline-flex', alignItems: 'baseline', gap: 6, cursor: 'pointer',
                                    border: `1px ${t.fuzzy ? 'dashed' : 'solid'} ${on ? 'var(--color-accent)' : '#e5e7eb'}`,
                                    borderRadius: 8, padding: '3px 8px', background: on ? '#eff6ff' : '#fff',
                                    fontFamily: MONO, fontVariantNumeric: 'tabular-nums', fontSize: 11.5,
                                }}>
                                <b style={{ color: '#111827' }}>{money(t.up_to)} ₽</b>
                                <span style={{ color: '#6b7280' }}>{pct(t.spp_below)}</span>
                                <span style={{ color: tone }}>→{pct(t.spp_above)}</span>
                            </button>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

function Hint({ level, small }: { level: { hint_down: SppHint | null; hint_up: SppHint | null; price: number }; small?: boolean }) {
    const d = level.hint_down;
    const u = level.hint_up;
    if (!d && !u) return <span style={{ color: '#d1d5db' }}>—</span>;
    const source = (cats: string[]) => (cats.length ? ` По данным: ${cats.join(', ')}.` : '');
    return (
        <span style={{ fontSize: small ? 11 : 11.5, display: 'inline-flex', gap: 10, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
            {d && (
                <span style={{ color: '#059669' }}
                    title={`Опустить цену на ${money(level.price - d.price)} ₽ — ${buyerMove(-d.gain)} (рычаг ×${formatNumber(d.leverage ?? 0, 1)}).${source(d.categories)}`}>
                    опустить до <b>{money(d.price)} ₽</b> → клиенту {money(d.buyer_price)} ₽
                    <span style={{ color: '#9ca3af' }}> (−{money(d.gain)})</span>
                </span>
            )}
            {u && (
                <span style={{ color: '#1e3a8a' }}
                    title={`Поднять цену на ${money(u.gain)} ₽ — ${buyerMove(u.buyer_delta)}.${source(u.categories)}`}>
                    поднять до <b>{money(u.price)} ₽</b> → клиенту {money(u.buyer_price)} ₽
                    {u.buyer_delta !== 0 && (
                        <span style={{ color: '#9ca3af' }}>
                            {' '}({u.buyer_delta > 0 ? '+' : '−'}{money(Math.abs(u.buyer_delta))})
                        </span>
                    )}
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
                            <th style={thFlat} title="Наша цена до СПП. У строки уровня — медиана реальных цен товаров корзины, у артикула — его собственная цена.">
                                Цена до СПП
                            </th>
                            <th style={thFlat}>СПП</th>
                            <th style={thFlat}>Цена клиенту</th>
                            <th style={thFlat} title="Разброс СПП внутри уровня: минимальный и максимальный СПП среди артикулов корзины. Прочерк — все получили одинаковый СПП.">
                                Разброс СПП
                            </th>
                            <th style={{ ...thFlat, textAlign: 'right' }}>Что можно сделать</th>
                            <th style={{ ...thFlat, paddingRight: 14 }}>Артикулов</th>
                        </tr>
                    </thead>
                    <tbody>
                        {cat.levels.map((lv) => {
                            const isOpen = open.has(lv.price);
                            const cliff = cliffAbove.has(lv.price);
                            const edge = cliff ? '2px solid #dc2626' : '1px solid #f3f4f6';
                            const realPrice = median(lv.items.map((it) => it.price));
                            const spread = lv.spp_max - lv.spp_min > SPREAD_MIN;
                            return (
                                <React.Fragment key={lv.price}>
                                    <tr onClick={() => toggle(lv.price)}
                                        style={{ cursor: 'pointer', background: isOpen ? '#f8fafc' : undefined }}>
                                        <td style={num({ textAlign: 'left', paddingLeft: 14, fontWeight: 700, fontSize: 12.5, borderBottom: edge })}>
                                            <span style={{ color: '#9ca3af', marginRight: 6 }}>{isOpen ? '▾' : '▸'}</span>
                                            {money(lv.price)} ₽
                                        </td>
                                        <td style={num({ color: '#6b7280', borderBottom: edge })}
                                            title="Медиана фактических цен артикулов уровня — уровень слева округлён по шагу сетки">
                                            {realPrice == null ? '—' : `${money(realPrice)} ₽`}
                                        </td>
                                        <td style={num({ fontWeight: 700, color: levelColor(lv.spp, best), borderBottom: edge })}>{pct(lv.spp)}</td>
                                        <td style={num({ borderBottom: edge })}>{money(lv.buyer_price)} ₽</td>
                                        <td style={num({ color: '#9ca3af', borderBottom: edge })}
                                            title={spread ? `На одной цене СПП различается: от ${pct(lv.spp_min)} до ${pct(lv.spp_max)}. Раскройте уровень — увидите, у каких артикулов ступенька не применена.` : undefined}>
                                            {spread ? `${pct(lv.spp_min)} – ${pct(lv.spp_max)}` : '—'}
                                        </td>
                                        <td style={num({ borderBottom: edge, fontFamily: 'inherit', whiteSpace: 'normal', maxWidth: 280 })}><Hint level={lv} /></td>
                                        <td style={num({ color: '#6b7280', paddingRight: 14, borderBottom: edge })}>{lv.n}</td>
                                    </tr>
                                    {isOpen && lv.items.map((it) => (
                                        <tr key={it.nm_id} style={{ background: '#fcfcfd', height: 34 }}>
                                            <td style={num({ textAlign: 'left', paddingLeft: 30, fontFamily: 'inherit', fontSize: 12 })}>
                                                <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                                    <WbThumb nmId={it.nm_id} size={22} height={29} rounded={4} />
                                                    <span style={{ display: 'flex', flexDirection: 'column', lineHeight: '13px', minWidth: 0 }}>
                                                        <a href={wbProductUrl(it.nm_id)} target="_blank" rel="noreferrer"
                                                            style={{ color: 'var(--color-accent)', fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
                                                            onClick={(e) => e.stopPropagation()}>
                                                            {it.vendor_code || it.nm_id}
                                                        </a>
                                                        <span style={{ color: '#9ca3af', fontFamily: MONO, fontSize: 10 }}>{it.nm_id}</span>
                                                    </span>
                                                </span>
                                            </td>
                                            <td style={num({ fontSize: 11.5, color: '#6b7280' })}>{formatNumber(it.price, 2)} ₽</td>
                                            <td style={num({ fontSize: 11.5, color: levelColor(it.spp, best), fontWeight: 600 })}>{pct(it.spp)}</td>
                                            <td style={num({ fontSize: 11.5 })}>{money(it.buyer_price)} ₽</td>
                                            <td style={num({ fontSize: 11.5, color: '#d1d5db' })}>—</td>
                                            <td style={num({ fontFamily: 'inherit', whiteSpace: 'normal', maxWidth: 280 })}>
                                                {it.lag_hint ? (
                                                    <span style={{ color: '#b45309', fontSize: 11 }}
                                                        title={`У ${it.lag_hint.peers} соседей с такой же ценой СПП ${formatNumber(it.lag_hint.peer_spp, 1)} % — на ${formatNumber(it.lag_hint.delta, 1)} п.п. больше. ВБ обычно догоняет за несколько часов.`}>
                                                        ступенька не применена: у соседей {formatNumber(it.lag_hint.peer_spp, 1)} % → клиенту было бы {money(it.lag_hint.buyer_price)} ₽
                                                    </span>
                                                ) : (
                                                    <Hint level={it} small />
                                                )}
                                            </td>
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
