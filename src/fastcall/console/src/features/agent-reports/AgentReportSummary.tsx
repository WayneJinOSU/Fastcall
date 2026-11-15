import { AgentReportSummary as Summary } from '../../api/types';
import { formatCost, formatDuration, formatNumber, formatPercent } from '../../utils/numbers';

export default function AgentReportSummary({ summary }: { summary: Summary }) {
  const missingTurns = summary.missing_turns_records ? ` · ${summary.missing_turns_records} 条缺失` : '';
  const retryValue = summary.total_records > 0 && summary.missing_llm_retry_records === summary.total_records ? '—' : `${formatNumber(summary.llm_retry_count)} 次`;
  const countWithMissing = (value: number, missing: number) => summary.total_records > 0 && missing === summary.total_records ? '—' : formatNumber(value);
  const missing = [
    summary.missing_llm_retry_records ? `LLM ${summary.missing_llm_retry_records}` : '',
    summary.missing_asr_reconnect_records ? `ASR ${summary.missing_asr_reconnect_records}` : '',
    summary.missing_tts_reconnect_records ? `TTS ${summary.missing_tts_reconnect_records}` : '',
  ].filter(Boolean).join(' / ');
  const missingNote = missing ? ` · 缺失 ${missing}` : '';
  const items = [
    { label: '逻辑 Call', value: formatNumber(summary.logical_calls), caption: `${formatNumber(summary.total_records)} 条技术记录` },
    { label: '语音信箱占比', value: formatPercent(summary.voicemail_rate), caption: `${formatNumber(summary.voicemail_calls)} 个逻辑 Call` },
    { label: '平均 Agent 会话', value: formatDuration(summary.avg_duration_sec), caption: '按实际会话开始时间' },
    { label: '平均对话轮次', value: summary.avg_dialogue_turns === null ? '—' : `${summary.avg_dialogue_turns} 轮`, caption: `${formatNumber(summary.turns_record_count)} 条有效记录${missingTurns}` },
    { label: '成本估算', value: formatCost(summary.total_cost), caption: `USD · ${summary.missing_cost_records ? `${summary.missing_cost_records} 条缺失` : '非供应商账单'}` },
    { label: '重试与重连', value: retryValue, caption: `ASR/TTS 重连 ${countWithMissing(summary.asr_reconnect_count, summary.missing_asr_reconnect_records)} / ${countWithMissing(summary.tts_reconnect_count, summary.missing_tts_reconnect_records)}${missingNote}` },
  ];
  return <section className="agent-reports-stats-strip">{items.map(item => <div className="agent-reports-stat" key={item.label}><div className="agent-reports-stat-value">{item.value}</div><div className="agent-reports-stat-label">{item.label}</div><div className="agent-reports-stat-caption">{item.caption}</div></div>)}</section>;
}
