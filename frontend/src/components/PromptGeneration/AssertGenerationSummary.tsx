import {
  Badge,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
} from '@fluentui/react-components'

import { parseAssertSummary } from './assertReview'
import GenerationSummaryFacts, { type GenerationSummaryFact } from './GenerationSummaryFacts'
import type { PromptGenerationSummaryProps } from './generatorUi'
import { formatCount } from './promptGenerationPresentation'
import { usePromptGenerationStyles } from './PromptGeneration.styles'

function ratio(covered: number, total: number): string {
  return total > 0 ? `${formatCount(covered)} of ${formatCount(total)}` : 'None'
}

/** Seed groups, dimension coverage, and groups per behavior category. */
export default function AssertGenerationSummary({ summary }: PromptGenerationSummaryProps) {
  const styles = usePromptGenerationStyles()
  const result = parseAssertSummary(summary)
  const permissible = result.categories.filter((category) => category.permissible).length
  const facts: GenerationSummaryFact[] = [
    { label: 'Seed groups', value: formatCount(result.group_count) },
    {
      label: 'Categories',
      value: formatCount(result.categories.length),
      detail: `${permissible} permissible, ${result.categories.length - permissible} impermissible`,
    },
    ...(result.coverage ? [
      { label: 'Level pairs covered', value: ratio(result.coverage.covered_pairs, result.coverage.total_pairs) },
      { label: 'Levels covered', value: ratio(result.coverage.covered_levels, result.coverage.total_levels) },
      { label: 'Unique assignments', value: formatCount(result.coverage.unique_assignments) },
    ] : []),
  ]

  return (
    <div className={styles.form} data-testid="assert-generation-summary">
      <GenerationSummaryFacts label="Generation results" facts={facts} />
      {result.edited_stages.length > 0 && (
        <Text size={200} className={styles.muted}>Reviewed and edited: {result.edited_stages.join(', ')}</Text>
      )}
      {result.categories.length > 0 && (
        <div className={styles.tableScroll}>
          <Table aria-label="Seed groups per category">
            <TableHeader>
              <TableRow>
                <TableHeaderCell>Category</TableHeaderCell>
                <TableHeaderCell>Policy</TableHeaderCell>
                <TableHeaderCell>Seed groups</TableHeaderCell>
              </TableRow>
            </TableHeader>
            <TableBody>
              {result.categories.map((category) => (
                <TableRow key={category.name}>
                  <TableCell>{category.name}</TableCell>
                  <TableCell>
                    <Badge appearance="tint" color={category.permissible ? 'success' : 'danger'}>
                      {category.permissible ? 'Permissible' : 'Impermissible'}
                    </Badge>
                  </TableCell>
                  <TableCell>{formatCount(category.groups)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  )
}
