'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/api';

export default function Home() {
    const [checking, setChecking] = useState(true);

    useEffect(() => {
        if (!api.isAuthenticated()) {
            window.location.href = '/login';
        } else {
            // Redirect to projects page
            window.location.href = '/projects';
        }
        setChecking(false);
    }, []);

    if (checking) return (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
            <div className="sidebar-logo" style={{ fontSize: 40 }}>DDS</div>
        </div>
    );

    return null;
}
