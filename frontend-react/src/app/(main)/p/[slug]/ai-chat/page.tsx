'use client';
import { useEffect, useState, useCallback, useRef, useMemo } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDate } from '@/lib/utils';
import type { AiConversation, AiMessage, FileAttachment } from '@/types/api';

/* ─── SSE parser helper ──────────────────────────────────────────────────── */
function parseSSE(chunk: string): Array<{ type: string; content?: string; message?: AiMessage }> {
    const events: Array<{ type: string; content?: string; message?: AiMessage }> = [];
    const lines = chunk.split('\n');
    for (const line of lines) {
        if (line.startsWith('data: ')) {
            try {
                const data = JSON.parse(line.slice(6));
                events.push(data);
            } catch { /* skip malformed */ }
        }
    }
    return events;
}

/* ─── Simple HTML renderer for assistant messages (Telegram HTML subset) ── */
function renderMessageContent(content: string) {
    // Convert Telegram HTML tags to safe HTML
    const html = content
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        // Restore allowed tags
        .replace(/&lt;b&gt;/g, '<b>').replace(/&lt;\/b&gt;/g, '</b>')
        .replace(/&lt;i&gt;/g, '<i>').replace(/&lt;\/i&gt;/g, '</i>')
        .replace(/&lt;code&gt;/g, '<code>').replace(/&lt;\/code&gt;/g, '</code>')
        .replace(/&lt;pre&gt;/g, '<pre>').replace(/&lt;\/pre&gt;/g, '</pre>')
        .replace(/&lt;u&gt;/g, '<u>').replace(/&lt;\/u&gt;/g, '</u>')
        .replace(/&lt;s&gt;/g, '<s>').replace(/&lt;\/s&gt;/g, '</s>')
        // Convert newlines to <br>
        .replace(/\n/g, '<br/>');
    return <div dangerouslySetInnerHTML={{ __html: html }} />;
}

/* ─── Main component ─────────────────────────────────────────────────────── */
export default function AiChatPage() {
    const { slug } = useParams() as { slug: string };

    // Conversations
    const [conversations, setConversations] = useState<AiConversation[]>([]);
    const [activeId, setActiveId] = useState<number | null>(null);
    const [searchQuery, setSearchQuery] = useState('');

    // Messages
    const [messages, setMessages] = useState<AiMessage[]>([]);
    const [streamingContent, setStreamingContent] = useState('');
    const [isStreaming, setIsStreaming] = useState(false);

    // Input
    const [input, setInput] = useState('');
    const [attachedFiles, setAttachedFiles] = useState<FileAttachment[]>([]);

    // UI state
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [sidebarOpen, setSidebarOpen] = useState(true);
    const [editingTitle, setEditingTitle] = useState(false);
    const [titleDraft, setTitleDraft] = useState('');
    const [brands, setBrands] = useState<string[]>([]);
    const [activeBrand, setActiveBrand] = useState<string | null>(null);

    // Refs
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    // Active conversation object
    const activeConversation = useMemo(
        () => conversations.find(c => c.id === activeId) ?? null,
        [conversations, activeId],
    );

    // Filtered conversations
    const filteredConversations = useMemo(() => {
        const q = searchQuery.toLowerCase();
        const filtered = q
            ? conversations.filter(c => c.title.toLowerCase().includes(q))
            : conversations;
        return [...filtered].sort((a, b) => {
            const da = a.updated_at || a.created_at;
            const db = b.updated_at || b.created_at;
            return db.localeCompare(da);
        });
    }, [conversations, searchQuery]);

    /* ─── Load conversations ─────────────────────────────────────────────── */
    const loadConversations = useCallback(async () => {
        try {
            const data = await api.listConversations();
            setConversations(data || []);
        } catch (e: any) {
            setError(e?.message || 'Ошибка загрузки диалогов');
        }
    }, []);

    /* ─── Load brands from funnel filters ────────────────────────────────── */
    const loadBrands = useCallback(async () => {
        try {
            const filters = await api.getFunnelFilters();
            setBrands(filters?.brands || []);
        } catch { /* non-critical */ }
    }, []);

    /* ─── Init ───────────────────────────────────────────────────────────── */
    useEffect(() => {
        if (!api.isAuthenticated()) {
            window.location.href = '/login';
            return;
        }
        Promise.all([loadConversations(), loadBrands()])
            .finally(() => setLoading(false));
    }, [loadConversations, loadBrands]);

    /* ─── Load conversation messages ─────────────────────────────────────── */
    const openConversation = useCallback(async (id: number) => {
        setActiveId(id);
        setMessages([]);
        setStreamingContent('');
        setError(null);
        try {
            const data = await api.getConversation(id);
            setMessages(data.messages || []);
            setActiveBrand(data.conversation.brand);
        } catch (e: any) {
            setError(e?.message || 'Ошибка загрузки диалога');
        }
    }, []);

    /* ─── Create new conversation ────────────────────────────────────────── */
    const createNewChat = useCallback(async () => {
        try {
            const conv = await api.createConversation({ title: 'Новый диалог' });
            setConversations(prev => [conv, ...prev]);
            setActiveId(conv.id);
            setMessages([]);
            setStreamingContent('');
            setActiveBrand(conv.brand);
            setError(null);
        } catch (e: any) {
            setError(e?.message || 'Ошибка создания диалога');
        }
    }, []);

    /* ─── Delete conversation ────────────────────────────────────────────── */
    const deleteConversation = useCallback(async (id: number, e: React.MouseEvent) => {
        e.stopPropagation();
        if (!confirm('Удалить диалог?')) return;
        try {
            await api.deleteConversation(id);
            setConversations(prev => prev.filter(c => c.id !== id));
            if (activeId === id) {
                setActiveId(null);
                setMessages([]);
            }
        } catch (err: any) {
            setError(err?.message || 'Ошибка удаления');
        }
    }, [activeId]);

    /* ─── Update title ───────────────────────────────────────────────────── */
    const saveTitle = useCallback(async () => {
        if (!activeId || !titleDraft.trim()) { setEditingTitle(false); return; }
        try {
            const updated = await api.updateConversation(activeId, { title: titleDraft.trim() });
            setConversations(prev => prev.map(c => c.id === updated.id ? updated : c));
            setEditingTitle(false);
        } catch (e: any) {
            setError(e?.message || 'Ошибка обновления');
        }
    }, [activeId, titleDraft]);

    /* ─── Update brand ───────────────────────────────────────────────────── */
    const changeBrand = useCallback(async (brand: string | null) => {
        if (!activeId) return;
        setActiveBrand(brand);
        try {
            const updated = await api.updateConversation(activeId, { brand: brand || '' });
            setConversations(prev => prev.map(c => c.id === updated.id ? updated : c));
        } catch (e: any) {
            setError(e?.message || 'Ошибка обновления бренда');
        }
    }, [activeId]);

    /* ─── Send message (SSE streaming) ───────────────────────────────────── */
    const sendMessage = useCallback(async () => {
        if (!activeId || (!input.trim() && attachedFiles.length === 0) || isStreaming) return;

        const content = input.trim();
        const files = [...attachedFiles];
        setInput('');
        setAttachedFiles([]);

        // Optimistically add user message
        const userMsg: AiMessage = {
            id: Date.now(),
            conversation_id: activeId,
            role: 'user',
            content,
            files: files.length > 0 ? files : null,
            tokens_used: 0,
            tools_used: null,
            created_at: new Date().toISOString(),
        };
        setMessages(prev => [...prev, userMsg]);
        setIsStreaming(true);
        setStreamingContent('');

        try {
            const response = await api.sendMessageStream(activeId, content, files.length > 0 ? files : undefined);

            if (!response.body) {
                throw new Error('Нет потока ответа');
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let accumulated = '';
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });

                // Parse complete SSE events from buffer
                const parts = buffer.split('\n\n');
                buffer = parts.pop() || '';

                for (const part of parts) {
                    const events = parseSSE(part);
                    for (const event of events) {
                        if (event.type === 'token' && event.content) {
                            accumulated += event.content;
                            setStreamingContent(accumulated);
                        } else if (event.type === 'done' && event.message) {
                            // Replace streaming content with final message
                            setMessages(prev => [...prev, event.message!]);
                            setStreamingContent('');
                            accumulated = '';
                        } else if (event.type === 'error') {
                            setError(event.content || 'Ошибка генерации ответа');
                        }
                    }
                }
            }

            // If we still have accumulated content but no "done" event, add as message
            if (accumulated) {
                const fallbackMsg: AiMessage = {
                    id: Date.now() + 1,
                    conversation_id: activeId,
                    role: 'assistant',
                    content: accumulated,
                    files: null,
                    tokens_used: 0,
                    tools_used: null,
                    created_at: new Date().toISOString(),
                };
                setMessages(prev => [...prev, fallbackMsg]);
                setStreamingContent('');
            }

            // Update conversation in list (for updated_at)
            await loadConversations();
        } catch (e: any) {
            setError(e?.message || 'Ошибка отправки сообщения');
        } finally {
            setIsStreaming(false);
        }
    }, [activeId, input, attachedFiles, isStreaming, loadConversations]);

    /* ─── File upload ────────────────────────────────────────────────────── */
    const handleFileUpload = useCallback(async (files: FileList | null) => {
        if (!files) return;
        for (const file of Array.from(files)) {
            try {
                const result = await api.uploadAiFile(file);
                setAttachedFiles(prev => [...prev, {
                    name: result.name,
                    type: result.type as 'excel' | 'image',
                    size: result.size,
                    content: result.content,
                }]);
            } catch (e: any) {
                setError(e?.message || 'Ошибка загрузки файла');
            }
        }
        if (fileInputRef.current) fileInputRef.current.value = '';
    }, []);

    const removeFile = useCallback((index: number) => {
        setAttachedFiles(prev => prev.filter((_, i) => i !== index));
    }, []);

    /* ─── Drag & drop ────────────────────────────────────────────────────── */
    const [isDragging, setIsDragging] = useState(false);

    const handleDragOver = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(true);
    }, []);

    const handleDragLeave = useCallback(() => {
        setIsDragging(false);
    }, []);

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
        handleFileUpload(e.dataTransfer.files);
    }, [handleFileUpload]);

    /* ─── Auto-scroll ────────────────────────────────────────────────────── */
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages, streamingContent]);

    /* ─── Auto-resize textarea ───────────────────────────────────────────── */
    const handleTextareaInput = useCallback(() => {
        const el = textareaRef.current;
        if (!el) return;
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 200) + 'px';
    }, []);

    /* ─── Keyboard shortcuts ─────────────────────────────────────────────── */
    const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    }, [sendMessage]);

    /* ─── Format file size ───────────────────────────────────────────────── */
    const formatFileSize = (bytes: number) => {
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    };

    /* ─── Loading state ──────────────────────────────────────────────────── */
    if (loading) {
        return (
            <div className="animate-in" style={{ padding: 24 }}>
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', borderRadius: 20 }}>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 15 }}>Загрузка...</div>
                </div>
            </div>
        );
    }

    /* ─── Render ─────────────────────────────────────────────────────────── */
    return (
        <div className="animate-in" style={{ height: 'calc(100vh - 32px)', display: 'flex', flexDirection: 'column', padding: '0 16px 16px' }}>
            {/* Error banner */}
            {error && (
                <div style={{
                    background: 'rgba(255, 59, 48, 0.1)',
                    border: '1px solid rgba(255, 59, 48, 0.2)',
                    borderRadius: 12,
                    padding: '10px 16px',
                    marginBottom: 8,
                    color: 'var(--color-danger)',
                    fontSize: 14,
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                }}>
                    <span>{error}</span>
                    <button
                        onClick={() => setError(null)}
                        style={{ background: 'none', border: 'none', color: 'var(--color-danger)', cursor: 'pointer', fontSize: 18, padding: '0 4px' }}
                    >
                        &times;
                    </button>
                </div>
            )}

            <div style={{ flex: 1, display: 'flex', gap: 12, minHeight: 0, overflow: 'hidden' }}>
                {/* ─── Sidebar ────────────────────────────────────────────── */}
                {sidebarOpen && (
                    <div style={{
                        width: 280,
                        minWidth: 280,
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 8,
                    }}>
                        {/* New chat button */}
                        <button
                            className="btn btn-primary"
                            onClick={createNewChat}
                            style={{ width: '100%', borderRadius: 12, gap: 8, fontWeight: 600 }}
                        >
                            <span style={{ fontSize: 18 }}>+</span>
                            Новый диалог
                        </button>

                        {/* Search */}
                        <input
                            type="text"
                            className="form-input"
                            placeholder="Поиск диалогов..."
                            value={searchQuery}
                            onChange={e => setSearchQuery(e.target.value)}
                            style={{ fontSize: 13, padding: '8px 12px', borderRadius: 10 }}
                        />

                        {/* Conversation list */}
                        <div style={{
                            flex: 1,
                            overflowY: 'auto',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: 2,
                        }}>
                            {filteredConversations.length === 0 && (
                                <div style={{ textAlign: 'center', padding: 24, color: 'var(--color-text-dim)', fontSize: 13 }}>
                                    {searchQuery ? 'Ничего не найдено' : 'Нет диалогов'}
                                </div>
                            )}
                            {filteredConversations.map(conv => (
                                <div
                                    key={conv.id}
                                    onClick={() => openConversation(conv.id)}
                                    style={{
                                        padding: '10px 12px',
                                        borderRadius: 10,
                                        cursor: 'pointer',
                                        background: conv.id === activeId
                                            ? 'rgba(0, 113, 227, 0.1)'
                                            : 'transparent',
                                        border: conv.id === activeId
                                            ? '1px solid rgba(0, 113, 227, 0.2)'
                                            : '1px solid transparent',
                                        transition: 'all 0.15s ease',
                                        display: 'flex',
                                        flexDirection: 'column',
                                        gap: 4,
                                        position: 'relative',
                                    }}
                                    onMouseEnter={e => {
                                        if (conv.id !== activeId) {
                                            (e.currentTarget as HTMLDivElement).style.background = 'var(--color-bg-hover)';
                                        }
                                    }}
                                    onMouseLeave={e => {
                                        if (conv.id !== activeId) {
                                            (e.currentTarget as HTMLDivElement).style.background = 'transparent';
                                        }
                                    }}
                                >
                                    <div style={{
                                        fontSize: 14,
                                        fontWeight: 500,
                                        overflow: 'hidden',
                                        textOverflow: 'ellipsis',
                                        whiteSpace: 'nowrap',
                                        paddingRight: 24,
                                    }}>
                                        {conv.title}
                                    </div>
                                    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                                        <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>
                                            {formatDate(conv.updated_at || conv.created_at)}
                                        </span>
                                        {conv.brand && (
                                            <span className="badge badge-info" style={{ fontSize: 10, padding: '1px 6px' }}>
                                                {conv.brand}
                                            </span>
                                        )}
                                    </div>
                                    {/* Delete button */}
                                    <button
                                        onClick={(e) => deleteConversation(conv.id, e)}
                                        style={{
                                            position: 'absolute',
                                            top: 8,
                                            right: 8,
                                            background: 'none',
                                            border: 'none',
                                            cursor: 'pointer',
                                            color: 'var(--color-text-dim)',
                                            fontSize: 14,
                                            padding: '2px 4px',
                                            borderRadius: 4,
                                            opacity: 0.5,
                                            transition: 'opacity 0.15s',
                                        }}
                                        onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.opacity = '1'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--color-danger)'; }}
                                        onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.opacity = '0.5'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--color-text-dim)'; }}
                                        title="Удалить диалог"
                                    >
                                        &#128465;
                                    </button>
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                {/* ─── Main chat area ────────────────────────────────────── */}
                <div style={{
                    flex: 1,
                    display: 'flex',
                    flexDirection: 'column',
                    minWidth: 0,
                    background: 'var(--color-bg-card)',
                    backdropFilter: 'blur(24px) saturate(180%)',
                    WebkitBackdropFilter: 'blur(24px) saturate(180%)',
                    border: '1px solid var(--color-border)',
                    borderRadius: 20,
                    boxShadow: 'inset 0 1px 0 0 var(--color-border-inner), var(--shadow-glass)',
                    overflow: 'hidden',
                }}>
                    {!activeId ? (
                        /* ─── Empty state ────────────────────────────────────── */
                        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                            <div className="empty-state">
                                <div className="empty-state-icon">&#129302;</div>
                                <div className="empty-state-text">AI-ассистент</div>
                                <p style={{ color: 'var(--color-text-dim)', marginTop: 8, fontSize: 14 }}>
                                    Задайте вопрос о вашем бизнесе на Wildberries
                                </p>
                                <button
                                    className="btn btn-primary"
                                    onClick={createNewChat}
                                    style={{ marginTop: 20 }}
                                >
                                    <span style={{ fontSize: 18 }}>+</span>
                                    Начать диалог
                                </button>
                            </div>
                        </div>
                    ) : (
                        <>
                            {/* ─── Top bar ───────────────────────────────────── */}
                            <div style={{
                                padding: '12px 16px',
                                borderBottom: '1px solid var(--color-border)',
                                display: 'flex',
                                alignItems: 'center',
                                gap: 12,
                                flexShrink: 0,
                            }}>
                                {/* Toggle sidebar button (mobile) */}
                                <button
                                    onClick={() => setSidebarOpen(!sidebarOpen)}
                                    style={{
                                        background: 'none',
                                        border: 'none',
                                        cursor: 'pointer',
                                        fontSize: 18,
                                        color: 'var(--color-text-muted)',
                                        padding: '4px 6px',
                                        borderRadius: 6,
                                    }}
                                    title={sidebarOpen ? 'Скрыть список' : 'Показать список'}
                                >
                                    &#9776;
                                </button>

                                {/* Title (editable) */}
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    {editingTitle ? (
                                        <input
                                            className="form-input"
                                            value={titleDraft}
                                            onChange={e => setTitleDraft(e.target.value)}
                                            onBlur={saveTitle}
                                            onKeyDown={e => { if (e.key === 'Enter') saveTitle(); if (e.key === 'Escape') setEditingTitle(false); }}
                                            autoFocus
                                            style={{ fontSize: 15, fontWeight: 600, padding: '4px 8px', borderRadius: 8 }}
                                        />
                                    ) : (
                                        <div
                                            onClick={() => { setTitleDraft(activeConversation?.title || ''); setEditingTitle(true); }}
                                            style={{
                                                fontSize: 15,
                                                fontWeight: 600,
                                                cursor: 'pointer',
                                                overflow: 'hidden',
                                                textOverflow: 'ellipsis',
                                                whiteSpace: 'nowrap',
                                                padding: '4px 0',
                                            }}
                                            title="Нажмите для редактирования"
                                        >
                                            {activeConversation?.title || 'Диалог'}
                                        </div>
                                    )}
                                </div>

                                {/* Brand selector */}
                                <select
                                    value={activeBrand || ''}
                                    onChange={e => changeBrand(e.target.value || null)}
                                    style={{
                                        background: 'var(--color-bg-input)',
                                        border: '1px solid var(--color-border)',
                                        borderRadius: 8,
                                        padding: '6px 10px',
                                        fontSize: 13,
                                        color: 'var(--color-text)',
                                        cursor: 'pointer',
                                        maxWidth: 180,
                                    }}
                                >
                                    <option value="">Весь проект</option>
                                    {brands.map(b => (
                                        <option key={b} value={b}>{b}</option>
                                    ))}
                                </select>

                                {/* Model badge */}
                                <span className="badge badge-secondary" style={{ fontSize: 11, flexShrink: 0 }}>
                                    Claude
                                </span>
                            </div>

                            {/* ─── Messages area ─────────────────────────────── */}
                            <div
                                style={{
                                    flex: 1,
                                    overflowY: 'auto',
                                    padding: 16,
                                    display: 'flex',
                                    flexDirection: 'column',
                                    gap: 12,
                                }}
                                onDragOver={handleDragOver}
                                onDragLeave={handleDragLeave}
                                onDrop={handleDrop}
                            >
                                {/* Drag overlay */}
                                {isDragging && (
                                    <div style={{
                                        position: 'absolute',
                                        inset: 0,
                                        background: 'rgba(0, 113, 227, 0.08)',
                                        border: '2px dashed var(--color-accent)',
                                        borderRadius: 16,
                                        display: 'flex',
                                        alignItems: 'center',
                                        justifyContent: 'center',
                                        zIndex: 10,
                                        fontSize: 16,
                                        color: 'var(--color-accent)',
                                        fontWeight: 500,
                                    }}>
                                        Перетащите файлы сюда
                                    </div>
                                )}

                                {messages.length === 0 && !streamingContent && (
                                    <div style={{
                                        flex: 1,
                                        display: 'flex',
                                        alignItems: 'center',
                                        justifyContent: 'center',
                                        color: 'var(--color-text-dim)',
                                        fontSize: 14,
                                    }}>
                                        Начните диалог — напишите сообщение ниже
                                    </div>
                                )}

                                {messages.map(msg => (
                                    <div
                                        key={msg.id}
                                        style={{
                                            display: 'flex',
                                            justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                                            animation: 'fadeIn 0.2s ease-out',
                                        }}
                                    >
                                        <div style={{
                                            maxWidth: '75%',
                                            padding: '10px 14px',
                                            borderRadius: msg.role === 'user' ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
                                            background: msg.role === 'user'
                                                ? 'rgba(0, 113, 227, 0.12)'
                                                : 'rgba(0, 0, 0, 0.03)',
                                            border: `1px solid ${msg.role === 'user' ? 'rgba(0, 113, 227, 0.15)' : 'var(--color-border)'}`,
                                            fontSize: 14,
                                            lineHeight: 1.55,
                                            wordBreak: 'break-word',
                                        }}>
                                            {/* File chips */}
                                            {msg.files && msg.files.length > 0 && (
                                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                                                    {msg.files.map((f, i) => (
                                                        <span key={i} className="badge badge-info" style={{ fontSize: 11, gap: 4 }}>
                                                            {f.type === 'excel' ? '\uD83D\uDCC4' : '\uD83D\uDDBC\uFE0F'} {f.name}
                                                        </span>
                                                    ))}
                                                </div>
                                            )}
                                            {/* Content */}
                                            {msg.role === 'assistant' ? renderMessageContent(msg.content) : msg.content}
                                            {/* Tools used */}
                                            {msg.tools_used && msg.tools_used.length > 0 && (
                                                <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                                                    {msg.tools_used.map((t, i) => (
                                                        <span key={i} className="badge badge-secondary" style={{ fontSize: 10, padding: '1px 6px' }}>
                                                            {t}
                                                        </span>
                                                    ))}
                                                </div>
                                            )}
                                            {/* Timestamp */}
                                            <div style={{ marginTop: 4, fontSize: 11, color: 'var(--color-text-dim)', textAlign: msg.role === 'user' ? 'right' : 'left' }}>
                                                {new Date(msg.created_at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}
                                            </div>
                                        </div>
                                    </div>
                                ))}

                                {/* Streaming message */}
                                {streamingContent && (
                                    <div style={{ display: 'flex', justifyContent: 'flex-start', animation: 'fadeIn 0.2s ease-out' }}>
                                        <div style={{
                                            maxWidth: '75%',
                                            padding: '10px 14px',
                                            borderRadius: '16px 16px 16px 4px',
                                            background: 'rgba(0, 0, 0, 0.03)',
                                            border: '1px solid var(--color-border)',
                                            fontSize: 14,
                                            lineHeight: 1.55,
                                            wordBreak: 'break-word',
                                        }}>
                                            {renderMessageContent(streamingContent)}
                                            <span style={{ display: 'inline-block', width: 6, height: 14, background: 'var(--color-accent)', borderRadius: 1, marginLeft: 2, animation: 'blink 1s step-end infinite' }} />
                                        </div>
                                    </div>
                                )}

                                {/* Typing indicator */}
                                {isStreaming && !streamingContent && (
                                    <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
                                        <div style={{
                                            padding: '12px 18px',
                                            borderRadius: '16px 16px 16px 4px',
                                            background: 'rgba(0, 0, 0, 0.03)',
                                            border: '1px solid var(--color-border)',
                                            display: 'flex',
                                            gap: 4,
                                            alignItems: 'center',
                                        }}>
                                            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--color-text-dim)', animation: 'typingDot 1.4s infinite', animationDelay: '0s' }} />
                                            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--color-text-dim)', animation: 'typingDot 1.4s infinite', animationDelay: '0.2s' }} />
                                            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--color-text-dim)', animation: 'typingDot 1.4s infinite', animationDelay: '0.4s' }} />
                                        </div>
                                    </div>
                                )}

                                <div ref={messagesEndRef} />
                            </div>

                            {/* ─── Input area ────────────────────────────────── */}
                            <div style={{
                                borderTop: '1px solid var(--color-border)',
                                padding: 12,
                                flexShrink: 0,
                            }}>
                                {/* Attached files preview */}
                                {attachedFiles.length > 0 && (
                                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                                        {attachedFiles.map((f, i) => (
                                            <span
                                                key={i}
                                                className="badge badge-info"
                                                style={{ fontSize: 12, gap: 6, cursor: 'default' }}
                                            >
                                                {f.type === 'excel' ? '\uD83D\uDCC4' : '\uD83D\uDDBC\uFE0F'} {f.name}
                                                <span style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>
                                                    ({formatFileSize(f.size)})
                                                </span>
                                                <button
                                                    onClick={() => removeFile(i)}
                                                    style={{
                                                        background: 'none',
                                                        border: 'none',
                                                        cursor: 'pointer',
                                                        color: 'var(--color-text-muted)',
                                                        fontSize: 14,
                                                        padding: 0,
                                                        lineHeight: 1,
                                                    }}
                                                >
                                                    &times;
                                                </button>
                                            </span>
                                        ))}
                                    </div>
                                )}

                                {/* Input row */}
                                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
                                    {/* Attach button */}
                                    <button
                                        onClick={() => fileInputRef.current?.click()}
                                        style={{
                                            background: 'none',
                                            border: '1px solid var(--color-border)',
                                            borderRadius: 10,
                                            padding: '8px 10px',
                                            cursor: 'pointer',
                                            color: 'var(--color-text-muted)',
                                            fontSize: 18,
                                            flexShrink: 0,
                                            transition: 'all 0.15s',
                                        }}
                                        title="Прикрепить файл"
                                    >
                                        &#128206;
                                    </button>
                                    <input
                                        ref={fileInputRef}
                                        type="file"
                                        accept=".xlsx,.xls,.csv,.png,.jpg,.jpeg,.gif,.webp"
                                        multiple
                                        style={{ display: 'none' }}
                                        onChange={e => handleFileUpload(e.target.files)}
                                    />

                                    {/* Textarea */}
                                    <textarea
                                        ref={textareaRef}
                                        value={input}
                                        onChange={e => { setInput(e.target.value); handleTextareaInput(); }}
                                        onKeyDown={handleKeyDown}
                                        placeholder="Введите сообщение... (Enter для отправки, Shift+Enter для новой строки)"
                                        rows={1}
                                        style={{
                                            flex: 1,
                                            background: 'var(--color-bg-input)',
                                            border: '1px solid var(--color-border)',
                                            borderRadius: 12,
                                            padding: '10px 14px',
                                            fontSize: 14,
                                            lineHeight: 1.5,
                                            resize: 'none',
                                            fontFamily: 'inherit',
                                            color: 'var(--color-text)',
                                            outline: 'none',
                                            maxHeight: 200,
                                            overflow: 'auto',
                                            transition: 'border-color 0.15s',
                                        }}
                                        onFocus={e => { (e.currentTarget as HTMLTextAreaElement).style.borderColor = 'var(--color-border-focus)'; }}
                                        onBlur={e => { (e.currentTarget as HTMLTextAreaElement).style.borderColor = 'var(--color-border)'; }}
                                    />

                                    {/* Send button */}
                                    <button
                                        onClick={sendMessage}
                                        disabled={isStreaming || (!input.trim() && attachedFiles.length === 0)}
                                        className="btn btn-primary"
                                        style={{
                                            borderRadius: 10,
                                            padding: '8px 14px',
                                            flexShrink: 0,
                                            opacity: (isStreaming || (!input.trim() && attachedFiles.length === 0)) ? 0.5 : 1,
                                            cursor: (isStreaming || (!input.trim() && attachedFiles.length === 0)) ? 'not-allowed' : 'pointer',
                                        }}
                                    >
                                        {isStreaming ? (
                                            <span style={{ display: 'inline-block', width: 18, height: 18, border: '2px solid rgba(255,255,255,0.3)', borderTopColor: '#fff', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
                                        ) : (
                                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                                <line x1="22" y1="2" x2="11" y2="13" />
                                                <polygon points="22 2 15 22 11 13 2 9 22 2" />
                                            </svg>
                                        )}
                                    </button>
                                </div>
                            </div>
                        </>
                    )}
                </div>
            </div>

            {/* ─── CSS animations ────────────────────────────────────────── */}
            <style>{`
                @keyframes typingDot {
                    0%, 60%, 100% { opacity: 0.3; transform: translateY(0); }
                    30% { opacity: 1; transform: translateY(-4px); }
                }
                @keyframes blink {
                    0%, 100% { opacity: 1; }
                    50% { opacity: 0; }
                }
                @keyframes spin {
                    to { transform: rotate(360deg); }
                }
            `}</style>
        </div>
    );
}
