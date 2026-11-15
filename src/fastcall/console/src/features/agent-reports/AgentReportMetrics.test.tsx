import { render, screen } from '@testing-library/react';
import AgentReportSummary from './AgentReportSummary';

describe('AgentReportSummary', () => {
  it('shows unknown totals and all partial missing counts without fabricating zero', () => {
    const summary = {
      total_records: 4, logical_calls: 4, voicemail_calls: 0, voicemail_rate: null,
      avg_duration_sec: null, total_duration_sec: null, avg_dialogue_turns: null,
      turns_record_count: 0, missing_turns_records: 4, asr_cost: null,
      llm_cost: null, tts_cost: null, total_cost: null, cost_record_count: 0,
      missing_cost_records: 0, cost_currency: 'USD', cost_estimated: true,
      llm_retry_count: 0, missing_llm_retry_records: 4,
      asr_reconnect_count: 0, missing_asr_reconnect_records: 1,
      tts_reconnect_count: 0, missing_tts_reconnect_records: 3,
      latency: {}, latency_sample_records: 0, latency_total_records: 4,
      latency_sample_limit: 5000, latency_truncated: false,
    } as any;
    render(<AgentReportSummary summary={summary} />);
    expect(screen.getAllByText('—', { selector: '.agent-reports-stat-value' })).toHaveLength(5);
    expect(screen.getAllByText('—').length).toBeGreaterThan(3);
    expect(screen.getByText('0 条有效记录 · 4 条缺失')).toBeInTheDocument();
    expect(screen.getByText('ASR/TTS 重连 0 / 0 · 缺失 LLM 4 / ASR 1 / TTS 3')).toBeInTheDocument();
  });
});
