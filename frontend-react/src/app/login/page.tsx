'use client';
import { useState } from 'react';
import { api } from '@/lib/api';

export default function LoginPage() {
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setLoading(true);
        try {
            const res = await api.login(username, password);
            api.setToken(res.access_token);
            // Check for pending invite
            const pendingInvite = localStorage.getItem('dds_pending_invite');
            if (pendingInvite) {
                localStorage.removeItem('dds_pending_invite');
                window.location.href = `/invite/${pendingInvite}`;
            } else {
                window.location.href = '/projects';
            }
        } catch (err: any) {
            setError(err.message || 'Ошибка входа');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="auth-page">
            <div className="auth-card animate-in">
                <div className="auth-logo">DDS</div>
                <div className="auth-title">Добро пожаловать</div>
                <div className="auth-subtitle">Войдите в свой аккаунт</div>

                {error && <div className="auth-error">{error}</div>}

                <form className="auth-form" onSubmit={handleSubmit}>
                    <div className="form-group">
                        <label className="form-label">Логин</label>
                        <input
                            className="form-input"
                            type="text"
                            placeholder="Введите логин"
                            value={username}
                            onChange={e => setUsername(e.target.value)}
                            required
                            autoFocus
                        />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Пароль</label>
                        <input
                            className="form-input"
                            type="password"
                            placeholder="Введите пароль"
                            value={password}
                            onChange={e => setPassword(e.target.value)}
                            required
                        />
                    </div>
                    <button type="submit" className="btn btn-primary" disabled={loading}
                        style={{ width: '100%', padding: '12px', fontSize: '15px', marginTop: 4 }}>
                        {loading ? 'Вход...' : 'Войти'}
                    </button>
                </form>

                <div className="auth-footer">
                    Нет аккаунта? <a href="/register">Зарегистрироваться</a>
                </div>
            </div>
        </div>
    );
}
