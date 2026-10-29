// Typed client for the sidecar's /sessions API (sidecar/src/clinic_sidecar/routes_consent.py).

import type { HttpMethod } from '@shared/sidecar'

export type Role = 'doctor' | 'patient' | 'companion' | 'interpreter' | 'student' | 'other'
export type Decision = 'granted' | 'declined' | 'unable'

export interface Participant {
  id: string
  role: Role
  label: string | null
  display_name: string
  added_at: string
  decision: Decision | 'pending'
  decided_at: string | null
}

export interface Session {
  id: string
  created_at: string
  mode: 'consent' | 'manual'
  participants: Participant[]
  recording_allowed: boolean
  blockers: string[]
}

export const ROLE_OPTIONS: { value: Role; title: string }[] = [
  { value: 'doctor', title: 'Doctor' },
  { value: 'patient', title: 'Patient' },
  { value: 'companion', title: 'Companion' },
  { value: 'interpreter', title: 'Interpreter' },
  { value: 'student', title: 'Student' },
  { value: 'other', title: 'Other person' }
]

export class ApiError extends Error {}

async function call<T>(method: HttpMethod, route: string, body?: unknown): Promise<T> {
  const res = await window.api.sidecar.call(method, route, body)
  if (!res.ok) {
    const detail = (res.body as { detail?: unknown } | null)?.detail
    // FastAPI validation errors come back as a list of {msg} objects.
    const message = Array.isArray(detail)
      ? detail.map((d: { msg?: string }) => d.msg ?? String(d)).join('; ')
      : typeof detail === 'string'
        ? detail
        : `Request failed (${res.status})`
    throw new ApiError(message)
  }
  return res.body as T
}

export const sessionsApi = {
  create: () => call<Session>('POST', '/sessions'),
  get: (id: string) => call<Session>('GET', `/sessions/${id}`),
  addParticipant: (id: string, role: Role, label?: string) =>
    call<Session>('POST', `/sessions/${id}/participants`, { role, label: label || null }),
  removeParticipant: (id: string, participantId: string) =>
    call<Session>('DELETE', `/sessions/${id}/participants/${participantId}`),
  decide: (id: string, participantId: string, decision: Decision) =>
    call<Session>('POST', `/sessions/${id}/participants/${participantId}/consent`, { decision }),
  switchToManual: (id: string) => call<Session>('POST', `/sessions/${id}/manual`)
}
