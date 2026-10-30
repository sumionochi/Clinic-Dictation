import { CircleCheck, CircleDashed, CircleX, LoaderCircle, type LucideIcon } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n, type MessageKey } from '@/i18n/i18n'
import type { MicStatus, SidecarState, SidecarStatus } from '@shared/sidecar'

const ENGINE_LABEL: Record<SidecarStatus, { text: MessageKey; icon: LucideIcon; spin?: boolean }> =
  {
    starting: { text: 'status.engine.starting', icon: LoaderCircle, spin: true },
    running: { text: 'status.engine.running', icon: CircleCheck },
    stopping: { text: 'status.engine.stopping', icon: LoaderCircle, spin: true },
    stopped: { text: 'status.engine.stopped', icon: CircleDashed },
    error: { text: 'status.engine.error', icon: CircleX }
  }

const MIC_LABEL: Record<MicStatus, { text: MessageKey; icon: LucideIcon }> = {
  granted: { text: 'status.mic.granted', icon: CircleCheck },
  'not-determined': { text: 'status.mic.notDetermined', icon: CircleDashed },
  denied: { text: 'status.mic.denied', icon: CircleX },
  restricted: { text: 'status.mic.restricted', icon: CircleX },
  unknown: { text: 'status.mic.unknown', icon: CircleDashed }
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
  const { t } = useI18n()
  const sidecar = useSidecarState()
  const [mic, setMic] = useState<MicStatus | null>(null)

  useEffect(() => {
    void window.api.mic.getStatus().then(setMic)
  }, [])

  const engine = ENGINE_LABEL[sidecar?.status ?? 'starting']
  const micLabel = MIC_LABEL[mic ?? 'unknown']

  return (
    <div className="flex flex-col gap-2 p-4">
      <StatusRow
        label={t('status.engine')}
        text={t(engine.text)}
        icon={engine.icon}
        spin={engine.spin}
      />
      {sidecar?.status === 'error' && (
        <>
          <p className="text-xs break-words text-muted-foreground">{sidecar.error}</p>
          <Button size="xs" variant="outline" onClick={() => void window.api.sidecar.restart()}>
            {t('status.retry')}
          </Button>
        </>
      )}

      <StatusRow label={t('status.microphone')} text={t(micLabel.text)} icon={micLabel.icon} />
      {mic === 'not-determined' && (
        <Button
          size="xs"
          variant="outline"
          onClick={() => void window.api.mic.request().then(setMic)}
        >
          {t('status.allowMic')}
        </Button>
      )}
      {(mic === 'denied' || mic === 'restricted') && (
        <p className="text-xs text-muted-foreground">{t('status.micSettings')}</p>
      )}
    </div>
  )
}
