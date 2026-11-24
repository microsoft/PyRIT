import { type ReactNode, useCallback, useEffect, useId, useRef, useState } from 'react'

import {
  Button,
  mergeClasses,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
} from '@fluentui/react-components'
import { ChevronDownRegular, ChevronUpRegular } from '@fluentui/react-icons'
import { Link } from 'react-router'

import type { ScenarioDatasetSizeEstimate, ScenarioDatasetSummary } from '@/types'

import { SCANNER_CELL_PADDING_CLASS, useScenarioCatalogStyles } from './ScenarioCatalog.styles'

const DESCRIPTION_OVERFLOW_TOLERANCE_PX = 2

/** Column headings, repeated as labels when rows stack on narrow screens. */
export interface ScannerColumnLabels {
  readonly scenario: string
  readonly datasets: string
  readonly techniques: string
  readonly runSize: string
}


function formatObjectiveCount(value: number): string {
  return `${value.toLocaleString()} objective${value === 1 ? '' : 's'}`
}

function formatSizeBound(size: ScenarioDatasetSizeEstimate): string {
  switch (size.kind) {
    case 'bounded':
      return `Up to ${formatObjectiveCount(size.value)}`
    case 'all_available':
      return 'All available data (configured child limits still apply)'
    case 'indeterminate':
      return size.detail
  }
}

interface DatasetSummaryProps {
  datasets: ScenarioDatasetSummary[]
  declaredDatasets: string[]
  /** Null while the size is unknown, which omits the size line. */
  datasetSize: ScenarioDatasetSizeEstimate | null
}

export function DatasetSummary({ datasets, declaredDatasets, datasetSize }: DatasetSummaryProps) {
  const styles = useScenarioCatalogStyles()

  if (datasets.length === 0 && declaredDatasets.length === 0) {
    return <Text weight="semibold">No default datasets</Text>
  }

  const datasetNames = declaredDatasets.length > 0
    ? declaredDatasets
    : datasets.map((dataset) => dataset.name)

  return (
    <div className={styles.compactStack}>
      {datasetSize && <Text weight="semibold">{formatSizeBound(datasetSize)}</Text>}
      <Text size={200} className={styles.secondaryText}>{datasetNames.join(' · ')}</Text>
    </div>
  )
}

interface CollapsibleContentProps {
  children: ReactNode
  collapsedClassName: string
  contentId: string
  contentKey: string
  expanded: boolean
  onClippedChange: (clipped: boolean) => void
}

function CollapsibleContent({
  children,
  collapsedClassName,
  contentId,
  contentKey,
  expanded,
  onClippedChange,
}: CollapsibleContentProps) {
  const contentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const content = contentRef.current
    if (!content || expanded) {
      return
    }

    const updateClippedState = () => {
      onClippedChange(content.scrollHeight - content.clientHeight > DESCRIPTION_OVERFLOW_TOLERANCE_PX)
    }
    updateClippedState()

    const resizeObserver = new ResizeObserver(updateClippedState)
    resizeObserver.observe(content)
    return () => resizeObserver.disconnect()
  }, [contentKey, expanded, onClippedChange])

  return (
    <div
      id={contentId}
      ref={contentRef}
      className={expanded ? undefined : collapsedClassName}
    >
      {children}
    </div>
  )
}

interface ScannerRowProps {
  columns: ScannerColumnLabels
  testId: string
  name: string
  /** Where the name links. Null renders the name as plain text. */
  path: string | null
  /** Always-visible details between the name and the collapsible description. */
  summary?: ReactNode
  description: ReactNode | null
  /** Changes with the description content so its overflow is measured again. */
  descriptionKey: string
  datasets: ReactNode
  techniques: string[]
  runSize: ReactNode
}

/** One scenario: its purpose, datasets, techniques, and run size, with long text collapsed. */
export function ScannerRow({
  columns,
  testId,
  name,
  path,
  summary,
  description,
  descriptionKey,
  datasets,
  techniques,
  runSize,
}: ScannerRowProps) {
  const styles = useScenarioCatalogStyles()
  const descriptionId = useId()
  const techniquesId = useId()
  const [expanded, setExpanded] = useState(false)
  const [descriptionClipped, setDescriptionClipped] = useState(false)
  const [techniquesClipped, setTechniquesClipped] = useState(false)
  const handleDescriptionClippedChange = useCallback((clipped: boolean) => {
    setDescriptionClipped(clipped)
  }, [])
  const handleTechniquesClippedChange = useCallback((clipped: boolean) => {
    setTechniquesClipped(clipped)
  }, [])
  const hasDescription = description !== null
  const hasTechniques = techniques.length > 0
  const techniqueNames = techniques.join(' · ')
  const rowCanExpand = (hasDescription && descriptionClipped) || (hasTechniques && techniquesClipped)
  const controlledIds = [
    ...(hasDescription ? [descriptionId] : []),
    ...(hasTechniques ? [techniquesId] : []),
  ].join(' ')
  const cellClassName = mergeClasses(
    styles.tableCell,
    styles.tableCellPadding,
    SCANNER_CELL_PADDING_CLASS,
  )

  return (
    <TableRow className={styles.summaryRow} data-testid={testId}>
      <TableCell className={cellClassName}>
        <Text className={styles.mobileLabel} size={200} weight="semibold">
          {columns.scenario}
        </Text>
        <div className={styles.scenarioSummary}>
          {path === null ? (
            <Text weight="semibold">{name}</Text>
          ) : (
            <Link to={path} className={styles.scenarioLink}>
              {name}
            </Link>
          )}
          {summary}
          {hasDescription && (
            <CollapsibleContent
              contentId={descriptionId}
              contentKey={descriptionKey}
              expanded={expanded}
              collapsedClassName={styles.purposePreviewCollapsed}
              onClippedChange={handleDescriptionClippedChange}
            >
              {description}
            </CollapsibleContent>
          )}
          {rowCanExpand && (
            <Button
              appearance="transparent"
              size="small"
              className={styles.descriptionToggle}
              icon={expanded ? <ChevronUpRegular /> : <ChevronDownRegular />}
              aria-controls={controlledIds}
              aria-expanded={expanded}
              aria-label={`${expanded ? 'Collapse' : 'Expand'} row details for ${name}`}
              onClick={() => setExpanded((current) => !current)}
            />
          )}
        </div>
      </TableCell>
      <TableCell className={cellClassName}>
        <Text className={styles.mobileLabel} size={200} weight="semibold">
          {columns.datasets}
        </Text>
        {datasets}
      </TableCell>
      <TableCell className={cellClassName}>
        <Text className={styles.mobileLabel} size={200} weight="semibold">
          {columns.techniques}
        </Text>
        {hasTechniques ? (
          <div className={styles.compactStack}>
            <Text weight="semibold">
              {techniques.length} technique{techniques.length === 1 ? '' : 's'}
            </Text>
            <CollapsibleContent
              contentId={techniquesId}
              contentKey={techniqueNames}
              expanded={expanded}
              collapsedClassName={styles.inlineSummaryCollapsed}
              onClippedChange={handleTechniquesClippedChange}
            >
              <Text size={200} className={styles.secondaryText}>{techniqueNames}</Text>
            </CollapsibleContent>
          </div>
        ) : (
          <Text weight="semibold">No default techniques</Text>
        )}
      </TableCell>
      <TableCell className={cellClassName}>
        <Text className={styles.mobileLabel} size={200} weight="semibold">
          {columns.runSize}
        </Text>
        {runSize}
      </TableCell>
    </TableRow>
  )
}

interface ScenarioCatalogTableProps {
  label: string
  columns: ScannerColumnLabels
  /** `ScannerRow` elements built with the same `columns`. */
  children: ReactNode
}

/** The Scanner's scenario table; it stacks each row into a labelled card on narrow screens. */
export default function ScenarioCatalogTable({ label, columns, children }: ScenarioCatalogTableProps) {
  const styles = useScenarioCatalogStyles()
  const headerCellClassName = (columnClassName: string): string => mergeClasses(
    columnClassName,
    styles.tableHeaderCell,
    SCANNER_CELL_PADDING_CLASS,
  )

  return (
    <div className={styles.tableContainer}>
      <Table className={styles.table} size="small" aria-label={label}>
        <TableHeader className={styles.tableHeader}>
          <TableRow>
            <TableHeaderCell className={headerCellClassName(styles.scenarioColumn)}>
              {columns.scenario}
            </TableHeaderCell>
            <TableHeaderCell className={headerCellClassName(styles.datasetColumn)}>
              {columns.datasets}
            </TableHeaderCell>
            <TableHeaderCell className={headerCellClassName(styles.techniqueColumn)}>
              {columns.techniques}
            </TableHeaderCell>
            <TableHeaderCell className={headerCellClassName(styles.sizeColumn)}>
              {columns.runSize}
            </TableHeaderCell>
          </TableRow>
        </TableHeader>
        <TableBody className={styles.tableBody}>
          {children}
        </TableBody>
      </Table>
    </div>
  )
}
