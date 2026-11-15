import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import EmptyState from '../../components/ui/EmptyState';
import ErrorState from '../../components/ui/ErrorState';
import Pagination from '../../components/ui/Pagination';
import { formatDateTime, shortId } from '../../utils/datetime';
import { formatDuration } from '../../utils/numbers';
import { listTaskCalls } from './api';
import { TaskCallListResponse } from './types';

export default function TaskCallTable({ taskId }: { taskId: string }) {
  const [result, setResult] = useState<TaskCallListResponse | null>(null);
  const [cursor, setCursor] = useState('');
  const [previous, setPrevious] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setResult(null);
    setLoading(true);
    setError('');
    listTaskCalls(taskId, { cursor: cursor || undefined, limit: 50 }, controller.signal)
      .then(setResult)
      .catch(err => {
        if (err.name !== 'AbortError') setError(err instanceof Error ? err.message : 'Call 加载失败');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [taskId, cursor, reload]);

  function nextPage() {
    if (!result?.next_cursor) return;
    setPrevious(stack => [...stack, cursor]);
    setCursor(result.next_cursor);
  }

  function previousPage() {
    const target = previous[previous.length - 1];
    if (target === undefined) return;
    setPrevious(stack => stack.slice(0, -1));
    setCursor(target);
  }

  const calls = result?.items || [];
  return <section className="panel tab-panel"><div className="panel-head"><span className="panel-title">Call 记录</span><span className="count">{calls.length}</span><span className="panel-note">每次重试保留独立呼叫历史</span></div>{error && <ErrorState message={error} onRetry={() => setReload(value => value + 1)} />}{loading ? <EmptyState>正在加载 Call…</EmptyState> : calls.length === 0 ? <EmptyState>暂无呼叫记录</EmptyState> : <div className="table-scroll"><table className="data-table"><thead><tr><th>Call</th><th>序号</th><th>状态</th><th>结果码</th><th>拨打时间</th><th>接听时间</th><th>电话时长</th><th>操作</th></tr></thead><tbody>{calls.map(call => <tr key={call.call_id}><td><Link className="link mono" to={`/calls/${call.call_id}?from=tasks`}>{shortId(call.call_id, 18)}</Link></td><td>#{call.call_no}</td><td><span className={`tasks-contact-status status-${call.status}`}>{call.status}</span></td><td>{call.result_code ? <span className="subtle-badge">{call.result_code}</span> : '—'}</td><td>{formatDateTime(call.dial_requested_at || call.created_at)}</td><td>{formatDateTime(call.answered_at)}</td><td className="number">{formatDuration(call.duration_sec)}</td><td><Link className="link" to={`/calls/${call.call_id}?from=tasks`}>详情</Link></td></tr>)}</tbody></table></div>}<Pagination note="按呼叫创建顺序排列 · 每页 50 条" hasPrevious={previous.length > 0} hasNext={Boolean(result?.next_cursor)} onPrevious={previousPage} onNext={nextPage} /></section>;
}
