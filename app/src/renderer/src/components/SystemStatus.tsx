import { CircleCheck, CircleDashed, CircleX, LoaderCircle, type LucideIcon } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import type { MicStatus, SidecarState, SidecarStatus } from '@shared/sidecar'

const ENGINE_LABEL: Record<SidecarStatus, { text: string; icon: LucideIcon; spin?: boolean }> = {
  starting: { text: 'Starting', icon: LoaderCircle, spin: true },
  running: { text: 'Running', icon: CircleCheck },
  stopping: { text: 'Stopping', icon: LoaderCircle, spin: true },
  stopped: { text: 'Stopped', icon: CircleDashed },
  error: { text: 'Error', icon: CircleX }
}

const MIC_LABEL: Record<MicStatus, { text: string; icon: LucideIcon }> = {
  granted: { text: 'Allowed', icon: CircleCheck },
  'not-determined': { text: 'Not asked yet', icon: CircleDashed },
  denied: { text: 'Blocked', icon: CircleX },
  restricted: { text: 'Restricted', icon: CircleX },
  unknown: { text: 'Unknown', icon: CircleDashed }
}

function useSidecarState(): SidecarState | null {
  const [state, setState] = useState<SidecarState | null>(null)
  useEffect(() => {
    // Subscribe first so a change that lands during getState() is not lost.
    const unsubscribe = window.api.sidecar.onState(setState)
    void window.api.sidecar.getState().then(setState)
    return unsubscribe
  }, [])
  return state
}

function StatusRow({
  label,
  text,
  icon: Icon,
  spin
}: {
  label: string
  text: string
  icon: LucideIcon
  spin?: boolean
}): React.JSX.Element {
  return (
    <div className="flex items-center justify-between gap-2 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span className="flex items-center gap-1.5 font-medium">
        <Icon className={spin ? 'size-3.5 animate-spin' : 'size-3.5'} />
        {text}
      </span>
    </div>
  )
}

export function SystemStatus(): React.JSX.Element {
  const sidecar = useSidecarState()
  const [mic, setMic] = useState<MicStatus | null>(null)

  useEffect(() => {
    void window.api.mic.getStatus().then(setMic)
  }, [])

  const engine = ENGINE_LABEL[sidecar?.status ?? 'starting']
  const micLabel = MIC_LABEL[mic ?? 'unknown']

  return (
    <div className="flex flex-col gap-2 p-4">
      <StatusRow label="Engine" text={engine.text} icon={engine.icon} spin={engine.spin} />
      {sidecar?.status === 'error' && (
        <>
          <p className="text-xs break-words text-muted-foreground">{sidecar.error}</p>
          <Button size="xs" variant="outline" onClick={() => void window.api.sidecar.restart()}>
            Retry
          </Button>
        </>
      )}

      <StatusRow label="Microphone" text={micLabel.text} icon={micLabel.icon} />
      {mic === 'not-determined' && (
        <Button
          size="xs"
          variant="outline"
          onClick={() => void window.api.mic.request().then(setMic)}
        >
          Allow microphone
        </Button>
      )}
      {(mic === 'denied' || mic === 'restricted') && (
        <p className="text-xs text-muted-foreground">
          Turn it on in System Settings → Privacy &amp; Security → Microphone.
        </p>
      )}
    </div>
  )
}
