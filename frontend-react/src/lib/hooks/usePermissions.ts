'use client';
import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import type { MyPermissions } from '@/types/api';

interface UsePermissionsResult {
    role: MyPermissions['role'] | null;
    pages: string[];
    isOwner: boolean;
    canAccess: (page: string) => boolean;
    canEdit: () => boolean;
    canManage: () => boolean;
    loading: boolean;
}

let permissionsCache: Record<string, MyPermissions> = {};

export function usePermissions(): UsePermissionsResult {
    const params = useParams();
    const slug = params?.slug as string;
    const [permissions, setPermissions] = useState<MyPermissions | null>(
        slug ? permissionsCache[slug] ?? null : null
    );
    const [loading, setLoading] = useState(!permissions);

    useEffect(() => {
        if (!slug) return;
        if (permissionsCache[slug]) {
            setPermissions(permissionsCache[slug]);
            setLoading(false);
            return;
        }
        let cancelled = false;
        api.getMyPermissions(slug)
            .then(data => {
                if (cancelled) return;
                permissionsCache[slug] = data;
                setPermissions(data);
            })
            .catch(() => {
                // graceful degradation: assume viewer if endpoint not available
                if (cancelled) return;
                const fallback: MyPermissions = { role: 'viewer', pages: [], is_owner: false };
                setPermissions(fallback);
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });
        return () => { cancelled = true; };
    }, [slug]);

    const canAccess = (page: string): boolean => {
        if (!permissions) return false;
        if (permissions.role === 'owner' || permissions.role === 'admin') return true;
        return permissions.pages.includes(page);
    };

    const canEdit = (): boolean => {
        if (!permissions) return false;
        return permissions.role !== 'viewer';
    };

    const canManage = (): boolean => {
        if (!permissions) return false;
        return permissions.role === 'owner' || permissions.role === 'admin';
    };

    return {
        role: permissions?.role ?? null,
        pages: permissions?.pages ?? [],
        isOwner: permissions?.is_owner ?? false,
        canAccess,
        canEdit,
        canManage,
        loading,
    };
}

/** Call to clear cached permissions (e.g., after role change) */
export function invalidatePermissionsCache(slug?: string) {
    if (slug) {
        delete permissionsCache[slug];
    } else {
        permissionsCache = {};
    }
}
