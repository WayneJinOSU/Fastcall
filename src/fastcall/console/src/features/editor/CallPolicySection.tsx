import { AppConfig } from '../../api/types';

export default function CallPolicySection({ config, onChange, readOnly }: { config: AppConfig; onChange: (value: AppConfig) => void; readOnly: boolean }) {
  return <section className="editor-card form-card"><div className="editor-card-head"><span className="editor-card-title">通话策略</span></div><div className="form-grid"><label className="checkbox-field"><input type="checkbox" disabled={readOnly} checked={config.call_policy.allow_interruptions} onChange={event => onChange({ ...config, call_policy: { ...config.call_policy, allow_interruptions: event.target.checked } })} />允许用户打断</label><label>最大通话时长（秒）<input type="number" min="1" readOnly={readOnly} value={config.call_policy.max_call_duration_sec} onChange={event => onChange({ ...config, call_policy: { ...config.call_policy, max_call_duration_sec: Number(event.target.value) } })} /></label></div></section>;
}
