import { ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';

export default function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const editing = location.pathname.includes('/edit');
  return (
    <div className="shell">
      <aside className="sidebar" aria-label="主导航">
        <Link to="/apps" className="brand">
          <span className="brand-mark">R</span>
          <span><span className="brand-name">Robot Config</span><span className="brand-subtitle">Console</span></span>
        </Link>
        <nav className="nav">
          <div className="nav-label">配置管理</div>
          <Link className="nav-item active" to="/apps" aria-current="page">
            <svg viewBox="0 0 24 24" fill="none"><rect x="4" y="4" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.7"/><rect x="14" y="4" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.7"/><rect x="4" y="14" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.7"/><rect x="14" y="14" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.7"/></svg>
            应用
          </Link>
        </nav>
      </aside>
      <main className="main">
        <header className="topbar"><span>Robot Config</span><span className="separator">/</span><span>应用</span>{editing && <><span className="separator">/</span><strong>编辑配置</strong></>}</header>
        {children}
      </main>
    </div>
  );
}
