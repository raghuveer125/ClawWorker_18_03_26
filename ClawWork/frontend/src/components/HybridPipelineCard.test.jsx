import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import HybridPipelineCard from './HybridPipelineCard'
import {
  analyzeHybridPipeline,
  fetchExpirySchedule,
  fetchHybridPipelineStatus,
  fetchLiveMarketData,
  setHybridModuleWeight,
  toggleHybridModule,
} from '../api'
import { useMarketWebSocket } from '../hooks/useMarketWebSocket'

vi.mock('../api', () => ({
  fetchExpirySchedule: vi.fn(),
  fetchHybridPipelineStatus: vi.fn(),
  fetchLiveMarketData: vi.fn(),
  analyzeHybridPipeline: vi.fn(),
  setHybridModuleWeight: vi.fn(),
  toggleHybridModule: vi.fn(),
}))

vi.mock('../hooks/useMarketWebSocket', () => ({
  useMarketWebSocket: vi.fn(),
}))

const exchangeSchedule = {
  expirySchedule: {
    SENSEX: {
      exchange: 'BSE',
      source: 'exchange',
      next_expiry: '2026-03-19',
      weekday: 3,
      weekday_name: 'Thursday',
      weekday_short: 'Thu',
      is_expiry_today: true,
    },
    NIFTY50: {
      exchange: 'NSE',
      source: 'exchange',
      next_expiry: '2026-03-24',
      weekday: 1,
      weekday_name: 'Tuesday',
      weekday_short: 'Tue',
      is_expiry_today: false,
    },
    BANKNIFTY: {
      exchange: 'NSE',
      source: 'exchange',
      next_expiry: '2026-03-25',
      weekday: 2,
      weekday_name: 'Wednesday',
      weekday_short: 'Wed',
      is_expiry_today: false,
    },
    FINNIFTY: {
      exchange: 'NSE',
      source: 'exchange',
      next_expiry: '2026-03-24',
      weekday: 1,
      weekday_name: 'Tuesday',
      weekday_short: 'Tue',
      is_expiry_today: false,
    },
  },
  todaysExpiry: ['SENSEX'],
  sourceStatus: 'exchange',
  indices: ['SENSEX', 'NIFTY50', 'BANKNIFTY', 'FINNIFTY'],
}

const unavailableSchedule = {
  expirySchedule: {
    SENSEX: {
      exchange: 'BSE',
      source: 'unavailable',
      next_expiry: null,
      weekday: null,
      weekday_name: null,
      weekday_short: null,
      is_expiry_today: false,
    },
    NIFTY50: {
      exchange: 'NSE',
      source: 'unavailable',
      next_expiry: null,
      weekday: null,
      weekday_name: null,
      weekday_short: null,
      is_expiry_today: false,
    },
  },
  todaysExpiry: [],
  sourceStatus: 'unavailable',
  indices: ['SENSEX', 'NIFTY50'],
}

const upcomingSchedule = {
  expirySchedule: {
    SENSEX: {
      exchange: 'BSE',
      source: 'exchange',
      next_expiry: '2026-03-26',
      weekday: 3,
      weekday_name: 'Thursday',
      weekday_short: 'Thu',
      is_expiry_today: false,
    },
    NIFTY50: {
      exchange: 'NSE',
      source: 'exchange',
      next_expiry: '2026-03-24',
      weekday: 1,
      weekday_name: 'Tuesday',
      weekday_short: 'Tue',
      is_expiry_today: false,
    },
    BANKNIFTY: {
      exchange: 'NSE',
      source: 'exchange',
      next_expiry: '2026-03-25',
      weekday: 2,
      weekday_name: 'Wednesday',
      weekday_short: 'Wed',
      is_expiry_today: false,
    },
  },
  todaysExpiry: [],
  sourceStatus: 'exchange',
  indices: ['SENSEX', 'NIFTY50', 'BANKNIFTY'],
}

const fyersFallbackSchedule = {
  expirySchedule: {
    SENSEX: {
      exchange: 'BSE',
      source: 'fyers',
      next_expiry: '2026-03-19',
      weekday: 3,
      weekday_name: 'Thursday',
      weekday_short: 'Thu',
      is_expiry_today: true,
    },
    NIFTY50: {
      exchange: 'NSE',
      source: 'fyers',
      next_expiry: '2026-03-24',
      weekday: 1,
      weekday_name: 'Tuesday',
      weekday_short: 'Tue',
      is_expiry_today: false,
    },
  },
  todaysExpiry: ['SENSEX'],
  sourceStatus: 'fyers',
  indices: ['SENSEX', 'NIFTY50'],
}

const liveMarketResponse = {
  indices: {
    SENSEX: { ltp: 80000 },
    NIFTY50: { ltp: 23000 },
    BANKNIFTY: { ltp: 50000 },
    FINNIFTY: { ltp: 25000 },
  },
  market_open: true,
}

const hybridStatus = {
  available: true,
  modules: {},
  stats: {},
}

const renderCard = () => render(<HybridPipelineCard marketData={{}} />)

describe('HybridPipelineCard expiry regressions', () => {
  beforeEach(() => {
    useMarketWebSocket.mockReturnValue({ lastMessage: null, connectionStatus: 'idle' })
    fetchHybridPipelineStatus.mockResolvedValue(hybridStatus)
    fetchLiveMarketData.mockResolvedValue(liveMarketResponse)
    fetchExpirySchedule.mockResolvedValue(exchangeSchedule)
    analyzeHybridPipeline.mockResolvedValue({ modules: {}, confidence: 50, regime: 'HIGH_VOLATILITY' })
    setHybridModuleWeight.mockResolvedValue({})
    toggleHybridModule.mockResolvedValue({})
  })

  it('shows SENSEX as the active expiry and auto-selects it on first load', async () => {
    renderCard()

    expect(await screen.findByText('Analyze SENSEX')).toBeInTheDocument()
    expect(screen.getByText('SENSEX EXP')).toBeInTheDocument()

    const sensexRow = screen.getByText('SENSEX').closest('tr')
    expect(sensexRow).not.toBeNull()
    expect(within(sensexRow).getByText('EXP')).toBeInTheDocument()
  })

  it('shows an unavailable note instead of expiry badges when exchange data is missing', async () => {
    fetchExpirySchedule.mockResolvedValueOnce(unavailableSchedule)

    renderCard()

    expect(await screen.findByText('Analyze NIFTY50')).toBeInTheDocument()
    expect(screen.getByText('Expiry data unavailable from exchange or FYERS')).toBeInTheDocument()
    expect(screen.getByText('SEN-NA')).toBeInTheDocument()
    expect(screen.getByText('NIF-NA')).toBeInTheDocument()
    expect(screen.queryByText('EXPIRY TODAY')).not.toBeInTheDocument()
    expect(screen.queryByText('SENSEX EXP')).not.toBeInTheDocument()
  })

  it('treats FYERS expiry dates as usable fallback data', async () => {
    fetchExpirySchedule.mockResolvedValueOnce(fyersFallbackSchedule)

    renderCard()

    expect(await screen.findByText('Analyze SENSEX')).toBeInTheDocument()
    expect(screen.getByText('SENSEX EXP')).toBeInTheDocument()
    expect(screen.getByText('Using FYERS expiry dates')).toBeInTheDocument()
    expect(screen.getByText('NIF-Tue')).toBeInTheDocument()
  })

  it('shows upcoming expiry information when no index is expiring today', async () => {
    fetchExpirySchedule.mockResolvedValueOnce(upcomingSchedule)

    renderCard()

    expect(await screen.findByText('Analyze NIFTY50')).toBeInTheDocument()
    expect(screen.getByText('NIFTY50 24 Mar NEXT')).toBeInTheDocument()
    expect(screen.getByText('NEXT 24 Mar')).toBeInTheDocument()

    const niftyRow = screen.getByText('NIFTY50').closest('tr')
    expect(niftyRow).not.toBeNull()
    expect(within(niftyRow).getByText('NEXT 24 Mar')).toBeInTheDocument()
  })

  it('keeps a manual index selection after a later refresh', async () => {
    renderCard()

    expect(await screen.findByText('Analyze SENSEX')).toBeInTheDocument()

    fireEvent.click(screen.getByText('NIFTY50'))
    expect(screen.getByText('Analyze NIFTY50')).toBeInTheDocument()

    fetchExpirySchedule.mockResolvedValueOnce(exchangeSchedule)
    fetchLiveMarketData.mockResolvedValueOnce(liveMarketResponse)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /refresh live/i }))
    })

    await waitFor(() => {
      expect(screen.getByText('Analyze NIFTY50')).toBeInTheDocument()
    })
  })
})
