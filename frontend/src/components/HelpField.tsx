import type { ReactNode } from 'react'

import { Field, type FieldProps, type LabelProps } from '@fluentui/react-components'

import ContextualHelpLabel from './ContextualHelpLabel'
import { useContextualHelpLabelStyles } from './ContextualHelpLabel.styles'

interface HelpFieldProps extends Omit<FieldProps, 'label' | 'hint' | 'validationMessage' | 'validationState'> {
  readonly label: string
  readonly help?: string | undefined
  readonly hint?: ReactNode
  readonly validationMessage?: FieldProps['validationMessage']
  readonly validationState?: FieldProps['validationState']
}

/** Keeps the field description available to assistive technology even when its help is closed. */
export default function HelpField({ label, help, hint, validationMessage, validationState, ...props }: HelpFieldProps) {
  const styles = useContextualHelpLabelStyles()
  const hintContent = help || hint != null ? <>{help && <span className={styles.srOnly}>{help}</span>}{hint}</> : undefined
  return (
    <Field
      {...props}
      label={help ? { children: (_: unknown, labelProps: LabelProps) => (
        <ContextualHelpLabel term={label} labelProps={labelProps}>{help}</ContextualHelpLabel>
      ) } : label}
      {...(hintContent !== undefined ? { hint: hintContent } : {})}
      {...(validationMessage !== undefined ? { validationMessage } : {})}
      {...(validationState !== undefined ? { validationState } : {})}
    />
  )
}
