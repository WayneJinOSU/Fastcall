import { OutboundCall } from '../../api/types';
import { formatTimelineTime, shortId } from '../../utils/datetime';
import { formatDuration } from '../../utils/numbers';

export default function TelephonyFacts({ call }: { call: OutboundCall }) {
  const timeline = [
    { label: '已预留', value: call.created_at, detail: '调度已创建呼叫记录' },
    { label: '拨号已确认', value: call.dial_acknowledged_at || call.dial_requested_at, detail: `拨号阶段：${call.dial_phase}` },
    { label: '用户接听', value: call.answered_at, detail: '主通道已关联' },
    { label: 'Agent Ready', value: call.session_ready_at, detail: `第 ${call.claim_generation} 代 Session` },
    { label: '通话结束', value: call.ended_at, detail: call.termination_reason || call.raw_hangup_cause || '终态已记录' },
  ].filter(item => item.value);
  return <div><section className="panel"><div className="panel-head"><span className="panel-title">电话事实</span></div><div className="call-facts-grid"><div><span>电话时长</span><strong>{formatDuration(call.duration_sec)}</strong></div><div><span>Session</span><strong>{call.session_state}</strong></div><div><span>挂机来源</span><strong>{call.hangup_source || '—'}</strong></div><div><span>可重试</span><strong>{call.retryable === null ? '—' : call.retryable ? '是' : '否'}</strong></div></div><div className="call-timeline">{timeline.map(item => <div className="call-timeline-item" key={item.label}><span className="call-timeline-time">{formatTimelineTime(item.value)}</span><span className="call-timeline-dot" /><div><div className="call-timeline-name">{item.label}</div><div className="call-timeline-detail">{item.detail}</div></div></div>)}</div></section><section className="panel"><div className="panel-head"><span className="panel-title">关键技术字段</span><span className="panel-note">不展示原始事件 Payload</span></div><div className="tech-list">{[['Call ID', call.call_id], ['Claim generation', call.claim_generation], ['Robot version', call.robot_version_id], ['FreeSWITCH UUID', call.freeswitch_uuid], ['配置哈希', call.config_hash], ['失败码', call.failure_code]].map(([label, value]) => <div className="tech-row" key={label}><span>{label}</span><strong className="mono">{shortId(String(value || '—'), 30)}</strong></div>)}</div></section></div>;
}
