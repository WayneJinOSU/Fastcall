import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import EmptyState from '../../components/ui/EmptyState';
import ErrorState from '../../components/ui/ErrorState';
import Pagination from '../../components/ui/Pagination';
import { formatDateTime, shortId } from '../../utils/datetime';
import { listContacts } from './api';
import { ContactListResponse } from './types';

export default function ContactTable({ taskId }: { taskId: string }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [result, setResult] = useState<ContactListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [previous, setPrevious] = useState<string[]>([]);
  const queryKey = searchParams.toString();

  useEffect(() => {
    const controller = new AbortController();
    setResult(null);
    setLoading(true);
    setError('');
    listContacts(taskId, {
      status: searchParams.get('contact_status') || undefined,
      result_code: searchParams.get('result_code') || undefined,
      cursor: searchParams.get('contact_cursor') || undefined,
      limit: 50,
    }, controller.signal).then(setResult).catch(err => {
      if (err.name !== 'AbortError') setError(err instanceof Error ? err.message : '联系人加载失败');
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [taskId, queryKey, reload]);

  function filters(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    next.delete('contact_cursor');
    if (value) next.set(key, value); else next.delete(key);
    setPrevious([]);
    setSearchParams(next);
  }

  function page(nextCursor: string | null) {
    const current = searchParams.get('contact_cursor') || '';
    const next = new URLSearchParams(searchParams);
    if (nextCursor) {
      setPrevious(stack => [...stack, current]);
      next.set('contact_cursor', nextCursor);
    } else {
      const target = previous[previous.length - 1];
      if (target === undefined) return;
      setPrevious(stack => stack.slice(0, -1));
      if (target) next.set('contact_cursor', target); else next.delete('contact_cursor');
    }
    setSearchParams(next);
  }

  const contacts = result?.items || [];
  return <section className="panel tab-panel"><div className="panel-head"><span className="panel-title">联系人</span><span className="count">{contacts.length}</span><span className="panel-note">仅显示脱敏标识与号码尾号</span></div><div className="inline-filters"><select className="select-control" aria-label="联系人状态" value={searchParams.get('contact_status') || ''} onChange={event => filters('contact_status', event.target.value)}><option value="">全部状态</option><option value="pending">待拨打</option><option value="calling">呼叫中</option><option value="waiting_retry">等待重试</option><option value="succeeded">成功</option><option value="failed">失败</option><option value="unknown">UNKNOWN</option><option value="cancelled">已取消</option></select><select className="select-control" aria-label="联系人结果" value={searchParams.get('result_code') || ''} onChange={event => filters('result_code', event.target.value)}><option value="">全部结果</option><option value="USER_HANGUP">USER_HANGUP</option><option value="NO_ANSWER">NO_ANSWER</option><option value="CALL_REJECTED">CALL_REJECTED</option><option value="VOICEMAIL">VOICEMAIL</option></select></div>{error && <ErrorState message={error} onRetry={() => setReload(value => value + 1)} />}{loading ? <EmptyState>正在加载联系人…</EmptyState> : contacts.length === 0 ? <EmptyState>暂无匹配的联系人</EmptyState> : <div className="table-scroll"><table className="data-table contacts-table"><thead><tr><th>客户联系人 ID</th><th>号码</th><th>状态</th><th>结果码</th><th className="number">重试</th><th>下一拨打时间</th><th>最近 Call</th><th></th></tr></thead><tbody>{contacts.map(contact => <tr key={contact.contact_id}><td className="mono">{contact.customer_contact_id}</td><td className="mono">••• {contact.phone_last4}</td><td><span className={`tasks-contact-status status-${contact.status}`}>{contact.status}</span></td><td>{contact.result_code ? <span className="subtle-badge">{contact.result_code}</span> : '—'}</td><td className="number">{contact.retry_count}</td><td>{formatDateTime(contact.next_call_at)}</td><td className="mono">{shortId(contact.last_call_id, 10)}</td><td>{contact.last_call_id && <Link className="link" to={`/calls/${contact.last_call_id}?from=tasks`}>详情</Link>}</td></tr>)}</tbody></table></div>}<Pagination note="按导入顺序排列 · 每页 50 条" hasPrevious={previous.length > 0} hasNext={Boolean(result?.next_cursor)} onPrevious={() => page(null)} onNext={() => page(result?.next_cursor || null)} /></section>;
}
