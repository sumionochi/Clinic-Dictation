// Typed client for the sidecar's recording API (sidecar/src/clinic_sidecar/routes_recording.py).

import { ApiError } from '@/lib/sessions'

export type RecordingStatus =
  'recording' | 'recorded' | 'transcribing' | 'transcribed' | 'failed' | 'interrupted'

export type Language = 'de' | 'en'

export interface Word {
  word: string
  start: number
  end: number
  probability: number
}

export interface TranscriptSegment {
  idx: number
  start: number
  end: number
  text: string
  words: Word[]
}

export interface Recording {
  id: string
  session_id: string
  status: RecordingStatus
  language: Language | null
  duration_s: number
  started_at: string
  stopped_at: string | null
  model: string | null
  transcribe_seconds: number | null
  error: string | null
  segments: TranscriptSegment[]
}

async function call<T>(method: 'GET' | 'POST', route: string, body?: unknown): Promise<T> {
  const res = await window.api.sidecar.call(method, route, body)
  if (!res.ok) {
    const detail = (res.body as { detail?: unknown } | null)?.detail
    throw new ApiError(typeof detail === 'string' ? detail : `Request failed (${res.status})`)
  }
  return res.body as T
}

export const recordingsApi = {
  get: (sessionId: string) => call<Recording | null>('GET', `/sessions/${sessionId}/recording`),
  start: (sessionId: string, language: Language | null) =>
    call<Recording>('POST', `/sessions/${sessionId}/recordings`, { language }),
  stop: (sessionId: string, recordingId: string) =>
    call<Recording>('POST', `/sessions/${sessionId}/recordings/${recordingId}/stop`),
  transcribe: (sessionId: string, recordingId: string) =>
    call<Recording>('POST', `/sessions/${sessionId}/recordings/${recordingId}/transcribe`)
}

export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
