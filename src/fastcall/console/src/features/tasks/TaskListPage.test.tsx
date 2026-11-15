import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import TaskListPage from './TaskListPage';
import { listTasks } from './api';
import { listApps } from '../apps/api';

vi.mock('./api', () => ({ listTasks: vi.fn(), getTask: vi.fn(), listContacts: vi.fn(), listTaskCalls: vi.fn(), runTaskAction: vi.fn() }));
vi.mock('../apps/api', () => ({ listApps: vi.fn() }));

const task = {
  task_id: 'task_1', customer_task_id: 'campaign_1', name: '逾期提醒', robot_app_id: 'app_1',
  robot_code: 'robot_en', robot_name: 'Collection EN', robot_version_id: 'version_1', config_hash: null,
  status: 'running', health_status: 'healthy', timezone: 'Asia/Shanghai', scheduled_start_at: null,
  scheduled_end_at: null, calling_windows: [], excluded_dates: [], max_concurrent_calls: 2,
  calls_per_minute: 30, retry_policy: {}, state_version: 2, last_error_code: null,
  contact_statistics: { succeeded: 3, pending: 2 }, active_call_count: 1, unknown_call_count: 2,
  created_at: '2026-11-14T01:00:00Z', started_at: '2026-11-14T01:30:00Z', completed_at: null,
};

describe('TaskListPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listApps).mockResolvedValue({ items: [] });
  });

  it('restores URL filters and follows cursor pagination', async () => {
    vi.mocked(listTasks)
      .mockResolvedValue({ items: [task] as any, next_cursor: null })
      .mockResolvedValueOnce({ items: [task] as any, next_cursor: 'cursor-2' })
      .mockResolvedValueOnce({ items: [{ ...task, task_id: 'task_2', name: '第二页' }] as any, next_cursor: null });
    render(<MemoryRouter initialEntries={['/tasks?status=running']}><TaskListPage /></MemoryRouter>);
    expect(await screen.findByText('逾期提醒')).toBeInTheDocument();
    expect(listTasks).toHaveBeenCalledWith(expect.objectContaining({ status: 'running', limit: 20 }), expect.any(AbortSignal));
    expect(screen.getByText('2', { selector: '.warn' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(await screen.findByText('第二页')).toBeInTheDocument();
    await waitFor(() => expect(listTasks).toHaveBeenCalledWith(expect.objectContaining({ cursor: 'cursor-2' }), expect.any(AbortSignal)));
    fireEvent.click(screen.getByRole('button', { name: '上一页' }));
    expect(await screen.findByText('逾期提醒')).toBeInTheDocument();
  });

  it('applies search and resets the cursor', async () => {
    vi.mocked(listTasks).mockResolvedValue({ items: [task] as any, next_cursor: null });
    render(<MemoryRouter initialEntries={['/tasks?cursor=old']}><TaskListPage /></MemoryRouter>);
    await screen.findByText('逾期提醒');
    fireEvent.change(screen.getByLabelText('搜索任务'), { target: { value: 'campaign' } });
    fireEvent.click(screen.getByRole('button', { name: '查询' }));
    await waitFor(() => expect(listTasks).toHaveBeenLastCalledWith(expect.objectContaining({ search: 'campaign' }), expect.any(AbortSignal)));
    expect(listTasks).toHaveBeenLastCalledWith(expect.not.objectContaining({ cursor: 'old' }), expect.any(AbortSignal));
  });
});
