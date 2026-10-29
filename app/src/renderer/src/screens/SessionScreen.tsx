import { CircleX, LoaderCircle, Lock, Mic, Pause, Play, Square } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
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
import { useConsultation } from '@/lib/consultation'
import {
  pauseRecording,
  resumeRecording,
  startRecording,
  stopRecording,
  useRecordingController
} from '@/lib/recordingController'
import { formatClock, recordingsApi, type Language, type Recording } from '@/lib/recordings'
import { sessionsApi, type Session } from '@/lib/sessions'

const LANGUAGES: { value: string; title: string }[] = [
  { value: 'de', title: 'German' },
  { value: 'en', title: 'English' },
  { value: 'auto', title: 'Detect automatically' }
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
  const { sessionId, goTo } = useConsultation()
  const controller = useRecordingController()
  const { session, recording, error, refresh } = useSessionData(sessionId, controller.phase)
  const [language, setLanguage] = useState('de')
  const live = controller.phase !== 'idle' && controller.sessionId === sessionId

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Session</h1>
        <p className="text-sm text-muted-foreground">
          Record the consultation locally. Audio never leaves this machine.
        </p>
      </header>

      {(error || controller.error) && (
        <Alert>
          <CircleX />
          <AlertTitle>Something went wrong</AlertTitle>
          <AlertDescription>{controller.error ?? error}</AlertDescription>
        </Alert>
      )}

      {!sessionId ? (
        <LockedCard
          title="No consultation open"
          lines={['Start a consultation and collect consent first.']}
          onConsent={() => goTo('consent')}
        />
      ) : !session ? null : live ? (
        <RecorderCard />
      ) : recording ? (
        <RecordingResult recording={recording} sessionId={sessionId} onChange={refresh} />
      ) : !session.recording_allowed ? (
        <LockedCard
          title="Recording is locked"
          lines={session.blockers}
          onConsent={() => goTo('consent')}
        />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Ready to record</CardTitle>
            <CardDescription>
              Everyone present has agreed. Pause at any time; paused audio is never recorded.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex items-end gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="language">Spoken language</Label>
              <Select value={language} onValueChange={setLanguage}>
                <SelectTrigger id="language" className="w-52">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LANGUAGES.map((l) => (
                    <SelectItem key={l.value} value={l.value}>
                      {l.title}
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
              Start recording
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
            Go to Consent
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function RecorderCard(): React.JSX.Element {
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
            ? 'Starting…'
            : c.phase === 'stopping'
              ? 'Saving the last seconds…'
              : paused
                ? 'Paused: nothing is being recorded'
                : 'Recording'}
        </CardTitle>
        <CardDescription>Saved so far: {formatClock(c.savedSeconds)}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="h-2 w-full overflow-hidden rounded-full bg-muted" aria-label="Input level">
          <div
            className="h-full bg-foreground transition-[width] duration-100"
            style={{ width: `${Math.min(100, Math.sqrt(c.level) * 160)}%` }}
          />
        </div>
        <div className="flex gap-2">
          {paused ? (
            <Button variant="outline" onClick={resumeRecording}>
              <Play data-icon="inline-start" />
              Resume
            </Button>
          ) : (
            <Button variant="outline" disabled={c.phase !== 'recording'} onClick={pauseRecording}>
              <Pause data-icon="inline-start" />
              Pause
            </Button>
          )}
          <Button
            disabled={c.phase === 'starting' || c.phase === 'stopping'}
            onClick={() => void stopRecording()}
          >
            <Square data-icon="inline-start" />
            Stop and transcribe
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function RecordingResult({
  recording,
  sessionId,
  onChange
}: {
  recording: Recording
  sessionId: string
  onChange: () => void
}): React.JSX.Element {
  const [actionError, setActionError] = useState<string | null>(null)
  const act = (fn: () => Promise<unknown>): void => {
    setActionError(null)
    fn().then(onChange, (e: Error) => setActionError(e.message))
  }
  const transcribe = (): void => act(() => recordingsApi.transcribe(sessionId, recording.id))

  if (recording.status === 'recording') {
    // The sidecar still thinks it is recording, but this window is not (e.g. it was reloaded).
    return (
      <StatusCard title="Recording was cut off" error={actionError}>
        <p className="text-sm">
          {formatClock(recording.duration_s)} of audio is saved. Stop the recording to transcribe
          it.
        </p>
        <Button onClick={() => act(() => recordingsApi.stop(sessionId, recording.id))}>
          Stop and transcribe
        </Button>
      </StatusCard>
    )
  }
  if (recording.status === 'recorded' || recording.status === 'transcribing') {
    return (
      <StatusCard
        title={`Transcribing ${formatClock(recording.duration_s)} of audio…`}
        icon={<LoaderCircle className="size-4 animate-spin" />}
      >
        <p className="text-sm text-muted-foreground">
          This runs on this Mac. The first time, the speech model (about 3 GB) is downloaded once.
        </p>
      </StatusCard>
    )
  }
  if (recording.status === 'failed' || recording.status === 'interrupted') {
    return (
      <StatusCard
        title={
          recording.status === 'failed' ? 'Transcription failed' : 'The recording was interrupted'
        }
        error={actionError}
      >
        <p className="text-sm">
          {formatClock(recording.duration_s)} of audio is saved and nothing is lost.
          {recording.error && <span className="text-muted-foreground"> {recording.error}</span>}
        </p>
        <Button onClick={transcribe}>Transcribe saved audio</Button>
      </StatusCard>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Transcript</CardTitle>
        <CardDescription>
          {formatClock(recording.duration_s)} of audio, transcribed in{' '}
          {Math.round(recording.transcribe_seconds ?? 0)} s with {recording.model?.split('/').pop()}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {recording.segments.length === 0 ? (
          <p className="text-sm text-muted-foreground">No speech was recognised.</p>
        ) : (
          <ol className="flex flex-col gap-3">
            {recording.segments.map((s) => (
              <li key={s.idx} className="flex gap-4 text-sm">
                <span className="w-12 shrink-0 text-muted-foreground tabular-nums">
                  {formatClock(s.start)}
                </span>
                <span>{s.text}</span>
              </li>
            ))}
          </ol>
        )}
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
