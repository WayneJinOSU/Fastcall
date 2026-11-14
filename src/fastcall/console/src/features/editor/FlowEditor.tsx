import { useEffect, useRef, useState } from 'react';
import { AppConfig } from '../../api/types';

type TextFieldId = 'welcome' | 'end' | 'noResponseReply' | 'hangupMessage' | 'voicemail';
type FieldSelection = { fieldId: TextFieldId; start: number; end: number };

type FlowEditorProps = {
  config: AppConfig;
  onChange: (value: AppConfig) => void;
  onManageVariables: () => void;
  readOnly: boolean;
};

export default function FlowEditor({ config, onChange, onManageVariables, readOnly }: FlowEditorProps) {
  const [mode, setMode] = useState<'form' | 'json'>('form');
  const [json, setJson] = useState(JSON.stringify(config.flow, null, 2));
  const [error, setError] = useState('');
  const [insertStatus, setInsertStatus] = useState('');
  const fieldRefs = useRef<Partial<Record<TextFieldId, HTMLInputElement | null>>>({});
  const selectionRef = useRef<FieldSelection | null>(null);

  useEffect(() => { setJson(JSON.stringify(config.flow, null, 2)); }, [config.flow]);

  function update(node: string, field: string, value: unknown) {
    onChange({ ...config, flow: { ...config.flow, [node]: { ...config.flow[node], [field]: value } } });
  }

  function updateNested(node: string, group: string, field: string, value: unknown) {
    onChange({ ...config, flow: { ...config.flow, [node]: { ...config.flow[node], [group]: { ...config.flow[node]?.[group], [field]: value } } } });
  }

  function updateTextField(fieldId: TextFieldId, value: string) {
    if (fieldId === 'welcome') update('welcomeNode', 'content', value);
    if (fieldId === 'end') update('endNode', 'content', value);
    if (fieldId === 'noResponseReply') update('noResponse', 'noResponseReply', value);
    if (fieldId === 'hangupMessage') updateNested('noResponse', 'noResponseHangUp', 'hangupMessage', value);
    if (fieldId === 'voicemail') update('voicemailNode', 'content', value);
  }

  function rememberSelection(fieldId: TextFieldId, input: HTMLInputElement) {
    selectionRef.current = {
      fieldId,
      start: input.selectionStart ?? input.value.length,
      end: input.selectionEnd ?? input.value.length,
    };
  }

  function insertVariable(key: string) {
    const selection = selectionRef.current;
    if (!selection) {
      setInsertStatus('请先选择要插入变量的文本框');
      return;
    }
    const input = fieldRefs.current[selection.fieldId];
    if (!input) {
      setInsertStatus('请先选择要插入变量的文本框');
      return;
    }
    const token = `{{${key}}}`;
    const nextValue = `${input.value.slice(0, selection.start)}${token}${input.value.slice(selection.end)}`;
    const cursor = selection.start + token.length;
    updateTextField(selection.fieldId, nextValue);
    setInsertStatus(`已插入 ${token}`);
    requestAnimationFrame(() => {
      input.focus();
      input.setSelectionRange(cursor, cursor);
      rememberSelection(selection.fieldId, input);
    });
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
  const textFieldProps = {
    readOnly,
    inputRef: (fieldId: TextFieldId, input: HTMLInputElement | null) => { fieldRefs.current[fieldId] = input; },
    onActivate: rememberSelection,
  };
  return <section className="editor-card form-card">
    <div className="editor-card-head"><div><span className="editor-card-title">Flow</span><span className="editor-card-subtitle">节点表单与完整 JSON 共享同一份配置</span></div><div className="segmented"><button className={mode === 'form' ? 'active' : ''} onClick={() => setMode('form')}>节点表单</button><button className={mode === 'json' ? 'active' : ''} onClick={() => setMode('json')}>完整 JSON</button></div></div>
    {mode === 'json' ? <div className="json-editor-wrap"><textarea className="json-editor" aria-label="完整 Flow JSON" readOnly={readOnly} value={json} onChange={event => applyJson(event.target.value)} />{error && <div className="form-error">{error}</div>}</div> : <>
      <div className="flow-variables">
        <div className="flow-variable-heading"><strong>可用变量</strong><span>与 Prompt 共用，不在 Flow 中重复创建</span></div>
        <div className="flow-variable-options">
          <div className="flow-variable-chips">{config.variables.map(variable => <button type="button" className="flow-variable-chip" key={variable.key} disabled={readOnly} aria-label={`插入变量 {{${variable.key}}}`} onMouseDown={event => event.preventDefault()} onClick={() => insertVariable(variable.key)}>{`{{${variable.key}}}`}</button>)}</div>
          <span className="flow-variable-helper" role="status">{insertStatus || (config.variables.length ? '点击标签，在最近聚焦的文本框光标处插入' : '暂无变量，可前往 Prompt 与变量中添加')}</span>
        </div>
        <button type="button" className="button small flow-manage-variables" onClick={onManageVariables}>管理变量 →</button>
      </div>
      <div className="flow-forms">
        <FlowGroup title="欢迎节点"><TextField {...textFieldProps} fieldId="welcome" label="content" value={flow.welcomeNode?.content || ''} onChange={value => update('welcomeNode', 'content', value)} /></FlowGroup>
        <FlowGroup title="结束节点"><TextField {...textFieldProps} fieldId="end" label="content" value={flow.endNode?.content || ''} onChange={value => update('endNode', 'content', value)} /><label className="checkbox-field"><input type="checkbox" disabled={readOnly} checked={flow.endNode?.hangUpConfig?.delayHangUp?.enabled ?? true} onChange={event => updateNested('endNode', 'hangUpConfig', 'delayHangUp', { ...flow.endNode?.hangUpConfig?.delayHangUp, enabled: event.target.checked })} />延迟挂断</label><NumberField label="delaySecond" value={flow.endNode?.hangUpConfig?.delayHangUp?.delaySecond || 1} readOnly={readOnly} onChange={value => updateNested('endNode', 'hangUpConfig', 'delayHangUp', { ...flow.endNode?.hangUpConfig?.delayHangUp, delaySecond: value })} /></FlowGroup>
        <FlowGroup title="无响应"><NumberField label="timeoutSec" value={flow.noResponse?.timeoutSec || 6} readOnly={readOnly} onChange={value => update('noResponse', 'timeoutSec', value)} /><TextField {...textFieldProps} fieldId="noResponseReply" label="noResponseReply" value={flow.noResponse?.noResponseReply || ''} onChange={value => update('noResponse', 'noResponseReply', value)} /><NumberField label="maxNoResponseCount" value={flow.noResponse?.maxNoResponseCount || 3} readOnly={readOnly} onChange={value => update('noResponse', 'maxNoResponseCount', value)} /><TextField {...textFieldProps} fieldId="hangupMessage" label="hangupMessage" value={flow.noResponse?.noResponseHangUp?.hangupMessage || ''} onChange={value => updateNested('noResponse', 'noResponseHangUp', 'hangupMessage', value)} /></FlowGroup>
        <FlowGroup title="语音信箱"><label className="checkbox-field"><input type="checkbox" disabled={readOnly} checked={flow.voicemailNode?.enabled ?? true} onChange={event => update('voicemailNode', 'enabled', event.target.checked)} />启用检测</label><label>action<select disabled={readOnly} value={flow.voicemailNode?.action || 'playThenHangup'} onChange={event => update('voicemailNode', 'action', event.target.value)}><option>playThenHangup</option><option>hangup</option></select></label><TextField {...textFieldProps} fieldId="voicemail" label="content" value={flow.voicemailNode?.content || ''} onChange={value => update('voicemailNode', 'content', value)} /></FlowGroup>
      </div>
    </>}
  </section>;
}

function FlowGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return <fieldset className="flow-group"><legend>{title}</legend><div className="form-grid three">{children}</div></fieldset>;
}

function TextField({ fieldId, label, value, onChange, readOnly, inputRef, onActivate }: {
  fieldId: TextFieldId;
  label: string;
  value: string;
  onChange: (value: string) => void;
  readOnly: boolean;
  inputRef: (fieldId: TextFieldId, input: HTMLInputElement | null) => void;
  onActivate: (fieldId: TextFieldId, input: HTMLInputElement) => void;
}) {
  return <label>{label}<input
    ref={input => inputRef(fieldId, input)}
    readOnly={readOnly}
    value={value}
    onFocus={event => onActivate(fieldId, event.currentTarget)}
    onSelect={event => onActivate(fieldId, event.currentTarget)}
    onChange={event => {
      onActivate(fieldId, event.currentTarget);
      onChange(event.target.value);
    }}
  /></label>;
}

function NumberField({ label, value, onChange, readOnly }: { label: string; value: number; onChange: (value: number) => void; readOnly: boolean }) {
  return <label>{label}<input type="number" min="1" readOnly={readOnly} value={value} onChange={event => onChange(Number(event.target.value))} /></label>;
}
