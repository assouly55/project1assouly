import { ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Database, FileText, User, LogOut, Search } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';

const navItems = [
  { to: '/client/tenders', icon: Search, label: 'Appels d\'Offres' },
  { to: '/client/profile', icon: User, label: 'Profil' },
];

export function ClientLayout({ children }: { children: ReactNode }) {
  const location = useLocation();
  const { user, logout } = useAuth();

  return (
    <div className="min-h-screen bg-background flex">
      {/* Sidebar */}
      <aside className="w-60 border-r border-border bg-sidebar flex flex-col">
        <div className="p-4 border-b border-sidebar-border">
          <Link to="/client/tenders" className="flex items-center gap-2">
            <div className="flex items-center justify-center w-8 h-8 rounded bg-primary/10">
              <Database className="w-4 h-4 text-primary" />
            </div>
            <div>
              <span className="font-semibold text-sm">Tender AI</span>
              <span className="text-[10px] text-muted-foreground font-mono bg-primary/20 text-primary px-1.5 py-0.5 rounded ml-1.5">Client</span>
            </div>
          </Link>
        </div>

        <nav className="flex-1 p-3 space-y-1">
          {navItems.map((item) => {
            const isActive = location.pathname.startsWith(item.to);
            return (
              <Link
                key={item.to}
                to={item.to}
                className={cn(
                  "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors",
                  isActive
                    ? "bg-sidebar-primary/10 text-sidebar-primary"
                    : "text-sidebar-foreground hover:text-foreground hover:bg-sidebar-accent"
                )}
              >
                <item.icon className="w-4 h-4" />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="p-3 border-t border-sidebar-border space-y-2">
          <div className="px-3 py-2">
            <div className="text-sm font-medium truncate">{user?.company_name || user?.contact_name || 'Client'}</div>
            <div className="text-xs text-muted-foreground truncate">{user?.email}</div>
          </div>
          <Button variant="ghost" size="sm" className="w-full justify-start text-muted-foreground" onClick={logout}>
            <LogOut className="w-4 h-4 mr-2" />
            Déconnexion
          </Button>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <div className="container py-6 max-w-7xl">
          {children}
        </div>
      </main>
    </div>
  );
}
