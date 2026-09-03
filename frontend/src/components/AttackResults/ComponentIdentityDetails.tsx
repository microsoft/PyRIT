import type { ReactNode } from 'react'

import { Text } from '@fluentui/react-components'

import type { ScenarioComponentIdentity, ScenarioIdentityValue } from '@/types'

import { useComponentIdentityDetailsStyles } from './ComponentIdentityDetails.styles'

const NO_OMITTED_PARAMETERS = new Set<string>()

export interface DetailMetricProps {
  readonly label: string
  readonly value: string
}

export function DetailMetric({ label, value }: DetailMetricProps) {
  const styles = useComponentIdentityDetailsStyles()
  return (
    <div className={styles.metric} role="group" aria-label={label}>
      <Text size={200} className={styles.label}>{label}</Text>
      <Text weight="semibold" className={styles.value}>{value}</Text>
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
  readonly renderChild?: (context: IdentityChildRenderContext) => ReactNode
}

export default function ComponentIdentityDetails({
  identity,
  hideComponentName = false,
  omittedParameters = NO_OMITTED_PARAMETERS,
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
            <DetailMetric key={name} label={formatIdentityLabel(name)} value={formatIdentityValue(value)} />
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
