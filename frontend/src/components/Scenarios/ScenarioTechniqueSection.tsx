import { useId, useState } from 'react'

import { Badge, Button, Checkbox, Radio, Tag, TagGroup, Text, ToggleButton, mergeClasses } from '@fluentui/react-components'
import { ChevronDownRegular, ChevronRightRegular } from '@fluentui/react-icons'

import ContextualHelpLabel from '@/components/ContextualHelpLabel'
import { HELP_DEFINITIONS } from '@/components/helpDefinitions'
import { IdentityJson } from '@/components/Registry/IdentityDisclosure'
import { useIdentityDisclosureStyles } from '@/components/Registry/IdentityDisclosure.styles'

import { useScenarioDetailStyles } from './ScenarioDetail.styles'
import { exceedsTechniqueLimit, type SelectableTechnique } from './scenarioLaunchForm'
import { techniqueSetName } from './scenarioTechniqueSets'

interface ScenarioTechniqueSectionProps {
  formId: string
  techniques: SelectableTechnique[]
  selectedTechniques: string[]
  supportsTechniques: boolean
  singleTechnique: boolean
  maxConcreteTechniques: number | null
  selectionOverLimit: boolean
  selectionError: string | null
  disabled: boolean
  /** Seeded preset tokens running without a checkbox of their own. */
  tokensWithoutCheckbox: string[]
  isSelected: (technique: SelectableTechnique) => boolean
  onTechniqueChange: (technique: SelectableTechnique, checked: boolean) => void
  onTagChange: (tag: string) => void
  onRemoveToken: (token: string) => void
}

export default function ScenarioTechniqueSection({
  formId,
  techniques,
  selectedTechniques,
  supportsTechniques,
  singleTechnique,
  maxConcreteTechniques,
  selectionOverLimit,
  selectionError,
  disabled,
  tokensWithoutCheckbox,
  isSelected,
  onTechniqueChange,
  onTagChange,
  onRemoveToken,
}: ScenarioTechniqueSectionProps) {
  const styles = useScenarioDetailStyles()
  const identityStyles = useIdentityDisclosureStyles()
  const identityId = useId()
  const techniqueLimitDescriptionId = `${identityId}-technique-limit`
  const baselineUnavailableId = `${identityId}-baseline-unavailable`
  const [expandedIdentities, setExpandedIdentities] = useState<Set<string>>(new Set())
  const toggleIdentity = (name: string): void => {
    setExpandedIdentities((current: Set<string>) => {
      const next = new Set(current)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }
  return (
    <section className={styles.section} aria-labelledby="techniques-section-title">
      <Text id="techniques-section-title" as="h2" aria-label="Techniques" size={400} weight="semibold">
        <ContextualHelpLabel term="Techniques" labelProps={{ weight: 'semibold' }}>
          {HELP_DEFINITIONS.technique}
        </ContextualHelpLabel>
      </Text>
      {supportsTechniques && <Text size={200} className={styles.hint}>
        {singleTechnique
          ? 'Select exactly one technique. Baseline comparison is optional and separate.'
          : 'Select individual techniques, or use a tag to select or clear all techniques with that tag.'}
      </Text>}
      {supportsTechniques && !singleTechnique && maxConcreteTechniques != null && (
        <Text id={techniqueLimitDescriptionId} size={200} className={styles.hint}>
          Select at most {maxConcreteTechniques} concrete techniques.
          {' '}Clear a selected technique or choose a smaller tag.
        </Text>
      )}
      {selectionError && (
        <Text className={styles.errorText} role="alert">
          {selectionError}
        </Text>
      )}
      {supportsTechniques && tokensWithoutCheckbox.length > 0 && (
        <div className={styles.pinnedTokens}>
          <Text size={200} className={styles.hint}>
            Also running, with no checkbox of their own:
          </Text>
          <TagGroup
            aria-label="Techniques without a checkbox"
            data-testid="techniques-without-checkbox"
            onDismiss={(_, data) => onRemoveToken(String(data.value))}
          >
            {tokensWithoutCheckbox.map((token) => (
              <Tag
                key={token}
                className={styles.touchTarget}
                value={token}
                dismissible
                disabled={disabled}
                size="small"
                aria-label={`Remove ${token}`}
              >
                {token}
              </Tag>
            ))}
          </TagGroup>
        </div>
      )}
      <div className={styles.techniqueList} role="group" aria-label="Techniques">
        {techniques.map((technique) => {
          const selected = isSelected(technique)
          const limited = !technique.isBaseline && !selected
            && maxConcreteTechniques != null
            && selectedTechniques.length >= maxConcreteTechniques
          const expanded = expandedIdentities.has(technique.name)
          const regionId = `${identityId}-${encodeURIComponent(technique.name)}`
          return (
            <div className={styles.techniqueOption} key={technique.name}>
            {singleTechnique && !technique.isBaseline ? (
              <Radio
                className={styles.selectionControl}
                name={`${formId}-technique`}
                label={technique.name}
                value={technique.name}
                checked={selected && !selectionOverLimit}
                disabled={disabled || technique.disabled}
                onChange={() => onTechniqueChange(technique, true)}
                data-testid={`technique-${technique.name}`}
              />
            ) : <Checkbox
              className={styles.selectionControl}
              label={technique.name}
              checked={selected}
              disabled={disabled || technique.disabled || limited}
              aria-describedby={technique.disabled ? baselineUnavailableId
                : limited ? techniqueLimitDescriptionId : undefined}
              onChange={(_, data) => onTechniqueChange(technique, data.checked === true)}
              data-testid={technique.isBaseline ? 'baseline-checkbox' : `technique-${technique.name}`}
            />}
            <div className={styles.techniqueDetails}>
              {technique.description && (
                <Text size={200} className={styles.hint}>{technique.description}</Text>
              )}
              {technique.tags.length > 0 && (
                <div className={styles.techniqueTags} aria-label={`${technique.name} tags`}>
                  {technique.tags.map((tag) => {
                    const tagMembers = techniques.filter(
                      (candidate) => !candidate.disabled && candidate.tags.includes(tag),
                    )
                    const tagSelected = tagMembers.length > 0 && tagMembers.every(isSelected)
                    const exceedsLimit = exceedsTechniqueLimit(
                      selectedTechniques,
                      tagMembers.filter((candidate) => !candidate.isBaseline).map((candidate) => candidate.name),
                      maxConcreteTechniques,
                    )
                    if (singleTechnique) {
                      return <Badge key={tag} size="small" appearance="outline">{techniqueSetName(tag)}</Badge>
                    }
                    return (
                      <ToggleButton
                        className={styles.techniqueTag}
                        key={tag}
                        size="small"
                        appearance="outline"
                        checked={tagSelected}
                        disabled={disabled || tagMembers.length === 0 || (!tagSelected && exceedsLimit)}
                        aria-describedby={tagMembers.length === 0 ? baselineUnavailableId
                          : !tagSelected && exceedsLimit ? techniqueLimitDescriptionId : undefined}
                        onClick={() => onTagChange(tag)}
                        aria-label={`${tagSelected ? 'Clear' : 'Select'} ${techniqueSetName(tag)} techniques`}
                      >
                        {techniqueSetName(tag)}
                      </ToggleButton>
                    )
                  })}
                </div>
              )}
              {technique.disabled && (
                <Text id={baselineUnavailableId} size={200} className={styles.hint}>
                  This scenario does not support a baseline comparison.
                </Text>
              )}
            </div>
            <Button type="button" appearance="subtle" className={styles.identityToggle}
              icon={expanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
              aria-label={`Identity for ${technique.name}`} aria-expanded={expanded} aria-controls={regionId}
              onClick={() => toggleIdentity(technique.name)} />
            {expanded && (technique.identifier
              ? <IdentityJson id={regionId} name={technique.name} identity={technique.identifier}
                className={styles.identityRegion} />
              : <div id={regionId} role="region" aria-label={`Identity for ${technique.name}`}
                className={mergeClasses(styles.identityRegion, identityStyles.details)}>
                <Text>{technique.isBaseline ? 'Baseline identity is available after launch.' : 'Identity was not recorded for this technique.'}</Text>
              </div>)}
          </div>
          )
        })}
      </div>
    </section>
  )
}
