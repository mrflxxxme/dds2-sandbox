'use client';

import { useState } from 'react';
import { ffLogin } from '@/lib/api/ff';

export default function FfLoginPage() {
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError('');
        setLoading(true);
        try {
            await ffLogin(username, password);
            window.location.href = '/ff/acceptances';
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : 'Ошибка входа');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="auth-page">
            <div className="auth-card animate-in">
                <div className="auth-logo">ФФ</div>
                <div className="auth-title">ФФ-портал</div>
                <div className="auth-subtitle">Вход для оператора фулфилмента</div>

                {error && <div className="auth-error">{error}</div>}

                <form className="auth-form" onSubmit={handleSubmit}>
                    <div className="form-group">
                        <label className="form-label">Логин</label>
                        <input
                            className="form-input"
                            type="text"
                            placeholder="Логин"
                            value={username}
                            onChange={(e) => setUsername(e.target.value)}
                            required
                            autoFocus
                            autoCapitalize="none"
                        />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Пароль</label>
                        <input
                            className="form-input"
                            type="password"
                            placeholder="Введите пароль"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            required
                        />
                    </div>
                    <button
                        type="submit"
                        className="btn btn-primary"
                        disabled={loading}
                        style={{ width: '100%', padding: '12px', fontSize: '15px', marginTop: 4 }}
                    >
                        {loading ? 'Вход…' : 'Войти'}
                    </button>
                </form>
            </div>
        </div>
    );
}
