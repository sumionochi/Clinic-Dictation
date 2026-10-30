import { app, shell, BrowserWindow, dialog, ipcMain } from 'electron'
import { writeFile } from 'fs/promises'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'

import { SAVE_DIALOG, dialogLanguage } from './dialogText'
import { savePdfDocument } from './documentExport'
import { getMicStatus, requestMicAccess, restrictWebPermissions } from './microphone'
import { SidecarManager } from './sidecarManager'
import type { UiLanguage } from '../shared/sidecar'

const sidecar = new SidecarManager((state) => {
  for (const win of BrowserWindow.getAllWindows()) {
    if (!win.isDestroyed()) win.webContents.send('sidecar:state', state)
  }
})

function registerIpc(): void {
  ipcMain.handle('sidecar:getState', () => sidecar.getState())
  ipcMain.handle('sidecar:restart', () => sidecar.restart())
  ipcMain.handle('sidecar:call', (_event, method: unknown, route: unknown, body: unknown) =>
    sidecar.callJson(method, route, body)
  )
  ipcMain.handle(
    'recording:audio',
    (_event, sessionId: unknown, recordingId: unknown, pcm: unknown) =>
      sidecar.postAudio(sessionId, recordingId, pcm)
  )
  ipcMain.handle('export:savePdf', (event, sessionId: unknown, lang: unknown) => {
    if (typeof sessionId !== 'string' || !/^[0-9a-f]{32}$/.test(sessionId)) {
      throw new Error('Invalid session id')
    }
    // Development-only hook so automated checks can export without the save dialog.
    const testDir = is.dev ? process.env['CLINIC_EXPORT_DIR'] : undefined
    return savePdfDocument(
      sidecar,
      sessionId,
      BrowserWindow.fromWebContents(event.sender),
      dialogLanguage(lang),
      testDir
    )
  })
  ipcMain.handle('export:saveFhir', (event, sessionId: unknown, lang: unknown) =>
    saveFhir(event.sender, sessionId, dialogLanguage(lang))
  )
  ipcMain.handle('mic:getStatus', () => getMicStatus())
  ipcMain.handle('mic:request', () => requestMicAccess())
}

/** Fetches the approved document from the sidecar and writes it where the doctor chooses. */
async function saveFhir(
  sender: Electron.WebContents,
  sessionId: unknown,
  lang: UiLanguage
): Promise<string | null> {
  if (typeof sessionId !== 'string' || !/^[0-9a-f]{32}$/.test(sessionId)) {
    throw new Error('Invalid session id')
  }
  const res = await sidecar.callJson('GET', `/sessions/${sessionId}/record/fhir`, undefined)
  if (!res.ok) {
    throw new Error((res.body as { detail?: string } | null)?.detail ?? 'Export failed')
  }
  const window = BrowserWindow.fromWebContents(sender)
  const date = new Date().toISOString().slice(0, 10)
  const options = {
    title: SAVE_DIALOG[lang].fhirTitle,
    defaultPath: `aufnahmegespraech-${date}.fhir.json`,
    filters: [{ name: SAVE_DIALOG[lang].fhirFilter, extensions: ['json'] }]
  }
  const choice = window
    ? await dialog.showSaveDialog(window, options)
    : await dialog.showSaveDialog(options)
  if (choice.canceled || !choice.filePath) return null
  await writeFile(choice.filePath, JSON.stringify(res.body, null, 2), 'utf-8')
  return choice.filePath
}

function createWindow(): void {
  const mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    show: false,
    title: 'Clinic Dictation',
    titleBarStyle: 'hiddenInset',
    backgroundColor: '#ffffff',
    autoHideMenuBar: true,
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
  })

  // Only real web links go to the system browser; everything else is blocked.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) void shell.openExternal(url)
    return { action: 'deny' }
  })

  // HMR for renderer base on electron-vite cli.
  // Load the remote URL for development or the local html file for production.
  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

app.whenReady().then(() => {
  electronApp.setAppUserModelId('com.clinicdictation.app')

  // Default open or close DevTools by F12 in development
  // and ignore CommandOrControl + R in production.
  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  restrictWebPermissions()
  registerIpc()
  createWindow()
  void sidecar.start()

  app.on('activate', function () {
    // On macOS it's common to re-create a window in the app when the
    // dock icon is clicked and there are no other windows open.
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

// Stop the Python process before quitting so it never outlives the app.
let quitting = false
app.on('before-quit', (event) => {
  if (quitting) return
  quitting = true
  event.preventDefault()
  void sidecar.stop().finally(() => app.quit())
})
