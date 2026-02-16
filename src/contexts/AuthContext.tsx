import { createContext, useContext, useState, useEffect, ReactNode, useCallback } from 'react';
import { authApi, type AuthUser } from '@/lib/auth';

type UserType = 'admin' | 'client';

interface AuthState {
  user: AuthUser | null;
  token: string | null;
  userType: UserType | null;
  isLoading: boolean;
}

interface AuthContextType extends AuthState {
  loginAdmin: (email: string, password: string) => Promise<{ success: boolean; error?: string }>;
  loginClient: (email: string, password: string) => Promise<{ success: boolean; error?: string }>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | null>(null);

function getStorageKey(type: UserType) {
  return `tender_ai_${type}_token`;
}

export function AdminAuthProvider({ children }: { children: ReactNode }) {
  return <AuthProvider userType="admin">{children}</AuthProvider>;
}

export function ClientAuthProvider({ children }: { children: ReactNode }) {
  return <AuthProvider userType="client">{children}</AuthProvider>;
}

function AuthProvider({ children, userType }: { children: ReactNode; userType: UserType }) {
  const [state, setState] = useState<AuthState>({
    user: null,
    token: null,
    userType,
    isLoading: true,
  });

  useEffect(() => {
    const stored = localStorage.getItem(getStorageKey(userType));
    if (stored) {
      try {
        const { token, user } = JSON.parse(stored);
        setState({ user, token, userType, isLoading: false });
      } catch {
        localStorage.removeItem(getStorageKey(userType));
        setState(s => ({ ...s, isLoading: false }));
      }
    } else {
      setState(s => ({ ...s, isLoading: false }));
    }
  }, [userType]);

  const loginAdmin = useCallback(async (email: string, password: string) => {
    const result = await authApi.adminLogin(email, password);
    if (result.success && result.data) {
      const { access_token, user } = result.data;
      localStorage.setItem(getStorageKey('admin'), JSON.stringify({ token: access_token, user }));
      setState({ user, token: access_token, userType: 'admin', isLoading: false });
      return { success: true };
    }
    return { success: false, error: result.error };
  }, []);

  const loginClient = useCallback(async (email: string, password: string) => {
    const result = await authApi.clientLogin(email, password);
    if (result.success && result.data) {
      const { access_token, user } = result.data;
      localStorage.setItem(getStorageKey('client'), JSON.stringify({ token: access_token, user }));
      setState({ user, token: access_token, userType: 'client', isLoading: false });
      return { success: true };
    }
    return { success: false, error: result.error };
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(getStorageKey(userType));
    setState({ user: null, token: null, userType, isLoading: false });
  }, [userType]);

  return (
    <AuthContext.Provider value={{ ...state, loginAdmin, loginClient, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider');
  return ctx;
}
