// Typed client for the sidecar's record API (sidecar/src/clinic_sidecar/routes_record.py).

import { ApiError } from '@/lib/sessions'

export type Category =
  'complaint' | 'symptom' | 'medication' | 'allergy' | 'condition' | 'surgery' | 'social'
export type Assertion = 'present' | 'absent' | 'past'

export interface Evidence {
  line: number
  quote: string
}

export interface Fact {
  category: Category
  name: string
  details: string | null
  assertion: Assertion
  evidence: Evidence[]
}

export interface ReviewFact {
  id: string
  fact: Fact
  state: 'kept' | 'removed'
  origin: 'agents' | 'verifier_rejected' | 'doctor_edited'
  reason: string | null
}

export interface Line {
  line: number
  speaker: string | null
  text: string
}

export interface Contradiction {
  category: Category
  name: string
  assertions: Assertion[]
  lines: number[]
}

export interface ClinicalRecord {
  session_id: string
  status: 'extracting' | 'ready' | 'failed' | 'approved'
  model: string | null
  lines: Line[]
  facts: ReviewFact[]
  contradictions: Contradiction[]
  log: string[]
  error: string | null
  extract_seconds: number | null
  created_at: string
  approved_at: string | null
  validation: { errors: number; warnings: number; resources: number } | null
}

export const SECTIONS: { title: string; categories: Category[] }[] = [
  { title: 'Complaint and symptoms', categories: ['complaint', 'symptom'] },
  { title: 'Medication', categories: ['medication'] },
  { title: 'Allergies', categories: ['allergy'] },
  { title: 'Previous illnesses', categories: ['condition'] },
  { title: 'Operations', categories: ['surgery'] },
  { title: 'Social history', categories: ['social'] }
]

export const ASSERTION_LABEL: Record<Assertion, string> = {
  present: 'Yes',
  absent: 'No (denied)',
  past: 'Previously'
}

async function call<T>(method: 'GET' | 'POST', route: string, body?: unknown): Promise<T> {
  const res = await window.api.sidecar.call(method, route, body)
  if (!res.ok) {
    const detail = (res.body as { detail?: unknown } | null)?.detail
    throw new ApiError(typeof detail === 'string' ? detail : `Request failed (${res.status})`)
  }
  return res.body as T
}

export const recordsApi = {
  get: (sessionId: string) => call<ClinicalRecord | null>('GET', `/sessions/${sessionId}/record`),
  start: (sessionId: string) => call<ClinicalRecord>('POST', `/sessions/${sessionId}/record`),
  change: (
    sessionId: string,
    factId: string,
    change: {
      action: 'remove' | 'restore' | 'edit'
      name?: string
      details?: string
      assertion?: Assertion
    }
  ) => call<ClinicalRecord>('POST', `/sessions/${sessionId}/record/facts/${factId}`, change),
  approve: (sessionId: string) =>
    call<ClinicalRecord>('POST', `/sessions/${sessionId}/record/approve`)
}
