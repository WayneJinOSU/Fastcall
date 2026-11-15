import { formatNumber } from '../../utils/numbers';
import { OutboundTask } from './types';

export default function TaskStatsStrip({ task }: { task: OutboundTask }) {
  const stats = task.contact_statistics;
  const total = Object.values(stats).reduce((sum, value) => sum + value, 0);
  const finished = ['succeeded', 'failed', 'unknown', 'cancelled'].reduce((sum, key) => sum + (stats[key] || 0), 0);
  const items = [
    { label: '待拨打', value: stats.pending || 0 },
    { label: '等待重试', value: stats.waiting_retry || 0 },
    { label: '成功', value: stats.succeeded || 0, tone: 'success' },
    { label: '失败', value: stats.failed || 0, tone: 'danger' },
    { label: 'UNKNOWN', value: stats.unknown || 0, tone: 'warn' },
  ];
  return <section className="tasks-stats-strip"><div className="tasks-stat tasks-stat-main"><span>联系人进度<small>已完成 {total ? Math.round(finished / total * 100) : 0}%</small></span><strong>{formatNumber(total)}</strong><small>全部联系人</small></div>{items.map(item => <div className="tasks-stat" key={item.label}><strong className={item.tone}>{formatNumber(item.value)}</strong><small>{item.label}</small></div>)}</section>;
}
