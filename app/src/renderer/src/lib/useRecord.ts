import { useEffect, useState } from 'react'

import { recordsApi, type ClinicalRecord } from '@/lib/records'

interface RecordData {
  record: ClinicalRecord | null
  loaded: boolean
  error: string | null
  setRecord: (r: ClinicalRecord) => void
  refresh: () => void
}

/** The record of the open consultation, polled while the agents are working. */
export function useRecord(sessionId: string | null): RecordData {
  const [record, setRecord] = useState<ClinicalRecord | null>(null)
  const [loadedFor, setLoadedFor] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    recordsApi.get(sessionId).then(
      (r) => {
        if (cancelled) return
        setRecord(r)
        setLoadedFor(sessionId)
        setError(null)
      },
      (e: Error) => !cancelled && setError(e.message)
    )
    return () => {
      cancelled = true
    }
  }, [sessionId, tick])

  const extracting = record?.status === 'extracting'
  useEffect(() => {
    if (!extracting) return
    const timer = setInterval(() => setTick((t) => t + 1), 2000)
    return () => clearInterval(timer)
  }, [extracting])

  const current = record && record.session_id === sessionId ? record : null
  return {
    record: current,
    loaded: loadedFor === sessionId,
    error,
    setRecord,
    refresh: () => setTick((t) => t + 1)
  }
}
