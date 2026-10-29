import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'

import type { Api } from '../shared/api'
import type { SidecarState } from '../shared/sidecar'

// The renderer never sees the sidecar's auth token; it only gets status and
// asks the main process to act.
const api: Api = {
  sidecar: {
    getState: () => ipcRenderer.invoke('sidecar:getState'),
    restart: () => ipcRenderer.invoke('sidecar:restart'),
    call: (method, route, body) => ipcRenderer.invoke('sidecar:call', method, route, body),
    onState: (callback) => {
      const listener = (_event: IpcRendererEvent, state: SidecarState): void => callback(state)
      ipcRenderer.on('sidecar:state', listener)
      return () => ipcRenderer.removeListener('sidecar:state', listener)
    }
  },
  mic: {
    getStatus: () => ipcRenderer.invoke('mic:getStatus'),
    request: () => ipcRenderer.invoke('mic:request')
  }
}

// Use `contextBridge` APIs to expose Electron APIs to
// renderer only if context isolation is enabled, otherwise
// just add to the DOM global.
if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', electronAPI)
    contextBridge.exposeInMainWorld('api', api)
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore (define in dts)
  window.electron = electronAPI
  // @ts-ignore (define in dts)
  window.api = api
}
