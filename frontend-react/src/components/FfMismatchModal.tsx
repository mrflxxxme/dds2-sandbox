'use client';
/** «Расхождение наполнения» — разбивка состава сборки vs привязанные заявки ФФ.
 *  FfMismatchView — презентационная таблица/сводка; FfMismatchBlock — инлайн-блок
 *  на деталке сборки (без клика); FfMismatchModal — то же из таблиц-списков. */
import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';
import type { FfMismatchDetail } from '@/types/api';

function useFfMismatch(assemblyId: number) {
    const [data, setData] = useState<FfMismatchDetail | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError('');
        api.getAssemblyFfMismatch(assemblyId)
            .then(r => { if (!controller.signal.aborted) setData(r); })
            .catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка загрузки'); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [assemblyId]);
    return { data, loading, error };
}

const diffColor = (diff: number) => (diff > 0 ? 'var(--color-warning)' : 'var(--color-danger)');
const signed = (n: number) => `${n > 0 ? '+' : ''}${formatNumber(n, 0)}`;

/** Сводка + таблица различий (без обёртки). */
export function FfMismatchView({ data }: { data: FfMismatchDetail }) {
    const totalDiff = data.ff_total - data.our_total;
    const [copied, setCopied] = useState(false);

    const fileBase = `rashozhdenie_${data.assembly_number || data.assembly_id}`;
    const exportXlsx = () => exportToExcel(
        data.rows.map(r => ({ barcode: r.barcode, article: r.article_seller || '', our: r.our_qty, ff: r.ff_qty, diff: r.diff })),
        fileBase,
        [
            { key: 'barcode', label: 'ШК' },
            { key: 'article', label: 'Артикул' },
            { key: 'our', label: 'У нас' },
            { key: 'ff', label: 'ФФ' },
            { key: 'diff', label: 'Разница' },
        ],
    );
    const copyTsv = async () => {
        const header = ['ШК', 'Артикул', 'У нас', 'ФФ', 'Разница'].join('\t');
        const lines = data.rows.map(r => [r.barcode, r.article_seller || '', r.our_qty, r.ff_qty, r.diff].join('\t'));
        try {
            await navigator.clipboard.writeText([header, ...lines].join('\n'));
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch { /* clipboard недоступен — тихо игнорируем */ }
    };

    return (
        <>
            <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                Сравнение нашего состава с {data.ff_request_numbers.length > 1 ? 'заявками' : 'заявкой'} ФФ
                {data.ff_request_numbers.length ? <> (<b style={{ color: 'var(--color-text)' }}>{data.ff_request_numbers.join(', ')}</b>)</> : ''}.
                {' '}Наш итог: <b style={{ color: 'var(--color-text)' }}>{formatNumber(data.our_total, 0)}</b> шт ·
                {' '}ФФ: <b style={{ color: 'var(--color-text)' }}>{formatNumber(data.ff_total, 0)}</b> шт
                {' '}(разница <b style={{ color: diffColor(totalDiff) }}>{signed(totalDiff)}</b>)
            </p>

            {data.mode === 'total' ? (
                <div style={{ padding: 16, fontSize: 13, color: 'var(--color-text-muted)', background: 'var(--color-bg-card)', borderRadius: 12 }}>
                    Состав по позициям для этого склада недоступен — показана только сводка по количеству.
                </div>
            ) : data.rows.length === 0 ? (
                <div style={{ padding: 16, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>Различий по позициям нет.</div>
            ) : (
                <>
                <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                    <button className="btn btn-secondary btn-sm" onClick={exportXlsx}>📥 Выгрузить в Excel</button>
                    <button className="btn btn-secondary btn-sm" onClick={copyTsv}>{copied ? '✓ Скопировано' : '📋 Скопировать'}</button>
                </div>
                <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                        <thead>
                            <tr style={{ textAlign: 'left', color: 'var(--color-text-muted)', borderBottom: '1px solid var(--color-border)' }}>
                                <th style={{ padding: '8px 12px' }}>ШК</th>
                                <th style={{ padding: '8px 12px' }}>Артикул</th>
                                <th style={{ padding: '8px 12px', textAlign: 'right' }}>У нас</th>
                                <th style={{ padding: '8px 12px', textAlign: 'right' }}>ФФ</th>
                                <th style={{ padding: '8px 12px', textAlign: 'right' }}>Разница</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data.rows.map(r => (
                                <tr key={r.barcode} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                    <td style={{ padding: '8px 12px', fontFamily: 'monospace' }}>{r.barcode}</td>
                                    <td style={{ padding: '8px 12px' }}>{r.article_seller || '—'}</td>
                                    <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatNumber(r.our_qty, 0)}</td>
                                    <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatNumber(r.ff_qty, 0)}</td>
                                    <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600, color: diffColor(r.diff) }}>{signed(r.diff)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                </>
            )}

            {data.extra_rows && data.extra_rows.length > 0 && (
                <div style={{ marginTop: 16 }}>
                    <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 8 }}>
                        Есть у ФФ, но не в нашей сборке — <b style={{ color: 'var(--color-text)' }}>не считается расхождением</b>
                        {' '}({formatNumber(data.extra_rows.reduce((s, r) => s + r.ff_qty, 0), 0)} шт в {data.extra_rows.length} SKU).
                        {' '}Обычно это строки, которые ФФ приписал к заявке от соседних отгрузок.
                    </p>
                    <div style={{ overflowX: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, opacity: 0.75 }}>
                            <thead>
                                <tr style={{ textAlign: 'left', color: 'var(--color-text-muted)', borderBottom: '1px solid var(--color-border)' }}>
                                    <th style={{ padding: '8px 12px' }}>ШК</th>
                                    <th style={{ padding: '8px 12px' }}>Артикул</th>
                                    <th style={{ padding: '8px 12px', textAlign: 'right' }}>ФФ</th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.extra_rows.map(r => (
                                    <tr key={r.barcode} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                        <td style={{ padding: '8px 12px', fontFamily: 'monospace' }}>{r.barcode}</td>
                                        <td style={{ padding: '8px 12px' }}>{r.article_seller || '—'}</td>
                                        <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatNumber(r.ff_qty, 0)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </>
    );
}

/** Инлайн-блок на деталке сборки — показывается, только когда есть реальное расхождение. */
export function FfMismatchBlock({ assemblyId }: { assemblyId: number }) {
    const { data, loading, error } = useFfMismatch(assemblyId);
    if (loading || error || !data) return null;
    const hasDiff = data.mode === 'barcode' ? data.rows.length > 0 : data.ff_total !== data.our_total;
    if (!hasDiff) return null;
    return (
        <div className="glass-card animate-in" style={{ marginTop: 16, borderLeft: '3px solid var(--color-warning)' }}>
            <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="badge badge-warning" style={{ fontSize: 11, padding: '2px 8px' }}>⚠</span>
                Расхождение наполнения с заявками ФФ
            </h3>
            <FfMismatchView data={data} />
        </div>
    );
}

/** Модалка — для таблиц-списков (список сборок, вкладка «ФФ», сводная). */
export function FfMismatchModal({ assemblyId, onClose }: { assemblyId: number; onClose: () => void }) {
    const { data, loading, error } = useFfMismatch(assemblyId);
    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card modal-card-wide modal-card-solid" onClick={e => e.stopPropagation()}>
                <h2 className="modal-title" style={{ marginBottom: 8 }}>
                    Расхождение наполнения{data?.assembly_number ? ` · ${data.assembly_number}` : ''}
                </h2>
                {loading ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>Загрузка...</div>
                ) : error ? (
                    <div style={{ color: 'var(--color-danger)', fontSize: 13, padding: 12 }}>{error}</div>
                ) : data ? (
                    <FfMismatchView data={data} />
                ) : null}
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
                    <button className="btn btn-secondary" onClick={onClose}>Закрыть</button>
                </div>
            </div>
        </div>
    );
}
