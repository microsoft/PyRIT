import { Button, Field, Input, mergeClasses, Select, Switch, Text, Tooltip } from '@fluentui/react-components'
import { ChevronDownRegular, ChevronRightRegular, InfoRegular } from '@fluentui/react-icons'
import type { ConverterCatalogEntry, Parameter } from '../../../types'
import { useConverterPanelStyles } from './ConverterPanel.styles'

interface ParamInputProps {
  param: Parameter
  value: string
  isMissing: boolean
  onChange: (name: string, value: string) => void
}

function ConverterParameterChoiceViewer({ param, value, onChange }: ParamInputProps) {
  return (
    <Select
      value={value ?? param.default ?? ''}
      onChange={(_, data) => onChange(param.name, data.value)}
      data-testid={`param-${param.name}`}
    >
      {(param.choices ?? []).map((choice) => (
        <option key={choice} value={choice}>
          {choice}
        </option>
      ))}
    </Select>
  )
}

function ParameterFileViewer({ param, value, isMissing, onChange, onBrowse }: ParamInputProps & { onBrowse: (name: string) => void }) {
  const styles = useConverterPanelStyles()

  return (
    <div className={styles.filePickerRow}>
      <Input
        value={value ?? ''}
        placeholder={param.default ?? 'Select a file...'}
        onChange={(_, data) => onChange(param.name, data.value)}
        className={isMissing ? styles.paramInputError : undefined}
        data-testid={`param-${param.name}`}
      />
      <Button
        appearance="subtle"
        size="small"
        onClick={() => onBrowse(param.name)}
        className={styles.touchTarget}
        data-testid={`param-${param.name}-browse`}
      >
        Browse
      </Button>
    </div>
  )
}

function ConverterParameterViewer({ param, value, isMissing, onChange }: ParamInputProps) {
  const styles = useConverterPanelStyles()

  return (
    <Input
      value={value ?? ''}
      placeholder={param.default ?? undefined}
      onChange={(_, data) => onChange(param.name, data.value)}
      className={isMissing ? styles.paramInputError : undefined}
      data-testid={`param-${param.name}`}
    />
  )
}

function ParameterNameLabel({ param }: { param: Parameter }) {
  const styles = useConverterPanelStyles()

  return (
    <span className={styles.paramLabel}>
      {param.name}
      {param.description && (
        <Tooltip content={param.description} relationship="description">
          <span className={styles.paramInfo} onClick={(e) => e.preventDefault()}>
            <InfoRegular fontSize={12} />
          </span>
        </Tooltip>
      )}
    </span>
  )
}

export interface ConverterParamsProps {
  converter: ConverterCatalogEntry
  paramValues: Record<string, string>
  paramsExpanded: boolean
  showValidation: boolean
  onParamChange: (name: string, value: string) => void
  onFileBrowse: (name: string) => void
  onToggleExpanded: () => void
}

export default function ConverterParams({ converter, paramValues, paramsExpanded, showValidation, onParamChange, onFileBrowse, onToggleExpanded }: ConverterParamsProps) {
  const styles = useConverterPanelStyles()

  if (!converter.parameters?.length) return null

  return (
    <div className={styles.paramsSection} data-testid="converter-params">
      <Button
        appearance="transparent"
        size="small"
        icon={paramsExpanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
        onClick={onToggleExpanded}
        className={mergeClasses(styles.paramsSectionHeader, styles.touchTarget)}
        data-testid="toggle-params-btn"
      >
        Parameters
      </Button>
      {paramsExpanded && (converter.parameters ?? []).map((param) => {
        const isMissing = showValidation && param.required && !paramValues[param.name]?.trim()
        const isChecked = (paramValues[param.name] ?? param.default ?? 'false').toLowerCase() === 'true'
        const typeHint = param.type_name !== 'bool' && !param.choices ? param.type_name : undefined

        return (
          <Field
            key={param.name}
            className={styles.paramBlock}
            label={<ParameterNameLabel param={param} />}
            required={param.required}
            validationMessage={isMissing ? 'Required' : undefined}
            validationState={isMissing ? 'error' : undefined}
            hint={typeHint}
          >
            {param.type_name === 'bool' ? (
              <div className={styles.filePickerRow}>
                <Switch
                  checked={isChecked}
                  onChange={(_, data) => onParamChange(param.name, data.checked ? 'true' : 'false')}
                  data-testid={`param-${param.name}`}
                />
                <Text size={200} aria-hidden="true">{isChecked ? 'True' : 'False'}</Text>
              </div>
            ) : param.choices ? (
              <ConverterParameterChoiceViewer param={param} value={paramValues[param.name]} isMissing={isMissing} onChange={onParamChange} />
            ) : /path|file/i.test(param.name) || /path|file/i.test(param.description ?? '') ? (
              <ParameterFileViewer param={param} value={paramValues[param.name]} isMissing={isMissing} onChange={onParamChange} onBrowse={onFileBrowse} />
            ) : (
              <ConverterParameterViewer param={param} value={paramValues[param.name]} isMissing={isMissing} onChange={onParamChange} />
            )}
          </Field>
        )
      })}
    </div>
  )
}
