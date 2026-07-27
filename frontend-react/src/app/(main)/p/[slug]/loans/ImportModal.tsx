'use client';

import { useRef, useState } from 'react';
import { api } from '@/lib/api';
import type { LoanImportResult } from '@/types/api';

export default function ImportModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
    const [file, setFile] = useState<File | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [result, setResult] = useState<LoanImportResult | null>(null);
    const inputRef = useRef<HTMLInputElement>(null);

    const submit = async () => {
        if (!file) return;
        setBusy(true);
        setError('');
        try {
            const res = await api.importLoans(file);
            setResult(res);
            onDone();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка импорта');
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card modal-card-solid" style={{ maxHeight: '88vh', overflow: 'auto' }} onClick={(e) => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                    <h3 style={{ margin: 0, fontSize: 17, fontWeight: 700 }}>Импорт реестра займов</h3>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>✕</button>
                </div>

                {!result && (
                    <>
                        <p style={{ color: 'var(--color-text-dim)', fontSize: 13, marginTop: 0 }}>
                            Файл Excel (.xlsx) с листом «Расчет процентов»: колонки Дата, Тип (Приход/Возврат),
                            Сумма, Контрагент, Номер договора, Ставка, От, До, Статус. Повторный импорт обновит
                            существующие займы, а не создаст дубли.
                        </p>
                        <div
                            onClick={() => inputRef.current?.click()}
                            style={{
                                border: '2px dashed var(--color-border)', borderRadius: 16, padding: 28,
                                textAlign: 'center', cursor: 'pointer', marginBottom: 12,
                            }}
                        >
                            <div style={{ fontSize: 32, marginBottom: 8 }}>📄</div>
                            <div style={{ fontWeight: 600 }}>{file ? file.name : 'Выберите .xlsx файл'}</div>
                            <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>нажмите, чтобы выбрать</div>
                            <input
                                ref={inputRef}
                                type="file"
                                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                                style={{ display: 'none' }}
                                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                            />
                        </div>
                        {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 12 }}>{error}</div>}
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                            <button className="btn btn-secondary btn-sm" onClick={onClose}>Отмена</button>
                            <button className="btn btn-primary btn-sm" disabled={!file || busy} onClick={submit}>
                                {busy ? 'Импорт…' : 'Импортировать'}
                            </button>
                        </div>
                    </>
                )}

                {result && (
                    <>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, margin: '8px 0 12px' }}>
                            <Stat label="Создано займов" value={result.created_loans} />
                            <Stat label="Обновлено" value={result.updated_loans} />
                            <Stat label="Новых контрагентов" value={result.created_counterparties} />
                            <Stat label="Платежей (возвратов)" value={result.created_payments} />
                            <Stat label="Заёмщиков" value={result.lenders.length} />
                            <Stat label="Пропущено строк" value={result.skipped_rows} />
                        </div>
                        {result.warnings.length > 0 && (
                            <details style={{ marginBottom: 12 }}>
                                <summary style={{ cursor: 'pointer', color: 'var(--color-warning)', fontSize: 13 }}>
                                    Предупреждения ({result.warnings.length})
                                </summary>
                                <ul style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 8, maxHeight: 160, overflow: 'auto' }}>
                                    {result.warnings.map((w, i) => <li key={i}>{w}</li>)}
                                </ul>
                            </details>
                        )}
                        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                            <button className="btn btn-primary btn-sm" onClick={onClose}>Готово</button>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}

function Stat({ label, value }: { label: string; value: number }) {
    return (
        <div style={{ background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 12, padding: '10px 12px' }}>
            <div style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>{label}</div>
            <div style={{ fontSize: 20, fontWeight: 700 }}>{value}</div>
        </div>
    );
}
