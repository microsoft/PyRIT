import { type ReactNode, useState } from 'react'

import {
  Accordion,
  AccordionHeader,
  AccordionItem,
  AccordionPanel,
  mergeClasses,
  Text,
} from '@fluentui/react-components'

import { useCollapsibleCardStyles } from './CollapsibleCard.styles'

const CARD_ITEM = 'card'

interface CollapsibleCardProps {
  /** Names the item. It and `meta` form the heading and the toggle's accessible name. */
  title: string
  /** Short facts read with the title, such as "3 scenarios". */
  meta?: ReactNode
  /** Status badges shown beside the heading, outside the toggle. */
  badges?: ReactNode
  description?: string | null
  /** Buttons beside the heading. They sit outside the toggle so each keeps its own name. */
  actions?: ReactNode
  /** Marks the card with a warning edge. */
  attention?: boolean
  /** Controls the card when set; otherwise the card keeps its own open state. */
  open?: boolean
  onOpenChange?: (open: boolean) => void
  testId?: string
  children: ReactNode
}

/** A card whose heading toggles a panel, for lists of named items with details on demand. */
export default function CollapsibleCard({
  title,
  meta,
  badges,
  description,
  actions,
  attention = false,
  open,
  onOpenChange,
  testId,
  children,
}: CollapsibleCardProps) {
  const styles = useCollapsibleCardStyles()
  const [ownOpen, setOwnOpen] = useState(false)
  const isOpen = open ?? ownOpen

  return (
    <article
      className={mergeClasses(styles.root, attention && styles.attention)}
      data-testid={testId}
    >
      <Accordion
        collapsible
        openItems={isOpen ? [CARD_ITEM] : []}
        onToggle={(_, data) => {
          const nextOpen = data.openItems.includes(CARD_ITEM)
          if (open === undefined) setOwnOpen(nextOpen)
          onOpenChange?.(nextOpen)
        }}
      >
        <AccordionItem value={CARD_ITEM}>
          <div className={styles.headerRow}>
            <AccordionHeader
              as="h3"
              className={styles.header}
              button={{ className: styles.headerButton }}
              expandIconPosition="start"
              size="large"
            >
              <span className={styles.title}>
                <Text size={400} weight="semibold">{title}</Text>
                {/* The space keeps the title and meta apart in the toggle's accessible name. */}
                {meta && <>{' '}<Text size={200} className={styles.meta}>{meta}</Text></>}
              </span>
            </AccordionHeader>
            {badges && <div className={styles.badges}>{badges}</div>}
            {actions && <div className={styles.actions}>{actions}</div>}
          </div>
          {description && <Text size={200} className={styles.description}>{description}</Text>}
          <AccordionPanel className={styles.panel}>{children}</AccordionPanel>
        </AccordionItem>
      </Accordion>
    </article>
  )
}
