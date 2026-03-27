'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

export default function ProjectsPage() {
    const [projects, setProjects] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [showCreate, setShowCreate] = useState(false);
    const [newName, setNewName] = useState('');
    const [creating, setCreating] = useState(false);

    useEffect(() => {
        if (!api.isAuthenticated()) { window.location.href = '/login'; return; }
        loadProjects();
    }, []);

    const loadProjects = async () => {
        try {
            const data = await api.getProjects();
            setProjects(data);
        } catch {
            window.location.href = '/login';
        } finally {
            setLoading(false);
        }
    };

    const handleCreate = async () => {
        if (!newName.trim()) return;
        setCreating(true);
        try {
            const p = await api.createProject(newName.trim());
            setProjects(prev => [p, ...prev]);
            setNewName('');
            setShowCreate(false);
        } catch { }
        setCreating(false);
    };

    const openProject = (p: any) => {
        api.setProjectId(p.id);
        localStorage.setItem('dds_project_slug', p.slug);
        localStorage.setItem('dds_project_name', p.name);
        window.location.href = `/p/${p.slug}`;
    };

    if (loading) return (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
            <div className="sidebar-logo" style={{ fontSize: 40 }}>DDS</div>
        </div>
    );

    return (
        <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '60px 24px' }}>
            <div className="sidebar-logo" style={{ fontSize: 36, marginBottom: 8 }}>DDS</div>
            <p style={{ color: 'var(--color-text-muted)', fontSize: 14, marginBottom: 40 }}>Выберите проект или создайте новый</p>

            <div className="animate-in" style={{ width: '100%', maxWidth: 600 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                    <h2 style={{ fontSize: 18, fontWeight: 600 }}>Мои проекты</h2>
                    <div style={{ display: 'flex', gap: 8 }}>
                        <button className="btn btn-secondary btn-sm" onClick={() => { window.location.href = '/profile'; }}>👤 Профиль</button>
                        <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)}>+ Новый проект</button>
                    </div>
                </div>

                {showCreate && (
                    <div className="glass-card animate-in" style={{ marginBottom: 16 }}>
                        <div style={{ display: 'flex', gap: 12, alignItems: 'end' }}>
                            <div className="form-group" style={{ flex: 1 }}>
                                <label className="form-label">Название проекта</label>
                                <input className="form-input" placeholder="Например: Мой бизнес"
                                    value={newName} onChange={e => setNewName(e.target.value)}
                                    onKeyDown={e => e.key === 'Enter' && handleCreate()} autoFocus />
                            </div>
                            <button className="btn btn-primary" onClick={handleCreate} disabled={creating}>
                                {creating ? '...' : 'Создать'}
                            </button>
                            <button className="btn btn-secondary" onClick={() => setShowCreate(false)}>✕</button>
                        </div>
                    </div>
                )}

                {projects.length === 0 ? (
                    <div className="empty-state glass-card">
                        <div className="empty-state-icon">📁</div>
                        <div className="empty-state-text">У вас пока нет проектов</div>
                        <button className="btn btn-primary" onClick={() => setShowCreate(true)}>Создать первый проект</button>
                    </div>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {projects.map(p => (
                            <div key={p.id} className="glass-card" onClick={() => openProject(p)}
                                style={{ cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 20px' }}>
                                <div>
                                    <div style={{ fontWeight: 600, fontSize: 16 }}>{p.name}</div>
                                    <div style={{ fontSize: 13, color: 'var(--color-text-dim)', marginTop: 2 }}>
                                        {p.members_count} участник{p.members_count > 1 ? (p.members_count < 5 ? 'а' : 'ов') : ''} · создан {new Date(p.created_at).toLocaleDateString('ru-RU')}
                                    </div>
                                </div>
                                <div style={{ color: 'var(--color-text-dim)', fontSize: 20 }}>→</div>
                            </div>
                        ))}
                    </div>
                )}

                <div style={{ textAlign: 'center', marginTop: 32 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => { api.clearToken(); window.location.href = '/login'; }}>
                        Выйти
                    </button>
                </div>
            </div>
        </div>
    );
}
