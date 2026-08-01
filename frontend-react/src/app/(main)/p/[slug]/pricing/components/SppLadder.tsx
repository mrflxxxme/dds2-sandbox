'use client';

/**
 * «Ступеньки СПП» — где снижение нашей цены на рубли роняет цену клиента на сотни.
 *
 * Советник: ничего не пишет в ВБ, цены меняет человек руками. Каждая строка несёт
 * основание (сколько наблюдений и какой давности), потому что доверять здесь можно
 * только своей истории товара — сравнение «дешёвые товары против дорогих» врёт.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { SppLadderResponse, SppLadderRow } from '@/types/api';

const money = (n: number | null | undefined) => (n == null ? '—' : formatNumber(n, 0));
const pct = (n: number | null | undefined) => (n == null ? '—' : formatNumber(n, 1) + '%');
const int0 = (n: number | null | undefined) => (n == null ? '—' : formatNumber(n, 0));

const confColor = (c: string) =>
    c === 'высокая' ? 'var(--color-success)' : c === 'средняя' ? 'var(--color-warning)' : 'var(--color-text-dim)';

export default function SppLadder({ dateFrom, dateTo }: { dateFrom: string; dateTo: string }) {
    const [resp, setResp] = useState<SppLadderResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [minLeverage, setMinLeverage] = useState(2);
    const [maxDrop, setMaxDrop] = useState(10);
    const [onlyInStock, setOnlyInStock] = useState(true);
    const [observing, setObserving] = useState(false);
    const [obsMsg, setObsMsg] = useState('');

    const reqRef = useRef(0);
    const load = useCallback(async () => {
        const myReq = ++reqRef.current;
        setLoading(true);
        setError('');
        try {
            const res = await api.getSppLadder({
                date_from: dateFrom,
                date_to: dateTo,
                min_leverage: minLeverage,
                max_drop_pct: maxDrop,
                only_in_stock: onlyInStock,
            });
            if (reqRef.current !== myReq) return;
            setResp(res);
        } catch (e) {
            if (reqRef.current !== myReq) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (reqRef.current === myReq) setLoading(false);
        }
    }, [dateFrom, dateTo, minLeverage, maxDrop, onlyInStock]);

    useEffect(() => {
        const t = setTimeout(load, 250);
        return () => clearTimeout(t);
    }, [load]);

    const doObserve = async (backfillDays: number) => {
        setObserving(true);
        setObsMsg('');
        try {
            const r = await api.observeSpp(backfillDays);
            setObsMsg(
                `снимок: ${r.snapshot.written} точек` +
                    (r.snapshot.stale ? ` (${r.snapshot.stale} пропущено — витрина ушла вперёд синка цен)` : '') +
                    (backfillDays ? ` · из заказов: ${r.backfill.written}` : ''),
            );
            await load();
        } catch (e) {
            setObsMsg(e instanceof Error ? e.message : 'Ошибка');
        } finally {
            setObserving(false);
        }
    };

    const rows = resp?.rows ?? [];
    const stats = resp?.stats;
    const steps = resp?.steps ?? [];

    const totals = useMemo(() => {
        const down = rows.filter((r) => r.verdict === 'step_down');
        return {
            down: down.length,
            hold: rows.length - down.length,
            give: down.reduce((s, r) => s + (r.drop_seller ?? 0), 0),
            gain: down.reduce((s, r) => s + (r.drop_buyer ?? 0), 0),
        };
    }, [rows]);

    const columns: Column[] = useMemo(
        () => [
            {
                key: 'vendor_code', label: 'Артикул', width: '200px', sortable: true,
                render: (v: string | null, r: SppLadderRow) => (
                    <div>
                        <div style={{ fontWeight: 600 }}>{v || r.nm_id}</div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                            {r.nm_id} · {r.category || '—'}
                        </div>
                    </div>
                ),
            },
            {
                key: 'verdict', label: 'Что делать', width: '150px', sortable: true,
                render: (v: string, r: SppLadderRow) =>
                    v === 'step_down' ? (
                        <span style={{ color: 'var(--color-success)', fontWeight: 600 }}>
                            ↓ до {money(r.target_price)} ₽
                        </span>
                    ) : (
                        <span style={{ color: 'var(--color-warning)' }} title={`Порог ${money(r.threshold)} ₽`}>
                            ✋ не поднимать
                        </span>
                    ),
            },
            { key: 'current_price', label: 'Цена сейчас', align: 'right', sortable: true, render: (v: number | null) => money(v) },
            {
                key: 'buyer_price', label: 'Клиент платит', align: 'right', sortable: true,
                render: (v: number | null, r: SppLadderRow) => (
                    <span title={`СПП ${pct(r.spp_rate)}`} style={{ fontWeight: 600 }}>{money(v)}</span>
                ),
            },
            {
                key: 'target_buyer_price', label: 'Станет платить', align: 'right', sortable: true,
                render: (v: number | null, r: SppLadderRow) =>
                    v == null ? '—' : (
                        <span title={`СПП ${pct(r.target_spp)}`} style={{ color: 'var(--color-success)', fontWeight: 600 }}>
                            {money(v)}
                        </span>
                    ),
            },
            { key: 'drop_seller', label: 'Отдаём ₽', align: 'right', sortable: true, render: (v: number | null) => money(v) },
            {
                key: 'drop_buyer', label: 'Выигрыш клиента ₽', align: 'right', sortable: true,
                render: (v: number | null) => <span style={{ color: 'var(--color-success)' }}>{money(v)}</span>,
            },
            {
                key: 'leverage', label: 'Рычаг', align: 'right', sortable: true,
                render: (v: number | null) =>
                    v == null ? '—' : (
                        <span style={{ fontWeight: 700, color: v >= 5 ? 'var(--color-success)' : undefined }}
                            title="Во сколько раз выигрыш клиента больше, чем наша уступка">
                            ×{formatNumber(v, 1)}
                        </span>
                    ),
            },
            { key: 'jump', label: 'СПП +п.п.', align: 'right', sortable: true, render: (v: number | null) => (v == null ? '—' : '+' + formatNumber(v, 1)) },
            {
                key: 'confidence', label: 'Основание', width: '230px', sortable: true,
                render: (v: string, r: SppLadderRow) => (
                    <div style={{ fontSize: 11 }}>
                        <span style={{ color: confColor(v), fontWeight: 600 }}>{v}</span>
                        <span style={{ color: 'var(--color-text-dim)' }}> · {r.jump_source}</span>
                        <div style={{ color: 'var(--color-text-dim)' }}>{r.evidence}</div>
                    </div>
                ),
            },
            {
                key: 'unit_profit_after', label: 'Прибыль/шт после', align: 'right', sortable: true,
                render: (v: number | null, r: SppLadderRow) =>
                    v == null ? '—' : (
                        <span title={`сейчас ${money(r.unit_profit_now)} ₽`} style={{ color: v > 0 ? undefined : 'var(--color-danger)' }}>
                            {money(v)}
                        </span>
                    ),
            },
            { key: 'floor', label: 'Пол цены', align: 'right', sortable: true, render: (v: number | null) => money(v) },
            { key: 'orders_count', label: 'Заказы', align: 'right', sortable: true, render: (v: number | null) => int0(v) },
            { key: 'wb_stock', label: 'Остаток ВБ', align: 'right', sortable: true, render: (v: number | null) => int0(v) },
        ],
        [],
    );

    const doExport = () =>
        exportToExcel(
            rows.map((r) => ({
                'Артикул': r.vendor_code ?? '', 'nm_id': r.nm_id, 'Категория': r.category ?? '',
                'Что делать': r.verdict === 'step_down' ? 'снизить' : 'не поднимать',
                'Цена сейчас': r.current_price, 'Целевая цена': r.target_price,
                'Клиент платит': r.buyer_price, 'Станет платить': r.target_buyer_price,
                'СПП сейчас %': r.spp_rate, 'СПП станет %': r.target_spp,
                'Отдаём ₽': r.drop_seller, 'Выигрыш клиента ₽': r.drop_buyer, 'Рычаг': r.leverage,
                'Основание': `${r.confidence} · ${r.jump_source} · ${r.evidence}`,
                'Прибыль/шт сейчас': r.unit_profit_now, 'Прибыль/шт после': r.unit_profit_after,
                'Пол цены': r.floor, 'Заказы': r.orders_count, 'Остаток ВБ': r.wb_stock,
            })),
            'spp_ladder',
        );

    return (
        <div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', margin: '16px 0' }}>
                <label style={{ fontSize: 12, display: 'flex', gap: 6, alignItems: 'center' }}>
                    Рычаг от
                    <input type="number" className="btn btn-sm" style={{ width: 70 }} min={1} max={50} step={0.5}
                        value={minLeverage} onChange={(e) => setMinLeverage(Number(e.target.value) || 1)} />
                </label>
                <label style={{ fontSize: 12, display: 'flex', gap: 6, alignItems: 'center' }}>
                    Снижать не более, %
                    <input type="number" className="btn btn-sm" style={{ width: 70 }} min={1} max={50}
                        value={maxDrop} onChange={(e) => setMaxDrop(Number(e.target.value) || 10)} />
                </label>
                <label style={{ fontSize: 12, display: 'flex', gap: 4, alignItems: 'center', cursor: 'pointer' }}>
                    <input type="checkbox" checked={onlyInStock} onChange={(e) => setOnlyInStock(e.target.checked)} />
                    с остатком ВБ
                </label>
                <div style={{ flex: 1 }} />
                <button className="btn btn-sm btn-secondary" onClick={doExport} disabled={!rows.length}>📥 Excel</button>
                <button className="btn btn-sm btn-secondary" onClick={() => doObserve(90)} disabled={observing}
                    title="Разобрать 90 дней заказов в точки истории — разово после установки">
                    {observing ? '⏳…' : '📚 Из заказов за 90 дн'}
                </button>
                <button className="btn btn-sm btn-primary" onClick={() => doObserve(0)} disabled={observing}
                    title="Снять точку «цена → СПП → цена клиента» прямо сейчас">
                    {observing ? '⏳ Снимаю…' : '🔄 Снять точку'}
                </button>
            </div>

            {obsMsg && <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 12 }}>{obsMsg}</div>}

            <div className="glass-card" style={{ padding: 14, marginBottom: 16, fontSize: 13, lineHeight: 1.5 }}>
                <b>Как это читать.</b> СПП назначает ВБ, и он ступенчатый: иногда 10 ₽ нашей уступки роняют цену
                клиента на 200 ₽. Ищем именно такие точки — рычаг = выигрыш клиента ÷ наша уступка. Вывод строится
                на истории <b>этого же товара</b>: сравнение «дешёвые товары против дорогих» даёт ложные пороги
                (на замере 01.08 кросс-секция обещала на 2000 ₽ скачок +11 п.п., а товары, реально переходившие
                этот порог, показали −0.4). Ниже цены безубытка советов нет.
            </div>

            {stats && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8, marginBottom: 16 }}>
                    <Kpi label="Снизить цену" value={int0(totals.down)} color="var(--color-success)" sub={`отдаём ${money(totals.give)} ₽ → клиент выигрывает ${money(totals.gain)} ₽`} />
                    <Kpi label="Держать (у порога)" value={int0(totals.hold)} sub="поднимать цену нельзя" />
                    <Kpi label="Точек истории" value={int0(stats.points)} sub={`${int0(stats.nm_with_points)} артикулов · ${stats.days} дн`} />
                    <Kpi label="Порогов по проекту" value={int0(stats.steps_found)} sub={steps.map((s) => money(s.threshold)).join(', ') || 'подтверждённых нет'} />
                    <Kpi label="Отсеяно безубытком" value={int0(stats.skipped_below_floor)} sub="ступенька ниже пола цены" />
                    <Kpi label="Свежесть" value={stats.last_point_on ?? '—'} sub="последняя точка" />
                </div>
            )}

            {steps.length > 0 && (
                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 12 }}>
                    Пороги, подтверждённые парным тестом:{' '}
                    {steps.map((s) => (
                        <span key={s.threshold} style={{ marginRight: 12 }}>
                            <b>{money(s.threshold)} ₽</b> +{formatNumber(s.jump, 1)} п.п. ({s.n_products} товаров, согласны {formatNumber(s.agree_pct, 0)} %)
                        </span>
                    ))}
                </div>
            )}

            {error && <div style={{ padding: 16, color: 'var(--color-danger)' }}>❌ {error}</div>}
            {!error && (
                <TanStackDataTable
                    columns={columns}
                    data={rows}
                    loading={loading}
                    emptyIcon="🪜"
                    emptyText={
                        stats && stats.points === 0
                            ? 'Истории ещё нет. Нажмите «Из заказов за 90 дн» — точки соберутся из ваших же заказов.'
                            : 'Ступенек не найдено: ни у одного товара нет цены ниже, где СПП был бы заметно выше.'
                    }
                    pageSize={50}
                    maxHeight={640}
                />
            )}
        </div>
    );
}

function Kpi({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
    return (
        <div className="glass-card" style={{ padding: '10px 12px' }}>
            <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>{label}</div>
            <div style={{ fontSize: 20, fontWeight: 700, color }}>{value}</div>
            {sub && <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>{sub}</div>}
        </div>
    );
}
