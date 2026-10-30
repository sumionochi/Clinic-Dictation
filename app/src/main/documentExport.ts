// The approved record as a readable PDF/A document.
//
// The sidecar renders the record as German HTML laid out for A4; Chromium prints it
// here in a hidden, sandboxed window without JavaScript; the sidecar converts the
// result to PDF/A-2b and only returns it if veraPDF accepts it (KBV eArztbrief
// guideline: the letter's content travels as PDF/A).

import { BrowserWindow, dialog } from 'electron'
import { writeFile } from 'fs/promises'
import { join } from 'path'

import { SAVE_DIALOG } from './dialogText'
import type { SidecarManager } from './sidecarManager'
import type { UiLanguage } from '../shared/sidecar'

interface RenderedDocument {
  html: string
  document_id: string
  filename: string
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)
}

async function printToPdf(doc: RenderedDocument): Promise<Buffer> {
  const window = new BrowserWindow({
    show: false,
    webPreferences: { javascript: false, sandbox: true, contextIsolation: true }
  })
  try {
    await window.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(doc.html)}`)
    return await window.webContents.printToPDF({
      preferCSSPageSize: true,
      printBackground: true,
      displayHeaderFooter: true,
      headerTemplate: '<span></span>',
      footerTemplate:
        '<div style="font-family: Arial, sans-serif; font-size: 7pt; color: #555; width: 100%; ' +
        'padding: 0 16mm; display: flex; justify-content: space-between;">' +
        `<span>Aufnahmegespräch · Dokument-ID ${escapeHtml(doc.document_id)}</span>` +
        '<span>Seite <span class="pageNumber"></span> von <span class="totalPages"></span></span></div>'
    })
  } finally {
    window.destroy()
  }
}

/** Builds the PDF/A and asks where to save it. Null if the doctor cancels. */
export async function savePdfDocument(
  sidecar: SidecarManager,
  sessionId: string,
  parent: BrowserWindow | null,
  lang: UiLanguage,
  testExportDir?: string
): Promise<string | null> {
  const res = await sidecar.callJson('GET', `/sessions/${sessionId}/record/document`, undefined)
  if (!res.ok) throw new Error((res.body as { detail?: string } | null)?.detail ?? 'Export failed')
  const doc = res.body as RenderedDocument
  const printed = await printToPdf(doc)
  const pdfa = await sidecar.exchangeBinary(
    `/sessions/${sessionId}/record/document/pdf`,
    printed,
    'application/pdf'
  )
  if (!pdfa.ok) throw new Error(pdfa.detail)

  let target: string | undefined
  if (testExportDir) {
    target = join(testExportDir, `${doc.filename}.pdf`)
  } else {
    const options = {
      title: SAVE_DIALOG[lang].pdfTitle,
      defaultPath: `${doc.filename}.pdf`,
      filters: [{ name: SAVE_DIALOG[lang].pdfFilter, extensions: ['pdf'] }]
    }
    const choice = parent
      ? await dialog.showSaveDialog(parent, options)
      : await dialog.showSaveDialog(options)
    if (choice.canceled || !choice.filePath) return null
    target = choice.filePath
  }
  await writeFile(target, pdfa.bytes)
  return target
}
