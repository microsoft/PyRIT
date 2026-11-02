import React, { useState, useMemo, forwardRef } from 'react'
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
  Checkbox,
  MessageBar,
  MessageBarBody,
  mergeClasses,
} from '@fluentui/react-components'
import {
  CheckmarkCircleFilled,
  DismissCircleFilled,
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
  EyeOffRegular,
  EyeRegular,
} from '@fluentui/react-icons'

import { useUserPreferences } from '@/hooks/useUserPreferences'
import type { TargetCapabilityDetection } from '@/hooks/useTargetCapabilityDetection'
import type { TargetInstance } from '@/types'
import {
  sameTarget,
  targetEndpoint,
  targetModelName,
  targetType,
  targetUnderlyingModelName,
} from '@/utils/targetIdentity'

import {
  CAPABILITY_COLUMNS,
  DEFAULT_TARGET_FILTERS,
  MODALITY_LABELS,
  activeTargetFilters,
  getTargetFilterOptions,
  isSameTargetFilters,
  orderModalities,
  targetMatchesFilters,
  type TargetFilters,
} from './targetFilters'
import TargetFiltersBar from './TargetFiltersBar'
import { useTargetTableStyles } from './TargetTable.styles'
import TargetSelect from './TargetSelect'

interface TargetTableProps {
  generation?: string
  targets: TargetInstance[]
  defaultObjectiveTarget: TargetInstance | null
  defaultAdversarialTarget: TargetInstance | null
  onSetDefaultObjectiveTarget: (target: TargetInstance | null) => void
  onSetDefaultAdversarialTarget: (target: TargetInstance | null) => void
  detection: TargetCapabilityDetection
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

/** Modality → icon for input/output column rendering. The icon accepts
 *  arbitrary props so Tooltip can inject event handlers / ARIA attributes. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const MODALITY_ICONS: Record<string, React.ComponentType<any>> = {
  text: TextTRegular,
  image_path: ImageRegular,
  audio_path: MicRegular,
  video_path: VideoRegular,
  reasoning: LightbulbRegular,
  function_call: MathFormulaRegular,
  function_call_output: FunctionCallOutputIcon,
  tool_call: WrenchRegular,
  binary_path: DocumentRegular,
  url: LinkRegular,
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
        const label = MODALITY_LABELS[modality] ?? modality
        const Icon = MODALITY_ICONS[modality] ?? DocumentRegular
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
function InnerTargetRows({ parentKey, innerTargets, weights, selectionVisible, detectionVisible }: {
  parentKey: string
  innerTargets: TargetInstance[]
  weights: number[] | undefined
  selectionVisible: boolean
  detectionVisible: boolean
}) {
  const styles = useTargetTableStyles()
  return (
    <>
      {innerTargets.map((inner, idx) => (
        <TableRow key={`${parentKey}-inner-${idx}`} className={styles.innerTargetRow}>
          {selectionVisible && <TableCell className={styles.selectionCell} />}
          <TableCell className={styles.actionCell} />
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
          {detectionVisible && <TableCell className={styles.detectionCell} />}
        </TableRow>
      ))}
    </>
  )
}

export default function TargetTable({
  generation = '',
  targets,
  defaultObjectiveTarget,
  defaultAdversarialTarget,
  onSetDefaultObjectiveTarget,
  onSetDefaultAdversarialTarget,
  detection,
}: TargetTableProps) {
  const styles = useTargetTableStyles()
  const { preferences, updatePreferences } = useUserPreferences()
  const hiddenTargetRegistryNames = useMemo(
    () => new Set(preferences.hiddenTargetRegistryNames),
    [preferences.hiddenTargetRegistryNames],
  )
  const [showHiddenTargets, setShowHiddenTargets] = useState(false)
  const [previousHiddenTargetCount, setPreviousHiddenTargetCount] = useState<number | null>(null)
  const [filters, setFilters] = useState<TargetFilters>(DEFAULT_TARGET_FILTERS)
  const [selecting, setSelecting] = useState(false)
  const { running: detecting, results: detectionResults } = detection
  const [excludedTargets, setExcludedTargets] = useState<Set<string>>(new Set())
  const [detectionGeneration, setDetectionGeneration] = useState(generation)
  if (detectionGeneration !== generation) {
    setDetectionGeneration(generation)
    setSelecting(false)
    setExcludedTargets(new Set())
  }
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

  const hiddenTargetCount = useMemo(
    () => targets.filter((target) => hiddenTargetRegistryNames.has(target.target_registry_name)).length,
    [hiddenTargetRegistryNames, targets],
  )

  if (previousHiddenTargetCount !== hiddenTargetCount) {
    setPreviousHiddenTargetCount(hiddenTargetCount)
    if (hiddenTargetCount === 0 && previousHiddenTargetCount !== null) {
      setShowHiddenTargets(false)
    }
  }

  const displayedTargets = useMemo(
    () => showHiddenTargets
      ? targets
      : targets.filter((target) => !hiddenTargetRegistryNames.has(target.target_registry_name)),
    [hiddenTargetRegistryNames, showHiddenTargets, targets],
  )

  const filterOptions = useMemo(() => getTargetFilterOptions(targets), [targets])
  const activeFilters = useMemo(() => activeTargetFilters(filters, filterOptions), [filters, filterOptions])
  // A reload can remove a selected choice. Forget it (adjusting state during render, as
  // ChatWindow does) so it cannot come back on a later reload; other selections stay.
  if (!isSameTargetFilters(activeFilters, filters)) {
    setFilters(activeFilters)
  }
  const filteredTargets = useMemo(
    () => displayedTargets.filter((target: TargetInstance) => targetMatchesFilters(target, activeFilters)),
    [displayedTargets, activeFilters],
  )
  const noTargetsMatch = displayedTargets.length > 0 && filteredTargets.length === 0
  const isSelected = (target: TargetInstance): boolean => detecting
    ? Boolean(detectionResults[target.target_registry_name])
    : !excludedTargets.has(target.target_registry_name)
  const selectedTargets = filteredTargets.filter(isSelected)
  const selectionVisible = selecting || detecting
  const detectionVisible = Object.keys(detectionResults).length > 0

  const selectShown = (selected: boolean): void => {
    setExcludedTargets((current: Set<string>) => {
      const next = new Set(current)
      for (const target of filteredTargets) {
        if (selected) next.delete(target.target_registry_name)
        else next.add(target.target_registry_name)
      }
      return next
    })
  }

  const beginDetection = async (): Promise<void> => {
    setSelecting(false)
    await detection.start(selectedTargets)
  }

  const isDefaultObjective = (target: TargetInstance): boolean =>
    sameTarget(defaultObjectiveTarget, target)
  const isDefaultAdversarial = (target: TargetInstance): boolean =>
    sameTarget(defaultAdversarialTarget, target)

  const setTargetHidden = (target: TargetInstance, hidden: boolean): void => {
    const nextHiddenTargetRegistryNames = new Set(hiddenTargetRegistryNames)
    if (hidden) nextHiddenTargetRegistryNames.add(target.target_registry_name)
    else nextHiddenTargetRegistryNames.delete(target.target_registry_name)
    updatePreferences((current) => {
      const currentHiddenTargetRegistryNames = new Set(current.hiddenTargetRegistryNames)
      if (hidden) currentHiddenTargetRegistryNames.add(target.target_registry_name)
      else currentHiddenTargetRegistryNames.delete(target.target_registry_name)
      return {
        ...current,
        hiddenTargetRegistryNames: [...currentHiddenTargetRegistryNames].sort(),
      }
    })
    if (!targets.some((candidate) => nextHiddenTargetRegistryNames.has(candidate.target_registry_name))) {
      setShowHiddenTargets(false)
    }
  }

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
      <div role="toolbar" aria-label="Target controls" className={styles.visibilityControls}>
        <Button
          className={mergeClasses(styles.rowAction, !showHiddenTargets && styles.visibilityInactive)}
          appearance="subtle"
          icon={showHiddenTargets ? <EyeRegular /> : <EyeOffRegular />}
          aria-pressed={showHiddenTargets && hiddenTargetCount > 0}
          disabled={hiddenTargetCount === 0}
          onClick={() => setShowHiddenTargets((current) => !current)}
          data-testid="show-hidden-targets"
        >
          Show hidden targets ({hiddenTargetCount})
        </Button>
        <div className={styles.detectionControls}>
          <Button
            className={styles.rowAction}
            appearance="primary"
            disabled={detecting || (selecting ? selectedTargets.length === 0 : filteredTargets.length === 0)}
            onClick={() => {
              if (selecting) void beginDetection()
              else {
                setExcludedTargets(new Set())
                setSelecting(true)
              }
            }}
          >
            {detecting ? 'Detecting capabilities...' : selecting ? 'Begin capability detection' : 'Auto-detect capabilities'}
          </Button>
          {selectionVisible && (
            <Button className={styles.rowAction} disabled={detecting} onClick={() => setSelecting(false)}>
              Cancel
            </Button>
          )}
        </div>
      </div>
      <TargetFiltersBar filters={activeFilters} options={filterOptions} onFiltersChange={setFilters} />
      {Object.entries(detectionResults).filter(([, result]) => result.error).map(([name, result]) => (
        <MessageBar key={name} intent="error">
          <MessageBarBody>{name}: {result.error}</MessageBarBody>
        </MessageBar>
      ))}

      <Table aria-label="Target instances" className={styles.table}>
        <TableHeader className={styles.stickyHeader}>
          <TableRow>
            {selectionVisible && (
              <TableHeaderCell className={styles.selectionCell}>
                <Checkbox
                  aria-label="Select all shown targets"
                  disabled={detecting || filteredTargets.length === 0}
                  checked={selectedTargets.length === 0 ? false
                    : selectedTargets.length === filteredTargets.length ? true : 'mixed'}
                  onChange={(_, data) => selectShown(data.checked === true)}
                />
              </TableHeaderCell>
            )}
            <TableHeaderCell className={styles.actionCell}>Actions</TableHeaderCell>
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
            {detectionVisible && <TableHeaderCell className={styles.detectionCell}>Detection</TableHeaderCell>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {filteredTargets.map((target) => {
            const expanded = expandedRows.has(target.target_registry_name)
            const expandable = hasInnerTargets(target)
            const hidden = hiddenTargetRegistryNames.has(target.target_registry_name)
            // Extract weights from target_specific_params so we can show per-inner-target weight
            const weights = target.target_specific_params?.weights as number[] | undefined

            return (
              <React.Fragment key={target.target_registry_name}>
                <TableRow
                  className={mergeClasses(
                    (isDefaultObjective(target) || isDefaultAdversarial(target)) && styles.defaultRow,
                    hidden && styles.hiddenRow,
                  )}
                  data-testid={`target-row-${target.target_registry_name}`}
                >
                  {selectionVisible && (
                    <TableCell className={styles.selectionCell}>
                      <Checkbox
                        aria-label={`Select ${target.target_registry_name}`}
                        disabled={detecting}
                        checked={isSelected(target)}
                        onChange={(_, data) => {
                          setExcludedTargets((current: Set<string>) => {
                            const next = new Set(current)
                            if (data.checked === true) next.delete(target.target_registry_name)
                            else next.add(target.target_registry_name)
                            return next
                          })
                        }}
                      />
                    </TableCell>
                  )}
                  <TableCell className={styles.actionCell}>
                    <Button
                      className={styles.rowAction}
                      appearance="subtle"
                      size="small"
                      icon={hidden ? <EyeRegular /> : <EyeOffRegular />}
                      onClick={() => setTargetHidden(target, !hidden)}
                      aria-label={`${hidden ? 'Show' : 'Hide'} ${target.target_registry_name}`}
                      data-testid={`toggle-target-visibility-${target.target_registry_name}`}
                    >
                      {hidden ? 'Show' : 'Hide'}
                    </Button>
                  </TableCell>
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
                  {detectionVisible && (
                    <TableCell className={styles.detectionCell}>
                      <span role="status">{detectionResults[target.target_registry_name]?.state ?? ''}</span>
                    </TableCell>
                  )}
                </TableRow>

                {/* Sub-rows for each inner target, visible when the parent row is expanded */}
                {expanded && target.inner_targets && (
                  <InnerTargetRows
                    parentKey={target.target_registry_name}
                    innerTargets={target.inner_targets}
                    weights={weights}
                    selectionVisible={selectionVisible}
                    detectionVisible={detectionVisible}
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
