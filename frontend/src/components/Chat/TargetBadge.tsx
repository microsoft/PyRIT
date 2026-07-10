import { Badge, Text, Tooltip } from '@fluentui/react-components'
import type { TargetInstance } from '../../types'
import { targetEndpoint, targetModelName, targetType, targetUnderlyingModelName } from '../../utils/targetIdentity'
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
  const innerTargets = target.inner_targets ?? []
  const isRoundRobin = innerTargets.length > 0

  const targetTypeName = targetType(target)
  const modelName = targetModelName(target)
  const underlyingModelName = targetUnderlyingModelName(target)
  const endpoint = targetEndpoint(target)

  // For RoundRobinTarget, prefer underlying_model_name because inner targets share
  // the same underlying model but may have different deployment names (model_name).
  // For regular targets, use model_name as before.
  const badgeModel = isRoundRobin
    ? (underlyingModelName ?? modelName)
    : modelName
  const displayName = isRoundRobin
    ? badgeModel
      ? `${targetTypeName} (${badgeModel} ×${innerTargets.length})`
      : `${targetTypeName} (×${innerTargets.length})`
    : modelName
      ? `${targetTypeName} (${modelName})`
      : targetTypeName

  const showUnderlying =
    underlyingModelName &&
    modelName &&
    underlyingModelName !== modelName
  const supportedCaps = target.capabilities
    ? CAPABILITY_LABELS.filter(c => target.capabilities?.[c.key]).map(c => c.label)
    : []
  const inputModalities = target.capabilities?.supported_input_modalities ?? []
  const outputModalities = target.capabilities?.supported_output_modalities ?? []
  const params = formatParams(target.target_specific_params)

  // Extract weights from params so we can show them next to each inner target
  const weights = target.target_specific_params?.weights as number[] | undefined

  const tooltipContent = (
    <div className={styles.tooltipBody}>
      <div className={styles.tooltipHeader}>
        <Text weight="semibold">{target.target_registry_name}</Text>
        <Text size={200}>{displayName}</Text>
        {showUnderlying && (
          <Text size={200} italic>
            Underlying model: {underlyingModelName}
          </Text>
        )}
      </div>
      {endpoint && (
        <div className={styles.tooltipSection}>
          <span className={styles.sectionLabel}>Endpoint</span>
          <Text className={styles.endpointText}>{endpoint}</Text>
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
      {/* Inner targets section — only shown for composite targets like RoundRobinTarget */}
      {isRoundRobin && (
        <div className={styles.tooltipSection}>
          <span className={styles.sectionLabel}>Inner Targets ({innerTargets.length})</span>
          {innerTargets.map((inner, idx) => (
            <div key={inner.target_registry_name} className={styles.innerTargetItem}>
              <Text size={200} weight="semibold">
                #{idx + 1}{weights?.[idx] != null ? ` (weight: ${weights[idx]})` : ''}
              </Text>
              <Text size={200}>
                {targetType(inner)}
                {targetModelName(inner) ? ` — ${targetModelName(inner)}` : ''}
              </Text>
              {targetEndpoint(inner) && (
                <Text className={styles.endpointText} size={200}>
                  {targetEndpoint(inner)}
                </Text>
              )}
            </div>
          ))}
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
