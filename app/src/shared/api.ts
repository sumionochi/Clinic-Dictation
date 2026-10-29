import type { MicStatus, SidecarState } from './sidecar'

/** What the preload script exposes to the renderer as `window.api`. */
export interface Api {
  sidecar: {
    getState: () => Promise<SidecarState>
    restart: () => Promise<void>
    /** Subscribes to status changes; returns an unsubscribe function. */
    onState: (callback: (state: SidecarState) => void) => () => void
  }
  mic: {
    getStatus: () => Promise<MicStatus>
    /** Shows the macOS microphone prompt the first time it is called. */
    request: () => Promise<MicStatus>
  }
}
