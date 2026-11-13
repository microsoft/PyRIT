import { Badge, Button, Text } from '@fluentui/react-components'

import type { FindingListItem } from '@/types'
import { findingPathName } from '@/utils/findingTree'

import { useFindingPathsPanelStyles } from './FindingPathsPanel.styles'

interface FindingPathsPanelProps {
  parent: FindingListItem
  /** The parent's direct sub-findings, which name the steps. */
  subFindings: readonly FindingListItem[]
  onEdit: (trigger: HTMLElement) => void
}

/** Each ordered way a finding's sub-findings demonstrate it, and whether every step of that way was demonstrated. */
export default function FindingPathsPanel({ parent, subFindings, onEdit }: FindingPathsPanelProps) {
  const styles = useFindingPathsPanelStyles()
  const byId = new Map(subFindings.map(finding => [finding.id, finding]))
  return (
    <section className={styles.root} aria-label={`Paths of ${parent.title}`}>
      <div className={styles.header}>
        <Text weight="semibold">Paths ({parent.paths.length})</Text>
        <Button className={styles.button} aria-label={`Edit paths: ${parent.title}`}
          onClick={event => { onEdit(event.currentTarget) }}>Edit paths</Button>
      </div>
      {parent.paths.length === 0 ? (
        <Text className={styles.muted}>
          No paths yet. Add one to record which sub-findings, in order, demonstrate this finding.
        </Text>
      ) : (
        <ol className={styles.list}>
          {parent.paths.map((path, index) => (
            <li key={path.id} className={styles.path}>
              <div className={styles.pathHeader}>
                <Text weight="semibold">{findingPathName(path, index)}</Text>
                <Badge className={styles.badge} appearance="tint" color={path.complete ? 'success' : 'warning'}>
                  {path.complete ? 'Complete' : 'Partial'}
                </Badge>
              </div>
              {path.description && <Text className={styles.muted}>{path.description}</Text>}
              <ol className={styles.steps} aria-label={`Steps of ${findingPathName(path, index)}`}>
                {path.steps.map((step, position) => {
                  const subFinding = byId.get(step)
                  return (
                    <li key={step} className={styles.step}>
                      {position > 0 && <span aria-hidden="true" className={styles.arrow}>→</span>}
                      <Text>{subFinding?.title ?? 'Unknown sub-finding'}</Text>
                      {subFinding?.demonstrated === false && <Text className={styles.missing}>(not demonstrated)</Text>}
                    </li>
                  )
                })}
              </ol>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
