import { useLayoutEffect, useRef, useState } from 'react'

import { Badge, Button, Text, mergeClasses } from '@fluentui/react-components'
import { ChevronDownRegular, ChevronUpRegular } from '@fluentui/react-icons'

import { useObjectiveHeaderStyles } from './ObjectiveHeader.styles'

interface ObjectiveHeaderProps {
  objective: string
}

export default function ObjectiveHeader({ objective }: ObjectiveHeaderProps) {
  const styles = useObjectiveHeaderStyles()
  const [expanded, setExpanded] = useState(false)
  const [overflowing, setOverflowing] = useState(false)
  const contentRef = useRef<HTMLElement>(null)

  useLayoutEffect(() => {
    const content = contentRef.current
    if (!content) return

    const measure = () => {
      if (expanded) return
      setOverflowing(content.scrollWidth > content.clientWidth)
    }

    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(content)
    return () => observer.disconnect()
  }, [objective, expanded])

  if (!objective) return null

  const showToggle = overflowing || expanded

  return (
    <div className={styles.root} data-testid="objective-header">
      <Badge className={styles.label} appearance="tint" color="brand" size="small">
        Objective
      </Badge>
      <Text
        ref={contentRef}
        className={mergeClasses(styles.content, expanded ? styles.contentExpanded : styles.contentCollapsed)}
        data-testid="objective-header-content"
      >
        {objective}
      </Text>
      {showToggle && (
        <Button
          appearance="transparent"
          size="small"
          icon={expanded ? <ChevronUpRegular /> : <ChevronDownRegular />}
          iconPosition="after"
          onClick={() => setExpanded((previous: boolean) => !previous)}
          className={styles.toggle}
          data-testid="toggle-objective-header-btn"
          aria-expanded={expanded}
          aria-label={expanded ? 'Show less of the objective' : 'Show more of the objective'}
        >
          {expanded ? 'Show less' : 'Show more'}
        </Button>
      )}
    </div>
  )
}
