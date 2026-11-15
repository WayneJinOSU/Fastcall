type MockCallClientOptions = {
  onMessage: (event: Record<string, any>) => void;
  onClose: () => void;
};

export class MockCallClient {
  private ws: WebSocket | null = null;
  private stream: MediaStream | null = null;
  private captureContext: AudioContext | null = null;
  private playbackContext: AudioContext | null = null;
  private processor: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private playbackSources = new Set<AudioBufferSourceNode>();
  private nextStartTime = 0;
  private active = false;
  private muted = false;

  constructor(private options: MockCallClientOptions) {}

  async openAudio() {
    this.captureContext = new AudioContext();
    this.playbackContext = new AudioContext();
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.source = this.captureContext.createMediaStreamSource(this.stream);
    this.processor = this.captureContext.createScriptProcessor(2048, 1, 1);
    this.source.connect(this.processor);
    this.processor.connect(this.captureContext.destination);
    this.processor.onaudioprocess = event => this.captureAudio(event.inputBuffer);
  }

  async connect(wsUrl: string) {
    if (!this.stream) await this.openAudio();
    await new Promise<void>((resolve, reject) => {
      const ws = new WebSocket(wsUrl);
      this.ws = ws;
      ws.binaryType = 'arraybuffer';
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error('无法连接 Mock Call 服务'));
      ws.onmessage = event => {
        if (event.data instanceof ArrayBuffer) {
          this.playAudio(event.data);
          return;
        }
        try {
          const message = JSON.parse(event.data);
          if (message.event === 'ready') this.active = true;
          if (message.event === 'ended' || message.event === 'error') this.active = false;
          this.options.onMessage(message);
        } catch {
          return;
        }
      };
      ws.onclose = () => {
        this.active = false;
        this.options.onClose();
      };
    });
  }

  answer() { this.control('answer'); }
  reject() { this.control('reject'); }
  hangup() { this.control('hangup'); }
  setMuted(value: boolean) { this.muted = value; }

  private control(action: string) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ action }));
    }
  }

  dispose() {
    this.active = false;
    this.playbackSources.forEach(source => { try { source.stop(); } catch { return; } });
    this.playbackSources.clear();
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
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
  }

  private captureAudio(buffer: AudioBuffer) {
    if (!this.active || this.muted || this.ws?.readyState !== WebSocket.OPEN || !this.captureContext) return;
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
    source.buffer = buffer;
    source.connect(this.playbackContext.destination);
    this.playbackSources.add(source);
    source.onended = () => this.playbackSources.delete(source);
    const now = this.playbackContext.currentTime;
    if (this.nextStartTime < now) this.nextStartTime = now + 0.05;
    source.start(this.nextStartTime);
    this.nextStartTime += buffer.duration;
  }
}
