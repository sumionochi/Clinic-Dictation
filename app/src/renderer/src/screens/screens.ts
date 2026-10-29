import type { LucideIcon } from 'lucide-react'
import { FileOutput, ListChecks, Mic, ShieldCheck } from 'lucide-react'
import type { ComponentType } from 'react'

import { ConsentScreen } from './ConsentScreen'
import { ExportScreen } from './ExportScreen'
import { ReviewScreen } from './ReviewScreen'
import { SessionScreen } from './SessionScreen'

export type ScreenId = 'consent' | 'session' | 'review' | 'export'

export interface ScreenDef {
  id: ScreenId
  label: string
  icon: LucideIcon
  component: ComponentType
}

// Order matches the consultation flow: nothing is recorded before consent,
// and nothing is exported before the doctor has reviewed it.
export const SCREENS: ScreenDef[] = [
  { id: 'consent', label: 'Consent', icon: ShieldCheck, component: ConsentScreen },
  { id: 'session', label: 'Session', icon: Mic, component: SessionScreen },
  { id: 'review', label: 'Review', icon: ListChecks, component: ReviewScreen },
  { id: 'export', label: 'Export', icon: FileOutput, component: ExportScreen }
]
