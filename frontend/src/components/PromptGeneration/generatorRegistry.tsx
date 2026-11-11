import type { ReactElement } from 'react'

import { MessageBar, MessageBarBody } from '@fluentui/react-components'

import AssertGenerationForm from './AssertGenerationForm'
import AssertGenerationSummary from './AssertGenerationSummary'
import AssertReviewEditor from './AssertReviewEditor'
import type { PromptGenerationFormProps, PromptGenerationReviewProps, PromptGenerationSummaryProps } from './generatorUi'
import TargetObjectiveGenerationForm from './TargetObjectiveGenerationForm'
import TargetObjectiveSummary from './TargetObjectiveSummary'

/** The generators this GUI has views for. */
type SupportedGenerator = 'assert' | 'target_objective'

function supportedGenerator(generator: string): SupportedGenerator | null {
  return generator === 'assert' || generator === 'target_objective' ? generator : null
}

function UnsupportedGenerator({ generator }: { readonly generator: string }) {
  return (
    <MessageBar intent="warning" data-testid="unsupported-generator">
      <MessageBarBody>
        This GUI doesn&apos;t support the &quot;{generator}&quot; generator. The frontend and backend may be out of
        date with each other.
      </MessageBarBody>
    </MessageBar>
  )
}

export function GeneratorFormView(props: PromptGenerationFormProps): ReactElement {
  switch (supportedGenerator(props.descriptor.name)) {
    case 'assert':
      return <AssertGenerationForm {...props} />
    case 'target_objective':
      return <TargetObjectiveGenerationForm {...props} />
    case null:
      return <UnsupportedGenerator generator={props.descriptor.name} />
  }
}

/** The review step; objective generations never pause for review. */
export function GeneratorReviewView(props: PromptGenerationReviewProps): ReactElement | null {
  switch (supportedGenerator(props.generation.generator)) {
    case 'assert':
      return <AssertReviewEditor {...props} />
    case 'target_objective':
      return null
    case null:
      return <UnsupportedGenerator generator={props.generation.generator} />
  }
}

interface GeneratorSummaryViewProps extends PromptGenerationSummaryProps {
  readonly generator: string
}

export function GeneratorSummaryView({ generator, summary }: GeneratorSummaryViewProps): ReactElement {
  switch (supportedGenerator(generator)) {
    case 'assert':
      return <AssertGenerationSummary summary={summary} />
    case 'target_objective':
      return <TargetObjectiveSummary summary={summary} />
    case null:
      return <UnsupportedGenerator generator={generator} />
  }
}
