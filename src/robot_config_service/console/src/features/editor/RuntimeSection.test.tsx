import { render, screen } from '@testing-library/react';
import RuntimeSection from './RuntimeSection';

const catalog = {
  runtime_profiles: [{ provider: 'deepseek', model: 'deepseek-flash', runtime_engine: 'standard', remote_runtime_supported: true, enabled: true }],
  asr: { deepgram: { models: { 'nova-2': { languages: ['en-US'] } } } },
  tts: { cartesia: { models: { 'sonic-2': {} }, voices: [{ voice_id: 'voice-uuid', name: '标准音色', enabled: true, languages: ['en-US'] }] } },
  realtime: {},
} as any;
const config = {
  llm: { provider: 'deepseek', model: 'deepseek-flash', temperature: 0.7 },
  asr: { provider: 'deepgram', model: 'nova-2', language: 'en-US', sample_rate: 8000, encoding: 'pcm_s16le', interim_results: true, endpointing_ms: 100 },
  tts: { provider: 'cartesia', model: 'sonic-2', voice_id: 'voice-uuid', language: 'en-US', speed: 0.9, volume: 80, sample_rate: 8000, encoding: 'pcm_s16le' },
} as any;

describe('RuntimeSection', () => {
  it('hides fixed audio settings and displays a friendly voice name', () => {
    render(<RuntimeSection config={config} catalog={catalog} onChange={vi.fn()} readOnly={false} />);
    for (const field of ['sample_rate', 'encoding', 'interim_results', 'endpointing_ms', 'volume']) {
      expect(screen.queryByText(field)).not.toBeInTheDocument();
    }
    expect(screen.getByRole('option', { name: '标准音色' })).toHaveValue('voice-uuid');
    expect(screen.getByLabelText('语速')).toHaveValue(0.9);
  });
});
