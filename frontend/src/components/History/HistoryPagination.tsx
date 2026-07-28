import {
  Text,
  Button,
} from '@fluentui/react-components'
import {
  ChevronLeftRegular,
  ChevronRightRegular,
} from '@fluentui/react-icons'
import { useMobileTouchTargetStyles } from '@/styles/mobileTouchTargetStyles'

import { useAttackHistoryStyles } from './AttackHistory.styles'

interface HistoryPaginationProps {
  page: number
  isLastPage: boolean
  onPrevPage: () => void
  onNextPage: () => void
}

export default function HistoryPagination({ page, isLastPage, onPrevPage, onNextPage }: HistoryPaginationProps) {
  const styles = useAttackHistoryStyles()
  const mobileTouchTargets = useMobileTouchTargetStyles()

  return (
    <div className={styles.pagination}>
      <Button
        appearance="subtle"
        icon={<ChevronLeftRegular />}
        disabled={page === 0}
        onClick={onPrevPage}
        data-testid="prev-page-btn"
        className={mobileTouchTargets.control}
      >
        First
      </Button>
      <Text size={200}>Page {page + 1}</Text>
      <Button
        appearance="subtle"
        icon={<ChevronRightRegular />}
        iconPosition="after"
        disabled={isLastPage}
        onClick={onNextPage}
        data-testid="next-page-btn"
        className={mobileTouchTargets.control}
      >
        Next
      </Button>
    </div>
  )
}
