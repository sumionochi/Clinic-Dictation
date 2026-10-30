import type { UiLanguage } from '../shared/sidecar'

// Text for the native save dialogs, which the main process shows in the
// language the renderer is set to. The renderer's own text is in renderer/src/i18n.
export const SAVE_DIALOG: Record<
  UiLanguage,
  { fhirTitle: string; fhirFilter: string; pdfTitle: string; pdfFilter: string }
> = {
  en: {
    fhirTitle: 'Save FHIR document (for other systems)',
    fhirFilter: 'FHIR JSON',
    pdfTitle: 'Save readable document (PDF/A)',
    pdfFilter: 'PDF/A document'
  },
  de: {
    fhirTitle: 'FHIR-Dokument speichern (für andere Systeme)',
    fhirFilter: 'FHIR-JSON',
    pdfTitle: 'Lesbares Dokument speichern (PDF/A)',
    pdfFilter: 'PDF/A-Dokument'
  }
}

/** The renderer's language, or English if it sent anything else. */
export function dialogLanguage(value: unknown): UiLanguage {
  return value === 'de' ? 'de' : 'en'
}
