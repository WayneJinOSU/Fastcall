import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import CallDetailPage from './CallDetailPage';
import { getCall } from './api';
import { getAgentRecord, listAgentRecords } from '../../api/agentRecords';

vi.mock('./api', () => ({ getCall: vi.fn() }));
vi.mock('../../api/agentRecords', () => ({ listAgentRecords: vi.fn(), getAgentRecord: vi.fn() }));

const call = {
  call_id: 'call_1', task_id: 'task_1', contact_id: 'contact_1', call_no: 2, status: 'succeeded',
  result_code: 'USER_HANGUP', retryable: false, robot_version_id: 'version_1', config_hash: 'hash',
  origination_uuid: 'origin', freeswitch_uuid: 'fs', claim_generation: 2, session_state: 'completed',
  session_ready_at: '2026-11-14T01:00:02Z', dial_phase: 'acknowledged', answered_by_human: true,
  raw_hangup_cause: 'NORMAL_CLEARING', hangup_source: 'user', failure_code: null,
  termination_reason: 'USER_HANGUP', state_version: 4, created_at: '2026-11-14T01:00:00Z',
  dial_requested_at: '2026-11-14T01:00:00Z', dial_acknowledged_at: '2026-11-14T01:00:01Z',
  dialing_at: null, ringing_at: null, answered_at: '2026-11-14T01:00:01Z',
  conversation_started_at: '2026-11-14T01:00:02Z', ended_at: '2026-11-14T01:01:00Z', duration_sec: 60,
};
const record = {
  record_id: 7, call_uuid: 'call_1', call_source: 'unclassified', claim_generation: 1,
  bot_mode: 'standard', robot_code: 'robot', robot_version_id: 'version_1',
  task_start_time: '2026-11-14T01:00:00Z', task_duration_sec: 60, is_voicemail: false,
  total_cost: 0.1, asr_model_name: 'nova-3', llm_model_name: 'deepseek', tts_model_name: 'sonic',
  llm_dialogue_turns: 4, llm_retry_count: 0, asr_reconnect_count: 0, tts_reconnect_count: 0,
  created_at: '2026-11-14T01:01:00Z',
  latency: { asr: { count: 1, avg_ms: 100, p95_ms: 100, max_ms: 100 }, llm: { count: 1, avg_ms: 200, p95_ms: 200, max_ms: 200 }, tts: { count: 1, avg_ms: 300, p95_ms: 300, max_ms: 300 } },
};
const detail = {
  ...record, task_end_time: '2026-11-14T01:01:00Z',
  technical_config: { asr: { provider: 'deepgram' }, llm: { provider: 'deepseek' }, tts: { provider: 'cartesia' }, pricing: {} },
  asr_param_config: {}, tts_param_config: {}, asr_ttfb_list: [], llm_ttfb_list: [], tts_ttfb_list: [],
  asr_cost: 0.01, llm_cost: 0.02, tts_cost: 0.03, asr_total_transcribed_chars: 10,
  asr_total_audio_duration: 30, asr_connection_requests: 1, llm_total_generated_chars: 20,
  llm_prompt_tokens: 100, llm_completion_tokens: 20, llm_cache_read_tokens: 0,
  tts_total_output_duration: 30, tts_total_chars: 30, tts_connection_requests: 1,
};

function HistoryControls() {
  const navigate = useNavigate();
  return <><button onClick={() => navigate(-1)}>测试后退</button><button onClick={() => navigate(1)}>测试前进</button></>;
}

function renderPage(path = '/calls/call_1') {
  return render(<MemoryRouter initialEntries={[path]}><HistoryControls /><Routes><Route path="/calls/:callId" element={<CallDetailPage />} /></Routes></MemoryRouter>);
}

describe('CallDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getCall).mockResolvedValue(call as any);
    vi.mocked(listAgentRecords).mockResolvedValue({ items: [record] as any, summary: {} as any, next_cursor: null });
    vi.mocked(getAgentRecord).mockResolvedValue(detail as any);
  });

  it('loads all record sources and falls back from an invalid record', async () => {
    renderPage('/calls/call_1?record=999&from=reports');
    expect(await screen.findByText('指定记录不可用，已选择首条可用记录。')).toBeInTheDocument();
    expect(listAgentRecords).toHaveBeenCalledWith({ source: 'all', call_uuid: 'call_1', limit: 200 }, expect.any(AbortSignal));
    await waitFor(() => expect(getAgentRecord).toHaveBeenCalledWith(7, expect.any(AbortSignal)));
    expect(screen.getByText('未分类')).toBeInTheDocument();
    expect(screen.getByText('电话事实')).toBeInTheDocument();
  });

  it('restores the selected record through browser history', async () => {
    const second = { ...record, record_id: 8, claim_generation: 2 };
    vi.mocked(listAgentRecords).mockResolvedValue({ items: [record, second] as any, summary: {} as any, next_cursor: null });
    vi.mocked(getAgentRecord).mockImplementation(async id => ({ ...detail, record_id: id }) as any);
    renderPage('/calls/call_1?record=7');
    await waitFor(() => expect(getAgentRecord).toHaveBeenCalledWith(7, expect.any(AbortSignal)));
    fireEvent.click(await screen.findByRole('button', { name: '#8 · Gen 2' }));
    await waitFor(() => expect(getAgentRecord).toHaveBeenCalledWith(8, expect.any(AbortSignal)));
    fireEvent.click(screen.getByRole('button', { name: '测试后退' }));
    await waitFor(() => expect(getAgentRecord).toHaveBeenLastCalledWith(7, expect.any(AbortSignal)));
    fireEvent.click(screen.getByRole('button', { name: '测试前进' }));
    await waitFor(() => expect(getAgentRecord).toHaveBeenLastCalledWith(8, expect.any(AbortSignal)));
  });

  it('keeps telephony facts visible when Agent is unavailable', async () => {
    vi.mocked(listAgentRecords).mockRejectedValue(new Error('Agent unavailable'));
    renderPage();
    expect(await screen.findByText('电话事实')).toBeInTheDocument();
    expect(screen.getByText('Agent unavailable')).toBeInTheDocument();
    expect(screen.getByText('通话结束')).toBeInTheDocument();
  });

  it('keeps Agent records visible when the main Call fails', async () => {
    vi.mocked(getCall).mockRejectedValue(new Error('Call missing'));
    renderPage();
    expect(await screen.findByText('Call missing')).toBeInTheDocument();
    await waitFor(() => expect(getAgentRecord).toHaveBeenCalled());
    expect(screen.getByText('Agent 技术记录')).toBeInTheDocument();
  });
});
