'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import KpiCard from '@/components/KpiCard';
import type { QuestionItem, QuestionsListResponse } from '@/types/api';

type SubTab = 'unanswered' | 'answered';

/** Инлайн-форма ручного ответа на вопрос (создаёт черновик). */
function ReplyForm({ question, onDone, onCancel }: {
    question: QuestionItem;
    onDone: () => void;
    onCancel: () => void;
}) {
    const [text, setText] = useState('');
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState('');

    const submit = async () => {
        setBusy(true);
        setErr('');
        try {
            await api.createReply({ target_type: 'question', target_wb_id: question.id, text: text.trim() });
            onDone();
        } catch (e) {
            setErr(e instanceof Error ? e.message : 'Не удалось сохранить черновик');
        } finally {
            setBusy(false);
        }
    };

    return (
        <div style={{ marginTop: 12 }}>
            <textarea
                className="form-input"
                value={text}
                onChange={e => setText(e.target.value)}
                rows={4}
                maxLength={5000}
                placeholder="Текст ответа покупателю…"
                style={{ width: '100%', resize: 'vertical', fontSize: 13, lineHeight: 1.5 }}
            />
            {err && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginTop: 6 }}>{err}</div>}
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <button className="btn btn-primary btn-sm" onClick={submit} disabled={busy || !text.trim()}>
                    {busy ? 'Сохранение…' : 'Сохранить черновик'}
                </button>
                <button className="btn btn-secondary btn-sm" onClick={onCancel}>Отмена</button>
            </div>
        </div>
    );
}

function QuestionCard({ question, onDraftCreated }: {
    question: QuestionItem;
    onDraftCreated: () => void;
}) {
    const [replyOpen, setReplyOpen] = useState(false);
    const [draftSaved, setDraftSaved] = useState(false);

    return (
        <div className="glass-card" style={{ padding: 20, marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
                <div style={{ minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        {question.user_name && (
                            <span style={{ fontWeight: 600, fontSize: 14 }}>{question.user_name}</span>
                        )}
                        {question.is_answered ? (
                            <span className="badge badge-success">Отвечен</span>
                        ) : (
                            <span className="badge badge-warning">Без ответа</span>
                        )}
                    </div>
                    <div style={{ color: 'var(--color-text-dim)', fontSize: 13, marginTop: 4 }}>
                        {[question.subject, question.product_name].filter(Boolean).join(' · ') || '—'}
                        {question.article ? ` · ${question.article}` : ''}
                        {question.nm_id ? ` · nmID ${question.nm_id}` : ''}
                    </div>
                </div>
                <div style={{ color: 'var(--color-text-dim)', fontSize: 13, whiteSpace: 'nowrap' }}>
                    {question.created_date ? formatDate(question.created_date) : ''}
                </div>
            </div>

            {question.text && (
                <p style={{ marginTop: 12, marginBottom: 0, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>
                    {question.text}
                </p>
            )}

            {question.answer_text && (
                <div style={{ marginTop: 12, padding: 12, background: 'var(--color-bg-card)', borderRadius: 8, fontSize: 14 }}>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginBottom: 4 }}>Ваш ответ:</div>
                    <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>{question.answer_text}</div>
                </div>
            )}

            {!question.is_answered && !draftSaved && (
                replyOpen ? (
                    <ReplyForm
                        question={question}
                        onCancel={() => setReplyOpen(false)}
                        onDone={() => { setReplyOpen(false); setDraftSaved(true); onDraftCreated(); }}
                    />
                ) : (
                    <div style={{ marginTop: 12 }}>
                        <button className="btn btn-primary btn-sm" onClick={() => setReplyOpen(true)}>
                            ✏️ Ответить
                        </button>
                    </div>
                )
            )}

            {draftSaved && (
                <div style={{ marginTop: 12, fontSize: 13, color: 'var(--color-success)' }}>
                    ✓ Черновик сохранён — одобрите и отправьте его во вкладке «🤖 Автоответы».
                </div>
            )}
        </div>
    );
}

const PAGE = 100; // вопросов за подгрузку

export default function QuestionsTab() {
    const [meta, setMeta] = useState<QuestionsListResponse | null>(null);
    const [items, setItems] = useState<QuestionItem[]>([]);
    const [sub, setSub] = useState<SubTab>('unanswered');
    const [loading, setLoading] = useState(true);
    const [loadingMore, setLoadingMore] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [syncMsg, setSyncMsg] = useState('');
    const [error, setError] = useState('');

    // Загрузка первой страницы среза (сброс накопленного списка).
    const load = useCallback(async (currentTab: SubTab) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.getQuestions({ isAnswered: currentTab === 'answered', take: PAGE, skip: 0 });
            setMeta(res);
            setItems(res.items);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить вопросы');
            setMeta(null);
            setItems([]);
        } finally {
            setLoading(false);
        }
    }, []);

    // Догрузка следующей страницы (append).
    const loadMore = useCallback(async () => {
        setLoadingMore(true);
        setError('');
        try {
            const res = await api.getQuestions({ isAnswered: sub === 'answered', take: PAGE, skip: items.length });
            setMeta(res);
            setItems(prev => [...prev, ...res.items]);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить ещё');
        } finally {
            setLoadingMore(false);
        }
    }, [sub, items.length]);

    useEffect(() => {
        load(sub);
    }, [sub, load]);

    const sync = useCallback(async () => {
        setSyncing(true);
        setError('');
        setSyncMsg('');
        try {
            const res = await api.syncQuestions();
            if (res.has_key) {
                setSyncMsg(`✓ Синхронизировано: получено ${formatNumber(res.rows_fetched, 0)}, сохранено ${formatNumber(res.rows_upserted, 0)}`);
            }
            await load(sub);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось синхронизировать вопросы');
        } finally {
            setSyncing(false);
        }
    }, [sub, load]);

    const total = sub === 'answered' ? (meta?.count_archive ?? 0) : (meta?.count_unanswered ?? 0);
    const hasMore = items.length < total;
    const busy = loading || syncing;

    return (
        <div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 20, alignItems: 'center', flexWrap: 'wrap' }}>
                <button
                    className={`btn btn-sm ${sub === 'unanswered' ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setSub('unanswered')}
                >
                    Без ответа {meta ? `(${formatNumber(meta.count_unanswered, 0)})` : ''}
                </button>
                <button
                    className={`btn btn-sm ${sub === 'answered' ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => setSub('answered')}
                >
                    Отвеченные {meta ? `(${formatNumber(meta.count_archive, 0)})` : ''}
                </button>
                <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={sync} disabled={busy}>
                    {syncing ? 'Синхронизация…' : '↻ Синхронизировать'}
                </button>
            </div>

            {syncMsg && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 20, color: 'var(--color-success)', display: 'flex', alignItems: 'center', gap: 8 }}>
                    {syncMsg}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => setSyncMsg('')}>✕</button>
                </div>
            )}

            {meta && meta.has_key && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 16, marginBottom: 20 }}>
                    <KpiCard label="Без ответа" value={formatNumber(meta.count_unanswered, 0)} />
                    <KpiCard label="Отвеченных всего" value={formatNumber(meta.count_archive, 0)} />
                    <KpiCard label="Показано" value={`${formatNumber(items.length, 0)} из ${formatNumber(total, 0)}`} />
                </div>
            )}

            {error && (
                <div className="glass-card" style={{ marginBottom: 20, color: 'var(--color-danger)' }}>
                    {error}{' '}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={() => load(sub)}>
                        Повторить
                    </button>
                </div>
            )}

            {!loading && !error && meta && !meta.has_key && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>🔑</div>
                    <h3 style={{ margin: '0 0 8px' }}>WB-ключ не настроен</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>
                        Чтобы видеть вопросы покупателей, добавьте API-ключ Wildberries со scope
                        «Вопросы и отзывы» в разделе «Настройка проекта» → Интеграции.
                    </p>
                </div>
            )}

            {loading && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48, color: 'var(--color-text-dim)' }}>
                    Загрузка вопросов…
                </div>
            )}

            {!loading && !error && meta && meta.has_key && items.length === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>❓</div>
                    <h3 style={{ margin: '0 0 8px' }}>Вопросов нет</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>
                        {sub === 'unanswered'
                            ? 'Без ответа ничего нет. Нажмите «Синхронизировать», если ждёте новые.'
                            : 'Отвеченных вопросов пока нет.'}
                    </p>
                </div>
            )}

            {!loading && !error && meta && meta.has_key && items.length > 0 && (
                <div>
                    {items.map((q) => (
                        <QuestionCard key={q.id} question={q} onDraftCreated={() => load(sub)} />
                    ))}
                    {hasMore && (
                        <div style={{ textAlign: 'center', marginTop: 8 }}>
                            <button className="btn btn-secondary" onClick={loadMore} disabled={loadingMore || busy}>
                                {loadingMore ? 'Загрузка…' : `Показать ещё (${formatNumber(total - items.length, 0)})`}
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
