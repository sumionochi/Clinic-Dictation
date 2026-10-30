import type { HttpMethod, MicStatus, SidecarResponse, SidecarState, UiLanguage } from './sidecar'

/** What the preload script exposes to the renderer as `window.api`. */
export interface Api {
  sidecar: {
    getState: () => Promise<SidecarState>
    restart: () => Promise<void>
    /** JSON request to the sidecar; the main process adds the auth token. */
    call: (method: HttpMethod, route: string, body?: unknown) => Promise<SidecarResponse>
    /** Subscribes to status changes; returns an unsubscribe function. */
    onState: (callback: (state: SidecarState) => void) => () => void
  }
  recording: {
    /** Appends 16 kHz 16-bit mono PCM to a recording; the sidecar saves it before answering. */
    sendAudio: (
      sessionId: string,
      recordingId: string,
      pcm: ArrayBuffer
    ) => Promise<SidecarResponse>
  }
  export: {
    /** Asks where to save, then writes the approved FHIR document. Null if cancelled.
     * `lang` is the language of the save dialog. */
    saveFhir: (sessionId: string, lang: UiLanguage) => Promise<string | null>
    /** Same, for the readable PDF/A document. */
    savePdf: (sessionId: string, lang: UiLanguage) => Promise<string | null>
  }
  mic: {
    getStatus: () => Promise<MicStatus>
    /** Shows the macOS microphone prompt the first time it is called. */
    request: () => Promise<MicStatus>
  }
}
