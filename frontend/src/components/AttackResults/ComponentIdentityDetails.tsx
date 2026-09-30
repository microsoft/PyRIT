import { useId, useState, type ReactNode } from 'react'

import { Button, mergeClasses, Text } from '@fluentui/react-components'
import { ChevronDownRegular, ChevronUpRegular } from '@fluentui/react-icons'

import type { ScenarioComponentIdentity, ScenarioIdentityValue } from '@/types'

import { useComponentIdentityDetailsStyles } from './ComponentIdentityDetails.styles'

const NO_OMITTED_PARAMETERS = new Set<string>()
const COLLAPSIBLE_VALUE_LENGTH = 240

export interface DetailMetricProps {
  readonly label: string
  readonly value: string
  readonly collapsible?: boolean
}

export function DetailMetric({ label, value, collapsible = false }: DetailMetricProps) {
  const styles = useComponentIdentityDetailsStyles()
  const contentId = useId()
  const [expanded, setExpanded] = useState(false)
  const canCollapse = collapsible && value.length > COLLAPSIBLE_VALUE_LENGTH
  return (
    <div className={styles.metric} role="group" aria-label={label}>
      <Text size={200} className={styles.label}>{label}</Text>
      <Text
        id={contentId}
        weight="semibold"
        className={mergeClasses(styles.value, canCollapse && !expanded && styles.collapsedValue)}
      >
        {value}
      </Text>
      {canCollapse && (
        <Button
          appearance="transparent"
          size="small"
          className={styles.valueToggle}
          icon={expanded ? <ChevronUpRegular /> : <ChevronDownRegular />}
          aria-controls={contentId}
          aria-expanded={expanded}
          aria-label={expanded ? `Collapse ${label}` : `Show full ${label}`}
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? 'Show less' : 'Show more'}
        </Button>
      )}
    </div>
  )
}

export interface IdentityChildRenderContext {
  readonly childName: string
  readonly identity: ScenarioComponentIdentity
  readonly index: number
}

interface ComponentIdentityDetailsProps {
  readonly identity: ScenarioComponentIdentity
  readonly hideComponentName?: boolean
  readonly omittedParameters?: ReadonlySet<string>
  readonly collapseParameterValues?: boolean
  readonly renderChild?: (context: IdentityChildRenderContext) => ReactNode
}

export default function ComponentIdentityDetails({
  identity,
  hideComponentName = false,
  omittedParameters = NO_OMITTED_PARAMETERS,
  collapseParameterValues = false,
  renderChild,
}: ComponentIdentityDetailsProps) {
  const styles = useComponentIdentityDetailsStyles()
  const parameters = Object.entries(identity.parameters)
    .filter(([name]) => !omittedParameters.has(name))
  const children = Object.entries(identity.children)

  return (
    <div className={styles.identity}>
      {!hideComponentName && <Text weight="semibold">{identity.component_name}</Text>}
      {parameters.length > 0 && (
        <div className={styles.fields}>
          {parameters.map(([name, value]) => (
            <DetailMetric
              key={name}
              label={formatIdentityLabel(name)}
              value={formatIdentityValue(value)}
              collapsible={collapseParameterValues}
            />
          ))}
        </div>
      )}
      {children.map(([childName, childIdentities]) => (
        <div key={childName} className={styles.childSection}>
          <Text size={200} className={styles.label}>{formatIdentityLabel(childName)}</Text>
          <div className={styles.childList}>
            {childIdentities.map((childIdentity, index) => {
              const customChild = renderChild?.({ childName, identity: childIdentity, index })
              return (
                <div key={`${childIdentity.component_name}-${index}`} className={styles.child}>
                  {customChild ?? (
                    <ComponentIdentityDetails
                      identity={childIdentity}
                      omittedParameters={omittedParameters}
                      collapseParameterValues={collapseParameterValues}
                      renderChild={renderChild}
                    />
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}

function formatIdentityLabel(name: string): string {
  const words = name.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

function formatIdentityValue(value: ScenarioIdentityValue): string {
  if (typeof value === 'string') {
    return value
  }
  if (Array.isArray(value) || (typeof value === 'object' && value !== null)) {
    return JSON.stringify(value)
  }
  return String(value)
}
