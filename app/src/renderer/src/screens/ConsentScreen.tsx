import { CircleCheck, CircleDashed, CircleX, Lock, Plus, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
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
  ROLE_OPTIONS,
  sessionsApi,
  type Decision,
  type Participant,
  type Role,
  type Session
} from '@/lib/sessions'

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

const DECISION_TEXT: Record<Participant['decision'], string> = {
  pending: 'Not answered',
  granted: 'Agreed',
  declined: 'Declined',
  unable: 'Cannot consent'
}

export function ConsentScreen(): React.JSX.Element {
  const { sessionId, setSessionId, goTo } = useConsultation()
  const [loaded, setSession] = useState<Session | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Only show what was loaded for the consultation that is currently open.
  const session = sessionId && loaded?.id === sessionId ? loaded : null

  // Every change goes through the sidecar, which saves it before answering, and
  // the screen always shows the saved state it gets back.
  const run = useCallback(async (action: () => Promise<Session>): Promise<void> => {
    setBusy(true)
    setError(null)
    try {
      setSession(await action())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [])

  // Coming back to this screen: reload the open consultation from the sidecar.
  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    sessionsApi.get(sessionId).then(
      (s) => !cancelled && setSession(s),
      (e: Error) => !cancelled && setError(e.message)
    )
    return () => {
      cancelled = true
    }
  }, [sessionId])

  const startConsultation = (): Promise<void> =>
    run(async () => {
      // Doctor and patient are always needed, so they are added straight away.
      const created = await sessionsApi.create()
      await sessionsApi.addParticipant(created.id, 'doctor')
      const withPeople = await sessionsApi.addParticipant(created.id, 'patient')
      setSessionId(created.id)
      return withPeople
    })

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-8">
      <header className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold tracking-tight">Consent</h1>
          <p className="text-sm text-muted-foreground">
            Everyone in the room agrees before anything is recorded.
          </p>
        </div>
        {session && (
          <Button variant="outline" disabled={busy} onClick={() => void startConsultation()}>
            New consultation
          </Button>
        )}
      </header>

      {error && (
        <Alert>
          <CircleX />
          <AlertTitle>Could not save that</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {!session ? (
        <Card>
          <CardHeader>
            <CardTitle>Start a consultation</CardTitle>
            <CardDescription>
              Add everyone in the room, read them the statement below, and record each person&apos;s
              answer. Recording stays locked until everyone has agreed.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button disabled={busy} onClick={() => void startConsultation()}>
              New consultation
            </Button>
          </CardContent>
        </Card>
      ) : (
        <>
          <ConsentStatement />
          <ParticipantsCard session={session} busy={busy} run={run} />
          <StatusCard
            session={session}
            busy={busy}
            onManual={() => void run(() => sessionsApi.switchToManual(session.id))}
            onContinue={() => goTo('session')}
          />
        </>
      )}
    </div>
  )
}

function ConsentStatement(): React.JSX.Element {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Read this to everyone present</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        <p lang="de">
          Dieses Gespräch wird mit Ihrer Zustimmung aufgezeichnet und nur auf diesem Computer in
          Text umgewandelt. Die Ärztin oder der Arzt prüft den daraus erstellten Bericht. Die
          Aufnahme wird gelöscht, sobald der Bericht freigegeben ist. Sie können ablehnen oder die
          Aufnahme jederzeit stoppen lassen. Ihre Behandlung hängt nicht davon ab.
        </p>
        <p className="text-muted-foreground">
          This conversation is recorded with your agreement and turned into text on this computer
          only. The doctor checks the record made from it. The recording is deleted once the record
          is approved. You can say no, or ask to stop at any time. Your treatment does not depend on
          it.
        </p>
      </CardContent>
    </Card>
  )
}

function ParticipantsCard({
  session,
  busy,
  run
}: {
  session: Session
  busy: boolean
  run: (action: () => Promise<Session>) => Promise<void>
}): React.JSX.Element {
  const [role, setRole] = useState<Role>('companion')
  const [label, setLabel] = useState('')
  const locked = session.mode === 'manual'

  const add = (): void => {
    void run(() => sessionsApi.addParticipant(session.id, role, label.trim())).then(() =>
      setLabel('')
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Who is in the room</CardTitle>
        <CardDescription>
          Everyone whose voice could be recorded needs to answer, the doctor included.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <ul className="flex flex-col divide-y rounded-lg border">
          {session.participants.map((p) => (
            <ParticipantRow
              key={p.id}
              participant={p}
              disabled={busy || locked}
              onDecide={(decision) =>
                void run(() => sessionsApi.decide(session.id, p.id, decision))
              }
              onRemove={() => void run(() => sessionsApi.removeParticipant(session.id, p.id))}
            />
          ))}
          {session.participants.length === 0 && (
            <li className="p-3 text-sm text-muted-foreground">Nobody added yet.</li>
          )}
        </ul>

        {!locked && (
          <form
            className="flex items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              add()
            }}
          >
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="add-role">Role</Label>
              <Select value={role} onValueChange={(v) => setRole(v as Role)}>
                <SelectTrigger id="add-role" className="w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ROLE_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-1 flex-col gap-1.5">
              <Label htmlFor="add-label">Description (optional, no names)</Label>
              <Input
                id="add-label"
                placeholder="e.g. Daughter"
                maxLength={40}
                value={label}
                onChange={(e) => setLabel(e.target.value)}
              />
            </div>
            <Button type="submit" variant="outline" disabled={busy}>
              <Plus data-icon="inline-start" />
              Add
            </Button>
          </form>
        )}
      </CardContent>
    </Card>
  )
}

function ParticipantRow({
  participant: p,
  disabled,
  onDecide,
  onRemove
}: {
  participant: Participant
  disabled: boolean
  onDecide: (decision: Decision) => void
  onRemove: () => void
}): React.JSX.Element {
  // Once someone has answered, the buttons hide until the doctor asks to change it.
  const [changing, setChanging] = useState(false)
  const answered = p.decision !== 'pending'
  const showButtons = !disabled && (!answered || changing)
  const Icon =
    p.decision === 'granted' ? CircleCheck : p.decision === 'pending' ? CircleDashed : CircleX

  const decide = (decision: Decision): void => {
    setChanging(false)
    onDecide(decision)
  }

  return (
    <li className="flex items-center gap-3 p-3">
      <Icon className="size-4 shrink-0" />
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-sm font-medium">{p.display_name}</span>
        <span className="text-xs text-muted-foreground">
          {DECISION_TEXT[p.decision]}
          {p.decided_at && ` at ${formatTime(p.decided_at)}`}
        </span>
      </div>
      {p.label && (
        <Badge variant="outline">{ROLE_OPTIONS.find((o) => o.value === p.role)?.title}</Badge>
      )}

      {showButtons ? (
        <div className="flex gap-1">
          <Button size="sm" onClick={() => decide('granted')}>
            Agrees
          </Button>
          <Button size="sm" variant="outline" onClick={() => decide('declined')}>
            Declines
          </Button>
          <Button size="sm" variant="ghost" onClick={() => decide('unable')}>
            Cannot consent
          </Button>
          {!answered && (
            <Button size="icon-sm" variant="ghost" aria-label="Remove" onClick={onRemove}>
              <Trash2 />
            </Button>
          )}
        </div>
      ) : (
        answered &&
        !disabled && (
          <Button size="sm" variant="ghost" onClick={() => setChanging(true)}>
            Change answer
          </Button>
        )
      )}
    </li>
  )
}

function StatusCard({
  session,
  busy,
  onManual,
  onContinue
}: {
  session: Session
  busy: boolean
  onManual: () => void
  onContinue: () => void
}): React.JSX.Element {
  const someoneRefused = session.participants.some(
    (p) => p.decision === 'declined' || p.decision === 'unable'
  )
  const manual = session.mode === 'manual'
  const Icon = session.recording_allowed ? CircleCheck : Lock
  const title = manual
    ? 'Documenting manually'
    : session.recording_allowed
      ? 'Everyone has agreed. Recording is allowed.'
      : 'Recording is locked'

  // Pinned to the bottom of the screen so the doctor always sees whether
  // recording is allowed, however long the list of people gets.
  return (
    <div className="sticky bottom-0 -mx-8 -mb-8 flex items-center gap-4 border-t bg-background px-8 py-4">
      <Icon className="size-5 shrink-0" />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p className="text-sm font-medium">{title}</p>
        {manual ? (
          <p className="text-xs text-muted-foreground">
            Nothing is recorded in this consultation. Start a new one to record another.
          </p>
        ) : (
          session.blockers.map((b) => (
            <p key={b} className="text-xs text-muted-foreground">
              {b}
            </p>
          ))
        )}
      </div>
      {session.recording_allowed && <Button onClick={onContinue}>Continue to session</Button>}
      {!manual && someoneRefused && (
        <Button variant="outline" disabled={busy} onClick={onManual}>
          Document manually instead
        </Button>
      )}
    </div>
  )
}
