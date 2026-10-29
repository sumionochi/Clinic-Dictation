import { ScreenLayout } from './ScreenLayout'

export function ExportScreen(): React.JSX.Element {
  return (
    <ScreenLayout
      title="Export"
      description="Send the approved record on as standard, validated FHIR."
      planned={[
        {
          step: 8,
          text: 'FHIR R4 bundle with German base profiles, plus a Provenance resource linking each field to its evidence.'
        },
        { step: 8, text: 'Validated with the HL7 FHIR validator before export.' }
      ]}
    />
  )
}
