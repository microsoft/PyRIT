import { useState } from 'react'

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { datasetsApi } from '@/services/api'
import type { SeedExampleDetailResponse, SeedRecord } from '@/types'
import { useSeedExampleDetail } from '@/hooks/useSeedExamples'

import SeedExampleDetail from './SeedExampleDetail'

function member(overrides: Partial<SeedRecord> = {}): SeedRecord {
  return {
    id: 'member-1',
    seed_type: 'prompt',
    value: 'plain',
    value_sha256: 'abc',
    data_type: 'text',
    name: 'named',
    dataset_name: 'harmbench',
    origin: 'local',
    harm_categories: ['hate'],
    description: 'described',
    authors: ['ada'],
    groups: ['group'],
    source: 'source',
    date_added: '2026-01-02T03:04:05Z',
    added_by: 'tester',
    metadata: { generation_conversation_id: 'conversation-9' },
    prompt_group_id: 'group-1',
    sequence: 0,
    role: 'user',
    parameters: ['name'],
    conditions: [{ condition_type: 'text_match', pattern: 'secret' }],
    response_json_schema: { type: 'object' },
    ...overrides,
  }
}

const EXACT_VALUE = '  keep\n\nwhitespace  '
const CONFIG_VALUE = '{\n  "num_turns": 3\n}'

const DETAIL: SeedExampleDetailResponse = {
  example_id: 'example-1',
  name: 'multimodal',
  preview: 'short',
  preview_truncated: true,
  modalities: ['text', 'image_path'],
  seed_types: ['prompt', 'simulated_conversation'],
  piece_count: 2,
  objective_count: 0,
  harm_categories: ['hate'],
  has_unlabeled_harm: false,
  members: [
    member({
      id: 'prompt-1',
      seed_type: 'prompt',
      value: EXACT_VALUE,
      data_type: 'text',
    }),
    member({
      id: 'config-1',
      seed_type: 'simulated_conversation',
      value: CONFIG_VALUE,
      data_type: 'text',
      parameters: null,
      conditions: null,
      harm_categories: null,
      role: null,
      sequence: null,
      value_sha256: null,
      name: null,
      dataset_name: null,
      description: null,
      authors: null,
      groups: null,
      source: null,
      metadata: null,
      prompt_group_id: null,
      response_json_schema: null,
      origin: 'unknown',
    }),
    member({
      id: 'image-1',
      seed_type: 'prompt',
      value: '<img src=x onerror=alert(1)> /tmp/secret.png',
      data_type: 'image_path',
      harm_categories: [],
      parameters: [],
      conditions: [],
    }),
  ],
}

function renderDetail(detail: SeedExampleDetailResponse = DETAIL) {
  return render(
    <FluentProvider theme={webLightTheme}>
      <SeedExampleDetail detail={detail} />
    </FluentProvider>,
  )
}

describe('SeedExampleDetail', () => {
  afterEach(() => {
    jest.restoreAllMocks()
  })

  it('keeps server member order and shows stored fields without executing them', () => {
    renderDetail()

    const members = screen.getAllByRole('article')
    expect(members.map((item) => item.getAttribute('aria-label'))).toEqual([
      'Member 1',
      'Member 2',
      'Member 3',
    ])
    expect(within(members[0]).getByTestId('seed-value-prompt-1').textContent).toBe(EXACT_VALUE)
    expect(within(members[1]).getByText('Stored configuration. This view does not run it.')).toBeInTheDocument()
    expect(within(members[1]).getByTestId('seed-value-config-1').textContent).toBe(CONFIG_VALUE)
    expect(screen.queryByText('Num turns')).not.toBeInTheDocument()
    expect(within(members[2]).getByText('File is missing.')).toBeInTheDocument()
    expect(within(members[2]).getByText('<img src=x onerror=alert(1)> /tmp/secret.png')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
    expect(document.querySelector('audio')).toBeNull()
    expect(document.querySelector('video')).toBeNull()
    expect(screen.getAllByText('Template status is not stored.').length).toBeGreaterThan(0)
    expect(screen.queryByText(/this is a template/i)).not.toBeInTheDocument()
    expect(screen.queryByText('Safe')).not.toBeInTheDocument()
    expect(screen.queryByText('License')).not.toBeInTheDocument()
    expect(within(members[0]).getByText('text_match')).toBeInTheDocument()
    expect(within(members[0]).getByText('conversation-9')).toBeInTheDocument()
    expect(within(members[0]).getByText('name')).toBeInTheDocument()
    expect(within(members[1]).getAllByText('Missing').length).toBeGreaterThan(0)
    expect(within(members[2]).getAllByText('None').length).toBeGreaterThan(0)
  })

  it('copies the stored value exactly and reports a clipboard failure', async () => {
    const user = userEvent.setup()
    const writeText = jest.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    renderDetail()

    await user.click(screen.getAllByRole('button', { name: 'Copy full text' })[0])
    expect(writeText).toHaveBeenCalledWith(EXACT_VALUE)
    expect(screen.getByRole('button', { name: 'Copied full text' })).toBeInTheDocument()

    writeText.mockRejectedValueOnce(new Error('denied'))
    await user.click(screen.getAllByRole('button', { name: 'Copy ID' })[0])
    expect(writeText).toHaveBeenCalledWith('prompt-1')
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not copy to the clipboard.')
  })

  it('previews a signed blob URL on demand and keeps the stored reference', async () => {
    const user = userEvent.setup()
    const stored = 'https://account.blob.core.windows.net/container/clip.mp4'
    const signed = `${stored}?sig=signed`
    renderDetail({
      ...DETAIL,
      members: [
        member({ id: 'prompt-1', value: EXACT_VALUE, data_type: 'text' }),
        member({ id: 'clip-1', value: stored, value_url: signed, data_type: 'video_path' }),
        member({
          id: 'mark-1',
          value: '/dbdata/seed-prompt-entries/mark.svg',
          value_url: '/api/media?path=%2Fdbdata%2Fseed-prompt-entries%2Fmark.svg',
          data_type: 'image_path',
        }),
      ],
    })

    expect(screen.getByTestId('seed-value-clip-1')).toHaveTextContent(stored)
    expect(screen.queryByTestId('video-player')).not.toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByText('This format cannot be previewed.')).toBeInTheDocument()
    const download = screen.getByRole('link', { name: 'Download' })
    expect(download).toHaveAttribute(
      'href',
      '/api/media?path=%2Fdbdata%2Fseed-prompt-entries%2Fmark.svg',
    )
    expect(download).toHaveAttribute('download', 'mark.svg')
    expect(download).not.toHaveAttribute('target')

    await user.click(screen.getByRole('button', { name: 'Show video preview' }))
    const video = screen.getByTestId('video-player')
    expect(video).toHaveAttribute('src', signed)
    expect(video).not.toHaveAttribute('autoplay')
    expect(video).toHaveAttribute('preload', 'none')
    expect(screen.getByTestId('seed-value-prompt-1').textContent).toBe(EXACT_VALUE)
  })

  it('should open an unsupported cross-origin file in a new tab', () => {
    const signed = 'https://account.blob.core.windows.net/container/clip.mp4?sig=signed'
    renderDetail({
      ...DETAIL,
      members: [
        member({
          id: 'odd-1',
          data_type: 'image_path',
          value: 'https://account.blob.core.windows.net/container/clip.mp4',
          value_url: signed,
        }),
      ],
    })
    const link = screen.getByRole('link', { name: 'Open in new tab' })
    expect(link).toHaveAttribute('href', signed)
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).not.toHaveAttribute('download')
  })

  it('should retry a 403 with a new signature and keep the stored reference', async () => {
    const user = userEvent.setup()
    const stored = 'https://account.blob.core.windows.net/container/pic.png'
    const expired = `${stored}?sig=expired`
    const fresh = `${stored}?sig=fresh`

    function Harness() {
      const [url, setUrl] = useState(expired)
      return (
        <FluentProvider theme={webLightTheme}>
          <SeedExampleDetail
            detail={{
              ...DETAIL,
              members: [
                member({ id: 'image-1', data_type: 'image_path', value: stored, value_url: url, name: null }),
              ],
            }}
            onRefreshUrls={async () => {
              await Promise.resolve()
              setUrl(fresh)
              return fresh
            }}
          />
        </FluentProvider>
      )
    }

    render(<Harness />)
    await user.click(screen.getByRole('button', { name: 'Show image preview' }))
    fireEvent.error(screen.getByAltText('image seed'))
    await user.click(screen.getByRole('button', { name: 'Retry image' }))
    await waitFor(() => expect(screen.getByAltText('image seed')).toHaveAttribute('src', fresh))
    expect(screen.getByTestId('seed-value-image-1')).toHaveTextContent(stored)
    expect(screen.queryByTestId('image-error')).not.toBeInTheDocument()
  })

  it('should refresh only the retried member and leave the other player on its current url', async () => {
    const user = userEvent.setup()
    const imageStored = 'https://account.blob.core.windows.net/container/pic.png'
    const audioStored = 'https://account.blob.core.windows.net/container/talk.wav'
    const imageOld = `${imageStored}?sig=old`
    const audioOld = `${audioStored}?sig=old`
    const imageNew = `${imageStored}?sig=new`
    const audioNew = `${audioStored}?sig=new`
    const initial = {
      ...DETAIL,
      members: [
        member({ id: 'image-1', data_type: 'image_path', value: imageStored, value_url: imageOld, name: 'picture' }),
        member({ id: 'audio-1', data_type: 'audio_path', value: audioStored, value_url: audioOld, name: 'talk' }),
      ],
    }
    const resigned = {
      ...initial,
      members: [
        member({ id: 'image-1', data_type: 'image_path', value: imageStored, value_url: imageNew, name: 'picture' }),
        member({ id: 'audio-1', data_type: 'audio_path', value: audioStored, value_url: audioNew, name: 'talk' }),
      ],
    }
    let phase: 'initial' | 'refresh' = 'initial'
    const spy = jest.spyOn(datasetsApi, 'getSeedExample').mockImplementation(async () => (
      phase === 'initial' ? initial : resigned
    ))

    function Harness() {
      const state = useSeedExampleDetail('dataset:named:harmbench', 'example-1', false, 'gen-1')
      if (state.detail == null) return null
      return (
        <FluentProvider theme={webLightTheme}>
          <SeedExampleDetail detail={state.detail} onRefreshUrls={state.refreshDisplayUrls} />
        </FluentProvider>
      )
    }

    render(<Harness />)
    await user.click(await screen.findByRole('button', { name: 'Show audio preview' }))
    const audio = screen.getByTestId('audio-player')
    expect(audio).toHaveAttribute('src', audioOld)

    await user.click(screen.getByRole('button', { name: 'Show image preview' }))
    fireEvent.error(screen.getByAltText('picture'))
    phase = 'refresh'
    await user.click(screen.getByRole('button', { name: 'Retry image' }))
    await waitFor(() => expect(screen.getByAltText('picture')).toHaveAttribute('src', imageNew))
    expect(screen.getByTestId('audio-player')).toBe(audio)
    expect(audio).toHaveAttribute('src', audioOld)
    expect(spy).toHaveBeenCalled()
  })

  it('should not put a refreshed active url on the download link', async () => {
    const user = userEvent.setup()
    const stored = 'https://account.blob.core.windows.net/container/pic.png'
    const expired = `${stored}?sig=expired`
    const html = 'data:text/html,<script>alert(1)</script>'

    function Harness() {
      const [url, setUrl] = useState(expired)
      return (
        <FluentProvider theme={webLightTheme}>
          <SeedExampleDetail
            detail={{
              ...DETAIL,
              members: [
                member({ id: 'image-1', data_type: 'image_path', value: stored, value_url: url, name: null }),
              ],
            }}
            onRefreshUrls={async () => {
              setUrl(html)
              return html
            }}
          />
        </FluentProvider>
      )
    }

    render(<Harness />)
    await user.click(screen.getByRole('button', { name: 'Show image preview' }))
    fireEvent.error(screen.getByAltText('image seed'))
    await user.click(screen.getByRole('button', { name: 'Retry image' }))
    await waitFor(() => expect(screen.getByText('This format cannot be previewed.')).toBeInTheDocument())
    expect(document.body.innerHTML).not.toContain('data:text/html')
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
  })

  it('should ignore a refresh that fails after the example is closed and opened again', async () => {
    const user = userEvent.setup()
    const stored = 'https://account.blob.core.windows.net/container/pic.png'
    const openedUrl = `${stored}?sig=opened`
    const reopenedUrl = `${stored}?sig=reopened`
    const opened = {
      ...DETAIL,
      members: [
        member({ id: 'image-1', data_type: 'image_path', value: stored, value_url: openedUrl, name: 'picture' }),
      ],
    }
    const reopened = {
      ...opened,
      members: [
        member({ id: 'image-1', data_type: 'image_path', value: stored, value_url: reopenedUrl, name: 'picture' }),
      ],
    }
    let rejectRefresh: (err: Error) => void = () => {}
    jest.spyOn(datasetsApi, 'getSeedExample')
      .mockResolvedValueOnce(opened)
      .mockImplementationOnce(() => new Promise((_, reject) => { rejectRefresh = reject }))
      .mockResolvedValue(reopened)

    function Harness({ exampleId }: { exampleId: string | null }) {
      const state = useSeedExampleDetail('dataset:named:harmbench', exampleId, false, 'gen-refresh')
      if (exampleId == null || state.detail == null || state.detail.example_id !== exampleId) return <div>closed</div>
      return <SeedExampleDetail detail={state.detail} onRefreshUrls={state.refreshDisplayUrls} />
    }

    const view = render(
      <FluentProvider theme={webLightTheme}>
        <Harness exampleId="example-1" />
      </FluentProvider>,
    )
    await user.click(await screen.findByRole('button', { name: 'Show image preview' }))
    fireEvent.error(screen.getByAltText('picture'))
    await user.click(screen.getByRole('button', { name: 'Retry image' }))
    view.rerender(
      <FluentProvider theme={webLightTheme}>
        <Harness exampleId={null} />
      </FluentProvider>,
    )
    expect(screen.getByText('closed')).toBeInTheDocument()
    view.rerender(
      <FluentProvider theme={webLightTheme}>
        <Harness exampleId="example-1" />
      </FluentProvider>,
    )
    await screen.findByRole('button', { name: 'Show image preview' })
    await act(async () => {
      rejectRefresh(new Error('unavailable'))
    })
    expect(screen.queryByText(/Couldn't refresh the link/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Show image preview' }))
    expect(screen.getByAltText('picture')).toHaveAttribute('src', reopenedUrl)
    expect(screen.queryByText(/Couldn't refresh the link/)).not.toBeInTheDocument()
  })

  it('should keep a member refresh alive when another member is retried', async () => {
    const user = userEvent.setup()
    const imageStored = 'https://account.blob.core.windows.net/container/pic.png'
    const audioStored = 'https://account.blob.core.windows.net/container/talk.wav'
    const imageOld = `${imageStored}?sig=old`
    const audioOld = `${audioStored}?sig=old`
    const opened = {
      ...DETAIL,
      members: [
        member({ id: 'image-1', data_type: 'image_path', value: imageStored, value_url: imageOld, name: 'picture' }),
        member({ id: 'audio-1', data_type: 'audio_path', value: audioStored, value_url: audioOld, name: 'talk' }),
      ],
    }
    let rejectImage: (err: Error) => void = () => {}
    let imageCalls = 0
    jest.spyOn(datasetsApi, 'getSeedExample').mockImplementation(() => {
      imageCalls += 1
      if (imageCalls === 1) return Promise.resolve(opened)
      if (imageCalls === 2) return new Promise((_, reject) => { rejectImage = reject })
      return new Promise(() => {})
    })

    function Harness() {
      const state = useSeedExampleDetail('dataset:named:harmbench', 'example-1', false, 'gen-1')
      if (state.detail == null) return null
      return (
        <FluentProvider theme={webLightTheme}>
          <SeedExampleDetail detail={state.detail} onRefreshUrls={state.refreshDisplayUrls} />
        </FluentProvider>
      )
    }

    render(<Harness />)
    await user.click(await screen.findByRole('button', { name: 'Show image preview' }))
    fireEvent.error(screen.getByAltText('picture'))
    await user.click(screen.getByRole('button', { name: 'Retry image' }))
    await user.click(screen.getByRole('button', { name: 'Show audio preview' }))
    fireEvent.error(screen.getByTestId('audio-player'))
    await user.click(screen.getByRole('button', { name: 'Retry audio' }))
    await act(async () => {
      rejectImage(new Error('unavailable'))
    })
    expect(await screen.findByTestId('image-refresh-error')).toHaveTextContent("Couldn't refresh the link.")
    expect(screen.queryByTestId('audio-refresh-error')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry audio' })).toBeInTheDocument()
    expect(screen.queryByAltText('picture')).not.toBeInTheDocument()
  })
})
