import { useEffect, useRef, useState } from 'react';
import { listApps } from '../apps/api';
import { getVersion } from '../editor/api';
import { listAgentRecords } from '../../api/agentRecords';
import { RobotApp, RobotVersion } from '../../api/types';
import { createMockCall, getMockCall, MockCallResult } from './api';
import { MockCallClient } from './mockCallClient';
import './mock-calls.css';

type Phase = 'idle' | 'preparing' | 'dialing' | 'incoming' | 'connecting' | 'active' | 'ending' | 'ended' | 'failed';
type EventItem = { time: string; name: string; detail?: string };

const phaseLabels: Record<Phase, string> = {
  idle: '待发起', preparing: '准备中', dialing: '正在呼叫', incoming: 'AI 来电',
  connecting: '正在连接 AI', active: '通话中', ending: '正在结束', ended: '已结束', failed: '失败',
};

function initialValue(type: string) {
  return ({ decimal: '128.50', integer: '1', boolean: 'true', datetime: '2030-01-01T10:00:00' } as Record<string, string>)[type] || 'Sample';
}

export default function MockCallPage() {
  const [apps, setApps] = useState<RobotApp[]>([]);
  const [appId, setAppId] = useState('');
  const [version, setVersion] = useState<RobotVersion | null>(null);
  const [variables, setVariables] = useState<Record<string, string>>({});
  const [phase, setPhase] = useState<Phase>('idle');
  const [error, setError] = useState('');
  const [events, setEvents] = useState<EventItem[]>([]);
  const [taskId, setTaskId] = useState('');
  const [result, setResult] = useState<MockCallResult | null>(null);
  const [agentRecords, setAgentRecords] = useState(0);
  const [muted, setMuted] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const clientRef = useRef<MockCallClient | null>(null);
  const taskRef = useRef('');
  const startedAtRef = useRef(0);
  const phaseRef = useRef<Phase>('idle');

  useEffect(() => {
    listApps().then(response => {
      const published = response.items.filter(app => app.current_release && app.enabled);
      setApps(published);
      if (published[0]) setAppId(published[0].app_id);
    }).catch(reason => setError(reason instanceof Error ? reason.message : '应用加载失败'));
    return () => clientRef.current?.dispose();
  }, []);

  useEffect(() => {
    const app = apps.find(item => item.app_id === appId);
    if (!app?.current_release) {
      setVersion(null);
      return;
    }
    getVersion(appId, app.current_release.robot_app_version_id).then(value => {
      setVersion(value);
      setVariables(Object.fromEntries((value.app_config?.variables || []).map(item => [item.key, item.default || initialValue(item.type)])));
    }).catch(reason => setError(reason instanceof Error ? reason.message : '发布版本加载失败'));
  }, [appId, apps]);

  useEffect(() => {
    if (phase !== 'active') return;
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - startedAtRef.current) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [phase]);

  function changePhase(value: Phase) {
    phaseRef.current = value;
    setPhase(value);
  }

  function appendEvent(name: string, detail = '') {
    setEvents(items => [...items.slice(-79), { time: new Date().toLocaleTimeString(), name, detail }]);
  }

  async function loadResult() {
    if (!taskRef.current) return;
    try {
      const value = await getMockCall(taskRef.current);
      setResult(value);
      if (value.call?.call_id) {
        const records = await listAgentRecords({ call_uuid: value.call.call_id });
        setAgentRecords(records.items.length);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '结果加载失败');
    }
  }

  function handleMessage(event: Record<string, any>) {
    const name = String(event.event || 'message');
    appendEvent(name, event.result_code || event.message || '');
    if (name === 'transport.ready') changePhase('dialing');
    if (name === 'incoming') changePhase('incoming');
    if (name === 'answer_accepted') changePhase('connecting');
    if (name === 'ready') {
      startedAtRef.current = Date.now();
      changePhase('active');
    }
    if (name === 'ending' || name === 'incoming_timeout') changePhase('ending');
    if (name === 'ended') {
      changePhase('ended');
      clientRef.current?.dispose();
      void loadResult();
    }
    if (name === 'error') {
      setError(event.message || 'Mock Call 失败');
      changePhase('failed');
      clientRef.current?.dispose();
    }
  }

  async function start() {
    if (!version || !appId) return;
    setError('');
    setEvents([]);
    setResult(null);
    setAgentRecords(0);
    setElapsed(0);
    changePhase('preparing');
    const client = new MockCallClient({
      onMessage: handleMessage,
      onClose: () => {
        client.dispose();
        if (!['ended', 'failed'].includes(phaseRef.current)) changePhase('failed');
      },
    });
    clientRef.current = client;
    try {
      await client.openAudio();
      const created = await createMockCall({
        app_id: appId,
        robot_version_id: version.robot_app_version_id,
        client_request_id: crypto.randomUUID(),
        variables,
      });
      taskRef.current = created.task_id;
      setTaskId(created.task_id);
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
      await client.connect(`${protocol}://${window.location.host}${created.ws_url}`);
    } catch (reason) {
      client.dispose();
      setError(reason instanceof Error ? reason.message : 'Mock Call 启动失败');
      changePhase('failed');
    }
  }

  function answer() {
    changePhase('connecting');
    clientRef.current?.answer();
  }

  function reject() {
    changePhase('ending');
    clientRef.current?.reject();
  }

  function hangup() {
    changePhase('ending');
    clientRef.current?.hangup();
  }

  const selectedApp = apps.find(app => app.app_id === appId);
  const busy = ['preparing', 'dialing', 'incoming', 'connecting', 'active', 'ending'].includes(phase);
  return <div className="page mock-call-page">
    <section className="heading"><div><h1>Mock Call</h1><p className="description">使用已发布 Robot，经正式调度、FreeSWITCH loopback、Agent 和 Webhook 完成一次模拟电话。</p></div><span className={`mock-phase ${phase}`}>{phaseLabels[phase]}</span></section>
    {error && <div className="alert error" role="alert">{error}</div>}
    <div className="mock-call-layout">
      <section className="panel mock-settings"><div className="panel-head"><span className="panel-title">呼叫设置</span><span className="subtle-badge">已发布版本</span></div><div className="mock-settings-body">
        <label>Robot 应用<select value={appId} disabled={busy} onChange={event => setAppId(event.target.value)}>{apps.map(app => <option key={app.app_id} value={app.app_id}>{app.name} · {app.app_code}</option>)}</select></label>
        {version && <div className="mock-version"><span>固定版本</span><strong>v{version.version_no}</strong><code>{version.config_hash.slice(0, 24)}…</code></div>}
        {(version?.app_config?.variables || []).map(variable => <label key={variable.key}><span><code>{variable.key}</code>{variable.required && <em>必填</em>}</span><input type={variable.sensitive ? 'password' : 'text'} aria-label={`Mock 变量 ${variable.key}`} value={variables[variable.key] || ''} disabled={busy} onChange={event => setVariables(values => ({ ...values, [variable.key]: event.target.value }))} /></label>)}
        {!busy && <button className="button primary mock-start" disabled={!version} onClick={() => void start()}>{phase === 'ended' || phase === 'failed' ? '发起新的 Mock Call' : '发起 Mock Call'}</button>}
      </div></section>
      <section className="panel mock-phone"><div className="panel-head"><span className="panel-title">模拟被叫</span><span className="panel-subtitle">FreeSWITCH loopback · collection-agent</span></div><div className="mock-phone-body">
        {phase === 'incoming' ? <div className="incoming-call"><span className="incoming-icon">AI</span><h2>AI 来电</h2><p>{selectedApp?.name} · v{version?.version_no}</p><div><button className="button reject" onClick={reject}>拒绝</button><button className="button primary answer" onClick={answer}>接听</button></div></div> : <div className="mock-call-placeholder"><strong>{phaseLabels[phase]}</strong><p>{phase === 'active' ? '双向媒体已连接，可以直接与 AI 对话。' : phase === 'idle' ? '发起后，这里会收到一通需要手动接听的 AI 电话。' : '请等待链路状态更新。'}</p></div>}
        <div className="mock-controls">{phase === 'active' && <button className="button" onClick={() => { setMuted(!muted); clientRef.current?.setMuted(!muted); }}>{muted ? '恢复麦克风' : '静音'}</button>}{['dialing', 'connecting', 'active'].includes(phase) && <button className="button" onClick={hangup}>挂断</button>}<span>{String(Math.floor(elapsed / 60)).padStart(2, '0')}:{String(elapsed % 60).padStart(2, '0')}</span></div>
      </div></section>
    </div>
    <section className="panel mock-events"><div className="panel-head"><span className="panel-title">运行事件</span>{taskId && <code>{taskId}</code>}</div><div className="mock-event-list">{events.length ? events.map((item, index) => <div key={`${item.time}-${index}`}><time>{item.time}</time><code>{item.name}</code><span>{item.detail}</span></div>) : <span>暂无事件</span>}</div></section>
    {result && <section className="panel mock-result"><div className="panel-head"><span className="panel-title">呼叫结果</span><button className="button small" onClick={() => void loadResult()}>刷新结果</button></div><div className="mock-result-grid"><div><span>Task</span><strong>{result.task.status}</strong></div><div><span>Call</span><strong>{result.call?.status || '—'}</strong></div><div><span>结果码</span><strong>{result.call?.result_code || '—'}</strong></div><div><span>Agent 技术记录</span><strong>{agentRecords}</strong></div></div><div className="mock-deliveries">{result.deliveries.map(item => <div key={item.delivery_id}><code>{item.event_type}</code><span>{item.status}</span><span>HTTP {item.last_http_status || '—'}</span><span>尝试 {item.attempt_count}</span></div>)}</div></section>}
  </div>;
}
