import { type ReactNode, useId } from 'react'

import { Button, MessageBar, MessageBarBody, Spinner, Text } from '@fluentui/react-components'
import { ArrowLeftRegular, ArrowSyncRegular } from '@fluentui/react-icons'
import { Link, useSearchParams } from 'react-router'

import { usePromptGenerationSeed } from '@/hooks/usePromptGenerationSeed'
import { usePromptGenerators } from '@/hooks/usePromptGenerators'
import type { TargetInstance } from '@/types'

import { GeneratorFormView } from './generatorRegistry'
import {
  newGenerationParamsFromSearch,
  newPromptGenerationRoutePath,
  PROMPT_GENERATIONS_PATH,
  type NewGenerationParams,
} from './promptGenerationRoutes'
import { usePromptGenerationStyles } from './PromptGeneration.styles'

interface NewPromptGenerationPageProps {
  readonly targets: TargetInstance[]
  readonly targetsLoading: boolean
  readonly targetsError: string | null
  /** The default adversarial target, pre-selected for every target role unless the source recorded targets. */
  readonly defaultTarget: TargetInstance | null
}

export default function NewPromptGenerationPage(props: NewPromptGenerationPageProps) {
  const [searchParams] = useSearchParams()
  const params = newGenerationParamsFromSearch(searchParams)
  // A new generator, template, or source link starts a fresh form.
  return <NewPromptGenerationContent key={JSON.stringify(params)} params={params} {...props} />
}

interface NewPromptGenerationContentProps extends NewPromptGenerationPageProps {
  readonly params: NewGenerationParams
}

function NewPromptGenerationContent({
  params,
  targets,
  targetsLoading,
  targetsError,
  defaultTarget,
}: NewPromptGenerationContentProps) {
  const styles = usePromptGenerationStyles()
  const titleId = useId()
  const generators = usePromptGenerators()
  const source = usePromptGenerationSeed(params)
  const generatorName = params.generator ?? source.generator
  const descriptor = generators.generators.find((item) => item.name === generatorName)

  const frame = (title: string, children: ReactNode, description?: string) => (
    <section className={styles.root} aria-labelledby={titleId} data-testid="new-prompt-generation-page">
      <div className={styles.content}>
        <Link to={PROMPT_GENERATIONS_PATH} className={styles.backLink}>
          <ArrowLeftRegular /> Back to generations
        </Link>
        <div className={styles.headerIdentity}>
          <Text id={titleId} as="h1" size={600} weight="semibold">{title}</Text>
          {description && <Text className={styles.subtitle}>{description}</Text>}
        </div>
        {children}
      </div>
    </section>
  )

  if (generators.loading || source.loading) {
    return frame('New generation', <Spinner label="Loading generators..." />)
  }
  if (generators.error) {
    return frame('New generation', (
      <MessageBar intent="error">
        <MessageBarBody>
          Generators could not be loaded: {generators.error}
          <Button className={styles.touchTarget} icon={<ArrowSyncRegular />} onClick={generators.retry}>Retry</Button>
        </MessageBarBody>
      </MessageBar>
    ))
  }
  if (source.error) {
    return frame('New generation', (
      <MessageBar intent="error">
        <MessageBarBody>
          The form could not be pre-filled: {source.error}
          <Button className={styles.touchTarget} icon={<ArrowSyncRegular />} onClick={source.retry}>Retry</Button>
        </MessageBarBody>
      </MessageBar>
    ))
  }
  if (params.generator && source.generator && params.generator !== source.generator) {
    return frame('New generation', (
      <MessageBar intent="error">
        <MessageBarBody>
          The source was made by the {source.generator} generator, not {params.generator}.
        </MessageBarBody>
      </MessageBar>
    ))
  }
  if (!generatorName) {
    return frame('New generation', (
      <ul className={styles.generatorList} aria-label="Generators">
        {generators.generators.map((generator) => (
          <li key={generator.name}>
            <article className={styles.generatorCard} aria-label={generator.display_name}>
              <Text as="h2" size={500} weight="semibold">{generator.display_name}</Text>
              <Text className={styles.muted}>{generator.description}</Text>
              <Text size={200} className={styles.muted}>
                {generator.stages.map((stage) => stage.label).join(' → ')}
              </Text>
              <Link
                className={styles.primaryLink}
                to={newPromptGenerationRoutePath({ generator: generator.name })}
                aria-label={`Start ${generator.display_name}`}
              >
                Start
              </Link>
            </article>
          </li>
        ))}
      </ul>
    ), 'Choose a generator. Each one writes a stored seed dataset from what you describe.')
  }
  if (!descriptor) {
    return frame('New generation', (
      <MessageBar intent="error">
        <MessageBarBody>
          No generator is named &quot;{generatorName}&quot;.{' '}
          <Link to={newPromptGenerationRoutePath()}>Choose a generator</Link>
        </MessageBarBody>
      </MessageBar>
    ))
  }

  return frame(`New ${descriptor.display_name}`, (
    <>
      {source.sourceDescription && (
        <MessageBar intent="info" data-testid="generation-seed-source">
          <MessageBarBody>{source.sourceDescription}</MessageBarBody>
        </MessageBar>
      )}
      <GeneratorFormView
        descriptor={descriptor}
        targets={targets}
        targetsLoading={targetsLoading}
        targetsError={targetsError}
        defaultTarget={defaultTarget}
        seed={source.seed}
        metadataRefreshing={generators.refreshing}
      />
    </>
  ), descriptor.description)
}
