import { Button, Input, mergeClasses, Select, Switch, Text, Tooltip } from '@fluentui/react-components'
import { ChevronDownRegular, ChevronRightRegular, InfoRegular } from '@fluentui/react-icons'
import type { ConverterCatalogEntry, Parameter } from '../../../types'
import { useConverterPanelStyles } from './ConverterPanel.styles'

interface ParamInputProps {
  param: Parameter
  value: string
  isMissing: boolean
  labelId: string
  describedBy?: string
  onChange: (name: string, value: string) => void
}

function ConverterParameterChoiceViewer({ param, value, labelId, describedBy, onChange }: ParamInputProps) {
  const stringDefault = typeof param.default === 'string' ? param.default : ''
  return (
    <Select
      value={value ?? stringDefault}
      onChange={(_, data) => onChange(param.name, data.value)}
      aria-labelledby={labelId}
      aria-describedby={describedBy}
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

function ParameterFileViewer({ param, value, isMissing, labelId, describedBy, onChange, onBrowse }: ParamInputProps & { onBrowse: (name: string) => void }) {
  const styles = useConverterPanelStyles()
  const stringDefault = typeof param.default === 'string' ? param.default : 'Select a file...'

  return (
    <div className={styles.filePickerRow}>
      <Input
        value={value ?? ''}
        placeholder={stringDefault}
        onChange={(_, data) => onChange(param.name, data.value)}
        aria-labelledby={labelId}
        aria-describedby={describedBy}
        aria-invalid={isMissing || undefined}
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

function ConverterParameterViewer({ param, value, isMissing, labelId, describedBy, onChange }: ParamInputProps) {
  const styles = useConverterPanelStyles()
  const stringDefault = typeof param.default === 'string' ? param.default : undefined

  return (
    <Input
      value={value ?? ''}
      placeholder={stringDefault}
      onChange={(_, data) => onChange(param.name, data.value)}
      aria-labelledby={labelId}
      aria-describedby={describedBy}
      aria-invalid={isMissing || undefined}
      className={isMissing ? styles.paramInputError : undefined}
      data-testid={`param-${param.name}`}
    />
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
        const labelId = `converter-param-${param.name}-label`
        const errorId = isMissing ? `converter-param-${param.name}-error` : undefined
        const hintId = param.type_name !== 'bool' && !param.choices ? `converter-param-${param.name}-hint` : undefined
        const describedBy = [errorId, hintId].filter(Boolean).join(' ') || undefined
        return (
          <div key={param.name} className={styles.paramBlock}>
            <span id={labelId} className={styles.paramLabel}>
              <Text size={200} weight="semibold">{param.name}{param.required ? ' *' : ''}</Text>
              {param.description && (
                <Tooltip content={param.description} relationship="description">
                  <span className={styles.paramInfo}><InfoRegular fontSize={12} /></span>
                </Tooltip>
              )}
            </span>
            {param.type_name === 'bool' ? (
              <Switch
                checked={(paramValues[param.name] ?? (typeof param.default === 'string' ? param.default : 'false')).toLowerCase() === 'true'}
                onChange={(_, data) => onParamChange(param.name, data.checked ? 'true' : 'false')}
                label={(paramValues[param.name] ?? (typeof param.default === 'string' ? param.default : 'false')).toLowerCase() === 'true' ? 'True' : 'False'}
                aria-labelledby={labelId}
                aria-describedby={describedBy}
                aria-invalid={isMissing || undefined}
                data-testid={`param-${param.name}`}
              />
            ) : param.choices ? (
              <ConverterParameterChoiceViewer param={param} value={paramValues[param.name]} isMissing={isMissing} labelId={labelId} describedBy={describedBy} onChange={onParamChange} />
            ) : /path|file/i.test(param.name) || /path|file/i.test(param.description ?? '') ? (
              <ParameterFileViewer param={param} value={paramValues[param.name]} isMissing={isMissing} labelId={labelId} describedBy={describedBy} onChange={onParamChange} onBrowse={onFileBrowse} />
            ) : (
              <ConverterParameterViewer param={param} value={paramValues[param.name]} isMissing={isMissing} labelId={labelId} describedBy={describedBy} onChange={onParamChange} />
            )}
            {isMissing && (
              <Text id={errorId} size={100} className={styles.paramErrorText}>Required</Text>
            )}
            {param.type_name !== 'bool' && !param.choices && (
              <Text id={hintId} size={100} className={styles.hintText}>{param.type_name}</Text>
            )}
          </div>
        )
      })}
    </div>
  )
}
