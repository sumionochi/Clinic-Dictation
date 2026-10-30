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
import { useI18n, type I18n, type MessageKey } from '@/i18n/i18n'
import { useConsultation } from '@/lib/consultation'
import {
  ROLE_OPTIONS,
  sessionsApi,
  type Blocker,
  type Decision,
  type Participant,
  type Role,
  type Session
} from '@/lib/sessions'

const DECISION_TEXT: Record<Participant['decision'], MessageKey> = {
  pending: 'consent.decision.pending',
  granted: 'consent.decision.granted',
  declined: 'consent.decision.declined',
  unable: 'consent.decision.unable'
}

function blockerText(b: Blocker, session: Session, { t, person }: I18n): string {
  const name = session.participants.find((p) => p.id === b.participant_id)?.display_name
  return t(`blocker.${b.code}`, { person: person(name) })
}

export function ConsentScreen(): React.JSX.Element {
  const { t } = useI18n()
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
          <h1 className="text-2xl font-semibold tracking-tight">{t('consent.title')}</h1>
          <p className="text-sm text-muted-foreground">{t('consent.subtitle')}</p>
        </div>
        {session && (
          <Button variant="outline" disabled={busy} onClick={() => void startConsultation()}>
            {t('consent.newConsultation')}
          </Button>
        )}
      </header>

      {error && (
        <Alert>
          <CircleX />
          <AlertTitle>{t('consent.saveError')}</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {!session ? (
        <Card>
          <CardHeader>
            <CardTitle>{t('consent.startTitle')}</CardTitle>
            <CardDescription>{t('consent.startText')}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button disabled={busy} onClick={() => void startConsultation()}>
              {t('consent.newConsultation')}
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
  const { t } = useI18n()
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('consent.readThis')}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        <p lang="de">
          Dieses Gespräch wird mit Ihrer Zustimmung aufgezeichnet und nur auf diesem Computer in
          Text umgewandelt. Die Ärztin oder der Arzt prüft den daraus erstellten Bericht. Die
          Aufnahme wird gelöscht, sobald der Bericht freigegeben ist. Sie können ablehnen oder die
          Aufnahme jederzeit stoppen lassen. Ihre Behandlung hängt nicht davon ab.
        </p>
        <p lang="en" className="text-muted-foreground">
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
  const { t } = useI18n()
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
        <CardTitle>{t('consent.whoTitle')}</CardTitle>
        <CardDescription>{t('consent.whoText')}</CardDescription>
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
            <li className="p-3 text-sm text-muted-foreground">{t('consent.nobody')}</li>
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
              <Label htmlFor="add-role">{t('consent.role')}</Label>
              <Select value={role} onValueChange={(v) => setRole(v as Role)}>
                <SelectTrigger id="add-role" className="w-48">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ROLE_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {t(o.title)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-1 flex-col gap-1.5">
              <Label htmlFor="add-label">{t('consent.description')}</Label>
              <Input
                id="add-label"
                placeholder={t('consent.descriptionPlaceholder')}
                maxLength={40}
                value={label}
                onChange={(e) => setLabel(e.target.value)}
              />
            </div>
            <Button type="submit" variant="outline" disabled={busy}>
              <Plus data-icon="inline-start" />
              {t('consent.add')}
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
  const { t, person, time } = useI18n()
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
        <span className="truncate text-sm font-medium">{person(p.display_name)}</span>
        <span className="text-xs text-muted-foreground">
          {p.decided_at
            ? t('consent.decidedAt', {
                decision: t(DECISION_TEXT[p.decision]),
                time: time(p.decided_at)
              })
            : t(DECISION_TEXT[p.decision])}
        </span>
      </div>
      {p.label && (
        <Badge variant="outline">
          {t(ROLE_OPTIONS.find((o) => o.value === p.role)?.title ?? 'role.other')}
        </Badge>
      )}

      {showButtons ? (
        <div className="flex gap-1">
          <Button size="sm" onClick={() => decide('granted')}>
            {t('consent.agrees')}
          </Button>
          <Button size="sm" variant="outline" onClick={() => decide('declined')}>
            {t('consent.declines')}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => decide('unable')}>
            {t('consent.cannotConsent')}
          </Button>
          {!answered && (
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label={t('consent.remove')}
              onClick={onRemove}
            >
              <Trash2 />
            </Button>
          )}
        </div>
      ) : (
        answered &&
        !disabled && (
          <Button size="sm" variant="ghost" onClick={() => setChanging(true)}>
            {t('consent.changeAnswer')}
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
  const i18n = useI18n()
  const { t } = i18n
  const someoneRefused = session.participants.some(
    (p) => p.decision === 'declined' || p.decision === 'unable'
  )
  const manual = session.mode === 'manual'
  const Icon = session.recording_allowed ? CircleCheck : Lock
  const title = manual
    ? t('consent.manualTitle')
    : session.recording_allowed
      ? t('consent.allowedTitle')
      : t('consent.lockedTitle')

  // Pinned to the bottom of the screen so the doctor always sees whether
  // recording is allowed, however long the list of people gets.
  return (
    <div className="sticky bottom-0 -mx-8 -mb-8 flex items-center gap-4 border-t bg-background px-8 py-4">
      <Icon className="size-5 shrink-0" />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p className="text-sm font-medium">{title}</p>
        {manual ? (
          <p className="text-xs text-muted-foreground">{t('consent.manualText')}</p>
        ) : (
          session.blocker_items.map((b) => (
            <p key={`${b.code}-${b.participant_id}`} className="text-xs text-muted-foreground">
              {blockerText(b, session, i18n)}
            </p>
          ))
        )}
      </div>
      {session.recording_allowed && <Button onClick={onContinue}>{t('consent.continue')}</Button>}
      {!manual && someoneRefused && (
        <Button variant="outline" disabled={busy} onClick={onManual}>
          {t('consent.manualButton')}
        </Button>
      )}
    </div>
  )
}
