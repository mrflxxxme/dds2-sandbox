'use client';

import { useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type { DesignTaskDetail } from '@/types/api';
import ModalShell from './ModalShell';

const MAX_FILES = 10;
const MAX_FILE_MB = 20;
const MAX_TOTAL_MB = 100;

interface SubmitWorkModalProps {
    taskId: number;
    onDone: (task: DesignTaskDetail) => void;
    onClose: () => void;
}

/** Модалка «Сдать работу»: файлы (1..10) + комментарий «что сделал». */
export default function SubmitWorkModal({ taskId, onDone, onClose }: SubmitWorkModalProps) {
    const [files, setFiles] = useState<File[]>([]);
    const [comment, setComment] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState('');
    const fileInputRef = useRef<HTMLInputElement | null>(null);

    const addFiles = (list: FileList | null) => {
        if (!list) return;
        setError('');
        const next = [...files];
        for (const f of Array.from(list)) {
            if (f.size > MAX_FILE_MB * 1024 * 1024) {
                setError(`Файл «${f.name}» больше ${MAX_FILE_MB} МБ`);
                continue;
            }
            next.push(f);
        }
        if (next.length > MAX_FILES) {
            setError(`Не больше ${MAX_FILES} файлов в одной версии`);
            setFiles(next.slice(0, MAX_FILES));
        } else {
            setFiles(next);
        }
        if (fileInputRef.current) fileInputRef.current.value = '';
    };

    const totalMb = files.reduce((s, f) => s + f.size, 0) / 1024 / 1024;

    const submit = async () => {
        if (files.length === 0) {
            setError('Прикрепите хотя бы один файл');
            return;
        }
        if (totalMb > MAX_TOTAL_MB) {
            setError(`Суммарный размер больше ${MAX_TOTAL_MB} МБ`);
            return;
        }
        setError('');
        setSubmitting(true);
        try {
            const task = await api.submitDesignVersion(taskId, files, comment.trim() || undefined);
            onDone(task);
        } catch (e) {
            const status = (e as { status?: number })?.status;
            // 503 = обрыв заливки в MinIO: версия уже создана и переиспользуется — повтор безопасен.
            if (status === 503 || status === 0) {
                setError('Соединение оборвалось: версия создана, но файлы не загрузились. Нажмите «Сдать» ещё раз — версия переиспользуется.');
            } else {
                setError(e instanceof Error ? e.message : 'Не удалось сдать работу');
            }
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <ModalShell title="Сдать работу" onClose={onClose}>
            <input
                ref={fileInputRef}
                type="file"
                multiple
                accept="image/*,.pdf,.zip,.rar"
                style={{ display: 'none' }}
                onChange={(e) => addFiles(e.target.files)}
            />
            <button className="btn btn-secondary btn-sm" onClick={() => fileInputRef.current?.click()} disabled={submitting}>
                📎 Добавить файлы (до {MAX_FILES}, каждый ≤ {MAX_FILE_MB} МБ, всего ≤ {MAX_TOTAL_MB} МБ)
            </button>

            {files.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 10 }}>
                    {files.map((f, i) => (
                        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px' }}>
                            <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.name}</span>
                            <span style={{ color: 'var(--color-dim)', whiteSpace: 'nowrap' }}>{formatNumber(f.size / 1024 / 1024, 1)} МБ</span>
                            <button className="btn btn-sm btn-secondary" style={{ marginLeft: 'auto' }} disabled={submitting} onClick={() => setFiles((list) => list.filter((_, j) => j !== i))}>✕</button>
                        </div>
                    ))}
                    <div style={{ fontSize: 12, color: totalMb > MAX_TOTAL_MB ? 'var(--color-danger)' : 'var(--color-muted)' }}>
                        Всего: {formatNumber(totalMb, 1)} МБ из {MAX_TOTAL_MB}
                    </div>
                </div>
            )}

            <label style={{ display: 'block', fontSize: 13, color: 'var(--color-muted)', margin: '12px 0 6px' }}>Что сделано</label>
            <textarea
                className="form-input"
                style={{ width: '100%', minHeight: 70, resize: 'vertical' }}
                value={comment}
                maxLength={2000}
                placeholder="Кратко: что сделал в этой версии"
                onChange={(e) => setComment(e.target.value)}
            />

            {error && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 8 }}>{error}</div>}

            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                <button className="btn btn-secondary" onClick={onClose} disabled={submitting}>Отмена</button>
                <button className="btn btn-primary" onClick={() => void submit()} disabled={submitting || files.length === 0}>
                    {submitting ? 'Отправка…' : '📤 Сдать на проверку'}
                </button>
            </div>
        </ModalShell>
    );
}
