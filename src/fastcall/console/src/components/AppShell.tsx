import { ReactNode } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';

function Icon({ name }: { name: 'tasks' | 'reports' | 'apps' | 'mock' }) {
  if (name === 'tasks') return <svg viewBox="0 0 24 24" fill="none"><rect x="5" y="3.5" width="14" height="17" rx="2" stroke="currentColor" strokeWidth="1.7"/><path d="M9 8h6M9 12h6M9 16h4" stroke="currentColor" strokeWidth="1.7"/></svg>;
  if (name === 'reports') return <svg viewBox="0 0 24 24" fill="none"><path d="M4 19V5M4 19h16M7 15l4-4 3 2 5-6" stroke="currentColor" strokeWidth="1.7"/></svg>;
  if (name === 'mock') return <svg viewBox="0 0 24 24" fill="none"><path d="M8 4H6a2 2 0 0 0-2 2c0 7.7 6.3 14 14 14a2 2 0 0 0 2-2v-2l-4-1-1.5 2a11 11 0 0 1-6.5-6.5L10 9 8 4Z" stroke="currentColor" strokeWidth="1.7"/></svg>;
  return <svg viewBox="0 0 24 24" fill="none"><rect x="4" y="4" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.7"/><rect x="14" y="4" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.7"/><rect x="4" y="14" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.7"/><rect x="14" y="14" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.7"/></svg>;
}

function breadcrumb(pathname: string) {
  if (pathname === '/mock-calls') return ['技术观测', 'Mock Call'];
  if (pathname.startsWith('/reports/agent')) return ['技术观测', '通话技术分析'];
  if (pathname.startsWith('/apps/')) return ['Robot 应用', '编辑配置'];
  if (pathname === '/apps') return ['机器人配置', 'Robot 应用'];
  if (pathname.startsWith('/calls/')) return ['外呼任务', 'Call 详情'];
  if (pathname.startsWith('/tasks/')) return ['外呼任务', '任务详情'];
  return ['外呼运营', '外呼任务'];
}

export default function AppShell({ children, actorId, mockCallsEnabled = true }: { children: ReactNode; actorId: string; mockCallsEnabled?: boolean }) {
  const location = useLocation();
  const crumbs = breadcrumb(location.pathname);
  const navClass = ({ isActive }: { isActive: boolean }) => `nav-item ${isActive ? 'active' : ''}`;
  return <div className="shell">
    <aside className="sidebar" aria-label="主导航">
      <Link to="/tasks" className="brand"><span className="brand-mark">F</span><span><span className="brand-name">Fastcall</span><span className="brand-subtitle">Console</span></span></Link>
      <nav className="nav">
        <div className="nav-section"><div className="nav-label">外呼运营</div><NavLink className={navClass} to="/tasks"><Icon name="tasks" />外呼任务</NavLink></div>
        <div className="nav-section"><div className="nav-label">技术观测</div><NavLink className={navClass} to="/reports/agent"><Icon name="reports" />通话技术分析</NavLink>{mockCallsEnabled && <NavLink className={navClass} to="/mock-calls"><Icon name="mock" />Mock Call</NavLink>}</div>
        <div className="nav-section"><div className="nav-label">机器人配置</div><NavLink className={navClass} to="/apps"><Icon name="apps" />Robot 应用</NavLink></div>
      </nav>
      <div className="sidebar-foot"><span className="avatar">{actorId.slice(0, 1).toUpperCase()}</span><div><div className="workspace-name">{actorId}</div><div className="workspace-role">Fastcall Workspace</div></div></div>
    </aside>
    <main className="main">
      <header className="topbar">{crumbs.map((crumb, index) => <span key={crumb} className="breadcrumb-item">{index > 0 && <span className="separator">/</span>}{index === crumbs.length - 1 ? <strong>{crumb}</strong> : crumb}</span>)}<span className="topbar-right">Console</span></header>
      {children}
    </main>
  </div>;
}
