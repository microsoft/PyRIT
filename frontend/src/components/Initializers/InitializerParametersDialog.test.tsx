import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import type { RegisteredInitializer } from '@/types'

import InitializerParametersDialog from './InitializerParametersDialog'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const allKindsInitializer: RegisteredInitializer = {
  initializer_name: 'kitchen_sink',
  initializer_type: 'DemoInitializer',
  description: 'Every control kind.',
  required_env_vars: ['DEMO_TOKEN'],
  supported_parameters: [
    { name: 'flag', type_name: 'bool', required: false, default: null, choices: null, is_list: false },
    { name: 'level', type_name: 'str', required: false, default: null, choices: ['low', 'high'], is_list: false },
    { name: 'tags', type_name: 'list[str]', required: false, default: null, choices: ['a', 'b'], is_list: true },
    { name: 'names', type_name: 'list[str]', required: false, default: null, choices: null, is_list: true },
    { name: 'days', type_name: 'int', required: false, default: null, choices: null, is_list: false },
    { name: 'label', type_name: 'str', required: false, default: null, choices: null, is_list: false },
  ],
}

const numericInitializer: RegisteredInitializer = {
  initializer_name: 'refresh_datasets',
  initializer_type: 'DatasetInitializer',
  description: 'Refreshes datasets.',
  required_env_vars: [],
  supported_parameters: [
    { name: 'days', type_name: 'int', required: false, default: null, choices: null, is_list: false },
    { name: 'names', type_name: 'list[str]', required: false, default: null, choices: null, is_list: true },
  ],
}

const requiredInitializer: RegisteredInitializer = {
  initializer_name: 'required_param',
  initializer_type: 'DemoInitializer',
  description: 'Requires a label.',
  required_env_vars: [],
  supported_parameters: [
    { name: 'label', type_name: 'str', required: true, default: null, choices: null, is_list: false },
  ],
}

const noParamInitializer: RegisteredInitializer = {
  initializer_name: 'load_default_datasets',
  initializer_type: 'DatasetInitializer',
  description: 'Loads default datasets.',
  required_env_vars: [],
  supported_parameters: [],
}

describe('InitializerParametersDialog', () => {
  const baseProps = {
    open: true,
    mode: 'add' as const,
    onSubmit: jest.fn().mockResolvedValue(undefined),
    onOpenChange: jest.fn(),
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('renders one control of the right kind for each parameter', () => {
    render(
      <TestWrapper>
        <InitializerParametersDialog {...baseProps} initializer={allKindsInitializer} />
      </TestWrapper>,
    )

    expect(screen.getByText('Add kitchen_sink initializer')).toBeInTheDocument()
    expect(screen.getByText(/Required env vars: DEMO_TOKEN/)).toBeInTheDocument()
    expect(screen.getByTestId('param-flag').tagName).toBe('SELECT')
    expect(screen.getByTestId('param-flag')).toHaveValue('')
    expect(screen.getByTestId('param-level').tagName).toBe('SELECT')
    expect(screen.getByTestId('param-tags-a')).toBeInTheDocument()
    expect(screen.getByTestId('param-tags-b')).toBeInTheDocument()
    expect(screen.getByTestId('param-names')).toBeInTheDocument()
    expect(screen.getByTestId('param-days')).toHaveAttribute('type', 'number')
    expect(screen.getByTestId('param-label')).toHaveAttribute('type', 'text')
  })

  it('should give each multiselect checkbox its own accessible name', () => {
    render(
      <TestWrapper>
        <InitializerParametersDialog {...baseProps} initializer={allKindsInitializer} />
      </TestWrapper>,
    )

    expect(screen.getByRole('group', { name: 'tags' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'a' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'b' })).toBeInTheDocument()
  })

  it('shows a no-parameters message and submits null for a parameterless initializer', async () => {
    const user = userEvent.setup()
    const onSubmit = jest.fn().mockResolvedValue(undefined)
    render(
      <TestWrapper>
        <InitializerParametersDialog {...baseProps} onSubmit={onSubmit} initializer={noParamInitializer} />
      </TestWrapper>,
    )

    expect(screen.getByText('This initializer takes no parameters.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Add', hidden: true }))

    expect(onSubmit).toHaveBeenCalledWith(null)
  })

  it('blocks submit and shows an error when a required field is empty', async () => {
    const user = userEvent.setup()
    const onSubmit = jest.fn().mockResolvedValue(undefined)
    render(
      <TestWrapper>
        <InitializerParametersDialog {...baseProps} onSubmit={onSubmit} initializer={requiredInitializer} />
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: 'Add', hidden: true }))

    expect(await screen.findByRole('alert')).toHaveTextContent('label is required.')
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('coerces typed number and comma-separated list values on submit', async () => {
    const user = userEvent.setup()
    const onSubmit = jest.fn().mockResolvedValue(undefined)
    render(
      <TestWrapper>
        <InitializerParametersDialog {...baseProps} onSubmit={onSubmit} initializer={numericInitializer} />
      </TestWrapper>,
    )

    fireEvent.change(screen.getByTestId('param-days'), { target: { value: '7' } })
    fireEvent.change(screen.getByTestId('param-names'), { target: { value: 'x, y' } })
    await user.click(screen.getByRole('button', { name: 'Add', hidden: true }))

    expect(onSubmit).toHaveBeenCalledWith({ days: 7, names: ['x', 'y'] })
  })

  it('submits toggled boolean and selected multiselect values', async () => {
    const user = userEvent.setup()
    const onSubmit = jest.fn().mockResolvedValue(undefined)
    render(
      <TestWrapper>
        <InitializerParametersDialog {...baseProps} onSubmit={onSubmit} initializer={allKindsInitializer} />
      </TestWrapper>,
    )

    fireEvent.change(screen.getByTestId('param-flag'), { target: { value: 'true' } })
    await user.click(screen.getByTestId('param-tags-a'))
    await user.click(screen.getByRole('button', { name: 'Add', hidden: true }))

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ flag: true, tags: ['a'] }))
  })

  it('toggles the intended multiselect option when clicking checkbox label text', async () => {
    const user = userEvent.setup()
    const onSubmit = jest.fn().mockResolvedValue(undefined)
    render(
      <TestWrapper>
        <InitializerParametersDialog {...baseProps} onSubmit={onSubmit} initializer={allKindsInitializer} />
      </TestWrapper>,
    )

    await user.click(screen.getByText('b'))
    await user.click(screen.getByRole('button', { name: 'Add', hidden: true }))

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ tags: ['b'] }))
  })

  it('leaves an optional boolean unset omitted from the submitted parameters', async () => {
    const user = userEvent.setup()
    const onSubmit = jest.fn().mockResolvedValue(undefined)
    render(
      <TestWrapper>
        <InitializerParametersDialog {...baseProps} onSubmit={onSubmit} initializer={allKindsInitializer} />
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: 'Add' }))

    // Every other optional field is also left blank, so the whole payload is null;
    // the key assertion is that the omitted boolean doesn't silently coerce to false.
    expect(onSubmit).toHaveBeenCalledWith(null)
  })

  it('unchecks a multiselect choice and picks a select value', async () => {
    const user = userEvent.setup()
    const onSubmit = jest.fn().mockResolvedValue(undefined)
    render(
      <TestWrapper>
        <InitializerParametersDialog {...baseProps} onSubmit={onSubmit} initializer={allKindsInitializer} />
      </TestWrapper>,
    )

    await user.click(screen.getByTestId('param-tags-a'))
    await user.click(screen.getByTestId('param-tags-a'))
    fireEvent.change(screen.getByTestId('param-level'), { target: { value: 'high' } })
    await user.click(screen.getByRole('button', { name: 'Add', hidden: true }))

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ level: 'high' }))
    expect(onSubmit.mock.calls[0][0]).not.toHaveProperty('tags')
  })

  it('prefills existing parameters in edit mode', () => {
    render(
      <TestWrapper>
        <InitializerParametersDialog
          {...baseProps}
          mode="edit"
          initializer={numericInitializer}
          initialParameters={{ days: 5, names: ['alpha', 'beta'] }}
        />
      </TestWrapper>,
    )

    expect(screen.getByText('Edit refresh_datasets initializer')).toBeInTheDocument()
    expect(screen.getByTestId('param-days')).toHaveValue(5)
    expect(screen.getByTestId('param-names')).toHaveValue('alpha, beta')
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument()
  })

  it('does not pin absent declaration defaults when editing persisted parameters', async () => {
    const user = userEvent.setup()
    const onSubmit = jest.fn().mockResolvedValue(undefined)
    const initializer: RegisteredInitializer = {
      ...numericInitializer,
      supported_parameters: [
        {
          name: 'days',
          type_name: 'int',
          required: false,
          default: '7',
          choices: null,
          is_list: false,
        },
      ],
    }

    render(
      <TestWrapper>
        <InitializerParametersDialog
          {...baseProps}
          mode="edit"
          initializer={initializer}
          initialParameters={{}}
          onSubmit={onSubmit}
        />
      </TestWrapper>,
    )

    expect(screen.getByTestId('param-days')).toHaveValue(null)
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSubmit).toHaveBeenCalledWith(null)
  })

  it('calls onOpenChange(false) when cancelled', async () => {
    const user = userEvent.setup()
    const onOpenChange = jest.fn()
    render(
      <TestWrapper>
        <InitializerParametersDialog {...baseProps} onOpenChange={onOpenChange} initializer={numericInitializer} />
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('disables the actions and shows progress text while submitting', () => {
    render(
      <TestWrapper>
        <InitializerParametersDialog {...baseProps} submitting initializer={numericInitializer} />
      </TestWrapper>,
    )

    expect(screen.getByRole('button', { name: 'Add...' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
  })

  it('displays an external error passed via externalError prop', () => {
    render(
      <TestWrapper>
        <InitializerParametersDialog
          {...baseProps}
          initializer={numericInitializer}
          externalError="Server rejected the request."
        />
      </TestWrapper>,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('Server rejected the request.')
  })

  it('prefers validation error over externalError', async () => {
    const user = userEvent.setup()
    const onSubmit = jest.fn().mockResolvedValue(undefined)
    render(
      <TestWrapper>
        <InitializerParametersDialog
          {...baseProps}
          onSubmit={onSubmit}
          initializer={requiredInitializer}
          externalError="Server error"
        />
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: 'Add', hidden: true }))

    expect(await screen.findByRole('alert')).toHaveTextContent('label is required.')
  })
})
