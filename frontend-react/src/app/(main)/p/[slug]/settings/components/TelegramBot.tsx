'use client';
import { useEffect, useState, useCallback, useMemo } from 'react';
import { api } from '@/lib/api';
import type { TelegramChatBinding, Warehouse } from '@/types/api';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';

export function TelegramBot() {
    const [chats, setChats] = useState<TelegramChatBinding[]>([]);
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [loading, setLoading] = useState(true);
    const [deepLink, setDeepLink] = useState('');
    const [msg, setMsg] = useState('');
    const [linking, setLinking] = useState(false);

    const loadChats = useCallback(async () => {
        try {
            const [chatData, whData] = await Promise.all([api.getTelegramChats(), api.getWarehouses()]);
            setChats(chatData);
            // Только FF-склады: сборки бывают только на них, иначе табло молча пустует.
            setWarehouses(whData.filter(w => w.is_active && w.warehouse_type === 'FULFILLMENT'));
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

    const handleToggleFf = async (id: number, current: boolean) => {
        try {
            await api.toggleTelegramFfNotify(id, !current);
            setChats(prev => prev.map(c => c.id === id ? { ...c, ff_notify_enabled: !current } : c));
        } catch (e: any) {
            setMsg(e.message);
        }
    };

    const handleToggleMeasurements = async (id: number, current: boolean) => {
        try {
            await api.toggleTelegramMeasurementsNotify(id, !current);
            setChats(prev => prev.map(c => c.id === id ? { ...c, measurements_notify_enabled: !current } : c));
        } catch (e: any) {
            setMsg(e.message);
        }
    };

    const handleSetBoard = async (id: number, enabled: boolean, warehouseId: number | null) => {
        try {
            await api.setTelegramFfBoard(id, enabled, warehouseId);
            setChats(prev => prev.map(c => c.id === id
                ? { ...c, ff_board_enabled: enabled, ff_board_warehouse_id: enabled ? warehouseId : null }
                : c));
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
            key: 'ff_notify_enabled', label: 'Уведомления ФФ',
            render: (_v: any, row: any) => (
                <button
                    className={`btn btn-sm ${row.ff_notify_enabled ? 'btn-success' : 'btn-secondary'}`}
                    onClick={() => handleToggleFf(row.id, row.ff_notify_enabled)}
                    title="о готовности сборки и принятой приёмке"
                >
                    {row.ff_notify_enabled ? '📦 Вкл' : '📦 Выкл'}
                </button>
            ),
        },
        {
            key: 'measurements_notify_enabled', label: 'Замеры',
            render: (_v: any, row: any) => (
                <button
                    className={`btn btn-sm ${row.measurements_notify_enabled ? 'btn-success' : 'btn-secondary'}`}
                    onClick={() => handleToggleMeasurements(row.id, row.measurements_notify_enabled)}
                    title="Ежедневная сводка замеров WB в 09:00 МСК (за вчера + сегодня)"
                >
                    {row.measurements_notify_enabled ? '📐 Вкл' : '📐 Выкл'}
                </button>
            ),
        },
        {
            key: '_ff_board', label: 'Табло ФФ', sortable: false,
            render: (_v: any, row: any) => (
                <select
                    style={{
                        fontSize: 13, padding: '6px 8px', borderRadius: 8,
                        background: 'var(--color-bg-input)', color: 'var(--color-text)',
                        border: '1px solid var(--color-border)', maxWidth: 180,
                    }}
                    value={row.ff_board_enabled ? String(row.ff_board_warehouse_id ?? 'all') : 'off'}
                    onChange={(e) => {
                        const v = e.target.value;
                        if (v === 'off') handleSetBoard(row.id, false, null);
                        else if (v === 'all') handleSetBoard(row.id, true, null);
                        else handleSetBoard(row.id, true, Number(v));
                    }}
                    title="Закреплённое авто-табло заявок ФФ в этом чате"
                >
                    <option value="off">Выключено</option>
                    <option value="all">🌐 Все склады</option>
                    {warehouses.map(w => (
                        <option key={w.id} value={String(w.id)}>{w.name}</option>
                    ))}
                    {row.ff_board_enabled && row.ff_board_warehouse_id != null
                        && !warehouses.some(w => w.id === row.ff_board_warehouse_id) && (
                        <option value={String(row.ff_board_warehouse_id)}>
                            Склад #{row.ff_board_warehouse_id} (недоступен)
                        </option>
                    )}
                </select>
            ),
        },
        {
            key: '_actions', label: '', width: '100',
            sortable: false,
            render: (_v: any, row: any) => (
                <button className="btn btn-danger btn-sm" onClick={() => handleDelete(row.id)}>✕</button>
            ),
        },
    ], [warehouses]);

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
                    В колонке <strong>«Табло ФФ»</strong> выберите склад — в чат добавится закреплённое авто-табло
                    заявок только этого склада (или <em>«Все склады»</em> для общего табло). Бот должен быть админом чата,
                    чтобы закрепить сообщение; табло появится при ближайшей синхронизации.
                    Тумблер <strong>«Замеры»</strong> включает ежедневную сводку замеров WB в этот чат
                    (в 09:00 МСК за вчера + сегодня).
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
