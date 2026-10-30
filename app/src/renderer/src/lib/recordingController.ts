// The live recording, kept outside React so it keeps running while the doctor
// switches screens. Screens read it with useRecordingController().

import { useSyncExternalStore } from 'react'

import { MicRecorder } from '@/lib/micRecorder'
import { recordingsApi, type Language } from '@/lib/recordings'

const SAMPLE_RATE = 16_000

export type Phase = 'idle' | 'starting' | 'recording' | 'paused' | 'stopping'

export interface ControllerState {
  phase: Phase
  sessionId: string | null
  recordingId: string | null
  /** Seconds of audio saved by the sidecar (paused time is not recorded). */
  savedSeconds: number
  level: number
  error: string | null
  /** The sidecar refused the audio and recording stopped; `error` holds its reason, if any. */
  refused: boolean
}

const IDLE: ControllerState = {
  phase: 'idle',
  sessionId: null,
  recordingId: null,
  savedSeconds: 0,
  level: 0,
  error: null,
  refused: false
}

let state: ControllerState = IDLE
const listeners = new Set<() => void>()

function set(patch: Partial<ControllerState>): void {
  state = { ...state, ...patch }
  listeners.forEach((l) => l())
}

let mic: MicRecorder | null = null
let pending: Int16Array[] = []
let pendingSamples = 0
// Uploads run one after another so chunks reach the file in order.
let uploads: Promise<void> = Promise.resolve()
let rejected = false

function queueUpload(): Promise<void> {
  if (pendingSamples === 0 || !state.sessionId || !state.recordingId) return uploads
  const pcm = new Int16Array(pendingSamples)
  let offset = 0
  for (const chunk of pending) {
    pcm.set(chunk, offset)
    offset += chunk.length
  }
  pending = []
  pendingSamples = 0
  const { sessionId, recordingId } = state
  uploads = uploads.then(async () => {
    if (rejected) return
    const res = await window.api.recording.sendAudio(sessionId, recordingId, pcm.buffer)
    if (res.ok) {
      set({ savedSeconds: (res.body as { samples: number }).samples / SAMPLE_RATE })
    } else {
      // The sidecar refused the audio, e.g. because consent changed. Stop at once.
      rejected = true
      const detail = (res.body as { detail?: string } | null)?.detail
      void stopRecording(detail ?? null)
    }
  })
  return uploads
}

export async function startRecording(sessionId: string, language: Language | null): Promise<void> {
  if (state.phase !== 'idle') return
  set({ ...IDLE, phase: 'starting', sessionId })
  try {
    const recording = await recordingsApi.start(sessionId, language)
    rejected = false
    set({ recordingId: recording.id })
    mic = new MicRecorder({
      onAudio: (chunk) => {
        pending.push(chunk)
        pendingSamples += chunk.length
        // Send about once a second.
        if (pendingSamples >= SAMPLE_RATE) void queueUpload()
      },
      onLevel: (level) => set({ level })
    })
    await mic.start()
    set({ phase: 'recording' })
  } catch (e) {
    await mic?.stop()
    mic = null
    set({ ...IDLE, error: e instanceof Error ? e.message : String(e) })
  }
}

export function pauseRecording(): void {
  if (state.phase !== 'recording') return
  mic?.pause()
  void queueUpload()
  set({ phase: 'paused', level: 0 })
}

export function resumeRecording(): void {
  if (state.phase !== 'paused') return
  mic?.resume()
  set({ phase: 'recording' })
}

/** Stops the microphone, waits until every chunk is saved, then starts transcription. */
export async function stopRecording(refusal?: string | null): Promise<void> {
  if (state.phase === 'idle' || state.phase === 'stopping') return
  const { sessionId, recordingId } = state
  set({ phase: 'stopping', level: 0 })
  await mic?.stop()
  mic = null
  await queueUpload()
  try {
    if (sessionId && recordingId) await recordingsApi.stop(sessionId, recordingId)
    set({ ...IDLE, error: refusal ?? null, refused: refusal !== undefined })
  } catch (e) {
    set({ ...IDLE, error: e instanceof Error ? e.message : String(e) })
  }
}

export function useRecordingController(): ControllerState {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => state
  )
}
