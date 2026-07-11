'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

/** Read a pending invite token from localStorage or the `?invite=` query param. */
function readInviteToken(): string | null {
    if (typeof window === 'undefined') return null;
    return localStorage.getItem('dds_pending_invite')
        || new URLSearchParams(window.location.search).get('invite');
}

export default function RegisterPage() {
    const [form, setForm] = useState({ username: '', password: '', email: '', first_name: '', last_name: '' });
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const [hasInvite, setHasInvite] = useState(false);

    useEffect(() => {
        setHasInvite(!!readInviteToken());
    }, []);

    const set = (k: string, v: string) => setForm(f => ({ ...f, [k]: v }));

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setLoading(true);
        try {
            const inviteToken = readInviteToken();
            const res = await api.register({ ...form, invite_token: inviteToken ?? undefined });
            api.setToken(res.access_token);
            if (res.refresh_token) api.setRefreshToken(res.refresh_token);
            // With an invite the backend already joined us to the project — go home.
            localStorage.removeItem('dds_pending_invite');
            window.location.href = '/projects';
        } catch (err: any) {
            setError(err.message || 'Ошибка регистрации');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="auth-page">
            <div className="auth-card animate-in">
                <div className="auth-logo">DDS</div>
                <div className="auth-title">Создать аккаунт</div>
                <div className="auth-subtitle">
                    {hasInvite ? 'Регистрация по приглашению в проект' : 'Заполните данные для регистрации'}
                </div>

                {error && <div className="auth-error">{error}</div>}

                <form className="auth-form" onSubmit={handleSubmit}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                        <div className="form-group">
                            <label className="form-label">Имя</label>
                            <input className="form-input" placeholder="Иван" value={form.first_name}
                                onChange={e => set('first_name', e.target.value)} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Фамилия</label>
                            <input className="form-input" placeholder="Иванов" value={form.last_name}
                                onChange={e => set('last_name', e.target.value)} />
                        </div>
                    </div>
                    <div className="form-group">
                        <label className="form-label">Email</label>
                        <input className="form-input" type="email" placeholder="email@example.com"
                            value={form.email} onChange={e => set('email', e.target.value)} />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Логин *</label>
                        <input className="form-input" placeholder="Придумайте логин" required
                            value={form.username} onChange={e => set('username', e.target.value)} autoFocus />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Пароль *</label>
                        <input className="form-input" type="password" placeholder="Минимум 6 символов" required
                            value={form.password} onChange={e => set('password', e.target.value)} />
                    </div>
                    <button type="submit" className="btn btn-primary" disabled={loading}
                        style={{ width: '100%', padding: '12px', fontSize: '15px', marginTop: 4 }}>
                        {loading ? 'Регистрация...' : 'Создать аккаунт'}
                    </button>
                </form>

                <div className="auth-footer">
                    Уже есть аккаунт? <a href="/login">Войти</a>
                </div>
            </div>
        </div>
    );
}
