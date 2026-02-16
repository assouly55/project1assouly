import { Navigate } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import { Loader2 } from 'lucide-react';

interface ProtectedRouteProps {
  children: React.ReactNode;
  requiredType: 'admin' | 'client';
  loginPath: string;
}

export function ProtectedRoute({ children, requiredType, loginPath }: ProtectedRouteProps) {
  const { user, userType, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  if (!user || userType !== requiredType) {
    return <Navigate to={loginPath} replace />;
  }

  return <>{children}</>;
}
