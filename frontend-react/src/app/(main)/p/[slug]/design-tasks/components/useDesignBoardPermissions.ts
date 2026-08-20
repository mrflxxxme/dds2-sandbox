'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import type { DesignBoardPermissions } from '@/types/api';

const NONE: DesignBoardPermissions = {
    can_create: false,
    can_reorder: false,
    can_manage_refs: false,
};

/**
 * Права уровня доски для страниц, которые сами доску не грузят (календарь,
 * загрузка, настройки). Нужны ради вкладки «Настройки»: её видимость решает
 * бэк флагом can_manage_refs (§6.9), а не роль на фронте.
 *
 * Ответ кэшируется на время жизни вкладки браузера: без кэша каждая страница
 * модуля делала бы лишний GET /board только ради двух булевых полей. Сбоя не
 * маскируем — при ошибке флаги остаются false и вкладки просто нет.
 */
let cached: Promise<DesignBoardPermissions> | null = null;

export function resetDesignBoardPermissionsCache(): void {
    cached = null;
}

export function useDesignBoardPermissions(): DesignBoardPermissions {
    const [perms, setPerms] = useState<DesignBoardPermissions>(NONE);

    useEffect(() => {
        let alive = true;
        if (!cached) {
            cached = api.getDesignBoard().then((r) => r.permissions);
            // Провалившийся промис нельзя оставлять в кэше: следующая страница
            // получила бы ту же ошибку навсегда.
            cached.catch(() => { cached = null; });
        }
        cached
            .then((p) => { if (alive) setPerms(p); })
            .catch(() => { /* флаги остаются false — вкладки просто нет */ });
        return () => { alive = false; };
    }, []);

    return perms;
}
