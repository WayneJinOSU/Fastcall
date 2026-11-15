import { useState } from 'react';
import { ApiError } from '../../api/client';
import { runTaskAction } from './api';
import { OutboundTask, TaskAction } from './types';

interface Props {
  task: OutboundTask;
  onUpdated: (task: OutboundTask) => void;
  onReload: () => void;
  onError: (message: string) => void;
}

export default function TaskActions({ task, onUpdated, onReload, onError }: Props) {
  const [busy, setBusy] = useState(false);

  async function run(action: TaskAction) {
    if (action === 'cancel' && !window.confirm('取消任务并请求挂断活动通话？此操作不可撤销。')) return;
    setBusy(true);
    onError('');
    try {
      onUpdated(await runTaskAction(task.task_id, action, task.state_version));
    } catch (err) {
      if (!(err instanceof ApiError) || [408, 409, 504].includes(err.status)) {
        onError(err instanceof ApiError && err.status === 409
          ? '任务状态已变化，已重新加载，请确认后再操作。'
          : '请求结果未知，已重新加载任务状态，请先确认再操作。');
        onReload();
      } else {
        onError(err instanceof Error ? err.message : '任务操作失败');
      }
    } finally {
      setBusy(false);
    }
  }

  const terminal = ['completed', 'cancelled'].includes(task.status);
  return <div className="header-actions">
    {['draft', 'scheduled'].includes(task.status) && <button className="button primary" disabled={busy} onClick={() => void run('start')}>启动任务</button>}
    {task.status === 'running' && <button className="button" disabled={busy} onClick={() => void run('pause')}>暂停任务</button>}
    {task.status === 'paused' && <button className="button primary" disabled={busy} onClick={() => void run('resume')}>恢复任务</button>}
    {!terminal && <button className="button danger" disabled={busy} onClick={() => void run('cancel')}>取消任务</button>}
    {terminal && <button className="button" onClick={onReload}>刷新</button>}
  </div>;
}
