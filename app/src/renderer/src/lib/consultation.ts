import { createContext, useContext } from 'react'

import type { ScreenId } from '@/screens/screens'

/** The consultation currently open in the app, shared by all screens. */
export interface Consultation {
  sessionId: string | null
  setSessionId: (id: string | null) => void
  goTo: (screen: ScreenId) => void
}

export const ConsultationContext = createContext<Consultation | null>(null)

export function useConsultation(): Consultation {
  const value = useContext(ConsultationContext)
  if (!value) throw new Error('useConsultation must be used inside ConsultationContext')
  return value
}
