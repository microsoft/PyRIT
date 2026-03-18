import '@testing-library/jest-dom';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FluentProvider, webLightTheme } from '@fluentui/react-components';
import React from 'react';

jest.mock('../../services/api', () => ({
  convertersApi: {
    listConverterCatalog: jest.fn(),
  },
  attacksApi: {
    getMessages: jest.fn().mockResolvedValue({ messages: [] }),
    getConversations: jest.fn().mockResolvedValue({ main_conversation_id: 'c1', conversations: [] }),
  },
}));

import { convertersApi } from '../../services/api';
import ConverterPanel from './ConverterPanel';

const mockedConvertersApi = jest.mocked(convertersApi);

function Wrapper({ children }: { children: React.ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>;
}

test('combobox opens in ConverterPanel', async () => {
  mockedConvertersApi.listConverterCatalog.mockResolvedValue({
    items: [
      { converter_type: 'Base64Converter', supported_input_types: ['text'], supported_output_types: ['text'] },
      { converter_type: 'CharSwapConverter', supported_input_types: ['text'], supported_output_types: ['text'] },
    ],
  });

  render(
    <Wrapper>
      <ConverterPanel onClose={jest.fn()} />
    </Wrapper>
  );

  // Wait for data to load
  await waitFor(() => {
    expect(screen.getByTestId('converter-item-Base64Converter')).toBeInTheDocument();
  });

  const input = screen.getByRole('combobox');
  console.log('aria-expanded before click:', input.getAttribute('aria-expanded'));

  await userEvent.click(input);
  console.log('aria-expanded after click:', input.getAttribute('aria-expanded'));

  const options = screen.queryAllByRole('option', { hidden: true });
  console.log('options count:', options.length);
  options.forEach(o => console.log('  option:', o.textContent));
});
