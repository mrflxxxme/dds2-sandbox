'use client';

/**
 * Прогоны цен: очередь проб и запуск.
 *
 * Очередь строится не наугад, а по ПРОБЕЛАМ в данных — где наблюдений нет,
 * там порог ВБ может быть, а может и не быть, и отличить одно от другого можно
 * только поставив туда цену. Три вида целей: сузить известный порог, проверить
 * круглую цену в пустоте, закрыть широкое белое пятно.
 *
 * Запуск ПИШЕТ ЦЕНЫ В ЖИВОЙ ВБ, поэтому кнопка двухшаговая и рядом всегда
 * висят рамки, в которых прогон работает.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDateTime } from '@/lib/utils';
import { MONO } from '../../funnel/components/FunnelTable';
import { CARD_TOOLBAR, TABLE_CARD, thFlat } from '../../funnel/components/funnelUi';
import WbThumb from '@/components/WbThumb';
import { wbProductUrl } from '@/lib/wbMedia';
import type { SppScanPlan, SppScanRun, SppProbeRow } from '@/types/api';

const money = (n: number | null | undefined, digits = 0) =>
    n == null ? '—' : formatNumber(n, digits);

const num = (extra?: React.CSSProperties): React.CSSProperties => ({
    textAlign: 'right', verticalAlign: 'middle', padding: '4px 10px',
    fontSize: 12, lineHeight: '16px', fontFamily: MONO, fontVariantNumeric: 'tabular-nums',
    whiteSpace: 'nowrap', borderBottom: '1px solid #f3f4f6', ...extra,
});

const KIND: Record<string, { label: string; color: string; title: string }> = {
    narrow: {
        label: 'порог', color: '#1e3a8a',
        title: 'Порог известен, но не известно, где именно он проходит. Проба посередине зазора делит его пополам.',
    },
    grid: {
        label: 'круглая', color: '#7c3aed',
        title: 'Круглая цена внутри пустого промежутка: пороги ВБ садятся именно на такие числа.',
    },
    explore: {
        label: 'пятно', color: '#6b7280',
        title: 'Широкий участок цен без единого наблюдения — порог может быть здесь.',
    },
};

export default function SppScanTab() {
    const [plan, setPlan] = useState<SppScanPlan | null>(null);
    const [probes, setProbes] = useState<SppProbeRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [top, setTop] = useState(10);
    const [confirming, setConfirming] = useState(false);
    const [running, setRunning] = useState(false);
    const [result, setResult] = useState<SppScanRun | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [p, j] = await Promise.all([api.getSppScanPlan(40), api.getSppProbes(30)]);
            setPlan(p);
            setProbes(j.rows);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const run = async () => {
        setConfirming(false);
        setRunning(true);
        setError('');
        setResult(null);
        try {
            const res = await api.runSppScan(top);
            setResult(res);
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Ошибка запуска');
        } finally {
            setRunning(false);
        }
    };

    const queue = plan?.plan ?? [];
    const lim = plan?.limits;

    return (
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div className="glass-card" style={{ ...TABLE_CARD, flex: '0 0 auto' }}>
                <div style={{ ...CARD_TOOLBAR, borderBottom: 'none', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 12.5, fontWeight: 600, color: '#374151' }}>Очередь прогона</span>
                    <span style={{ fontFamily: MONO, fontSize: 12, color: '#9ca3af' }}>{queue.length}</span>

                    <span style={{ fontSize: 12, color: '#9ca3af', marginLeft: 6 }}>ставить</span>
                    <input type="number" min={1} max={40} value={top}
                        onChange={(e) => setTop(Math.min(40, Math.max(1, Number(e.target.value) || 1)))}
                        style={{
                            width: 62, padding: '5px 8px', borderRadius: 8, fontSize: 12.5,
                            border: '1px solid var(--color-border)', background: '#fff',
                            fontFamily: MONO, fontVariantNumeric: 'tabular-nums',
                        }} />

                    <div style={{ flex: 1 }} />

                    {/* Двухшаговое подтверждение: за кнопкой живые цены в ВБ */}
                    {confirming ? (
                        <>
                            <span style={{ fontSize: 12, color: '#b45309' }}>
                                Поставить {Math.min(top, queue.length)} цен в живой ВБ?
                            </span>
                            <button className="btn btn-sm btn-secondary" onClick={() => setConfirming(false)}>Отмена</button>
                            <button className="btn btn-sm btn-danger" onClick={run}>Да, запустить</button>
                        </>
                    ) : (
                        <button className="btn btn-sm btn-primary" onClick={() => setConfirming(true)}
                            disabled={running || !queue.length}>
                            {running ? 'Прогон идёт…' : 'Запустить прогон'}
                        </button>
                    )}
                    <button className="btn btn-sm btn-secondary" onClick={load} disabled={loading || running}>
                        Обновить
                    </button>
                </div>

                {lim && (
                    <div style={{ padding: '0 16px 10px', fontSize: 11.5, color: '#6b7280', lineHeight: 1.6 }}>
                        Рамки прогона: вниз не более <b>{money(lim.max_down_rub)} ₽</b>, вверх не более{' '}
                        <b>{money(lim.max_up_rub)} ₽</b>, шаг до <b>{lim.max_step_pct} %</b> цены. Каждая цена
                        ставится с копейками (хвост {formatNumber(lim.kopecks, 2)}), после реакции витрины
                        возвращается к прежнему значению. Наблюдение записывается только если цена клиента
                        реально сдвинулась — иначе мы бы записали «ступеньки нет» там, где ВБ просто не успел
                        пересчитать.
                    </div>
                )}
            </div>

            {error && (
                <div className="glass-card" style={{ padding: 14, color: 'var(--color-danger)', fontSize: 12.5 }}>
                    {error}
                    {error.includes('401') || error.toLowerCase().includes('ключ') ? (
                        <div style={{ color: '#6b7280', marginTop: 6 }}>
                            Ключу ВБ нужен доступ «Цены и скидки»: кабинет ВБ → Настройки → Доступ к API →
                            создать токен с этой категорией и заменить его в интеграции проекта.
                        </div>
                    ) : null}
                </div>
            )}

            {running && (
                <div className="glass-card" style={{ padding: 14, fontSize: 12.5, color: '#b45309' }}>
                    Цены поставлены, ждём реакции витрины. Опрос идёт раз в минуту, каждая цена возвращается
                    сразу после того, как ВБ пересчитал её. Не закрывайте вкладку.
                </div>
            )}

            {result && <RunResult res={result} />}

            {loading && !plan && (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                    Загрузка…
                </div>
            )}

            {!loading && !error && !queue.length && (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                    Пробелов в данных не осталось — прогонять нечего.
                </div>
            )}

            {!!queue.length && (
                <div className="glass-card" style={{ ...TABLE_CARD }}>
                    <div style={{ overflow: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, background: '#fff' }}>
                            <thead>
                                <tr>
                                    <th style={{ ...thFlat, textAlign: 'left', paddingLeft: 14 }}>Что проверяем</th>
                                    <th style={thFlat}>Цена пробы</th>
                                    <th style={{ ...thFlat, textAlign: 'left' }}>Донор</th>
                                    <th style={thFlat}>Сейчас</th>
                                    <th style={thFlat}>Сдвиг</th>
                                    <th style={thFlat}>Зазор</th>
                                    <th style={{ ...thFlat, paddingRight: 14 }}>Рядом</th>
                                </tr>
                            </thead>
                            <tbody>
                                {queue.map((t, i) => {
                                    const k = KIND[t.kind] ?? KIND.explore;
                                    const inRun = i < top;
                                    return (
                                        <tr key={`${t.price}-${t.donor.nm_id}`}
                                            style={{ background: inRun ? '#f8fafc' : undefined, opacity: inRun ? 1 : 0.5 }}>
                                            <td style={num({ textAlign: 'left', paddingLeft: 14, fontFamily: 'inherit', whiteSpace: 'normal', maxWidth: 420 })}>
                                                <span title={k.title} style={{
                                                    color: k.color, border: `1px solid ${k.color}33`, borderRadius: 6,
                                                    padding: '1px 6px', fontSize: 11, marginRight: 8, whiteSpace: 'nowrap',
                                                }}>{k.label}</span>
                                                <span style={{ color: '#6b7280', fontSize: 11.5 }}>{t.why}</span>
                                            </td>
                                            <td style={num({ fontWeight: 700 })}>{money(t.price, 2)} ₽</td>
                                            <td style={num({ textAlign: 'left', fontFamily: 'inherit', fontSize: 11.5 })}>
                                                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                                                    <WbThumb nmId={t.donor.nm_id} size={26} height={34} rounded={5} />
                                                    <span style={{ display: 'inline-flex', flexDirection: 'column', lineHeight: 1.25 }}>
                                                        <a href={wbProductUrl(t.donor.nm_id)} target="_blank" rel="noreferrer"
                                                            style={{ color: 'var(--color-accent)', fontWeight: 500 }}>
                                                            {t.donor.vendor_code || t.donor.nm_id}
                                                        </a>
                                                        <span style={{ color: '#9ca3af', fontSize: 11 }}>{t.donor.category}</span>
                                                    </span>
                                                </span>
                                            </td>
                                            <td style={num({ color: '#6b7280' })}>{money(t.donor.price, 2)} ₽</td>
                                            <td style={num({ color: t.donor.delta < 0 ? '#059669' : '#1e3a8a' })}>
                                                {t.donor.delta > 0 ? '+' : '−'}{money(Math.abs(t.donor.delta))} ₽
                                                <span style={{ color: '#9ca3af' }}> ({formatNumber(t.donor.step_pct, 1)}%)</span>
                                            </td>
                                            <td style={num({ color: '#9ca3af' })}
                                                title="Насколько сузится неизвестность после этой пробы">
                                                {money(t.gap_before)} → {money(t.gap_after)} ₽
                                            </td>
                                            <td style={num({ color: '#6b7280', paddingRight: 14 })}
                                                title="Сколько наших артикулов стоит в пределах 10 % от этой цены">
                                                {t.nearby}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {!!probes.length && <ProbeLog rows={probes} />}
        </div>
    );
}

/** Итог прогона: что витрина ответила и что записано в карту. */
function RunResult({ res }: { res: SppScanRun }) {
    return (
        <div className="glass-card" style={{ padding: 14, fontSize: 12.5 }}>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>
                Прогон закончен: поставлено {res.launched}, ответила витрина у {res.reacted.length}
            </div>
            {!!res.reacted.length && (
                <div style={{ fontFamily: MONO, fontSize: 12, color: '#374151', lineHeight: 1.7 }}>
                    {res.reacted.map((r) => (
                        <div key={r.nm_id}>
                            {r.nm_id}: {money(r.price, 2)} ₽ → клиент {money(r.buyer_before)} → {money(r.buyer_after)} ₽,
                            СПП {formatNumber(r.spp, 1)} % (за {Math.round(r.after_sec / 60)} мин)
                        </div>
                    ))}
                </div>
            )}
            {!!res.no_reaction.length && (
                <div style={{ color: '#b45309', marginTop: 8 }}>
                    Без реакции: {res.no_reaction.join(', ')} — цены возвращены, наблюдения не записаны
                    (иначе это выглядело бы как «ступеньки здесь нет»).
                </div>
            )}
            {!!res.refused.length && (
                <div style={{ color: '#6b7280', marginTop: 8 }}>
                    Не запущены: {res.refused.map((r) => `${r.nm_id} — ${r.reason}`).join('; ')}
                </div>
            )}
            {!!res.errors.length && (
                <div style={{ color: 'var(--color-danger)', marginTop: 8 }}>
                    Ошибки: {res.errors.map((r) => `${r.nm_id} — ${r.error}`).join('; ')}
                </div>
            )}
        </div>
    );
}

/** Журнал проб: главное здесь — колонка «Цена вернулась». */
function ProbeLog({ rows }: { rows: SppProbeRow[] }) {
    return (
        <div className="glass-card" style={{ ...TABLE_CARD }}>
            <div style={{ ...CARD_TOOLBAR, borderBottom: '1px solid #f3f4f6' }}>
                <span style={{ fontSize: 12.5, fontWeight: 600, color: '#374151' }}>Журнал проб</span>
                <span style={{ fontFamily: MONO, fontSize: 12, color: '#9ca3af' }}>{rows.length}</span>
            </div>
            <div style={{ overflow: 'auto', maxHeight: 320 }}>
                <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, background: '#fff' }}>
                    <thead>
                        <tr>
                            <th style={{ ...thFlat, textAlign: 'left', paddingLeft: 14 }}>Когда</th>
                            <th style={{ ...thFlat, textAlign: 'left' }}>Артикул</th>
                            <th style={thFlat}>Ставили</th>
                            <th style={thFlat}>Клиент до → после</th>
                            <th style={thFlat}>СПП до → после</th>
                            <th style={thFlat}>Статус</th>
                            <th style={{ ...thFlat, paddingRight: 14 }}>Цена вернулась</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((p) => (
                            <tr key={p.id}>
                                <td style={num({ textAlign: 'left', paddingLeft: 14, color: '#6b7280', fontSize: 11.5 })}>
                                    {formatDateTime(p.started_at)}
                                </td>
                                <td style={num({ textAlign: 'left' })}>
                                    <a href={wbProductUrl(p.nm_id)} target="_blank" rel="noreferrer"
                                        style={{ color: 'var(--color-accent)' }}>{p.nm_id}</a>
                                </td>
                                <td style={num()}>{money(p.price_before, 2)} → <b>{money(p.target_price, 2)}</b> ₽</td>
                                <td style={num({ color: '#6b7280' })}>
                                    {money(p.buyer_before)} → {money(p.buyer_after)} ₽
                                </td>
                                <td style={num({ color: '#6b7280' })}>
                                    {p.spp_before == null ? '—' : `${formatNumber(p.spp_before, 1)}%`} →{' '}
                                    {p.spp_after == null ? '—' : `${formatNumber(p.spp_after, 1)}%`}
                                </td>
                                <td style={num({ color: p.status === 'OK' ? '#059669' : p.status === 'ERROR' ? '#dc2626' : '#b45309' })}
                                    title={p.error ?? undefined}>
                                    {p.status}
                                </td>
                                <td style={num({ paddingRight: 14, color: p.reverted ? '#059669' : '#dc2626', fontWeight: p.reverted ? 400 : 700 })}>
                                    {p.reverted ? 'да' : 'НЕТ'}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
