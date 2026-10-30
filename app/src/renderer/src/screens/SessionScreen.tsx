import {
  CircleCheck,
  CircleDashed,
  CircleX,
  LoaderCircle,
  Lock,
  Mic,
  Pause,
  Play,
  Square
} from 'lucide-react'
import { useEffect, useState } from 'react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/select'
import { isMessageKey, useI18n, type MessageKey } from '@/i18n/i18n'
import { useConsultation } from '@/lib/consultation'
import {
  pauseRecording,
  resumeRecording,
  startRecording,
  stopRecording,
  useRecordingController
} from '@/lib/recordingController'
import {
  formatClock,
  recordingsApi,
  type Language,
  type LiveView,
  type Recording
} from '@/lib/recordings'
import { sessionsApi, type Session } from '@/lib/sessions'

const LANGUAGES: { value: string; title: MessageKey }[] = [
  { value: 'de', title: 'session.lang.de' },
  { value: 'en', title: 'session.lang.en' },
  { value: 'auto', title: 'session.lang.auto' }
]

/** Keeps the session and its recording in sync with the sidecar, polling while it works. */
interface SessionData {
  session: Session | null
  recording: Recording | null
  error: string | null
  refresh: () => void
}

function useSessionData(sessionId: string | null, controllerPhase: string): SessionData {
  const [session, setSession] = useState<Session | null>(null)
  const [recording, setRecording] = useState<Recording | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    Promise.all([sessionsApi.get(sessionId), recordingsApi.get(sessionId)]).then(
      ([s, r]) => {
        if (cancelled) return
        setSession(s)
        setRecording(r)
        setError(null)
      },
      (e: Error) => !cancelled && setError(e.message)
    )
    return () => {
      cancelled = true
    }
  }, [sessionId, controllerPhase, tick])

  // While the sidecar is transcribing, check back every second.
  const busy = recording?.status === 'transcribing' || recording?.status === 'recorded'
  useEffect(() => {
    if (!busy) return
    const timer = setInterval(() => setTick((t) => t + 1), 1000)
    return () => clearInterval(timer)
  }, [busy])

  const current = (x: { id?: string; session_id?: string } | null): boolean =>
    !!x && (x.id === sessionId || x.session_id === sessionId)
  return {
    session: current(session) ? session : null,
    recording: current(recording) ? recording : null,
    error,
    refresh: () => setTick((t) => t + 1)
  }
}

export function SessionScreen(): React.JSX.Element {
  const i18n = useI18n()
  const { t } = i18n
  const { sessionId, goTo } = useConsultation()
  const controller = useRecordingController()
  const { session, recording, error, refresh } = useSessionData(sessionId, controller.phase)
  const [language, setLanguage] = useState('de')
  const live = controller.phase !== 'idle' && controller.sessionId === sessionId

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 p-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">{t('session.title')}</h1>
        <p className="text-sm text-muted-foreground">{t('session.subtitle')}</p>
      </header>

      {(error || controller.error || controller.refused) && (
        <Alert>
          <CircleX />
          <AlertTitle>{t('common.somethingWrong')}</AlertTitle>
          <AlertDescription>
            {controller.refused
              ? t('recorder.stoppedBecause', {
                  detail: controller.error ?? t('recorder.refused')
                })
              : (controller.error ?? error)}
          </AlertDescription>
        </Alert>
      )}

      {!sessionId ? (
        <LockedCard
          title={t('session.noConsultation')}
          lines={[t('session.startFirst')]}
          onConsent={() => goTo('consent')}
        />
      ) : !session ? null : live ? (
        <>
          <RecorderCard />
          <LivePanel sessionId={sessionId} />
        </>
      ) : recording ? (
        <RecordingResult recording={recording} session={session} onChange={refresh} />
      ) : !session.recording_allowed ? (
        <LockedCard
          title={t('consent.lockedTitle')}
          lines={session.blocker_items.map((b) =>
            t(`blocker.${b.code}`, {
              person: i18n.person(
                session.participants.find((p) => p.id === b.participant_id)?.display_name
              )
            })
          )}
          onConsent={() => goTo('consent')}
        />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>{t('session.readyTitle')}</CardTitle>
            <CardDescription>{t('session.readyText')}</CardDescription>
          </CardHeader>
          <CardContent className="flex items-end gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="language">{t('session.spokenLanguage')}</Label>
              <Select value={language} onValueChange={setLanguage}>
                <SelectTrigger id="language" className="w-52">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LANGUAGES.map((l) => (
                    <SelectItem key={l.value} value={l.value}>
                      {t(l.title)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              disabled={controller.phase !== 'idle'}
              onClick={() =>
                void startRecording(sessionId, language === 'auto' ? null : (language as Language))
              }
            >
              <Mic data-icon="inline-start" />
              {t('session.start')}
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

function LockedCard({
  title,
  lines,
  onConsent
}: {
  title: string
  lines: string[]
  onConsent: () => void
}): React.JSX.Element {
  const { t } = useI18n()
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Lock className="size-4" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <ul className="list-disc pl-5 text-sm">
          {lines.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
        <div>
          <Button variant="outline" onClick={onConsent}>
            {t('goTo.consent')}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function RecorderCard(): React.JSX.Element {
  const { t } = useI18n()
  const c = useRecordingController()
  const paused = c.phase === 'paused'
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {c.phase === 'recording' ? (
            <span className="size-2.5 animate-pulse rounded-full bg-foreground" />
          ) : (
            <Pause className="size-4" />
          )}
          {c.phase === 'starting'
            ? t('recorder.starting')
            : c.phase === 'stopping'
              ? t('recorder.stopping')
              : paused
                ? t('recorder.paused')
                : t('recorder.recording')}
        </CardTitle>
        <CardDescription>
          {t('recorder.saved', { time: formatClock(c.savedSeconds) })}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div
          className="h-2 w-full overflow-hidden rounded-full bg-muted"
          aria-label={t('recorder.level')}
        >
          <div
            className="h-full bg-foreground transition-[width] duration-100"
            style={{ width: `${Math.min(100, Math.sqrt(c.level) * 160)}%` }}
          />
        </div>
        <div className="flex gap-2">
          {paused ? (
            <Button variant="outline" onClick={resumeRecording}>
              <Play data-icon="inline-start" />
              {t('recorder.resume')}
            </Button>
          ) : (
            <Button variant="outline" disabled={c.phase !== 'recording'} onClick={pauseRecording}>
              <Pause data-icon="inline-start" />
              {t('recorder.pause')}
            </Button>
          )}
          <Button
            disabled={c.phase === 'starting' || c.phase === 'stopping'}
            onClick={() => void stopRecording()}
          >
            <Square data-icon="inline-start" />
            {t('recorder.stop')}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

const LIVE_POLL_MS = 3000

function LivePanel({ sessionId }: { sessionId: string }): React.JSX.Element {
  const { t } = useI18n()
  const [live, setLive] = useState<LiveView | null>(null)
  useEffect(() => {
    let cancelled = false
    const load = (): void => {
      recordingsApi.live(sessionId).then(
        (v) => !cancelled && setLive(v),
        () => undefined
      )
    }
    load()
    const timer = setInterval(load, LIVE_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [sessionId])

  // Topic labels by id; the sidecar's English label only for a topic the app does not know.
  const checklistLabel = (item: { id: string; label: string }): string => {
    const key = `checklist.${item.id}`
    return isMessageKey(key) ? t(key) : item.label
  }
  const missing = live?.checklist.filter((c) => !c.covered).length ?? 0
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <Card size="sm">
        <CardHeader>
          <CardTitle>{t('live.checklist')}</CardTitle>
          <CardDescription>
            {!live || live.lines.length === 0
              ? t('live.waiting')
              : missing === 0
                ? t('live.allCovered')
                : missing === 1
                  ? t('live.missingOne')
                  : t('live.missingMany', { n: missing })}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="flex flex-col gap-2">
            {(live?.checklist ?? []).map((item) => (
              <li key={item.id} className="flex items-start gap-2 text-sm">
                {item.covered ? (
                  <CircleCheck className="mt-0.5 size-4 shrink-0" />
                ) : (
                  <CircleDashed className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                )}
                <div className="flex flex-col">
                  <span className={item.covered ? '' : 'text-muted-foreground'}>
                    {checklistLabel(item)}
                  </span>
                  {item.covered && (
                    <span className="text-xs text-muted-foreground">“{item.match}”</span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
      <Card size="sm">
        <CardHeader>
          <CardTitle>{t('live.previewTitle')}</CardTitle>
          <CardDescription>
            {t('live.previewText')}
            {live && ` ${t('live.upTo', { time: formatClock(live.processed_s) })}`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ol className="flex flex-col gap-2">
            {(live?.lines ?? []).slice(-6).map((ln) => (
              <li key={ln.idx} className="flex gap-3 text-sm">
                <span className="w-10 shrink-0 text-muted-foreground tabular-nums">
                  {formatClock(ln.start)}
                </span>
                <span>{ln.text}</span>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>
    </div>
  )
}

function RecordingResult({
  recording,
  session,
  onChange
}: {
  recording: Recording
  session: Session
  onChange: () => void
}): React.JSX.Element {
  const { t, person } = useI18n()
  const sessionId = session.id
  const [actionError, setActionError] = useState<string | null>(null)
  const act = (fn: () => Promise<unknown>): void => {
    setActionError(null)
    fn().then(onChange, (e: Error) => setActionError(e.message))
  }
  const transcribe = (): void => act(() => recordingsApi.transcribe(sessionId, recording.id))

  if (recording.status === 'recording') {
    // The sidecar still thinks it is recording, but this window is not (e.g. it was reloaded).
    return (
      <StatusCard title={t('result.cutOff')} error={actionError}>
        <p className="text-sm">
          {t('result.cutOffText', { time: formatClock(recording.duration_s) })}
        </p>
        <Button onClick={() => act(() => recordingsApi.stop(sessionId, recording.id))}>
          {t('recorder.stop')}
        </Button>
      </StatusCard>
    )
  }
  if (recording.status === 'recorded' || recording.status === 'transcribing') {
    return (
      <StatusCard
        title={t('result.transcribing', { time: formatClock(recording.duration_s) })}
        icon={<LoaderCircle className="size-4 animate-spin" />}
      >
        <p className="text-sm text-muted-foreground">{t('result.transcribingText')}</p>
      </StatusCard>
    )
  }
  if (recording.status === 'failed' || recording.status === 'interrupted') {
    return (
      <StatusCard
        title={recording.status === 'failed' ? t('result.failed') : t('result.interrupted')}
        error={actionError}
      >
        <p className="text-sm">
          {t('result.savedNothingLost', { time: formatClock(recording.duration_s) })}
          {recording.error && <span className="text-muted-foreground"> {recording.error}</span>}
        </p>
        <Button onClick={transcribe}>{t('result.transcribeSaved')}</Button>
      </StatusCard>
    )
  }

  const names = new Map(recording.speakers.map((v) => [v.speaker, v.name]))
  // Mark lines in a language other than the one most of the consultation was in.
  const counts = new Map<string, number>()
  for (const s of recording.segments) {
    if (s.language) counts.set(s.language, (counts.get(s.language) ?? 0) + 1)
  }
  const mainLanguage = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0]
  return (
    <>
      {recording.speakers.length > 0 && (
        <SpeakersCard recording={recording} session={session} onChange={onChange} />
      )}
      <Card>
        <CardHeader>
          <CardTitle>{t('transcript.title')}</CardTitle>
          <CardDescription>
            {t('transcript.meta', {
              duration: formatClock(recording.duration_s),
              seconds: Math.round(recording.transcribe_seconds ?? 0),
              model: recording.model?.split('/').pop() ?? ''
            })}
            {recording.diarization_model &&
              t('transcript.metaSpeakers', {
                seconds: Math.round(recording.diarize_seconds ?? 0)
              })}
            {recording.audio_deleted_at ? t('transcript.audioDeleted') : '.'}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {recording.diarization_error && (
            <p className="text-sm text-muted-foreground">
              {t('transcript.diarizationError', { error: recording.diarization_error })}
            </p>
          )}
          {recording.segments.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('transcript.noSpeech')}</p>
          ) : (
            <ol className="flex flex-col gap-3">
              {recording.segments.map((s, i) => {
                // Name the speaker only where it changes, like a script.
                const newSpeaker = s.speaker !== recording.segments[i - 1]?.speaker
                return (
                  <li key={s.idx} className="flex gap-4 text-sm">
                    <span className="w-12 shrink-0 text-muted-foreground tabular-nums">
                      {formatClock(s.start)}
                    </span>
                    <span className="w-28 shrink-0 truncate font-medium">
                      {!newSpeaker || recording.speakers.length === 0 ? (
                        ''
                      ) : s.speaker ? (
                        person(names.get(s.speaker))
                      ) : (
                        <span className="font-normal text-muted-foreground">
                          {t('transcript.unknown')}
                        </span>
                      )}
                    </span>
                    <span>
                      {s.language && s.language !== mainLanguage && (
                        <Badge variant="outline" className="mr-2 uppercase">
                          {s.language}
                        </Badge>
                      )}
                      {s.text}
                    </span>
                  </li>
                )
              })}
            </ol>
          )}
        </CardContent>
      </Card>
    </>
  )
}

const UNMATCHED = 'none'

function SpeakersCard({
  recording,
  session,
  onChange
}: {
  recording: Recording
  session: Session
  onChange: () => void
}): React.JSX.Element {
  const { t, person } = useI18n()
  const [error, setError] = useState<string | null>(null)
  const unmatched = recording.speakers.filter((v) => !v.participant_id).length
  const match = (speaker: string, value: string): void => {
    setError(null)
    recordingsApi
      .matchSpeaker(session.id, recording.id, speaker, value === UNMATCHED ? null : value)
      .then(onChange, (e: Error) => setError(e.message))
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('speakers.title')}</CardTitle>
        <CardDescription>
          {unmatched > 0
            ? t('speakers.unmatched', { n: recording.speakers.length })
            : t('speakers.allMatched')}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {recording.speakers.map((v) => (
          <div key={v.speaker} className="flex items-center gap-4">
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="text-sm font-medium">
                {t('speakers.voice', { n: v.speaker.slice(1) })}{' '}
                <span className="font-normal text-muted-foreground">
                  · {t('speakers.spoken', { time: formatClock(v.seconds) })}
                </span>
              </span>
              <span className="truncate text-xs text-muted-foreground">
                {t('speakers.firstSaid', { words: v.first_words })}
              </span>
            </div>
            <Select
              value={v.participant_id ?? UNMATCHED}
              onValueChange={(value) => match(v.speaker, value)}
            >
              <SelectTrigger
                className="w-44"
                aria-label={t('speakers.personFor', { n: v.speaker.slice(1) })}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={UNMATCHED}>{t('speakers.notMatched')}</SelectItem>
                {session.participants.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {person(p.display_name)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ))}
        {error && <p className="text-sm text-muted-foreground">{error}</p>}
      </CardContent>
    </Card>
  )
}

function StatusCard({
  title,
  icon,
  error,
  children
}: {
  title: string
  icon?: React.ReactNode
  error?: string | null
  children: React.ReactNode
}): React.JSX.Element {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {icon}
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col items-start gap-3">
        {children}
        {error && <p className="text-sm text-muted-foreground">{error}</p>}
      </CardContent>
    </Card>
  )
}
