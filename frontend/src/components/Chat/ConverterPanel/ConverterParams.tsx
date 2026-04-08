import { Button, Input, Select, Switch, Text, Tooltip } from '@fluentui/react-components'
import { ChevronDownRegular, ChevronRightRegular, InfoRegular } from '@fluentui/react-icons'
import type { ConverterCatalogEntry, ConverterParameterSchema } from '../../../types'
import { useConverterPanelStyles } from './ConverterPanel.styles'

interface ParamInputProps {
  param: ConverterParameterSchema
  value: string
  isMissing: boolean
  onChange: (name: string, value: string) => void
}

function ConverterParameterChoiceViewer({ param, value, onChange }: ParamInputProps) {
  return (
    <Select
      value={value ?? param.default_value ?? ''}
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
        placeholder={param.default_value ?? 'Select a file...'}
        onChange={(_, data) => onChange(param.name, data.value)}
        className={isMissing ? styles.paramInputError : undefined}
        data-testid={`param-${param.name}`}
      />
      <Button
        appearance="subtle"
        size="small"
        onClick={() => onBrowse(param.name)}
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
      placeholder={param.default_value ?? undefined}
      onChange={(_, data) => onChange(param.name, data.value)}
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
        className={styles.paramsSectionHeader}
        data-testid="toggle-params-btn"
      >
        Parameters
      </Button>
      {paramsExpanded && (converter.parameters ?? []).map((param) => {
        const isMissing = showValidation && param.required && !paramValues[param.name]?.trim()
        return (
          <div key={param.name} className={styles.paramBlock}>
            <span className={styles.paramLabel}>
              <Text size={200} weight="semibold">{param.name}{param.required ? ' *' : ''}</Text>
              {param.description && (
                <Tooltip content={param.description} relationship="description">
                  <span className={styles.paramInfo}><InfoRegular fontSize={12} /></span>
                </Tooltip>
              )}
            </span>
            {param.type_name === 'bool' || param.type_name === 'Optional[bool]' ? (
              <Switch
                checked={(paramValues[param.name] ?? param.default_value ?? 'false').toLowerCase() === 'true'}
                onChange={(_, data) => onParamChange(param.name, data.checked ? 'true' : 'false')}
                label={(paramValues[param.name] ?? param.default_value ?? 'false').toLowerCase() === 'true' ? 'True' : 'False'}
                data-testid={`param-${param.name}`}
              />
            ) : param.choices ? (
              <ConverterParameterChoiceViewer param={param} value={paramValues[param.name]} isMissing={isMissing} onChange={onParamChange} />
            ) : /path|file/i.test(param.name) || /path|file/i.test(param.description ?? '') ? (
              <ParameterFileViewer param={param} value={paramValues[param.name]} isMissing={isMissing} onChange={onParamChange} onBrowse={onFileBrowse} />
            ) : (
              <ConverterParameterViewer param={param} value={paramValues[param.name]} isMissing={isMissing} onChange={onParamChange} />
            )}
            {isMissing && (
              <Text size={100} className={styles.paramErrorText}>Required</Text>
            )}
            {param.type_name !== 'bool' && param.type_name !== 'Optional[bool]' && !param.choices && (
              <Text size={100} className={styles.hintText}>{param.type_name.replace(/^Optional\[(.+)\]$/, '$1')}</Text>
            )}
          </div>
        )
      })}
    </div>
  )
}
