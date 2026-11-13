import { AppConfig, CapabilityCatalog } from '../../api/types';

const COUNTRY_NAMES: Record<string, string> = {
  US: '美国', MX: '墨西哥', PK: '巴基斯坦', PH: '菲律宾', VN: '越南', IN: '印度',
  GB: '英国', CN: '中国', ES: '西班牙', BR: '巴西', FR: '法国', DE: '德国', JP: '日本',
};

const PROVIDER_NAMES: Record<string, string> = {
  deepgram: 'Deepgram', cartesia: 'Cartesia', elevenlabs: 'ElevenLabs',
  assemblyai: 'AssemblyAI', speechmatics: 'Speechmatics', soniox: 'Soniox',
};

function voiceLabel(voice: any) {
  const country = COUNTRY_NAMES[voice.country] || voice.country || '';
  return `${voice.name || voice.voice_id}${country ? ` · ${country}` : ''}`;
}

function enabledModels(provider: any): string[] {
  return Object.entries(provider?.models || {})
    .filter(([, model]: [string, any]) => model.enabled)
    .map(([name]) => name);
}

function modelLanguages(provider: any, model: string): string[] {
  return provider?.models?.[model]?.languages || [];
}

function compatibleLanguage(language: string, languages: string[]): string {
  return languages.includes(language) ? language : languages[0] || '';
}

export default function RuntimeSection({ config, catalog, onChange, readOnly }: { config: AppConfig; catalog: CapabilityCatalog; onChange: (value: AppConfig) => void; readOnly: boolean }) {
  const profile = catalog.runtime_profiles.find(item => item.provider === config.llm.provider && item.model === config.llm.model);
  const isRealtime = profile?.runtime_engine === 'realtime';
  const asrProvider = catalog.asr[String(config.asr?.provider || 'deepgram')] || {};
  const asrModel = String(config.asr?.model || '');
  const ttsProvider = catalog.tts[String(config.tts?.provider || 'cartesia')] || {};
  const ttsModel = String(config.tts?.model || '');
  const ttsVoices = (ttsProvider.voices || []).filter((voice: any) => voice.enabled);
  const selectedTtsVoice = ttsVoices.find((voice: any) => voice.voice_id === config.tts?.voice_id);
  const catalogOrCustomVoice = ttsProvider.voice_input === 'catalog_or_custom';
  const customVoice = ttsProvider.voice_input === 'custom' || (catalogOrCustomVoice && !selectedTtsVoice);
  const ttsLanguages = customVoice ? modelLanguages(ttsProvider, ttsModel) : selectedTtsVoice?.languages || [];
  const speedRange = ttsProvider.speed_range || { min: 0.5, max: 2 };

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

  function changeAsrProvider(providerName: string) {
    const provider = catalog.asr[providerName];
    const model = enabledModels(provider)[0] || '';
    const language = compatibleLanguage(String(config.asr?.language || ''), modelLanguages(provider, model));
    onChange({ ...config, asr: { ...config.asr, provider: providerName, model, language, sample_rate: 8000, encoding: 'pcm_s16le', interim_results: true, endpointing_ms: 100 } });
  }

  function changeAsrModel(model: string) {
    const language = compatibleLanguage(String(config.asr?.language || ''), modelLanguages(asrProvider, model));
    onChange({ ...config, asr: { ...config.asr, model, language } });
  }

  function ttsSelection(provider: any, model: string, voiceId: string, language: string) {
    const catalogVoices = provider.voices || [];
    const isCustom = provider.voice_input === 'custom' || (
      provider.voice_input === 'catalog_or_custom'
      && voiceId
      && !catalogVoices.some((voice: any) => voice.voice_id === voiceId)
    );
    if (isCustom) {
      return { voice_id: voiceId, language: compatibleLanguage(language, modelLanguages(provider, model)) };
    }
    const voices = catalogVoices.filter((voice: any) => voice.enabled && voice.models?.includes(model));
    const voice = voices.find((item: any) => item.voice_id === voiceId) || voices[0];
    return {
      voice_id: voice?.voice_id || '',
      language: compatibleLanguage(language, voice?.languages || []),
    };
  }

  function changeTtsProvider(providerName: string) {
    const provider = catalog.tts[providerName];
    const model = enabledModels(provider)[0] || '';
    const selection = ttsSelection(provider, model, '', String(config.tts?.language || ''));
    onChange({ ...config, tts: { ...config.tts, provider: providerName, model, ...selection, speed: config.tts?.speed ?? 0.9, volume: config.tts?.volume ?? 80, sample_rate: 8000, encoding: 'pcm_s16le' } });
  }

  function changeTtsModel(model: string) {
    const selection = ttsSelection(ttsProvider, model, String(config.tts?.voice_id || ''), String(config.tts?.language || ''));
    onChange({ ...config, tts: { ...config.tts, model, ...selection } });
  }

  function changeTtsVoice(voiceId: string) {
    if (!voiceId) {
      onChange({ ...config, tts: { ...config.tts, voice_id: '' } });
      return;
    }
    const nextVoice = (ttsProvider.voices || []).find((voice: any) => voice.voice_id === voiceId);
    const language = compatibleLanguage(String(config.tts?.language || ''), nextVoice?.languages || []);
    const model = nextVoice?.models?.includes(ttsModel) ? ttsModel : nextVoice?.models?.[0] || ttsModel;
    onChange({ ...config, tts: { ...config.tts, voice_id: voiceId, model, language } });
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
      <section className="editor-card form-card"><div className="editor-card-head"><div><span className="editor-card-title">{PROVIDER_NAMES[String(config.asr.provider)] || config.asr.provider} ASR</span><span className="editor-card-subtitle">厂商、模型与语言联动；音频格式由通话链路固定</span></div></div><div className="form-grid two">
        <label>厂商<select aria-label="ASR 厂商" disabled={readOnly} value={String(config.asr.provider)} onChange={event => changeAsrProvider(event.target.value)}>{Object.entries(catalog.asr).filter(([, provider]: [string, any]) => provider.enabled).map(([name]) => <option key={name} value={name}>{PROVIDER_NAMES[name] || name}</option>)}</select></label>
        <label>模型<select aria-label="ASR 模型" disabled={readOnly} value={asrModel} onChange={event => changeAsrModel(event.target.value)}>{enabledModels(asrProvider).map(model => <option key={model}>{model}</option>)}</select></label>
        <label>语言<select aria-label="ASR 语言" disabled={readOnly} value={String(config.asr.language)} onChange={event => updateBlock('asr', 'language', event.target.value)}>{modelLanguages(asrProvider, asrModel).map((language: string) => <option key={language}>{language}</option>)}</select></label>
      </div></section>
      <section className="editor-card form-card"><div className="editor-card-head"><div><span className="editor-card-title">{PROVIDER_NAMES[String(config.tts.provider)] || config.tts.provider} TTS</span><span className="editor-card-subtitle">选择播放模型、音色、语言与语速</span></div></div><div className="form-grid two">
        <label>厂商<select aria-label="TTS 厂商" disabled={readOnly} value={String(config.tts.provider)} onChange={event => changeTtsProvider(event.target.value)}>{Object.entries(catalog.tts).filter(([, provider]: [string, any]) => provider.enabled).map(([name]) => <option key={name} value={name}>{PROVIDER_NAMES[name] || name}</option>)}</select></label>
        <label>模型<select aria-label="TTS 模型" disabled={readOnly} value={ttsModel} onChange={event => changeTtsModel(event.target.value)}>{enabledModels(ttsProvider).map(model => <option key={model}>{model}</option>)}</select></label>
        {(!customVoice || catalogOrCustomVoice) && <label>音色<select aria-label="TTS 音色" disabled={readOnly} value={String(config.tts.voice_id)} onChange={event => changeTtsVoice(event.target.value)}>{ttsVoices.map((voice: any) => <option key={voice.voice_id} value={voice.voice_id}>{voiceLabel(voice)}</option>)}{catalogOrCustomVoice && <option value="">自定义 Voice ID</option>}{catalogOrCustomVoice && customVoice && config.tts.voice_id && <option value={String(config.tts.voice_id)}>当前自定义音色</option>}</select></label>}
        {customVoice && <label>Voice ID<input aria-label="TTS Voice ID" readOnly={readOnly} value={String(config.tts.voice_id || '')} placeholder="输入账号可用的 Voice ID" onChange={event => updateBlock('tts', 'voice_id', event.target.value)} /></label>}
        <label>语言<select aria-label="TTS 语言" disabled={readOnly} value={String(config.tts.language)} onChange={event => updateBlock('tts', 'language', event.target.value)}>{ttsLanguages.map((language: string) => <option key={language}>{language}</option>)}</select></label>
        <NumberField label="语速" step="0.1" min={speedRange.min} max={speedRange.max} value={Number(config.tts.speed)} readOnly={readOnly} onChange={value => updateBlock('tts', 'speed', value)} />
      </div></section>
    </>}
    {isRealtime && config.realtime && <section className="editor-card form-card"><div className="editor-card-head"><span className="editor-card-title">Realtime</span></div><div className="form-grid two">
      <label>转录模型<select disabled={readOnly} value={String(config.realtime.transcription_model)} onChange={event => updateBlock('realtime', 'transcription_model', event.target.value)}>{catalog.realtime.transcription_models.map((model: string) => <option key={model}>{model}</option>)}</select></label>
      <label>音色<select disabled={readOnly} value={String(config.realtime.voice_id)} onChange={event => updateBlock('realtime', 'voice_id', event.target.value)}>{catalog.realtime.voices.map((voice: any) => <option key={voice.voice_id} value={voice.voice_id}>{voiceLabel(voice)}</option>)}</select></label>
      <label>语言<select disabled={readOnly} value={String(config.realtime.language)} onChange={event => updateBlock('realtime', 'language', event.target.value)}>{catalog.realtime.voices.find((voice: any) => voice.voice_id === config.realtime?.voice_id)?.languages.map((language: string) => <option key={language}>{language}</option>)}</select></label>
      <label className="checkbox-field"><input type="checkbox" disabled={readOnly} checked={Boolean(config.realtime.noise_reduction)} onChange={event => updateBlock('realtime', 'noise_reduction', event.target.checked)} />noise_reduction</label>
      <label>turn_detection.type<input readOnly={readOnly} value={String(typeof config.realtime.turn_detection === 'object' && config.realtime.turn_detection ? config.realtime.turn_detection.type || 'server_vad' : 'server_vad')} onChange={event => onChange({ ...config, realtime: { ...config.realtime, turn_detection: { type: event.target.value } } })} /></label>
    </div></section>}
  </>;
}

function NumberField({ label, value, onChange, readOnly, step = '1', min, max }: { label: string; value: number; onChange: (value: number) => void; readOnly: boolean; step?: string; min?: number; max?: number }) {
  return <label>{label}<input type="number" step={step} min={min} max={max} readOnly={readOnly} value={Number.isFinite(value) ? value : 0} onChange={event => onChange(Number(event.target.value))} /></label>;
}
