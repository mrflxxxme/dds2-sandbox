import type { Metadata } from 'next';
import '../globals.css';
import './tma.css';

export const metadata: Metadata = {
    title: 'DDS Analytics',
    description: 'DDS Telegram Mini App',
};

export default function TmaRootLayout({ children }: { children: React.ReactNode }) {
    return (
        <html lang="ru">
            <head>
                <link rel="preconnect" href="https://fonts.googleapis.com" />
                <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
                <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" />
                <script src="https://telegram.org/js/telegram-web-app.js" />
            </head>
            <body className="tma-body">{children}</body>
        </html>
    );
}
