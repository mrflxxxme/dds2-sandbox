/** Projects & Team API methods */
import { ApiClient } from './client';
import type { Project, ProjectInvite, ProjectMember, MyPermissions, MessageResponse } from '@/types/api';

export function addProjectMethods(api: ApiClient) {
    return {
        getProjects() {
            return api.request<Array<{
                id: number; name: string; slug: string; owner_id: number;
                created_at: string; members_count: number;
            }>>('GET', '/api/v1/projects');
        },
        getProject(slug: string) {
            return api.request<{
                id: number; name: string; slug: string; owner_id: number;
                created_at: string;
            }>('GET', `/api/v1/projects/${slug}`);
        },
        createProject(name: string) {
            return api.request<Project>('POST', '/api/v1/projects', { name });
        },
        deleteProject(slug: string) {
            return api.request<MessageResponse>('DELETE', `/api/v1/projects/${slug}`);
        },
        getMembers(slug: string) {
            return api.request<Array<{
                id: number; user_id: number; username: string;
                email: string | null; first_name: string | null;
                last_name: string | null; joined_at: string;
            }>>('GET', `/api/v1/projects/${slug}/members`);
        },
        inviteByEmail(slug: string, email: string) {
            return api.request<ProjectInvite>('POST', `/api/v1/projects/${slug}/invite?email=${encodeURIComponent(email)}`);
        },
        getInviteLink(slug: string) {
            return api.request<{ invite_token: string; link: string }>('GET', `/api/v1/projects/${slug}/invite-link`);
        },
        getInvites(slug: string) {
            return api.request<Array<{
                id: number; email: string | null; invite_token: string;
                status: string; created_at: string; accepted_at: string | null;
            }>>('GET', `/api/v1/projects/${slug}/invites`);
        },
        removeMember(slug: string, userId: number) {
            return api.request<MessageResponse>('DELETE', `/api/v1/projects/${slug}/members/${userId}`);
        },
        acceptInvite(token: string) {
            return api.request<MessageResponse>('POST', `/api/v1/projects/invite/accept/${token}`);
        },
        getMyPermissions(slug: string) {
            return api.request<MyPermissions>('GET', `/api/v1/projects/${slug}/my-permissions`);
        },
        updateMemberRole(slug: string, userId: number, role: string, pages: string[]) {
            return api.request<MessageResponse>('PUT', `/api/v1/projects/${slug}/members/${userId}/role`, { role, pages });
        },
    };
}
