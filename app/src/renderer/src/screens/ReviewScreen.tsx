import { ScreenLayout } from './ScreenLayout'

export function ReviewScreen(): React.JSX.Element {
  return (
    <ScreenLayout
      title="Review"
      description="The doctor checks every field against what was actually said."
      planned={[
        {
          step: 7,
          text: 'Extraction and verifier agents fill the form. Every value links to a transcript segment or stays empty.'
        },
        {
          step: 9,
          text: 'Form next to the transcript. Clicking a field jumps to its evidence. Flags show contradictions and unsupported values.'
        },
        { step: 9, text: 'Approval deletes the audio automatically.' }
      ]}
    />
  )
}
