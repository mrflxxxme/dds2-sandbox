'use client';
import { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import type { TelegramChatBinding } from '@/types/api';

export function TelegramBot() {
    const [chats, setChats] = useState<TelegramChatBinding[]>([]);
    const [loading, setLoading] = useState(true);
    const [deepLink, setDeepLink] = useState('');
    const [msg, setMsg] = useState('');
    const [linking, setLinking] = useState(false);

    const loadChats = useCallback(async () => {
        try {
            const data = await api.getTelegramChats();
            setChats(data);
        } catch { /* ignore */ }
        setLoading(false);
    }, []);

    useEffect(() => { loadChats(); }, [loadChats]);

    const handleLink = async () => {
        setLinking(true);
        try {
            const res = await api.getTelegramLink();
            setDeepLink(res.deep_link_url);
        } catch (e: any) {
            setMsg(e.message || 'Ошибка получения ссылки');
        }
        setLinking(false);
    };

    const handleDelete = async (id: number) => {
        if (!confirm('Отвязать чат?')) return;
        try {
            await api.deleteTelegramChat(id);
            loadChats();
        } catch (e: any) {
            setMsg(e.message);
        }
    };

    const handleToggle = async (id: number, current: boolean) => {
        try {
            await api.toggleTelegramNotify(id, !current);
            setChats(prev => prev.map(c => c.id === id ? { ...c, notify_enabled: !current } : c));
        } catch (e: any) {
            setMsg(e.message);
        }
    };

    if (loading) return <div style={{ padding: 40, color: 'var(--color-text-muted)' }}>Загрузка...</div>;

    return (
        <>
            {msg && (
                <div className="auth-error" style={{ marginBottom: 16 }}>
                    {msg}
                    <span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setMsg('')}>✕</span>
                </div>
            )}

            <div className="glass-card" style={{ marginBottom: 24 }}>
                <div className="table-toolbar">
                    <h3 style={{ fontSize: 16, fontWeight: 600 }}>🤖 Telegram-бот</h3>
                    <button className="btn btn-primary btn-sm" onClick={handleLink} disabled={linking}>
                        {linking ? '⏳' : '🔗'} Привязать Telegram
                    </button>
                </div>

                <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 16 }}>
                    Привяжите аккаунт Telegram через бота <strong>@dds_analytics_bot</strong>.
                    После привязки добавьте бота в групповой чат и выполните команду <code>/setup</code> для привязки чата к проекту.
                </p>

                {deepLink && (
                    <div style={{
                        background: 'var(--color-bg-input)',
                        border: '1px solid var(--color-border)',
                        borderRadius: 10,
                        padding: 16,
                        marginBottom: 16,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 12,
                        flexWrap: 'wrap',
                    }}>
                        <span style={{ fontSize: 13 }}>Откройте ссылку для привязки:</span>
                        <a href={deepLink} target="_blank" rel="noopener noreferrer" className="btn btn-primary btn-sm">
                            Открыть бота в Telegram
                        </a>
                        <span style={{ fontSize: 12, color: 'var(--color-text-dim)', fontFamily: 'monospace', wordBreak: 'break-all' }}>
                            {deepLink}
                        </span>
                    </div>
                )}

                {chats.length === 0 ? (
                    <div className="empty-state">
                        <div className="empty-state-icon">💬</div>
                        <div className="empty-state-text">Нет привязанных чатов</div>
                    </div>
                ) : (
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Chat ID</th>
                                <th>Бренд</th>
                                <th>Уведомления</th>
                                <th style={{ width: 100 }}></th>
                            </tr>
                        </thead>
                        <tbody>
                            {chats.map(c => (
                                <tr key={c.id}>
                                    <td style={{ fontFamily: 'monospace', fontSize: 13 }}>{c.chat_id}</td>
                                    <td>{c.brand || <span style={{ color: 'var(--color-text-dim)' }}>—</span>}</td>
                                    <td>
                                        <button
                                            className={`btn btn-sm ${c.notify_enabled ? 'btn-success' : 'btn-secondary'}`}
                                            onClick={() => handleToggle(c.id, c.notify_enabled)}
                                        >
                                            {c.notify_enabled ? '🔔 Вкл' : '🔕 Выкл'}
                                        </button>
                                    </td>
                                    <td>
                                        <button className="btn btn-danger btn-sm" onClick={() => handleDelete(c.id)}>✕</button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>
        </>
    );
}
