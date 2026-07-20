import '@testing-library/jest-dom/vitest';

// jsdom не реализует ResizeObserver, а на нём держатся таблицы с автоподгонкой высоты
// под вьюпорт (useFitViewport). Заглушка — no-op: в тестах высота не важна.
if (!('ResizeObserver' in globalThis)) {
    globalThis.ResizeObserver = class {
        observe() { }
        unobserve() { }
        disconnect() { }
    } as unknown as typeof ResizeObserver;
}
