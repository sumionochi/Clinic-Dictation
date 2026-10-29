import { session, systemPreferences } from 'electron'

import type { MicStatus } from '../shared/sidecar'

export function getMicStatus(): MicStatus {
  if (process.platform !== 'darwin' && process.platform !== 'win32') return 'granted'
  return systemPreferences.getMediaAccessStatus('microphone')
}

/** Shows the macOS microphone prompt the first time; afterwards returns the saved answer. */
export async function requestMicAccess(): Promise<MicStatus> {
  if (process.platform === 'darwin') {
    await systemPreferences.askForMediaAccess('microphone')
  }
  return getMicStatus()
}

/**
 * The renderer may use the microphone and nothing else: no camera, screen capture,
 * location, notifications or other web permissions.
 */
export function restrictWebPermissions(): void {
  const ses = session.defaultSession
  ses.setPermissionRequestHandler((_webContents, permission, callback, details) => {
    const audioOnly =
      permission === 'media' &&
      'mediaTypes' in details &&
      (details.mediaTypes ?? []).every((type) => type === 'audio')
    callback(audioOnly)
  })
  ses.setPermissionCheckHandler((_webContents, permission) => permission === 'media')
}
