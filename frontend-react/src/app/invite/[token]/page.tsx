'use client';
import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';

export default function AcceptInvitePage() {
    const { token } = useParams() as { token: string };
    const router = useRouter();
    const [status, setStatus] = useState<'loading' | 'success' | 'login' | 'error'>('loading');
    const [message, setMessage] = useState('');
    const [projectSlug, setProjectSlug] = useState('');

    useEffect(() => {
        acceptInvite();
    }, []);

    const acceptInvite = async () => {
        // Check if user is logged in
        if (!api.isAuthenticated()) {
            setStatus('login');
            setMessage('Для принятия приглашения необходимо войти или зарегистрироваться');
            return;
        }

        try {
            const res = await api.acceptInvite(token);
            setStatus('success');
            setMessage(res.message || 'Приглашение принято!');
            setProjectSlug(res.project_slug || '');

            // Auto-redirect after 2 seconds
            if (res.project_slug) {
                setTimeout(() => {
                    router.push(`/p/${res.project_slug}`);
                }, 2000);
            }
        } catch (err: any) {
            setStatus('error');
            setMessage(err.message || 'Не удалось принять приглашение');
        }
    };

    const goToLogin = () => {
        // Save invite token so we can accept after login
        localStorage.setItem('dds_pending_invite', token);
        router.push('/login');
    };

    const goToRegister = () => {
        localStorage.setItem('dds_pending_invite', token);
        router.push('/register');
    };

    return (
        <div style={{
            minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'var(--color-bg, #0f0f1a)',
        }}>
            <div style={{
                background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: 16, padding: '48px 40px', maxWidth: 440, width: '100%', textAlign: 'center',
            }}>
                {status === 'loading' && (
                    <>
                        <div style={{ fontSize: 48, marginBottom: 16 }}>⏳</div>
                        <h1 style={{ fontSize: 20, fontWeight: 600, color: '#fff', marginBottom: 8 }}>
                            Принимаем приглашение...
                        </h1>
                        <p style={{ color: '#888', fontSize: 14 }}>Подождите немного</p>
                    </>
                )}

                {status === 'success' && (
                    <>
                        <div style={{ fontSize: 48, marginBottom: 16 }}>🎉</div>
                        <h1 style={{ fontSize: 20, fontWeight: 600, color: '#10b981', marginBottom: 8 }}>
                            Добро пожаловать!
                        </h1>
                        <p style={{ color: '#ccc', fontSize: 14, marginBottom: 24 }}>{message}</p>
                        {projectSlug && (
                            <p style={{ color: '#888', fontSize: 12 }}>
                                Перенаправляем в проект...
                            </p>
                        )}
                        <button
                            onClick={() => projectSlug ? router.push(`/p/${projectSlug}`) : router.push('/')}
                            style={{
                                marginTop: 16, padding: '10px 24px', background: 'rgba(139,92,246,0.15)',
                                border: '1px solid rgba(139,92,246,0.4)', borderRadius: 8, color: '#a78bfa',
                                cursor: 'pointer', fontSize: 14, fontWeight: 500,
                            }}
                        >
                            Перейти в проект →
                        </button>
                    </>
                )}

                {status === 'login' && (
                    <>
                        <div style={{ fontSize: 48, marginBottom: 16 }}>🔐</div>
                        <h1 style={{ fontSize: 20, fontWeight: 600, color: '#fff', marginBottom: 8 }}>
                            Приглашение в проект
                        </h1>
                        <p style={{ color: '#ccc', fontSize: 14, marginBottom: 24 }}>{message}</p>
                        <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
                            <button onClick={goToLogin} style={{
                                padding: '10px 24px', background: 'rgba(139,92,246,0.15)',
                                border: '1px solid rgba(139,92,246,0.4)', borderRadius: 8, color: '#a78bfa',
                                cursor: 'pointer', fontSize: 14, fontWeight: 500,
                            }}>
                                Войти
                            </button>
                            <button onClick={goToRegister} style={{
                                padding: '10px 24px', background: 'rgba(34,197,94,0.1)',
                                border: '1px solid rgba(34,197,94,0.3)', borderRadius: 8, color: '#22c55e',
                                cursor: 'pointer', fontSize: 14, fontWeight: 500,
                            }}>
                                Регистрация
                            </button>
                        </div>
                    </>
                )}

                {status === 'error' && (
                    <>
                        <div style={{ fontSize: 48, marginBottom: 16 }}>❌</div>
                        <h1 style={{ fontSize: 20, fontWeight: 600, color: '#ef4444', marginBottom: 8 }}>
                            Ошибка
                        </h1>
                        <p style={{ color: '#ccc', fontSize: 14, marginBottom: 24 }}>{message}</p>
                        <button
                            onClick={() => router.push('/')}
                            style={{
                                padding: '10px 24px', background: 'rgba(255,255,255,0.05)',
                                border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, color: '#888',
                                cursor: 'pointer', fontSize: 14,
                            }}
                        >
                            На главную
                        </button>
                    </>
                )}
            </div>
        </div>
    );
}
