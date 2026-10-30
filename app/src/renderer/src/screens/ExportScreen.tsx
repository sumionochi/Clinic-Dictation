import { CircleCheck, Download, Lock } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useConsultation } from '@/lib/consultation'
import { useRecord } from '@/lib/useRecord'

export function ExportScreen(): React.JSX.Element {
  const { sessionId, goTo } = useConsultation()
  const { record, loaded } = useRecord(sessionId)
  const [saved, setSaved] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const approved = record?.status === 'approved'
  const kept = record?.facts.filter((f) => f.state === 'kept').length ?? 0

  const save = (): void => {
    if (!sessionId) return
    setError(null)
    window.api.export.saveFhir(sessionId).then(
      (path) => path && setSaved(path),
      (e: Error) => setError(e.message)
    )
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Export</h1>
        <p className="text-sm text-muted-foreground">
          Send the approved record on as a standard FHIR R4 document.
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
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CircleCheck className="size-4" />
              Ready to export
            </CardTitle>
            <CardDescription>
              {kept} facts in {record.validation?.resources} FHIR resources, each with a Provenance
              that quotes the transcript. Checked by the official HL7 validator:{' '}
              {record.validation?.errors} errors, {record.validation?.warnings} warnings.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col items-start gap-3">
            <Button onClick={save}>
              <Download data-icon="inline-start" />
              Save FHIR document…
            </Button>
            {saved && <p className="text-sm text-muted-foreground">Saved to {saved}</p>}
            {error && <p className="text-sm text-muted-foreground">{error}</p>}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
