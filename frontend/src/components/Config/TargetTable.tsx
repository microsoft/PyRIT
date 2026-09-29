import React, { useState, useMemo, forwardRef, useId } from 'react'
import type { ChangeEvent } from 'react'
import {
  Table,
  TableHeader,
  TableRow,
  TableHeaderCell,
  TableBody,
  TableCell,
  Badge,
  Button,
  Divider,
  Text,
  Tooltip,
  Select,
  type SelectOnChangeData,
} from '@fluentui/react-components'
import {
  CheckmarkCircleFilled,
  DismissCircleFilled,
  FilterDismissRegular,
  TextTRegular,
  ImageRegular,
  MicRegular,
  VideoRegular,
  DocumentRegular,
  LinkRegular,
  LightbulbRegular,
  MathFormulaRegular,
  WrenchRegular,
  ArrowHookUpLeftRegular,
  ChevronRightRegular,
  ChevronDownRegular,
} from '@fluentui/react-icons'
import type { TargetInstance } from '../../types'
import { sameTarget, targetEndpoint, targetModelName, targetType, targetUnderlyingModelName } from '../../utils/targetIdentity'
import { useTargetTableStyles } from './TargetTable.styles'
import TargetSelect from './TargetSelect'

interface TargetTableProps {
  targets: TargetInstance[]
  defaultObjectiveTarget: TargetInstance | null
  defaultAdversarialTarget: TargetInstance | null
  onSetDefaultObjectiveTarget: (target: TargetInstance | null) => void
  onSetDefaultAdversarialTarget: (target: TargetInstance | null) => void
}

/** Format target_specific_params into a short human-readable string. */
function formatParams(params?: Record<string, unknown> | null): string {
  if (!params) return ''
  const parts: string[] = []
  for (const [key, val] of Object.entries(params)) {
    if (val == null) continue
    if (key === 'extra_body_parameters' && typeof val === 'object') {
      for (const [k, v] of Object.entries(val as Record<string, unknown>)) {
        parts.push(`${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
      }
    } else {
      parts.push(`${key}: ${typeof val === 'object' ? JSON.stringify(val) : String(val)}`)
    }
  }
  return parts.join('\n')
}

/** Capability column definitions with tooltip descriptions. */
const CAPABILITY_COLUMNS = [
  { key: 'supports_multi_turn', label: 'Multi-turn', tooltip: 'Supports multi-turn conversations' },
  { key: 'supports_multi_message_pieces', label: 'Multi-piece', tooltip: 'Supports multiple message pieces in a single request' },
  { key: 'supports_json_schema', label: 'JSON Schema', tooltip: 'Supports constraining output to a JSON schema' },
  { key: 'supports_json_output', label: 'JSON Output', tooltip: 'Supports JSON output format' },
  { key: 'supports_editable_history', label: 'Edit History', tooltip: 'Allows attack history to be modified' },
  { key: 'supports_system_prompt', label: 'System Prompt', tooltip: 'Supports system prompts' },
] as const

const COLUMN_TOOLTIPS = {
  registryName: 'Unique name used to identify this configured target',
  type: 'Target class implementation',
  model: 'Configured model name. A dotted underline indicates the deployment alias differs from the underlying model — hover the value to see it.',
  endpoint: 'API endpoint URL the target sends requests to',
  parameters: 'Target-specific configuration parameters (e.g., reasoning_effort, max_output_tokens)',
  inputs: 'Modalities the target accepts as input',
  outputs: 'Modalities the target can produce as output',
} as const

/** Composite icon: f(x) with a small return-arrow badge for function call outputs. */
const FunctionCallOutputIcon = forwardRef<HTMLSpanElement, React.HTMLAttributes<HTMLSpanElement> & { className?: string }>(
  function FunctionCallOutputIcon({ className, ...rest }, ref) {
    const styles = useTargetTableStyles()
    return (
      <span ref={ref} className={styles.compositeIcon} {...rest}>
        <MathFormulaRegular className={className} />
        <ArrowHookUpLeftRegular className={styles.compositeBadge} />
      </span>
    )
  }
)

/** Modality → (icon, label) for input/output column rendering. The renderer accepts
 *  arbitrary props so Tooltip can inject event handlers / ARIA attributes. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const MODALITY_RENDERERS: Record<string, { Icon: React.ComponentType<any>; label: string }> = {
  text: { Icon: TextTRegular, label: 'Text' },
  image_path: { Icon: ImageRegular, label: 'Image' },
  audio_path: { Icon: MicRegular, label: 'Audio' },
  video_path: { Icon: VideoRegular, label: 'Video' },
  reasoning: { Icon: LightbulbRegular, label: 'Reasoning' },
  function_call: { Icon: MathFormulaRegular, label: 'Function call' },
  function_call_output: { Icon: FunctionCallOutputIcon, label: 'Function call output' },
  tool_call: { Icon: WrenchRegular, label: 'Tool call' },
  binary_path: { Icon: DocumentRegular, label: 'Binary' },
  url: { Icon: LinkRegular, label: 'URL' },
}

/** Canonical display order for modality icons; unknown values are appended last. */
const MODALITY_ORDER: readonly string[] = [
  'text',
  'image_path',
  'audio_path',
  'video_path',
  'reasoning',
  'function_call',
  'function_call_output',
  'tool_call',
  'binary_path',
  'url',
]

/** Capability list that a modality filter reads. */
type ModalityField = 'supported_input_modalities' | 'supported_output_modalities'

/** A filter dropdown choice: the raw value and the text shown for it. */
interface FilterOption {
  value: string
  label: string
}

/** Known modalities in canonical order, followed by the rest in their given order. */
function orderModalities(modalities: string[]): string[] {
  const known = MODALITY_ORDER.filter((modality: string) => modalities.includes(modality))
  const extras = modalities.filter((modality: string) => !MODALITY_ORDER.includes(modality))
  return [...known, ...extras]
}

/** Whether the target lists the modality under the given capability field. */
function supportsModality(target: TargetInstance, field: ModalityField, modality: string): boolean {
  return (target.capabilities?.[field] ?? []).includes(modality)
}

/** Every modality the targets list under one field, labeled and in display order. */
function modalityFilterOptions(targets: TargetInstance[], field: ModalityField): FilterOption[] {
  const present = new Set<string>()
  for (const target of targets) {
    for (const modality of target.capabilities?.[field] ?? []) {
      present.add(modality)
    }
  }
  return orderModalities([...present].sort()).map((modality: string) => ({
    value: modality,
    label: MODALITY_RENDERERS[modality]?.label ?? modality,
  }))
}

/** A modality filter can narrow the table only if some target lacks one of the listed modalities. */
function canFilterByModality(targets: TargetInstance[], field: ModalityField, options: FilterOption[]): boolean {
  return options.some((option: FilterOption) =>
    targets.some((target: TargetInstance) => !supportsModality(target, field, option.value)))
}

/** Render a row of modality icons; falls back to "—" when empty. */
function ModalityCell({ modalities }: { modalities: string[] | undefined }) {
  const styles = useTargetTableStyles()
  if (!modalities || modalities.length === 0) {
    return <Text size={200}>—</Text>
  }
  const sorted = orderModalities(modalities)
  return (
    <div className={styles.modalityRow}>
      {sorted.map((modality) => {
        const renderer = MODALITY_RENDERERS[modality]
        const label = renderer?.label ?? modality
        const Icon = renderer?.Icon ?? DocumentRegular
        return (
          <Tooltip key={modality} content={label} relationship="label">
            <Icon className={styles.modalityIcon} />
          </Tooltip>
        )
      })}
    </div>
  )
}

/** Render a capability indicator: ✓ (green) / ✗ (red) / — (unknown). */
function CapabilityCell({ value }: { value: boolean | undefined }) {
  const styles = useTargetTableStyles()
  if (value === undefined) {
    return <Text size={200}>—</Text>
  }
  if (value) {
    return <CheckmarkCircleFilled className={styles.capabilityIconSupported} />
  }
  return <DismissCircleFilled className={styles.capabilityIconUnsupported} />
}

/** Render the model cell with a tooltip when underlying model differs. */
function ModelCell({ target }: { target: TargetInstance }) {
  const modelName = targetModelName(target)
  const underlyingModelName = targetUnderlyingModelName(target)
  const displayName = modelName || '—'
  const hasUnderlying = underlyingModelName
    && modelName
    && underlyingModelName !== modelName

  if (hasUnderlying) {
    return (
      <Tooltip
        content={`Underlying model: ${underlyingModelName}`}
        relationship="description"
      >
        <Text size={200} style={{ textDecoration: 'underline dotted', cursor: 'help' }}>
          {displayName}
        </Text>
      </Tooltip>
    )
  }

  return <Text size={200}>{displayName}</Text>
}

/** Render capability cells for a target. */
function CapabilityCells({ target }: { target: TargetInstance }) {
  const styles = useTargetTableStyles()
  return (
    <>
      {CAPABILITY_COLUMNS.map(({ key }) => (
        <TableCell key={key} className={styles.capabilityCell}>
          <CapabilityCell
            value={target.capabilities?.[key]}
          />
        </TableCell>
      ))}
    </>
  )
}

/** Render expandable sub-rows for a RoundRobinTarget's inner targets. */
function InnerTargetRows({ parentKey, innerTargets, weights }: {
  parentKey: string
  innerTargets: TargetInstance[]
  weights: number[] | undefined
}) {
  const styles = useTargetTableStyles()
  return (
    <>
      {innerTargets.map((inner, idx) => (
        <TableRow key={`${parentKey}-inner-${idx}`} className={styles.innerTargetRow}>
          <TableCell className={styles.registryNameCell}>
            <Text size={200} className={styles.registryNameText}>#{idx + 1} {inner.target_registry_name}</Text>
          </TableCell>
          <TableCell>
            <Text size={200}>{targetType(inner)}</Text>
          </TableCell>
          <TableCell>
            <ModelCell target={inner} />
          </TableCell>
          <TableCell>
            <Text size={200} className={styles.endpointCell} title={targetEndpoint(inner) || undefined}>
              {targetEndpoint(inner) || '—'}
            </Text>
          </TableCell>
          <TableCell className={styles.inputsModalityCell}>
            <ModalityCell modalities={inner.capabilities?.supported_input_modalities} />
          </TableCell>
          <TableCell className={styles.modalityCell}>
            <ModalityCell modalities={inner.capabilities?.supported_output_modalities} />
          </TableCell>
          <CapabilityCells target={inner} />
          <TableCell>
            <Text size={200} className={styles.paramsCell}>
              {weights?.[idx] != null ? `weight: ${weights[idx]}` : '—'}
            </Text>
          </TableCell>
        </TableRow>
      ))}
    </>
  )
}

interface FilterSelectProps {
  label: string
  allLabel: string
  value: string
  options: FilterOption[]
  onChange: (value: string) => void
  testId: string
}

/** A labeled table filter whose empty value means no filtering. */
function FilterSelect({ label, allLabel, value, options, onChange, testId }: FilterSelectProps) {
  const styles = useTargetTableStyles()
  const selectId = useId()
  return (
    <div className={styles.filterGroup}>
      <label className={styles.filterLabel} htmlFor={selectId}>
        <Text size={200}>{label}</Text>
      </label>
      <Select
        id={selectId}
        className={styles.filterSelect}
        value={value}
        onChange={(_: ChangeEvent<HTMLSelectElement>, data: SelectOnChangeData) => onChange(data.value)}
        data-testid={testId}
      >
        <option value="">{allLabel}</option>
        {options.map((option: FilterOption) => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </Select>
    </div>
  )
}

export default function TargetTable({
  targets,
  defaultObjectiveTarget,
  defaultAdversarialTarget,
  onSetDefaultObjectiveTarget,
  onSetDefaultAdversarialTarget,
}: TargetTableProps) {
  const styles = useTargetTableStyles()
  const [typeFilter, setTypeFilter] = useState('')
  const [inputFilter, setInputFilter] = useState('')
  const [outputFilter, setOutputFilter] = useState('')
  // Tracks which RoundRobinTarget rows are expanded to show inner targets.
  // We use a Set of target_registry_name strings — when a name is in the set,
  // that row's sub-rows are visible.
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set())

  const toggleExpanded = (registryName: string) => {
    setExpandedRows((prev) => {
      const next = new Set(prev)
      if (next.has(registryName)) {
        next.delete(registryName)
      } else {
        next.add(registryName)
      }
      return next
    })
  }

  const hasInnerTargets = (target: TargetInstance): boolean =>
    (target.inner_targets ?? []).length > 0

  const typeOptions = useMemo(
    () => Array.from(new Set(targets.map((target: TargetInstance) => targetType(target))))
      .sort()
      .map((type: string) => ({ value: type, label: type })),
    [targets],
  )
  const inputOptions = useMemo(
    () => modalityFilterOptions(targets, 'supported_input_modalities'),
    [targets],
  )
  const outputOptions = useMemo(
    () => modalityFilterOptions(targets, 'supported_output_modalities'),
    [targets],
  )

  const showTypeFilter = typeOptions.length > 1
  const showInputFilter = canFilterByModality(targets, 'supported_input_modalities', inputOptions)
  const showOutputFilter = canFilterByModality(targets, 'supported_output_modalities', outputOptions)

  const filteredTargets = useMemo(
    () => targets.filter((target: TargetInstance) =>
      (!typeFilter || targetType(target) === typeFilter)
      && (!inputFilter || supportsModality(target, 'supported_input_modalities', inputFilter))
      && (!outputFilter || supportsModality(target, 'supported_output_modalities', outputFilter))),
    [targets, typeFilter, inputFilter, outputFilter],
  )
  const hasActiveFilter = Boolean(typeFilter || inputFilter || outputFilter)
  const noTargetsMatch = targets.length > 0 && filteredTargets.length === 0

  const resetFilters = () => {
    setTypeFilter('')
    setInputFilter('')
    setOutputFilter('')
  }

  const isDefaultObjective = (target: TargetInstance): boolean =>
    sameTarget(defaultObjectiveTarget, target)
  const isDefaultAdversarial = (target: TargetInstance): boolean =>
    sameTarget(defaultAdversarialTarget, target)

  return (
    <div className={styles.tableContainer} data-testid="target-table-scroll-region">
      <section aria-label="Target defaults" className={styles.defaultsSummary}>
        <TargetSelect
          label="Default objective target"
          targets={targets}
          value={defaultObjectiveTarget?.target_registry_name ?? ''}
          onChange={onSetDefaultObjectiveTarget}
          placeholder="Not set"
        />
        <TargetSelect
          label="Default adversarial target"
          targets={targets.filter((target: TargetInstance) => target.capabilities?.supports_multi_turn === true)}
          value={defaultAdversarialTarget?.target_registry_name ?? ''}
          onChange={onSetDefaultAdversarialTarget}
          placeholder="Use server default"
        />
      </section>
      <Divider appearance="strong" className={styles.defaultsDivider} />
      {(showTypeFilter || showInputFilter || showOutputFilter) && (
        <div className={styles.filterRow}>
          {showTypeFilter && (
            <FilterSelect
              label="Filter by type:"
              allLabel="All types"
              value={typeFilter}
              options={typeOptions}
              onChange={setTypeFilter}
              testId="target-type-filter"
            />
          )}
          {showInputFilter && (
            <FilterSelect
              label="Filter by input:"
              allLabel="All inputs"
              value={inputFilter}
              options={inputOptions}
              onChange={setInputFilter}
              testId="target-input-filter"
            />
          )}
          {showOutputFilter && (
            <FilterSelect
              label="Filter by output:"
              allLabel="All outputs"
              value={outputFilter}
              options={outputOptions}
              onChange={setOutputFilter}
              testId="target-output-filter"
            />
          )}
          <Tooltip content="Reset all filters" relationship="label">
            <Button
              className={styles.resetFiltersButton}
              appearance="subtle"
              size="small"
              icon={<FilterDismissRegular />}
              aria-label="Reset all filters"
              disabled={!hasActiveFilter}
              onClick={resetFilters}
              data-testid="target-reset-filters-btn"
            />
          </Tooltip>
        </div>
      )}

      <Table aria-label="Target instances" className={styles.table}>
        <TableHeader className={styles.stickyHeader}>
          <TableRow>
            <TableHeaderCell style={{ width: '180px' }}>
              <Tooltip content={COLUMN_TOOLTIPS.registryName} relationship="description">
                <span className={styles.helpHeader}>Registry Name</span>
              </Tooltip>
            </TableHeaderCell>
            <TableHeaderCell style={{ width: '140px' }}>
              <Tooltip content={COLUMN_TOOLTIPS.type} relationship="description">
                <span className={styles.helpHeader}>Type</span>
              </Tooltip>
            </TableHeaderCell>
            <TableHeaderCell style={{ width: '160px' }}>
              <Tooltip content={COLUMN_TOOLTIPS.model} relationship="description">
                <span className={styles.helpHeader}>Model</span>
              </Tooltip>
            </TableHeaderCell>
            <TableHeaderCell style={{ width: '450px' }}>
              <Tooltip content={COLUMN_TOOLTIPS.endpoint} relationship="description">
                <span className={styles.helpHeader}>Endpoint</span>
              </Tooltip>
            </TableHeaderCell>
            <TableHeaderCell className={styles.inputsModalityCell}>
              <Tooltip content={COLUMN_TOOLTIPS.inputs} relationship="description">
                <span className={styles.helpHeader}>Inputs</span>
              </Tooltip>
            </TableHeaderCell>
            <TableHeaderCell className={styles.modalityCell}>
              <Tooltip content={COLUMN_TOOLTIPS.outputs} relationship="description">
                <span className={styles.helpHeader}>Outputs</span>
              </Tooltip>
            </TableHeaderCell>
            {CAPABILITY_COLUMNS.map(({ key, label, tooltip }) => (
              <TableHeaderCell key={key} className={styles.capabilityCell}>
                <Tooltip content={tooltip} relationship="description">
                  <span className={styles.helpHeader}>{label}</span>
                </Tooltip>
              </TableHeaderCell>
            ))}
            <TableHeaderCell style={{ width: '160px' }}>
              <Tooltip content={COLUMN_TOOLTIPS.parameters} relationship="description">
                <span className={styles.helpHeader}>Parameters</span>
              </Tooltip>
            </TableHeaderCell>
          </TableRow>
        </TableHeader>
        <TableBody>
          {filteredTargets.map((target) => {
            const expanded = expandedRows.has(target.target_registry_name)
            const expandable = hasInnerTargets(target)
            // Extract weights from target_specific_params so we can show per-inner-target weight
            const weights = target.target_specific_params?.weights as number[] | undefined

            return (
              <React.Fragment key={target.target_registry_name}>
                <TableRow
                  className={isDefaultObjective(target) || isDefaultAdversarial(target) ? styles.defaultRow : undefined}
                  data-testid={`target-row-${target.target_registry_name}`}
                >
                  <TableCell className={styles.registryNameCell}>
                    <Text size={200} className={styles.registryNameText}>{target.target_registry_name}</Text>
                    {(isDefaultObjective(target) || isDefaultAdversarial(target)) && (
                      <div className={styles.defaultIndicators}>
                        {isDefaultObjective(target) && (
                          <Badge appearance="tint" color="brand" size="small" aria-label="Default objective target">
                            Objective
                          </Badge>
                        )}
                        {isDefaultAdversarial(target) && (
                          <Badge appearance="outline" color="brand" size="small" aria-label="Default adversarial target">
                            Adversarial
                          </Badge>
                        )}
                      </div>
                    )}
                  </TableCell>
                  <TableCell>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                      {expandable && (
                        <Button
                          className={styles.rowAction}
                          appearance="subtle"
                          size="small"
                          icon={expanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
                          onClick={() => toggleExpanded(target.target_registry_name)}
                          aria-label={expanded ? 'Collapse inner targets' : 'Expand inner targets'}
                        />
                      )}
                      <Text size={200}>{targetType(target)}</Text>
                    </div>
                  </TableCell>
                  <TableCell>
                    <ModelCell target={target} />
                  </TableCell>
                  <TableCell>
                    <Text size={200} className={styles.endpointCell} title={targetEndpoint(target) || undefined}>
                      {targetEndpoint(target) || '—'}
                    </Text>
                  </TableCell>
                  <TableCell className={styles.inputsModalityCell}>
                    <ModalityCell modalities={target.capabilities?.supported_input_modalities} />
                  </TableCell>
                  <TableCell className={styles.modalityCell}>
                    <ModalityCell modalities={target.capabilities?.supported_output_modalities} />
                  </TableCell>
                  <CapabilityCells target={target} />
                  <TableCell>
                    <Text size={200} className={styles.paramsCell}>
                      {formatParams(target.target_specific_params) || '—'}
                    </Text>
                  </TableCell>
                </TableRow>

                {/* Sub-rows for each inner target, visible when the parent row is expanded */}
                {expanded && target.inner_targets && (
                  <InnerTargetRows
                    parentKey={target.target_registry_name}
                    innerTargets={target.inner_targets}
                    weights={weights}
                  />
                )}
              </React.Fragment>
            )
          })}
        </TableBody>
      </Table>
      {/* Stays mounted so screen readers announce the message when filtering hides every row. */}
      <div role="status">
        {noTargetsMatch && (
          <div className={styles.noMatchState} data-testid="target-table-no-match">
            <Text size={400}>No targets match the selected filters.</Text>
            <Text size={200}>Try adjusting your filters.</Text>
          </div>
        )}
      </div>
    </div>
  )
}
