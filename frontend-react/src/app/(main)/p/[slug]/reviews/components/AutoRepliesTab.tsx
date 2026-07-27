'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import type { LlmProvider, RepliesListResponse, Reply, ReplyAction, ReplyAgent, ReplyAgentRunResult, ReplyAgentSave, ReplyAgentTarget, ReplyStatus } from '@/types/api';

type SubTab = 'queue' | 'agents';

const STATUS_LABEL: Record<ReplyStatus, string> = {
    draft: 'Черновик',
    approved: 'Одобрен',
    sent: 'Отправлен',
    error: 'Ошибка',
    rejected: 'Отклонён',
};
const STATUS_BADGE: Record<ReplyStatus, string> = {
    draft: 'badge-warning',
    approved: 'badge-info',
    sent: 'badge-success',
    error: 'badge-danger',
    rejected: 'badge-secondary',
};

const TARGET_LABEL: Record<ReplyAgentTarget, string> = {
    feedback: 'Отзывы',
    question: 'Вопросы',
    both: 'Отзывы и вопросы',
};

function Stars({ rating }: { rating: number }) {
    const r = Math.max(0, Math.min(5, rating));
    return (
        <span style={{ color: 'var(--color-warning)', letterSpacing: 1 }} title={`${r} / 5`}>
            {'★'.repeat(r)}
            <span style={{ color: 'var(--color-border)' }}>{'★'.repeat(5 - r)}</span>
        </span>
    );
}

// ─── Очередь ответов ─────────────────────────────────────────────────────────

function ReplyCard({ reply, onChanged }: { reply: Reply; onChanged: () => void }) {
    const [text, setText] = useState(reply.text);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState('');

    const dirty = text !== reply.text;
    // needs_info-черновик: одобрение заблокировано, пока текст не отредактирован вручную
    const needsInfoBlocked = reply.needs_info && !dirty;

    const act = async (action?: ReplyAction, saveText?: boolean) => {
        setBusy(true);
        setErr('');
        try {
            await api.updateReply(reply.id, {
                ...(saveText ? { text } : {}),
                ...(action ? { action } : {}),
            });
            onChanged();
        } catch (e) {
            setErr(e instanceof Error ? e.message : 'Не удалось обновить ответ');
        } finally {
            setBusy(false);
        }
    };

    const t = reply.target;

    return (
        <div className="glass-card" style={{ padding: 16, marginBottom: 12 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 13 }}>
                {reply.target_type === 'feedback' ? (
                    <span className="badge badge-secondary">Отзыв {t?.rating != null ? `· ${t.rating}★` : ''}</span>
                ) : (
                    <span className="badge badge-secondary">Вопрос</span>
                )}
                {reply.target_type === 'feedback' && t?.rating != null && <Stars rating={t.rating} />}
                {t?.user_name && <span style={{ fontWeight: 600 }}>{t.user_name}</span>}
                <span style={{ color: 'var(--color-text-dim)' }}>
                    {[t?.brand, t?.product_name || t?.subject].filter(Boolean).join(' · ') || (t?.nm_id ? `nmID ${t.nm_id}` : '—')}
                </span>
                <span className={`badge ${STATUS_BADGE[reply.status]}`}>{STATUS_LABEL[reply.status]}</span>
                {reply.generation === 'kb_direct' && <span className="badge badge-info">📚 из базы знаний</span>}
                {reply.generation === 'llm' && <span className="badge badge-info">🤖 ИИ</span>}
                {reply.needs_info && <span className="badge badge-danger">⚠️ Нет данных в базе знаний</span>}
                {reply.source === 'agent' && <span style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>🤖 агент</span>}
                <span style={{ marginLeft: 'auto', color: 'var(--color-text-dim)' }}>
                    {reply.status === 'sent' && reply.sent_at ? `Отправлен ${formatDate(reply.sent_at)}` : (t?.created_date ? formatDate(t.created_date) : '')}
                </span>
            </div>

            {t?.text && (
                <p style={{ margin: '8px 0 0', fontSize: 13, color: 'var(--color-text-dim)', fontStyle: 'italic', whiteSpace: 'pre-wrap' }}>
                    {reply.target_type === 'feedback' ? 'Отзыв' : 'Вопрос'}: {t.text}
                </p>
            )}

            {reply.status === 'draft' ? (
                <textarea
                    className="form-input"
                    value={text}
                    onChange={e => setText(e.target.value)}
                    rows={4}
                    maxLength={5000}
                    style={{ width: '100%', resize: 'vertical', fontSize: 13, lineHeight: 1.5, marginTop: 10 }}
                />
            ) : (
                <p style={{ margin: '10px 0 0', fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>{reply.text}</p>
            )}

            {reply.status === 'error' && reply.error && (
                <div style={{ marginTop: 8, fontSize: 13, color: 'var(--color-danger)' }}>Ошибка отправки: {reply.error}</div>
            )}

            {err && <div style={{ marginTop: 8, fontSize: 13, color: 'var(--color-danger)' }}>{err}</div>}

            {reply.status === 'draft' && needsInfoBlocked && (
                <div style={{ marginTop: 8, fontSize: 13, color: 'var(--color-warning)' }}>
                    ⚠️ В базе знаний нет ответа на этот {reply.target_type === 'feedback' ? 'отзыв' : 'вопрос'}.
                    Добавьте знание во вкладке «📚 База знаний» или отредактируйте текст вручную — тогда одобрение станет доступно.
                </div>
            )}

            <div style={{ display: 'flex', gap: 6, marginTop: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                {reply.status === 'draft' && (
                    <>
                        <button
                            className="btn btn-success btn-sm"
                            disabled={busy || needsInfoBlocked}
                            title={needsInfoBlocked ? 'Сначала добавьте знание во вкладке «База знаний» или отредактируйте текст вручную' : undefined}
                            onClick={() => act('approve', dirty)}
                        >
                            ✓ Одобрить{dirty ? ' (с правкой)' : ''}
                        </button>
                        {dirty && (
                            <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => act(undefined, true)}>
                                💾 Сохранить правку
                            </button>
                        )}
                        <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => act('reject')}>
                            Отклонить
                        </button>
                    </>
                )}
                {reply.status === 'approved' && (
                    <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>⏳ Ожидает отправки — кнопка «Отправить одобренные» выше.</span>
                )}
                {reply.status === 'error' && (
                    <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => act('reopen')}>
                        ↩ Переоткрыть в черновики
                    </button>
                )}
                {reply.status === 'rejected' && (
                    <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => act('reopen')}>
                        ↩ Переоткрыть
                    </button>
                )}
            </div>
        </div>
    );
}

const QUEUE_PAGE = 100;
const STATUS_FILTERS: { key: ReplyStatus | ''; label: string }[] = [
    { key: '', label: 'Все' },
    { key: 'draft', label: 'Черновики' },
    { key: 'approved', label: 'Одобренные' },
    { key: 'sent', label: 'Отправленные' },
    { key: 'error', label: 'Ошибки' },
    { key: 'rejected', label: 'Отклонённые' },
];

function RepliesQueue() {
    const [meta, setMeta] = useState<RepliesListResponse | null>(null);
    const [items, setItems] = useState<Reply[]>([]);
    const [status, setStatus] = useState<ReplyStatus | ''>('');
    const [loading, setLoading] = useState(true);
    const [loadingMore, setLoadingMore] = useState(false);
    const [sending, setSending] = useState(false);
    const [sendMsg, setSendMsg] = useState('');
    const [error, setError] = useState('');

    const load = useCallback(async (currentStatus: ReplyStatus | '') => {
        setLoading(true);
        setError('');
        try {
            const res = await api.getReplies({ status: currentStatus || undefined, take: QUEUE_PAGE, skip: 0 });
            setMeta(res);
            setItems(res.items);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить ответы');
            setMeta(null);
            setItems([]);
        } finally {
            setLoading(false);
        }
    }, []);

    const loadMore = useCallback(async () => {
        setLoadingMore(true);
        try {
            const res = await api.getReplies({ status: status || undefined, take: QUEUE_PAGE, skip: items.length });
            setMeta(res);
            setItems(prev => [...prev, ...res.items]);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить ещё');
        } finally {
            setLoadingMore(false);
        }
    }, [status, items.length]);

    useEffect(() => {
        load(status);
    }, [status, load]);

    const sendApproved = async () => {
        const approved = meta?.counts?.approved ?? 0;
        if (!window.confirm(`Отправить в WB ${formatNumber(approved, 0)} одобренных ответов?`)) return;
        setSending(true);
        setSendMsg('');
        setError('');
        try {
            const res = await api.sendReplies();
            setSendMsg(`✓ Отправка запущена: в очереди ${formatNumber(res.pending, 0)} ответов (WB ограничивает ~1 ответ/сек). Обновим список через несколько секунд…`);
            // Отправка идёт фоном — даём бэкенду время и перечитываем список
            setTimeout(() => { load(status); }, 4000);
            setTimeout(() => { load(status); }, 10000);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось запустить отправку');
        } finally {
            setSending(false);
        }
    };

    const counts = meta?.counts ?? {};
    const total = meta?.total ?? 0;
    const hasMore = items.length < total;
    const approvedCount = counts.approved ?? 0;

    return (
        <div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                {STATUS_FILTERS.map(f => (
                    <button
                        key={f.key}
                        className={`btn btn-sm ${status === f.key ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setStatus(f.key)}
                    >
                        {f.label}
                        {f.key && counts[f.key] != null ? ` (${formatNumber(counts[f.key], 0)})` : ''}
                    </button>
                ))}
                {approvedCount > 0 && (
                    <button className="btn btn-success btn-sm" style={{ marginLeft: 'auto' }} onClick={sendApproved} disabled={sending || loading}>
                        {sending ? 'Запуск…' : `📨 Отправить одобренные (${formatNumber(approvedCount, 0)})`}
                    </button>
                )}
            </div>

            {sendMsg && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 16, color: 'var(--color-success)', display: 'flex', alignItems: 'center', gap: 8 }}>
                    {sendMsg}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => setSendMsg('')}>✕</button>
                </div>
            )}

            {error && (
                <div className="glass-card" style={{ marginBottom: 16, color: 'var(--color-danger)' }}>
                    {error}{' '}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={() => load(status)}>Повторить</button>
                </div>
            )}

            {loading && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48, color: 'var(--color-text-dim)' }}>
                    Загрузка ответов…
                </div>
            )}

            {!loading && !error && items.length === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>🤖</div>
                    <h3 style={{ margin: '0 0 8px' }}>Ответов нет</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>
                        {status
                            ? 'С таким статусом ничего нет.'
                            : 'Запустите агента в настройках или создайте черновик вручную из вкладок «Отзывы» / «Вопросы».'}
                    </p>
                </div>
            )}

            {!loading && !error && items.length > 0 && (
                <div>
                    {items.map(r => (
                        <ReplyCard key={r.id} reply={r} onChanged={() => load(status)} />
                    ))}
                    {hasMore && (
                        <div style={{ textAlign: 'center', marginTop: 8 }}>
                            <button className="btn btn-secondary" onClick={loadMore} disabled={loadingMore}>
                                {loadingMore ? 'Загрузка…' : `Показать ещё (${formatNumber(total - items.length, 0)})`}
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

// ─── Настройки агентов ───────────────────────────────────────────────────────

interface AgentFormState {
    name: string;
    target: ReplyAgentTarget;
    stars: number[];
    nm_ids: string;
    auto_send: boolean;
    rules: string;
    examples: string;
    llm_provider: LlmProvider;
    llm_model: string;
    llm_base_url: string;
    enabled: boolean;
}

const EMPTY_FORM: AgentFormState = {
    name: '',
    target: 'both',
    stars: [1, 2, 3, 4, 5],
    nm_ids: '',
    auto_send: false,
    rules: '',
    examples: '',
    llm_provider: 'openai_compatible',
    llm_model: 'deepseek-chat',
    llm_base_url: '',
    enabled: true,
};

function agentToForm(a: ReplyAgent): AgentFormState {
    return {
        name: a.name,
        target: a.target,
        stars: a.star_levels.split(',').map(s => parseInt(s.trim(), 10)).filter(n => n >= 1 && n <= 5),
        nm_ids: a.nm_ids ?? '',
        auto_send: a.auto_send,
        rules: a.rules,
        examples: a.examples ?? '',
        llm_provider: a.llm_provider,
        llm_model: a.llm_model,
        llm_base_url: a.llm_base_url ?? '',
        enabled: a.enabled,
    };
}

function AgentForm({ initial, title, busy, err, onSave, onCancel }: {
    initial: AgentFormState;
    title: string;
    busy: boolean;
    err: string;
    onSave: (body: ReplyAgentSave) => void;
    onCancel: () => void;
}) {
    const [f, setF] = useState<AgentFormState>(initial);
    const withFeedback = f.target !== 'question';

    const toggleStar = (n: number) => {
        setF(prev => ({
            ...prev,
            stars: prev.stars.includes(n) ? prev.stars.filter(s => s !== n) : [...prev.stars, n].sort(),
        }));
    };

    const submit = () => {
        const body: ReplyAgentSave = {
            name: f.name.trim(),
            target: f.target,
            star_levels: withFeedback ? (f.stars.length ? f.stars.join(',') : '1,2,3,4,5') : '1,2,3,4,5',
            nm_ids: f.nm_ids.trim() || null,
            auto_send: false, // автоотправка отключена: все ответы проходят ручное одобрение
            rules: f.rules,
            examples: f.examples.trim() || null,
            llm_provider: f.llm_provider,
            llm_model: f.llm_model.trim() || 'deepseek-chat',
            llm_base_url: f.llm_base_url.trim() || null,
            enabled: f.enabled,
        };
        onSave(body);
    };

    return (
        <div className="glass-card" style={{ padding: 20, marginBottom: 16, borderLeft: '3px solid var(--color-accent)' }}>
            <h3 style={{ margin: '0 0 12px', fontSize: 17 }}>{title}</h3>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12, marginBottom: 12 }}>
                <label style={{ fontSize: 13 }}>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>Название</div>
                    <input className="form-input" value={f.name} onChange={e => setF({ ...f, name: e.target.value })} placeholder="Например: Ответы на негатив" />
                </label>
                <label style={{ fontSize: 13 }}>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>На что отвечает</div>
                    <select className="form-input" value={f.target} onChange={e => setF({ ...f, target: e.target.value as ReplyAgentTarget })}>
                        <option value="feedback">Отзывы</option>
                        <option value="question">Вопросы</option>
                        <option value="both">Отзывы и вопросы</option>
                    </select>
                </label>
                <label style={{ fontSize: 13 }}>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>Модель LLM</div>
                    <input className="form-input" value={f.llm_model} onChange={e => setF({ ...f, llm_model: e.target.value })} placeholder="deepseek-chat" />
                </label>
                <label style={{ fontSize: 13 }}>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>Провайдер</div>
                    <select className="form-input" value={f.llm_provider} onChange={e => setF({ ...f, llm_provider: e.target.value as LlmProvider })}>
                        <option value="openai_compatible">OpenAI-совместимый (DeepSeek и др.)</option>
                        <option value="claude">Claude</option>
                    </select>
                </label>
                <label style={{ fontSize: 13 }}>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>Base URL (опционально)</div>
                    <input className="form-input" value={f.llm_base_url} onChange={e => setF({ ...f, llm_base_url: e.target.value })} placeholder="https://api.deepseek.com" />
                </label>
                <label style={{ fontSize: 13 }}>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>nmID товаров (через запятую, пусто = все)</div>
                    <input className="form-input" value={f.nm_ids} onChange={e => setF({ ...f, nm_ids: e.target.value })} placeholder="12345678, 87654321" />
                </label>
            </div>

            {withFeedback && (
                <div style={{ marginBottom: 12, fontSize: 13 }}>
                    <div style={{ fontWeight: 600, marginBottom: 6 }}>Оценки отзывов, на которые отвечать</div>
                    <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                        {[1, 2, 3, 4, 5].map(n => (
                            <label key={n} style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                                <input type="checkbox" checked={f.stars.includes(n)} onChange={() => toggleStar(n)} />
                                {n}★
                            </label>
                        ))}
                    </div>
                </div>
            )}

            <label style={{ display: 'block', fontSize: 13, marginBottom: 12 }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>Правила и тон ответов</div>
                <textarea
                    className="form-input"
                    value={f.rules}
                    onChange={e => setF({ ...f, rules: e.target.value })}
                    rows={4}
                    placeholder="Например: Отвечай вежливо и коротко, от лица бренда. Извинись за неудобства, предложи решение, не спорь с покупателем."
                    style={{ width: '100%', resize: 'vertical', lineHeight: 1.5 }}
                />
            </label>

            <label style={{ display: 'block', fontSize: 13, marginBottom: 12 }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>Примеры ответов (few-shot, JSON — опционально)</div>
                <textarea
                    className="form-input"
                    value={f.examples}
                    onChange={e => setF({ ...f, examples: e.target.value })}
                    rows={3}
                    placeholder='[{"input": "текст отзыва", "output": "текст ответа"}]'
                    style={{ width: '100%', resize: 'vertical', lineHeight: 1.5, fontFamily: 'monospace' }}
                />
            </label>

            <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginBottom: 12, fontSize: 14 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                    <input type="checkbox" checked={f.enabled} onChange={e => setF({ ...f, enabled: e.target.checked })} />
                    Агент включён
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'not-allowed', opacity: 0.6 }}>
                    <input type="checkbox" checked={false} disabled />
                    Отправлять без подтверждения (черновики сразу одобряются)
                </label>
            </div>
            <div style={{ margin: '-4px 0 12px', fontSize: 13, color: 'var(--color-text-dim)' }}>
                ℹ️ Автоотправка отключена — все ответы проходят ручное одобрение во вкладке «📨 Очередь ответов».
            </div>

            {err && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 8 }}>{err}</div>}
            <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn-primary" onClick={submit} disabled={busy || !f.name.trim()}>
                    {busy ? 'Сохранение…' : 'Сохранить'}
                </button>
                <button className="btn btn-secondary" onClick={onCancel}>Отмена</button>
            </div>
        </div>
    );
}

function AgentsSettings() {
    const [agents, setAgents] = useState<ReplyAgent[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [formFor, setFormFor] = useState<'new' | number | null>(null); // 'new' | id редактируемого
    const [formBusy, setFormBusy] = useState(false);
    const [formErr, setFormErr] = useState('');
    const [runBusy, setRunBusy] = useState<number | null>(null);
    const [runResults, setRunResults] = useState<Record<number, ReplyAgentRunResult | { error: string }>>({});

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setAgents(await api.getReplyAgents());
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить агентов');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const save = async (body: ReplyAgentSave) => {
        setFormBusy(true);
        setFormErr('');
        try {
            if (formFor === 'new') {
                await api.createReplyAgent(body);
            } else if (typeof formFor === 'number') {
                await api.updateReplyAgent(formFor, body);
            }
            setFormFor(null);
            await load();
        } catch (e) {
            setFormErr(e instanceof Error ? e.message : 'Не удалось сохранить агента');
        } finally {
            setFormBusy(false);
        }
    };

    const remove = async (a: ReplyAgent) => {
        if (!window.confirm(`Удалить агента «${a.name}»? Созданные им черновики останутся.`)) return;
        try {
            await api.deleteReplyAgent(a.id);
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось удалить агента');
        }
    };

    const toggleEnabled = async (a: ReplyAgent) => {
        try {
            await api.updateReplyAgent(a.id, { enabled: !a.enabled });
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось переключить агента');
        }
    };

    const run = async (a: ReplyAgent) => {
        setRunBusy(a.id);
        try {
            const res = await api.runReplyAgent(a.id);
            setRunResults(prev => ({ ...prev, [a.id]: res }));
            await load(); // обновить last_run_at
        } catch (e) {
            setRunResults(prev => ({ ...prev, [a.id]: { error: e instanceof Error ? e.message : 'Ошибка запуска' } }));
        } finally {
            setRunBusy(null);
        }
    };

    const editAgent = formFor !== 'new' && typeof formFor === 'number' ? agents.find(a => a.id === formFor) : undefined;

    return (
        <div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center' }}>
                <button className="btn btn-primary btn-sm" onClick={() => { setFormFor('new'); setFormErr(''); }} disabled={formFor !== null}>
                    ＋ Новый агент
                </button>
            </div>

            {formFor === 'new' && (
                <AgentForm
                    initial={EMPTY_FORM}
                    title="Новый агент автоответов"
                    busy={formBusy}
                    err={formErr}
                    onSave={save}
                    onCancel={() => setFormFor(null)}
                />
            )}

            {error && (
                <div className="glass-card" style={{ marginBottom: 16, color: 'var(--color-danger)' }}>
                    {error}{' '}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={load}>Повторить</button>
                </div>
            )}

            {loading && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48, color: 'var(--color-text-dim)' }}>
                    Загрузка агентов…
                </div>
            )}

            {!loading && !error && agents.length === 0 && formFor === null && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>🤖</div>
                    <h3 style={{ margin: '0 0 8px' }}>Агентов пока нет</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>
                        Создайте агента — он будет генерировать черновики ответов на неотвеченные отзывы и вопросы.
                    </p>
                </div>
            )}

            {!loading && agents.map(a => (
                <div key={a.id}>
                    {editAgent?.id === a.id ? (
                        <AgentForm
                            initial={agentToForm(a)}
                            title={`Редактирование: ${a.name}`}
                            busy={formBusy}
                            err={formErr}
                            onSave={save}
                            onCancel={() => setFormFor(null)}
                        />
                    ) : (
                        <div className="glass-card" style={{ padding: 16, marginBottom: 12 }}>
                            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 13 }}>
                                <span style={{ fontWeight: 700, fontSize: 15 }}>{a.name}</span>
                                <span className="badge badge-secondary">{TARGET_LABEL[a.target] ?? a.target}</span>
                                {(a.target === 'feedback' || a.target === 'both') && (
                                    <span style={{ color: 'var(--color-text-dim)' }}>★ {a.star_levels}</span>
                                )}
                                {a.auto_send && <span className="badge badge-warning">Автоотправка</span>}
                                {a.enabled
                                    ? <span className="badge badge-success">Включён</span>
                                    : <span className="badge badge-secondary">Выключен</span>}
                                <span style={{ marginLeft: 'auto', color: 'var(--color-text-dim)' }}>
                                    {a.llm_model}
                                    {a.last_run_at ? ` · запуск: ${formatDate(a.last_run_at)}` : ' · ещё не запускался'}
                                </span>
                            </div>

                            {a.rules && (
                                <p style={{ margin: '8px 0 0', fontSize: 13, color: 'var(--color-text-dim)', whiteSpace: 'pre-wrap' }}>
                                    Правила: {a.rules.length > 200 ? `${a.rules.slice(0, 200)}…` : a.rules}
                                </p>
                            )}

                            {runResults[a.id] && (
                                <div style={{ marginTop: 8, fontSize: 13, color: 'error' in runResults[a.id] ? 'var(--color-danger)' : 'var(--color-success)' }}>
                                    {'error' in runResults[a.id]
                                        ? `Ошибка: ${(runResults[a.id] as { error: string }).error}`
                                        : (() => {
                                            const r = runResults[a.id] as ReplyAgentRunResult;
                                            return `✓ Проверено: ${formatNumber(r.checked, 0)}, создано черновиков: ${formatNumber(r.drafted, 0)}, ошибок: ${formatNumber(r.errors, 0)}${r.auto_send ? ' — автоотправка включена, черновики уже одобрены' : ''}`;
                                        })()}
                                </div>
                            )}

                            <div style={{ display: 'flex', gap: 6, marginTop: 10, flexWrap: 'wrap' }}>
                                <button className="btn btn-success btn-sm" onClick={() => run(a)} disabled={runBusy !== null || !a.enabled}>
                                    {runBusy === a.id ? 'Генерация…' : '▶ Запустить сейчас'}
                                </button>
                                <button className="btn btn-secondary btn-sm" onClick={() => { setFormFor(a.id); setFormErr(''); }} disabled={formFor !== null}>
                                    ✏️ Изменить
                                </button>
                                <button className="btn btn-secondary btn-sm" onClick={() => toggleEnabled(a)}>
                                    {a.enabled ? 'Выключить' : 'Включить'}
                                </button>
                                <button className="btn btn-danger btn-sm" onClick={() => remove(a)}>
                                    Удалить
                                </button>
                            </div>
                        </div>
                    )}
                </div>
            ))}
        </div>
    );
}

// ─── Вкладка «Автоответы» ────────────────────────────────────────────────────

export default function AutoRepliesTab() {
    const [sub, setSub] = useState<SubTab>('queue');

    return (
        <div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
                <button className={`btn btn-sm ${sub === 'queue' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setSub('queue')}>
                    📨 Очередь ответов
                </button>
                <button className={`btn btn-sm ${sub === 'agents' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setSub('agents')}>
                    ⚙️ Настройки агента
                </button>
            </div>

            {sub === 'queue' && <RepliesQueue />}
            {sub === 'agents' && <AgentsSettings />}
        </div>
    );
}
