import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import AppEditorPage from './AppEditorPage';
import { getApp, getCapabilities, getVersion, listVersions } from './api';

vi.mock('./api', () => ({
  getApp: vi.fn(), getCapabilities: vi.fn(), getVersion: vi.fn(), listVersions: vi.fn(),
  saveDraft: vi.fn(), validateDraft: vi.fn(), publishDraft: vi.fn(), rollbackVersion: vi.fn(),
}));

const config = {
  schema_version: 'robot-app/v1', app: { code: 'retail-cn', name: '零售客服' },
  prompt: { system_template: 'Current {{user_name}}' },
  variables: [{ key: 'user_name', type: 'string', required: true }],
  llm: { provider: 'deepseek', model: 'deepseek-flash' },
  asr: {}, tts: {}, flow: {}, call_policy: { allow_interruptions: true, max_call_duration_sec: 180 },
};
const app = { app_id: 'app1', app_code: 'retail-cn', name: '零售客服', enabled: true, draft: config, draft_revision: 2, current_release: null };
const historical = { robot_app_version_id: 'v1', version_no: 1, created_at: '2026-11-09T10:00:00Z', created_by: 'editor', app_config: { ...config, prompt: { system_template: 'Historical prompt' } } };
const catalog = { runtime_profiles: [], asr: {}, tts: { cartesia: { voices: [] } }, realtime: {} };

describe('AppEditorPage history', () => {
  it('shows history read-only in the same editor and restores unsaved draft', async () => {
    vi.mocked(getApp).mockResolvedValue(app as any);
    vi.mocked(getCapabilities).mockResolvedValue(catalog as any);
    vi.mocked(listVersions).mockResolvedValue({ items: [historical] } as any);
    vi.mocked(getVersion).mockResolvedValue(historical as any);
    render(<MemoryRouter initialEntries={['/apps/app1/edit']}><Routes><Route path="/apps/:appId/edit" element={<AppEditorPage />} /></Routes></MemoryRouter>);
    const editor = await screen.findByLabelText('系统 Prompt') as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: 'Unsaved draft' } });
    fireEvent.click(screen.getByRole('button', { name: '历史版本' }));
    fireEvent.click(await screen.findByRole('button', { name: /v1/ }));
    await screen.findByText('正在查看历史版本 v1');
    expect(screen.getByLabelText('系统 Prompt')).toHaveValue('Historical prompt');
    expect(screen.getByLabelText('系统 Prompt')).toHaveAttribute('readonly');
    expect(screen.queryByRole('button', { name: '保存草稿' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '返回当前草稿' }));
    expect(screen.getByLabelText('系统 Prompt')).toHaveValue('Unsaved draft');
    await waitFor(() => expect(getVersion).toHaveBeenCalledWith('app1', 'v1'));
  });
});
