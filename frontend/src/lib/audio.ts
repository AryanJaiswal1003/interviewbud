/** PCM playback for the interviewer's streamed voice + WAV encoding for candidate utterances. */

export class PCMPlayer {
  ctx: AudioContext;
  node: AudioWorkletNode | null = null;
  private el: HTMLAudioElement;
  onLevel: (rms: number) => void = () => {};
  onStarted: () => void = () => {};
  onDrained: () => void = () => {};

  private sampleRate: number;

  constructor(sampleRate: number) {
    this.sampleRate = sampleRate;
    this.ctx = new AudioContext({ latencyHint: "interactive" });
    this.el = new Audio();
    this.el.autoplay = true;
  }

  async init() {
    await this.ctx.audioWorklet.addModule("/pcm-player.worklet.js");
    this.node = new AudioWorkletNode(this.ctx, "pcm-player", {
      numberOfInputs: 0,
      outputChannelCount: [1],
      processorOptions: { sampleRate: this.sampleRate },
    });
    this.node.port.onmessage = (e) => {
      const m = e.data;
      if (m.type === "level") this.onLevel(m.rms);
      else if (m.type === "started") this.onStarted();
      else if (m.type === "drained") this.onDrained();
    };
    // Route through a media element so the browser's echo canceller sees the interviewer's voice
    // and doesn't hand it back to us through the microphone.
    const dest = this.ctx.createMediaStreamDestination();
    this.node.connect(dest);
    this.el.srcObject = dest.stream;
    await this.resume();
  }

  async resume() {
    if (this.ctx.state !== "running") await this.ctx.resume();
    try {
      await this.el.play();
    } catch {
      /* needs a user gesture; start() is called from one */
    }
  }

  push(pcm: ArrayBuffer) {
    this.node?.port.postMessage({ type: "pcm", data: pcm }, [pcm]);
  }

  clear() {
    this.node?.port.postMessage({ type: "clear" });
  }

  duck(level: number) {
    this.node?.port.postMessage({ type: "duck", level });
  }

  close() {
    this.el.srcObject = null;
    void this.ctx.close();
  }
}

/** Float32 @16 kHz -> 16-bit PCM WAV. */
export function encodeWav(samples: Float32Array, sampleRate = 16000): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buffer);
  const str = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF");
  v.setUint32(4, 36 + samples.length * 2, true);
  str(8, "WAVE");
  str(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, sampleRate, true);
  v.setUint32(28, sampleRate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  str(36, "data");
  v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

/** Play a short sample of an interviewer voice (POST /api/voices/preview returns raw PCM16). */
export async function playPreview(ctx: AudioContext, voiceId: string, text: string): Promise<void> {
  const res = await fetch("/api/voices/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ fish_model_id: voiceId, text }),
  });
  if (!res.ok) throw new Error((await res.text()).slice(0, 200));
  const sr = Number(res.headers.get("X-Sample-Rate") || 24000);
  const pcm = new Int16Array(await res.arrayBuffer());
  const buf = ctx.createBuffer(1, pcm.length, sr);
  const ch = buf.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.connect(ctx.destination);
  await ctx.resume();
  await new Promise<void>((done) => {
    src.onended = () => done();
    src.start();
  });
}
