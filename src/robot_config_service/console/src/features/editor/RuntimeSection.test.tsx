import { fireEvent, render, screen } from '@testing-library/react';
import RuntimeSection from './RuntimeSection';

const catalog = {
  runtime_profiles: [{ provider: 'deepseek', model: 'deepseek-flash', runtime_engine: 'standard', remote_runtime_supported: true, enabled: true }],
  asr: { deepgram: { models: { 'nova-2': { languages: ['en-US'] } } } },
  tts: { cartesia: {
    models: { 'sonic-2': {}, 'sonic-3.6': {} },
    voices: [
      { voice_id: 'voice-uuid', name: 'Charlotte - Heiress', country: 'GB', models: ['sonic-2'], enabled: true, languages: ['en-GB'] },
      { voice_id: 'mexico-voice', name: 'Regina - Friendly Specialist', country: 'MX', models: ['sonic-3.6'], enabled: true, languages: ['es-MX'] },
    ],
  } },
  realtime: {},
} as any;
const config = {
  llm: { provider: 'deepseek', model: 'deepseek-flash', temperature: 0.7 },
  asr: { provider: 'deepgram', model: 'nova-2', language: 'en-US', sample_rate: 8000, encoding: 'pcm_s16le', interim_results: true, endpointing_ms: 100 },
  tts: { provider: 'cartesia', model: 'sonic-2', voice_id: 'voice-uuid', language: 'en-GB', speed: 0.9, volume: 80, sample_rate: 8000, encoding: 'pcm_s16le' },
} as any;

describe('RuntimeSection', () => {
  it('hides fixed audio settings and displays the official voice name and country', () => {
    render(<RuntimeSection config={config} catalog={catalog} onChange={vi.fn()} readOnly={false} />);
    for (const field of ['sample_rate', 'encoding', 'interim_results', 'endpointing_ms', 'volume']) {
      expect(screen.queryByText(field)).not.toBeInTheDocument();
    }
    expect(screen.getByRole('option', { name: 'Charlotte - Heiress · 英国' })).toHaveValue('voice-uuid');
    expect(screen.queryByRole('option', { name: 'Regina - Friendly Specialist · 墨西哥' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('语速')).toHaveValue(0.9);
  });

  it('selects a compatible voice and language when changing the TTS model', () => {
    const onChange = vi.fn();
    render(<RuntimeSection config={config} catalog={catalog} onChange={onChange} readOnly={false} />);
    fireEvent.change(screen.getByLabelText('TTS 模型'), { target: { value: 'sonic-3.6' } });
    expect(onChange).toHaveBeenCalledOnce();
    expect(onChange.mock.calls[0][0].tts).toMatchObject({
      model: 'sonic-3.6', voice_id: 'mexico-voice', language: 'es-MX',
    });
  });
});
