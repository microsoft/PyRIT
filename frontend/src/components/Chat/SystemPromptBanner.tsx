import { useLayoutEffect, useRef, useState } from 'react'
import { Button, Caption1, Text, mergeClasses } from '@fluentui/react-components'
import { ChevronDownRegular, ChevronRightRegular } from '@fluentui/react-icons'
import { useSystemPromptBannerStyles } from './SystemPromptBanner.styles'

interface SystemPromptBannerProps {
  content: string
}

export default function SystemPromptBanner({ content }: SystemPromptBannerProps) {
  const styles = useSystemPromptBannerStyles()
  const [expanded, setExpanded] = useState(false)
  const [overflowing, setOverflowing] = useState(false)
  const contentRef = useRef<HTMLElement>(null)

  useLayoutEffect(() => {
    const el = contentRef.current
    if (!el) return
    const measure = () => setOverflowing(el.scrollWidth > el.clientWidth)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [content])

  const expandable = overflowing || expanded

  return (
    <div className={styles.root} data-testid="system-prompt-banner">
      {expandable ? (
        <Button
          appearance="transparent"
          size="small"
          icon={expanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
          onClick={() => setExpanded(prev => !prev)}
          className={styles.header}
          data-testid="toggle-system-prompt-banner-btn"
          aria-expanded={expanded}
        >
          System Prompt
        </Button>
      ) : (
        <Caption1 className={styles.label}>System Prompt</Caption1>
      )}
      <Text
        ref={contentRef}
        className={mergeClasses(styles.content, expanded ? styles.contentExpanded : styles.contentCollapsed)}
        data-testid="system-prompt-banner-content"
      >
        {content}
      </Text>
    </div>
  )
}
