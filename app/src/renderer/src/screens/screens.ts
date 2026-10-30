import type { LucideIcon } from 'lucide-react'
import { FileOutput, ListChecks, Mic, ShieldCheck } from 'lucide-react'
import type { ComponentType } from 'react'

import type { MessageKey } from '@/i18n/i18n'

import { ConsentScreen } from './ConsentScreen'
import { ExportScreen } from './ExportScreen'
import { ReviewScreen } from './ReviewScreen'
import { SessionScreen } from './SessionScreen'

export type ScreenId = 'consent' | 'session' | 'review' | 'export'

export interface ScreenDef {
  id: ScreenId
  label: MessageKey
  icon: LucideIcon
  component: ComponentType
}

// Order matches the consultation flow: nothing is recorded before consent,
// and nothing is exported before the doctor has reviewed it.
export const SCREENS: ScreenDef[] = [
  { id: 'consent', label: 'nav.consent', icon: ShieldCheck, component: ConsentScreen },
  { id: 'session', label: 'nav.session', icon: Mic, component: SessionScreen },
  { id: 'review', label: 'nav.review', icon: ListChecks, component: ReviewScreen },
  { id: 'export', label: 'nav.export', icon: FileOutput, component: ExportScreen }
]
