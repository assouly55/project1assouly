import { ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Database, Search, Play, Settings, FileText, Activity } from 'lucide-react';
import { cn } from '@/lib/utils';

interface NavItemProps {
  to: string;
  icon: ReactNode;
  label: string;
  isActive: boolean;
}

const NavItem = ({ to, icon, label, isActive }: NavItemProps) => (
  <Link
    to={to}
    className={cn(
      "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors",
      isActive 
        ? "bg-primary/10 text-primary" 
        : "text-muted-foreground hover:text-foreground hover:bg-muted"
    )}
  >
    {icon}
    <span>{label}</span>
  </Link>
);

interface AppLayoutProps {
  children: ReactNode;
}

export function AppLayout({ children }: AppLayoutProps) {
  const location = useLocation();

  const navItems = [
    { to: '/', icon: <Search className="w-4 h-4" />, label: 'Tenders' },
    { to: '/scraper', icon: <Play className="w-4 h-4" />, label: 'Scraper' },
    { to: '/logs', icon: <Activity className="w-4 h-4" />, label: 'Logs' },
    { to: '/docs', icon: <FileText className="w-4 h-4" />, label: 'API Docs' },
  ];

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="container flex h-14 items-center justify-between">
          <div className="flex items-center gap-6">
            {/* Logo */}
            <Link to="/" className="flex items-center gap-2">
              <div className="flex items-center justify-center w-8 h-8 rounded bg-primary/10">
                <Database className="w-4 h-4 text-primary" />
              </div>
              <span className="font-semibold">Tender AI</span>
              <span className="text-xs text-muted-foreground font-mono bg-muted px-1.5 py-0.5 rounded">v1</span>
            </Link>

            {/* Nav */}
            <nav className="hidden md:flex items-center gap-1">
              {navItems.map((item) => (
                <NavItem
                  key={item.to}
                  {...item}
                  isActive={location.pathname === item.to}
                />
              ))}
            </nav>
          </div>

          {/* Status indicator */}
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 text-sm">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-success"></span>
              </span>
              <span className="text-muted-foreground font-mono text-xs">Backend: localhost:8000</span>
            </div>
            <Link 
              to="/settings" 
              className="p-2 rounded-md hover:bg-muted transition-colors"
            >
              <Settings className="w-4 h-4 text-muted-foreground" />
            </Link>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="container py-6">
        {children}
      </main>
    </div>
  );
}
