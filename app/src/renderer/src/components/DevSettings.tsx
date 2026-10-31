import { Settings } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Switch } from '@/components/ui/switch'
import { useI18n } from '@/i18n/i18n'
import { useConsultation } from '@/lib/consultation'
import { demoApi } from '@/lib/demo'

/** Development only (npm run dev): a switch that opens a synthetic consultation on
 * Review, for demos and screenshots. Off, the app runs its normal flow. */
export function DevSettings(): React.JSX.Element {
  const { t } = useI18n()
  const { sessionId, setSessionId, goTo } = useConsultation()
  const [demoId, setDemoId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // On only while the demo is the open consultation; starting a real one turns it off.
  const on = demoId !== null && demoId === sessionId

  async function toggle(next: boolean): Promise<void> {
    setError(null)
    if (!next) {
      setSessionId(null)
      goTo('consent')
      return
    }
    setBusy(true)
    try {
      const id = await demoApi.create()
      setDemoId(id)
      setSessionId(id)
      goTo('review')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={t('dev.settings')}>
          <Settings />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="flex flex-col gap-3">
        <p className="text-xs font-medium text-muted-foreground">{t('dev.settings')}</p>
        <div className="flex items-center justify-between gap-3">
          <Label htmlFor="dev-demo">{t('dev.demo')}</Label>
          <Switch
            id="dev-demo"
            checked={on}
            disabled={busy}
            onCheckedChange={(next) => void toggle(next)}
          />
        </div>
        <p className="text-xs text-muted-foreground">{t('dev.demoHint')}</p>
        {error && <p className="text-xs text-destructive">{error}</p>}
      </PopoverContent>
    </Popover>
  )
}
