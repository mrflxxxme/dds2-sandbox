'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate } from '@/lib/utils';
import KpiCard from '@/components/KpiCard';
import type { ComplaintCandidate, ComplaintItem, ComplaintReason, ComplaintStatus, ComplaintStats } from '@/types/api';

const REASONS: { key: ComplaintReason; label: string }[] = [
    { key: 'not_related', label: 'Отзыв не относится к товару' },
    { key: 'competitors', label: 'Отзыв оставили конкуренты' },
    { key: 'other', label: 'Другое' },
];
const SELLER_KEY = 'reviews.complaint.seller';
const DEFAULT_SELLER = 'ООО «ПлюсВайб»';

const STATUS_LABEL: Record<ComplaintStatus, string> = { pending: 'В ожидании', removed: 'Удалён', rejected: 'Не удалён' };
const STATUS_BADGE: Record<ComplaintStatus, string> = { pending: 'badge-warning', removed: 'badge-success', rejected: 'badge-danger' };

/** Текст жалобы по шаблону (причина + продавец). */
function buildTemplate(seller: string, reason: ComplaintReason): string {
    const s = seller.trim() || DEFAULT_SELLER;
    const cause = reason === 'competitors'
        ? 'поскольку есть основания полагать, что он оставлен конкурентами с целью навредить деловой репутации'
        : reason === 'other'
            ? 'поскольку он нарушает правила размещения пользовательских отзывов'
            : 'поскольку он не относится к самому товару и, предположительно, оставлен с целью навредить деловой репутации';
    return `Уважаемая служба поддержки,

Просим удалить отзыв, размещённый на странице товара (продавец: ${s}), ${cause}.

Содержание отзыва не касается характеристик, качества или потребительских свойств товара, что противоречит правилам размещения пользовательских отзывов. Также имеются основания полагать, что отзыв предвзятый и не основан на реальном опыте покупки.

Просим рассмотреть жалобу и удалить данный отзыв в соответствии с политикой платформы.

С уважением,
${s}`;
}

function Stars({ rating }: { rating: number }) {
    const r = Math.max(0, Math.min(5, rating));
    return <span style={{ color: 'var(--color-warning)', letterSpacing: 1 }}>{'★'.repeat(r)}<span style={{ color: 'var(--color-border)' }}>{'★'.repeat(5 - r)}</span></span>;
}

/** Модалка подачи жалобы. */
function ComplaintModal({ candidate, seller, onClose, onSubmitted }: {
    candidate: ComplaintCandidate;
    seller: string;
    onClose: () => void;
    onSubmitted: () => void;
}) {
    const [reason, setReason] = useState<ComplaintReason>('not_related');
    const [text, setText] = useState(buildTemplate(seller, 'not_related'));
    const [copied, setCopied] = useState(false);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState('');

    const changeReason = (r: ComplaintReason) => { setReason(r); setText(buildTemplate(seller, r)); };

    const submit = async () => {
        setBusy(true); setErr('');
        try {
            await api.createComplaint({ wb_feedback_id: candidate.wb_feedback_id, reason, text });
            onSubmitted();
        } catch (e) {
            setErr(e instanceof Error ? e.message : 'Не удалось сохранить жалобу');
        } finally {
            setBusy(false);
        }
    };

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'flex-start', justifyContent: 'center', zIndex: 1000, padding: 24, overflowY: 'auto' }} onClick={onClose}>
            <div className="glass-card" style={{ maxWidth: 640, width: '100%', padding: 24, marginTop: 40 }} onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
                    <h2 style={{ margin: 0, fontSize: 22 }}>Жалоба на отзыв</h2>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>✕</button>
                </div>

                {/* контекст отзыва */}
                <div className="glass-card" style={{ padding: 12, margin: '12px 0', background: 'var(--color-bg-card)' }}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
                        <Stars rating={candidate.rating} />
                        <span style={{ fontWeight: 600 }}>{candidate.product_name || (candidate.nm_id ? `nmID ${candidate.nm_id}` : '')}</span>
                    </div>
                    {(candidate.text || candidate.cons) && (
                        <p style={{ margin: '6px 0 0', fontSize: 13, color: 'var(--color-text)' }}>{candidate.text || candidate.cons}</p>
                    )}
                </div>

                <div style={{ fontWeight: 600, marginBottom: 8 }}>Причина</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>
                    {REASONS.map(r => (
                        <label key={r.key} style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', fontSize: 15 }}>
                            <input type="radio" name="reason" checked={reason === r.key} onChange={() => changeReason(r.key)} />
                            {r.label}
                        </label>
                    ))}
                </div>

                <div style={{ fontWeight: 600, marginBottom: 6 }}>Текст жалобы</div>
                <textarea
                    className="form-input"
                    value={text}
                    onChange={e => setText(e.target.value)}
                    rows={11}
                    maxLength={1000}
                    style={{ width: '100%', resize: 'vertical', fontSize: 13, lineHeight: 1.5 }}
                />
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>
                    <span>{formatNumber(text.length, 0)} / 1000</span>
                    <button className="btn btn-secondary btn-sm" onClick={() => { navigator.clipboard?.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
                        {copied ? 'Скопировано ✓' : 'Копировать текст'}
                    </button>
                </div>

                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', margin: '12px 0' }}>
                    Скопируйте текст и подайте жалобу в кабинете WB (Отзывы → «Пожаловаться»). Кнопка ниже
                    зафиксирует жалобу в учёте — статус «Удалён / Не удалён» проставите позже по итогу рассмотрения.
                </div>

                {err && <div style={{ color: 'var(--color-danger)', marginBottom: 8, fontSize: 13 }}>{err}</div>}
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-primary" onClick={submit} disabled={busy || !text.trim()}>{busy ? 'Сохранение…' : 'Зафиксировать жалобу'}</button>
                    <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
                </div>
            </div>
        </div>
    );
}

/** Модалка массовой подачи: один текст на все накопившиеся отзывы. */
function BulkModal({ count, seller, onClose, onDone }: {
    count: number;
    seller: string;
    onClose: () => void;
    onDone: (created: number, truncated: boolean) => void;
}) {
    const [reason, setReason] = useState<ComplaintReason>('not_related');
    const [text, setText] = useState(buildTemplate(seller, 'not_related'));
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState('');

    const changeReason = (r: ComplaintReason) => { setReason(r); setText(buildTemplate(seller, r)); };

    const submit = async () => {
        setBusy(true); setErr('');
        try {
            const res = await api.createComplaintsBulk({ reason, text, max_rating: 3 });
            onDone(res.created, res.truncated);
        } catch (e) {
            setErr(e instanceof Error ? e.message : 'Не удалось подать жалобы');
        } finally {
            setBusy(false);
        }
    };

    return (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'flex-start', justifyContent: 'center', zIndex: 1000, padding: 24, overflowY: 'auto' }} onClick={onClose}>
            <div className="glass-card" style={{ maxWidth: 640, width: '100%', padding: 24, marginTop: 40 }} onClick={e => e.stopPropagation()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
                    <h2 style={{ margin: 0, fontSize: 22 }}>🗑 Подготовить жалобы на все</h2>
                    <button className="btn btn-secondary btn-sm" onClick={onClose}>✕</button>
                </div>

                <div className="glass-card" style={{ padding: 12, margin: '12px 0', borderLeft: '3px solid var(--color-warning)' }}>
                    <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>Будет зафиксировано жалоб: {formatNumber(count, 0)}</div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                        Все накопившиеся отзывы 1–3★ без жалобы. Один текст на все.
                        Отзывы <b>не удаляются автоматически</b> — решение принимает WB. Жалобы нужно подать
                        в кабинете WB, здесь они фиксируются для учёта (статус «В ожидании»).
                    </div>
                </div>

                <div style={{ fontWeight: 600, marginBottom: 8 }}>Причина</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>
                    {REASONS.map(r => (
                        <label key={r.key} style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', fontSize: 15 }}>
                            <input type="radio" name="bulk-reason" checked={reason === r.key} onChange={() => changeReason(r.key)} />
                            {r.label}
                        </label>
                    ))}
                </div>

                <div style={{ fontWeight: 600, marginBottom: 6 }}>Текст жалобы (общий)</div>
                <textarea className="form-input" value={text} onChange={e => setText(e.target.value)} rows={10} maxLength={1000} style={{ width: '100%', resize: 'vertical', fontSize: 13, lineHeight: 1.5 }} />
                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', margin: '4px 0 16px' }}>{formatNumber(text.length, 0)} / 1000</div>

                {err && <div style={{ color: 'var(--color-danger)', marginBottom: 8, fontSize: 13 }}>{err}</div>}
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-danger" onClick={submit} disabled={busy || !text.trim() || count === 0} style={{ fontSize: 15, fontWeight: 600, padding: '10px 20px' }}>
                        {busy ? 'Сохраняем…' : `🗑 Взять в учёт ${formatNumber(count, 0)} жалоб`}
                    </button>
                    <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
                </div>
            </div>
        </div>
    );
}

type SubTab = 'candidates' | 'filed';

export default function ReviewsComplaintsTab() {
    const [subTab, setSubTab] = useState<SubTab>('candidates');
    const [seller, setSeller] = useState(DEFAULT_SELLER);
    const [candidates, setCandidates] = useState<ComplaintCandidate[]>([]);
    const [totalOpen, setTotalOpen] = useState(0);
    const [filed, setFiled] = useState<ComplaintItem[]>([]);
    const [stats, setStats] = useState<ComplaintStats | null>(null);
    const [hasKey, setHasKey] = useState(true);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [modalFor, setModalFor] = useState<ComplaintCandidate | null>(null);
    const [bulkOpen, setBulkOpen] = useState(false);
    const [bulkMsg, setBulkMsg] = useState('');

    useEffect(() => {
        const saved = typeof window !== 'undefined' ? window.localStorage.getItem(SELLER_KEY) : null;
        if (saved) setSeller(saved);
    }, []);

    const load = useCallback(async () => {
        setLoading(true); setError('');
        try {
            const [cand, comp] = await Promise.all([
                api.getComplaintCandidates(3, 100, true),
                api.getComplaints(),
            ]);
            setCandidates(cand.items);
            setTotalOpen(cand.total_open);
            setFiled(comp.items);
            setStats(comp.stats);
            setHasKey(cand.has_key);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const setStatus = async (id: number, status: ComplaintStatus) => {
        try {
            await api.updateComplaint(id, { status });
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось обновить статус');
        }
    };

    const saveSeller = (v: string) => { setSeller(v); if (typeof window !== 'undefined') window.localStorage.setItem(SELLER_KEY, v); };

    return (
        <div>
            {/* KPI */}
            {stats && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 16 }}>
                    <KpiCard label="Подано жалоб" value={formatNumber(stats.filed, 0)} />
                    <KpiCard label="Удалено" value={formatNumber(stats.removed, 0)} />
                    <KpiCard label="Не удалено" value={formatNumber(stats.rejected, 0)} />
                    <KpiCard label="В ожидании" value={formatNumber(stats.pending, 0)} />
                    <KpiCard label="% удаления" value={stats.removal_rate != null ? `${formatNumber(stats.removal_rate, 0)}%` : '—'} />
                </div>
            )}

            {/* Продавец для шаблона */}
            <div className="glass-card" style={{ padding: 12, marginBottom: 16, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Продавец в шаблоне жалобы:</span>
                <input className="form-input" style={{ minWidth: 240 }} value={seller} onChange={e => saveSeller(e.target.value)} placeholder="ООО «…»" />
            </div>

            {/* Массовая подача — крупная кнопка */}
            {hasKey && totalOpen > 0 && (
                <div className="glass-card" style={{ padding: 20, marginBottom: 16, display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap', borderLeft: '4px solid var(--color-danger)' }}>
                    <button
                        className="btn btn-danger"
                        onClick={() => setBulkOpen(true)}
                        style={{ fontSize: 17, fontWeight: 700, padding: '16px 32px', borderRadius: 12, display: 'inline-flex', alignItems: 'center', gap: 12 }}
                    >
                        <span style={{ fontSize: 24, lineHeight: 1 }}>🗑</span>
                        Подготовить жалобы на все ({formatNumber(totalOpen, 0)})
                    </button>
                    <div style={{ fontSize: 13, color: 'var(--color-text-dim)', minWidth: 220, flex: 1 }}>
                        Одним действием готовит общий текст и берёт в учёт все накопившиеся отзывы <b>1–3★</b>.
                        <b style={{ color: 'var(--color-warning)' }}> В WB ничего не отправляется</b> — подать жалобы
                        нужно вручную в кабинете WB, здесь фиксируется факт подачи и исход.
                    </div>
                </div>
            )}

            {bulkMsg && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 16, color: 'var(--color-success)', display: 'flex', alignItems: 'center', gap: 8 }}>
                    {bulkMsg}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => setBulkMsg('')}>✕</button>
                </div>
            )}

            {/* Подвкладки */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                <button className={`btn btn-sm ${subTab === 'candidates' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setSubTab('candidates')}>Кандидаты (1–3★)</button>
                <button className={`btn btn-sm ${subTab === 'filed' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setSubTab('filed')}>Поданные жалобы {stats ? `(${formatNumber(stats.filed, 0)})` : ''}</button>
            </div>

            {error && <div className="glass-card" style={{ marginBottom: 16, color: 'var(--color-danger)' }}>{error} <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={load}>Повторить</button></div>}
            {loading && <div className="glass-card" style={{ textAlign: 'center', padding: 48, color: 'var(--color-text-dim)' }}>Загрузка…</div>}

            {!loading && !error && !hasKey && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>🔑</div>
                    <h3 style={{ margin: '0 0 8px' }}>WB-ключ не настроен</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>Добавьте ключ и обновите отзывы, чтобы появились кандидаты на жалобу.</p>
                </div>
            )}

            {/* Кандидаты */}
            {!loading && !error && hasKey && subTab === 'candidates' && (
                candidates.length === 0 ? (
                    <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                        <div style={{ fontSize: 48, marginBottom: 12 }}>✅</div>
                        <h3 style={{ margin: '0 0 8px' }}>Нет отзывов для жалоб</h3>
                        <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>Все отзывы 1–3★ либо уже с поданной жалобой, либо отсутствуют.</p>
                    </div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                        {candidates.map(c => (
                            <div key={c.wb_feedback_id} className="glass-card" style={{ padding: 16, display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                                <div style={{ minWidth: 0, flex: 1 }}>
                                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 13 }}>
                                        <Stars rating={c.rating} />
                                        {c.user_name && <span style={{ fontWeight: 600 }}>{c.user_name}</span>}
                                        <span style={{ color: 'var(--color-text-dim)' }}>{[c.brand, c.product_name].filter(Boolean).join(' · ') || (c.nm_id ? `nmID ${c.nm_id}` : '')}</span>
                                        <span style={{ marginLeft: 'auto', color: 'var(--color-text-dim)' }}>{c.created_date ? formatDate(c.created_date) : ''}</span>
                                    </div>
                                    {(c.text || c.cons) && <p style={{ margin: '6px 0 0', fontSize: 14, lineHeight: 1.5 }}>{c.text || c.cons}</p>}
                                    {!c.text && !c.cons && <p style={{ margin: '6px 0 0', fontSize: 13, color: 'var(--color-text-dim)' }}>Без текста (только оценка)</p>}
                                </div>
                                <button className="btn btn-primary btn-sm" style={{ whiteSpace: 'nowrap' }} onClick={() => setModalFor(c)}>🚩 Пожаловаться</button>
                            </div>
                        ))}
                    </div>
                )
            )}

            {/* Поданные жалобы */}
            {!loading && !error && hasKey && subTab === 'filed' && (
                filed.length === 0 ? (
                    <div className="glass-card" style={{ textAlign: 'center', padding: 48, color: 'var(--color-text-dim)' }}>Жалобы ещё не подавались.</div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                        {filed.map(c => (
                            <div key={c.id} className="glass-card" style={{ padding: 16 }}>
                                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 13 }}>
                                    <Stars rating={c.rating} />
                                    <span style={{ fontWeight: 600 }}>{c.product_name || (c.nm_id ? `nmID ${c.nm_id}` : '')}</span>
                                    <span className={`badge ${STATUS_BADGE[c.status]}`}>{STATUS_LABEL[c.status]}</span>
                                    <span style={{ color: 'var(--color-text-dim)' }}>{REASONS.find(r => r.key === c.reason)?.label}</span>
                                    <span style={{ marginLeft: 'auto', color: 'var(--color-text-dim)' }}>{c.created_at ? formatDate(c.created_at) : ''}</span>
                                </div>
                                {c.review_text && <p style={{ margin: '6px 0 0', fontSize: 13, color: 'var(--color-text-dim)', fontStyle: 'italic' }}>Отзыв: {c.review_text}</p>}
                                <div style={{ display: 'flex', gap: 6, marginTop: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                                    <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>Исход:</span>
                                    <button className={`btn btn-sm ${c.status === 'removed' ? 'btn-success' : 'btn-secondary'}`} onClick={() => setStatus(c.id, 'removed')}>Удалён</button>
                                    <button className={`btn btn-sm ${c.status === 'rejected' ? 'btn-danger' : 'btn-secondary'}`} onClick={() => setStatus(c.id, 'rejected')}>Не удалён</button>
                                    <button className={`btn btn-sm ${c.status === 'pending' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setStatus(c.id, 'pending')}>В ожидании</button>
                                    {c.nm_id != null && <a href={`https://www.wildberries.ru/catalog/${c.nm_id}/detail.aspx`} target="_blank" rel="noopener noreferrer" style={{ marginLeft: 'auto', color: 'var(--color-accent)', textDecoration: 'none', fontSize: 12 }}>↗ WB</a>}
                                </div>
                            </div>
                        ))}
                    </div>
                )
            )}

            {modalFor && (
                <ComplaintModal
                    candidate={modalFor}
                    seller={seller}
                    onClose={() => setModalFor(null)}
                    onSubmitted={() => { setModalFor(null); load(); }}
                />
            )}

            {bulkOpen && (
                <BulkModal
                    count={totalOpen}
                    seller={seller}
                    onClose={() => setBulkOpen(false)}
                    onDone={(created, truncated) => {
                        setBulkOpen(false);
                        setBulkMsg(
                            `✓ Зафиксировано жалоб: ${formatNumber(created, 0)}`
                            + (truncated ? ' — это максимум за раз, нажмите ещё раз для остальных.' : ''),
                        );
                        load();
                    }}
                />
            )}
        </div>
    );
}
