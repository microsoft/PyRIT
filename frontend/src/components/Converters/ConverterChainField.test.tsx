import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { HELP_DEFINITIONS } from '@/components/helpDefinitions'

import ConverterChainField from './ConverterChainField'

describe('ConverterChainField', () => {
  beforeEach(() => jest.clearAllMocks())

  it('uses explicit help even when the visible label changes', async () => {
    const user = userEvent.setup()
    render(<FluentProvider theme={webLightTheme}>
      <ConverterChainField label="Converted replies" help={HELP_DEFINITIONS.responseConverters}
        converters={[]} value={[]} onChange={jest.fn()} onConvertersChanged={jest.fn()} />
    </FluentProvider>)

    await user.hover(screen.getByText('Converted replies'))
    expect(await screen.findByRole('tooltip')).toHaveTextContent('Transform target responses before scoring.')
  })
})
