import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Message } from '../../types/api'
import { ChatPanel } from './ChatPanel'

vi.mock('../../services/api', () => ({
  api: {
    health: async () => ({
      tts: { provider: 'sarvam', model: 'bulbul:v2', speaker: 'anushka', enabled: true },
      simulated: { tts: false },
    }),
    speechUrl: (id: string) => `/api/voice/messages/${id}/speech`,
    sendMessage: vi.fn(),
    createCase: vi.fn(),
  },
}))

function message(over: Partial<Message> & Pick<Message, 'id' | 'direction'>): Message {
  return {
    channel: 'CHAT',
    sender: over.direction === 'INBOUND' ? 'MERCHANT' : 'SAARTHI',
    content: 'Settlement for TXN18293 is still pending.',
    status: 'SENT',
    created_at: '2026-09-19T10:00:00+00:00',
    ...over,
  } as Message
}

function renderPanel(messages: Message[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel caseId="CASE-18293" messages={messages} />
    </QueryClientProvider>,
  )
}

describe('ChatPanel', () => {
  it('offers to play what Saarthi said, but never the merchant', async () => {
    renderPanel([
      message({ id: 'MSG-1', direction: 'INBOUND', content: 'Where is my settlement?' }),
      message({ id: 'MSG-2', direction: 'OUTBOUND' }),
    ])

    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /play this message/i })).toHaveLength(1),
    )
  })

  it('does not speak a conversation that was already on screen', async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play')
    renderPanel([
      message({ id: 'MSG-1', direction: 'INBOUND', channel: 'VOICE' }),
      message({ id: 'MSG-2', direction: 'OUTBOUND' }),
    ])

    // Opening an old case is silent; only replies that actually arrive speak.
    await waitFor(() => expect(screen.getByText(/Merchant conversation/i)).toBeInTheDocument())
    expect(play).not.toHaveBeenCalled()
  })

  it('speaks a reply that arrives after a spoken question', async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, 'play')
    const asked = [message({ id: 'MSG-1', direction: 'INBOUND', channel: 'VOICE' })]
    const { rerender } = renderPanel(asked)

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    rerender(
      <QueryClientProvider client={client}>
        <ChatPanel
          caseId="CASE-18293"
          messages={[...asked, message({ id: 'MSG-2', direction: 'OUTBOUND' })]}
        />
      </QueryClientProvider>,
    )

    await waitFor(() => expect(play).toHaveBeenCalled())
    expect(screen.getByText(/speaking/i)).toBeInTheDocument()
  })
})
