'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { api } from '@/lib/api';
import { haptic } from '@/lib/telegram';
import { sanitizeAIHtml } from '@/lib/sanitize';

/**
 * TMA AI Chat page — chat with AI assistant about project financials.
 *
 * XSS-safe rendering: AI responses are passed through DOMPurify
 * (`sanitizeAIHtml`) which strips `<script>`, event handlers (`onerror` etc.),
 * `javascript:`/unsafe `data:` URLs, and any tag/attr not on the strict
 * allowlist. User-provided `msg.content` for role='user' is rendered as plain
 * text (React auto-escaping), never through `dangerouslySetInnerHTML`.
 */

interface ChatMessage {
    role: 'user' | 'assistant';
    content: string;
}

const SUGGESTIONS = [
    'Какая прибыль за этот месяц?',
    'Топ расходов за неделю',
    'Сравни доходы с прошлым месяцем',
    'Какие товары самые прибыльные?',
];

/**
 * Pre-process AI response and sanitize via DOMPurify.
 *
 *  1. Strip synthesizer artefacts (`<!-- ... -->` hidden insights, `[INSIGHT]` lines).
 *  2. Apply markdown-lite replacements (`**bold**`, `*italic*`, `` `code` ``)
 *     BEFORE sanitization so they become real HTML tags that DOMPurify can
 *     whitelist.
 *  3. Replace newlines with `<br>` for chat-bubble line breaks.
 *  4. Run the full string through `sanitizeAIHtml` — strips scripts, event
 *     handlers, unsafe URLs, and anything outside the allowlist.
 */
function renderMessageSafe(text: string): string {
    // 1. Drop hidden synthesizer blocks / INSIGHT lines (not for user eyes).
    const cleaned = text
        .replace(/<!--[\s\S]*?-->/g, '')
        .replace(/^\[INSIGHT\].*$/gm, '')
        .replace(/\n{3,}/g, '\n\n')
        .trim();

    // 2. Markdown-lite → HTML (DOMPurify will keep the tags, strip anything
    //    malicious inside them).
    let withMarkdown = cleaned
        .replace(/\*\*(.*?)\*\*/g, '<b>$1</b>')
        .replace(/\*(.*?)\*/g, '<i>$1</i>')
        .replace(/`([^`]+)`/g, '<code>$1</code>');

    // 3. Line breaks → <br> (TMA chat bubbles are compact, no paragraph tags).
    withMarkdown = withMarkdown.replace(/\n/g, '<br>');

    // 4. Full DOMPurify sanitation — last line of defence.
    return sanitizeAIHtml(withMarkdown);
}

export default function TmaChatPage() {
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [input, setInput] = useState('');
    const [sending, setSending] = useState(false);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const scrollToBottom = useCallback(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, []);

    useEffect(() => {
        scrollToBottom();
    }, [messages, scrollToBottom]);

    // Auto-resize textarea
    const handleInputChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
        setInput(e.target.value);
        const el = e.target;
        el.style.height = 'auto';
        el.style.height = Math.min(el.scrollHeight, 120) + 'px';
    }, []);

    const sendMessage = useCallback(async (text: string) => {
        const trimmed = text.trim();
        if (!trimmed || sending) return;

        haptic('light');
        const userMsg: ChatMessage = { role: 'user', content: trimmed };
        setMessages(prev => [...prev, userMsg]);
        setInput('');
        setSending(true);

        // Reset textarea height
        if (textareaRef.current) {
            textareaRef.current.style.height = 'auto';
        }

        try {
            const projectId = api.getProjectId();
            const response = await api.request<{ answer: string }>(
                'POST',
                '/api/v1/tma/chat',
                { project_id: projectId, message: trimmed }
            );

            const assistantMsg: ChatMessage = {
                role: 'assistant',
                content: response.answer || 'Нет ответа',
            };
            setMessages(prev => [...prev, assistantMsg]);
            haptic('success');
        } catch (err) {
            const errorMsg: ChatMessage = {
                role: 'assistant',
                content: `Ошибка: ${err instanceof Error ? err.message : 'Не удалось получить ответ'}`,
            };
            setMessages(prev => [...prev, errorMsg]);
            haptic('error');
        } finally {
            setSending(false);
        }
    }, [sending]);

    const handleSubmit = useCallback((e: React.FormEvent) => {
        e.preventDefault();
        sendMessage(input);
    }, [input, sendMessage]);

    const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage(input);
        }
    }, [input, sendMessage]);

    const handleSuggestion = useCallback((text: string) => {
        haptic('selection');
        sendMessage(text);
    }, [sendMessage]);

    return (
        <div className="tma-chat-container">
            {/* Messages */}
            <div className="tma-chat-messages">
                {messages.length === 0 && (
                    <div className="tma-empty" style={{ padding: '40px 0' }}>
                        <div className="tma-empty-icon">🤖</div>
                        <div className="tma-empty-text" style={{ fontSize: 14, color: 'var(--tma-hint)' }}>
                            Задайте вопрос о финансах проекта
                        </div>
                    </div>
                )}

                {messages.map((msg, i) => (
                    <div
                        key={i}
                        className={`tma-chat-bubble ${msg.role === 'user' ? 'tma-chat-user' : 'tma-chat-assistant'}`}
                        dangerouslySetInnerHTML={
                            msg.role === 'assistant'
                                ? { __html: renderMessageSafe(msg.content) }
                                : undefined
                        }
                    >
                        {msg.role === 'user' ? msg.content : undefined}
                    </div>
                ))}

                {sending && (
                    <div className="tma-chat-bubble tma-chat-assistant">
                        <div className="tma-spinner tma-spinner-sm" />
                    </div>
                )}

                <div ref={messagesEndRef} />
            </div>

            {/* Suggestions (show only when no messages) */}
            {messages.length === 0 && (
                <div className="tma-chat-suggestions">
                    {SUGGESTIONS.map((s, i) => (
                        <button
                            key={i}
                            className="tma-chat-suggestion"
                            onClick={() => handleSuggestion(s)}
                        >
                            {s}
                        </button>
                    ))}
                </div>
            )}

            {/* Input Area */}
            <form className="tma-chat-input-area" onSubmit={handleSubmit}>
                <textarea
                    ref={textareaRef}
                    className="tma-chat-textarea"
                    placeholder="Введите сообщение..."
                    value={input}
                    onChange={handleInputChange}
                    onKeyDown={handleKeyDown}
                    rows={1}
                    disabled={sending}
                />
                <button
                    type="submit"
                    className="tma-chat-send"
                    disabled={!input.trim() || sending}
                    aria-label="Отправить"
                >
                    ↑
                </button>
            </form>
        </div>
    );
}
