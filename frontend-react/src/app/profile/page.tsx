'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

export default function ProfilePage() {
    const [profile, setProfile] = useState<any>(null);
    const [form, setForm] = useState({ email: '', first_name: '', last_name: '' });
    const [pwForm, setPwForm] = useState({ old_password: '', new_password: '' });
    const [msg, setMsg] = useState('');
    const [pwMsg, setPwMsg] = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (!api.isAuthenticated()) { window.location.href = '/login'; return; }
        api.getProfile().then(p => {
            setProfile(p);
            setForm({ email: p.email || '', first_name: p.first_name || '', last_name: p.last_name || '' });
        }).finally(() => setLoading(false));
    }, []);

    const updateProfile = async () => {
        try {
            await api.updateProfile(form);
            setMsg('Профиль обновлён');
        } catch (e: any) { setMsg(e.message); }
    };

    const changePassword = async () => {
        try {
            await api.changePassword(pwForm.old_password, pwForm.new_password);
            setPwMsg('Пароль изменён');
            setPwForm({ old_password: '', new_password: '' });
        } catch (e: any) { setPwMsg(e.message); }
    };

    if (loading) return (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
            <div className="sidebar-logo" style={{ fontSize: 40 }}>DDS</div>
        </div>
    );

    return (
        <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '60px 24px' }}>
            <div className="sidebar-logo" style={{ fontSize: 32, marginBottom: 8 }}>DDS</div>
            <p style={{ color: 'var(--color-text-muted)', fontSize: 14, marginBottom: 32 }}>Личный кабинет</p>

            <div className="animate-in" style={{ width: '100%', maxWidth: 500 }}>
                {/* Profile */}
                <div className="glass-card" style={{ marginBottom: 20 }}>
                    <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>👤 Профиль</h3>
                    {msg && <div style={{ color: 'var(--color-success)', fontSize: 13, marginBottom: 12 }}>{msg}</div>}
                    <div className="auth-form">
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                            <div className="form-group">
                                <label className="form-label">Имя</label>
                                <input className="form-input" value={form.first_name}
                                    onChange={e => setForm(f => ({ ...f, first_name: e.target.value }))} />
                            </div>
                            <div className="form-group">
                                <label className="form-label">Фамилия</label>
                                <input className="form-input" value={form.last_name}
                                    onChange={e => setForm(f => ({ ...f, last_name: e.target.value }))} />
                            </div>
                        </div>
                        <div className="form-group">
                            <label className="form-label">Email</label>
                            <input className="form-input" type="email" value={form.email}
                                onChange={e => setForm(f => ({ ...f, email: e.target.value }))} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Логин</label>
                            <input className="form-input" value={profile?.username} disabled style={{ opacity: 0.5 }} />
                        </div>
                        <button className="btn btn-primary" onClick={updateProfile} style={{ width: '100%' }}>
                            Сохранить
                        </button>
                    </div>
                </div>

                {/* Password */}
                <div className="glass-card" style={{ marginBottom: 20 }}>
                    <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>🔒 Сменить пароль</h3>
                    {pwMsg && <div style={{ color: 'var(--color-success)', fontSize: 13, marginBottom: 12 }}>{pwMsg}</div>}
                    <div className="auth-form">
                        <div className="form-group">
                            <label className="form-label">Текущий пароль</label>
                            <input className="form-input" type="password" value={pwForm.old_password}
                                onChange={e => setPwForm(f => ({ ...f, old_password: e.target.value }))} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Новый пароль</label>
                            <input className="form-input" type="password" value={pwForm.new_password}
                                onChange={e => setPwForm(f => ({ ...f, new_password: e.target.value }))} />
                        </div>
                        <button className="btn btn-secondary" onClick={changePassword} style={{ width: '100%' }}>
                            Изменить пароль
                        </button>
                    </div>
                </div>

                <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => window.location.href = '/projects'}>
                        ← Проекты
                    </button>
                    <button className="btn btn-danger btn-sm" onClick={() => { api.clearToken(); window.location.href = '/login'; }}>
                        Выйти
                    </button>
                </div>
            </div>
        </div>
    );
}
