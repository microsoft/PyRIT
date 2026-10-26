import { Badge, Checkbox, Radio, Text, ToggleButton } from '@fluentui/react-components'

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
  isSelected: (technique: SelectableTechnique) => boolean
  onTechniqueChange: (technique: SelectableTechnique, checked: boolean) => void
  onTagChange: (tag: string) => void
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
  isSelected,
  onTechniqueChange,
  onTagChange,
}: ScenarioTechniqueSectionProps) {
  const styles = useScenarioDetailStyles()
  return (
    <section className={styles.section} aria-labelledby="techniques-section-title">
      <Text id="techniques-section-title" as="h2" size={400} weight="semibold">
        Techniques
      </Text>
      {supportsTechniques && <Text size={200} className={styles.hint}>
        {singleTechnique
          ? 'Select exactly one technique. Baseline comparison is optional and separate.'
          : 'Select individual techniques, or use a tag to select or clear all techniques with that tag.'}
      </Text>}
      {selectionError && (
        <Text className={styles.errorText} role="alert">
          {selectionError}
        </Text>
      )}
      <div className={styles.techniqueList} role="group" aria-label="Techniques">
        {techniques.map((technique) => {
          const selected = isSelected(technique)
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
              disabled={disabled || technique.disabled || (!technique.isBaseline && !selected
                && maxConcreteTechniques != null
                && selectedTechniques.length >= maxConcreteTechniques)}
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
                <Text size={200} className={styles.hint}>
                  This scenario does not support a baseline comparison.
                </Text>
              )}
            </div>
          </div>
          )
        })}
      </div>
    </section>
  )
}
