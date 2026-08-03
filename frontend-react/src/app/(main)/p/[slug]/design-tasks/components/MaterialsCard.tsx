'use client';

import { useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import WbThumb from '@/components/WbThumb';
import { downloadBlob, isValidHttpUrl } from '@/lib/design';
import type { DesignTaskDetail } from '@/types/api';
import ProductSuggest from './ProductSuggest';

interface MaterialsCardProps {
    task: DesignTaskDetail;
    onChanged: () => void;
    onError: (msg: string) => void;
}

/** «Исходные материалы» (Р12): плитка файл/ссылка/артикул + добавление и скачивание. */
export default function MaterialsCard({ task, onChanged, onError }: MaterialsCardProps) {
    const [adding, setAdding] = useState<'LINK' | 'NM' | null>(null);
    const [linkUrl, setLinkUrl] = useState('');
    const [linkCaption, setLinkCaption] = useState('');
    const [nmQuery, setNmQuery] = useState('');
    const [busy, setBusy] = useState(false);
    const fileInputRef = useRef<HTMLInputElement | null>(null);

    // Добавление материалов — по can_edit; удаление проверяет бэк (автор материала | автор | lead).
    const canAdd = task.permissions.can_edit;
    const canTryDelete = task.permissions.can_comment; // не-viewer; точное право решает бэк (403 → toast)

    const run = async (fn: () => Promise<unknown>): Promise<boolean> => {
        setBusy(true);
        try {
            await fn();
            onChanged();
            return true;
        } catch (e) {
            onError(e instanceof Error ? e.message : 'Ошибка');
            return false;
        } finally {
            setBusy(false);
        }
    };

    const download = async (matId: number, filename: string | null) => {
        try {
            const blob = await api.getDesignMaterialFileBlob(task.id, matId);
            downloadBlob(blob, filename || `material-${matId}`);
        } catch (e) {
            onError(e instanceof Error ? e.message : 'Не удалось скачать файл');
        }
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12 }}>
                <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Исходные материалы</h3>
                {canAdd && (
                    <div style={{ display: 'flex', gap: 4, marginLeft: 'auto' }}>
                        <input
                            ref={fileInputRef}
                            type="file"
                            accept="image/*,.pdf,.zip,.rar"
                            style={{ display: 'none' }}
                            onChange={(e) => {
                                const f = e.target.files?.[0];
                                if (f) void run(() => api.uploadDesignMaterialFile(task.id, f));
                                if (fileInputRef.current) fileInputRef.current.value = '';
                            }}
                        />
                        <button className="btn btn-sm btn-secondary" disabled={busy} onClick={() => fileInputRef.current?.click()}>+ Файл</button>
                        <button className="btn btn-sm btn-secondary" disabled={busy} onClick={() => setAdding(adding === 'LINK' ? null : 'LINK')}>+ Ссылка</button>
                        <button className="btn btn-sm btn-secondary" disabled={busy} onClick={() => setAdding(adding === 'NM' ? null : 'NM')}>+ Артикул</button>
                    </div>
                )}
            </div>

            {adding === 'LINK' && (
                <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
                    <input className="form-input" style={{ flex: 2, minWidth: 180 }} placeholder="https://…" value={linkUrl} onChange={(e) => setLinkUrl(e.target.value)} />
                    <input className="form-input" style={{ flex: 1, minWidth: 120 }} placeholder="Подпись" value={linkCaption} onChange={(e) => setLinkCaption(e.target.value)} />
                    <button
                        className="btn btn-sm btn-primary"
                        disabled={busy}
                        onClick={async () => {
                            if (!isValidHttpUrl(linkUrl.trim())) { onError('Нужен корректный http(s)-URL'); return; }
                            const ok = await run(() => api.addDesignMaterial(task.id, { kind: 'LINK', url: linkUrl.trim(), caption: linkCaption.trim() || null }));
                            if (ok) { setLinkUrl(''); setLinkCaption(''); setAdding(null); }
                        }}
                    >
                        Добавить
                    </button>
                </div>
            )}
            {adding === 'NM' && (
                <div style={{ marginBottom: 12 }}>
                    <ProductSuggest
                        value={nmQuery}
                        onChange={setNmQuery}
                        placeholder="Поиск товара-примера"
                        onPick={async (s) => {
                            const ok = await run(() => api.addDesignMaterial(task.id, {
                                kind: 'NM', ref_nm_id: s.nm_id,
                                caption: [s.brand, s.subject].filter(Boolean).join(' · ') || null,
                            }));
                            if (ok) { setNmQuery(''); setAdding(null); }
                        }}
                    />
                </div>
            )}

            {task.materials.length === 0 && (
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Материалов нет.</div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {task.materials.map((m) => (
                    <div key={m.id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px' }}>
                        {m.kind === 'FILE' && (
                            <>
                                <span>📎</span>
                                <button
                                    className="btn btn-sm btn-secondary"
                                    style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}
                                    title="Скачать"
                                    onClick={() => void download(m.id, m.original_filename)}
                                >
                                    {m.original_filename || `Файл #${m.id}`}
                                </button>
                                {m.file_size != null && (
                                    <span style={{ color: 'var(--color-text-dim)', whiteSpace: 'nowrap' }}>
                                        {formatNumber(m.file_size / 1024, 0)} КБ
                                    </span>
                                )}
                            </>
                        )}
                        {m.kind === 'LINK' && (
                            <>
                                <span>🔗</span>
                                <a href={m.url ?? '#'} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--color-accent)', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {m.caption || m.url}
                                </a>
                            </>
                        )}
                        {m.kind === 'NM' && (
                            <>
                                <WbThumb nmId={m.ref_nm_id} size={24} height={31} />
                                <span>Артикул {m.ref_nm_id}{m.caption ? ` · ${m.caption}` : ''}</span>
                            </>
                        )}
                        {canTryDelete && (
                            <button
                                className="btn btn-sm btn-secondary"
                                style={{ marginLeft: 'auto' }}
                                title="Удалить материал"
                                disabled={busy}
                                onClick={() => void run(() => api.deleteDesignMaterial(task.id, m.id))}
                            >
                                🗑
                            </button>
                        )}
                    </div>
                ))}
            </div>
        </div>
    );
}
