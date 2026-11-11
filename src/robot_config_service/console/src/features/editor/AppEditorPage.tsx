import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ApiError } from '../../api/client';
import { AppConfig, CapabilityCatalog, RobotApp, RobotVersion, ValidationReport } from '../../api/types';
import CallPolicySection from './CallPolicySection';
import FlowEditor from './FlowEditor';
import HistoryDrawer from './HistoryDrawer';
import PromptSection from './PromptSection';
import RuntimeSection from './RuntimeSection';
import VariablesSection from './VariablesSection';
import { getApp, getCapabilities, getVersion, listVersions, publishDraft, rollbackVersion, saveDraft, validateDraft } from './api';

type Section = 'basic' | 'prompt' | 'runtime' | 'flow' | 'policy';

const sections: Array<{ id: Section; name: string; description: string }> = [
  { id: 'basic', name: '基本信息', description: 'app_code / name' },
  { id: 'prompt', name: 'Prompt 与变量', description: '指令、引用与变量定义' },
  { id: 'runtime', name: 'Runtime', description: '模型、语音与语言' },
  { id: 'flow', name: 'Flow', description: '语音对话结构' },
  { id: 'policy', name: '通话策略', description: '打断与时长限制' },
];

function initialConfig(app: RobotApp, catalog: CapabilityCatalog): AppConfig {
  const draft = app.draft || {};
  return {
    schema_version: 'robot-app/v1',
    app: { ...(draft.app || {}), code: draft.app?.code || app.app_code, name: draft.app?.name || app.name },
    prompt: { ...(draft.prompt || {}), system_template: draft.prompt?.system_template ?? '' },
    variables: draft.variables || [],
    llm: { provider: 'deepseek', model: 'deepseek-flash', temperature: 0.7, top_p: 0.9, context_rounds: 10, max_tokens: 500, timeout_ms: 6000, ...(draft.llm || {}) },
    ...(draft.realtime ? {} : {
      asr: { provider: 'deepgram', model: 'nova-2', language: 'en-US', sample_rate: 8000, encoding: 'pcm_s16le', interim_results: true, endpointing_ms: 100, ...(draft.asr || {}) },
      tts: { provider: 'cartesia', model: 'sonic-2', voice_id: catalog.tts.cartesia.voices.find((voice: any) => voice.enabled)?.voice_id || '', language: 'en-US', speed: 0.9, volume: 80, sample_rate: 8000, encoding: 'pcm_s16le', ...(draft.tts || {}) },
    }),
    ...(draft.realtime ? { realtime: draft.realtime } : {}),
    flow: {
      welcomeNode: { content: '', ...(draft.flow?.welcomeNode || {}) },
      endNode: { content: '', hangUpConfig: { delayHangUp: { enabled: true, delaySecond: 1 } }, ...(draft.flow?.endNode || {}) },
      noResponse: { timeoutSec: 6, noResponseReply: '', maxNoResponseCount: 3, noResponseHangUp: { enabled: true, hangupMessage: '' }, ...(draft.flow?.noResponse || {}) },
      voicemailNode: { enabled: true, action: 'playThenHangup', content: '', ...(draft.flow?.voicemailNode || {}) },
      ...draft.flow,
    },
    call_policy: { ...(draft.call_policy || {}), allow_interruptions: draft.call_policy?.allow_interruptions ?? true, max_call_duration_sec: draft.call_policy?.max_call_duration_sec ?? 180 },
  };
}

export default function AppEditorPage() {
  const { appId = '' } = useParams();
  const navigate = useNavigate();
  const [app, setApp] = useState<RobotApp | null>(null);
  const [catalog, setCatalog] = useState<CapabilityCatalog | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [original, setOriginal] = useState('');
  const [section, setSection] = useState<Section>('prompt');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [validation, setValidation] = useState<ValidationReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [versions, setVersions] = useState<RobotVersion[]>([]);
  const [historical, setHistorical] = useState<RobotVersion | null>(null);
  const draftSnapshot = useRef<AppConfig | null>(null);

  const dirty = useMemo(() => Boolean(config && original && JSON.stringify(config) !== original), [config, original]);

  const load = useCallback(async () => {
    setError('');
    try {
      const [appValue, catalogValue] = await Promise.all([getApp(appId), getCapabilities()]);
      const value = initialConfig(appValue, catalogValue);
      setApp(appValue);
      setCatalog(catalogValue);
      setConfig(value);
      setOriginal(JSON.stringify(value));
      setHistorical(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : '应用加载失败');
    }
  }, [appId]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const listener = (event: BeforeUnloadEvent) => { if (dirty && !historical) event.preventDefault(); };
    window.addEventListener('beforeunload', listener);
    return () => window.removeEventListener('beforeunload', listener);
  }, [dirty, historical]);

  function changeConfig(value: AppConfig) {
    if (!historical) {
      setConfig(value);
      setValidation(null);
      setNotice('');
    }
  }

  async function save() {
    if (!app || !config || historical) return null;
    setBusy(true);
    setError('');
    try {
      const updated = await saveDraft(app.app_id, app.draft_revision, config);
      const normalized = initialConfig(updated, catalog!);
      setApp(updated);
      setConfig(normalized);
      setOriginal(JSON.stringify(normalized));
      setNotice('草稿已保存');
      return updated;
    } catch (err) {
      setError(err instanceof ApiError && err.code === 'DRAFT_REVISION_CONFLICT' ? '草稿已被其他用户更新，请重新加载后再编辑。' : err instanceof Error ? err.message : '保存失败');
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function publish() {
    if (!app || !config || historical) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const current = dirty ? await save() : app;
      if (!current) return;
      const report = await validateDraft(appId, current.draft_revision);
      setValidation(report);
      if (!report.valid || !report.config_hash) return;
      await publishDraft(appId, current.draft_revision, report.config_hash);
      setNotice('发布成功；新通话将使用当前 Release');
      const updated = await getApp(appId);
      setApp(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : '发布失败');
    } finally {
      setBusy(false);
    }
  }

  async function openHistory() {
    setError('');
    try {
      const response = await listVersions(appId);
      setVersions(response.items);
      setHistoryOpen(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : '历史版本加载失败');
    }
  }

  async function selectVersion(version: RobotVersion) {
    if (!config) return;
    setError('');
    try {
      const detail = await getVersion(appId, version.robot_app_version_id);
      if (!detail.app_config) throw new Error('版本配置为空');
      draftSnapshot.current = config;
      setConfig(detail.app_config);
      setHistorical(detail);
      setHistoryOpen(false);
      setNotice('');
    } catch (err) {
      setError(err instanceof Error ? err.message : '版本配置加载失败');
    }
  }

  function returnToDraft() {
    if (draftSnapshot.current) {
      setConfig(draftSnapshot.current);
      setHistorical(null);
      setNotice('已返回当前草稿');
    }
  }

  async function rollback() {
    if (!historical || !app) return;
    if (!window.confirm(`回滚到 v${historical.version_no}？只影响新通话，不修改当前草稿。`)) return;
    setBusy(true);
    try {
      await rollbackVersion(app.app_id, historical.robot_app_version_id);
      const updated = await getApp(appId);
      setApp(updated);
      setNotice(`已回滚到 v${historical.version_no}，当前草稿未修改`);
    } catch (err) {
      setError(err instanceof Error ? err.message : '回滚失败');
    } finally {
      setBusy(false);
    }
  }

  if (error && !config) return <div className="page"><div className="alert error">{error}</div><button className="button" onClick={() => navigate('/apps')}>返回应用列表</button></div>;
  if (!app || !catalog || !config) return <div className="page-loading">正在加载应用配置…</div>;

  const readOnly = Boolean(historical);
  return <div className="page editor-page">
    <section className="page-heading">
      <div className="app-heading"><span className="app-icon">{config.app.name.slice(0, 1) || 'R'}</span><div><h1>{config.app.name}</h1><div className="app-meta">{config.app.code} · {historical ? `历史版本 v${historical.version_no} · 只读` : `当前草稿 r${app.draft_revision}${dirty ? ' · 有未保存更改' : ''}`}</div></div></div>
      <div className="header-actions"><button className="button" onClick={() => void openHistory()}>历史版本</button>{!historical && <><button className="button" disabled={busy || !dirty} onClick={() => void save()}>保存草稿</button><button className="button primary" disabled={busy} onClick={() => void publish()}>发布草稿</button></>}</div>
    </section>
    {historical && <div className="history-banner"><strong>正在查看历史版本 v{historical.version_no}</strong><span>配置只读，不会修改当前草稿。</span><button className="button return-draft" onClick={returnToDraft}>返回当前草稿</button><button className="button rollback" disabled={busy} onClick={() => void rollback()}>回滚到此版本</button></div>}
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert success" role="status">{notice}</div>}
    {validation && <div className={`alert ${validation.valid ? 'success' : 'error'}`}>{validation.valid ? '校验通过，可以发布' : '校验失败'}{[...validation.errors, ...validation.warnings].map(issue => <div key={`${issue.code}${issue.path}`}><code>{issue.path || issue.code}</code> {issue.message}</div>)}</div>}
    <section className="editor-layout">
      <nav className="section-nav"><div className="section-nav-title">应用配置</div>{sections.map(item => <button className={`section-link ${section === item.id ? 'active' : ''}`} key={item.id} onClick={() => setSection(item.id)}><span className="section-name">{item.name}</span><span className="section-desc">{item.description}</span></button>)}<div className="draft-status"><span>当前工作区</span><strong>{historical ? `版本 v${historical.version_no}` : `草稿 r${app.draft_revision}`}</strong><small>{historical ? '不可编辑或发布' : dirty ? '有未保存更改' : '已与保存内容一致'}</small></div></nav>
      <div className="editor-content">
        <div className="context-strip"><span>当前配置</span><code>{historical?.runtime_engine || validation?.runtime_resolution?.engine || '由模型派生'}</code><code>{config.llm.provider} / {config.llm.model}</code></div>
        {section === 'basic' && <section className="editor-card form-card"><div className="editor-card-head"><span className="editor-card-title">基本信息</span></div><div className="form-grid two"><label>app_code<input readOnly value={config.app.code} /></label><label>name<input readOnly={readOnly} value={config.app.name} onChange={event => changeConfig({ ...config, app: { ...config.app, name: event.target.value } })} /></label></div></section>}
        {section === 'prompt' && <><PromptSection config={config} onChange={changeConfig} readOnly={readOnly} /><VariablesSection config={config} onChange={changeConfig} readOnly={readOnly} /></>}
        {section === 'runtime' && <RuntimeSection config={config} catalog={catalog} onChange={changeConfig} readOnly={readOnly} />}
        {section === 'flow' && <FlowEditor config={config} onChange={changeConfig} readOnly={readOnly} />}
        {section === 'policy' && <CallPolicySection config={config} onChange={changeConfig} readOnly={readOnly} />}
      </div>
    </section>
    {historyOpen && <HistoryDrawer versions={versions} currentVersionId={app.current_release?.robot_app_version_id} onSelect={value => void selectVersion(value)} onClose={() => setHistoryOpen(false)} />}
  </div>;
}
