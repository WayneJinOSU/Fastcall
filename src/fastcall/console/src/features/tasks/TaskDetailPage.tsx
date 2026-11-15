import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import EmptyState from '../../components/ui/EmptyState';
import ErrorState from '../../components/ui/ErrorState';
import { formatDateTime, shortId } from '../../utils/datetime';
import ContactTable from './ContactTable';
import TaskActions from './TaskActions';
import TaskCallTable from './TaskCallTable';
import TaskConfigPanel from './TaskConfigPanel';
import { getTask } from './api';
import { OutboundTask } from './types';
import { TaskStatusLabel } from './TaskListPage';
import TaskStatsStrip from './TaskStatsStrip';
import './tasks.css';

export default function TaskDetailPage() {
  const { taskId = '' } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const [task, setTask] = useState<OutboundTask | null>(null);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const tab = searchParams.get('tab') || 'contacts';

  useEffect(() => {
    const controller = new AbortController();
    setTask(null);
    setLoading(true);
    setError('');
    getTask(taskId, controller.signal).then(setTask).catch(err => {
      if (err.name !== 'AbortError') setError(err instanceof Error ? err.message : '任务加载失败');
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [taskId, reload]);

  function selectTab(value: string) {
    const next = new URLSearchParams(searchParams);
    next.set('tab', value);
    setSearchParams(next);
  }

  if (loading && !task) return <div className="page-loading">正在加载任务详情…</div>;
  if (error && !task) return <div className="page"><ErrorState message={error} onRetry={() => setReload(value => value + 1)} /><Link className="button" to="/tasks">返回任务列表</Link></div>;
  if (!task) return <EmptyState>任务不存在</EmptyState>;

  return <div className="page tasks-page">
    <Link className="back-link" to="/tasks">← 返回任务列表</Link>
    <section className="heading"><div><div className="title-with-status"><h1>{task.name}</h1><TaskStatusLabel status={task.status} /></div><div className="title-meta"><span className="mono">{shortId(task.task_id, 20)}</span> · {task.customer_task_id} · 创建于 {formatDateTime(task.created_at)}</div></div><TaskActions task={task} onUpdated={setTask} onReload={() => setReload(value => value + 1)} onError={setActionError} /></section>
    {(actionError || error) && <ErrorState message={actionError || error} />}
    <TaskStatsStrip task={task} />
    <div className="context-strip"><div className="context-item">Robot<strong>{task.robot_name || '—'}</strong></div><div className="context-item">固定版本<strong className="mono">{shortId(task.robot_version_id, 18)}</strong></div><div className="context-item">并发<strong>{task.max_concurrent_calls}</strong></div><div className="context-item">速率<strong>{task.calls_per_minute} 次/分钟</strong></div><div className="context-item">活动 Call<strong>{task.active_call_count}</strong></div></div>
    <div className="tabs" role="tablist">{[['contacts', '联系人'], ['calls', 'Call 记录'], ['config', '任务配置']].map(([value, label]) => <button key={value} role="tab" aria-selected={tab === value} className={`tab ${tab === value ? 'active' : ''}`} onClick={() => selectTab(value)}>{label}</button>)}</div>
    {tab === 'contacts' && <ContactTable key={`contacts-${task.state_version}`} taskId={taskId} />}
    {tab === 'calls' && <TaskCallTable key={`calls-${task.state_version}`} taskId={taskId} />}
    {tab === 'config' && <TaskConfigPanel task={task} />}
  </div>;
}
