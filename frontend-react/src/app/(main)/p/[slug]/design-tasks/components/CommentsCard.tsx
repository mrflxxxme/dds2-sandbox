'use client';

import { useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatDateTime, formatNumber } from '@/lib/utils';
import { downloadBlob } from '@/lib/design';
import type { DesignTaskDetail } from '@/types/api';

/** Зеркало лимита бэка (413 при превышении) — отсекаем заранее, не гоняя 20+ МБ впустую. */
const MAX_FILE_MB = 20;

interface CommentsCardProps {
    task: DesignTaskDetail;
    onChanged: () => void;
    onError: (msg: string) => void;
}

/** Тред комментариев; ввод — только can_comment (viewer read-only). Вложение — необязательное. */
export default function CommentsCard({ task, onChanged, onError }: CommentsCardProps) {
    const [body, setBody] = useState('');
    const [file, setFile] = useState<File | null>(null);
    const [sending, setSending] = useState(false);
    const [formError, setFormError] = useState('');
    const fileInputRef = useRef<HTMLInputElement | null>(null);

    const pickFile = (list: FileList | null) => {
        const picked = list?.[0] ?? null;
        // Сброс input'а — иначе повторный выбор того же файла не даёт onChange.
        if (fileInputRef.current) fileInputRef.current.value = '';
        if (!picked) return;
        if (picked.size > MAX_FILE_MB * 1024 * 1024) {
            setFormError(`Файл «${picked.name}» больше ${MAX_FILE_MB} МБ`);
            return;
        }
        setFormError('');
        setFile(picked);
    };

    const send = async () => {
        const text = body.trim();
        if (!text) return;
        setFormError('');
        setSending(true);
        try {
            // С файлом — multipart-ручка, без файла — прежний JSON-путь (контракт FROZEN).
            if (file) await api.addDesignCommentWithFile(task.id, text, file);
            else await api.addDesignComment(task.id, { body: text });
            setBody('');
            setFile(null);
            onChanged();
        } catch (e) {
            // Текст сервера как есть: 400 — запрещённый тип (svg/html), 413 — больше 20 МБ.
            setFormError(e instanceof Error ? e.message : 'Не удалось отправить комментарий');
        } finally {
            setSending(false);
        }
    };

    const download = async (commentId: number, filename: string | null) => {
        try {
            const blob = await api.getDesignCommentFile(task.id, commentId);
            downloadBlob(blob, filename || `comment-${commentId}`);
        } catch (e) {
            onError(e instanceof Error ? e.message : 'Не удалось скачать вложение');
        }
    };

    return (
        <div className="glass-card">
            <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 12px' }}>Комментарии</h3>
            {task.comments.length === 0 && (
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 8 }}>Комментариев нет.</div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: task.permissions.can_comment ? 12 : 0 }}>
                {task.comments.map((c) => (
                    <div key={c.id} style={{ border: '1px solid var(--color-border)', borderRadius: 8, padding: '8px 10px' }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>
                            {c.author_name ?? `#${c.author_user_id}`} · {formatDateTime(c.created_at)}
                        </div>
                        <div style={{ fontSize: 13, whiteSpace: 'pre-wrap' }}>{c.body}</div>
                        {/* Кнопка скачивания — только у комментариев с вложением. */}
                        {c.original_filename && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6 }}>
                                <span style={{ fontSize: 13 }}>📎</span>
                                <button
                                    className="btn btn-sm btn-secondary"
                                    style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}
                                    title="Скачать вложение"
                                    onClick={() => void download(c.id, c.original_filename)}
                                >
                                    {c.original_filename}
                                </button>
                            </div>
                        )}
                    </div>
                ))}
            </div>
            {task.permissions.can_comment && (
                <div>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
                        <textarea
                            className="form-input"
                            style={{ flex: 1, minHeight: 60, resize: 'vertical' }}
                            placeholder="Написать комментарий…"
                            value={body}
                            maxLength={2000}
                            disabled={sending}
                            onChange={(e) => setBody(e.target.value)}
                        />
                        <input
                            ref={fileInputRef}
                            type="file"
                            accept="image/*,.pdf,.zip,.rar"
                            style={{ display: 'none' }}
                            onChange={(e) => pickFile(e.target.files)}
                        />
                        <button
                            className="btn btn-sm btn-secondary"
                            title={`Прикрепить файл (не больше ${MAX_FILE_MB} МБ)`}
                            disabled={sending}
                            onClick={() => fileInputRef.current?.click()}
                        >
                            📎
                        </button>
                        <button className="btn btn-primary btn-sm" disabled={sending || !body.trim()} onClick={() => void send()}>
                            {sending ? '⏳ Отправка…' : 'Отправить'}
                        </button>
                    </div>

                    {file && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, marginTop: 8, border: '1px solid var(--color-border)', borderRadius: 8, padding: '6px 10px' }}>
                            <span>📎</span>
                            <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{file.name}</span>
                            <span style={{ color: 'var(--color-text-dim)', whiteSpace: 'nowrap' }}>{formatNumber(file.size / 1024, 0)} КБ</span>
                            <button
                                className="btn btn-sm btn-secondary"
                                style={{ marginLeft: 'auto' }}
                                title="Убрать файл"
                                disabled={sending}
                                onClick={() => setFile(null)}
                            >
                                ✕
                            </button>
                        </div>
                    )}
                    {/* Бэк требует body 1..2000 и с файлом тоже — подсказываем, почему кнопка неактивна. */}
                    {file && !body.trim() && (
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 6 }}>
                            К вложению нужен текст комментария.
                        </div>
                    )}
                    {formError && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 8 }}>{formError}</div>}
                </div>
            )}
        </div>
    );
}
