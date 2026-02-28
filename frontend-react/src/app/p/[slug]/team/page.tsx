'use client';
import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/utils';

export default function TeamPage() {
    const { slug } = useParams() as { slug: string };
    const [members, setMembers] = useState<any[]>([]);
    const [invites, setInvites] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [email, setEmail] = useState('');
    const [inviteLink, setInviteLink] = useState('');
    const [msg, setMsg] = useState('');

    useEffect(() => { loadData(); }, []);

    const loadData = async () => {
        const [m, inv] = await Promise.all([api.getMembers(slug), api.getInvites(slug)]);
        setMembers(m);
        setInvites(inv);
        setLoading(false);
    };

    const sendInvite = async () => {
        if (!email.trim()) return;
        try {
            await api.inviteByEmail(slug, email.trim());
            setEmail('');
            setMsg('Приглашение отправлено');
            loadData();
        } catch (e: any) { setMsg(e.message); }
    };

    const copyLink = async () => {
        try {
            const res = await api.getInviteLink(slug);
            const link = `${window.location.origin}/invite/${res.invite_token}`;
            setInviteLink(link);
            navigator.clipboard.writeText(link);
            setMsg('Ссылка скопирована');
        } catch (e: any) { setMsg(e.message); }
    };

    const removeMember = async (userId: number) => {
        if (!confirm('Удалить участника?')) return;
        await api.removeMember(slug, userId);
        loadData();
    };

    if (loading) return <div style={{ padding: 40, color: 'var(--color-text-muted)' }}>Загрузка...</div>;

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Команда</h1>
                    <p className="page-subtitle">Управление участниками проекта</p>
                </div>
            </div>

            {msg && (
                <div style={{
                    background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.3)',
                    color: 'var(--color-success)', padding: '10px 14px', borderRadius: 8, fontSize: 13, marginBottom: 16
                }}>
                    {msg}
                    <span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setMsg('')}>✕</span>
                </div>
            )}

            {/* Invite */}
            <div className="glass-card" style={{ marginBottom: 24 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>✉️ Пригласить участника</h3>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                    <div className="form-group" style={{ flex: 1, minWidth: 250 }}>
                        <input className="form-input" placeholder="Email участника" type="email"
                            value={email} onChange={e => setEmail(e.target.value)}
                            onKeyDown={e => e.key === 'Enter' && sendInvite()} />
                    </div>
                    <button className="btn btn-primary" onClick={sendInvite}>Пригласить</button>
                    <button className="btn btn-secondary" onClick={copyLink}>🔗 Ссылка</button>
                </div>
                {inviteLink && (
                    <div style={{
                        marginTop: 10, fontSize: 12, color: 'var(--color-text-dim)', fontFamily: 'monospace',
                        background: 'var(--color-bg-input)', padding: '8px 12px', borderRadius: 6
                    }}>
                        {inviteLink}
                    </div>
                )}
            </div>

            {/* Members */}
            <div className="glass-card" style={{ marginBottom: 24 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>👥 Участники ({members.length})</h3>
                <table className="data-table">
                    <thead>
                        <tr>
                            <th>Пользователь</th>
                            <th>Email</th>
                            <th>Присоединился</th>
                            <th></th>
                        </tr>
                    </thead>
                    <tbody>
                        {members.map(m => (
                            <tr key={m.id}>
                                <td style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                                    <div className="sidebar-avatar" style={{ width: 28, height: 28, fontSize: 12 }}>
                                        {(m.first_name || m.username).charAt(0).toUpperCase()}
                                    </div>
                                    <span>{m.first_name ? `${m.first_name} ${m.last_name || ''}` : m.username}</span>
                                </td>
                                <td style={{ color: 'var(--color-text-muted)' }}>{m.email || '—'}</td>
                                <td style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>{formatDateTime(m.joined_at)}</td>
                                <td>
                                    <button className="btn btn-danger btn-sm" onClick={() => removeMember(m.user_id)}>Удалить</button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {/* Invites History */}
            {invites.length > 0 && (
                <div className="glass-card">
                    <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>📨 История приглашений</h3>
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Email</th>
                                <th>Статус</th>
                                <th>Создано</th>
                                <th>Принято</th>
                            </tr>
                        </thead>
                        <tbody>
                            {invites.map(inv => (
                                <tr key={inv.id}>
                                    <td>{inv.email || 'Ссылка-приглашение'}</td>
                                    <td>
                                        <span className={`badge ${inv.status === 'accepted' ? 'badge-success' : inv.status === 'pending' ? 'badge-warning' : 'badge-danger'}`}>
                                            {inv.status === 'accepted' ? 'Принято' : inv.status === 'pending' ? 'Ожидание' : 'Истекло'}
                                        </span>
                                    </td>
                                    <td style={{ fontSize: 13 }}>{formatDateTime(inv.created_at)}</td>
                                    <td style={{ fontSize: 13 }}>{formatDateTime(inv.accepted_at)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
