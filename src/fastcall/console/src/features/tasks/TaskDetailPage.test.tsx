import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ApiError } from '../../api/client';
import TaskDetailPage from './TaskDetailPage';
import { getTask, listContacts, listTaskCalls, runTaskAction } from './api';

vi.mock('./api', () => ({ listTasks: vi.fn(), getTask: vi.fn(), listContacts: vi.fn(), listTaskCalls: vi.fn(), runTaskAction: vi.fn() }));

const task = {
  task_id: 'task_1', customer_task_id: 'campaign_1', name: '逾期提醒', robot_app_id: 'app_1',
  robot_code: 'robot_en', robot_name: 'Collection EN', robot_version_id: 'version_1', config_hash: 'sha256:abc',
  status: 'running', health_status: 'healthy', timezone: 'Asia/Shanghai', scheduled_start_at: null,
  scheduled_end_at: null, calling_windows: [], excluded_dates: [], max_concurrent_calls: 2,
  calls_per_minute: 30, retry_policy: { max_retries: 1 }, state_version: 3, last_error_code: null,
  contact_statistics: { succeeded: 3, pending: 2 }, active_call_count: 1, unknown_call_count: 0,
  created_at: '2026-11-14T01:00:00Z', started_at: '2026-11-14T01:30:00Z', completed_at: null,
};

describe('TaskDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getTask).mockResolvedValue(task as any);
    vi.mocked(listContacts).mockResolvedValue({ items: [], next_cursor: null });
    vi.mocked(listTaskCalls).mockResolvedValue({ items: [], next_cursor: null });
  });

  it('runs the allowed action with the current state version', async () => {
    vi.mocked(runTaskAction).mockResolvedValue({ ...task, status: 'paused', state_version: 4 } as any);
    render(<MemoryRouter><TaskDetailPage /></MemoryRouter>);
    expect(await screen.findByRole('heading', { name: '逾期提醒' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '暂停任务' }));
    await waitFor(() => expect(runTaskAction).toHaveBeenCalledWith('task_1', 'pause', 3));
    expect(await screen.findByRole('button', { name: '恢复任务' })).toBeInTheDocument();
    await waitFor(() => expect(listContacts).toHaveBeenCalledTimes(2));
  });

  it('reloads after a version conflict instead of retrying blindly', async () => {
    vi.mocked(runTaskAction).mockRejectedValue(new ApiError(409, 'STATE_VERSION_CONFLICT', 'changed'));
    render(<MemoryRouter><TaskDetailPage /></MemoryRouter>);
    await screen.findByRole('heading', { name: '逾期提醒' });
    fireEvent.click(screen.getByRole('button', { name: '暂停任务' }));
    expect(await screen.findByText('任务状态已变化，已重新加载，请确认后再操作。')).toBeInTheDocument();
    await waitFor(() => expect(getTask).toHaveBeenCalledTimes(2));
    expect(runTaskAction).toHaveBeenCalledOnce();
  });

  it('requires confirmation before cancelling and switches task tabs', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.mocked(runTaskAction).mockResolvedValue({ ...task, status: 'cancelled', state_version: 4 } as any);
    render(<MemoryRouter><TaskDetailPage /></MemoryRouter>);
    await screen.findByRole('heading', { name: '逾期提醒' });
    fireEvent.click(screen.getByRole('tab', { name: 'Call 记录' }));
    await waitFor(() => expect(listTaskCalls).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: '取消任务' }));
    await waitFor(() => expect(runTaskAction).toHaveBeenCalledWith('task_1', 'cancel', 3));
  });
});
