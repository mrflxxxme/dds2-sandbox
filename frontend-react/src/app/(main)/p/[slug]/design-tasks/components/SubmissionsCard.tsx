'use client';

import { api } from '@/lib/api';
import { formatDateTime, formatNumber } from '@/lib/utils';
import { DESIGN_VERDICT_LABEL, downloadBlob } from '@/lib/design';
import type { DesignTaskDetail, DesignVerdict } from '@/types/api';

const VERDICT_BADGE: Record<DesignVerdict, string> = {
    PENDING: 'badge-warning',
    ACCEPTED: 'badge-success',
    REJECTED: 'badge-danger',
};

interface SubmissionsCardProps {
    task: DesignTaskDetail;
    /** user_id → отображаемое имя (для автора версии/вердикта). */
    userName: (id: number | null) => string;
    onError: (msg: string) => void;
}

/** История версий сдач: №, автор, дата, комментарий, файлы, вердикт с причиной. */
export default function SubmissionsCard({ task, userName, onError }: SubmissionsCardProps) {
    const submissions = [...task.submissions].sort((a, b) => b.version_no - a.version_no);

    const download = async (subId: number, fileId: number, filename: string | null) => {
        try {
            const blob = await api.getDesignSubmissionFileBlob(task.id, subId, fileId);
            downloadBlob(blob, filename || `file-${fileId}`);
        } catch (e) {
            onError(e instanceof Error ? e.message : 'Не удалось скачать файл');
        }
    };

    return (
        <div className="glass-card">
            <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 12px' }}>
                Версии сдач {submissions.length > 0 && <span style={{ color: 'var(--color-muted)' }}>({formatNumber(submissions.length, 0)})</span>}
            </h3>
            {submissions.length === 0 && (
                <div style={{ fontSize: 13, color: 'var(--color-muted)' }}>Сдач ещё не было.</div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {submissions.map((s) => (
                    <div key={s.id} style={{ border: '1px solid var(--color-border)', borderRadius: 12, padding: 12 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            <span style={{ fontWeight: 600, fontSize: 14 }}>Версия {s.version_no}</span>
                            <span className={`badge ${VERDICT_BADGE[s.verdict]}`}>{DESIGN_VERDICT_LABEL[s.verdict]}</span>
                            <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--color-muted)' }}>
                                {userName(s.submitted_by_user_id)} · {formatDateTime(s.submitted_at)}
                            </span>
                        </div>
                        {s.comment && (
                            <div style={{ fontSize: 13, marginTop: 6, whiteSpace: 'pre-wrap' }}>{s.comment}</div>
                        )}
                        {s.files.length > 0 && (
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
                                {s.files.map((f) => (
                                    <button
                                        key={f.id}
                                        className="btn btn-sm btn-secondary"
                                        title={f.file_size != null ? `${formatNumber(f.file_size / 1024, 0)} КБ` : undefined}
                                        onClick={() => void download(s.id, f.id, f.original_filename)}
                                    >
                                        📎 {f.original_filename || `Файл #${f.id}`}
                                    </button>
                                ))}
                            </div>
                        )}
                        {s.verdict !== 'PENDING' && (
                            <div style={{ marginTop: 8, fontSize: 13, color: s.verdict === 'REJECTED' ? 'var(--color-danger)' : 'var(--color-success)' }}>
                                {DESIGN_VERDICT_LABEL[s.verdict]}
                                {s.verdict_by_user_id != null && ` · ${userName(s.verdict_by_user_id)}`}
                                {s.verdict_at && ` · ${formatDateTime(s.verdict_at)}`}
                                {s.verdict_comment && (
                                    <div style={{ whiteSpace: 'pre-wrap', color: 'var(--color-text)', marginTop: 4 }}>
                                        Причина: {s.verdict_comment}
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                ))}
            </div>
        </div>
    );
}
