import { createContext, useContext } from 'react'

import type { UiLanguage } from '@shared/sidecar'

import { de, en, type MessageKey } from './messages'

export type { MessageKey }
export type Vars = Record<string, string | number>

const CATALOGUE: Record<UiLanguage, Record<MessageKey, string>> = { en, de }
const LOCALE: Record<UiLanguage, string> = { en: 'en-GB', de: 'de-DE' }
const STORAGE_KEY = 'clinic-dictation.language'

export interface I18n {
  lang: UiLanguage
  setLang: (lang: UiLanguage) => void
  t: (key: MessageKey, vars?: Vars) => string
  /** Time of day, e.g. "14:05". */
  time: (iso: string) => string
  /** Date and time, e.g. "30.10.2026, 14:05" or "30/10/2026, 14:05". */
  dateTime: (iso: string) => string
  /** A person's name: default English names from the sidecar ("Doctor", "Companion 2",
   * "Speaker 1") in the UI language; descriptions the doctor typed stay as they are. */
  person: (name: string | null | undefined) => string
}

export const I18nContext = createContext<I18n | null>(null)

export function useI18n(): I18n {
  const value = useContext(I18nContext)
  if (!value) throw new Error('useI18n must be used inside I18nContext')
  return value
}

export function isMessageKey(key: string): key is MessageKey {
  return key in en
}

export function initialLanguage(): UiLanguage {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'en' || saved === 'de') return saved
  } catch {
    // Storage unavailable: fall back to the system language.
  }
  return navigator.language.toLowerCase().startsWith('de') ? 'de' : 'en'
}

export function rememberLanguage(lang: UiLanguage): void {
  try {
    localStorage.setItem(STORAGE_KEY, lang)
  } catch {
    // Not remembered; the choice still applies until the app closes.
  }
}

// The sidecar's default names (sidecar/src/clinic_sidecar/consent.py ROLE_TITLES).
const DEFAULT_NAMES: Record<string, MessageKey> = {
  Doctor: 'role.doctor',
  Patient: 'role.patient',
  Companion: 'role.companion',
  Interpreter: 'role.interpreter',
  Student: 'role.student',
  'Other person': 'role.other'
}

export function makeI18n(lang: UiLanguage, setLang: (lang: UiLanguage) => void): I18n {
  const messages = CATALOGUE[lang]
  const t = (key: MessageKey, vars?: Vars): string =>
    messages[key].replace(/\{(\w+)\}/g, (m, name: string) =>
      vars && name in vars ? String(vars[name]) : m
    )
  const locale = LOCALE[lang]
  return {
    lang,
    setLang,
    t,
    time: (iso) => new Date(iso).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' }),
    dateTime: (iso) =>
      new Date(iso).toLocaleString(locale, {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      }),
    person: (name) => {
      if (!name) return t('transcript.unknown')
      const speaker = /^Speaker (\d+)$/.exec(name)
      if (speaker) return t('person.speaker', { n: speaker[1] })
      const numbered = /^(.+?)(?: (\d+))?$/.exec(name)
      const key = numbered && DEFAULT_NAMES[numbered[1]]
      if (!key) return name
      return numbered[2] ? `${t(key)} ${numbered[2]}` : t(key)
    }
  }
}
