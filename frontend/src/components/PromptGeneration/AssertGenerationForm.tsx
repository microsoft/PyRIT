import { useId, useState } from 'react'

import {
  Accordion,
  AccordionHeader,
  AccordionItem,
  AccordionPanel,
  Badge,
  Button,
  Checkbox,
  Combobox,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Option,
  Radio,
  RadioGroup,
  Select,
  Spinner,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
  Textarea,
} from '@fluentui/react-components'
import { DismissRegular } from '@fluentui/react-icons'

import SearchableMultiCombobox from '@/components/SearchableMultiCombobox'
import { type AssertLibraryState, useAssertLibrary } from '@/hooks/useAssertLibrary'
import { useGenerationTargetSelection } from '@/hooks/useGenerationTargetSelection'
import { usePromptGenerationPreflight } from '@/hooks/usePromptGenerationPreflight'
import { type PromptGenerationSubmission, usePromptGenerationSubmit } from '@/hooks/usePromptGenerationSubmit'
import type {
  AssertBehaviorPreset,
  AssertSuitePreset,
  PromptGenerationBatchItem,
  PromptGenerationCreateRequest,
  TargetInstance,
} from '@/types'

import AssertDimensionsEditor from './AssertDimensionsEditor'
import {
  ASSERT_SAMPLING_LABELS,
  ASSERT_SAMPLING_METHODS,
  assertBehaviorErrors,
  assertDraftFromRequest,
  assertSettingsErrors,
  buildAssertRequest,
  defaultAssertSettings,
  stratifyOptions,
  suiteDatasetName,
  type AssertBehaviorDraft,
  type AssertSettingsDraft,
} from './assertRequestDraft'
import GenerationCommonFields from './GenerationCommonFields'
import GenerationFormFooter from './GenerationFormFooter'
import {
  preflightProblems,
  targetSelectionErrors,
  type TargetRoleFilter,
} from './generationFormHelpers'
import type { PromptGenerationFormProps } from './generatorUi'
import { datasetNameError, datasetNameFromText } from './promptGenerationPresentation'
import { usePromptGenerationStyles } from './PromptGeneration.styles'

type AssertFormMode = 'single' | 'suite'

const RESEARCH_ROLE = 'research'
const GENERATION_ROLE = 'generation'
const MAX_SUITE_DATASETS = 100
const MAX_GROUP_LABEL_LENGTH = 200
const WEB_SEARCH_FILTER_REASON = 'Web search is on, so only targets that support web search are listed. '
  + 'Leave this empty to research with the generation target, which must then support web search.'

const WEB_SEARCH_FILTER: TargetRoleFilter = {
  allows: (target: TargetInstance) => target.capabilities?.supports_web_search === true,
  reason: WEB_SEARCH_FILTER_REASON,
}

/** Turns a written policy into a behavior taxonomy and seed groups, or a suite of them. */
export default function AssertGenerationForm(props: PromptGenerationFormProps) {
  const library = useAssertLibrary()
  if (library.loading) return <Spinner label="Loading ASSERT templates..." />
  return <AssertGenerationFormContent {...props} library={library} />
}

interface AssertGenerationFormContentProps extends PromptGenerationFormProps {
  readonly library: AssertLibraryState
}

interface InitialAssertForm {
  readonly mode: AssertFormMode
  readonly behavior: AssertBehaviorDraft
  readonly templateName: string
  readonly settings: AssertSettingsDraft
  readonly suite: AssertSuitePreset | undefined
}

function initialAssertForm(
  seed: PromptGenerationFormProps['seed'],
  library: AssertLibraryState,
): InitialAssertForm {
  const parsed = seed.request ? assertDraftFromRequest(seed.request) : null
  const template = seed.template ? library.behaviors.find((item) => item.name === seed.template) : undefined
  const suite = seed.suite && !seed.request ? library.suites.find((item) => item.name === seed.suite) : undefined
  const settings = parsed?.settings ?? defaultAssertSettings()
  return {
    mode: suite ? 'suite' : 'single',
    behavior: parsed?.behavior
      ?? (template ? { behaviorName: template.name, behaviorDescription: template.description } : { behaviorName: '', behaviorDescription: '' }),
    templateName: template?.name ?? '',
    settings: suite ? { ...settings, context: suite.context } : settings,
    suite,
  }
}

function TemplateDetails({ preset }: { readonly preset: AssertBehaviorPreset | AssertSuitePreset }) {
  const styles = usePromptGenerationStyles()
  return (
    <div className={styles.cellStack} data-testid="assert-template-details">
      <Text size={200}>{preset.summary}</Text>
      {(preset.tags.length > 0 || preset.applicable_to.length > 0) && (
        <div className={styles.badges}>
          {preset.tags.map((tag) => <Badge key={`tag:${tag}`} appearance="outline">{tag}</Badge>)}
          {preset.applicable_to.map((item) => <Badge key={`for:${item}`} appearance="tint" color="informative">{item}</Badge>)}
        </div>
      )}
    </div>
  )
}

function AssertGenerationFormContent({
  descriptor,
  targets,
  targetsLoading,
  targetsError,
  defaultTarget,
  seed,
  metadataRefreshing = false,
  library,
}: AssertGenerationFormContentProps) {
  const styles = usePromptGenerationStyles()
  const stratifyLabelId = useId()
  const [initial] = useState(() => initialAssertForm(seed, library))
  const [mode, setMode] = useState<AssertFormMode>(initial.mode)
  const [behavior, setBehavior] = useState<AssertBehaviorDraft>(initial.behavior)
  const [templateName, setTemplateName] = useState(initial.templateName)
  const [templateSearch, setTemplateSearch] = useState(initial.templateName)
  const [settings, setSettings] = useState<AssertSettingsDraft>(initial.settings)
  const [datasetName, setDatasetName] = useState(seed.datasetName ?? '')
  const [datasetNameTouched, setDatasetNameTouched] = useState(seed.datasetName !== undefined)
  const [suiteName, setSuiteName] = useState(initial.suite?.name ?? '')
  const [suiteLabel, setSuiteLabel] = useState(initial.suite?.name ?? '')
  const [suiteBehaviors, setSuiteBehaviors] = useState<string[]>(
    () => (initial.suite?.behaviors ?? []).filter((name) => library.behaviors.some((item) => item.name === name)),
  )
  const [suiteDatasetNames, setSuiteDatasetNames] = useState<Record<string, string>>({})
  const { selection: selectedTargets, choose: setSelectedTargets } = useGenerationTargetSelection(
    descriptor, seed.targets, defaultTarget,
  )
  const [pauseForReview, setPauseForReview] = useState(seed.pauseForReview ?? false)
  const { submitting, error: submitError, submit } = usePromptGenerationSubmit()

  const updateSettings = (changes: Partial<AssertSettingsDraft>): void => {
    setSettings((previous) => ({ ...previous, ...changes }))
  }

  const behaviorPresets = new Map(library.behaviors.map((item) => [item.name, item]))
  const selectedTemplate = templateName ? behaviorPresets.get(templateName) : undefined
  const selectedSuite = library.suites.find((item) => item.name === suiteName)
  const missingTemplate = seed.template && !library.error && !behaviorPresets.has(seed.template) ? seed.template : null
  const missingSuite = seed.suite && !library.error && !library.suites.some((item) => item.name === seed.suite)
    ? seed.suite : null
  const singleDatasetName = datasetNameTouched ? datasetName : datasetNameFromText(behavior.behaviorName)
  // Default dataset names follow the label, so a renamed run of the same suite gets new names.
  const suitePrefix = suiteLabel
  const suiteItemName = (behaviorName: string): string =>
    suiteDatasetNames[behaviorName] ?? suiteDatasetName(suitePrefix, behaviorName)
  const targetFilters: Record<string, TargetRoleFilter> = settings.webSearch
    && descriptor.target_roles.some((role) => role.role === RESEARCH_ROLE)
    ? { [RESEARCH_ROLE]: WEB_SEARCH_FILTER }
    : {}

  const clientErrors: string[] = []
  if (mode === 'single') {
    clientErrors.push(...assertBehaviorErrors(behavior))
  } else {
    if (!suiteLabel.trim()) clientErrors.push('Enter a suite label.')
    if (suiteLabel.length > MAX_GROUP_LABEL_LENGTH) clientErrors.push(`Suite labels are at most ${MAX_GROUP_LABEL_LENGTH} characters.`)
    if (suiteBehaviors.length === 0) clientErrors.push('Choose at least one behavior for the suite.')
    if (suiteBehaviors.length > MAX_SUITE_DATASETS) clientErrors.push(`A suite can generate at most ${MAX_SUITE_DATASETS} datasets.`)
  }
  clientErrors.push(...assertSettingsErrors(settings))
  if (mode === 'single') {
    const nameError = datasetNameError(singleDatasetName)
    if (nameError) clientErrors.push(nameError)
  } else {
    const names = suiteBehaviors.map(suiteItemName)
    for (const [index, name] of names.entries()) {
      const nameError = datasetNameError(name)
      if (nameError) clientErrors.push(`${suiteBehaviors[index]}: ${nameError}`)
    }
    if (new Set(names).size !== names.length) clientErrors.push('Each behavior in the suite needs a different dataset name.')
  }
  clientErrors.push(...targetSelectionErrors(descriptor.target_roles, targets, selectedTargets, targetFilters))
  const generationTarget = targets.find((target) => target.target_registry_name === selectedTargets[GENERATION_ROLE])
  if (settings.webSearch && !selectedTargets[RESEARCH_ROLE] && generationTarget?.capabilities?.supports_web_search === false) {
    clientErrors.push('Web search is on: choose a research target that supports web search, or a generation target that does.')
  }

  let submission: PromptGenerationSubmission | null = null
  if (clientErrors.length === 0) {
    if (mode === 'single') {
      submission = { kind: 'single', datasetName: singleDatasetName, request: { ...buildAssertRequest(settings, behavior) } }
    } else {
      const items: PromptGenerationBatchItem[] = suiteBehaviors.map((name) => ({
        dataset_name: suiteItemName(name),
        request: {
          ...buildAssertRequest(settings, {
            behaviorName: name,
            behaviorDescription: behaviorPresets.get(name)?.description ?? '',
          }),
        },
      }))
      submission = { kind: 'batch', label: suiteLabel.trim(), items }
    }
  }
  const datasetNames = submission === null ? []
    : submission.kind === 'single' ? [submission.datasetName] : submission.items.map((item) => item.dataset_name)
  const preflightRequests: PromptGenerationCreateRequest[] | null = submission === null ? null
    : (submission.kind === 'single'
      ? [{ dataset_name: submission.datasetName, request: submission.request }]
      : submission.items.map((item) => ({ dataset_name: item.dataset_name, request: item.request })))
      .map((item) => ({
        generator: descriptor.name,
        dataset_name: item.dataset_name,
        request: item.request,
        targets: selectedTargets,
        pause_for_review: pauseForReview,
      }))
  const preflight = usePromptGenerationPreflight(preflightRequests)
  const problems = preflightProblems(preflight.results, datasetNames)

  const handleSubmit = (): void => {
    if (!submission) return
    void submit({ generator: descriptor.name, submission, targets: selectedTargets, pauseForReview })
  }

  const applyTemplate = (name: string): void => {
    const preset = behaviorPresets.get(name)
    if (!preset) return
    setTemplateName(preset.name)
    setTemplateSearch(preset.name)
    setBehavior({ behaviorName: preset.name, behaviorDescription: preset.description })
  }

  const applySuite = (name: string): void => {
    setSuiteName(name)
    setSuiteDatasetNames({})
    const suite = library.suites.find((item) => item.name === name)
    if (!suite) return
    setSuiteLabel(suite.name)
    setSuiteBehaviors(suite.behaviors.filter((behaviorName) => behaviorPresets.has(behaviorName)))
    updateSettings({ context: suite.context })
  }

  const templateQuery = templateSearch.trim().toLowerCase()
  const templateOptions = templateQuery && templateQuery !== templateName.toLowerCase()
    ? library.behaviors.filter((item) => item.name.toLowerCase().includes(templateQuery)
      || item.summary.toLowerCase().includes(templateQuery))
    : library.behaviors

  const contextField = (
    <Field
      label="Application context"
      hint="Optional. Describe the system under test, such as its users, tools, and domain."
    >
      <Textarea
        className={styles.wideControl}
        resize="vertical"
        rows={4}
        value={settings.context}
        disabled={submitting}
        onChange={(_, data) => updateSettings({ context: data.value })}
        data-testid="assert-context"
      />
    </Field>
  )

  return (
    <form
      className={styles.form}
      aria-label={`${descriptor.display_name} settings`}
      noValidate
      onSubmit={(event) => { event.preventDefault() }}
      data-testid="assert-generation-form"
    >
      {library.error && (
        <MessageBar intent="warning">
          <MessageBarBody>ASSERT templates could not be loaded: {library.error} You can still write a policy.</MessageBarBody>
        </MessageBar>
      )}
      {missingTemplate && (
        <MessageBar intent="warning">
          <MessageBarBody>No ASSERT behavior template is named &quot;{missingTemplate}&quot;.</MessageBarBody>
        </MessageBar>
      )}
      {missingSuite && (
        <MessageBar intent="warning">
          <MessageBarBody>No ASSERT suite is named &quot;{missingSuite}&quot;.</MessageBarBody>
        </MessageBar>
      )}

      {!seed.request && (
        <Field label="What to generate">
          <RadioGroup
            layout="horizontal"
            value={mode}
            disabled={submitting}
            onChange={(_, data) => { if (data.value === 'single' || data.value === 'suite') setMode(data.value) }}
            data-testid="assert-mode"
          >
            <Radio value="single" label="One policy" />
            <Radio value="suite" label="A suite: one dataset per behavior" />
          </RadioGroup>
        </Field>
      )}

      {mode === 'single' ? (
        <section className={styles.section} aria-labelledby="assert-policy-title">
          <Text id="assert-policy-title" as="h2" size={400} weight="semibold">Policy</Text>
          {library.behaviors.length > 0 && (
            <Field label="Start from template" hint="Fills the behavior name and policy text from the ASSERT behavior library.">
              <Combobox
                className={styles.control}
                placeholder="Search behavior templates"
                value={templateSearch}
                selectedOptions={templateName ? [templateName] : []}
                disabled={submitting}
                onChange={(event) => setTemplateSearch(event.target.value)}
                onOptionSelect={(_, data) => { if (data.optionValue) applyTemplate(data.optionValue) }}
                data-testid="assert-template-picker"
              >
                {templateOptions.map((item) => (
                  <Option key={item.name} value={item.name} text={item.name}>
                    <div className={styles.cellStack}>
                      <Text weight="semibold">{item.name}</Text>
                      <Text size={200} className={styles.muted}>{item.summary}</Text>
                    </div>
                  </Option>
                ))}
              </Combobox>
            </Field>
          )}
          {selectedTemplate && <TemplateDetails preset={selectedTemplate} />}
          <Field label="Behavior name" required hint="A short identifier, such as private_order_disclosure.">
            <Input
              className={styles.control}
              value={behavior.behaviorName}
              disabled={submitting}
              onChange={(_, data) => setBehavior((previous) => ({ ...previous, behaviorName: data.value }))}
              data-testid="assert-behavior-name"
            />
          </Field>
          <Field
            label="Policy text"
            required
            hint="What the system should and should not do. Markdown is fine."
          >
            <Textarea
              className={styles.wideControl}
              resize="vertical"
              rows={8}
              value={behavior.behaviorDescription}
              disabled={submitting}
              onChange={(_, data) => setBehavior((previous) => ({ ...previous, behaviorDescription: data.value }))}
              data-testid="assert-policy-text"
            />
          </Field>
          {contextField}
        </section>
      ) : (
        <section className={styles.section} aria-labelledby="assert-suite-title" data-testid="assert-suite">
          <Text id="assert-suite-title" as="h2" size={400} weight="semibold">Suite</Text>
          <Field label="Suite template" hint="Picks behaviors and context for an application. Choose behaviors yourself with a custom selection.">
            <Select
              className={styles.control}
              value={suiteName}
              disabled={submitting}
              onChange={(_, data) => applySuite(data.value)}
              data-testid="assert-suite-picker"
            >
              <option value="">Custom selection</option>
              {library.suites.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
            </Select>
          </Field>
          {selectedSuite && <TemplateDetails preset={selectedSuite} />}
          <Field label="Suite label" required hint="Names the group of generations.">
            <Input
              className={styles.control}
              value={suiteLabel}
              disabled={submitting}
              onChange={(_, data) => setSuiteLabel(data.value)}
              data-testid="assert-suite-label"
            />
          </Field>
          <Field label="Behaviors">
            <SearchableMultiCombobox
              className={styles.control}
              ariaLabel="Behaviors"
              placeholder="Choose behaviors"
              summaryPrefix="Behaviors"
              options={library.behaviors.map((item) => ({ value: item.name, label: item.name }))}
              selectedOptions={suiteBehaviors}
              onSelect={setSuiteBehaviors}
              testId="assert-suite-behaviors"
            />
          </Field>
          {suiteBehaviors.length > 0 && (
            <div className={styles.tableScroll}>
              <Table aria-label="Suite datasets">
                <TableHeader>
                  <TableRow>
                    <TableHeaderCell>Behavior</TableHeaderCell>
                    <TableHeaderCell>Dataset name</TableHeaderCell>
                    <TableHeaderCell aria-label="Remove behavior" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {suiteBehaviors.map((name) => (
                    <TableRow key={name} data-testid={`assert-suite-row-${name}`}>
                      <TableCell>
                        <div className={styles.cellStack}>
                          <Text weight="semibold">{name}</Text>
                          <Text size={200} className={styles.muted}>{behaviorPresets.get(name)?.summary}</Text>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Input
                          aria-label={`Dataset name for ${name}`}
                          value={suiteItemName(name)}
                          disabled={submitting}
                          onChange={(_, data) => setSuiteDatasetNames((previous) => ({ ...previous, [name]: data.value }))}
                        />
                      </TableCell>
                      <TableCell>
                        <Button
                          className={styles.touchTarget}
                          appearance="subtle"
                          icon={<DismissRegular />}
                          aria-label={`Remove ${name}`}
                          disabled={submitting}
                          onClick={() => setSuiteBehaviors((previous) => previous.filter((item) => item !== name))}
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
          {contextField}
        </section>
      )}

      <section className={styles.section} aria-labelledby="assert-generation-title">
        <Text id="assert-generation-title" as="h2" size={400} weight="semibold">Seed groups</Text>
        <div className={styles.fieldGrid}>
          <Field label="Seed groups" required hint="How many seed groups each dataset holds.">
            <Input
              className={styles.control}
              type="number"
              min={1}
              value={settings.groupCount}
              disabled={submitting}
              onChange={(_, data) => updateSettings({ groupCount: data.value })}
              data-testid="assert-group-count"
            />
          </Field>
          <Field label="Suggested categories" hint="A hint for the taxonomy size, not a quota.">
            <Input
              className={styles.control}
              type="number"
              min={1}
              max={1000}
              value={settings.behaviorCategoryCount}
              disabled={submitting}
              onChange={(_, data) => updateSettings({ behaviorCategoryCount: data.value })}
              data-testid="assert-category-count"
            />
          </Field>
        </div>
        <Checkbox
          checked={settings.includeUserPrompt}
          disabled={submitting}
          onChange={(_, data) => updateSettings({ includeUserPrompt: data.checked === true })}
          label="Include a user prompt in each seed group"
        />
        <Checkbox
          checked={settings.includeAdversarialPrompt}
          disabled={submitting}
          onChange={(_, data) => updateSettings({ includeAdversarialPrompt: data.checked === true })}
          label="Include an adversarial prompt (needed for the multi-turn technique)"
        />
      </section>

      <AssertDimensionsEditor settings={settings} onChange={updateSettings} disabled={submitting} />

      <Accordion collapsible>
        <AccordionItem value="advanced">
          <AccordionHeader data-testid="assert-advanced-toggle">Advanced settings</AccordionHeader>
          <AccordionPanel>
            <div className={styles.section}>
              <div className={styles.fieldGrid}>
                <Field label="Sampling method" hint="How dimension levels are combined across seed groups.">
                  <Select
                    className={styles.control}
                    value={settings.samplingMethod}
                    disabled={submitting}
                    onChange={(_, data) => {
                      const method = ASSERT_SAMPLING_METHODS.find((item) => item === data.value)
                      if (method) updateSettings({ samplingMethod: method })
                    }}
                    data-testid="assert-sampling-method"
                  >
                    {ASSERT_SAMPLING_METHODS.map((method) => (
                      <option key={method} value={method}>{ASSERT_SAMPLING_LABELS[method]}</option>
                    ))}
                  </Select>
                </Field>
                <Field label="Random seed" hint="The same seed and inputs choose the same assignments.">
                  <Input
                    className={styles.control}
                    type="number"
                    value={settings.randomSeed}
                    disabled={submitting}
                    onChange={(_, data) => updateSettings({ randomSeed: data.value })}
                    data-testid="assert-random-seed"
                  />
                </Field>
              </div>
              {settings.samplingMethod === 'stratified' && (
                <div role="group" aria-labelledby={stratifyLabelId} className={styles.cellStack}>
                  <Text id={stratifyLabelId} weight="semibold">Stratify by</Text>
                  <div className={styles.actions}>
                    {stratifyOptions(settings).map((name) => (
                      <Checkbox
                        key={name}
                        label={name}
                        checked={settings.stratifyBy.includes(name)}
                        disabled={submitting}
                        onChange={(_, data) => updateSettings({
                          stratifyBy: data.checked === true
                            ? [...settings.stratifyBy, name]
                            : settings.stratifyBy.filter((item) => item !== name),
                        })}
                      />
                    ))}
                  </div>
                </div>
              )}
              {settings.samplingMethod === 'full_factorial' && (
                <Field label="Replication">
                  <RadioGroup
                    layout="horizontal"
                    value={settings.replication}
                    disabled={submitting}
                    onChange={(_, data) => updateSettings({ replication: data.value === 'none' ? 'none' : 'balanced' })}
                  >
                    <Radio value="balanced" label="Balanced" />
                    <Radio value="none" label="None" />
                  </RadioGroup>
                </Field>
              )}
              {settings.samplingMethod === 'random' && (
                <Checkbox
                  checked={settings.withReplacement}
                  disabled={submitting}
                  onChange={(_, data) => updateSettings({ withReplacement: data.checked === true })}
                  label="Sample with replacement"
                />
              )}
              <Switch
                checked={settings.webSearch}
                disabled={submitting}
                onChange={(_, data) => updateSettings({ webSearch: data.checked })}
                label="Research with web search"
                data-testid="assert-web-search"
              />
              {settings.webSearch && (
                <Text size={200} className={styles.muted}>
                  Web search runs on the research target, or on the generation target when no research target is chosen.
                </Text>
              )}
              <Field label="Target instructions" hint="The system prompt stored with each seed group for the target under test.">
                <RadioGroup
                  value={settings.instructionMode}
                  disabled={submitting}
                  onChange={(_, data) => {
                    if (data.value === 'default' || data.value === 'custom' || data.value === 'target') {
                      updateSettings({ instructionMode: data.value })
                    }
                  }}
                  data-testid="assert-instruction-mode"
                >
                  <Radio value="default" label="Let the generator decide" />
                  <Radio value="custom" label="Use this system prompt" />
                  <Radio value="target" label="The target owns its instructions (no system prompt)" />
                </RadioGroup>
              </Field>
              {settings.instructionMode === 'custom' && (
                <Field label="Target system prompt" required>
                  <Textarea
                    className={styles.wideControl}
                    resize="vertical"
                    rows={4}
                    value={settings.targetSystemPrompt}
                    disabled={submitting}
                    onChange={(_, data) => updateSettings({ targetSystemPrompt: data.value })}
                    data-testid="assert-target-system-prompt"
                  />
                </Field>
              )}
            </div>
          </AccordionPanel>
        </AccordionItem>
      </Accordion>

      <GenerationCommonFields
        descriptor={descriptor}
        datasetName={mode === 'single' ? {
          value: singleDatasetName,
          onChange: (value: string) => {
            setDatasetName(value)
            setDatasetNameTouched(true)
          },
          error: singleDatasetName ? datasetNameError(singleDatasetName) : null,
        } : undefined}
        targets={targets}
        targetsLoading={targetsLoading}
        targetsError={targetsError}
        selectedTargets={selectedTargets}
        onTargetsChange={setSelectedTargets}
        targetFilters={targetFilters}
        pauseForReview={pauseForReview}
        onPauseForReviewChange={setPauseForReview}
        disabled={submitting}
      />

      <GenerationFormFooter
        descriptor={descriptor}
        clientErrors={clientErrors}
        preflight={preflight}
        problems={problems}
        datasetCount={mode === 'suite' ? Math.max(suiteBehaviors.length, 1) : 1}
        submitting={submitting}
        submitError={submitError}
        metadataRefreshing={metadataRefreshing}
        onGenerate={handleSubmit}
      />
    </form>
  )
}
