import { AppConfig, CapabilityCatalog } from '../../api/types';

export default function RuntimeSection({ config, catalog, onChange, readOnly }: { config: AppConfig; catalog: CapabilityCatalog; onChange: (value: AppConfig) => void; readOnly: boolean }) {
  const profile = catalog.runtime_profiles.find(item => item.provider === config.llm.provider && item.model === config.llm.model);
  const isRealtime = profile?.runtime_engine === 'realtime';

  function changeModel(modelKey: string) {
    const selected = catalog.runtime_profiles.find(item => `${item.provider}:${item.model}` === modelKey);
    if (!selected) return;
    const next: AppConfig = { ...config, llm: { ...config.llm, provider: selected.provider, model: selected.model } };
    if (selected.runtime_engine === 'realtime') {
      delete next.asr;
      delete next.tts;
      next.llm = { provider: selected.provider, model: selected.model };
      next.realtime = {
        transcription_model: catalog.realtime.transcription_models[0] || '',
        language: 'en-US',
        voice_id: catalog.realtime.voices.find((voice: any) => voice.enabled)?.voice_id || '',
        noise_reduction: true,
        input_audio_format: 'pcm16',
        output_audio_format: 'pcm16',
        turn_detection: { type: 'server_vad' },
      };
    } else {
      delete next.realtime;
      next.asr ||= { provider: 'deepgram', model: 'nova-2', language: 'en-US', sample_rate: 8000, encoding: 'pcm_s16le', interim_results: true, endpointing_ms: 100 };
      next.tts ||= { provider: 'cartesia', model: 'sonic-2', voice_id: catalog.tts.cartesia.voices.find((voice: any) => voice.enabled)?.voice_id || '', language: 'en-US', speed: 0.9, volume: 80, sample_rate: 8000, encoding: 'pcm_s16le' };
    }
    onChange(next);
  }

  function updateBlock(block: 'asr' | 'tts' | 'realtime', field: string, value: string | number | boolean) {
    onChange({ ...config, [block]: { ...config[block], [field]: value } });
  }

  return <>
    <section className="editor-card form-card">
      <div className="editor-card-head"><div><span className="editor-card-title">Chat 模型</span><span className="editor-card-subtitle">Runtime 由服务端精确派生</span></div></div>
      <div className="form-grid two">
        <label>Provider / Model<select disabled={readOnly} value={`${config.llm.provider}:${config.llm.model}`} onChange={event => changeModel(event.target.value)}><option value="">请选择模型</option>{catalog.runtime_profiles.filter(item => item.enabled).map(item => <option key={`${item.provider}:${item.model}`} value={`${item.provider}:${item.model}`}>{item.provider} / {item.model}{item.remote_runtime_supported ? '' : '（不可发布）'}</option>)}</select></label>
        {!isRealtime && <NumberField label="timeout_ms" value={Number(config.llm.timeout_ms || 6000)} readOnly={readOnly} onChange={value => onChange({ ...config, llm: { ...config.llm, timeout_ms: value } })} />}
        {!isRealtime && <NumberField label="temperature" step="0.1" value={Number(config.llm.temperature ?? 0.7)} readOnly={readOnly} onChange={value => onChange({ ...config, llm: { ...config.llm, temperature: value } })} />}
        {!isRealtime && <NumberField label="top_p" step="0.1" value={Number(config.llm.top_p ?? 0.9)} readOnly={readOnly} onChange={value => onChange({ ...config, llm: { ...config.llm, top_p: value } })} />}
        {!isRealtime && <NumberField label="context_rounds" value={Number(config.llm.context_rounds || 10)} readOnly={readOnly} onChange={value => onChange({ ...config, llm: { ...config.llm, context_rounds: value } })} />}
        {!isRealtime && <NumberField label="max_tokens" value={Number(config.llm.max_tokens || 500)} readOnly={readOnly} onChange={value => onChange({ ...config, llm: { ...config.llm, max_tokens: value } })} />}
      </div>
      {profile && !profile.remote_runtime_supported && <div className="alert warning">远程 Adapter 尚未启用，可保存草稿，但不能发布。</div>}
    </section>
    {!isRealtime && config.asr && config.tts && <>
      <section className="editor-card form-card"><div className="editor-card-head"><div><span className="editor-card-title">Deepgram ASR</span><span className="editor-card-subtitle">音频格式由通话链路固定</span></div></div><div className="form-grid two">
        <label>模型<select disabled={readOnly} value={String(config.asr.model)} onChange={event => updateBlock('asr', 'model', event.target.value)}>{Object.keys(catalog.asr.deepgram.models).map(model => <option key={model}>{model}</option>)}</select></label>
        <label>语言<select disabled={readOnly} value={String(config.asr.language)} onChange={event => updateBlock('asr', 'language', event.target.value)}>{catalog.asr.deepgram.models[String(config.asr.model)]?.languages.map((language: string) => <option key={language}>{language}</option>)}</select></label>
      </div></section>
      <section className="editor-card form-card"><div className="editor-card-head"><div><span className="editor-card-title">Cartesia TTS</span><span className="editor-card-subtitle">选择播放音色、语言与语速</span></div></div><div className="form-grid two">
        <label>模型<select disabled={readOnly} value={String(config.tts.model)} onChange={event => updateBlock('tts', 'model', event.target.value)}>{Object.keys(catalog.tts.cartesia.models).map(model => <option key={model}>{model}</option>)}</select></label>
        <label>音色<select disabled={readOnly} value={String(config.tts.voice_id)} onChange={event => updateBlock('tts', 'voice_id', event.target.value)}>{catalog.tts.cartesia.voices.filter((voice: any) => voice.enabled).map((voice: any) => <option key={voice.voice_id} value={voice.voice_id}>{voice.name || voice.voice_id}</option>)}</select></label>
        <label>语言<select disabled={readOnly} value={String(config.tts.language)} onChange={event => updateBlock('tts', 'language', event.target.value)}>{catalog.tts.cartesia.voices.find((voice: any) => voice.voice_id === config.tts?.voice_id)?.languages.map((language: string) => <option key={language}>{language}</option>)}</select></label>
        <NumberField label="语速" step="0.1" value={Number(config.tts.speed)} readOnly={readOnly} onChange={value => updateBlock('tts', 'speed', value)} />
      </div></section>
    </>}
    {isRealtime && config.realtime && <section className="editor-card form-card"><div className="editor-card-head"><span className="editor-card-title">Realtime</span></div><div className="form-grid two">
      <label>转录模型<select disabled={readOnly} value={String(config.realtime.transcription_model)} onChange={event => updateBlock('realtime', 'transcription_model', event.target.value)}>{catalog.realtime.transcription_models.map((model: string) => <option key={model}>{model}</option>)}</select></label>
      <label>音色<select disabled={readOnly} value={String(config.realtime.voice_id)} onChange={event => updateBlock('realtime', 'voice_id', event.target.value)}>{catalog.realtime.voices.map((voice: any) => <option key={voice.voice_id} value={voice.voice_id}>{voice.name || voice.voice_id}</option>)}</select></label>
      <label>语言<select disabled={readOnly} value={String(config.realtime.language)} onChange={event => updateBlock('realtime', 'language', event.target.value)}>{catalog.realtime.voices.find((voice: any) => voice.voice_id === config.realtime?.voice_id)?.languages.map((language: string) => <option key={language}>{language}</option>)}</select></label>
      <label className="checkbox-field"><input type="checkbox" disabled={readOnly} checked={Boolean(config.realtime.noise_reduction)} onChange={event => updateBlock('realtime', 'noise_reduction', event.target.checked)} />noise_reduction</label>
      <label>turn_detection.type<input readOnly={readOnly} value={String(typeof config.realtime.turn_detection === 'object' && config.realtime.turn_detection ? config.realtime.turn_detection.type || 'server_vad' : 'server_vad')} onChange={event => onChange({ ...config, realtime: { ...config.realtime, turn_detection: { type: event.target.value } } })} /></label>
    </div></section>}
  </>;
}

function NumberField({ label, value, onChange, readOnly, step = '1' }: { label: string; value: number; onChange: (value: number) => void; readOnly: boolean; step?: string }) {
  return <label>{label}<input type="number" step={step} readOnly={readOnly} value={Number.isFinite(value) ? value : 0} onChange={event => onChange(Number(event.target.value))} /></label>;
}
