// Types shared by the main process, preload bridge and renderer.

export type SidecarStatus = 'stopped' | 'starting' | 'running' | 'stopping' | 'error'

export interface SidecarState {
  status: SidecarStatus
  port: number | null
  version: string | null
  error: string | null
}

/** The app's interface language. */
export type UiLanguage = 'en' | 'de'

export type MicStatus = 'not-determined' | 'granted' | 'denied' | 'restricted' | 'unknown'

export type HttpMethod = 'GET' | 'POST' | 'DELETE'

export interface SidecarResponse {
  ok: boolean
  status: number
  body: unknown
}
