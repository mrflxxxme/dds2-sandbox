/**
 * Telegram WebApp SDK helpers for Mini App.
 *
 * Wraps the global Telegram.WebApp object with type safety.
 */

interface TelegramWebApp {
    initData: string;
    initDataUnsafe: {
        user?: {
            id: number;
            first_name: string;
            last_name?: string;
            username?: string;
            language_code?: string;
        };
    };
    colorScheme: 'light' | 'dark';
    themeParams: {
        bg_color?: string;
        text_color?: string;
        hint_color?: string;
        link_color?: string;
        button_color?: string;
        button_text_color?: string;
        secondary_bg_color?: string;
        header_bg_color?: string;
        section_bg_color?: string;
        section_header_text_color?: string;
        subtitle_text_color?: string;
        destructive_text_color?: string;
    };
    isExpanded: boolean;
    viewportHeight: number;
    viewportStableHeight: number;
    BackButton: {
        isVisible: boolean;
        show(): void;
        hide(): void;
        onClick(cb: () => void): void;
        offClick(cb: () => void): void;
    };
    MainButton: {
        text: string;
        isVisible: boolean;
        isActive: boolean;
        show(): void;
        hide(): void;
        setText(text: string): void;
        onClick(cb: () => void): void;
        offClick(cb: () => void): void;
        showProgress(leaveActive?: boolean): void;
        hideProgress(): void;
        enable(): void;
        disable(): void;
    };
    HapticFeedback: {
        impactOccurred(style: 'light' | 'medium' | 'heavy' | 'rigid' | 'soft'): void;
        notificationOccurred(type: 'error' | 'success' | 'warning'): void;
        selectionChanged(): void;
    };
    expand(): void;
    close(): void;
    ready(): void;
    setHeaderColor(color: string): void;
    setBackgroundColor(color: string): void;
}

declare global {
    interface Window {
        Telegram?: {
            WebApp: TelegramWebApp;
        };
    }
}

export function getTelegramWebApp(): TelegramWebApp | null {
    if (typeof window === 'undefined') return null;
    return window.Telegram?.WebApp ?? null;
}

export function isTelegramMiniApp(): boolean {
    const tg = getTelegramWebApp();
    return !!tg?.initData;
}

export function getTelegramInitData(): string {
    return getTelegramWebApp()?.initData ?? '';
}

export function getTelegramUser() {
    return getTelegramWebApp()?.initDataUnsafe?.user ?? null;
}

export function getTelegramTheme() {
    const tg = getTelegramWebApp();
    if (!tg) return { isDark: false, colors: {} as TelegramWebApp['themeParams'] };
    return {
        isDark: tg.colorScheme === 'dark',
        colors: tg.themeParams,
    };
}

export function haptic(type: 'light' | 'medium' | 'success' | 'error' | 'selection') {
    const tg = getTelegramWebApp();
    if (!tg) return;
    switch (type) {
        case 'light':
        case 'medium':
            tg.HapticFeedback.impactOccurred(type);
            break;
        case 'success':
        case 'error':
            tg.HapticFeedback.notificationOccurred(type);
            break;
        case 'selection':
            tg.HapticFeedback.selectionChanged();
            break;
    }
}
