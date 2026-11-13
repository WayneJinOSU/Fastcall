import { fireEvent, render, screen } from '@testing-library/react';
import RuntimeSection from './RuntimeSection';

const catalog = {
  runtime_profiles: [{ provider: 'deepseek', model: 'deepseek-flash', runtime_engine: 'standard', remote_runtime_supported: true, enabled: true }],
  asr: {
    deepgram: { enabled: true, models: { 'nova-2': { enabled: true, languages: ['en-US'] } } },
    assemblyai: { enabled: true, models: { 'u3-rt-pro': { enabled: true, languages: ['en-US', 'es-MX'] } } },
  },
  tts: {
    cartesia: {
      enabled: true,
      voice_input: 'catalog_or_custom',
      models: {
        'sonic-2': { enabled: true, languages: ['en-GB', 'en-US'] },
        'sonic-3.6': { enabled: true, languages: ['es-MX', 'en-US'] },
      },
      voices: [
        { voice_id: 'voice-uuid', name: 'Charlotte - Heiress', country: 'GB', models: ['sonic-2'], enabled: true, languages: ['en-GB'] },
        { voice_id: 'mexico-voice', name: 'Regina - Friendly Specialist', country: 'MX', models: ['sonic-3.6'], enabled: true, languages: ['es-MX'] },
      ],
    },
    elevenlabs: {
      enabled: true,
      voice_input: 'catalog_or_custom',
      speed_range: { min: 0.7, max: 1.2 },
      models: { eleven_flash_v2_5: { enabled: true, languages: ['en-US', 'es-MX'] } },
      voices: [
        { voice_id: 'Xb7hH8MSUJpSbSDYk0k2', name: 'Alice', country: 'GB', models: ['eleven_flash_v2_5'], enabled: true, languages: ['en-US', 'es-MX'] },
      ],
    },
  },
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
    expect(screen.getByRole('option', { name: 'Regina - Friendly Specialist · 墨西哥' })).toHaveValue('mexico-voice');
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

  it('keeps all voices visible and switches to the selected voice model', () => {
    const onChange = vi.fn();
    render(<RuntimeSection config={config} catalog={catalog} onChange={onChange} readOnly={false} />);
    fireEvent.change(screen.getByLabelText('TTS 音色'), { target: { value: 'mexico-voice' } });
    expect(onChange.mock.calls[0][0].tts).toMatchObject({
      model: 'sonic-3.6', voice_id: 'mexico-voice', language: 'es-MX',
    });
  });

  it('changes the ASR provider and selects its supported model', () => {
    const onChange = vi.fn();
    render(<RuntimeSection config={config} catalog={catalog} onChange={onChange} readOnly={false} />);
    fireEvent.change(screen.getByLabelText('ASR 厂商'), { target: { value: 'assemblyai' } });
    expect(onChange.mock.calls[0][0].asr).toMatchObject({
      provider: 'assemblyai', model: 'u3-rt-pro', language: 'en-US', sample_rate: 8000,
    });
  });

  it('supports a custom Cartesia Voice ID', () => {
    const onChange = vi.fn();
    const { rerender } = render(<RuntimeSection config={config} catalog={catalog} onChange={onChange} readOnly={false} />);
    fireEvent.change(screen.getByLabelText('TTS 音色'), { target: { value: '' } });
    const custom = onChange.mock.calls[0][0];
    expect(custom.tts.voice_id).toBe('');

    rerender(<RuntimeSection config={custom} catalog={catalog} onChange={onChange} readOnly={false} />);
    const input = screen.getByLabelText('TTS Voice ID');
    fireEvent.change(input, { target: { value: 'cartesia-account-voice' } });
    expect(onChange.mock.calls[1][0].tts).toMatchObject({
      provider: 'cartesia', voice_id: 'cartesia-account-voice', language: 'en-GB',
    });
    expect(screen.getByLabelText('TTS 语言')).toHaveTextContent('en-US');
  });

  it('offers official ElevenLabs voices and a custom Voice ID', () => {
    const onChange = vi.fn();
    const { rerender } = render(<RuntimeSection config={config} catalog={catalog} onChange={onChange} readOnly={false} />);
    fireEvent.change(screen.getByLabelText('TTS 厂商'), { target: { value: 'elevenlabs' } });
    const official = onChange.mock.calls[0][0];
    expect(official.tts).toMatchObject({
      provider: 'elevenlabs', model: 'eleven_flash_v2_5', voice_id: 'Xb7hH8MSUJpSbSDYk0k2', language: 'en-US',
    });

    rerender(<RuntimeSection config={official} catalog={catalog} onChange={onChange} readOnly={false} />);
    expect(screen.getByRole('option', { name: 'Alice · 英国' })).toHaveValue('Xb7hH8MSUJpSbSDYk0k2');
    fireEvent.change(screen.getByLabelText('TTS 音色'), { target: { value: '' } });
    const custom = onChange.mock.calls[1][0];
    expect(custom.tts.voice_id).toBe('');

    rerender(<RuntimeSection config={custom} catalog={catalog} onChange={onChange} readOnly={false} />);
    const input = screen.getByLabelText('TTS Voice ID');
    fireEvent.change(input, { target: { value: 'account-voice' } });
    expect(onChange.mock.calls[2][0].tts.voice_id).toBe('account-voice');
  });
});
