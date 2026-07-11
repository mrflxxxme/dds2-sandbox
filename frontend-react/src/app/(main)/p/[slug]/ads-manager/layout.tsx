import React from 'react';
import ToastProvider from './components/Toasts';

/** Раздел «Управление рекламой»: общий провайдер всплывающих уведомлений. */
export default function AdsManagerLayout({ children }: { children: React.ReactNode }) {
    return <ToastProvider>{children}</ToastProvider>;
}
