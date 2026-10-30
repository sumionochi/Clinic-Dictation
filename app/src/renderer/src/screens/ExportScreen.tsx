import { CircleCheck, Download, FileText, LoaderCircle, Lock } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useI18n } from '@/i18n/i18n'
import { useConsultation } from '@/lib/consultation'
import { useRecord } from '@/lib/useRecord'

type Format = 'pdf' | 'fhir'

export function ExportScreen(): React.JSX.Element {
  const { t, lang } = useI18n()
  const { sessionId, goTo } = useConsultation()
  const { record, loaded } = useRecord(sessionId)
  const [preview, setPreview] = useState<{ sessionId: string; html: string } | null>(null)
  const [saving, setSaving] = useState<Format | null>(null)
  const [saved, setSaved] = useState<Partial<Record<Format, string>>>({})
  const [error, setError] = useState<string | null>(null)
  const approved = record?.status === 'approved'
  const kept = record?.facts.filter((f) => f.state === 'kept').length ?? 0

  useEffect(() => {
    if (!sessionId || !approved) return
    let cancelled = false
    window.api.sidecar.call('GET', `/sessions/${sessionId}/record/document`).then((res) => {
      if (!cancelled && res.ok) setPreview({ sessionId, html: (res.body as { html: string }).html })
    })
    return () => {
      cancelled = true
    }
  }, [sessionId, approved])

  const save = (format: Format): void => {
    if (!sessionId) return
    setError(null)
    setSaving(format)
    const action = format === 'pdf' ? window.api.export.savePdf : window.api.export.saveFhir
    action(sessionId, lang)
      .then(
        (path) => path && setSaved((s) => ({ ...s, [format]: path })),
        (e: Error) =>
          setError(e.message.replace(/^Error invoking remote method '[^']+': (Error: )?/, ''))
      )
      .finally(() => setSaving(null))
  }

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 p-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">{t('export.title')}</h1>
        <p className="text-sm text-muted-foreground">{t('export.subtitle')}</p>
      </header>

      {!sessionId || !loaded ? null : !approved ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Lock className="size-4" />
              {t('export.notApproved')}
            </CardTitle>
            <CardDescription>{t('export.notApprovedText')}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" onClick={() => goTo('review')}>
              {t('goTo.review')}
            </Button>
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Card size="sm">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <FileText className="size-4" />
                  {t('export.pdfTitle')}
                </CardTitle>
                <CardDescription>{t('export.pdfText')}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col items-start gap-2">
                <Button disabled={saving !== null} onClick={() => save('pdf')}>
                  {saving === 'pdf' ? (
                    <LoaderCircle className="animate-spin" data-icon="inline-start" />
                  ) : (
                    <Download data-icon="inline-start" />
                  )}
                  {t('export.savePdf')}
                </Button>
                {saved.pdf && (
                  <p className="text-sm text-muted-foreground">
                    {t('export.savedTo', { path: saved.pdf })}
                  </p>
                )}
              </CardContent>
            </Card>
            <Card size="sm">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <CircleCheck className="size-4" />
                  {t('export.fhirTitle')}
                </CardTitle>
                <CardDescription>
                  {t('export.fhirText', {
                    facts: kept,
                    resources: record.validation?.resources ?? 0,
                    errors: record.validation?.errors ?? 0,
                    warnings: record.validation?.warnings ?? 0
                  })}
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col items-start gap-2">
                <Button variant="outline" disabled={saving !== null} onClick={() => save('fhir')}>
                  <Download data-icon="inline-start" />
                  {t('export.saveFhir')}
                </Button>
                {saved.fhir && (
                  <p className="text-sm text-muted-foreground">
                    {t('export.savedTo', { path: saved.fhir })}
                  </p>
                )}
              </CardContent>
            </Card>
          </div>
          {error && <p className="text-sm text-muted-foreground">{error}</p>}

          <Card size="sm">
            <CardHeader>
              <CardTitle>{t('export.preview')}</CardTitle>
              <CardDescription>{t('export.previewText')}</CardDescription>
            </CardHeader>
            <CardContent>
              {preview?.sessionId === sessionId ? (
                // Static content only: the sandbox blocks scripts, forms and navigation.
                <iframe
                  title={t('export.previewFrame')}
                  sandbox=""
                  srcDoc={preview.html}
                  className="h-[70vh] w-full rounded-md border bg-white"
                />
              ) : (
                <LoaderCircle className="size-4 animate-spin" />
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
