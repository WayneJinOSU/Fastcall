import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import MockCallPage from './MockCallPage';
import { createMockCall, getMockCall } from './api';
import { listApps } from '../apps/api';
import { getVersion } from '../editor/api';
import { listAgentRecords } from '../../api/agentRecords';
import { MockCallClient } from './mockCallClient';

vi.mock('../apps/api');
vi.mock('../editor/api');
vi.mock('./api');
vi.mock('../../api/agentRecords');
vi.mock('./mockCallClient', () => ({ MockCallClient: vi.fn() }));

const app = {
  app_id: 'app-1', app_code: 'robot', name: 'Standard English', enabled: true,
  current_release: { robot_app_version_id: 'version-1', updated_by: 'tester', updated_at: '' },
};
const version = {
  robot_app_version_id: 'version-1', version_no: 3, config_hash: 'sha256:test',
  app_config: { variables: [{ key: 'user_name', type: 'string', required: true, sensitive: true }] },
};

describe('MockCallPage', () => {
  let client: any;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listApps).mockResolvedValue({ items: [app] } as any);
    vi.mocked(getVersion).mockResolvedValue(version as any);
    vi.mocked(createMockCall).mockResolvedValue({ task_id: 'task-1', ws_url: '/mock', robot_version_id: 'version-1' });
    vi.mocked(getMockCall).mockResolvedValue({
      task: { task_id: 'task-1', status: 'completed', result_revision: 1, completed_at: '' },
      call: { call_id: 'call-1', status: 'succeeded', result_code: 'NORMAL_CLEARING' },
      deliveries: [{ delivery_id: 'delivery-1', event_type: 'task.completed', status: 'delivered', attempt_count: 1, last_http_status: 204, delivered_at: '' }],
    } as any);
    vi.mocked(listAgentRecords).mockResolvedValue({ items: [] } as any);
    client = {
      openAudio: vi.fn().mockResolvedValue(undefined), connect: vi.fn().mockResolvedValue(undefined),
      answer: vi.fn(), reject: vi.fn(), hangup: vi.fn(), setMuted: vi.fn(), dispose: vi.fn(),
    };
    vi.mocked(MockCallClient).mockImplementation(function (options: any) {
      return Object.assign(client, { options });
    } as any);
  });

  it('presents an incoming AI call and enters active only after media ready', async () => {
    render(<MockCallPage />);
    await waitFor(() => expect(screen.getByRole('button', { name: '发起 Mock Call' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '发起 Mock Call' }));
    await waitFor(() => expect(client.connect).toHaveBeenCalled());
    act(() => client.options.onMessage({ event: 'incoming', call_id: 'call-1' }));
    expect(screen.getByRole('heading', { name: 'AI 来电' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '接听' }));
    expect(client.answer).toHaveBeenCalledTimes(1);
    act(() => client.options.onMessage({ event: 'ready', call_id: 'call-1' }));
    expect(screen.getByText('双向媒体已连接，可以直接与 AI 对话。')).toBeInTheDocument();
    act(() => client.options.onMessage({ event: 'ended', call_id: 'call-1', result_code: 'NORMAL_CLEARING' }));
    await waitFor(() => expect(screen.getByText('HTTP 204')).toBeInTheDocument());
  });

  it('rejects an incoming call without answering', async () => {
    render(<MockCallPage />);
    await waitFor(() => expect(screen.getByRole('button', { name: '发起 Mock Call' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '发起 Mock Call' }));
    await waitFor(() => expect(client.connect).toHaveBeenCalled());
    act(() => client.options.onMessage({ event: 'incoming', call_id: 'call-1' }));
    fireEvent.click(screen.getByRole('button', { name: '拒绝' }));
    expect(client.reject).toHaveBeenCalledTimes(1);
    expect(client.answer).not.toHaveBeenCalled();
  });
});
