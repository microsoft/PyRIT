import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import ConversionTextEditor from './ConversionTextEditor'

function TestWrapper({ initial = 'hello world' }: { initial?: string }) {
  const [value, setValue] = useState(initial)
  return (
    <FluentProvider theme={webLightTheme}>
      <ConversionTextEditor value={value} label="Working input" placeholder="Input" allowSelection onChange={setValue} />
    </FluentProvider>
  )
}

describe('ConversionTextEditor', () => {
  it('marks a keyboard selection without losing it when the button receives focus', async () => {
    const user = userEvent.setup()
    render(<TestWrapper />)
    const editor = screen.getByRole('textbox', { name: 'Working input' })
    const mark = screen.getByRole('button', { name: 'Convert selection only in Working input' })
    expect(mark).toBeDisabled()
    await user.click(editor)
    // user-event does not extend textarea selections with Shift+Arrow.
    if (!(editor instanceof HTMLTextAreaElement)) throw new Error('Expected textarea')
    editor.setSelectionRange(0, 5)
    fireEvent.select(editor)
    await user.tab()
    expect(mark).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(editor).toHaveValue('\u27eahello\u27eb world')
    expect(editor).toHaveFocus()
  })

  it('marks multiline text and supports separate marked regions', async () => {
    const user = userEvent.setup()
    render(<TestWrapper initial={'one\ntwo rest'} />)
    const editor = screen.getByRole('textbox', { name: 'Working input' })
    await user.pointer([
      { target: editor, offset: 0, keys: '[MouseLeft>]' },
      { target: editor, offset: 7 },
      { keys: '[/MouseLeft]' },
    ])
    await user.click(screen.getByRole('button', { name: 'Convert selection only in Working input' }))
    expect(editor).toHaveValue('\u27eaone\ntwo\u27eb rest')
    await user.pointer([
      { target: editor, offset: 10, keys: '[MouseLeft>]' },
      { target: editor, offset: 14 },
      { keys: '[/MouseLeft]' },
    ])
    await user.click(screen.getByRole('button', { name: 'Convert selection only in Working input' }))
    expect(editor).toHaveValue('\u27eaone\ntwo\u27eb \u27earest\u27eb')
    expect(screen.getAllByTestId('conversion-marked-region')).toHaveLength(2)
    expect(screen.getAllByTestId('conversion-marked-region')[0].textContent).toBe('\u27eaone\ntwo\u27eb')
  })

  it.each([[1, 4], [0, 5], [3, 8]])('rejects a selection overlapping markers (%s, %s)', async (start: number, end: number) => {
    const user = userEvent.setup()
    render(<TestWrapper initial={'\u27eaone\u27eb rest'} />)
    const editor = screen.getByRole('textbox', { name: 'Working input' })
    await user.pointer([
      { target: editor, offset: start, keys: '[MouseLeft>]' },
      { target: editor, offset: end },
      { keys: '[/MouseLeft]' },
    ])
    await user.click(screen.getByRole('button', { name: 'Convert selection only in Working input' }))
    expect(screen.getByText('Select text outside an existing marked region.')).toBeInTheDocument()
    expect(editor).toHaveValue('\u27eaone\u27eb rest')
  })

  it('does not highlight an unmatched marker', () => {
    render(<TestWrapper initial={'plain \u27eaunfinished'} />)

    expect(screen.queryByTestId('conversion-marked-region')).not.toBeInTheDocument()
    expect(screen.getByTestId('conversion-highlight-layer')).toHaveTextContent('plain \u27eaunfinished')
  })

  it.each([
    ['one\r\ntwo\r\nthree', 4, 7, 'one\r\n\u27eatwo\u27eb\r\nthree'],
    ['one\r\ntwo\r\nthree', 1, 6, 'o\u27eane\r\ntw\u27ebo\r\nthree'],
    ['\ud83d\ude00\r\ntwo', 3, 6, '\ud83d\ude00\r\n\u27eatwo\u27eb'],
    ['one\rtwo', 4, 7, 'one\r\u27eatwo\u27eb'],
  ])('marks a selection without changing source line endings in %j', async (
    value: string, start: number, end: number, expected: string,
  ) => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<FluentProvider theme={webLightTheme}>
      <ConversionTextEditor value={value} label="Working input" placeholder="Input" allowSelection onChange={onChange} />
    </FluentProvider>)
    const editor = screen.getByRole('textbox', { name: 'Working input' })
    if (!(editor instanceof HTMLTextAreaElement)) throw new Error('Expected textarea')
    await user.click(editor)
    editor.setSelectionRange(start, end)
    fireEvent.select(editor)
    await user.click(screen.getByRole('button', { name: 'Convert selection only in Working input' }))

    expect(onChange).toHaveBeenCalledWith(expected)
  })

  it('keeps the highlight layer scrolled with the editor', () => {
    render(<TestWrapper initial={'one\ntwo\nthree\nfour\nfive'} />)
    const editor = screen.getByRole('textbox', { name: 'Working input' })

    fireEvent.scroll(editor, { target: { scrollTop: 64, scrollLeft: 8 } })

    const highlight = screen.getByTestId('conversion-highlight-layer')
    expect(highlight.scrollTop).toBe(64)
    expect(highlight.scrollLeft).toBe(8)
  })
})
