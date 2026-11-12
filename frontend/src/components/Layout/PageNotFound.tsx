import { Text } from '@fluentui/react-components'
import { Link } from 'react-router'

import { usePageNotFoundStyles } from './PageNotFound.styles'

export default function PageNotFound() {
  const styles = usePageNotFoundStyles()

  return (
    <section className={styles.root} aria-labelledby="page-not-found-title">
      <Text as="h1" size={600} weight="semibold" className={styles.heading} id="page-not-found-title">
        Page not found
      </Text>
      <Text>The link does not match a page.</Text>
      <Link to="/" className={styles.homeLink}>Home</Link>
    </section>
  )
}
