import { FormEvent, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import EmptyState from '../../components/ui/EmptyState';
import ErrorState from '../../components/ui/ErrorState';
import Pagination from '../../components/ui/Pagination';
import { formatDateTime, shortId } from '../../utils/datetime';
import { formatNumber } from '../../utils/numbers';
import { listApps } from '../apps/api';
import { RobotApp } from '../../api/types';
import { listTasks } from './api';
import { OutboundTask, TaskListResponse, TaskStatus } from './types';
import './tasks.css';

const statusLabels: Record<TaskStatus, string> = {
  draft: '草稿', scheduled: '已计划', running: '运行中', paused: '已暂停', completed: '已完成', cancelled: '已取消',
};

export function TaskStatusLabel({ status }: { status: TaskStatus }) {
  return <span className={`tasks-status tasks-status-${status}`}>{statusLabels[status] || status}</span>;
}

function progress(task: OutboundTask) {
  const stats = task.contact_statistics;
  const total = Object.values(stats).reduce((sum, value) => sum + value, 0);
  const finished = ['succeeded', 'failed', 'unknown', 'cancelled'].reduce((sum, key) => sum + (stats[key] || 0), 0);
  return { total, finished, percent: total ? Math.round(finished / total * 100) : 0 };
}

export default function TaskListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [result, setResult] = useState<TaskListResponse | null>(null);
  const [apps, setApps] = useState<RobotApp[]>([]);
  const [search, setSearch] = useState(searchParams.get('search') || '');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [previousCursors, setPreviousCursors] = useState<string[]>([]);
  const queryKey = searchParams.toString();

  useEffect(() => setSearch(searchParams.get('search') || ''), [queryKey]);
  useEffect(() => {
    listApps().then(value => setApps(value.items)).catch(() => setApps([]));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setResult(null);
    setLoading(true);
    setError('');
    const values = Object.fromEntries(searchParams.entries());
    listTasks({ ...values, limit: 20 }, controller.signal)
      .then(setResult)
      .catch(err => {
        if (err.name !== 'AbortError') setError(err instanceof Error ? err.message : '任务加载失败');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [queryKey, reload]);

  function changeFilters(values: Record<string, string>) {
    const next = new URLSearchParams(searchParams);
    next.delete('cursor');
    Object.entries(values).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
    setPreviousCursors([]);
    setSearchParams(next);
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    changeFilters({ search: search.trim() });
  }

  function nextPage() {
    if (!result?.next_cursor) return;
    setPreviousCursors(stack => [...stack, searchParams.get('cursor') || '']);
    const next = new URLSearchParams(searchParams);
    next.set('cursor', result.next_cursor);
    setSearchParams(next);
  }

  function previousPage() {
    const target = previousCursors[previousCursors.length - 1];
    if (target === undefined) return;
    setPreviousCursors(stack => stack.slice(0, -1));
    const next = new URLSearchParams(searchParams);
    if (target) next.set('cursor', target); else next.delete('cursor');
    setSearchParams(next);
  }

  const tasks = result?.items || [];
  return <div className="page tasks-page">
    <section className="heading"><div><h1>外呼任务</h1><p className="description">监控任务执行进度、联系人状态与每一次呼叫。</p></div><div className="heading-actions"><button className="button" aria-label="刷新任务" onClick={() => setReload(value => value + 1)}>刷新</button></div></section>
    <form className="tasks-filterbar" onSubmit={submitSearch}>
      <input className="search-input" type="search" aria-label="搜索任务" placeholder="搜索任务名称或客户任务 ID" value={search} onChange={event => setSearch(event.target.value)} />
      <select className="select-control" aria-label="任务状态" value={searchParams.get('status') || ''} onChange={event => changeFilters({ status: event.target.value })}><option value="">全部状态</option>{Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
      <select className="select-control" aria-label="Robot 筛选" value={searchParams.get('robot_app_id') || ''} onChange={event => changeFilters({ robot_app_id: event.target.value })}><option value="">全部 Robot</option>{apps.map(app => <option key={app.app_id} value={app.app_id}>{app.name}</option>)}</select>
      <button className="button" type="submit">查询</button>
      <span className="filter-note">筛选条件将随分页保持</span>
    </form>
    {error && <ErrorState message={error} onRetry={() => setReload(value => value + 1)} />}
    <section className="panel tasks-panel">
      <div className="panel-head"><span className="panel-title">任务列表</span><span className="count">{tasks.length}</span><span className="panel-note">按创建时间倒序</span></div>
      {loading ? <EmptyState>正在加载任务…</EmptyState> : tasks.length === 0 ? <EmptyState>暂无匹配的外呼任务</EmptyState> : <div className="table-scroll"><table className="data-table tasks-table"><thead><tr><th>任务</th><th>状态</th><th>联系人进度</th><th className="number">活动 Call</th><th className="number">UNKNOWN</th><th>Robot</th><th>开始时间</th><th>操作</th></tr></thead><tbody>{tasks.map(task => {
        const value = progress(task);
        return <tr key={task.task_id}><td><Link className="primary-cell tasks-link" to={`/tasks/${task.task_id}`}>{task.name}</Link><div className="secondary-cell mono">{task.customer_task_id}</div></td><td><TaskStatusLabel status={task.status} /></td><td><div>{formatNumber(value.finished)} / {formatNumber(value.total)}</div><div className="progress-track" aria-label={`完成 ${value.percent}%`}><span style={{ width: `${value.percent}%` }} /></div></td><td className="number">{task.active_call_count}</td><td className={`number ${task.unknown_call_count ? 'warn' : ''}`}>{task.unknown_call_count || '—'}</td><td><div>{task.robot_name || '—'}</div><div className="secondary-cell mono">{shortId(task.robot_version_id, 10)}</div></td><td><div>{formatDateTime(task.started_at || task.scheduled_start_at)}</div><div className="secondary-cell">{task.timezone}</div></td><td><Link className="link" to={`/tasks/${task.task_id}`}>查看</Link></td></tr>;
      })}</tbody></table></div>}
      <Pagination note="每页 20 条 · 使用稳定游标分页" hasPrevious={previousCursors.length > 0} hasNext={Boolean(result?.next_cursor)} onPrevious={previousPage} onNext={nextPage} />
    </section>
  </div>;
}
