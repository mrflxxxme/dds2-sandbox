'use client';
import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate, formatDateTime } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import Toast from '@/components/Toast';
import type { FfMatchRow, FfRequestDetail, FfRequestDetailProduct, FfRequestRow, FfStatusEvent } from '@/types/api';
import type { Column } from '@/components/DataTable';
import { FF_LINKED_STATUS_LABELS, FfLinkModal, ffEventBadge, ffEventSummary, ffSkippedNotice, ffStageBadge } from '../../ff-shared';

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

    // История синхронизации этой заявки (журнал смены статусов; ошибку глушим — секция вторична)
    const [syncHistory, setSyncHistory] = useState<FfStatusEvent[]>([]);

    // Связывание с нашим документом (модал-пикер + отвязка)
    const [linkOpen, setLinkOpen] = useState(false);
    const [linkActing, setLinkActing] = useState(false);
    const [linkError, setLinkError] = useState('');

    // Архив (local_archived) и создание заявки на сборку из ФФ
    const [archActing, setArchActing] = useState(false);
    const [archError, setArchError] = useState('');
    const [toast, setToast] = useState('');
    const [notice, setNotice] = useState('');

    // Ручной ШК для строки без номенклатуры (migfull: карточка без штрихкода)
    const [skuModal, setSkuModal] = useState<{ guid: string; name: string } | null>(null);
    const [skuBarcode, setSkuBarcode] = useState('');
    const [skuSaving, setSkuSaving] = useState(false);
    const [skuError, setSkuError] = useState('');

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

    useEffect(() => {
        const controller = new AbortController();
        api.getFfStatusHistory(warehouseId, { ffRequestId })
            .then(r => { if (!controller.signal.aborted) setSyncHistory(r); })
            .catch(() => { if (!controller.signal.aborted) setSyncHistory([]); });
        return () => controller.abort();
    }, [warehouseId, ffRequestId]);

    // Назад — на вкладку «Фулфилмент» склада, в под-вкладку по kind заявки.
    // kind=return и недозагруженная деталка раньше падали в голый URL склада —
    // юзер улетал в «Приёмки» раздела Склад вместо ФФ, откуда пришёл.
    const backTab = detail?.kind === 'assembly'
        ? 'ff-assembly'
        : detail?.kind === 'inbound'
            ? 'ff-inbound'
            : detail?.kind === 'return'
                ? 'ff-return'
                : 'fulfillment';
    const backUrl = `/p/${slug}/warehouse/${warehouseId}?tab=${backTab}`;
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

    // Создать заявку на сборку из состава ФФ-заявки и сразу связать их (kind=assembly)
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
                setNotice(ffSkippedNotice(result.assembly_number, result.skipped_barcodes));
            }
        } catch (e: unknown) {
            setLinkError(e instanceof Error ? e.message : 'Ошибка создания заявки на сборку');
        } finally {
            setLinkActing(false);
        }
    };

    // Перечитать деталку (после ручной привязки ШК — расхождение пересчитывается на бэке)
    const reloadDetail = async () => {
        try {
            setDetail(await api.getFfRequestDetail(warehouseId, ffRequestId));
        } catch { /* оставляем текущее состояние */ }
    };

    // Сохранить ручной ШК короба для строки без номенклатуры (по product_guid)
    const handleSaveSku = async () => {
        if (!skuModal) return;
        const bc = skuBarcode.trim();
        if (!/^\d{8,}$/.test(bc)) {
            setSkuError('ШК — только цифры (короб ITF14 — 14, россыпь EAN13 — 13)');
            return;
        }
        setSkuSaving(true);
        setSkuError('');
        try {
            await api.setFfGuidBarcode(warehouseId, skuModal.guid, { barcode: bc });
            setSkuModal(null);
            setSkuBarcode('');
            setToast('ШК сохранён — расхождение пересчитано');
            await reloadDetail();
        } catch (e: unknown) {
            setSkuError(e instanceof Error ? e.message : 'Ошибка сохранения ШК');
        } finally {
            setSkuSaving(false);
        }
    };

    const hasMatch = d.match !== null;

    // Мульти-связка: сёстры той же машины (N заявок ФФ → один наш документ).
    // groupCount > 1 → сверка построена по СУММЕ составов группы (бэк).
    const siblings = d.sibling_requests ?? [];
    const groupCount = d.mismatch_group_numbers?.length ?? 0;
    // «2 заявки» / «5 заявок» — счётная форма для подписи «У ФФ (...)»
    const reqWord = (n: number) => {
        if (n % 10 === 1 && n % 100 !== 11) return 'заявка';
        if (n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14)) return 'заявки';
        return 'заявок';
    };

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
                    {p.nomenclature_id === null && p.product_guid && (
                        <button
                            className="btn btn-sm btn-secondary"
                            style={{ fontSize: 11, padding: '2px 8px' }}
                            title="Привязать ШК короба/россыпи (карточка ФФ без штрихкода)"
                            onClick={() => { setSkuModal({ guid: p.product_guid as string, name: p.name ?? '' }); setSkuBarcode(''); setSkuError(''); }}
                        >
                            Указать ШК
                        </button>
                    )}
                </span>
            ),
            exportValue: (p: FfRequestDetailProduct) => p.article_seller ?? p.vendor_code ?? '',
        },
        {
            key: 'qty', label: 'Заявлено', align: 'right',
            render: (v: number, p: FfRequestDetailProduct) => (
                <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'flex-end' }}>
                    <span>{formatNumber(v, 0)}</span>
                    {p.box_qty > 0 && (
                        <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                            {formatNumber(p.box_qty, 0)} кор × {formatNumber(p.units_per_box, 0)}
                        </span>
                    )}
                </span>
            ),
            exportValue: (p: FfRequestDetailProduct) => p.qty,
        },
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

            {/* Заявки этой машины (мульти-связка: N заявок ФФ → один наш документ) */}
            {siblings.length > 0 && (
                <div className="glass-card" style={{ padding: 16, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Заявки этой машины:</span>
                    <span className="badge badge-info" style={{ fontSize: 12, padding: '4px 10px' }} title="Текущая заявка">
                        {d.number || d.external_id}
                        {d.total_qty ? ` · ${formatNumber(d.total_qty, 0)} шт` : ''}
                    </span>
                    {siblings.map(s => (
                        <Link
                            key={s.id}
                            href={`/p/${slug}/warehouse/${warehouseId}/ff-request/${s.id}`}
                            className="badge badge-secondary"
                            style={{ fontSize: 12, padding: '4px 10px', textDecoration: 'none' }}
                            title="Открыть заявку группы"
                        >
                            {s.number || `#${s.id}`}
                            {s.total_qty != null ? ` · ${formatNumber(s.total_qty, 0)} шт` : ''}
                        </Link>
                    ))}
                </div>
            )}

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
                                ✓ Состав совпадает с {d.linked_number}{groupCount > 1 ? ` (по ${groupCount} заявкам)` : ''}
                            </span>
                        ) : (
                            <span className="badge badge-danger" style={{ fontSize: 13, padding: '4px 12px' }}>
                                Расхождение с {d.linked_number}{groupCount > 1 ? ` (по ${groupCount} заявкам машины)` : ''}: {formatNumber(d.match.mismatches.length, 0)} поз.
                            </span>
                        )}
                        <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                            У ФФ{groupCount > 1 ? ` (${groupCount} ${reqWord(groupCount)})` : ''}: {formatNumber(d.match.ff_total, 0)} шт / {formatNumber(d.match.ff_positions, 0)} поз. ·
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

            {/* История синхронизации DDS (журнал смены статусов — для всех провайдеров) */}
            {syncHistory.length > 0 && (
                <div className="glass-card" style={{ padding: 20, marginTop: 16 }}>
                    <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 4px' }}>История синхронизации</h3>
                    <p style={{ fontSize: 12, color: 'var(--color-text-muted)', margin: '0 0 12px' }}>
                        Изменения статуса заявки, зафиксированные синхронизацией DDS.
                    </p>
                    <div style={{ display: 'flex', flexDirection: 'column' }}>
                        {syncHistory.map(e => (
                            <div key={e.id} style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 13, padding: '8px 0', borderBottom: '1px solid var(--color-border)' }}>
                                <span style={{ color: 'var(--color-text-muted)', whiteSpace: 'nowrap', minWidth: 140 }}>{formatDateTime(e.changed_at)}</span>
                                {ffEventBadge(e)}
                                <span style={{ flex: 1 }}>{ffEventSummary(e)}</span>
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

            {/* Модал «Указать ШК» — ручная привязка ШК короба/россыпи к строке без номенклатуры */}
            {skuModal && (
                <div
                    style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000, padding: 16 }}
                    onClick={() => !skuSaving && setSkuModal(null)}
                >
                    <div className="glass-card" style={{ padding: 24, maxWidth: 460, width: '100%' }} onClick={e => e.stopPropagation()}>
                        <h3 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 4px' }}>Указать ШК</h3>
                        <p style={{ fontSize: 13, color: 'var(--color-text-muted)', margin: '0 0 12px' }}>
                            Карточка товара у ФФ без штрихкода. Укажите ШК короба (ITF14, 14 цифр) — сведём к россыпи по «короб N шт.»; либо ШК россыпи (EAN13).
                        </p>
                        {skuModal.name && (
                            <div style={{ fontSize: 13, marginBottom: 12 }}>
                                Товар: <span style={{ fontWeight: 500 }}>{skuModal.name}</span>
                            </div>
                        )}
                        <input
                            type="text"
                            inputMode="numeric"
                            value={skuBarcode}
                            onChange={e => setSkuBarcode(e.target.value)}
                            onKeyDown={e => { if (e.key === 'Enter') handleSaveSku(); }}
                            placeholder="ШК короба (ITF14) или россыпи (EAN13)"
                            autoFocus
                            style={{ width: '100%', padding: '8px 12px', fontSize: 14, borderRadius: 8, border: '1px solid var(--color-border)', background: 'var(--color-bg-card)', color: 'var(--color-text)' }}
                        />
                        {skuError && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 8 }}>{skuError}</div>}
                        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
                            <button className="btn btn-secondary" onClick={() => setSkuModal(null)} disabled={skuSaving}>Отмена</button>
                            <button className="btn btn-primary" onClick={handleSaveSku} disabled={skuSaving}>{skuSaving ? '...' : 'Сохранить'}</button>
                        </div>
                    </div>
                </div>
            )}

            {toast && <Toast message={toast} onClose={() => setToast('')} />}
        </div>
    );
}
