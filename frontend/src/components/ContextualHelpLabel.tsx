import { useState, type FocusEvent, type KeyboardEvent, type ReactNode } from 'react'

import {
  InfoLabel, Label, Tooltip, mergeClasses, type LabelProps,
} from '@fluentui/react-components'

import { useContextualHelpLabelStyles } from './ContextualHelpLabel.styles'

interface ContextualHelpLabelProps {
  readonly term: string
  readonly children: ReactNode
  readonly labelProps?: LabelProps
}

export default function ContextualHelpLabel({ term, children, labelProps }: ContextualHelpLabelProps) {
  const styles = useContextualHelpLabelStyles()
  const [hoverVisible, setHoverVisible] = useState(false)
  const [infoOpen, setInfoOpen] = useState(false)

  return (
    <InfoLabel {...labelProps}
      className={mergeClasses(styles.root, labelProps?.className)}
      root={{ onBlurCapture: (event: FocusEvent<HTMLSpanElement>) => {
        if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) {
          setInfoOpen(false)
          setHoverVisible(false)
        }
      } }}
      info={{ children, className: styles.content, onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
        // Dismiss help without also dismissing an enclosing editor.
        if (event.key === 'Escape') event.stopPropagation()
      } }}
      infoButton={{
        className: styles.infoButton,
        popover: {
          onOpenChange: (_: unknown, data: { open: boolean }) => {
            setInfoOpen(data.open)
            setHoverVisible(false)
          },
        },
      }}
      label={{ children: (_: unknown, props: LabelProps) => (
        <Tooltip relationship="description" withArrow visible={hoverVisible && !infoOpen}
          content={{ children, className: styles.content }}
          onVisibleChange={(_: unknown, data: { visible: boolean }) => {
            setHoverVisible(!infoOpen && data.visible)
          }}>
          <Label {...props} className={mergeClasses(styles.label, props.className)}>{term}</Label>
        </Tooltip>
      ) }}>
      {term}
    </InfoLabel>
  )
}
