import EmptyState from '../../components/ui/EmptyState';
import ErrorState from '../../components/ui/ErrorState';
import { AgentTechnicalRecordDetail, AgentTechnicalRecordListItem } from '../../api/types';
import { formatDateTime } from '../../utils/datetime';
import { formatCost, formatDuration, formatMs, formatNumber } from '../../utils/numbers';

interface Props {
  records: AgentTechnicalRecordListItem[];
  listLoading: boolean;
  listError: string;
  selectedId: number | null;
  detail: AgentTechnicalRecordDetail | null;
  detailLoading: boolean;
  detailError: string;
  notice: string;
  onSelect: (recordId: number) => void;
  onRetry: () => void;
}

export default function AgentRecordPanel({ records, listLoading, listError, selectedId, detail, detailLoading, detailError, notice, onSelect, onRetry }: Props) {
  return <div><section className="panel agent-record-panel"><div className="panel-head"><span className="panel-title">Agent 技术记录</span><span className="count">{records.length}</span><button className="icon-link" aria-label="刷新 Agent 记录" onClick={onRetry}>刷新</button></div>{listError ? <ErrorState message={listError} onRetry={onRetry} /> : listLoading ? <EmptyState>正在加载 Agent 记录…</EmptyState> : records.length === 0 ? <EmptyState>暂无技术记录或尚未落库</EmptyState> : <><div className="record-switch">{records.map(record => <button key={record.record_id} className={`record-chip ${record.record_id === selectedId ? 'active' : ''}`} onClick={() => onSelect(record.record_id)}>#{record.record_id} · Gen {record.claim_generation || '—'}</button>)}</div>{notice && <div className="alert warning">{notice}</div>}{detailError ? <ErrorState message={detailError} /> : detailLoading || !detail ? <EmptyState>正在加载记录详情…</EmptyState> : <><div className="agent-summary"><div><strong>{formatDuration(detail.task_duration_sec)}</strong><span>Agent 会话</span></div><div><strong>{formatNumber(detail.llm_dialogue_turns)}</strong><span>LLM 对话轮次</span></div><div><strong>{formatCost(detail.total_cost)}</strong><span>USD 成本估算</span></div></div><div className="provider-list"><Provider name="ASR" model={`${detail.technical_config.asr.provider || '—'} / ${detail.asr_model_name || '—'}`} p95={formatMs(detail.latency.asr.p95_ms)} usageLabel="音频时长" usage={formatDuration(detail.asr_total_audio_duration)} retries={detail.asr_reconnect_count} cost={detail.asr_cost} /><Provider name="LLM" model={detail.llm_model_name} p95={formatMs(detail.latency.llm.p95_ms)} usageLabel="输入 / 输出 Token" usage={`${formatNumber(detail.llm_prompt_tokens)} / ${formatNumber(detail.llm_completion_tokens)}`} retries={detail.llm_retry_count} cost={detail.llm_cost} /><Provider name="TTS" model={`${detail.technical_config.tts.provider || '—'} / ${detail.tts_model_name || '—'}`} p95={formatMs(detail.latency.tts.p95_ms)} usageLabel="输出时长" usage={formatDuration(detail.tts_total_output_duration)} retries={detail.tts_reconnect_count} cost={detail.tts_cost} /></div><div className="cost-note">本地价格配置估算，非供应商账单。价格版本：{detail.technical_config.pricing.version || '历史记录未知'}。</div></>}</>}</section>{detail && <section className="panel"><div className="panel-head"><span className="panel-title">记录元数据</span></div><div className="tech-list"><div className="tech-row"><span>Record ID</span><strong className="mono">{detail.record_id}</strong></div><div className="tech-row"><span>来源</span><strong>{sourceLabel(detail.call_source)}</strong></div><div className="tech-row"><span>会话开始</span><strong>{formatDateTime(detail.task_start_time, true)}</strong></div><div className="tech-row"><span>落库时间</span><strong>{formatDateTime(detail.created_at, true)}</strong></div></div></section>}<div className="independent-note">记录缺失或服务不可用不会影响左侧电话事实。</div></div>;
}

function Provider({ name, model, p95, usageLabel, usage, retries, cost }: { name: string; model: string; p95: string; usageLabel: string; usage: string; retries: number | null; cost: number | null }) {
  return <div className="provider-row"><div className="provider-head"><div className="provider-name"><span className={`service-dot ${name.toLowerCase()}`} />{name}</div><span className="provider-model mono">{model}</span></div><div className="provider-stats"><span>TTFB P95<strong>{p95}</strong></span><span>{usageLabel}<strong>{usage}</strong></span><span>重试或重连 / 成本<strong>{formatNumber(retries)} / {formatCost(cost)}</strong></span></div></div>;
}

function sourceLabel(source: string) {
  return ({ fastcall_outbound: '正式外呼', console_voice_test: '语音测试', generic_ws: '其他 WebSocket', unclassified: '未分类' } as Record<string, string>)[source] || source;
}
