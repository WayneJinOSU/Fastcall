import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AppListPage from './AppListPage';
import { createApp, listApps } from './api';

vi.mock('./api', () => ({ listApps: vi.fn(), createApp: vi.fn(), setAppEnabled: vi.fn() }));

const apps = [
  { app_id: 'app1', app_code: 'robot_123', name: '零售客服', enabled: true, draft_revision: 2, current_release: { robot_app_version_id: 'appver_1234567890', updated_by: 'a', updated_at: '' }, updated_at: '2026-11-10T10:00:00Z' },
  { app_id: 'app2', app_code: 'robot_456', name: 'Global', enabled: false, draft_revision: 1, current_release: null, updated_at: '2026-11-10T10:00:00Z' },
];

describe('AppListPage', () => {
  it('renders only API-backed fields and filters by status', async () => {
    vi.mocked(listApps).mockResolvedValue({ items: apps } as any);
    render(<MemoryRouter><AppListPage /></MemoryRouter>);
    expect(await screen.findByText('零售客服')).toBeInTheDocument();
    expect(screen.getByText('Global')).toBeInTheDocument();
    expect(screen.queryByText('Runtime')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('状态筛选'), { target: { value: 'unpublished' } });
    expect(screen.queryByText('零售客服')).not.toBeInTheDocument();
    expect(screen.getByText('Global')).toBeInTheDocument();
    await waitFor(() => expect(listApps).toHaveBeenCalledOnce());
  });

  it('creates an app with only its name', async () => {
    vi.mocked(listApps).mockResolvedValue({ items: [] });
    vi.mocked(createApp).mockResolvedValue(apps[0] as any);
    render(<MemoryRouter><AppListPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '＋ 新建应用' }));
    expect(screen.queryByLabelText('应用代码')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('应用名称'), { target: { value: '新应用' } });
    fireEvent.click(screen.getByRole('button', { name: '创建并编辑' }));
    await waitFor(() => expect(createApp).toHaveBeenCalledWith('新应用'));
  });
});
