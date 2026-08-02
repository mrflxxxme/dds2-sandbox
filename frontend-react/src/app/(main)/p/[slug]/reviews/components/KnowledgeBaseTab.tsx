'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type { KbCreate, KbItem, KbProductItem, KbTopic, KbUpdate } from '@/types/api';

const TOPICS: KbTopic[] = ['Размер', 'Доставка', 'Состав', 'Цвет', 'Комплект', 'Гарантия', 'Качество', 'Прочее'];
const PAGE = 200; // записей КБ за подгрузку

// ─── Форма записи КБ (создание / редактирование) ────────────────────────────

interface KbFormState {
    nm_id: string;
    topic: string;
    question_example: string;
    answer: string;
}

function KbEntryForm({ initial, title, lockNmId, busy, err, onSave, onCancel }: {
    initial: KbFormState;
    title: string;
    /** true — nm_id зафиксирован выбранным товаром, поле не редактируется */
    lockNmId: boolean;
    busy: boolean;
    err: string;
    onSave: (body: { nm_id: number; topic: string; question_example: string | null; answer: string }) => void;
    onCancel: () => void;
}) {
    const [f, setF] = useState<KbFormState>(initial);
    const nmIdNum = parseInt(f.nm_id.trim(), 10);
    const valid = Number.isFinite(nmIdNum) && nmIdNum > 0 && f.topic.trim() !== '' && f.answer.trim() !== '';

    return (
        <div className="glass-card" style={{ padding: 16, marginBottom: 12, borderLeft: '3px solid var(--color-accent)' }}>
            <h4 style={{ margin: '0 0 12px', fontSize: 15 }}>{title}</h4>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12, marginBottom: 12 }}>
                <label style={{ fontSize: 13 }}>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>nmID товара</div>
                    <input
                        className="form-input"
                        value={f.nm_id}
                        onChange={e => setF({ ...f, nm_id: e.target.value.replace(/[^\d]/g, '') })}
                        placeholder="12345678"
                        disabled={lockNmId}
                    />
                </label>
                <label style={{ fontSize: 13 }}>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>Тема</div>
                    <select className="form-input" value={f.topic} onChange={e => setF({ ...f, topic: e.target.value })}>
                        {TOPICS.map(t => <option key={t} value={t}>{t}</option>)}
                    </select>
                </label>
            </div>
            <label style={{ display: 'block', fontSize: 13, marginBottom: 12 }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>Пример вопроса (опционально)</div>
                <input
                    className="form-input"
                    value={f.question_example}
                    onChange={e => setF({ ...f, question_example: e.target.value })}
                    placeholder="Например: Какой размер выбрать на рост 170?"
                />
            </label>
            <label style={{ display: 'block', fontSize: 13, marginBottom: 12 }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>Ответ / факт</div>
                <textarea
                    className="form-input"
                    value={f.answer}
                    onChange={e => setF({ ...f, answer: e.target.value })}
                    rows={3}
                    maxLength={5000}
                    placeholder="Точная информация о товаре, которую агент использует в ответах…"
                    style={{ width: '100%', resize: 'vertical', lineHeight: 1.5 }}
                />
            </label>
            {err && <div style={{ color: 'var(--color-danger)', fontSize: 13, marginBottom: 8 }}>{err}</div>}
            <div style={{ display: 'flex', gap: 8 }}>
                <button
                    className="btn btn-primary btn-sm"
                    disabled={busy || !valid}
                    onClick={() => onSave({
                        nm_id: nmIdNum,
                        topic: f.topic,
                        question_example: f.question_example.trim() || null,
                        answer: f.answer.trim(),
                    })}
                >
                    {busy ? 'Сохранение…' : 'Сохранить'}
                </button>
                <button className="btn btn-secondary btn-sm" onClick={onCancel}>Отмена</button>
            </div>
        </div>
    );
}

// ─── Карточка записи КБ ──────────────────────────────────────────────────────

function KbEntryCard({ item, productLabel, onChanged, onDeleted }: {
    item: KbItem;
    productLabel: string;
    onChanged: (updated: KbItem) => void;
    onDeleted: (id: number) => void;
}) {
    const [editing, setEditing] = useState(false);
    const [busy, setBusy] = useState(false);
    const [err, setErr] = useState('');

    const toggleEnabled = async () => {
        setBusy(true);
        setErr('');
        try {
            const updated = await api.updateKb(item.id, { enabled: !item.enabled });
            onChanged(updated);
        } catch (e) {
            setErr(e instanceof Error ? e.message : 'Не удалось переключить запись');
        } finally {
            setBusy(false);
        }
    };

    const remove = async () => {
        if (!window.confirm('Удалить запись из базы знаний? Мягкая альтернатива — выключить переключателем.')) return;
        setBusy(true);
        setErr('');
        try {
            await api.deleteKb(item.id);
            onDeleted(item.id);
        } catch (e) {
            setErr(e instanceof Error ? e.message : 'Не удалось удалить запись');
            setBusy(false);
        }
    };

    const saveEdit = async (body: { nm_id: number; topic: string; question_example: string | null; answer: string }) => {
        setBusy(true);
        setErr('');
        try {
            const patch: KbUpdate = {
                topic: body.topic,
                question_example: body.question_example,
                answer: body.answer,
            };
            const updated = await api.updateKb(item.id, patch);
            setEditing(false);
            onChanged(updated);
        } catch (e) {
            setErr(e instanceof Error ? e.message : 'Не удалось сохранить запись');
        } finally {
            setBusy(false);
        }
    };

    if (editing) {
        return (
            <KbEntryForm
                initial={{
                    nm_id: String(item.nm_id),
                    topic: item.topic,
                    question_example: item.question_example ?? '',
                    answer: item.answer,
                }}
                title={`Редактирование записи #${item.id}`}
                lockNmId
                busy={busy}
                err={err}
                onSave={saveEdit}
                onCancel={() => { setEditing(false); setErr(''); }}
            />
        );
    }

    return (
        <div className="glass-card" style={{ padding: 16, marginBottom: 12, opacity: item.enabled ? 1 : 0.6 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 13 }}>
                <span className="badge badge-info">{item.topic}</span>
                {item.source === 'import'
                    ? <span className="badge badge-secondary">📥 импорт</span>
                    : <span className="badge badge-secondary">✍️ вручную</span>}
                {!item.enabled && <span className="badge badge-warning">Выключена</span>}
                <span style={{ color: 'var(--color-text-dim)' }}>{productLabel}</span>
                <label style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 13 }}>
                    <input type="checkbox" checked={item.enabled} onChange={toggleEnabled} disabled={busy} />
                    Использовать
                </label>
            </div>

            {item.question_example && (
                <p style={{ margin: '8px 0 0', fontSize: 13, color: 'var(--color-text-dim)', fontStyle: 'italic', whiteSpace: 'pre-wrap' }}>
                    Вопрос: {item.question_example}
                </p>
            )}
            <p style={{ margin: '8px 0 0', fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>{item.answer}</p>

            {err && <div style={{ marginTop: 8, fontSize: 13, color: 'var(--color-danger)' }}>{err}</div>}

            <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
                <button className="btn btn-secondary btn-sm" onClick={() => setEditing(true)} disabled={busy}>
                    ✏️ Изменить
                </button>
                <button className="btn btn-danger btn-sm" onClick={remove} disabled={busy}>
                    Удалить
                </button>
            </div>
        </div>
    );
}

// ─── Вкладка «База знаний» ───────────────────────────────────────────────────

export default function KnowledgeBaseTab() {
    const [products, setProducts] = useState<KbProductItem[]>([]);
    const [productsLoading, setProductsLoading] = useState(true);
    const [search, setSearch] = useState('');
    const [selectedNm, setSelectedNm] = useState<number | null>(null);

    const [entries, setEntries] = useState<KbItem[]>([]);
    const [entriesTotal, setEntriesTotal] = useState(0);
    const [entriesLoading, setEntriesLoading] = useState(false);
    const [loadingMore, setLoadingMore] = useState(false);
    const [topicFilter, setTopicFilter] = useState<string>('');

    const [createOpen, setCreateOpen] = useState(false);
    const [formBusy, setFormBusy] = useState(false);
    const [formErr, setFormErr] = useState('');

    const [importBusy, setImportBusy] = useState(false);
    const [importMsg, setImportMsg] = useState('');
    const [error, setError] = useState('');

    // ─── Товары ───
    const loadProducts = useCallback(async () => {
        setProductsLoading(true);
        setError('');
        try {
            const res = await api.getKbProducts();
            setProducts(res.items);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить товары');
            setProducts([]);
        } finally {
            setProductsLoading(false);
        }
    }, []);

    useEffect(() => { loadProducts(); }, [loadProducts]);

    const filteredProducts = useMemo(() => {
        const q = search.trim().toLowerCase();
        if (!q) return products;
        return products.filter(p =>
            (p.product_name ?? '').toLowerCase().includes(q)
            || (p.article ?? '').toLowerCase().includes(q)
            || (p.brand ?? '').toLowerCase().includes(q)
            || String(p.nm_id).includes(q),
        );
    }, [products, search]);

    const selectedProduct = useMemo(
        () => products.find(p => p.nm_id === selectedNm) ?? null,
        [products, selectedNm],
    );

    // ─── Записи КБ выбранного товара ───
    const loadEntries = useCallback(async (nmId: number) => {
        setEntriesLoading(true);
        setError('');
        try {
            const res = await api.getKbList({ nmId, take: PAGE, skip: 0 });
            setEntries(res.items);
            setEntriesTotal(res.total);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить записи базы знаний');
            setEntries([]);
            setEntriesTotal(0);
        } finally {
            setEntriesLoading(false);
        }
    }, []);

    useEffect(() => {
        setTopicFilter('');
        setCreateOpen(false);
        if (selectedNm != null) {
            loadEntries(selectedNm);
        } else {
            setEntries([]);
            setEntriesTotal(0);
        }
    }, [selectedNm, loadEntries]);

    const loadMore = useCallback(async () => {
        if (selectedNm == null) return;
        setLoadingMore(true);
        try {
            const res = await api.getKbList({ nmId: selectedNm, take: PAGE, skip: entries.length });
            setEntries(prev => [...prev, ...res.items]);
            setEntriesTotal(res.total);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить ещё');
        } finally {
            setLoadingMore(false);
        }
    }, [selectedNm, entries.length]);

    const topicCounts = useMemo(() => {
        const m = new Map<string, number>();
        for (const e of entries) m.set(e.topic, (m.get(e.topic) ?? 0) + 1);
        return m;
    }, [entries]);

    const visibleEntries = useMemo(
        () => (topicFilter ? entries.filter(e => e.topic === topicFilter) : entries),
        [entries, topicFilter],
    );

    const hasMore = entries.length < entriesTotal;

    // ─── Создание ───
    const createEntry = async (body: { nm_id: number; topic: string; question_example: string | null; answer: string }) => {
        setFormBusy(true);
        setFormErr('');
        try {
            const payload: KbCreate = {
                nm_id: body.nm_id,
                topic: body.topic,
                question_example: body.question_example,
                answer: body.answer,
            };
            await api.createKb(payload);
            setCreateOpen(false);
            await loadProducts();
            if (body.nm_id === selectedNm) {
                await loadEntries(body.nm_id);
            } else {
                setSelectedNm(body.nm_id);
            }
        } catch (e) {
            setFormErr(e instanceof Error ? e.message : 'Не удалось создать запись');
        } finally {
            setFormBusy(false);
        }
    };

    // ─── Импорт ───
    const runImport = async () => {
        if (!window.confirm('Импортировать базу знаний из архива отвеченных вопросов WB? Дубли будут пропущены автоматически.')) return;
        setImportBusy(true);
        setImportMsg('');
        setError('');
        try {
            const res = await api.importKb();
            setImportMsg(
                `✓ Импорт завершён: создано ${formatNumber(res.created, 0)}, `
                + `дублей пропущено ${formatNumber(res.skipped_dupe, 0)}, `
                + `пустых ${formatNumber(res.skipped_empty, 0)} `
                + `(вопросов в архиве: ${formatNumber(res.source_questions, 0)}, товаров: ${formatNumber(res.nm_count, 0)})`,
            );
            await loadProducts();
            if (selectedNm != null) await loadEntries(selectedNm);
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось импортировать базу знаний');
        } finally {
            setImportBusy(false);
        }
    };

    const productLabel = (p: KbProductItem) =>
        [p.brand, p.product_name].filter(Boolean).join(' · ') || p.article || `nmID ${p.nm_id}`;

    const selectedLabel = selectedProduct
        ? productLabel(selectedProduct)
        : (selectedNm != null ? `nmID ${selectedNm}` : '');

    return (
        <div>
            <div style={{ display: 'flex', gap: 8, marginBottom: 20, alignItems: 'center', flexWrap: 'wrap' }}>
                <button
                    className="btn btn-primary btn-sm"
                    onClick={() => { setCreateOpen(v => !v); setFormErr(''); }}
                >
                    ＋ Добавить знание
                </button>
                <button className="btn btn-secondary btn-sm" onClick={runImport} disabled={importBusy}>
                    {importBusy ? 'Импорт…' : '📥 Импорт из архива WB'}
                </button>
            </div>

            {importMsg && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 20, color: 'var(--color-success)', display: 'flex', alignItems: 'center', gap: 8 }}>
                    {importMsg}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={() => setImportMsg('')}>✕</button>
                </div>
            )}

            {createOpen && (
                <KbEntryForm
                    initial={{
                        nm_id: selectedNm != null ? String(selectedNm) : '',
                        topic: TOPICS[0],
                        question_example: '',
                        answer: '',
                    }}
                    title={selectedNm != null ? `Новое знание для: ${selectedLabel}` : 'Новое знание (укажите nmID товара)'}
                    lockNmId={selectedNm != null}
                    busy={formBusy}
                    err={formErr}
                    onSave={createEntry}
                    onCancel={() => setCreateOpen(false)}
                />
            )}

            {error && (
                <div className="glass-card" style={{ marginBottom: 20, color: 'var(--color-danger)' }}>
                    {error}{' '}
                    <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={loadProducts}>Повторить</button>
                </div>
            )}

            {productsLoading && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48, color: 'var(--color-text-dim)' }}>
                    Загрузка товаров…
                </div>
            )}

            {!productsLoading && !error && products.length === 0 && (
                <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                    <div style={{ fontSize: 48, marginBottom: 12 }}>📚</div>
                    <h3 style={{ margin: '0 0 8px' }}>Товаров нет</h3>
                    <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>
                        Товары появятся после синхронизации отзывов/вопросов. Базу знаний можно наполнить вручную
                        кнопкой «＋ Добавить знание» или импортом из архива отвеченных вопросов WB.
                    </p>
                </div>
            )}

            {!productsLoading && products.length > 0 && (
                <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', alignItems: 'flex-start' }}>
                    {/* Список товаров */}
                    <div style={{ flex: '0 1 300px', minWidth: 260 }}>
                        <input
                            className="form-input"
                            value={search}
                            onChange={e => setSearch(e.target.value)}
                            placeholder="Поиск: название, артикул, nmID…"
                            style={{ width: '100%', marginBottom: 12, fontSize: 13 }}
                        />
                        {filteredProducts.length === 0 && (
                            <div className="glass-card" style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-dim)', fontSize: 13 }}>
                                По запросу «{search}» ничего не найдено.
                            </div>
                        )}
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 560, overflowY: 'auto' }}>
                            {filteredProducts.map(p => (
                                <button
                                    key={p.nm_id}
                                    className="glass-card"
                                    onClick={() => setSelectedNm(p.nm_id)}
                                    style={{
                                        padding: 12,
                                        textAlign: 'left',
                                        cursor: 'pointer',
                                        border: selectedNm === p.nm_id ? '1px solid var(--color-accent)' : undefined,
                                        background: selectedNm === p.nm_id ? 'var(--color-bg-card)' : undefined,
                                    }}
                                >
                                    <div style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.4 }}>
                                        {p.product_name || p.article || `nmID ${p.nm_id}`}
                                    </div>
                                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4 }}>
                                        {p.article ? `${p.article} · ` : ''}nmID {p.nm_id}
                                    </div>
                                    <div style={{ marginTop: 6 }}>
                                        <span className={`badge ${p.kb_count > 0 ? 'badge-success' : 'badge-secondary'}`}>
                                            📚 {formatNumber(p.kb_count, 0)}
                                        </span>
                                    </div>
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* Записи КБ выбранного товара */}
                    <div style={{ flex: '1 1 420px', minWidth: 320 }}>
                        {selectedNm == null && (
                            <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                                <div style={{ fontSize: 48, marginBottom: 12 }}>👈</div>
                                <h3 style={{ margin: '0 0 8px' }}>Выберите товар</h3>
                                <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>
                                    Кликните по товару слева, чтобы посмотреть и дополнить его базу знаний.
                                </p>
                            </div>
                        )}

                        {selectedNm != null && (
                            <>
                                <div style={{ display: 'flex', gap: 6, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                                    <button
                                        className={`btn btn-sm ${topicFilter === '' ? 'btn-primary' : 'btn-secondary'}`}
                                        onClick={() => setTopicFilter('')}
                                    >
                                        Все ({formatNumber(entries.length, 0)})
                                    </button>
                                    {[...topicCounts.entries()].map(([topic, count]) => (
                                        <button
                                            key={topic}
                                            className={`btn btn-sm ${topicFilter === topic ? 'btn-primary' : 'btn-secondary'}`}
                                            onClick={() => setTopicFilter(topic)}
                                        >
                                            {topic} ({formatNumber(count, 0)})
                                        </button>
                                    ))}
                                </div>

                                {entriesLoading && (
                                    <div className="glass-card" style={{ textAlign: 'center', padding: 48, color: 'var(--color-text-dim)' }}>
                                        Загрузка записей…
                                    </div>
                                )}

                                {!entriesLoading && entries.length === 0 && (
                                    <div className="glass-card" style={{ textAlign: 'center', padding: 48 }}>
                                        <div style={{ fontSize: 48, marginBottom: 12 }}>📚</div>
                                        <h3 style={{ margin: '0 0 8px' }}>Записей пока нет</h3>
                                        <p style={{ color: 'var(--color-text-dim)', margin: 0 }}>
                                            Добавьте знание вручную кнопкой «＋ Добавить знание» или импортируйте из архива отвеченных вопросов WB.
                                        </p>
                                    </div>
                                )}

                                {!entriesLoading && entries.length > 0 && visibleEntries.length === 0 && (
                                    <div className="glass-card" style={{ textAlign: 'center', padding: 32, color: 'var(--color-text-dim)' }}>
                                        С темой «{topicFilter}» записей нет.
                                    </div>
                                )}

                                {!entriesLoading && visibleEntries.map(item => (
                                    <KbEntryCard
                                        key={item.id}
                                        item={item}
                                        productLabel={selectedLabel}
                                        onChanged={updated => setEntries(prev => prev.map(e => (e.id === updated.id ? updated : e)))}
                                        onDeleted={id => {
                                            setEntries(prev => prev.filter(e => e.id !== id));
                                            setEntriesTotal(t => Math.max(0, t - 1));
                                            loadProducts();
                                        }}
                                    />
                                ))}

                                {!entriesLoading && hasMore && (
                                    <div style={{ textAlign: 'center', marginTop: 8 }}>
                                        <button className="btn btn-secondary" onClick={loadMore} disabled={loadingMore}>
                                            {loadingMore ? 'Загрузка…' : `Показать ещё (${formatNumber(entriesTotal - entries.length, 0)})`}
                                        </button>
                                    </div>
                                )}
                            </>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
