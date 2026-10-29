import { ScreenLayout } from './ScreenLayout'

export function ConsentScreen(): React.JSX.Element {
  return (
    <ScreenLayout
      title="Consent"
      description="Everyone in the room agrees before anything is recorded."
      planned={[
        {
          step: 3,
          text: 'Add each person present (patient, companion, interpreter, student) and log their consent with role and timestamp.'
        },
        {
          step: 3,
          text: 'Recording stays locked unless everyone consents. Otherwise the doctor documents manually as usual.'
        }
      ]}
    />
  )
}
