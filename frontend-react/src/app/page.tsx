"use client";

import { useState, useEffect } from "react";
import { getToken } from "@/lib/api";
import LoginPage from "@/components/LoginPage";
import Dashboard from "@/components/Dashboard";

export default function Home() {
    const [isAuth, setIsAuth] = useState(false);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const token = getToken();
        setIsAuth(!!token);
        setLoading(false);
    }, []);

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-screen">
                <div className="animate-spin w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full" />
            </div>
        );
    }

    if (!isAuth) {
        return <LoginPage onLogin={() => setIsAuth(true)} />;
    }

    return <Dashboard onLogout={() => setIsAuth(false)} />;
}
