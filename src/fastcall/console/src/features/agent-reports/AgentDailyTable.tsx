import { Fragment, useState } from 'react';
import EmptyState from '../../components/ui/EmptyState';
import { DailyMetric } from '../../api/types';
import { formatCost, formatDuration, formatMs, formatNumber, formatPercent } from '../../utils/numbers';

function countValue(value: number, missing: number, total: number) {
  return total > 0 && missing === total ? '—' : formatNumber(value);
}

export default function AgentDailyTable({ items }: { items: DailyMetric[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  return <section className="panel agent-daily-panel"><div className="panel-head"><span className="panel-title">每日指标明细</span><span className="panel-note">点击行展开 ASR / LLM / TTS 技术指标</span></div>{items.length === 0 || items.every(item => item.total_records === 0) ? <EmptyState>当前时间范围暂无通话技术记录</EmptyState> : <div className="table-scroll"><table className="data-table agent-daily-table"><thead><tr><th>日期</th><th className="number">记录 / 逻辑 Call</th><th className="number">语音信箱占比</th><th className="number">平均 Agent 会话</th><th className="number">平均轮次</th><th className="number">成本 USD</th><th className="number">重试 / 重连</th><th>技术指标</th></tr></thead><tbody>{items.map(item => {
    const open = expanded === item.date;
    const reconnects = item.total_records > 0 && (item.missing_asr_reconnect_records === item.total_records || item.missing_tts_reconnect_records === item.total_records)
      ? null : item.asr_reconnect_count + item.tts_reconnect_count;
    return <Fragment key={item.date}><tr><td>{item.date}</td><td className="number">{formatNumber(item.total_records)} / {formatNumber(item.logical_calls)}</td><td className="number">{formatPercent(item.voicemail_rate)}</td><td className="number">{formatDuration(item.avg_duration_sec)}</td><td className="number">{item.avg_dialogue_turns ?? '—'}</td><td className="number">{formatCost(item.total_cost)}{item.missing_cost_records ? <small className="block-note">缺失 {item.missing_cost_records} 条</small> : null}</td><td className="number">{countValue(item.llm_retry_count, item.missing_llm_retry_records, item.total_records)} / {formatNumber(reconnects)}</td><td><button className="link" aria-expanded={open} onClick={() => setExpanded(open ? null : item.date)}>技术指标 {open ? '⌃' : '⌄'}</button></td></tr>{open && <tr className="agent-daily-technical-row"><td colSpan={8}><div className="agent-daily-technical"><table><thead><tr><th>Provider</th><th className="number">平均 TTFB</th><th className="number">P50</th><th className="number">P80</th><th className="number">P95</th><th className="number">有效样本</th></tr></thead><tbody>{(['asr', 'llm', 'tts'] as const).map(name => <tr key={name}><td>{name.toUpperCase()}</td><td className="number">{formatMs(item.latency[name].avg_ms)}</td><td className="number">{formatMs(item.latency[name].p50_ms)}</td><td className="number">{formatMs(item.latency[name].p80_ms)}</td><td className="number">{formatMs(item.latency[name].p95_ms)}</td><td className="number">{formatNumber(item.latency[name].count)}</td></tr>)}</tbody></table><div className="table-note">时延采样 {item.latency_sample_records} / {item.latency_total_records} 条记录{item.latency_truncated ? ' · 已截断' : ''}</div></div></td></tr>}</Fragment>;
  })}</tbody></table></div>}<div className="table-note">空值不按 0 处理；跨日唯一 Call 不累加。</div></section>;
}
