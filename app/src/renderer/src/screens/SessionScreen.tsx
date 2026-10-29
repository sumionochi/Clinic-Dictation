import { ScreenLayout } from './ScreenLayout'

export function SessionScreen(): React.JSX.Element {
  return (
    <ScreenLayout
      title="Session"
      description="Record the consultation locally. Audio never leaves this machine."
      planned={[
        {
          step: 4,
          text: 'Record with a pause button, transcribe with Whisper large-v3, and save to disk before showing it.'
        },
        { step: 6, text: 'Label speakers and rename them to Arzt and Patient.' },
        {
          step: 10,
          text: 'Live checklist of covered and missing topics (complaint, medications, allergies, history, social situation).'
        }
      ]}
    />
  )
}
