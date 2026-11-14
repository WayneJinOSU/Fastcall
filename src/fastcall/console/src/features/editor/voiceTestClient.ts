type VoiceTestClientOptions = {
  onMessage: (event: Record<string, any>) => void;
  onClose: () => void;
  onLevel: (level: number) => void;
};

export class VoiceTestClient {
  private ws: WebSocket | null = null;
  private stream: MediaStream | null = null;
  private captureContext: AudioContext | null = null;
  private playbackContext: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private analyser: AnalyserNode | null = null;
  private playbackSources = new Set<AudioBufferSourceNode>();
  private nextStartTime = 0;
  private muted = false;
  private levelFrame = 0;
  private lastLevelAt = 0;

  constructor(private options: VoiceTestClientOptions) {}

  async openAudio() {
    const AudioContextClass = window.AudioContext;
    this.captureContext = new AudioContextClass();
    this.playbackContext = new AudioContextClass();
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    this.source = this.captureContext.createMediaStreamSource(this.stream);
    this.processor = this.captureContext.createScriptProcessor(2048, 1, 1);
    this.analyser = this.captureContext.createAnalyser();
    this.source.connect(this.analyser);
    this.source.connect(this.processor);
    this.processor.connect(this.captureContext.destination);
    this.processor.onaudioprocess = event => this.captureAudio(event.inputBuffer);
    this.drawLevel();
  }

  async connect(wsUrl: string) {
    if (!this.stream) await this.openAudio();
    await new Promise<void>((resolve, reject) => {
      const ws = new WebSocket(wsUrl);
      this.ws = ws;
      ws.binaryType = 'arraybuffer';
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error('无法连接语音测试服务'));
      ws.onmessage = event => {
        if (event.data instanceof ArrayBuffer) this.playAudio(event.data);
        else {
          try {
            const message = JSON.parse(event.data);
            if (message.event === 'user.speaking' && message.speaking) this.stopPlayback();
            this.options.onMessage(message);
          } catch {
            return;
          }
        }
      };
      ws.onclose = () => {
        this.options.onClose();
        this.dispose();
      };
    });
  }

  setMuted(muted: boolean) {
    this.muted = muted;
  }

  stopPlayback() {
    this.playbackSources.forEach(source => {
      try { source.stop(); } catch { return; }
    });
    this.playbackSources.clear();
    this.nextStartTime = this.playbackContext?.currentTime || 0;
  }

  hangup() {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ action: 'hangup', data: {} }));
    }
    this.dispose();
  }

  dispose() {
    this.stopPlayback();
    cancelAnimationFrame(this.levelFrame);
    this.processor?.disconnect();
    this.source?.disconnect();
    this.stream?.getTracks().forEach(track => track.stop());
    void this.captureContext?.close();
    void this.playbackContext?.close();
    this.processor = null;
    this.source = null;
    this.stream = null;
    this.captureContext = null;
    this.playbackContext = null;
    this.analyser = null;
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    this.options.onLevel(0);
  }

  private captureAudio(buffer: AudioBuffer) {
    if (this.muted || this.ws?.readyState !== WebSocket.OPEN || !this.captureContext) return;
    let samples = buffer.getChannelData(0);
    if (this.captureContext.sampleRate !== 8000) {
      samples = this.resample(samples, this.captureContext.sampleRate, 8000);
    }
    const pcm = new Int16Array(samples.length);
    for (let index = 0; index < samples.length; index += 1) {
      const value = Math.max(-1, Math.min(1, samples[index]));
      pcm[index] = value < 0 ? value * 0x8000 : value * 0x7fff;
    }
    this.ws.send(pcm.buffer);
  }

  private resample(samples: Float32Array, fromRate: number, toRate: number) {
    const ratio = fromRate / toRate;
    const result = new Float32Array(Math.round(samples.length / ratio));
    for (let index = 0; index < result.length; index += 1) {
      const sourceIndex = index * ratio;
      const low = Math.floor(sourceIndex);
      const high = Math.min(low + 1, samples.length - 1);
      const fraction = sourceIndex - low;
      result[index] = samples[low] * (1 - fraction) + samples[high] * fraction;
    }
    return result;
  }

  private playAudio(data: ArrayBuffer) {
    if (!this.playbackContext) return;
    const pcm = new Int16Array(data);
    const samples = new Float32Array(pcm.length);
    for (let index = 0; index < pcm.length; index += 1) samples[index] = pcm[index] / 32768;
    const buffer = this.playbackContext.createBuffer(1, samples.length, 8000);
    buffer.getChannelData(0).set(samples);
    const source = this.playbackContext.createBufferSource();
    this.playbackSources.add(source);
    source.onended = () => this.playbackSources.delete(source);
    source.buffer = buffer;
    source.connect(this.playbackContext.destination);
    const now = this.playbackContext.currentTime;
    if (this.nextStartTime < now) this.nextStartTime = now + 0.05;
    source.start(this.nextStartTime);
    this.nextStartTime += buffer.duration;
  }

  private drawLevel() {
    if (!this.analyser) return;
    const values = new Uint8Array(this.analyser.fftSize);
    const draw = (now: number) => {
      if (!this.analyser) return;
      this.analyser.getByteTimeDomainData(values);
      if (now - this.lastLevelAt > 100) {
        const energy = values.reduce((sum, value) => sum + Math.abs(value - 128), 0) / values.length;
        this.options.onLevel(Math.min(100, Math.round(energy * 4)));
        this.lastLevelAt = now;
      }
      this.levelFrame = requestAnimationFrame(draw);
    };
    this.levelFrame = requestAnimationFrame(draw);
  }
}
