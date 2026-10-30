import {
  CircleAlert,
  CircleCheck,
  CircleX,
  FileCheck2,
  LoaderCircle,
  Lock,
  Pencil,
  RotateCcw,
  Sparkles,
  Trash2
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/select'
import { useI18n, type MessageKey } from '@/i18n/i18n'
import { useConsultation } from '@/lib/consultation'
import { recordingsApi, type Recording } from '@/lib/recordings'
import {
  ASSERTION_LABEL,
  SECTIONS,
  recordsApi,
  type Assertion,
  type ClinicalRecord,
  type Evidence,
  type Line,
  type ReviewFact
} from '@/lib/records'
import { useRecord } from '@/lib/useRecord'

/** What the transcript highlights: the lines and exact words behind a fact. */
interface Highlight {
  lines: number[]
  quotes: Evidence[]
}

export function ReviewScreen(): React.JSX.Element {
  const { t } = useI18n()
  const { sessionId, goTo } = useConsultation()
  const { record, loaded, error, setRecord, refresh } = useRecord(sessionId)
  const [recording, setRecording] = useState<Recording | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    recordingsApi.get(sessionId).then(
      (r) => !cancelled && setRecording(r),
      () => undefined
    )
    return () => {
      cancelled = true
    }
  }, [sessionId])

  const act = async (fn: () => Promise<ClinicalRecord>): Promise<void> => {
    setBusy(true)
    setActionError(null)
    try {
      setRecord(await fn())
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const transcribed = recording?.session_id === sessionId && recording?.status === 'transcribed'

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">{t('review.title')}</h1>
        <p className="text-sm text-muted-foreground">{t('review.subtitle')}</p>
      </header>

      {(error || actionError) && (
        <Alert>
          <CircleX />
          <AlertTitle>{t('common.somethingWrong')}</AlertTitle>
          <AlertDescription>{actionError ?? error}</AlertDescription>
        </Alert>
      )}

      {!sessionId ? (
        <Locked text={t('review.startFirst')} onClick={() => goTo('consent')} to="goTo.consent" />
      ) : !loaded ? null : !record ? (
        transcribed ? (
          <Card>
            <CardHeader>
              <CardTitle>{t('review.draftTitle')}</CardTitle>
              <CardDescription>{t('review.draftText')}</CardDescription>
            </CardHeader>
            <CardContent>
              <Button disabled={busy} onClick={() => void act(() => recordsApi.start(sessionId))}>
                <Sparkles data-icon="inline-start" />
                {t('review.draftButton')}
              </Button>
            </CardContent>
          </Card>
        ) : (
          <Locked
            text={t('review.needsTranscript')}
            onClick={() => goTo('session')}
            to="goTo.session"
          />
        )
      ) : record.status === 'extracting' ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <LoaderCircle className="size-4 animate-spin" />
              {t('review.drafting')}
            </CardTitle>
            <CardDescription>{t('review.draftingText')}</CardDescription>
          </CardHeader>
        </Card>
      ) : record.status === 'failed' ? (
        <Card>
          <CardHeader>
            <CardTitle>{t('review.failed')}</CardTitle>
            <CardDescription>{record.error}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col items-start gap-2">
            {record.error?.includes('Ollama') && (
              <p className="text-sm text-muted-foreground">
                {t('review.ollamaBefore')} <code>ollama serve</code>
                {t('review.ollamaAfter')}
              </p>
            )}
            <Button disabled={busy} onClick={() => void act(() => recordsApi.start(sessionId))}>
              {t('common.tryAgain')}
            </Button>
          </CardContent>
        </Card>
      ) : (
        <RecordReview
          record={record}
          busy={busy}
          onChange={(factId, change) =>
            void act(() => recordsApi.change(sessionId, factId, change))
          }
          onApprove={() => void act(() => recordsApi.approve(sessionId)).then(refresh)}
          onExport={() => goTo('export')}
        />
      )}
    </div>
  )
}

function Locked({
  text,
  to,
  onClick
}: {
  text: string
  to: MessageKey
  onClick: () => void
}): React.JSX.Element {
  const { t } = useI18n()
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Lock className="size-4" />
          {t('review.nothingYet')}
        </CardTitle>
        <CardDescription>{text}</CardDescription>
      </CardHeader>
      <CardContent>
        <Button variant="outline" onClick={onClick}>
          {t(to)}
        </Button>
      </CardContent>
    </Card>
  )
}

type Change = {
  action: 'remove' | 'restore' | 'edit'
  name?: string
  details?: string
  assertion?: Assertion
}

function RecordReview({
  record,
  busy,
  onChange,
  onApprove,
  onExport
}: {
  record: ClinicalRecord
  busy: boolean
  onChange: (factId: string, change: Change) => void
  onApprove: () => void
  onExport: () => void
}): React.JSX.Element {
  const { t, dateTime } = useI18n()
  const [highlight, setHighlight] = useState<Highlight | null>(null)
  const [confirming, setConfirming] = useState(false)
  const approved = record.status === 'approved'
  const kept = record.facts.filter((f) => f.state === 'kept')
  const removed = record.facts.filter((f) => f.state === 'removed')
  const show = (evidence: Evidence[]): void =>
    setHighlight({ lines: evidence.map((e) => e.line), quotes: evidence })

  return (
    <>
      {approved && (
        <Alert>
          <FileCheck2 />
          <AlertTitle>
            {t('review.approvedTitle', { time: dateTime(record.approved_at!) })}
          </AlertTitle>
          <AlertDescription>
            <p>
              {t('review.approvedText', {
                errors: record.validation?.errors ?? 0,
                resources: record.validation?.resources ?? 0
              })}
            </p>
            <Button className="mt-2" size="sm" onClick={onExport}>
              {t('goTo.export')}
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {record.contradictions.length > 0 && (
        <Alert>
          <CircleAlert />
          <AlertTitle>{t('review.contradictionTitle')}</AlertTitle>
          <AlertDescription>
            <ul className="flex flex-col gap-1">
              {record.contradictions.map((c) => (
                <li key={`${c.category}-${c.name}`} className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-foreground">{c.name}</span>
                  <span>
                    {c.assertions.map((a) => t(ASSERTION_LABEL[a])).join(t('review.and'))}
                  </span>
                  {c.lines.map((n) => (
                    <LineChip
                      key={n}
                      n={n}
                      onClick={() => setHighlight({ lines: [n], quotes: [] })}
                    />
                  ))}
                </li>
              ))}
            </ul>
            <p className="mt-1">{t('review.contradictionText')}</p>
          </AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="flex flex-col gap-4">
          {SECTIONS.map((section) => {
            const facts = kept.filter((f) => section.categories.includes(f.fact.category))
            if (facts.length === 0) return null
            return (
              <Card key={section.title} size="sm">
                <CardHeader>
                  <CardTitle>{t(section.title)}</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-col divide-y">
                  {facts.map((f) => (
                    <FactRow
                      key={f.id}
                      item={f}
                      readOnly={approved || busy}
                      onShow={() => show(f.fact.evidence)}
                      onChange={(change) => onChange(f.id, change)}
                    />
                  ))}
                </CardContent>
              </Card>
            )
          })}
          {kept.length === 0 && (
            <p className="text-sm text-muted-foreground">{t('review.noneKept')}</p>
          )}

          {removed.length > 0 && (
            <Card size="sm">
              <CardHeader>
                <CardTitle>{t('review.removedTitle', { n: removed.length })}</CardTitle>
                <CardDescription>{t('review.removedText')}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col divide-y">
                {removed.map((f) => (
                  <FactRow
                    key={f.id}
                    item={f}
                    readOnly={approved || busy}
                    onShow={() => show(f.fact.evidence)}
                    onChange={(change) => onChange(f.id, change)}
                  />
                ))}
              </CardContent>
            </Card>
          )}
        </div>

        <TranscriptPanel lines={record.lines} highlight={highlight} />
      </div>

      {!approved && (
        <div className="sticky bottom-0 -mx-8 -mb-8 flex items-center gap-4 border-t bg-background px-8 py-4">
          <div className="flex min-w-0 flex-1 flex-col">
            <span className="text-sm font-medium">
              {t('review.counts', { kept: kept.length, removed: removed.length })}
            </span>
            <span className="text-xs text-muted-foreground">
              {t('review.draftedBy', {
                model: record.model ?? '',
                seconds: Math.round(record.extract_seconds ?? 0)
              })}
            </span>
          </div>
          {confirming ? (
            <>
              <Button variant="outline" onClick={() => setConfirming(false)}>
                {t('common.cancel')}
              </Button>
              <Button
                disabled={busy}
                onClick={() => {
                  setConfirming(false)
                  onApprove()
                }}
              >
                {busy ? <LoaderCircle className="animate-spin" data-icon="inline-start" /> : null}
                {t('review.approveConfirm')}
              </Button>
            </>
          ) : (
            <Button disabled={busy || kept.length === 0} onClick={() => setConfirming(true)}>
              <CircleCheck data-icon="inline-start" />
              {t('review.approve')}
            </Button>
          )}
        </div>
      )}
    </>
  )
}

function LineChip({ n, onClick }: { n: number; onClick: () => void }): React.JSX.Element {
  const { t } = useI18n()
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-md border px-1.5 py-0.5 text-xs text-muted-foreground tabular-nums hover:bg-muted hover:text-foreground"
    >
      {t('review.line', { n })}
    </button>
  )
}

function FactRow({
  item,
  readOnly,
  onShow,
  onChange
}: {
  item: ReviewFact
  readOnly: boolean
  onShow: () => void
  onChange: (change: Change) => void
}): React.JSX.Element {
  const { t } = useI18n()
  const { fact } = item
  const [editing, setEditing] = useState(false)
  const [assertion, setAssertion] = useState<Assertion>(fact.assertion)
  const [details, setDetails] = useState(fact.details ?? '')
  const [name, setName] = useState(fact.name)
  const removed = item.state === 'removed'

  if (editing) {
    return (
      <form
        className="flex flex-col gap-2 py-3"
        onSubmit={(e) => {
          e.preventDefault()
          setEditing(false)
          onChange({ action: 'edit', name, details, assertion })
        }}
      >
        <div className="flex gap-2">
          <Input
            value={name}
            maxLength={60}
            onChange={(e) => setName(e.target.value)}
            aria-label={t('review.name')}
          />
          <Select value={assertion} onValueChange={(v) => setAssertion(v as Assertion)}>
            <SelectTrigger className="w-40" aria-label={t('review.said')}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(ASSERTION_LABEL) as Assertion[]).map((a) => (
                <SelectItem key={a} value={a}>
                  {t(ASSERTION_LABEL[a])}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Input
          value={details}
          maxLength={120}
          placeholder={t('review.detailsPlaceholder')}
          onChange={(e) => setDetails(e.target.value)}
          aria-label={t('review.details')}
        />
        <div className="flex gap-2">
          <Button size="sm" type="submit">
            {t('common.save')}
          </Button>
          <Button size="sm" variant="ghost" type="button" onClick={() => setEditing(false)}>
            {t('common.cancel')}
          </Button>
        </div>
      </form>
    )
  }

  return (
    <div className="flex items-start gap-3 py-3">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={removed ? 'font-medium text-muted-foreground line-through' : 'font-medium'}
          >
            {fact.name}
          </span>
          <Badge variant={fact.assertion === 'present' ? 'default' : 'outline'}>
            {t(ASSERTION_LABEL[fact.assertion])}
          </Badge>
          {item.origin === 'doctor_edited' && (
            <Badge variant="secondary">{t('review.edited')}</Badge>
          )}
        </div>
        {fact.details && <span className="text-sm text-muted-foreground">{fact.details}</span>}
        {item.reason && removed && (
          <span className="text-xs text-muted-foreground">{item.reason}</span>
        )}
        <div className="flex flex-wrap gap-1">
          {fact.evidence.map((e, i) => (
            <LineChip key={`${e.line}-${i}`} n={e.line} onClick={onShow} />
          ))}
        </div>
      </div>
      {!readOnly && (
        <div className="flex shrink-0 gap-1">
          {removed ? (
            <Button size="sm" variant="outline" onClick={() => onChange({ action: 'restore' })}>
              <RotateCcw data-icon="inline-start" />
              {t('review.restore')}
            </Button>
          ) : (
            <>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={t('review.edit')}
                onClick={() => setEditing(true)}
              >
                <Pencil />
              </Button>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={t('review.remove')}
                onClick={() => onChange({ action: 'remove' })}
              >
                <Trash2 />
              </Button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function TranscriptPanel({
  lines,
  highlight
}: {
  lines: Line[]
  highlight: Highlight | null
}): React.JSX.Element {
  const { t, person } = useI18n()
  const refs = useRef(new Map<number, HTMLLIElement>())

  useEffect(() => {
    if (!highlight?.lines.length) return
    refs.current
      .get(Math.min(...highlight.lines))
      ?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [highlight])

  return (
    <Card size="sm" className="lg:sticky lg:top-4 lg:max-h-[calc(100vh-8rem)]">
      <CardHeader>
        <CardTitle>{t('transcript.title')}</CardTitle>
        <CardDescription>{t('review.transcriptText')}</CardDescription>
      </CardHeader>
      <CardContent className="overflow-y-auto">
        <ol className="flex flex-col gap-1">
          {lines.map((ln) => {
            const active = highlight?.lines.includes(ln.line) ?? false
            const quotes =
              highlight?.quotes.filter((q) => q.line === ln.line).map((q) => q.quote) ?? []
            return (
              <li
                key={ln.line}
                ref={(el) => {
                  if (el) refs.current.set(ln.line, el)
                  else refs.current.delete(ln.line)
                }}
                className={`flex gap-3 rounded-md px-2 py-1 text-sm ${active ? 'bg-muted' : ''}`}
              >
                <span className="w-6 shrink-0 text-right text-muted-foreground tabular-nums">
                  {ln.line}
                </span>
                <span className="w-20 shrink-0 truncate font-medium">{person(ln.speaker)}</span>
                <span>{active ? <Marked text={ln.text} quotes={quotes} /> : ln.text}</span>
              </li>
            )
          })}
        </ol>
      </CardContent>
    </Card>
  )
}

/** The line with each quoted passage marked; matching ignores case like the evidence check. */
function Marked({ text, quotes }: { text: string; quotes: string[] }): React.JSX.Element {
  const lower = text.toLowerCase()
  const spans: [number, number][] = []
  for (const q of quotes) {
    const at = lower.indexOf(q.toLowerCase())
    if (at >= 0) spans.push([at, at + q.length])
  }
  spans.sort((a, b) => a[0] - b[0])
  const parts: React.ReactNode[] = []
  let pos = 0
  spans.forEach(([start, end], i) => {
    if (start < pos) return
    parts.push(text.slice(pos, start))
    parts.push(
      <mark key={i} className="rounded-sm bg-foreground px-0.5 text-background">
        {text.slice(start, end)}
      </mark>
    )
    pos = end
  })
  parts.push(text.slice(pos))
  return <>{parts}</>
}
