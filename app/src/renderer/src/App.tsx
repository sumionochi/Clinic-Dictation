import { useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { LanguageSelect } from '@/components/LanguageSelect'
import { SystemStatus } from '@/components/SystemStatus'
import { I18nContext, initialLanguage, makeI18n, rememberLanguage } from '@/i18n/i18n'
import { ConsultationContext, type Consultation } from '@/lib/consultation'
import { SCREENS, type ScreenId } from '@/screens/screens'
import type { UiLanguage } from '@shared/sidecar'

function App(): React.JSX.Element {
  const [active, setActive] = useState<ScreenId>('consent')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [lang, setLang] = useState<UiLanguage>(initialLanguage)
  const ActiveScreen = SCREENS.find((s) => s.id === active)!.component
  const consultation = useMemo<Consultation>(
    () => ({ sessionId, setSessionId, goTo: setActive }),
    [sessionId]
  )
  const i18n = useMemo(() => makeI18n(lang, setLang), [lang])
  const { t } = i18n

  useEffect(() => {
    document.documentElement.lang = lang
    rememberLanguage(lang)
  }, [lang])

  return (
    <I18nContext.Provider value={i18n}>
      <ConsultationContext.Provider value={consultation}>
        <div className="flex h-screen bg-background text-foreground">
          <aside className="flex w-56 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground">
            {/* Leaves room for the macOS traffic-light buttons with hiddenInset title bar */}
            <div className="app-drag h-12 shrink-0" />
            <div className="flex flex-col gap-3 px-4 pb-4">
              <div>
                <p className="text-sm font-semibold">Clinic Dictation</p>
                <p className="text-xs text-muted-foreground">{t('app.tagline')}</p>
              </div>
              <LanguageSelect />
            </div>
            <Separator />
            <nav className="flex flex-col gap-1 p-2">
              {SCREENS.map((screen, index) => (
                <Button
                  key={screen.id}
                  variant={screen.id === active ? 'secondary' : 'ghost'}
                  className="justify-start"
                  onClick={() => setActive(screen.id)}
                >
                  <screen.icon data-icon="inline-start" />
                  <span className="flex-1 text-left">{t(screen.label)}</span>
                  <span className="text-xs text-muted-foreground">{index + 1}</span>
                </Button>
              ))}
            </nav>
            <div className="mt-auto">
              <Separator />
              <SystemStatus />
            </div>
          </aside>

          <main className="flex-1 overflow-y-auto">
            <div className="app-drag h-12" />
            <ActiveScreen />
          </main>
        </div>
      </ConsultationContext.Provider>
    </I18nContext.Provider>
  )
}

export default App
