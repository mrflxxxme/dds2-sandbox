'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';

import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/utils';
import type { FboAuditListEntry } from '@/types/api';

const ACTION_LABEL: Record<string, string> = {
    ARCHIVE: 'В архив',
    UNARCHIVE: 'Из архива / автоправило',
    BULK_RESTORE: 'Массовое восстановление',
    RETURN: 'Обработана недоприёмка',
    EXCESS: 'Списан излишек',
    REASSIGN: 'Привязана как доприёмка',
    REVERT: 'Откат действия',
};

const ACTION_ICON: Record<string, string> = {
    ARCHIVE: '📁',
    UNARCHIVE: '♻️',
    BULK_RESTORE: '♻️',
    RETURN: '↩️',
    EXCESS: '📤',
    REASSIGN: '📦',
    REVERT: '↶',
};

function formatPayload(entry: FboAuditListEntry): string {
    const p = entry.payload || {};
    switch (entry.action) {
        case 'ARCHIVE':
        case 'UNARCHIVE': {
            const fmt = (v: unknown) => v === true ? 'в архиве' : v === false ? 'активна' : 'автоправило';
            return `${fmt(p.old)} → ${fmt(p.new)}`;
        }
        case 'REASSIGN': {
            const tgt = p.target_wb_supply_id;
            const qty = p.reassigned_qty;
            return `${qty} шт → в поставку FBW-${tgt}`;
        }
        case 'RETURN': {
            const type = p.return_type || '—';
            const qty = p.return_qty || 0;
            const receipt = p.receipt_number;
            return `${qty} шт, тип: ${type}${receipt ? `, приёмка ${receipt}` : ''}`;
        }
        case 'EXCESS': {
            const qty = p.excess_qty || 0;
            const num = p.shipment_number;
            return `${qty} шт, отгрузка ${num || '—'}`;
        }
        case 'REVERT': {
            const orig = p.action || '';
            return `откат действия «${ACTION_LABEL[orig as string] || orig}»`;
        }
        case 'BULK_RESTORE':
            return 'восстановлена из архива (bulk)';
        default:
            return '';
    }
}

export default function FboHistoryPage() {
    const { slug } = useParams() as { slug: string };

    const [entries, setEntries] = useState<FboAuditListEntry[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [actionFilter, setActionFilter] = useState('');
    const [supplySearch, setSupplySearch] = useState('');
    const [supplySearchInput, setSupplySearchInput] = useState('');
    const [page, setPage] = useState(0);
    const [reverting, setReverting] = useState<number | null>(null);
    const PAGE_SIZE = 100;

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const resp = await api.getFboAuditList({
                action: actionFilter || undefined,
                supply_wb_id: supplySearch || undefined,
                limit: PAGE_SIZE,
                offset: page * PAGE_SIZE,
            });
            setEntries(resp.entries);
            setTotal(resp.total);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [actionFilter, supplySearch, page]);

    useEffect(() => { load(); }, [load]);

    const revert = async (auditId: number) => {
        if (!confirm('Откатить это действие?')) return;
        setReverting(auditId);
        try {
            await api.revertFboAudit(auditId);
            await load();
        } catch (e) {
            alert('Ошибка отката: ' + (e instanceof Error ? e.message : 'неизвестно'));
        } finally {
            setReverting(null);
        }
    };

    const totalPages = Math.ceil(total / PAGE_SIZE);

    return (
        <div className="animate-in" style={{ padding: 24 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
                <div>
                    <h1 style={{ fontSize: 28, fontWeight: 700, letterSpacing: '-0.03em' }}>История действий FBO</h1>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 4 }}>
                        Всего записей: {total}
                    </div>
                </div>
                <Link href={`/p/${slug}/warehouse/fbo-supplies`} className="btn btn-secondary">
                    ← К поставкам
                </Link>
            </div>

            <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                    <input
                        type="text"
                        className="form-input"
                        placeholder="Поиск по номеру поставки WB..."
                        value={supplySearchInput}
                        onChange={e => setSupplySearchInput(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') { setSupplySearch(supplySearchInput.trim()); setPage(0); } }}
                        style={{ flex: '1 1 240px', minWidth: 200 }}
                    />
                    <select
                        className="form-input"
                        value={actionFilter}
                        onChange={e => { setActionFilter(e.target.value); setPage(0); }}
                        style={{ minWidth: 220 }}
                    >
                        <option value="">Все действия</option>
                        {Object.entries(ACTION_LABEL).map(([k, v]) => (
                            <option key={k} value={k}>{v}</option>
                        ))}
                    </select>
                    {(supplySearch || actionFilter) && (
                        <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => {
                                setSupplySearch(''); setSupplySearchInput('');
                                setActionFilter('');
                                setPage(0);
                            }}
                        >
                            Сбросить
                        </button>
                    )}
                </div>
            </div>

            {loading ? (
                <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Загрузка...
                </div>
            ) : error ? (
                <div className="glass-card" style={{ padding: 24, color: 'var(--color-danger)' }}>
                    {error}
                </div>
            ) : entries.length === 0 ? (
                <div className="glass-card" style={{ padding: 40, textAlign: 'center' }}>
                    <div style={{ fontSize: 40, marginBottom: 8 }}>📜</div>
                    <div style={{ fontSize: 16, fontWeight: 500 }}>История пуста</div>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 4 }}>
                        Здесь будут отображаться действия пользователей над FBO-поставками: архив, возвраты, излишки, привязки доприёмок.
                    </div>
                </div>
            ) : (
                <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                    <table className="data-table" style={{ fontSize: 13, margin: 0 }}>
                        <thead>
                            <tr>
                                <th style={{ paddingLeft: 12, width: 40 }}></th>
                                <th>Действие</th>
                                <th>Поставка</th>
                                <th>Детали</th>
                                <th>Кто</th>
                                <th style={{ textAlign: 'right' }}>Когда</th>
                                <th style={{ textAlign: 'right', paddingRight: 12, width: 120 }}></th>
                            </tr>
                        </thead>
                        <tbody>
                            {entries.map(e => {
                                const isReverted = e.reverted_at !== null;
                                return (
                                    <tr key={e.id} style={isReverted ? { opacity: 0.55 } : undefined}>
                                        <td style={{ paddingLeft: 12, fontSize: 18 }}>{ACTION_ICON[e.action] || '•'}</td>
                                        <td>
                                            <div style={{ fontWeight: 500 }}>
                                                {ACTION_LABEL[e.action] || e.action}
                                            </div>
                                            {isReverted && (
                                                <span className="badge badge-secondary" style={{ fontSize: 10 }}>
                                                    откачено
                                                </span>
                                            )}
                                        </td>
                                        <td>
                                            <Link
                                                href={`/p/${slug}/warehouse/fbo-supplies?search=${encodeURIComponent(e.supply_wb_id)}`}
                                                style={{ color: 'var(--color-accent)', textDecoration: 'none', fontWeight: 500 }}
                                            >
                                                FBW-{e.supply_wb_id}
                                            </Link>
                                            {e.warehouse_name && (
                                                <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                                    {e.warehouse_name}
                                                </div>
                                            )}
                                        </td>
                                        <td style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                            {formatPayload(e)}
                                        </td>
                                        <td style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                            {e.username || '—'}
                                        </td>
                                        <td style={{ textAlign: 'right', fontSize: 12, color: 'var(--color-text-muted)' }}>
                                            {formatDateTime(e.created_at)}
                                        </td>
                                        <td style={{ textAlign: 'right', paddingRight: 12 }}>
                                            {e.revertible && !isReverted && (
                                                <button
                                                    className="btn btn-secondary btn-sm"
                                                    onClick={() => revert(e.id)}
                                                    disabled={reverting !== null}
                                                >
                                                    {reverting === e.id ? '...' : '↶ Откатить'}
                                                </button>
                                            )}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>

                    {totalPages > 1 && (
                        <div style={{
                            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                            padding: '12px 16px', borderTop: '1px solid var(--color-border)',
                        }}>
                            <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                                Показано {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} из {total}
                            </span>
                            <div style={{ display: 'flex', gap: 4 }}>
                                <button
                                    className="btn btn-secondary btn-sm"
                                    disabled={page === 0}
                                    onClick={() => setPage(p => p - 1)}
                                >
                                    ←
                                </button>
                                <button
                                    className="btn btn-secondary btn-sm"
                                    disabled={page >= totalPages - 1}
                                    onClick={() => setPage(p => p + 1)}
                                >
                                    →
                                </button>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
