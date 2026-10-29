import type { HttpMethod, MicStatus, SidecarResponse, SidecarState } from './sidecar'

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
  mic: {
    getStatus: () => Promise<MicStatus>
    /** Shows the macOS microphone prompt the first time it is called. */
    request: () => Promise<MicStatus>
  }
}
