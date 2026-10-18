// Streams PCM16 mono into the audio graph through a ring buffer. Barge-in = one "clear" message.
class PCMPlayer extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.inRate = options.processorOptions.sampleRate;
    this.ratio = this.inRate / sampleRate; // input samples per output sample
    this.buf = new Float32Array(this.inRate * 120);
    this.read = 0;
    this.write = 0;
    this.pos = 0;
    this.gain = 1;
    this.target = 1;
    this.playing = false;
    this.frames = 0;
    this.port.onmessage = (e) => {
      const m = e.data;
      if (m.type === "pcm") {
        const v = new Int16Array(m.data);
        for (let i = 0; i < v.length; i++) {
          this.buf[this.write] = v[i] / 32768;
          this.write = (this.write + 1) % this.buf.length;
        }
      } else if (m.type === "clear") {
        this.read = this.write;
        this.pos = 0;
      } else if (m.type === "duck") {
        this.target = m.level;
      }
    };
  }

  available() {
    return (this.write - this.read + this.buf.length) % this.buf.length;
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    let sum = 0;
    for (let i = 0; i < out.length; i++) {
      this.gain += (this.target - this.gain) * 0.01;
      if (this.available() > 1) {
        const a = this.buf[this.read];
        const b = this.buf[(this.read + 1) % this.buf.length];
        const s = (a + (b - a) * this.pos) * this.gain;
        out[i] = s;
        sum += s * s;
        this.pos += this.ratio;
        while (this.pos >= 1) {
          this.pos -= 1;
          this.read = (this.read + 1) % this.buf.length;
        }
      } else {
        out[i] = 0;
      }
    }
    const nowPlaying = this.available() > 1;
    if (nowPlaying !== this.playing) {
      this.playing = nowPlaying;
      this.port.postMessage({ type: nowPlaying ? "started" : "drained" });
    }
    if (++this.frames % 4 === 0) {
      this.port.postMessage({ type: "level", rms: Math.sqrt(sum / out.length) });
    }
    return true;
  }
}

registerProcessor("pcm-player", PCMPlayer);
