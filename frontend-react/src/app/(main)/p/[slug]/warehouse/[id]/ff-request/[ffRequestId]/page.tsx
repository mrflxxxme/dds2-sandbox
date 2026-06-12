'use client';
import React, { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import Toast from '@/components/Toast';
import type { FfMatchRow, FfRequestDetail, FfRequestDetailProduct, FfRequestRow } from '@/types/api';
import type { Column } from '@/components/DataTable';
import { FF_LINKED_STATUS_LABELS, FfLinkModal, ffStageBadge } from '../../ff-shared';

/* ─── Page: деталка заявки ФФ (состав, поля, история стадий) ─────────────── */

export default function FfRequestDetailPage() {
    const params = useParams();
    const router = useRouter();
    const slug = params.slug as string;
    const warehouseId = Number(params.id);
    const ffRequestId = Number(params.ffRequestId);

    const [detail, setDetail] = useState<FfRequestDetail | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Связывание с нашим документом (модал-пикер + отвязка)
    const [linkOpen, setLinkOpen] = useState(false);
    const [linkActing, setLinkActing] = useState(false);
    const [linkError, setLinkError] = useState('');

    // Архив (local_archived) и создание заявки на сборку из ФФ
    const [archActing, setArchActing] = useState(false);
    const [archError, setArchError] = useState('');
    const [toast, setToast] = useState('');
    const [notice, setNotice] = useState('');

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        setError('');
        api.getFfRequestDetail(warehouseId, ffRequestId)
            .then(r => { if (!controller.signal.aborted) setDetail(r); })
            .catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка'); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [warehouseId, ffRequestId]);

    // Назад — на вкладку склада по kind заявки; пока kind неизвестен — просто на склад
    const backUrl = detail && (detail.kind === 'assembly' || detail.kind === 'inbound')
        ? `/p/${slug}/warehouse/${warehouseId}?tab=${detail.kind === 'assembly' ? 'ff-assembly' : 'ff-inbound'}`
        : `/p/${slug}/warehouse/${warehouseId}`;
    const goBack = () => router.push(backUrl);

    // Значение динамического поля: похожее на ISO-дату — через formatDate, иначе как есть
    const fieldValue = (v: string | null) => {
        if (!v) return '—';
        return /^\d{4}-\d{2}-\d{2}($|[T ])/.test(v) ? formatDate(v) : v;
    };

    const infoCell = (label: string, value: React.ReactNode) => (
        <div key={label}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--color-text-muted)' }}>{label}</div>
            <div style={{ fontSize: 14, fontWeight: 500 }}>{value}</div>
        </div>
    );

    if (loading) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>;

    if (error) {
        return (
            <div className="animate-in">
                <div className="page-header">
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                        <button onClick={goBack} className="btn btn-secondary" style={{ padding: '6px 12px', fontSize: 18, lineHeight: 1 }} title="Назад">&larr;</button>
                        <div>
                            <h1 className="page-title">Заявка ФФ</h1>
                            <p className="page-subtitle">Деталка заявки фулфилмента</p>
                        </div>
                    </div>
                </div>
                <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>
                <div style={{ display: 'flex', justifyContent: 'flex-start', marginTop: 16 }}>
                    <button className="btn btn-secondary" onClick={goBack}>&larr; Назад к складу</button>
                </div>
            </div>
        );
    }

    if (!detail) return <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Заявка не найдена</div>;

    const d = detail;

    const handleUnlink = async () => {
        if (!d.linked_number) return;
        if (!confirm(`Отвязать заявку ${d.number || d.external_id} от документа ${d.linked_number}?`)) return;
        setLinkActing(true);
        setLinkError('');
        try {
            const updated = await api.unlinkFulfillmentRequest(warehouseId, ffRequestId);
            // total_qty в строке — зеркало БД (бывает null); в деталке — живая сумма состава, не затираем
            setDetail(prev => (prev ? { ...prev, ...updated, total_qty: prev.total_qty } : prev));
        } catch (e: unknown) {
            setLinkError(e instanceof Error ? e.message : 'Ошибка отвязки');
        } finally {
            setLinkActing(false);
        }
    };

    // Применить обновлённую строку заявки к деталке (паттерн «не затираем total_qty»)
    const applyUpdatedRow = (updated: FfRequestRow) => {
        setDetail(prev => (prev ? { ...prev, ...updated, total_qty: prev.total_qty } : prev));
    };

    const handleArchiveToggle = async () => {
        setArchActing(true);
        setArchError('');
        try {
            const updated = d.local_archived
                ? await api.unarchiveFulfillmentRequest(warehouseId, ffRequestId)
                : await api.archiveFulfillmentRequest(warehouseId, ffRequestId);
            applyUpdatedRow(updated);
        } catch (e: unknown) {
            setArchError(e instanceof Error ? e.message : (d.local_archived ? 'Ошибка возврата из архива' : 'Ошибка архивирования'));
        } finally {
            setArchActing(false);
        }
    };

    // Создать заявку на сборку из состава ФФ-заявки (kind=assembly, без связи)
    const handleCreateAssembly = async () => {
        if (!confirm(`Создать заявку на сборку из ФФ-заявки ${d.number || d.external_id}?`)) return;
        setLinkActing(true);
        setLinkError('');
        setNotice('');
        try {
            const result = await api.createAssemblyFromFf(warehouseId, ffRequestId);
            applyUpdatedRow(result.request);
            setToast(`Создана заявка на сборку № ${result.assembly_number}`);
            if (result.skipped_barcodes.length > 0) {
                setNotice(`Заявка № ${result.assembly_number} создана без ${formatNumber(result.skipped_barcodes.length, 0)} ШК: ${result.skipped_barcodes.join(', ')}`);
            }
        } catch (e: unknown) {
            setLinkError(e instanceof Error ? e.message : 'Ошибка создания заявки на сборку');
        } finally {
            setLinkActing(false);
        }
    };

    const hasMatch = d.match !== null;

    const productCols: Column[] = [
        { key: 'barcode', label: 'ШК', render: (v: string | null) => v || '—' },
        {
            key: 'article_seller', label: 'Наш артикул',
            render: (_: unknown, p: FfRequestDetailProduct) => (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <span>{p.article_seller ?? p.vendor_code ?? '—'}</span>
                    {p.nomenclature_id === null && (
                        <span className="badge badge-warning" style={{ fontSize: 11, padding: '2px 8px' }}>нет в номенклатуре</span>
                    )}
                </span>
            ),
            exportValue: (p: FfRequestDetailProduct) => p.article_seller ?? p.vendor_code ?? '',
        },
        { key: 'qty', label: 'Заявлено', align: 'right', render: (v: number) => formatNumber(v, 0) },
        ...(hasMatch ? [
            {
                key: 'our_qty', label: 'В нашей заявке', align: 'right',
                render: (v: number | null, p: FfRequestDetailProduct) => {
                    const our = v ?? 0;
                    const same = our === p.qty;
                    return (
                        <span style={{ color: same ? undefined : 'var(--color-danger)', fontWeight: same ? 400 : 600 }}>
                            {formatNumber(our, 0)}
                        </span>
                    );
                },
                exportValue: (p: FfRequestDetailProduct) => String(p.our_qty ?? 0),
            } as Column,
        ] : []),
        // Приёмка-специфика: у сборки приёмки нет — accepted/defect всегда 0
        ...(d.kind !== 'assembly' ? [
            { key: 'accepted_qty', label: 'Принято', align: 'right', render: (v: number) => formatNumber(v, 0) } as Column,
            {
                key: 'defect_qty', label: 'Брак', align: 'right',
                render: (v: number) => (
                    <span style={{ color: v > 0 ? 'var(--color-warning)' : 'var(--color-text-muted)', fontWeight: v > 0 ? 600 : 400 }}>
                        {formatNumber(v, 0)}
                    </span>
                ),
            } as Column,
        ] : []),
    ];

    const summary = [
        { label: 'Позиций', value: d.products.length },
        { label: 'Заявлено', value: d.total_qty },
        // У сборки total_accepted всегда 0 (нет стадии приёмки) — карточка не нужна
        ...(d.kind !== 'assembly' ? [{ label: 'Принято', value: d.total_accepted }] : []),
    ];

    const matchCols: Column[] = [
        { key: 'barcode', label: 'ШК' },
        {
            key: 'article_seller', label: 'Наш артикул',
            render: (v: string | null, m: FfMatchRow) => v ?? m.name ?? '—',
            exportValue: (m: FfMatchRow) => m.article_seller ?? m.name ?? '',
        },
        { key: 'ff_qty', label: 'У ФФ', align: 'right', render: (v: number) => formatNumber(v, 0) },
        { key: 'our_qty', label: 'В нашей заявке', align: 'right', render: (v: number) => formatNumber(v, 0) },
        {
            key: 'diff', label: 'Расхождение', align: 'right',
            render: (v: number, m: FfMatchRow) => (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    {m.ff_qty === 0 && <span className="badge badge-danger" style={{ fontSize: 11, padding: '2px 8px' }}>нет у ФФ</span>}
                    {m.our_qty === 0 && <span className="badge badge-warning" style={{ fontSize: 11, padding: '2px 8px' }}>нет в нашей</span>}
                    <span style={{ color: v > 0 ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 600 }}>
                        {v > 0 ? '+' : ''}{formatNumber(v, 0)}
                    </span>
                </span>
            ),
            exportValue: (m: FfMatchRow) => String(m.diff),
        },
    ];

    return (
        <div className="animate-in">
            {/* Header */}
            <div className="page-header">
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <button onClick={goBack} className="btn btn-secondary" style={{ padding: '6px 12px', fontSize: 18, lineHeight: 1 }} title="Назад к складу">&larr;</button>
                    <div>
                        <h1 className="page-title" style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                            <span>Заявка {d.number || d.external_id}</span>
                            {ffStageBadge(d)}
                            {d.local_archived && (
                                <span className="badge badge-secondary" style={{ fontSize: 11, padding: '2px 8px' }}>В архиве</span>
                            )}
                        </h1>
                        <p className="page-subtitle">{d.kind === 'assembly' ? 'ФФ сборка' : d.kind === 'inbound' ? 'ФФ приёмка' : 'Заявка ФФ'}{d.type_name ? ` — ${d.type_name}` : ''}</p>
                    </div>
                    <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
                        {archError && <span style={{ fontSize: 13, color: 'var(--color-danger)' }}>{archError}</span>}
                        <button
                            className="btn btn-sm btn-secondary"
                            title={d.local_archived ? 'Вернуть из архива в активные' : 'Убрать в архив (локальная пометка)'}
                            onClick={handleArchiveToggle}
                            disabled={archActing}
                        >
                            {archActing ? '...' : (d.local_archived ? 'Вернуть' : 'В архив')}
                        </button>
                    </div>
                </div>
            </div>

            {notice && (
                <div style={{ color: 'var(--color-warning)', marginBottom: 12, display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
                    <span>{notice}</span>
                    <button className="btn btn-sm btn-secondary" onClick={() => setNotice('')}>✕</button>
                </div>
            )}

            {/* Шапка: реквизиты заявки */}
            <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
                    {infoCell('Тип', d.type_name || '—')}
                    {infoCell('Стадия', (
                        <>
                            <div>{d.stage_title || d.status || '—'}</div>
                            {d.stage_description && (
                                <div style={{ fontSize: 12, fontWeight: 400, color: 'var(--color-text-muted)' }}>{d.stage_description}</div>
                            )}
                        </>
                    ))}
                    {infoCell('Исполнитель', d.executor || '—')}
                    {infoCell('Создатель', d.creator || '—')}
                    {infoCell('Кабинет', d.customer_name || '—')}
                </div>
                {d.comment && (
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 12 }}>
                        Комментарий: <span style={{ color: 'var(--color-text)' }}>{d.comment}</span>
                    </div>
                )}
                {(d.kind === 'assembly' || d.kind === 'inbound') ? (
                    <div style={{ fontSize: 13, marginTop: 12, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                        {d.linked_number ? (
                            <>
                                <span>
                                    Связана с <span style={{ fontWeight: 600 }}>{d.linked_number}</span>
                                    {d.linked_status ? ` (${FF_LINKED_STATUS_LABELS[d.linked_status] || d.linked_status})` : ''}
                                </span>
                                <button className="btn btn-sm btn-secondary" onClick={handleUnlink} disabled={linkActing}>
                                    {linkActing ? '...' : 'Отвязать'}
                                </button>
                            </>
                        ) : (
                            <>
                                <button className="btn btn-sm btn-secondary" onClick={() => setLinkOpen(true)} disabled={linkActing}>Связать</button>
                                {d.kind === 'assembly' && d.assembly_request_id == null && (
                                    <button
                                        className="btn btn-sm btn-primary"
                                        title="Создать заявку на сборку из состава этой ФФ-заявки"
                                        onClick={handleCreateAssembly}
                                        disabled={linkActing}
                                    >
                                        {linkActing ? '...' : 'Создать заявку'}
                                    </button>
                                )}
                            </>
                        )}
                        {linkError && <span style={{ color: 'var(--color-danger)' }}>{linkError}</span>}
                    </div>
                ) : d.linked_number && (
                    <div style={{ fontSize: 13, marginTop: 12 }}>
                        Связана с <span style={{ fontWeight: 600 }}>{d.linked_number}</span>
                        {d.linked_status ? ` (${FF_LINKED_STATUS_LABELS[d.linked_status] || d.linked_status})` : ''}
                    </div>
                )}
            </div>

            {/* Поля заявки */}
            {d.fields.length > 0 && (
                <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
                        {d.fields.map((f, i) => (
                            <div key={`${f.field ?? f.name ?? 'f'}-${i}`}>
                                <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--color-text-muted)' }}>{f.name || f.field || '—'}</div>
                                <div style={{ fontSize: 14, fontWeight: 500 }}>{fieldValue(f.value)}</div>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Сверка со связанным нашим документом */}
            {d.match && (
                <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                        {d.match.matched ? (
                            <span className="badge badge-success" style={{ fontSize: 13, padding: '4px 12px' }}>
                                ✓ Состав совпадает с {d.linked_number}
                            </span>
                        ) : (
                            <span className="badge badge-danger" style={{ fontSize: 13, padding: '4px 12px' }}>
                                Расхождение с {d.linked_number}: {formatNumber(d.match.mismatches.length, 0)} поз.
                            </span>
                        )}
                        <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                            У ФФ: {formatNumber(d.match.ff_total, 0)} шт / {formatNumber(d.match.ff_positions, 0)} поз. ·
                            {' '}В нашей заявке: {formatNumber(d.match.our_total, 0)} шт / {formatNumber(d.match.our_positions, 0)} поз.
                        </span>
                    </div>
                    {!d.match.matched && (
                        <div style={{ marginTop: 12 }}>
                            <TanStackDataTable
                                columns={matchCols}
                                data={d.match.mismatches}
                                emptyText="Расхождений нет"
                                emptyIcon="✓"
                                exportName="ff_request_mismatches"
                            />
                        </div>
                    )}
                </div>
            )}

            {/* Сводка */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 16 }}>
                {summary.map(s => (
                    <div key={s.label} className="glass-card" style={{ padding: 16, textAlign: 'center' }}>
                        <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>{s.label}</div>
                        <div style={{ fontSize: 24, fontWeight: 700 }}>{formatNumber(s.value, 0)}</div>
                    </div>
                ))}
            </div>

            {/* Товары */}
            <TanStackDataTable
                columns={productCols}
                data={d.products}
                emptyText="Провайдер не вернул состав заявки"
                emptyIcon="📦"
                exportName="ff_request_products"
            />

            {/* История стадий */}
            {d.stage_logs.length > 0 && (
                <div className="glass-card" style={{ padding: 20, marginTop: 16 }}>
                    <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 8px' }}>История стадий</h3>
                    <div style={{ display: 'flex', flexDirection: 'column' }}>
                        {d.stage_logs.map((l, i) => (
                            <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 12, fontSize: 13, padding: '6px 0', borderBottom: '1px solid var(--color-border)' }}>
                                <span style={{ fontWeight: 500, flex: 1 }}>{l.stage || '—'}</span>
                                <span style={{ color: 'var(--color-text-muted)' }}>{l.executor || '—'}</span>
                                <span style={{ color: 'var(--color-text-muted)' }}>{l.created_at || ''}</span>
                                <span style={{ color: 'var(--color-text-muted)' }}>{l.spent_time || ''}</span>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* Bottom back */}
            <div style={{ display: 'flex', justifyContent: 'flex-start', marginTop: 20, paddingBottom: 20 }}>
                <button className="btn btn-secondary" onClick={goBack}>&larr; Назад к складу</button>
            </div>

            {/* Модал «Связать» */}
            {linkOpen && (d.kind === 'assembly' || d.kind === 'inbound') && (
                <FfLinkModal
                    warehouseId={warehouseId}
                    kind={d.kind}
                    request={d}
                    onClose={() => setLinkOpen(false)}
                    onLinked={updated => {
                        // total_qty в строке — зеркало БД (бывает null); в деталке — живая сумма состава, не затираем
                        applyUpdatedRow(updated);
                        setLinkOpen(false);
                    }}
                />
            )}

            {toast && <Toast message={toast} onClose={() => setToast('')} />}
        </div>
    );
}
