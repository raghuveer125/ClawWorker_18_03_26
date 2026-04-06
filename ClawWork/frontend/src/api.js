/**
 * API abstraction — switches between:
 *   live mode  : FastAPI backend at /api/... (local dev with Vite proxy)
 *   static mode: pre-generated JSON files at {BASE_URL}data/... (GitHub Pages)
 *
 * Set VITE_STATIC_DATA=true at build time to enable static mode.
 */

const STATIC = import.meta.env.VITE_STATIC_DATA === 'true'
const BASE_URL = import.meta.env.BASE_URL || '/'          // e.g. /-Live-Bench/
const FRONTEND_PORT = import.meta.env.VITE_FRONTEND_PORT || '3001'
const API_PORT = import.meta.env.VITE_API_PORT || '8001'
const API_ORIGIN = import.meta.env.VITE_API_ORIGIN || ''
const WS_ORIGIN = import.meta.env.VITE_WS_ORIGIN || ''

const staticUrl = (path) => `${BASE_URL}data/${path}`
const liveUrl = (path) => `/api/${path}`

const get = (url) => fetch(url).then(r => { if (!r.ok) throw new Error(r.status); return r.json() })

const trimTrailingSlash = (value) => value.replace(/\/$/, '')

const resolveBackendHttpOrigin = () => {
  if (API_ORIGIN) return trimTrailingSlash(API_ORIGIN)
  if (typeof window === 'undefined') return ''

  const { protocol, hostname, origin, port } = window.location
  if (port === FRONTEND_PORT) {
    return `${protocol}//${hostname}:${API_PORT}`
  }

  return origin
}

export const resolveWebSocketUrl = (path) => {
  if (typeof window === 'undefined') return path

  const baseOrigin = WS_ORIGIN
    ? trimTrailingSlash(WS_ORIGIN)
    : resolveBackendHttpOrigin().replace(/^http/, 'ws')

  return `${baseOrigin}${path}`
}

// ── Endpoints ─────────────────────────────────────────────────────────────────

export const fetchAgents = () =>
  get(STATIC ? staticUrl('agents.json') : liveUrl('agents'))

export const fetchLeaderboard = () =>
  get(STATIC ? staticUrl('leaderboard.json') : liveUrl('leaderboard'))

export const fetchAgentDetail = (sig) =>
  get(STATIC ? staticUrl(`agents/${encodeURIComponent(sig)}.json`) : liveUrl(`agents/${sig}`))

export const fetchAgentEconomic = (sig) =>
  get(STATIC ? staticUrl(`agents/${encodeURIComponent(sig)}/economic.json`) : liveUrl(`agents/${sig}/economic`))

export const fetchAgentTasks = (sig) =>
  get(STATIC ? staticUrl(`agents/${encodeURIComponent(sig)}/tasks.json`) : liveUrl(`agents/${sig}/tasks`))

export const fetchAgentLearning = (sig) =>
  get(STATIC ? staticUrl(`agents/${encodeURIComponent(sig)}/learning.json`) : liveUrl(`agents/${sig}/learning`))

export const fetchAgentLearningRoi = (sig) =>
  get(STATIC ? staticUrl(`agents/${encodeURIComponent(sig)}/learning-roi.json`) : liveUrl(`agents/${sig}/learning/roi`))

export const fetchHiddenAgents = () =>
  get(STATIC ? staticUrl('settings/hidden-agents.json') : liveUrl('settings/hidden-agents'))

export const fetchDisplayNames = () =>
  get(STATIC ? staticUrl('settings/displaying-names.json') : liveUrl('settings/displaying-names'))

export const fetchArtifacts = ({ count = 30, sort = 'recent' } = {}) =>
  get(STATIC ? staticUrl('artifacts.json') : liveUrl(`artifacts?count=${count}&sort=${encodeURIComponent(sort)}`))

export const fetchTerminalLog = (sig, date) =>
  get(STATIC
    ? staticUrl(`agents/${encodeURIComponent(sig)}/terminal-logs/${date}.json`)
    : liveUrl(`agents/${encodeURIComponent(sig)}/terminal-log/${date}`)
  )

export const fetchLatestFyersScreener = () =>
  get(STATIC ? staticUrl('fyers/screener-latest.json') : liveUrl('fyers/screener/latest'))

export const fetchLatestInstitutionalShadow = (sig) =>
  get(STATIC ? staticUrl(`agents/${encodeURIComponent(sig)}/institutional-shadow-latest.json`) : liveUrl(`agents/${sig}/institutional-shadow/latest`))

export const fetchAgentDashboardSupplemental = (sig) =>
  get(liveUrl(`agents/${sig}/dashboard-supplemental`))

// Institutional Trading Endpoints
export const fetchMarketSession = () =>
  get(liveUrl('institutional/market-session'))

export const fetchRiskConfig = () =>
  get(liveUrl('institutional/risk-config'))

export const validateTrade = (params) =>
  fetch(liveUrl(`institutional/validate-trade?${new URLSearchParams(params)}`), { method: 'POST' })
    .then(r => r.json())

export const calculatePositionSize = (index, entry, stopLoss) =>
  get(liveUrl(`institutional/position-size?index=${index}&entry=${entry}&stop_loss=${stopLoss}`))

// Multi-Bot Ensemble Endpoints
export const fetchBotsStatus = () =>
  get(liveUrl('bots/status'))

export const fetchBotsLeaderboard = () =>
  get(liveUrl('bots/leaderboard'))

export const fetchEnsembleStats = () =>
  get(liveUrl('bots/ensemble-stats'))

export const analyzeMarket = (index, marketData) =>
  fetch(liveUrl('bots/analyze'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index, market_data: marketData }),
  }).then(r => r.json())

export const analyzeAllIndices = (indicesData) =>
  fetch(liveUrl('bots/analyze-all'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ indices_data: indicesData }),
  }).then(r => r.json())

export const recordTradeOutcome = (index, exitPrice, outcome, pnl) =>
  fetch(liveUrl('bots/record-trade'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index, exit_price: exitPrice, outcome, pnl }),
  }).then(r => r.json())

export const fetchBotDetails = (botName) =>
  get(liveUrl(`bots/${botName}/details`))

export const fetchIctSniperStatus = () =>
  get(liveUrl('bots/ict-sniper/status'))

export const fetchRegimeHunterStatus = () =>
  get(liveUrl('bots/regime-hunter/status'))

// Regime Hunter Independent Pipeline
export const fetchRHPipelineStatus = () =>
  get(liveUrl('regime-hunter-pipeline/status'))

export const startRHPipeline = () =>
  fetch(liveUrl('regime-hunter-pipeline/start'), { method: 'POST' }).then(r => r.json())

export const stopRHPipeline = () =>
  fetch(liveUrl('regime-hunter-pipeline/stop'), { method: 'POST' }).then(r => r.json())

export const pauseRHPipeline = () =>
  fetch(liveUrl('regime-hunter-pipeline/pause'), { method: 'POST' }).then(r => r.json())

export const resumeRHPipeline = () =>
  fetch(liveUrl('regime-hunter-pipeline/resume'), { method: 'POST' }).then(r => r.json())

export const resetRHPipelineDaily = () =>
  fetch(liveUrl('regime-hunter-pipeline/reset-daily'), { method: 'POST' }).then(r => r.json())

// Index Expiry Schedule (from shared_project_engine)
export const fetchExpirySchedule = () =>
  get(liveUrl('indices/expiry-schedule'))

// Live Market Data (from FYERS API)
export const fetchLiveMarketData = () =>
  get(liveUrl('market/live'))

// Hybrid Regime Hunter Pipeline (Modular Architecture)
export const fetchHybridPipelineStatus = () =>
  get(liveUrl('hybrid-pipeline/status'))

export const analyzeHybridPipeline = (index, marketData, historicalData = null) =>
  fetch(liveUrl('hybrid-pipeline/analyze'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index, market_data: marketData, historical_data: historicalData }),
  }).then(r => r.json())

export const toggleHybridModule = (module, enabled) =>
  fetch(liveUrl('hybrid-pipeline/module/toggle'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ module, enabled }),
  }).then(r => r.json())

export const setHybridModuleWeight = (module, weight) =>
  fetch(liveUrl('hybrid-pipeline/module/weight'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ module, weight }),
  }).then(r => r.json())

export const configureHybridExpiry = () =>
  fetch(liveUrl('hybrid-pipeline/configure-expiry'), { method: 'POST' }).then(r => r.json())

export const configureHybridNormal = () =>
  fetch(liveUrl('hybrid-pipeline/configure-normal'), { method: 'POST' }).then(r => r.json())

// Auto-Trader Endpoints
export const fetchTradeHistory = () =>
  get(liveUrl('auto-trader/trades'))

export const fetchFyersn7TradeHistory = (date) =>
  get(liveUrl(`fyersn7/trades-flat/${date}`))

export const fetchAutoTraderStatus = () =>
  get(liveUrl('auto-trader/status'))

export const fetchAutoTraderPerformance = () =>
  get(liveUrl('auto-trader/performance'))

export const startAutoTrader = () =>
  fetch(liveUrl('auto-trader/start'), { method: 'POST' }).then(r => r.json())

export const stopAutoTrader = () =>
  fetch(liveUrl('auto-trader/stop'), { method: 'POST' }).then(r => r.json())

export const pauseAutoTrader = () =>
  fetch(liveUrl('auto-trader/pause'), { method: 'POST' }).then(r => r.json())

export const resumeAutoTrader = () =>
  fetch(liveUrl('auto-trader/resume'), { method: 'POST' }).then(r => r.json())

export const resetAutoTraderDaily = () =>
  fetch(liveUrl('auto-trader/reset-daily'), { method: 'POST' }).then(r => r.json())

export const fetchTradingMode = () =>
  get(liveUrl('auto-trader/trading-mode'))

export const toggleTradingMode = (mode = null) =>
  fetch(liveUrl('auto-trader/toggle-mode'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(mode ? { mode } : {}),
  }).then(r => r.json())

// Execution Quality & Go-Live Validation Endpoints
export const fetchExecutionQuality = (days = 10) =>
  get(liveUrl(`auto-trader/execution-quality?days=${days}`))

export const fetchGateStatus = () =>
  get(liveUrl('auto-trader/gates'))

export const validateGate = (gateName) =>
  get(liveUrl(`auto-trader/gate/${gateName}`))

export const generateDailySummary = (targetDate = null) =>
  fetch(liveUrl('auto-trader/generate-daily-summary'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(targetDate ? { target_date: targetDate } : {}),
  }).then(r => r.json())

/** Returns a URL that can be used directly in fetch() or as an iframe src */
export const getArtifactFileUrl = (path) =>
  STATIC
    ? `${BASE_URL}data/files/${path}`
    : `/api/artifacts/file?path=${encodeURIComponent(path)}`

// fyersN7 Signal View Endpoints
export const fetchFyersN7Dates = () =>
  get(liveUrl('fyersn7/dates'))

export const fetchFyersN7Signals = (date, index = null, options = {}) => {
  const params = new URLSearchParams()
  if (index) params.set('index', index)
  if (options.latestOnly) params.set('latest_only', 'true')
  const query = params.toString()
  return get(liveUrl(`fyersn7/signals/${date}${query ? `?${query}` : ''}`))
}

export const fetchFyersN7Trades = (date, index = null) =>
  get(liveUrl(`fyersn7/trades/${date}${index ? `?index=${index}` : ''}`))

export const fetchFyersN7Events = (date, index = null) =>
  get(liveUrl(`fyersn7/events/${date}${index ? `?index=${index}` : ''}`))

export const fetchFyersN7Snapshot = (date, index = null, options = {}) => {
  const params = new URLSearchParams()
  if (index) params.set('index', index)
  if (options.latestOnly) params.set('latest_only', 'true')
  const query = params.toString()
  return get(liveUrl(`fyersn7/snapshot/${date}${query ? `?${query}` : ''}`))
}

export const fetchFyersN7LiveSignals = (index) =>
  get(liveUrl(`fyersn7/live-signals/${encodeURIComponent(index)}`))

export const fetchFyersN7Summary = (date) =>
  get(liveUrl(`fyersn7/summary/${date}`))

// Centralized Indices Configuration
export const fetchIndicesConfig = () =>
  get(liveUrl('indices/config'))

/** No-op in static mode (can't persist state to GitHub Pages) */
export const saveHiddenAgents = (hiddenArray) => {
  if (STATIC) return Promise.resolve()
  return fetch('/api/settings/hidden-agents', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hidden: hiddenArray }),
  })
}

// ── Lottery Pipeline ──────────────────────────────────────────────────────────

export const fetchLotteryStatus = (symbol = 'NIFTY') =>
  get(liveUrl(`lottery/status?symbol=${symbol}`))

export const fetchLotteryConfig = (symbol = 'NIFTY') =>
  get(liveUrl(`lottery/config?symbol=${symbol}`))

export const fetchLotteryRawData = (symbol = 'NIFTY') =>
  get(liveUrl(`lottery/raw-data?symbol=${symbol}`))

export const fetchLotteryFormulaAudit = (symbol = 'NIFTY') =>
  get(liveUrl(`lottery/formula-audit?symbol=${symbol}`))

export const fetchLotteryQuality = (symbol = 'NIFTY') =>
  get(liveUrl(`lottery/quality?symbol=${symbol}`))

export const fetchLotterySignals = (symbol = 'NIFTY', limit = 50) =>
  get(liveUrl(`lottery/signals?symbol=${symbol}&limit=${limit}`))

export const fetchLotteryTrades = (symbol = 'NIFTY', limit = 50) =>
  get(liveUrl(`lottery/trades?symbol=${symbol}&limit=${limit}`))

export const fetchLotteryCapital = (symbol = 'NIFTY') =>
  get(liveUrl(`lottery/capital?symbol=${symbol}`))

export const fetchLotteryCandidates = (symbol = 'NIFTY') =>
  get(liveUrl(`lottery/candidates?symbol=${symbol}`))

export const fetchLotteryRejections = (symbol = 'NIFTY', limit = 20) =>
  get(liveUrl(`lottery/rejections?symbol=${symbol}&limit=${limit}`))

export const IS_STATIC = STATIC
