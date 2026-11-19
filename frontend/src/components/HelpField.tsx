import type { ReactNode } from 'react'

import { Field, type FieldProps, type LabelProps } from '@fluentui/react-components'

import ContextualHelpLabel from './ContextualHelpLabel'
import { useContextualHelpLabelStyles } from './ContextualHelpLabel.styles'

interface HelpFieldProps extends Omit<FieldProps, 'label' | 'hint'> {
  readonly label: string
  readonly help?: string
  readonly hint?: ReactNode
}

/** Keeps the field description available to assistive technology even when its help is closed. */
export default function HelpField({ label, help, hint, ...props }: HelpFieldProps) {
  const styles = useContextualHelpLabelStyles()
  return (
    <Field {...props}
      label={help ? { children: (_: unknown, labelProps: LabelProps) => (
        <ContextualHelpLabel term={label} labelProps={labelProps}>{help}</ContextualHelpLabel>
      ) } : label}
      hint={help || hint != null ? <>{help && <span className={styles.srOnly}>{help}</span>}{hint}</> : undefined}
    />
  )
}
