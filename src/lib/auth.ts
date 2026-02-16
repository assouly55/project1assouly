// Tender AI Platform — Auth API Client

import type { ApiResponse } from '@/types/tender';

const API_BASE_URL = import.meta.env.VITE_API_URL || (import.meta.env.DEV ? '' : '');

export interface AuthUser {
  id: string;
  email: string;
  username?: string;
  full_name?: string;
  role?: string;
  company_name?: string;
  contact_name?: string;
  phone?: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: AuthUser;
}

export interface ClientAccount {
  id: string;
  email: string;
  company_name: string | null;
  contact_name: string | null;
  phone: string | null;
  is_active: boolean;
  is_approved: boolean;
  created_at: string;
  last_login: string | null;
}

export interface AdminStats {
  tenders: { total: number; analyzed: number; pending: number; error: number };
  clients: { total: number; active: number; pending_approval: number };
  scraper: { total_jobs: number; completed: number; failed: number; success_rate: number };
  recent_jobs: {
    id: string;
    target_date: string;
    status: string;
    total_found: number;
    downloaded: number;
    failed: number;
    elapsed_seconds: number | null;
    started_at: string | null;
    completed_at: string | null;
  }[];
}

function getAuthHeaders(token: string): HeadersInit {
  return {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
  };
}

async function request<T>(endpoint: string, options: RequestInit = {}): Promise<ApiResponse<T>> {
  try {
    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
    });
    const data = await response.json();
    if (!response.ok) {
      return { success: false, error: data.detail || data.message || `HTTP ${response.status}` };
    }
    return { success: true, data };
  } catch (error) {
    return { success: false, error: error instanceof Error ? error.message : 'Network error' };
  }
}

// ============================
// AUTH ENDPOINTS
// ============================

export const authApi = {
  // Admin
  adminLogin: (email: string, password: string) =>
    request<TokenResponse>('/api/auth/admin/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  adminMe: (token: string) =>
    request<AuthUser>('/api/auth/admin/me', {
      headers: getAuthHeaders(token),
    }),

  seedAdmin: () =>
    request<{ message: string }>('/api/auth/admin/seed', { method: 'POST' }),

  // Client
  clientLogin: (email: string, password: string) =>
    request<TokenResponse>('/api/auth/client/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  clientRegister: (data: { email: string; password: string; company_name?: string; contact_name?: string; phone?: string }) =>
    request<{ message: string; id: string }>('/api/auth/client/register', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  clientMe: (token: string) =>
    request<AuthUser>('/api/auth/client/me', {
      headers: getAuthHeaders(token),
    }),

  clientUpdateProfile: (token: string, data: { company_name?: string; contact_name?: string; phone?: string; email?: string }) =>
    request<AuthUser>('/api/auth/client/profile', {
      method: 'PUT',
      headers: getAuthHeaders(token),
      body: JSON.stringify(data),
    }),

  // Admin: Client Management
  listClients: (token: string) =>
    request<ClientAccount[]>('/api/auth/admin/clients', {
      headers: getAuthHeaders(token),
    }),

  approveClient: (token: string, clientId: string) =>
    request<{ message: string }>(`/api/auth/admin/clients/${clientId}/approve`, {
      method: 'POST',
      headers: getAuthHeaders(token),
    }),

  suspendClient: (token: string, clientId: string) =>
    request<{ message: string }>(`/api/auth/admin/clients/${clientId}/suspend`, {
      method: 'POST',
      headers: getAuthHeaders(token),
    }),

  deleteClient: (token: string, clientId: string) =>
    request<{ message: string }>(`/api/auth/admin/clients/${clientId}`, {
      method: 'DELETE',
      headers: getAuthHeaders(token),
    }),

  createClient: (token: string, data: { email: string; password: string; company_name?: string; contact_name?: string; phone?: string }) =>
    request<{ id: string; email: string }>('/api/auth/admin/clients/create', {
      method: 'POST',
      headers: getAuthHeaders(token),
      body: JSON.stringify(data),
    }),

  // Admin: Stats
  getAdminStats: (token: string) =>
    request<AdminStats>('/api/auth/admin/stats/overview', {
      headers: getAuthHeaders(token),
    }),

  getTendersByDate: (token: string) =>
    request<{ date: string; count: number }[]>('/api/auth/admin/stats/tenders-by-date', {
      headers: getAuthHeaders(token),
    }),

  getTendersByCategory: (token: string) =>
    request<{ category: string; count: number }[]>('/api/auth/admin/stats/categories', {
      headers: getAuthHeaders(token),
    }),
};
