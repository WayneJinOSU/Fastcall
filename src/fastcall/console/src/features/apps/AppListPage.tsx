import { FormEvent, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { RobotApp } from '../../api/types';
import { createApp, listApps, setAppEnabled } from './api';

function shortId(value: string) {
  return `${value.slice(0, 12)}…`;
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value));
}

export default function AppListPage() {
  const navigate = useNavigate();
  const [apps, setApps] = useState<RobotApp[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('all');
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function load() {
    setLoading(true);
    setError('');
    try {
      const response = await listApps();
      setApps(response.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : '应用加载失败');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  const filtered = useMemo(() => apps.filter(app => {
    const matchesSearch = `${app.name} ${app.app_code}`.toLowerCase().includes(search.toLowerCase());
    const matchesStatus = status === 'all'
      || (status === 'enabled' && app.enabled)
      || (status === 'disabled' && !app.enabled)
      || (status === 'unpublished' && !app.current_release);
    return matchesSearch && matchesStatus;
  }), [apps, search, status]);

  async function toggle(app: RobotApp) {
    const action = app.enabled ? '停用' : '启用';
    if (!window.confirm(`${action}“${app.name}”？${app.enabled ? '停用后新的 Resolve 将失败，进行中的通话不受影响。' : ''}`)) return;
    setSubmitting(true);
    try {
      const updated = await setAppEnabled(app.app_id, !app.enabled);
      setApps(items => items.map(item => item.app_id === app.app_id ? updated : item));
    } catch (err) {
      setError(err instanceof Error ? err.message : `${action}失败`);
    } finally {
      setSubmitting(false);
    }
  }

  async function submitCreate(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      const app = await createApp(name);
      navigate(`/apps/${app.app_id}/edit`);
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建失败');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="page">
      <section className="heading">
        <div><h1>Robot 应用</h1><p className="description">管理应用草稿、发布版本与启停状态。</p></div>
        <div className="heading-actions">
          <input className="search-input" type="search" aria-label="搜索应用" placeholder="搜索名称或 app_code" value={search} onChange={event => setSearch(event.target.value)} />
          <select className="select-control" aria-label="状态筛选" value={status} onChange={event => setStatus(event.target.value)}><option value="all">全部状态</option><option value="enabled">已启用</option><option value="disabled">已停用</option><option value="unpublished">未发布</option></select>
          <button className="button primary" onClick={() => setCreateOpen(true)}>＋ 新建应用</button>
        </div>
      </section>
      {error && <div className="alert error" role="alert">{error}<button onClick={() => setError('')}>×</button></div>}
      <section className="summary" aria-label="应用概览">
        <div><div className="summary-title">应用概览</div><div className="summary-caption">基于当前应用列表</div></div>
        <Metric value={apps.length} label="全部应用" />
        <Metric value={apps.filter(app => app.enabled).length} label="已启用" tone="green" />
        <Metric value={apps.filter(app => !app.enabled).length} label="已停用" />
        <Metric value={apps.filter(app => !app.current_release).length} label="未发布" tone="amber" />
      </section>
      <section>
        <div className="list-heading"><div className="list-title">全部应用<span className="list-count">{filtered.length}</span></div><div className="list-note">启停变更仅影响新的 Resolve 请求</div></div>
        {loading ? <div className="empty-state">正在加载应用…</div> : filtered.length === 0 ? <div className="empty-state">暂无匹配的应用</div> : (
          <div className="app-grid">
            {filtered.map(app => (
              <article className="app-card" key={app.app_id}>
                <div className="card-heading"><span className="app-icon">{app.name.slice(0, 1).toUpperCase()}</span><div className="app-title"><div className="app-name">{app.name}</div><div className="app-code">{app.app_code}</div></div><span className={`release-badge ${app.current_release ? 'published' : ''}`}>{app.current_release ? '已发布' : '未发布'}</span></div>
                <div className="facts">
                  <div><div className="fact-label">Release</div><div className="fact-value">{app.current_release ? shortId(app.current_release.robot_app_version_id) : '暂无 Release'}</div></div>
                  <div><div className="fact-label">启停状态</div><div className="switch-wrap"><button className={`toggle ${app.enabled ? 'on' : ''}`} role="switch" aria-checked={app.enabled} aria-label={`${app.enabled ? '停用' : '启用'}${app.name}`} disabled={submitting} onClick={() => void toggle(app)} /><span className={app.enabled ? 'status-enabled' : ''}>{app.enabled ? '已启用' : '已停用'}</span></div></div>
                  <div className="fact-time"><div className="fact-label">更新时间</div><div className="fact-value">{formatTime(app.updated_at)}</div></div>
                </div>
                <div className="card-footer"><button className="action primary-link" onClick={() => navigate(`/apps/${app.app_id}/edit`)}>编辑配置</button><span className="revision">草稿 r{app.draft_revision}</span></div>
              </article>
            ))}
          </div>
        )}
      </section>
      {createOpen && <div className="modal-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setCreateOpen(false); }}><form className="modal" onSubmit={submitCreate}><div className="modal-head"><h2>新建应用</h2><button type="button" className="icon-close" onClick={() => setCreateOpen(false)}>×</button></div><p className="modal-description">只需填写名称，系统会自动生成稳定的应用代码。</p><label>应用名称<input value={name} onChange={event => setName(event.target.value)} placeholder="Standard English" required /></label><div className="modal-actions"><button type="button" className="button" onClick={() => setCreateOpen(false)}>取消</button><button className="button primary" disabled={submitting}>{submitting ? '创建中…' : '创建并编辑'}</button></div></form></div>}
    </div>
  );
}

function Metric({ value, label, tone = '' }: { value: number; label: string; tone?: string }) {
  return <div className="metric"><div className="metric-value">{value}</div><div className={`metric-label ${tone}`}>{label}</div></div>;
}
