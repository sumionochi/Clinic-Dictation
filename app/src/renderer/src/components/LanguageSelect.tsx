import { Languages } from 'lucide-react'

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/select'
import { useI18n } from '@/i18n/i18n'
import type { UiLanguage } from '@shared/sidecar'

// Each language is named in itself, so it can be found whichever one is active.
const OPTIONS: { value: UiLanguage; name: string }[] = [
  { value: 'en', name: 'English' },
  { value: 'de', name: 'Deutsch' }
]

export function LanguageSelect(): React.JSX.Element {
  const { lang, setLang, t } = useI18n()
  return (
    <Select value={lang} onValueChange={(v) => setLang(v as UiLanguage)}>
      <SelectTrigger size="sm" className="w-full" aria-label={t('app.language')}>
        <Languages data-icon="inline-start" />
        <SelectValue className="flex-1" />
      </SelectTrigger>
      <SelectContent>
        {OPTIONS.map((o) => (
          <SelectItem key={o.value} value={o.value} lang={o.value}>
            {o.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
