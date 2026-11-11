/**
 * Root Route - App Shell
 */

import { createRootRoute, Outlet, Link, useLocation } from '@tanstack/react-router';
import { TanStackRouterDevtools } from '@tanstack/router-devtools';
import { cn } from '@/lib/utils';
import { Home, Bot, Building2, FolderOpen, FileQuestion, ClipboardCheck, FileText, UserCircle, UsersRound } from 'lucide-react';

export const Route = createRootRoute({
  component: RootComponent,
});

function RootComponent() {
  return (
    <div className="min-h-screen bg-background">
      <Header />
      <div className="flex">
        <Sidebar />
        <main className="flex-1 p-6">
          <Outlet />
        </main>
      </div>
      {import.meta.env.DEV && (
        <TanStackRouterDevtools position="bottom-right" />
      )}
    </div>
  );
}

function Header() {
  return (
    <header className="h-16 border-b bg-card flex items-center px-6">
      <h1 className="text-xl font-semibold">JSON UI Engine</h1>
    </header>
  );
}

function NavLink({ to, icon: Icon, label }: { to: string; icon: React.ComponentType<{ className?: string }>; label: string }) {
  const location = useLocation();
  const isActive = location.pathname.includes(`/entities/${to}`);

  return (
    <Link
      to="/entities/$entityType"
      params={{ entityType: to }}
      className={cn(
        'flex items-center gap-3 px-3 py-2 rounded-lg transition-colors',
        isActive
          ? 'bg-primary text-primary-foreground'
          : 'hover:bg-muted'
      )}
    >
      <Icon className="h-4 w-4" />
      {label}
    </Link>
  );
}

function Sidebar() {
  const location = useLocation();

  return (
    <aside className="w-64 border-r bg-card min-h-[calc(100vh-4rem)]">
      <nav className="p-4 space-y-2">
        <Link
          to="/"
          className={cn(
            'flex items-center gap-3 px-3 py-2 rounded-lg transition-colors',
            location.pathname === '/'
              ? 'bg-primary text-primary-foreground'
              : 'hover:bg-muted'
          )}
        >
          <Home className="h-4 w-4" />
          Dashboard
        </Link>

        <Link
          to="/agent"
          className={cn(
            'flex items-center gap-3 px-3 py-2 rounded-lg transition-colors',
            location.pathname === '/agent'
              ? 'bg-primary text-primary-foreground'
              : 'hover:bg-muted'
          )}
        >
          <Bot className="h-4 w-4" />
          Agent
        </Link>

        <div className="pt-4">
          <h3 className="px-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            Entities
          </h3>
          <div className="mt-2 space-y-1">
            <NavLink to="portfolio" icon={FolderOpen} label="Portfolios" />
            <NavLink to="group" icon={UsersRound} label="Groups" />
            <NavLink to="vbu" icon={Building2} label="VBUs" />
            <NavLink to="assessment" icon={ClipboardCheck} label="Assessments" />
            <NavLink to="question" icon={FileQuestion} label="Questions" />
            <NavLink to="person" icon={UserCircle} label="People" />
            <NavLink to="evidence" icon={FileText} label="Evidence" />
          </div>
        </div>
      </nav>
    </aside>
  );
}
