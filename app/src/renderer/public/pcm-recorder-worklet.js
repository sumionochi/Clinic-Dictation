// Adapted from TranscriptionSuite (https://github.com/homelab-00/TranscriptionSuite) —
// dashboard/public/audio-worklet-processor.js: resampling the microphone to 16 kHz with
// linear interpolation and converting to 16-bit PCM in the audio thread.
// Changed here: a pause that drops audio before it ever leaves this thread, and a
// level value for the meter.

/**
 * Runs in the audio rendering thread. Turns microphone input (usually 48 kHz)
 * into 16 kHz mono 16-bit PCM and posts it to the page in ~100 ms chunks.
 *
 * Messages in: { type: 'pause' } | { type: 'resume' }
 * Messages out: { type: 'audio', data: ArrayBuffer, level: number }
 */
class PcmRecorderProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super(options)
    const target = Number(options?.processorOptions?.targetSampleRateHz)
    this._targetRate = Number.isFinite(target) && target > 0 ? Math.round(target) : 16000
    this._ratio = sampleRate / this._targetRate // `sampleRate` is the worklet global
    this._chunkSize = Math.max(1, Math.round(this._targetRate / 10))
    this._buffer = []
    this._bufferLength = 0
    this._paused = false
    this.port.onmessage = (event) => {
      if (event.data?.type === 'pause') {
        this._paused = true
        // Drop anything half-collected so no paused audio slips into the next chunk.
        this._buffer = []
        this._bufferLength = 0
      } else if (event.data?.type === 'resume') {
        this._paused = false
      }
    }
  }

  process(inputs) {
    const input = inputs[0]
    if (this._paused || !input || !input[0] || input[0].length === 0) return true

    this._buffer.push(new Float32Array(input[0]))
    this._bufferLength += input[0].length

    const needed = this._chunkSize * this._ratio
    while (this._bufferLength >= needed) {
      const full = this._concatenate()
      const consume = Math.ceil(needed)
      const downsampled = this._downsample(full.subarray(0, consume))
      const int16 = this._toInt16(downsampled)
      this.port.postMessage({ type: 'audio', data: int16.buffer, level: this._rms(downsampled) }, [
        int16.buffer
      ])
      const rest = full.subarray(consume)
      this._buffer = rest.length > 0 ? [new Float32Array(rest)] : []
      this._bufferLength = rest.length
    }
    return true
  }

  _downsample(samples) {
    const outLen = Math.floor(samples.length / this._ratio)
    const out = new Float32Array(outLen)
    for (let i = 0; i < outLen; i++) {
      const src = i * this._ratio
      const lo = Math.floor(src)
      const hi = Math.min(lo + 1, samples.length - 1)
      const frac = src - lo
      out[i] = samples[lo] * (1 - frac) + samples[hi] * frac
    }
    return out
  }

  _toInt16(float32) {
    const int16 = new Int16Array(float32.length)
    for (let i = 0; i < float32.length; i++) {
      const s = Math.max(-1, Math.min(1, float32[i]))
      int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff
    }
    return int16
  }

  _rms(samples) {
    let sum = 0
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i]
    return Math.sqrt(sum / Math.max(1, samples.length))
  }

  _concatenate() {
    const out = new Float32Array(this._bufferLength)
    let offset = 0
    for (const buf of this._buffer) {
      out.set(buf, offset)
      offset += buf.length
    }
    return out
  }
}

registerProcessor('pcm-recorder', PcmRecorderProcessor)
