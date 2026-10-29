// Microphone → 16 kHz mono 16-bit PCM, using public/pcm-recorder-worklet.js.

const TARGET_RATE = 16_000

export interface MicRecorderEvents {
  /** ~100 ms of 16 kHz 16-bit PCM. Never called while paused. */
  onAudio: (pcm: Int16Array) => void
  /** RMS level of the latest chunk, 0..1. */
  onLevel: (level: number) => void
}

export class MicRecorder {
  private stream: MediaStream | null = null
  private ctx: AudioContext | null = null
  private node: AudioWorkletNode | null = null

  constructor(private readonly events: MicRecorderEvents) {}

  async start(): Promise<void> {
    try {
      // Raw microphone: echo cancellation, noise suppression and auto gain
      // change the signal in ways that hurt speech recognition.
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false
        }
      })
      const nativeRate = this.stream.getAudioTracks()[0].getSettings().sampleRate || 48_000
      this.ctx = new AudioContext({ sampleRate: nativeRate })
      await this.ctx.audioWorklet.addModule('./pcm-recorder-worklet.js')
      this.node = new AudioWorkletNode(this.ctx, 'pcm-recorder', {
        processorOptions: { targetSampleRateHz: TARGET_RATE }
      })
      this.node.port.onmessage = (event: MessageEvent) => {
        if (event.data?.type !== 'audio') return
        this.events.onAudio(new Int16Array(event.data.data))
        this.events.onLevel(event.data.level)
      }
      // Not connected to the speakers: we only listen, never play back.
      this.ctx.createMediaStreamSource(this.stream).connect(this.node)
    } catch (err) {
      await this.stop()
      throw err
    }
  }

  pause(): void {
    this.node?.port.postMessage({ type: 'pause' })
    this.events.onLevel(0)
  }

  resume(): void {
    this.node?.port.postMessage({ type: 'resume' })
  }

  async stop(): Promise<void> {
    if (this.node) {
      this.node.port.onmessage = null
      this.node.disconnect()
      this.node = null
    }
    this.stream?.getTracks().forEach((t) => t.stop())
    this.stream = null
    await this.ctx?.close()
    this.ctx = null
  }
}
