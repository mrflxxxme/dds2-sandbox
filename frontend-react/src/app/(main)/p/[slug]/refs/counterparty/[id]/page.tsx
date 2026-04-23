'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import CounterpartyTypeBadge, { TYPE_CONFIG } from '@/components/CounterpartyTypeBadge';
import CurrencySplitStats from '@/components/CurrencySplitStats';
import DocumentUploader from '@/components/DocumentUploader';
import TabLayout from '@/components/TabLayout';
import type {
    CounterpartyDetail, CounterpartyDocument, DocType,
    CounterpartyUpdate, CounterpartyTransactionItem, CounterpartyType,
} from '@/types/api';

const TODAY = new Date().toISOString().slice(0, 10);
const HALF_YEAR_AGO = new Date(Date.now() - 180 * 24 * 3600 * 1000).toISOString().slice(0, 10);

const DOC_TYPE_LABELS: Record<DocType, string> = {
    CONTRACT: 'Договор',
    CERTIFICATE: 'Сертификат',
    INVOICE: 'Счёт',
    OTHER: 'Прочее',
};

export default function CounterpartyDetailPage() {
    const params = useParams();
    const slug = params.slug as string;
    const id = Number(params.id);
    const router = useRouter();

    const [detail, setDetail] = useState<CounterpartyDetail | null>(null);
    const [documents, setDocuments] = useState<CounterpartyDocument[]>([]);
    const [transactions, setTransactions] = useState<CounterpartyTransactionItem[]>([]);
    const [txTotal, setTxTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [activeTab, setActiveTab] = useState(0);

    // Date range for stats
    const [dateFrom, setDateFrom] = useState(HALF_YEAR_AGO);
    const [dateTo, setDateTo] = useState(TODAY);

    // Edit
    const [editing, setEditing] = useState(false);
    const [editForm, setEditForm] = useState<CounterpartyUpdate>({});
    const [saving, setSaving] = useState(false);

    const loadDetail = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [d, docs, txs] = await Promise.all([
                api.getCounterparty(id, { date_from: dateFrom, date_to: dateTo }),
                api.listCounterpartyDocuments(id),
                api.getCounterpartyTransactions(id, { date_from: dateFrom, date_to: dateTo, limit: 500 }),
            ]);
            setDetail(d);
            setDocuments(docs);
            setTransactions(txs.items);
            setTxTotal(txs.total);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [id, dateFrom, dateTo]);

    useEffect(() => { loadDetail(); }, [loadDetail]);

    const handleDelete = async () => {
        if (!confirm(`Удалить контрагента "${detail?.name}"?`)) return;
        try {
            await api.deleteCounterparty(id);
            router.push(`/p/${slug}/refs/counterparty`);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка удаления');
        }
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            await api.updateCounterparty(id, editForm);
            setEditing(false);
            await loadDetail();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    const handleDocUpload = async (file: File, docType: DocType) => {
        await api.uploadCounterpartyDocument(id, file, docType);
        const docs = await api.listCounterpartyDocuments(id);
        setDocuments(docs);
    };

    const handleDocDelete = async (docId: number) => {
        if (!confirm('Удалить документ?')) return;
        await api.deleteCounterpartyDocument(id, docId);
        setDocuments(docs => docs.filter(d => d.id !== docId));
    };

    if (loading && !detail) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
            </div>
        );
    }

    if (error && !detail) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={loadDetail}>Повторить</button>
                </div>
            </div>
        );
    }

    if (!detail) return null;

    const tabs = [
        { label: 'Статистика', icon: '📊' },
        { label: `Документы (${detail.docs_count})`, icon: '📎' },
    ];

    return (
        <div className="animate-in">
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => router.push(`/p/${slug}/refs/counterparty`)}
                    >
                        ← Назад
                    </button>
                    <div>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                            <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>{detail.name}</h1>
                            <CounterpartyTypeBadge type={detail.primary_type} />
                        </div>
                        {detail.inn && (
                            <div style={{ fontSize: 13, color: 'var(--color-text-dim)', fontFamily: 'monospace', marginTop: 4 }}>
                                ИНН: {detail.inn} {detail.kpp ? `/ КПП: ${detail.kpp}` : ''}
                            </div>
                        )}
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => {
                            setEditForm({
                                name: detail.name,
                                primary_type: detail.primary_type,
                                inn: detail.inn,
                                kpp: detail.kpp,
                                contract_number: detail.contract_number,
                                notes: detail.notes,
                            });
                            setEditing(true);
                        }}
                    >
                        Изменить
                    </button>
                    <button className="btn btn-danger btn-sm" onClick={handleDelete}>Удалить</button>
                </div>
            </div>

            {error && (
                <div style={{ color: 'var(--color-danger)', marginBottom: 16, fontSize: 13 }}>{error}</div>
            )}

            {/* Edit form */}
            {editing && (
                <div className="glass-card" style={{ marginBottom: 16 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
                        <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Редактировать контрагента</h3>
                        <button className="btn btn-secondary btn-sm" onClick={() => setEditing(false)}>✕</button>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                        <div className="form-group" style={{ gridColumn: '1/-1' }}>
                            <label className="form-label">Название *</label>
                            <input className="form-input" value={editForm.name ?? ''} onChange={e => setEditForm(f => ({ ...f, name: e.target.value }))} />
                        </div>
                        <div className="form-group" style={{ gridColumn: '1/-1' }}>
                            <label className="form-label">Категория *</label>
                            <select
                                className="form-input"
                                value={editForm.primary_type ?? 'OTHER'}
                                onChange={e => setEditForm(f => ({ ...f, primary_type: e.target.value as CounterpartyType }))}
                            >
                                {(Object.entries(TYPE_CONFIG) as [CounterpartyType, { label: string; icon: string }][]).map(([key, cfg]) => (
                                    <option key={key} value={key}>{cfg.icon} {cfg.label}</option>
                                ))}
                            </select>
                        </div>
                        <div className="form-group">
                            <label className="form-label">ИНН</label>
                            <input className="form-input" value={editForm.inn ?? ''} onChange={e => setEditForm(f => ({ ...f, inn: e.target.value || null }))} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">КПП</label>
                            <input className="form-input" value={editForm.kpp ?? ''} onChange={e => setEditForm(f => ({ ...f, kpp: e.target.value || null }))} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Контракт</label>
                            <input className="form-input" value={editForm.contract_number ?? ''} onChange={e => setEditForm(f => ({ ...f, contract_number: e.target.value || null }))} />
                        </div>
                        <div className="form-group" style={{ gridColumn: '1/-1' }}>
                            <label className="form-label">Примечания</label>
                            <input className="form-input" value={editForm.notes ?? ''} onChange={e => setEditForm(f => ({ ...f, notes: e.target.value || null }))} />
                        </div>
                    </div>
                    <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
                        <button className="btn btn-primary btn-sm" onClick={handleSave} disabled={saving}>
                            {saving ? 'Сохранение...' : 'Сохранить'}
                        </button>
                        <button className="btn btn-secondary btn-sm" onClick={() => setEditing(false)}>Отмена</button>
                    </div>
                </div>
            )}

            {/* Date range picker for stats */}
            <div className="glass-card" style={{ marginBottom: 16, padding: '12px 16px' }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                    <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Период статистики:</span>
                    <input type="date" className="form-input" style={{ width: 160 }} value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
                    <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                    <input type="date" className="form-input" style={{ width: 160 }} value={dateTo} onChange={e => setDateTo(e.target.value)} />
                </div>
            </div>

            {/* Stats */}
            <div style={{ marginBottom: 20 }}>
                <CurrencySplitStats rub={detail.stats_rub} cny={detail.stats_cny} loading={loading} />
            </div>

            {/* Linked entities */}
            {(detail.linked_warehouses.length > 0 || detail.linked_suppliers.length > 0) && (
                <div style={{ display: 'flex', gap: 16, marginBottom: 20 }}>
                    {detail.linked_warehouses.length > 0 && (
                        <div className="glass-card" style={{ flex: 1 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>🏢 Привязанные склады</div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                {detail.linked_warehouses.map(w => (
                                    <button
                                        key={w.id}
                                        type="button"
                                        onClick={() => router.push(`/p/${slug}/warehouse/${w.id}`)}
                                        style={{
                                            background: 'none', border: 'none', padding: '4px 0',
                                            fontSize: 13, color: 'var(--color-accent)', cursor: 'pointer',
                                            textAlign: 'left',
                                        }}
                                    >
                                        {w.name} →
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}
                    {detail.linked_suppliers.length > 0 && (
                        <div className="glass-card" style={{ flex: 1 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>🏭 Связанные поставщики</div>
                            {detail.linked_suppliers.map(s => (
                                <div key={s.id} style={{ fontSize: 13, padding: '4px 0' }}>{s.name}</div>
                            ))}
                        </div>
                    )}
                </div>
            )}

            {/* Notes */}
            {detail.notes && (
                <div className="glass-card" style={{ marginBottom: 20, padding: '12px 16px' }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 4 }}>Примечания</div>
                    <div style={{ fontSize: 14 }}>{detail.notes}</div>
                </div>
            )}

            {/* Tabs */}
            <div>
                {/* Tab navigation */}
                <div style={{ display: 'flex', gap: 4, marginBottom: 16 }}>
                    {tabs.map((t, i) => (
                        <button
                            key={i}
                            className={`btn ${activeTab === i ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                            onClick={() => setActiveTab(i)}
                        >
                            {t.icon} {t.label}
                        </button>
                    ))}
                </div>

                {/* Статистика */}
                {activeTab === 0 && (
                    <div>
                        {transactions.length === 0 ? (
                            <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                                Транзакций за выбранный период не найдено
                            </div>
                        ) : (
                            <div className="glass-card">
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                                    <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>
                                        Транзакции ({formatNumber(txTotal, 0)}
                                        {txTotal > transactions.length ? `, показано ${formatNumber(transactions.length, 0)}` : ''})
                                    </h3>
                                    <button
                                        className="btn btn-secondary btn-sm"
                                        onClick={() => exportToExcel(
                                            transactions.map(t => ({
                                                Дата: formatDate(t.date),
                                                Счёт: t.account,
                                                Валюта: t.currency,
                                                Приход: Number(t.income),
                                                Расход: Number(t.expense),
                                                Назначение: t.purpose ?? '',
                                                Тип: t.event_type2 ?? '',
                                                'Займ-тип': t.loan_payment_type ?? '',
                                                Контракт: t.contract_number ?? '',
                                            })),
                                            'counterparty_transactions',
                                        )}
                                    >
                                        Excel
                                    </button>
                                </div>
                                <div style={{ overflowX: 'auto' }}>
                                    <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                                        <thead>
                                            <tr style={{ borderBottom: '1px solid var(--color-border)', textAlign: 'left' }}>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Дата</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Счёт</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', textAlign: 'right' }}>Приход</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', textAlign: 'right' }}>Расход</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Вал.</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Назначение</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {transactions.map(t => (
                                                <tr key={t.id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                                    <td style={{ padding: '8px 8px', whiteSpace: 'nowrap' }}>{formatDate(t.date)}</td>
                                                    <td style={{ padding: '8px 8px', fontFamily: 'monospace', fontSize: 12, color: 'var(--color-text-muted)' }}>
                                                        {t.account.length > 10 ? `…${t.account.slice(-10)}` : t.account}
                                                    </td>
                                                    <td style={{ padding: '8px 8px', textAlign: 'right', color: Number(t.income) > 0 ? 'var(--color-success)' : undefined, fontVariantNumeric: 'tabular-nums' }}>
                                                        {Number(t.income) > 0 ? formatNumber(t.income) : ''}
                                                    </td>
                                                    <td style={{ padding: '8px 8px', textAlign: 'right', color: Number(t.expense) > 0 ? 'var(--color-danger)' : undefined, fontVariantNumeric: 'tabular-nums' }}>
                                                        {Number(t.expense) > 0 ? formatNumber(t.expense) : ''}
                                                    </td>
                                                    <td style={{ padding: '8px 8px', fontSize: 12 }}>{t.currency}</td>
                                                    <td style={{ padding: '8px 8px', fontSize: 12, maxWidth: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={t.purpose ?? ''}>
                                                        {t.purpose ?? '—'}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        )}
                    </div>
                )}

                {/* Документы */}
                {activeTab === 1 && (
                    <div>
                        <div className="glass-card" style={{ marginBottom: 16 }}>
                            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Загрузить документ</div>
                            <DocumentUploader onUpload={handleDocUpload} />
                        </div>

                        {documents.length === 0 ? (
                            <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                                Документов пока нет
                            </div>
                        ) : (
                            <div className="glass-card">
                                <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
                                    Документы ({documents.length})
                                </div>
                                {documents.map(doc => (
                                    <div
                                        key={doc.id}
                                        style={{
                                            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                                            padding: '10px 0', borderBottom: '1px solid var(--color-border)',
                                        }}
                                    >
                                        <div style={{ flex: 1, minWidth: 0 }}>
                                            <div style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                {doc.original_filename || 'Без имени'}
                                            </div>
                                            <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginTop: 2 }}>
                                                {DOC_TYPE_LABELS[doc.doc_type]} · {doc.file_size ? `${(doc.file_size / 1024).toFixed(1)} KB` : ''} · {formatDate(doc.uploaded_at)}
                                            </div>
                                        </div>
                                        <div style={{ display: 'flex', gap: 8 }}>
                                            <a
                                                href={doc.minio_path_signed_url}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                                className="btn btn-secondary btn-sm"
                                                style={{ padding: '2px 8px', fontSize: 11 }}
                                            >
                                                Скачать
                                            </a>
                                            <button
                                                className="btn btn-danger btn-sm"
                                                style={{ padding: '2px 8px', fontSize: 11 }}
                                                onClick={() => handleDocDelete(doc.id)}
                                            >
                                                ✕
                                            </button>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                )}

            </div>
        </div>
    );
}
