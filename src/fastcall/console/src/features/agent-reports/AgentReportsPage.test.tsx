import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AgentReportsPage from './AgentReportsPage';
import { getAgentDaily, getAgentFacets, getAgentSummary } from './api';

vi.mock('./api', () => ({ getAgentSummary: vi.fn(), getAgentDaily: vi.fn(), getAgentFacets: vi.fn() }));

const latency = { count: 2, avg_ms: 300, p50_ms: 300, p80_ms: 400, p95_ms: 500, max_ms: 500 };
const summary = {
  total_records: 3, logical_calls: 2, voicemail_calls: 1, voicemail_rate: 50,
  avg_duration_sec: 20, total_duration_sec: 60, avg_dialogue_turns: 3,
  turns_record_count: 2, missing_turns_records: 1, asr_cost: 0.1, llm_cost: 0.2,
  tts_cost: 0.1, total_cost: 0.4, cost_record_count: 2, missing_cost_records: 1,
  cost_currency: 'USD', cost_estimated: true, llm_retry_count: 1,
  missing_llm_retry_records: 0, asr_reconnect_count: 1, missing_asr_reconnect_records: 0,
  tts_reconnect_count: 0, missing_tts_reconnect_records: 0,
  latency: { asr: latency, llm: latency, tts: latency }, latency_sample_records: 3,
  latency_total_records: 3, latency_sample_limit: 5000, latency_truncated: false,
};
const dailyItem = { ...summary, date: '2026-11-14' };

describe('AgentReportsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getAgentSummary).mockResolvedValue(summary as any);
    vi.mocked(getAgentDaily).mockResolvedValue({ items: [dailyItem], start_at: '2026-11-13T16:00:00Z', end_at: '2026-11-14T16:00:00Z' } as any);
    vi.mocked(getAgentFacets).mockResolvedValue({ robot_codes: ['robot-1', 'legacy-robot'], bot_modes: ['standard'] });
  });

  it('renders all charts vertically without records, call search, timezone or tabs', async () => {
    render(<MemoryRouter><AgentReportsPage /></MemoryRouter>);
    expect(await screen.findByRole('heading', { name: '通话技术分析' })).toBeInTheDocument();
    expect(screen.getAllByRole('img')).toHaveLength(7);
    expect(screen.getByText('TTFB 延迟分布')).toBeInTheDocument();
    expect(screen.getByText('每日指标明细')).toBeInTheDocument();
    expect(screen.queryByLabelText('精确 Call UUID')).not.toBeInTheDocument();
    expect(screen.queryByText('Asia/Shanghai')).not.toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    expect(screen.queryByText('技术记录明细')).not.toBeInTheDocument();
    expect(screen.getAllByText('50.0%').length).toBeGreaterThan(0);
    expect(screen.getByText('3 轮')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Robot' })).toHaveTextContent('legacy-robot');
    const query = vi.mocked(getAgentDaily).mock.calls[0][0];
    expect(query.start_at).toEqual(expect.any(String));
    expect(query.end_at).toEqual(expect.any(String));
    expect(query.source).toBeUndefined();
    expect(query.call_uuid).toBeUndefined();
  });

  it('expands daily provider metrics and clears stale data on failure', async () => {
    render(<MemoryRouter><AgentReportsPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '技术指标 ⌄' }));
    expect(screen.getByText('P50')).toBeInTheDocument();
    expect(screen.getAllByText('300 ms').length).toBeGreaterThan(0);
    vi.mocked(getAgentSummary).mockRejectedValueOnce(new Error('analysis failed'));
    fireEvent.change(screen.getByRole('combobox', { name: 'Robot' }), { target: { value: 'legacy-robot' } });
    expect(await screen.findByText('analysis failed')).toBeInTheDocument();
    expect(screen.queryByText('50.0%')).not.toBeInTheDocument();
  });

  it('uses a fixed seven-day default and sends no source scope', async () => {
    render(<MemoryRouter><AgentReportsPage /></MemoryRouter>);
    await waitFor(() => expect(getAgentSummary).toHaveBeenCalledOnce());
    const query = vi.mocked(getAgentSummary).mock.calls[0][0];
    expect(Date.parse(query.end_at!) - Date.parse(query.start_at!)).toBe(7 * 24 * 60 * 60 * 1000);
    expect(getAgentFacets).toHaveBeenCalledOnce();
  });

  it('preserves Robot and mode filters when refreshing the time boundary', async () => {
    render(<MemoryRouter><AgentReportsPage /></MemoryRouter>);
    await screen.findByRole('heading', { name: '通话技术分析' });
    fireEvent.change(screen.getByRole('combobox', { name: 'Robot' }), { target: { value: 'legacy-robot' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Bot 模式' }), { target: { value: 'standard' } });
    await waitFor(() => expect(getAgentDaily).toHaveBeenCalledWith(expect.objectContaining({ robot_code: 'legacy-robot', bot_mode: 'standard' }), expect.any(AbortSignal)));
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    await waitFor(() => expect(getAgentDaily).toHaveBeenLastCalledWith(expect.objectContaining({ robot_code: 'legacy-robot', bot_mode: 'standard' }), expect.any(AbortSignal)));
  });
});
