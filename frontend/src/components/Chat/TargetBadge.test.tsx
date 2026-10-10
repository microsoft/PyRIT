import { render, screen } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { makeTarget } from '@/test-utils/targetFixtures'
import TargetBadge from './TargetBadge'
import type { FlatTargetInput } from '@/test-utils/targetFixtures'
import type { TargetInstance } from '../../types'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const flatBaseTarget: FlatTargetInput = {
  target_registry_name: 'azure_openai_gpt4o',
  target_type: 'OpenAIChatTarget',
  endpoint: 'https://example.openai.azure.com/openai/deployments/gpt-4o',
  model_name: 'gpt-4o',
  underlying_model_name: 'gpt-4o',
  capabilities: {
    supports_multi_turn: true,
    supports_multi_message_pieces: true,
    supports_json_schema: false,
    supports_json_output: true,
    supports_editable_history: true,
    supports_system_prompt: true,
    supported_input_modalities: ['text', 'image_path'],
    supported_output_modalities: ['text'],
  },
  target_specific_params: {
    api_version: '2024-09-01',
  },
}

const baseTarget: TargetInstance = makeTarget(flatBaseTarget)

describe('TargetBadge', () => {
  it('renders the type and model name as the visible badge label', () => {
    render(
      <TestWrapper>
        <TargetBadge target={baseTarget} />
      </TestWrapper>
    )
    const badge = screen.getByTestId('target-badge')
    expect(badge).toHaveTextContent('OpenAIChatTarget (gpt-4o)')
  })

  it('uses the registry name in its accessible name so screen readers can disambiguate', () => {
    render(
      <TestWrapper>
        <TargetBadge target={baseTarget} />
      </TestWrapper>
    )
    expect(screen.getByLabelText(/azure_openai_gpt4o/)).toBeInTheDocument()
  })

  it('renders only the type when no model name is set', () => {
    render(
      <TestWrapper>
        <TargetBadge target={makeTarget({ ...flatBaseTarget, model_name: null })} />
      </TestWrapper>
    )
    const badge = screen.getByTestId('target-badge')
    expect(badge).toHaveTextContent('OpenAIChatTarget')
    expect(badge).not.toHaveTextContent('gpt-4o')
  })

  it('does not throw on a target with no capabilities or params', () => {
    const minimal: TargetInstance = makeTarget({
      target_registry_name: 't',
      target_type: 'TextTarget',
    })
    expect(() =>
      render(
        <TestWrapper>
          <TargetBadge target={minimal} />
        </TestWrapper>
      )
    ).not.toThrow()
    expect(screen.getByTestId('target-badge')).toHaveTextContent('TextTarget')
  })

  it('shows count in display name for RoundRobinTarget with inner targets', () => {
    const rrTarget: TargetInstance = makeTarget({
      target_registry_name: 'rr_test',
      target_type: 'RoundRobinTarget',
      model_name: 'gpt-4o',
      inner_targets: [
        { target_registry_name: 'a', target_type: 'OpenAIChatTarget', model_name: 'gpt-4o' },
        { target_registry_name: 'b', target_type: 'OpenAIChatTarget', model_name: 'gpt-4o' },
        { target_registry_name: 'c', target_type: 'OpenAIChatTarget', model_name: 'gpt-4o' },
      ],
    })
    render(
      <TestWrapper>
        <TargetBadge target={rrTarget} />
      </TestWrapper>
    )
    const badge = screen.getByTestId('target-badge')
    expect(badge).toHaveTextContent('RoundRobinTarget (gpt-4o ×3)')
  })

  it('prefers underlying_model_name over model_name for RoundRobinTarget badge', () => {
    const rrTarget: TargetInstance = makeTarget({
      target_registry_name: 'rr_mixed',
      target_type: 'RoundRobinTarget',
      model_name: 'gpt-4o-japan-nilfilter',
      underlying_model_name: 'gpt-4o',
      inner_targets: [
        { target_registry_name: 'a', target_type: 'OpenAIChatTarget', model_name: 'deploy-1' },
        { target_registry_name: 'b', target_type: 'OpenAIChatTarget', model_name: 'deploy-2' },
      ],
    })
    render(
      <TestWrapper>
        <TargetBadge target={rrTarget} />
      </TestWrapper>
    )
    const badge = screen.getByTestId('target-badge')
    // Should show the underlying model, not the deployment name
    expect(badge).toHaveTextContent('RoundRobinTarget (gpt-4o ×2)')
  })
})
