import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import VoiceTestSection from './VoiceTestSection';
import { createVoiceTestSession } from './api';

const mocks = vi.hoisted(() => ({
  openAudio: vi.fn(),
  connect: vi.fn(),
  setMuted: vi.fn(),
  hangup: vi.fn(),
  dispose: vi.fn(),
  options: undefined as any,
}));

vi.mock('./voiceTestClient', () => ({
  VoiceTestClient: class {
    constructor(options: any) { mocks.options = options; }
    openAudio = mocks.openAudio;
    connect = mocks.connect;
    setMuted = mocks.setMuted;
    hangup = mocks.hangup;
    dispose = mocks.dispose;
  },
}));
vi.mock('./api', () => ({ createVoiceTestSession: vi.fn() }));

const config = {
  variables: [
    { key: 'app_type', type: 'string', required: false, default: 'Retail' },
    { key: 'user_name', type: 'string', required: true },
    { key: 'remaining_amount', type: 'decimal', required: false },
  ],
  llm: { provider: 'deepseek', model: 'deepseek-flash' },
} as any;

describe('VoiceTestSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.openAudio.mockResolvedValue(undefined);
    mocks.connect.mockResolvedValue(undefined);
    vi.mocked(createVoiceTestSession).mockResolvedValue({
      session_id: 'voice-test-1', ws_url: '/voice-test', runtime_engine: 'standard',
      config_hash: 'sha256:test', call_uuid: 'console-test-1',
      variables: { app_type: 'Retail', user_name: 'Test User', remaining_amount: '128.50' },
    } as any);
  });

  it('prefills defaults and test samples, including required variables without defaults', () => {
    render(<VoiceTestSection appId="app1" config={config} draftRevision={3} dirty={false} />);
    expect(screen.getByLabelText('测试变量 app_type')).toHaveValue('Retail');
    expect(screen.getByLabelText('测试变量 user_name')).toHaveValue('Test User');
    expect(screen.getByLabelText('测试变量 remaining_amount')).toHaveValue('128.50');
    expect(screen.getByRole('button', { name: '开始语音测试' })).toBeEnabled();
  });

  it('opens audio, creates a session and supports mute and hangup', async () => {
    render(<VoiceTestSection appId="app1" config={config} draftRevision={3} dirty={false} />);
    fireEvent.click(screen.getByRole('button', { name: '开始语音测试' }));
    await waitFor(() => expect(mocks.connect).toHaveBeenCalled());
    expect(mocks.openAudio).toHaveBeenCalledOnce();
    expect(createVoiceTestSession).toHaveBeenCalledWith('app1', 3, {
      app_type: 'Retail', user_name: 'Test User', remaining_amount: '128.50',
    });
    act(() => mocks.options.onMessage({ event: 'session.ready' }));
    fireEvent.click(screen.getByRole('button', { name: '静音' }));
    expect(mocks.setMuted).toHaveBeenCalledWith(true);
    expect(screen.getByText('语音连接已建立，请通过麦克风与机器人对话。')).toBeInTheDocument();
    act(() => mocks.options.onMessage({ event: 'bot.text', text: 'Must not render' }));
    expect(screen.queryByText('Must not render')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '挂断' }));
    expect(mocks.hangup).toHaveBeenCalledOnce();
  });

  it('ends the test and releases audio resources when collection-agent hangs up', async () => {
    render(<VoiceTestSection appId="app1" config={config} draftRevision={3} dirty={false} />);
    fireEvent.click(screen.getByRole('button', { name: '开始语音测试' }));
    await waitFor(() => expect(mocks.connect).toHaveBeenCalled());
    act(() => mocks.options.onMessage({ event: 'session.ready' }));
    act(() => mocks.options.onMessage({ action: 'hangup', data: { code: 480 } }));
    expect(mocks.dispose).toHaveBeenCalled();
    expect(screen.getByText('已结束')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新测试' })).toBeEnabled();
  });

  it('requires saved configuration before starting', () => {
    render(<VoiceTestSection appId="app1" config={config} draftRevision={3} dirty={true} />);
    expect(screen.getByRole('button', { name: '开始语音测试' })).toBeDisabled();
    expect(screen.getByText('有未保存的配置修改，请先保存草稿后再测试。')).toBeInTheDocument();
  });
});
