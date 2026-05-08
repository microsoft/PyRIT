import { useState, useMemo, forwardRef } from 'react'
import {
  Table,
  TableHeader,
  TableRow,
  TableHeaderCell,
  TableBody,
  TableCell,
  Badge,
  Button,
  Text,
  Tooltip,
  Select,
} from '@fluentui/react-components'
import {
  CheckmarkRegular,
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
} from '@fluentui/react-icons'
import type { TargetInstance } from '../../types'
import { useTargetTableStyles } from './TargetTable.styles'

interface TargetTableProps {
  targets: TargetInstance[]
  activeTarget: TargetInstance | null
  onSetActiveTarget: (target: TargetInstance) => void
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

/** Render a row of modality icons; falls back to "—" when empty. */
function ModalityCell({ modalities }: { modalities: string[] | undefined }) {
  const styles = useTargetTableStyles()
  if (!modalities || modalities.length === 0) {
    return <Text size={200}>—</Text>
  }
  const ordered = MODALITY_ORDER.filter((m) => modalities.includes(m))
  const extras = modalities.filter((m) => !MODALITY_ORDER.includes(m))
  const sorted = [...ordered, ...extras]
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
  const displayName = target.model_name || '—'
  const hasUnderlying = target.underlying_model_name
    && target.model_name
    && target.underlying_model_name !== target.model_name

  if (hasUnderlying) {
    return (
      <Tooltip
        content={`Underlying model: ${target.underlying_model_name}`}
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

export default function TargetTable({ targets, activeTarget, onSetActiveTarget }: TargetTableProps) {
  const styles = useTargetTableStyles()
  const [typeFilter, setTypeFilter] = useState('')

  const targetTypes = useMemo(
    () => Array.from(new Set(targets.map(t => t.target_type))).sort(),
    [targets],
  )

  const filteredTargets = useMemo(
    () => typeFilter ? targets.filter(t => t.target_type === typeFilter) : targets,
    [targets, typeFilter],
  )

  const isActive = (target: TargetInstance): boolean =>
    activeTarget?.target_registry_name === target.target_registry_name

  return (
    <div className={styles.tableContainer}>
      {activeTarget && (
        <Table aria-label="Active target" className={styles.table} style={{ marginBottom: '12px' }}>
          <TableBody>
            <TableRow className={styles.activeRow}>
              <TableCell style={{ width: '120px' }}>
                <Badge appearance="filled" color="brand" icon={<CheckmarkRegular />}>Active</Badge>
              </TableCell>
              <TableCell style={{ width: '140px' }}>
                <Text size={200}>{activeTarget.target_type}</Text>
              </TableCell>
              <TableCell style={{ width: '160px' }}>
                <ModelCell target={activeTarget} />
              </TableCell>
              <TableCell style={{ width: '450px' }}>
                <Text size={200} className={styles.endpointCell} title={activeTarget.endpoint || undefined}>
                  {activeTarget.endpoint || '—'}
                </Text>
              </TableCell>
              <TableCell className={styles.inputsModalityCell}>
                <ModalityCell modalities={activeTarget.capabilities?.supported_input_modalities} />
              </TableCell>
              <TableCell className={styles.modalityCell}>
                <ModalityCell modalities={activeTarget.capabilities?.supported_output_modalities} />
              </TableCell>
              <CapabilityCells target={activeTarget} />
              <TableCell style={{ width: '160px' }}>
                <Text size={200} className={styles.paramsCell}>
                  {formatParams(activeTarget.target_specific_params) || '—'}
                </Text>
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      )}

      {targetTypes.length > 1 && (
        <div style={{ marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Text size={200}>Filter by type:</Text>
          <Select
            value={typeFilter}
            onChange={(_, data) => setTypeFilter(data.value)}
            style={{ minWidth: '200px' }}
          >
            <option value="">All types</option>
            {targetTypes.map(t => (
              <option key={t} value={t}>{t}</option>
            ))}
          </Select>
        </div>
      )}

      <Table aria-label="Target instances" className={styles.table}>
        <TableHeader className={styles.stickyHeader}>
          <TableRow>
            <TableHeaderCell style={{ width: '120px' }} />
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
          {filteredTargets.map((target) => (
            <TableRow
              key={target.target_registry_name}
              className={isActive(target) ? styles.activeRow : undefined}
            >
              <TableCell>
                {isActive(target) ? (
                  <Badge appearance="filled" color="brand" icon={<CheckmarkRegular />}>
                    Active
                  </Badge>
                ) : (
                  <Button
                    appearance="primary"
                    size="small"
                    onClick={() => onSetActiveTarget(target)}
                  >
                    Set Active
                  </Button>
                )}
              </TableCell>
              <TableCell>
                <Text size={200}>{target.target_type}</Text>
              </TableCell>
              <TableCell>
                <ModelCell target={target} />
              </TableCell>
              <TableCell>
                <Text size={200} className={styles.endpointCell} title={target.endpoint || undefined}>
                  {target.endpoint || '—'}
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
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
