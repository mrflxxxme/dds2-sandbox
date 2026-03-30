'use client';
import { useEffect, useState, useCallback, useMemo } from 'react';
import { api } from '@/lib/api';
import type { TelegramChatBinding } from '@/types/api';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';

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

    const columns: Column[] = useMemo(() => [
        {
            key: 'chat_id', label: 'Chat ID',
            render: (v: any) => <span style={{ fontFamily: 'monospace', fontSize: 13 }}>{v}</span>,
        },
        {
            key: 'brand', label: 'Бренд',
            render: (v: any) => v || <span style={{ color: 'var(--color-text-dim)' }}>—</span>,
        },
        {
            key: 'notify_enabled', label: 'Уведомления',
            render: (_v: any, row: any) => (
                <button
                    className={`btn btn-sm ${row.notify_enabled ? 'btn-success' : 'btn-secondary'}`}
                    onClick={() => handleToggle(row.id, row.notify_enabled)}
                >
                    {row.notify_enabled ? '🔔 Вкл' : '🔕 Выкл'}
                </button>
            ),
        },
        {
            key: '_actions', label: '', width: '100',
            sortable: false,
            render: (_v: any, row: any) => (
                <button className="btn btn-danger btn-sm" onClick={() => handleDelete(row.id)}>✕</button>
            ),
        },
    ], []);

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
                    <TanStackDataTable
                        columns={columns}
                        data={chats}
                        enableSorting
                        enablePagination={false}
                    />
                )}
            </div>
        </>
    );
}
