import { makeStyles, tokens } from '@fluentui/react-components'

export const useRegistryIdentityRowStyles = makeStyles({
  row: {
    backgroundColor: tokens.colorNeutralBackground1,
  },
  name: {
    paddingBlock: tokens.spacingVerticalS,
  },
  nameCell: {
    minWidth: 0,
  },
  // Under the name, so every row's indicators line up however long its name is.
  nameIndicators: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXS,
    marginTop: tokens.spacingVerticalXXS,
  },
})
