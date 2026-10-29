import { useEffect, useRef, useState, type PointerEvent, type ReactNode } from 'react'

import {
  Label, Tooltip, mergeClasses, type LabelProps, type TooltipProps,
} from '@fluentui/react-components'

import { useContextualHelpLabelStyles } from './ContextualHelpLabel.styles'

interface ContextualHelpLabelProps {
  readonly term: string
  readonly children: ReactNode
  readonly labelProps?: LabelProps
}

export default function ContextualHelpLabel({ term, children, labelProps }: ContextualHelpLabelProps) {
  const styles = useContextualHelpLabelStyles()
  const [visible, setVisible] = useState(false)
  const touchOpen = useRef(false)
  const labelRef = useRef<HTMLLabelElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!visible) return
    const dismiss = (): void => {
      touchOpen.current = false
      setVisible(false)
    }
    const dismissOutside = (event: Event): void => {
      const target = event.target
      if (target instanceof Node && (labelRef.current?.contains(target) || contentRef.current?.contains(target))) return
      dismiss()
    }
    document.addEventListener('pointerdown', dismissOutside, true)
    document.addEventListener('scroll', dismissOutside, true)
    document.addEventListener('input', dismiss, true)
    return () => {
      document.removeEventListener('pointerdown', dismissOutside, true)
      document.removeEventListener('scroll', dismissOutside, true)
      document.removeEventListener('input', dismiss, true)
    }
  }, [visible])

  return (
    <Tooltip relationship="description" withArrow visible={visible}
      content={{ children, ref: contentRef, className: styles.content }}
      onVisibleChange={(
        event: Parameters<NonNullable<TooltipProps['onVisibleChange']>>[0],
        data: Parameters<NonNullable<TooltipProps['onVisibleChange']>>[1],
      ) => {
        if (touchOpen.current && !data.visible && (event?.type === 'blur' || event?.type === 'pointerleave')) return
        if (!data.visible) touchOpen.current = false
        setVisible(data.visible)
      }}>
      <Label {...labelProps} ref={labelRef} tabIndex={0}
        className={mergeClasses(styles.label, labelProps?.className)}
        onPointerUp={(event: PointerEvent<HTMLLabelElement>) => {
          if (event.pointerType !== 'touch') return
          touchOpen.current = !touchOpen.current
          setVisible(touchOpen.current)
        }}>
        {term}
      </Label>
    </Tooltip>
  )
}
