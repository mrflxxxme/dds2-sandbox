'use client';
import { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { exportToExcel, formatNumber } from '@/lib/utils';
import type { WbTariff } from '@/types/api';

export function WbTariffs() {
    const [tariffs, setTariffs] = useState<WbTariff[]>([]);
    const [loading, setLoading] = useState(true);
    const [msg, setMsg] = useState('');
    const [uploading, setUploading] = useState(false);

    const loadTariffs = useCallback(async () => {
        setLoading(true);
        try {
            const data = await api.getTariffs();
            setTariffs(data);
        } catch (e: any) {
            setMsg(`❌ ${e.message}`);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { loadTariffs(); }, [loadTariffs]);

    const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;
        setUploading(true);
        setMsg('');
        try {
            const result = await api.uploadTariffs(file);
            setMsg(`✅ Загружено ${result.inserted} тарифов (заменено ${result.replaced})`);
            await loadTariffs();
        } catch (err: any) {
            setMsg(`❌ ${err.message}`);
        } finally {
            setUploading(false);
            e.target.value = '';
        }
    };

    const handleDelete = async (id: number) => {
        if (!confirm('Удалить тариф?')) return;
        try {
            await api.deleteTariff(id);
            await loadTariffs();
        } catch (e: any) {
            setMsg(`❌ ${e.message}`);
        }
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div>
                    <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Тарифы WB (комиссия по предметам)</h3>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginTop: 2 }}>
                        Загружено: {tariffs.length} предметов
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm"
                        onClick={() => exportToExcel(tariffs.map(t => ({ subject_name: t.subject_name, commission_rate: t.commission_rate })), 'wb_tariffs')}
                        disabled={tariffs.length === 0}>
                        📥 Excel
                    </button>
                    <label className={`btn btn-primary btn-sm ${uploading ? 'opacity-50' : ''}`}
                        style={{ cursor: uploading ? 'wait' : 'pointer' }}>
                        {uploading ? '⏳ Загрузка...' : '📤 Загрузить .xlsx'}
                        <input type="file" accept=".xlsx,.xls" onChange={handleUpload}
                            disabled={uploading} style={{ display: 'none' }} />
                    </label>
                </div>
            </div>

            {msg && (
                <div style={{
                    fontSize: 13, marginBottom: 8, padding: '6px 12px', borderRadius: 6,
                    background: msg.startsWith('✅') ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)',
                    color: msg.startsWith('✅') ? 'var(--color-success)' : 'var(--color-danger)',
                }}>
                    {msg} <span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setMsg('')}>✕</span>
                </div>
            )}

            <div style={{
                fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 12,
                padding: '8px 12px', borderRadius: 6, background: 'var(--color-bg-input)',
                border: '1px solid var(--color-border)',
            }}>
                📋 Загрузите xlsx-файл с тарифами WB. Ожидаемые колонки: <b>Предмет</b> и <b>Склад WB %</b>.
                При загрузке нового файла все старые тарифы заменяются.
            </div>

            {loading ? (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-dim)' }}>⏳ Загрузка...</div>
            ) : tariffs.length === 0 ? (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--color-text-dim)' }}>
                    Нет загруженных тарифов. Загрузите xlsx-файл.
                </div>
            ) : (
                <div style={{ maxHeight: 500, overflowY: 'auto' }}>
                    <table className="data-table" style={{ width: '100%', fontSize: 13 }}>
                        <thead>
                            <tr>
                                <th style={{ textAlign: 'left' }}>Предмет</th>
                                <th style={{ textAlign: 'right' }}>Комиссия %</th>
                                <th style={{ width: 50 }}></th>
                            </tr>
                        </thead>
                        <tbody>
                            {tariffs.map(t => (
                                <tr key={t.id}>
                                    <td>{t.subject_name}</td>
                                    <td style={{ textAlign: 'right' }}>{formatNumber(t.commission_rate, 2)}%</td>
                                    <td style={{ textAlign: 'center' }}>
                                        <button className="btn btn-danger btn-sm" style={{ fontSize: 11, padding: '2px 6px' }}
                                            onClick={() => handleDelete(t.id)}>✕</button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
