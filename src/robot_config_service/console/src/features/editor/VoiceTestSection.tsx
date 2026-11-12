import { useEffect, useMemo, useRef, useState } from 'react';
import { AppConfig } from '../../api/types';
import { createVoiceTestSession } from './api';
import { VoiceTestClient } from './voiceTestClient';

type VoiceTestSectionProps = {
  appId: string;
  config: AppConfig;
  draftRevision: number;
  dirty: boolean;
};

type TestEvent = { time: string; name: string; detail?: string };

function sampleVariable(key: string, type: string) {
  if (key === 'user_name') return 'Test User';
  if (key === 'app_type') return 'Test App';
  return ({ decimal: '128.50', integer: '1', boolean: 'true', datetime: '2030-01-01T10:00:00' } as Record<string, string>)[type] || 'Sample';
}

function initialVariables(config: AppConfig) {
  return Object.fromEntries(config.variables.map(variable => [variable.key, {
    value: variable.default || sampleVariable(variable.key, variable.type),
    source: variable.default ? '默认' : '示例',
  }]));
}

function formatDuration(seconds: number) {
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

export default function VoiceTestSection({ appId, config, draftRevision, dirty }: VoiceTestSectionProps) {
  const [values, setValues] = useState<Record<string, { value: string; source: string }>>(() => initialVariables(config));
  const [status, setStatus] = useState<'idle' | 'connecting' | 'active' | 'ended' | 'error'>('idle');
  const [error, setError] = useState('');
  const [muted, setMuted] = useState(false);
  const [level, setLevel] = useState(0);
  const [events, setEvents] = useState<TestEvent[]>([]);
  const [sessionId, setSessionId] = useState('');
  const [elapsed, setElapsed] = useState(0);
  const clientRef = useRef<VoiceTestClient | null>(null);
  const startedAtRef = useRef(0);

  const runtimeEngine = config.realtime ? 'realtime' : 'standard';
  const variables = useMemo(() => Object.fromEntries(Object.entries(values).map(([key, item]) => [key, item.value])), [values]);

  useEffect(() => () => clientRef.current?.dispose(), []);
  useEffect(() => {
    if (status !== 'active') return;
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - startedAtRef.current) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [status]);

  function appendEvent(name: string, detail = '') {
    setEvents(items => [...items.slice(-49), { time: new Date().toLocaleTimeString(), name, detail }]);
  }

  function handleMessage(event: Record<string, any>) {
    if (event.action === 'hangup') {
      appendEvent('call.ended', 'remote');
      setStatus('ended');
      clientRef.current?.dispose();
      return;
    }
    const name = String(event.event || 'message');
    appendEvent(name, event.message || event.reason || '');
    if (name === 'session.ready') {
      startedAtRef.current = Date.now();
      setStatus('active');
    } else if (name === 'call.ended') {
      setStatus('ended');
      clientRef.current?.dispose();
    } else if (name === 'error') {
      setError(event.message || '语音测试失败');
      setStatus('error');
    }
  }

  async function start() {
    setError('');
    setEvents([]);
    setElapsed(0);
    setStatus('connecting');
    const client = new VoiceTestClient({
      onMessage: handleMessage,
      onClose: () => setStatus(current => current === 'error' ? current : 'ended'),
      onLevel: setLevel,
    });
    clientRef.current = client;
    try {
      await client.openAudio();
      const session = await createVoiceTestSession(appId, draftRevision, variables);
      setValues(Object.fromEntries(Object.entries(session.variables).map(([key, value]) => [key, { value: String(value), source: values[key]?.source || '示例' }])));
      setSessionId(session.session_id);
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
      await client.connect(`${protocol}://${window.location.host}${session.ws_url}`);
    } catch (reason) {
      client.dispose();
      setStatus('error');
      setError(reason instanceof Error ? reason.message : '语音测试启动失败');
    }
  }

  function hangup() {
    clientRef.current?.hangup();
    setStatus('ended');
  }

  function resetVariables() {
    setValues(initialVariables(config));
  }

  const active = status === 'connecting' || status === 'active';
  return <>
    <div className="voice-test-layout">
      <section className="editor-card voice-test-settings">
        <div className="editor-card-head"><div><span className="editor-card-title">测试设置</span><span className="editor-card-subtitle">当前草稿 r{draftRevision} · {runtimeEngine}</span></div></div>
        <div className="voice-test-settings-body">
          <div className="voice-test-variables-head"><strong>本次通话变量</strong><button className="action primary-link" onClick={resetVariables}>恢复默认/示例</button></div>
          <p className="voice-test-help">自动使用默认值；没有默认值时填入测试示例，不修改正式配置。</p>
          {config.variables.length === 0 && <div className="inline-empty">当前配置没有模板变量，可直接开始测试。</div>}
          {config.variables.map(variable => <label className="voice-variable-field" key={variable.key}>
            <span><code>{variable.key}</code>{variable.required && <em>必填</em>}<small>{values[variable.key]?.source}</small></span>
            <input type={variable.sensitive ? 'password' : 'text'} aria-label={`测试变量 ${variable.key}`} value={values[variable.key]?.value || ''} disabled={active} onChange={event => setValues(current => ({ ...current, [variable.key]: { value: event.target.value, source: '手动' } }))} />
          </label>)}
          {dirty && <div className="voice-test-warning">有未保存的配置修改，请先保存草稿后再测试。</div>}
          <button className="button primary voice-test-start" disabled={active || dirty} onClick={() => void start()}>{status === 'connecting' ? '正在连接…' : status === 'ended' || status === 'error' ? '重新测试' : '开始语音测试'}</button>
        </div>
      </section>

      <section className="editor-card voice-test-call">
        <div className="editor-card-head"><div><span className="editor-card-title">语音通话</span><span className="editor-card-subtitle">连接真实 collection-agent</span></div><span className={`voice-test-status ${status}`}>{({ idle: '待开始', connecting: '连接中', active: '通话中', ended: '已结束', error: '测试失败' })[status]}</span></div>
        {error && <div className="alert error" role="alert">{error}</div>}
        <div className="voice-test-meter"><span style={{ width: `${level}%` }} /></div>
        <div className="voice-test-transcript" aria-label="语音测试状态">
          <div className="voice-test-empty">{status === 'active' ? '语音连接已建立，请通过麦克风与机器人对话。' : '当前链路仅传输音频，不返回实时文字。开始测试后可直接语音对话。'}</div>
        </div>
        <div className="voice-test-controls">
          {status === 'active' && <button className="button" onClick={() => { const next = !muted; setMuted(next); clientRef.current?.setMuted(next); }}>{muted ? '恢复麦克风' : '静音'}</button>}
          {(status === 'active' || status === 'connecting') && <button className="button voice-test-hangup" onClick={hangup}>挂断</button>}
          <span className="voice-test-duration">{formatDuration(elapsed)}</span>
        </div>
      </section>
    </div>
    <section className="editor-card voice-test-events">
      <div className="editor-card-head"><span className="editor-card-title">运行事件</span>{sessionId && <code>{sessionId}</code>}</div>
      <div className="voice-test-event-list">{events.length === 0 ? <span>暂无事件</span> : events.map((item, index) => <div key={`${item.time}-${index}`}><time>{item.time}</time><code>{item.name}</code><span>{item.detail}</span></div>)}</div>
    </section>
  </>;
}
