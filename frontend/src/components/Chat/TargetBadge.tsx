import { Badge, Text, Tooltip } from '@fluentui/react-components'
import type { TargetInstance } from '../../types'
import { useTargetBadgeStyles } from './TargetBadge.styles'

interface TargetBadgeProps {
  target: TargetInstance
}

const CAPABILITY_LABELS: Array<{ key: keyof NonNullable<TargetInstance['capabilities']>; label: string }> = [
  { key: 'supports_multi_turn', label: 'Multi-turn' },
  { key: 'supports_multi_message_pieces', label: 'Multi-piece' },
  { key: 'supports_json_schema', label: 'JSON schema' },
  { key: 'supports_json_output', label: 'JSON output' },
  { key: 'supports_editable_history', label: 'Editable history' },
  { key: 'supports_system_prompt', label: 'System prompt' },
]

function formatParams(params?: Record<string, unknown> | null): string {
  if (!params) return ''
  const lines: string[] = []
  for (const [key, val] of Object.entries(params)) {
    if (val == null) continue
    if (key === 'extra_body_parameters' && typeof val === 'object') {
      for (const [k, v] of Object.entries(val as Record<string, unknown>)) {
        lines.push(`${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
      }
    } else {
      lines.push(`${key}: ${typeof val === 'object' ? JSON.stringify(val, null, 2) : String(val)}`)
    }
  }
  return lines.join('\n')
}

export default function TargetBadge({ target }: TargetBadgeProps) {
  const styles = useTargetBadgeStyles()
  const displayName = target.model_name
    ? `${target.target_type} (${target.model_name})`
    : target.target_type
  const showUnderlying =
    target.underlying_model_name &&
    target.model_name &&
    target.underlying_model_name !== target.model_name
  const supportedCaps = target.capabilities
    ? CAPABILITY_LABELS.filter(c => target.capabilities?.[c.key]).map(c => c.label)
    : []
  const inputModalities = target.capabilities?.supported_input_modalities ?? []
  const outputModalities = target.capabilities?.supported_output_modalities ?? []
  const params = formatParams(target.target_specific_params)

  const tooltipContent = (
    <div className={styles.tooltipBody}>
      <div className={styles.tooltipHeader}>
        <Text weight="semibold">{target.target_registry_name}</Text>
        <Text size={200}>{displayName}</Text>
        {showUnderlying && (
          <Text size={200} italic>
            Underlying model: {target.underlying_model_name}
          </Text>
        )}
      </div>
      {target.endpoint && (
        <div className={styles.tooltipSection}>
          <span className={styles.sectionLabel}>Endpoint</span>
          <Text className={styles.endpointText}>{target.endpoint}</Text>
        </div>
      )}
      {(inputModalities.length > 0 || outputModalities.length > 0) && (
        <div className={styles.tooltipSection}>
          <span className={styles.sectionLabel}>Modalities</span>
          {inputModalities.length > 0 && (
            <Text size={200}>In: {inputModalities.join(', ')}</Text>
          )}
          {outputModalities.length > 0 && (
            <Text size={200}>Out: {outputModalities.join(', ')}</Text>
          )}
        </div>
      )}
      {target.capabilities && (
        <div className={styles.tooltipSection}>
          <span className={styles.sectionLabel}>Capabilities</span>
          <div className={styles.flagsRow}>
            {supportedCaps.length > 0 ? (
              supportedCaps.map(cap => (
                <Badge key={cap} appearance="outline" size="small">
                  {cap}
                </Badge>
              ))
            ) : (
              <Text size={200} italic>None</Text>
            )}
          </div>
        </div>
      )}
      {params && (
        <div className={styles.tooltipSection}>
          <span className={styles.sectionLabel}>Parameters</span>
          <pre className={styles.paramsBlock}>{params}</pre>
        </div>
      )}
    </div>
  )

  return (
    <Tooltip
      content={{ children: tooltipContent, className: styles.tooltipSurface }}
      relationship="description"
      withArrow
      positioning="below-start"
    >
      <span
        className={styles.badge}
        data-testid="target-badge"
        aria-label={`Active target: ${target.target_registry_name}`}
        tabIndex={0}
      >
        <Text className={styles.badgeText} size={200} weight="semibold">
          {displayName}
        </Text>
      </span>
    </Tooltip>
  )
}
