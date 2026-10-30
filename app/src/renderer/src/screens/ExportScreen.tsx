import { CircleCheck, Download, FileText, LoaderCircle, Lock } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useConsultation } from '@/lib/consultation'
import { useRecord } from '@/lib/useRecord'

type Format = 'pdf' | 'fhir'

export function ExportScreen(): React.JSX.Element {
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
    action(sessionId)
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
        <h1 className="text-2xl font-semibold tracking-tight">Export</h1>
        <p className="text-sm text-muted-foreground">
          The approved record in two forms: a document for people and a FHIR file for systems.
        </p>
      </header>

      {!sessionId || !loaded ? null : !approved ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Lock className="size-4" />
              Not approved yet
            </CardTitle>
            <CardDescription>
              Only a record you have reviewed and approved can be exported.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" onClick={() => goTo('review')}>
              Go to Review
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
                  Readable document (PDF/A)
                </CardTitle>
                <CardDescription>
                  For reading, forwarding and the patient file. German, with the consents, every
                  fact and its quote, open questions and a log of your changes (§ 630f BGB).
                  Archival PDF/A-2b, checked by veraPDF. To send it as an eArztbrief, sign it in
                  your practice software with your eHBA.
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col items-start gap-2">
                <Button disabled={saving !== null} onClick={() => save('pdf')}>
                  {saving === 'pdf' ? (
                    <LoaderCircle className="animate-spin" data-icon="inline-start" />
                  ) : (
                    <Download data-icon="inline-start" />
                  )}
                  Save PDF/A…
                </Button>
                {saved.pdf && <p className="text-sm text-muted-foreground">Saved to {saved.pdf}</p>}
              </CardContent>
            </Card>
            <Card size="sm">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <CircleCheck className="size-4" />
                  FHIR R4 document (JSON)
                </CardTitle>
                <CardDescription>
                  For other systems: {kept} facts in {record.validation?.resources} resources, each
                  with a Provenance that quotes the transcript. HL7 validator:{' '}
                  {record.validation?.errors} errors, {record.validation?.warnings} warnings.
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col items-start gap-2">
                <Button variant="outline" disabled={saving !== null} onClick={() => save('fhir')}>
                  <Download data-icon="inline-start" />
                  Save FHIR…
                </Button>
                {saved.fhir && (
                  <p className="text-sm text-muted-foreground">Saved to {saved.fhir}</p>
                )}
              </CardContent>
            </Card>
          </div>
          {error && <p className="text-sm text-muted-foreground">{error}</p>}

          <Card size="sm">
            <CardHeader>
              <CardTitle>Preview</CardTitle>
              <CardDescription>The readable document as it will be saved.</CardDescription>
            </CardHeader>
            <CardContent>
              {preview?.sessionId === sessionId ? (
                // Static content only: the sandbox blocks scripts, forms and navigation.
                <iframe
                  title="Document preview"
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
