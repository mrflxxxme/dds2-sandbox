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
const cache = new Map<number, Promise<DesignBoardPermissions>>();

export function resetDesignBoardPermissionsCache(): void {
    cache.clear();
}

export function useDesignBoardPermissions(): DesignBoardPermissions {
    const [perms, setPerms] = useState<DesignBoardPermissions>(NONE);

    useEffect(() => {
        let alive = true;
        // Кэш ключуется проектом: без этого переход между проектами клиентской
        // навигацией показал бы права предыдущего (сегодня спасает только жёсткая
        // перезагрузка при смене проекта — на такой инвариант полагаться нельзя).
        const projectId = api.getProjectId();
        if (projectId == null) return;
        let pending = cache.get(projectId);
        if (!pending) {
            pending = api.getDesignBoard().then((r) => r.permissions);
            // Провалившийся промис нельзя оставлять в кэше: следующая страница
            // получила бы ту же ошибку навсегда.
            pending.catch(() => cache.delete(projectId));
            cache.set(projectId, pending);
        }
        pending
            .then((p) => { if (alive) setPerms(p); })
            .catch(() => { /* флаги остаются false — вкладки просто нет */ });
        return () => { alive = false; };
    }, []);

    return perms;
}
