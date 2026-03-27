'use client';
import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { haptic } from '@/lib/telegram';

interface ChatMessage {
    role: 'user' | 'assistant';
    content: string;
}

const SUGGESTIONS = [
    'Как дела с продажами?',
    'Покажи БДР за неделю',
    'Какие товары сливают бюджет?',
    'Остатки на складе',
];

export default function TmaChatPage() {
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [input, setInput] = useState('');
    const [sending, setSending] = useState(false);
    const messagesRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);

    useEffect(() => {
        scrollToBottom();
    }, [messages]);

    function scrollToBottom() {
        if (messagesRef.current) {
            messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
        }
    }

    async function sendMessage(text?: string) {
        const msg = (text || input).trim();
        if (!msg || sending) return;

        haptic('light');
        setInput('');
        setMessages(prev => [...prev, { role: 'user', content: msg }]);
        setSending(true);

        try {
            const projectId = api.getProjectId();
            if (!projectId) throw new Error('Проект не выбран');

            const res = await api.request<{ reply: string }>(
                'POST', '/api/v1/tma/chat',
                { project_id: projectId, message: msg },
            );

            setMessages(prev => [...prev, { role: 'assistant', content: res.reply }]);
            haptic('success');
        } catch (e: any) {
            setMessages(prev => [...prev, {
                role: 'assistant',
                content: `Ошибка: ${e.message || 'не удалось получить ответ'}`,
            }]);
            haptic('error');
        }
        setSending(false);
    }

    function handleKeyDown(e: React.KeyboardEvent) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    }

    // Convert Telegram HTML to safe renderable content
    function renderHtml(html: string) {
        // The AI returns Telegram HTML (<b>, <i>, <blockquote>)
        // Basic sanitization: only allow safe tags
        const clean = html
            .replace(/<(?!\/?(b|i|strong|em|blockquote|br|code|pre)\b)[^>]*>/gi, '')
            .replace(/\n/g, '<br/>');
        return { __html: clean };
    }

    return (
        <div className="tma-chat">
            <div className="tma-chat-messages" ref={messagesRef}>
                {messages.length === 0 && (
                    <div style={{
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        justifyContent: 'center',
                        flex: 1,
                        gap: 16,
                        padding: '20px 0',
                    }}>
                        <div style={{ fontSize: 48 }}>🤖</div>
                        <div style={{
                            fontSize: 15,
                            color: 'var(--tg-theme-hint-color, #86868b)',
                            textAlign: 'center',
                            lineHeight: 1.5,
                        }}>
                            AI-аналитик по вашим данным WB.<br/>
                            Задайте вопрос или выберите:
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, width: '100%', maxWidth: 280 }}>
                            {SUGGESTIONS.map((s, i) => (
                                <button
                                    key={i}
                                    className="tma-btn tma-btn-secondary"
                                    style={{ fontSize: 13, padding: '10px 16px' }}
                                    onClick={() => sendMessage(s)}
                                >
                                    {s}
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                {messages.map((msg, i) => (
                    <div key={i} className={`tma-chat-bubble ${msg.role}`}>
                        {msg.role === 'assistant' ? (
                            <div dangerouslySetInnerHTML={renderHtml(msg.content)} />
                        ) : (
                            msg.content
                        )}
                    </div>
                ))}

                {sending && (
                    <div className="tma-chat-bubble assistant">
                        <div className="tma-typing">
                            <div className="tma-typing-dot" />
                            <div className="tma-typing-dot" />
                            <div className="tma-typing-dot" />
                        </div>
                    </div>
                )}
            </div>

            <div className="tma-chat-input-area">
                <textarea
                    ref={inputRef}
                    className="tma-chat-input"
                    placeholder="Спросите что-нибудь..."
                    value={input}
                    onChange={e => setInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    rows={1}
                    disabled={sending}
                />
                <button
                    className="tma-chat-send"
                    onClick={() => sendMessage()}
                    disabled={!input.trim() || sending}
                >
                    ↑
                </button>
            </div>
        </div>
    );
}
