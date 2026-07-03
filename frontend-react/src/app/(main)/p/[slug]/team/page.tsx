'use client';
import { useEffect, useState, useCallback } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/utils';
import { usePermissions, invalidatePermissionsCache } from '@/lib/hooks/usePermissions';
import type { ProjectMember } from '@/types/api';
import TanStackDataTable from '@/components/TanStackDataTable';

// ─── Section/page structure for role editing ────────────────────────────────

const SECTION_PAGES: Record<string, { key: string; label: string }[]> = {
    'Общее': [
        { key: 'dashboard', label: 'Дашборд' },
    ],
    'Финансы': [
        { key: 'import', label: 'Импорт документов' },
        { key: 'txn', label: 'Операции' },
        { key: 'inbox', label: 'Inbox' },
        { key: 'reports', label: 'Отчёты' },
        { key: 'cost', label: 'Себестоимость' },
        { key: 'refs', label: 'Справочники' },
    ],
    'Склад': [
        { key: 'assembly', label: 'Заявки на сборку' },
        { key: 'assembly-analytics', label: 'Анализ сборки' },
        { key: 'logistics', label: 'Лист логиста' },
        { key: 'fbo', label: 'Поставки FBO' },
        { key: 'stocks', label: 'Остатки WB' },
        { key: 'stock-analytics', label: 'Аналитика остатков' },
        { key: 'barcode-labels', label: 'Генератор ШК' },
    ],
    'Поставки': [
        { key: 'supply-chain', label: 'Цепочка поставок' },
    ],
    'Заказы': [
        { key: 'planning', label: 'Планирование' },
        { key: 'container', label: 'Загрузка контейнера' },
    ],
    'Продажи': [
        { key: 'funnel', label: 'Воронка/Реклама' },
        { key: 'trends', label: 'Тренды' },
        { key: 'opiu', label: 'ОПИУ' },
        { key: 'plan-fact', label: 'План-Факт' },
        { key: 'geography', label: 'География заказов' },
    ],
    'Настройки': [
        { key: 'monitoring', label: 'Мониторинг' },
        { key: 'project-settings', label: 'Настройки' },
        { key: 'team', label: 'Команда' },
    ],
};

const ROLE_BADGE_STYLES: Record<string, { bg: string; border: string; color: string }> = {
    owner:  { bg: 'rgba(139,92,246,0.12)',  border: 'rgba(139,92,246,0.3)',  color: '#a78bfa' },
    admin:  { bg: 'rgba(59,130,246,0.12)',   border: 'rgba(59,130,246,0.3)',  color: '#60a5fa' },
    editor: { bg: 'rgba(34,197,94,0.12)',    border: 'rgba(34,197,94,0.3)',   color: '#4ade80' },
    viewer: { bg: 'rgba(148,163,184,0.12)',  border: 'rgba(148,163,184,0.3)', color: '#94a3b8' },
};

const ROLE_LABELS: Record<string, string> = {
    owner: 'Владелец',
    admin: 'Админ',
    editor: 'Редактор',
    viewer: 'Наблюдатель',
};

function RoleBadge({ role }: { role: string }) {
    const style = ROLE_BADGE_STYLES[role] || ROLE_BADGE_STYLES.viewer;
    return (
        <span style={{
            display: 'inline-block',
            padding: '2px 8px',
            borderRadius: 6,
            fontSize: 12,
            fontWeight: 500,
            background: style.bg,
            border: `1px solid ${style.border}`,
            color: style.color,
        }}>
            {ROLE_LABELS[role] || role}
        </span>
    );
}

// ─── Role Edit Modal ────────────────────────────────────────────────────────

interface RoleEditModalProps {
    member: ProjectMember;
    onClose: () => void;
    onSave: (role: string, pages: string[]) => Promise<void>;
}

function RoleEditModal({ member, onClose, onSave }: RoleEditModalProps) {
    const [role, setRole] = useState(member.role);
    const [selectedPages, setSelectedPages] = useState<Set<string>>(new Set(member.pages || []));
    const [saving, setSaving] = useState(false);

    const togglePage = (key: string) => {
        setSelectedPages(prev => {
            const next = new Set(prev);
            if (next.has(key)) next.delete(key); else next.add(key);
            return next;
        });
    };

    const toggleSection = (sectionPages: { key: string }[]) => {
        const keys = sectionPages.map(p => p.key);
        const allSelected = keys.every(k => selectedPages.has(k));
        setSelectedPages(prev => {
            const next = new Set(prev);
            keys.forEach(k => allSelected ? next.delete(k) : next.add(k));
            return next;
        });
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            await onSave(role, Array.from(selectedPages));
            onClose();
        } catch {
            setSaving(false);
        }
    };

    const showPages = role === 'editor' || role === 'viewer';

    return (
        <div style={{
            position: 'fixed', inset: 0, zIndex: 1000,
            background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center',
        }} onClick={onClose}>
            <div style={{
                width: 480, maxHeight: '80vh', overflow: 'auto', padding: 24,
                background: 'var(--color-bg, #fff)', borderRadius: 16,
                boxShadow: '0 20px 60px rgba(0,0,0,0.3)', border: '1px solid var(--color-border, #e5e7eb)',
            }} onClick={e => e.stopPropagation()}>
                <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>
                    Изменить роль: {member.first_name || member.username}
                </h3>

                <div className="form-group" style={{ marginBottom: 16 }}>
                    <label className="form-label">Роль</label>
                    <select className="form-input" value={role} onChange={e => setRole(e.target.value as ProjectMember['role'])}>
                        <option value="admin">Админ</option>
                        <option value="editor">Редактор</option>
                        <option value="viewer">Наблюдатель</option>
                    </select>
                </div>

                {showPages && (
                    <div style={{ marginBottom: 16 }}>
                        <label className="form-label" style={{ marginBottom: 8, display: 'block' }}>Доступные разделы</label>
                        {Object.entries(SECTION_PAGES).map(([section, pages]) => {
                            const sectionKeys = pages.map(p => p.key);
                            const allChecked = sectionKeys.every(k => selectedPages.has(k));
                            const someChecked = sectionKeys.some(k => selectedPages.has(k));
                            return (
                                <div key={section} style={{ marginBottom: 12 }}>
                                    <label style={{
                                        display: 'flex', alignItems: 'center', gap: 8,
                                        cursor: 'pointer', fontWeight: 500, fontSize: 14,
                                        color: 'var(--color-text)',
                                    }}>
                                        <input
                                            type="checkbox"
                                            checked={allChecked}
                                            ref={el => { if (el) el.indeterminate = someChecked && !allChecked; }}
                                            onChange={() => toggleSection(pages)}
                                            style={{ accentColor: '#8b5cf6' }}
                                        />
                                        {section}
                                    </label>
                                    <div style={{ paddingLeft: 24, marginTop: 4 }}>
                                        {pages.map(page => (
                                            <label key={page.key} style={{
                                                display: 'flex', alignItems: 'center', gap: 8,
                                                cursor: 'pointer', fontSize: 13, padding: '2px 0',
                                                color: 'var(--color-text-muted)',
                                            }}>
                                                <input
                                                    type="checkbox"
                                                    checked={selectedPages.has(page.key)}
                                                    onChange={() => togglePage(page.key)}
                                                    style={{ accentColor: '#8b5cf6' }}
                                                />
                                                {page.label}
                                            </label>
                                        ))}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}

                {role === 'admin' && (
                    <p style={{ fontSize: 13, color: 'var(--color-text-dim)', marginBottom: 16 }}>
                        Админ имеет доступ ко всем разделам.
                    </p>
                )}

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button className="btn btn-secondary" onClick={onClose}>Отмена</button>
                    <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
                        {saving ? 'Сохранение...' : 'Сохранить'}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ─── Main Page ──────────────────────────────────────────────────────────────

export default function TeamPage() {
    const { slug } = useParams() as { slug: string };
    const [members, setMembers] = useState<ProjectMember[]>([]);
    const [invites, setInvites] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [email, setEmail] = useState('');
    const [inviteRole, setInviteRole] = useState<'admin' | 'editor' | 'viewer'>('viewer');
    const [inviteLink, setInviteLink] = useState('');
    const [msg, setMsg] = useState('');
    const [editMember, setEditMember] = useState<ProjectMember | null>(null);
    const { canManage, loading: permLoading } = usePermissions();
    const [currentUserId, setCurrentUserId] = useState<number | null>(null);
    const [editingTelegramId, setEditingTelegramId] = useState<number | null>(null);
    const [telegramInput, setTelegramInput] = useState('');

    useEffect(() => {
        loadData();
        api.getProfile().then(u => setCurrentUserId(u.id)).catch(() => {});
    }, []);

    const loadData = useCallback(async () => {
        try {
            const [m, inv] = await Promise.all([api.getMembers(slug), api.getInvites(slug)]);
            setMembers(m as unknown as ProjectMember[]);
            setInvites(inv);
        } catch {}
        setLoading(false);
    }, [slug]);

    const sendInvite = async () => {
        if (!email.trim()) return;
        try {
            await api.inviteByEmail(slug, email.trim());
            setEmail('');
            setMsg('Приглашение отправлено');
            loadData();
        } catch (e: any) { setMsg(e.message); }
    };

    const copyToClipboard = (text: string) => {
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text);
        } else {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.left = '-9999px';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
        }
    };

    const copyLink = async () => {
        try {
            const res = await api.getInviteLink(slug);
            const link = `${window.location.origin}/invite/${res.invite_token}`;
            setInviteLink(link);
            copyToClipboard(link);
            setMsg('Ссылка скопирована');
        } catch (e: any) { setMsg(e.message); }
    };

    const removeMember = async (userId: number) => {
        if (!confirm('Удалить участника?')) return;
        await api.removeMember(slug, userId);
        loadData();
    };

    const handleRoleSave = async (role: string, pages: string[]) => {
        if (!editMember) return;
        try {
            await api.updateMemberRole(slug, editMember.user_id, role, pages);
            invalidatePermissionsCache(slug);
            setMsg('Роль обновлена');
            loadData();
        } catch (e: any) {
            setMsg(e?.message || 'Ошибка сохранения роли');
            throw e;
        }
    };

    const startTelegramEdit = (member: ProjectMember) => {
        setEditingTelegramId(member.user_id);
        setTelegramInput(member.telegram_username || '');
    };

    const saveTelegram = async (userId: number) => {
        const value = telegramInput.trim().replace(/^@/, '') || null;
        try {
            await api.updateTelegramUsername(slug, userId, value);
            setMembers(prev => prev.map(m =>
                m.user_id === userId ? { ...m, telegram_username: value ?? undefined } : m
            ));
            setMsg('Telegram обновлён');
        } catch (e: any) {
            setMsg(e.message || 'Ошибка сохранения');
        }
        setEditingTelegramId(null);
    };

    const handleTelegramKeyDown = (e: React.KeyboardEvent, userId: number) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            saveTelegram(userId);
        } else if (e.key === 'Escape') {
            setEditingTelegramId(null);
        }
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
            {canManage() && (
                <div className="glass-card" style={{ marginBottom: 24 }}>
                    <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>Пригласить участника</h3>
                    <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                        <div className="form-group" style={{ margin: 0, minWidth: 140 }}>
                            <select className="form-input" value={inviteRole} onChange={e => setInviteRole(e.target.value as any)}>
                                <option value="admin">Админ</option>
                                <option value="editor">Редактор</option>
                                <option value="viewer">Наблюдатель</option>
                            </select>
                        </div>
                        <button className="btn btn-primary" onClick={copyLink}>Получить ссылку-приглашение</button>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'center', opacity: 0.5 }}>
                            <div className="form-group" style={{ flex: 1, minWidth: 200, margin: 0 }}>
                                <input className="form-input" placeholder="Email участника" type="email"
                                    value={email} onChange={e => setEmail(e.target.value)} disabled
                                    style={{ cursor: 'not-allowed' }} />
                            </div>
                            <button className="btn btn-secondary" disabled style={{ cursor: 'not-allowed' }}
                                title="Отправка email не настроена">Пригласить</button>
                        </div>
                        <span style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>
                            Отправка email пока не настроена — используйте ссылку
                        </span>
                    </div>
                    {inviteLink && (
                        <div style={{
                            marginTop: 12, fontSize: 13, color: '#a78bfa', fontFamily: 'monospace',
                            background: 'rgba(139,92,246,0.08)', border: '1px solid rgba(139,92,246,0.2)',
                            padding: '10px 14px', borderRadius: 8, display: 'flex', alignItems: 'center', gap: 10
                        }}>
                            <span style={{ flex: 1, wordBreak: 'break-all' }}>{inviteLink}</span>
                            <button className="btn btn-sm btn-secondary"
                                onClick={() => { copyToClipboard(inviteLink); setMsg('Ссылка скопирована'); }}
                                style={{ whiteSpace: 'nowrap' }}>Копировать</button>
                        </div>
                    )}
                </div>
            )}

            {/* Members */}
            {/* TODO: migrate to TanStackDataTable — table with inline Telegram edit input and action buttons */}
            <div className="glass-card" style={{ marginBottom: 24 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>Участники ({members.length})</h3>
                <table className="data-table">
                    <thead>
                        <tr>
                            <th>Пользователь</th>
                            <th>Роль</th>
                            <th>Email</th>
                            <th>Telegram</th>
                            <th>Присоединился</th>
                            {canManage() && <th></th>}
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
                                <td><RoleBadge role={m.role} /></td>
                                <td style={{ color: 'var(--color-text-muted)' }}>{m.email || '—'}</td>
                                <td style={{ color: 'var(--color-text-muted)' }}>
                                    {editingTelegramId === m.user_id ? (
                                        <input
                                            className="form-input"
                                            value={telegramInput}
                                            onChange={e => setTelegramInput(e.target.value)}
                                            onBlur={() => saveTelegram(m.user_id)}
                                            onKeyDown={e => handleTelegramKeyDown(e, m.user_id)}
                                            placeholder="@username"
                                            autoFocus
                                            style={{ padding: '2px 8px', fontSize: 13, width: 150 }}
                                        />
                                    ) : (
                                        <span
                                            onClick={() => canManage() && startTelegramEdit(m)}
                                            style={{ cursor: canManage() ? 'pointer' : 'default' }}
                                            title={canManage() ? 'Нажмите для редактирования' : undefined}
                                        >
                                            {m.telegram_username ? `@${m.telegram_username}` : '—'}
                                        </span>
                                    )}
                                </td>
                                <td style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>{formatDateTime(m.joined_at)}</td>
                                {canManage() && (
                                    <td>
                                        <div style={{ display: 'flex', gap: 6 }}>
                                            {m.role !== 'owner' && m.user_id !== currentUserId && (
                                                <>
                                                    <button className="btn btn-secondary btn-sm" onClick={() => setEditMember(m)}>
                                                        Изменить
                                                    </button>
                                                    <button className="btn btn-danger btn-sm" onClick={() => removeMember(m.user_id)}>
                                                        Удалить
                                                    </button>
                                                </>
                                            )}
                                        </div>
                                    </td>
                                )}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {/* Invites History */}
            {invites.length > 0 && (
                <TanStackDataTable
                    title="История приглашений"
                    columns={[
                        { key: 'email', label: 'Email', render: (v: any) => <>{v || 'Ссылка-приглашение'}</> },
                        {
                            key: 'status', label: 'Статус',
                            render: (v: any) => (
                                <span className={`badge ${v === 'accepted' ? 'badge-success' : v === 'pending' ? 'badge-warning' : 'badge-danger'}`}>
                                    {v === 'accepted' ? 'Принято' : v === 'pending' ? 'Ожидание' : 'Истекло'}
                                </span>
                            ),
                        },
                        { key: 'created_at', label: 'Создано', render: (v: any) => <span style={{ fontSize: 13 }}>{formatDateTime(v)}</span> },
                        { key: 'accepted_at', label: 'Принято', render: (v: any) => <span style={{ fontSize: 13 }}>{formatDateTime(v)}</span> },
                    ]}
                    data={invites}
                    enableSorting
                    enablePagination={false}
                />
            )}

            {/* Role Edit Modal */}
            {editMember && (
                <RoleEditModal
                    member={editMember}
                    onClose={() => setEditMember(null)}
                    onSave={handleRoleSave}
                />
            )}
        </div>
    );
}
