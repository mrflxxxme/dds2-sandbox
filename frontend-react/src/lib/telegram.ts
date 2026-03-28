/**
 * Telegram Web App SDK helpers.
 *
 * Provides typed wrappers around the global Telegram.WebApp object
 * that is injected by telegram-web-app.js script in the TMA layout.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

export interface TelegramUser {
    id: number;
    first_name: string;
    last_name?: string;
    username?: string;
    language_code?: string;
    photo_url?: string;
}

export interface TelegramTheme {
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
}

export interface TelegramWebApp {
    initData: string;
    initDataUnsafe: {
        user?: TelegramUser;
        auth_date?: number;
        hash?: string;
        query_id?: string;
        start_param?: string;
    };
    version: string;
    platform: string;
    colorScheme: 'light' | 'dark';
    themeParams: TelegramTheme;
    isExpanded: boolean;
    viewportHeight: number;
    viewportStableHeight: number;
    MainButton: any;
    BackButton: {
        isVisible: boolean;
        show(): void;
        hide(): void;
        onClick(cb: () => void): void;
        offClick(cb: () => void): void;
    };
    HapticFeedback: {
        impactOccurred(style: 'light' | 'medium' | 'heavy' | 'rigid' | 'soft'): void;
        notificationOccurred(type: 'error' | 'success' | 'warning'): void;
        selectionChanged(): void;
    };
    ready(): void;
    expand(): void;
    close(): void;
    setHeaderColor(color: string): void;
    setBackgroundColor(color: string): void;
    enableClosingConfirmation(): void;
    disableClosingConfirmation(): void;
}

/**
 * Get the Telegram WebApp instance. Returns null outside TMA context.
 */
export function getTelegramWebApp(): TelegramWebApp | null {
    if (typeof window === 'undefined') return null;
    return (window as any).Telegram?.WebApp ?? null;
}

/**
 * Check if we are running inside a Telegram Mini App.
 */
export function isTelegramMiniApp(): boolean {
    const twa = getTelegramWebApp();
    return !!twa && !!twa.initData;
}

/**
 * Get the raw initData string for server-side validation.
 */
export function getTelegramInitData(): string {
    return getTelegramWebApp()?.initData ?? '';
}

/**
 * Get the current Telegram user info (from unsigned client data).
 */
export function getTelegramUser(): TelegramUser | null {
    return getTelegramWebApp()?.initDataUnsafe?.user ?? null;
}

/**
 * Get current Telegram theme params for styling.
 */
export function getTelegramTheme(): TelegramTheme {
    return getTelegramWebApp()?.themeParams ?? {};
}

/**
 * Trigger haptic feedback if available.
 */
export function haptic(type: 'light' | 'medium' | 'heavy' | 'success' | 'error' | 'warning' | 'selection' = 'light') {
    const twa = getTelegramWebApp();
    if (!twa?.HapticFeedback) return;

    if (type === 'selection') {
        twa.HapticFeedback.selectionChanged();
    } else if (type === 'success' || type === 'error' || type === 'warning') {
        twa.HapticFeedback.notificationOccurred(type);
    } else {
        twa.HapticFeedback.impactOccurred(type);
    }
}
