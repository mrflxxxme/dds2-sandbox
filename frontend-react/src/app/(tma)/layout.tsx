import type { Metadata, Viewport } from 'next';
import Script from 'next/script';
import './tma/tma.css';

export const metadata: Metadata = {
    title: 'DDS Mini App',
    description: 'Управленческий учёт — Telegram Mini App',
};

export const viewport: Viewport = {
    width: 'device-width',
    initialScale: 1,
    maximumScale: 1,
    userScalable: false,
    viewportFit: 'cover',
};

export default function TmaRootLayout({ children }: { children: React.ReactNode }) {
    return (
        <html lang="ru">
            <head>
                <Script
                    src="https://telegram.org/js/telegram-web-app.js"
                    strategy="afterInteractive"
                />
                <link rel="preconnect" href="https://fonts.googleapis.com" />
                <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
                <link
                    href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap"
                    rel="stylesheet"
                />
            </head>
            <body className="tma-body">{children}</body>
        </html>
    );
}
