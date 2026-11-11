import { useEffect, useState } from 'react';
import { AppConfig } from '../../api/types';

export default function FlowEditor({ config, onChange, readOnly }: { config: AppConfig; onChange: (value: AppConfig) => void; readOnly: boolean }) {
  const [mode, setMode] = useState<'form' | 'json'>('form');
  const [json, setJson] = useState(JSON.stringify(config.flow, null, 2));
  const [error, setError] = useState('');

  useEffect(() => { setJson(JSON.stringify(config.flow, null, 2)); }, [config.flow]);

  function update(node: string, field: string, value: unknown) {
    onChange({ ...config, flow: { ...config.flow, [node]: { ...config.flow[node], [field]: value } } });
  }

  function updateNested(node: string, group: string, field: string, value: unknown) {
    onChange({ ...config, flow: { ...config.flow, [node]: { ...config.flow[node], [group]: { ...config.flow[node]?.[group], [field]: value } } } });
  }

  function applyJson(value: string) {
    setJson(value);
    try {
      const flow = JSON.parse(value);
      if (typeof flow !== 'object' || Array.isArray(flow) || flow === null) throw new Error();
      setError('');
      onChange({ ...config, flow });
    } catch {
      setError('Flow 必须是有效的 JSON 对象');
    }
  }

  const flow = config.flow;
  return <section className="editor-card form-card">
    <div className="editor-card-head"><div><span className="editor-card-title">Flow</span><span className="editor-card-subtitle">节点表单与完整 JSON 共享同一份配置</span></div><div className="segmented"><button className={mode === 'form' ? 'active' : ''} onClick={() => setMode('form')}>节点表单</button><button className={mode === 'json' ? 'active' : ''} onClick={() => setMode('json')}>完整 JSON</button></div></div>
    {mode === 'json' ? <div className="json-editor-wrap"><textarea className="json-editor" aria-label="完整 Flow JSON" readOnly={readOnly} value={json} onChange={event => applyJson(event.target.value)} />{error && <div className="form-error">{error}</div>}</div> : <div className="flow-forms">
      <FlowGroup title="欢迎节点"><TextField label="content" value={flow.welcomeNode?.content || ''} readOnly={readOnly} onChange={value => update('welcomeNode', 'content', value)} /></FlowGroup>
      <FlowGroup title="结束节点"><TextField label="content" value={flow.endNode?.content || ''} readOnly={readOnly} onChange={value => update('endNode', 'content', value)} /><label className="checkbox-field"><input type="checkbox" disabled={readOnly} checked={flow.endNode?.hangUpConfig?.delayHangUp?.enabled ?? true} onChange={event => updateNested('endNode', 'hangUpConfig', 'delayHangUp', { ...flow.endNode?.hangUpConfig?.delayHangUp, enabled: event.target.checked })} />延迟挂断</label><NumberField label="delaySecond" value={flow.endNode?.hangUpConfig?.delayHangUp?.delaySecond || 1} readOnly={readOnly} onChange={value => updateNested('endNode', 'hangUpConfig', 'delayHangUp', { ...flow.endNode?.hangUpConfig?.delayHangUp, delaySecond: value })} /></FlowGroup>
      <FlowGroup title="无响应"><NumberField label="timeoutSec" value={flow.noResponse?.timeoutSec || 6} readOnly={readOnly} onChange={value => update('noResponse', 'timeoutSec', value)} /><TextField label="noResponseReply" value={flow.noResponse?.noResponseReply || ''} readOnly={readOnly} onChange={value => update('noResponse', 'noResponseReply', value)} /><NumberField label="maxNoResponseCount" value={flow.noResponse?.maxNoResponseCount || 3} readOnly={readOnly} onChange={value => update('noResponse', 'maxNoResponseCount', value)} /><TextField label="hangupMessage" value={flow.noResponse?.noResponseHangUp?.hangupMessage || ''} readOnly={readOnly} onChange={value => updateNested('noResponse', 'noResponseHangUp', 'hangupMessage', value)} /></FlowGroup>
      <FlowGroup title="语音信箱"><label className="checkbox-field"><input type="checkbox" disabled={readOnly} checked={flow.voicemailNode?.enabled ?? true} onChange={event => update('voicemailNode', 'enabled', event.target.checked)} />启用检测</label><label>action<select disabled={readOnly} value={flow.voicemailNode?.action || 'playThenHangup'} onChange={event => update('voicemailNode', 'action', event.target.value)}><option>playThenHangup</option><option>hangup</option></select></label><TextField label="content" value={flow.voicemailNode?.content || ''} readOnly={readOnly} onChange={value => update('voicemailNode', 'content', value)} /></FlowGroup>
    </div>}
  </section>;
}

function FlowGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return <fieldset className="flow-group"><legend>{title}</legend><div className="form-grid three">{children}</div></fieldset>;
}

function TextField({ label, value, onChange, readOnly }: { label: string; value: string; onChange: (value: string) => void; readOnly: boolean }) {
  return <label>{label}<input readOnly={readOnly} value={value} onChange={event => onChange(event.target.value)} /></label>;
}

function NumberField({ label, value, onChange, readOnly }: { label: string; value: number; onChange: (value: number) => void; readOnly: boolean }) {
  return <label>{label}<input type="number" min="1" readOnly={readOnly} value={value} onChange={event => onChange(Number(event.target.value))} /></label>;
}
