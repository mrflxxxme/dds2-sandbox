import React from 'react';

/** Обёртка минималистичной line-иконки (currentColor, как в сайдбаре). */
export const Ic = ({ children, size = 16 }: { children: React.ReactNode; size?: number }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round"
        style={{ flexShrink: 0, verticalAlign: '-0.15em' }}>{children}</svg>
);

type P = { size?: number };
export const IcMegaphone = (p: P) => <Ic {...p}><path d="m3 11 18-5v12L3 14v-3z" /><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6" /></Ic>;
export const IcClusters = (p: P) => <Ic {...p}><path d="M5.5 8.5 9 12l-3.5 3.5L2 12z" /><path d="m12 2 3.5 3.5L12 9 8.5 5.5z" /><path d="M18.5 8.5 22 12l-3.5 3.5L15 12z" /><path d="m12 15 3.5 3.5L12 22l-3.5-3.5z" /></Ic>;
export const IcList = (p: P) => <Ic {...p}><path d="M8 6h13" /><path d="M8 12h13" /><path d="M8 18h13" /><path d="M3 6h.01" /><path d="M3 12h.01" /><path d="M3 18h.01" /></Ic>;
export const IcChart = (p: P) => <Ic {...p}><line x1="12" x2="12" y1="20" y2="10" /><line x1="18" x2="18" y1="20" y2="4" /><line x1="6" x2="6" y1="20" y2="16" /></Ic>;
export const IcFlame = (p: P) => <Ic {...p}><path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z" /></Ic>;
export const IcMoon = (p: P) => <Ic {...p}><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" /></Ic>;
export const IcBan = (p: P) => <Ic {...p}><circle cx="12" cy="12" r="10" /><path d="m4.9 4.9 14.2 14.2" /></Ic>;
export const IcHourglass = (p: P) => <Ic {...p}><path d="M5 22h14" /><path d="M5 2h14" /><path d="M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22" /><path d="M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2" /></Ic>;
export const IcRefresh = (p: P) => <Ic {...p}><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" /><path d="M21 3v5h-5" /><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" /><path d="M8 16H3v5" /></Ic>;
export const IcDownload = (p: P) => <Ic {...p}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" x2="12" y1="15" y2="3" /></Ic>;
export const IcSliders = (p: P) => <Ic {...p}><line x1="4" x2="4" y1="21" y2="14" /><line x1="4" x2="4" y1="10" y2="3" /><line x1="12" x2="12" y1="21" y2="12" /><line x1="12" x2="12" y1="8" y2="3" /><line x1="20" x2="20" y1="21" y2="16" /><line x1="20" x2="20" y1="12" y2="3" /><line x1="2" x2="6" y1="14" y2="14" /><line x1="10" x2="14" y1="8" y2="8" /><line x1="18" x2="22" y1="16" y2="16" /></Ic>;
export const IcColumns = (p: P) => <Ic {...p}><rect width="18" height="18" x="3" y="3" rx="2" /><path d="M9 3v18" /><path d="M15 3v18" /></Ic>;
export const IcPause = (p: P) => <Ic {...p}><rect x="6" y="4" width="4" height="16" rx="1" /><rect x="14" y="4" width="4" height="16" rx="1" /></Ic>;
export const IcPlay = (p: P) => <Ic {...p}><polygon points="6 3 20 12 6 21 6 3" /></Ic>;
export const IcClock = (p: P) => <Ic {...p}><circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" /></Ic>;
export const IcGear = (p: P) => <Ic {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></Ic>;
export const IcHistory = (p: P) => <Ic {...p}><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" /><path d="M3 3v5h5" /><path d="M12 7v5l4 2" /></Ic>;
export const IcExternal = (p: P) => <Ic {...p}><path d="M15 3h6v6" /><path d="M10 14 21 3" /><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /></Ic>;
export const IcCopy = (p: P) => <Ic {...p}><rect width="14" height="14" x="8" y="8" rx="2" ry="2" /><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" /></Ic>;
export const IcSearch = (p: P) => <Ic {...p}><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /></Ic>;
export const IcCalendar = (p: P) => <Ic {...p}><path d="M8 2v4" /><path d="M16 2v4" /><rect width="18" height="18" x="3" y="4" rx="2" /><path d="M3 10h18" /></Ic>;
export const IcX = (p: P) => <Ic {...p}><path d="M18 6 6 18" /><path d="m6 6 12 12" /></Ic>;
export const IcPencil = (p: P) => <Ic {...p}><path d="M12 20h9" /><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" /></Ic>;
export const IcCheck = (p: P) => <Ic {...p}><path d="M20 6 9 17l-5-5" /></Ic>;
export const IcCheckCircle = (p: P) => <Ic {...p}><circle cx="12" cy="12" r="10" /><path d="m9 12 2 2 4-4" /></Ic>;
export const IcXCircle = (p: P) => <Ic {...p}><circle cx="12" cy="12" r="10" /><path d="m15 9-6 6" /><path d="m9 9 6 6" /></Ic>;
export const IcAlertTriangle = (p: P) => <Ic {...p}><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><line x1="12" x2="12" y1="9" y2="13" /><line x1="12" x2="12.01" y1="17" y2="17" /></Ic>;
export const IcWallet = (p: P) => <Ic {...p}><path d="M19 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0 0 4h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5" /><path d="M16 12h.01" /></Ic>;
export const IcPlus = (p: P) => <Ic {...p}><path d="M12 5v14" /><path d="M5 12h14" /></Ic>;
export const IcSun = (p: P) => <Ic {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v2" /><path d="M12 20v2" /><path d="m4.93 4.93 1.41 1.41" /><path d="m17.66 17.66 1.41 1.41" /><path d="M2 12h2" /><path d="M20 12h2" /><path d="m6.34 17.66-1.41 1.41" /><path d="m19.07 4.93-1.41 1.41" /></Ic>;
