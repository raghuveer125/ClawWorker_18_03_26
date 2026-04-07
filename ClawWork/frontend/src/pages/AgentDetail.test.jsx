import { render, screen, waitFor, act } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DisplayNamesContext } from '../DisplayNamesContext'
import AgentDetail from './AgentDetail'
import {
  fetchAgentDashboardSupplemental,
  fetchAgentDetail,
  fetchAgentEconomic,
  fetchAgentTasks,
} from '../api'

vi.mock('../api', () => ({
  fetchAgentDetail: vi.fn(),
  fetchAgentEconomic: vi.fn(),
  fetchAgentTasks: vi.fn(),
  fetchAgentDashboardSupplemental: vi.fn(),
}))

const renderAgentRoute = ({
  route = '/agent/stock-agent',
  onRouteAgentSelected = vi.fn(),
  displayNames = { 'stock-agent': 'Stock Agent' },
} = {}) => render(
  <DisplayNamesContext.Provider value={displayNames}>
    <MemoryRouter
      initialEntries={[route]}
      future={{
        v7_startTransition: true,
        v7_relativeSplatPath: true,
      }}
    >
      <Routes>
        <Route
          path="/agent/:signature"
          element={<AgentDetail onRouteAgentSelected={onRouteAgentSelected} />}
        />
      </Routes>
    </MemoryRouter>
  </DisplayNamesContext.Provider>
)

describe('AgentDetail invalid worker regression', () => {
  const flushDashboardLoad = async () => {
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })
  }

  beforeEach(() => {
    fetchAgentDetail.mockRejectedValue(new Error('404'))
    fetchAgentEconomic.mockResolvedValue({})
    fetchAgentTasks.mockResolvedValue({ tasks: [] })
    fetchAgentDashboardSupplemental.mockResolvedValue({})
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders the unavailable worker state for direct invalid routes', async () => {
    renderAgentRoute()

    expect(await screen.findByRole('heading', { name: 'Worker Unavailable' })).toBeInTheDocument()
    expect(screen.getByText(/Stock Agent is not an active worker in this dashboard/i)).toBeInTheDocument()
    expect(fetchAgentDetail).toHaveBeenCalledWith('stock-agent')
  })

  it('stops supplemental polling after the initial 404 response', async () => {
    vi.useFakeTimers()

    renderAgentRoute()

    await flushDashboardLoad()

    expect(screen.getByRole('heading', { name: 'Worker Unavailable' })).toBeInTheDocument()
    expect(fetchAgentDashboardSupplemental).toHaveBeenCalledTimes(1)

    await act(async () => {
      vi.advanceTimersByTime(16000)
      await Promise.resolve()
    })

    expect(fetchAgentDashboardSupplemental).toHaveBeenCalledTimes(1)
  })

  it('forwards the decoded route signature to the parent selection callback', async () => {
    const onRouteAgentSelected = vi.fn()

    renderAgentRoute({
      route: '/agent/stock%20agent',
      onRouteAgentSelected,
      displayNames: { 'stock agent': 'Stock Agent' },
    })

    await waitFor(() => {
      expect(onRouteAgentSelected).toHaveBeenCalledWith('stock agent')
    })
    expect(fetchAgentDetail).toHaveBeenCalledWith('stock agent')
  })
})
