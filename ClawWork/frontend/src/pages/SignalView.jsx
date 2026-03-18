import { useState, useEffect, useCallback } from 'react'
import { RefreshCw, Calendar, ChevronDown } from 'lucide-react'
import { fetchFyersN7Dates, fetchFyersN7Snapshot, fetchIndicesConfig } from '../api'

// Fallback indices if config fetch fails
const DEFAULT_INDICES = ['SENSEX', 'NIFTY50', 'BANKNIFTY', 'FINNIFTY']

const MERGED_COLS = [
  'Time', 'Side', 'Strike', 'StrPCR', 'Level', 'Entry', 'SL', 'T1', 'T2', 'Conf',
  'Status', 'Score', 'Action', 'Stable', 'CooldownS', 'EntryReady', 'Selected',
  'Bid', 'Ask', 'Spr%', 'IV%', 'Delta', 'Gamma', 'ThetaD', 'Decay%',
  'VoteCE', 'VotePE', 'VoteSide', 'VoteDiff', 'LearnP', 'LearnGate', 'VolDom', 'VolSwitch', 'Note'
]

const EVENT_COLS = ['Time', 'Type', 'Side', 'Strike', 'Entry', 'Exit', 'Reason']
const PAPER_TRADE_COLS = ['ID', 'Time', 'Side', 'Strike', 'Qty', 'Entry', 'Exit', 'Reason', 'P&L', 'Hold', 'Result']

// Safe string coercion for Y/N or boolean fields
const isYes = (val) => {
  if (val === true || val === 1) return true
  if (typeof val === 'string') return val.toUpperCase() === 'Y'
  return false
}

// Safe string conversion
const toStr = (val) => (val == null ? '' : String(val))

// PCR sentiment helper
const getPcrSentiment = (pcr) => {
  if (pcr >= 1.5) return { text: 'Very Bullish', className: 'pcr-bull-strong' }
  if (pcr >= 1.2) return { text: 'Bullish', className: 'pcr-bull' }
  if (pcr >= 0.8) return { text: 'Neutral', className: 'pcr-neutral' }
  if (pcr >= 0.5) return { text: 'Bearish', className: 'pcr-bear' }
  return { text: 'Very Bearish', className: 'pcr-bear-strong' }
}

// PCR level label
const getPcrLevelLabel = (pcr) => {
  if (pcr >= 5.0) return 'SUP++'
  if (pcr >= 2.0) return 'SUP+'
  if (pcr >= 1.2) return 'SUP'
  if (pcr >= 0.8) return 'NEUT'
  if (pcr >= 0.5) return 'RES'
  if (pcr >= 0.2) return 'RES+'
  return 'RES++'
}

// Session status helper
const getSessionStatus = () => {
  const now = new Date()
  const h = now.getHours()
  const m = now.getMinutes()
  if (h < 9 || (h === 9 && m < 15)) return { text: 'PRE-MARKET', className: 'session-pre' }
  if (h < 15 || (h === 15 && m <= 30)) return { text: 'ACTIVE', className: 'session-active' }
  return { text: 'CLOSED', className: 'session-closed' }
}

// Row class determination
const getRowClasses = (signal) => {
  const classes = []
  const status = toStr(signal.status).toUpperCase()

  if (status === 'APPROVED') classes.push('row-approved')
  else if (status === 'PREFILTER') classes.push('row-prefilter')

  const entryReady = isYes(signal.entry_ready)
  if (entryReady && status === 'APPROVED') classes.push('entry-ready')

  if (isYes(signal.vol_switch)) classes.push('vol-switch')

  // Note: fyersN7 spread_pct values are higher than typical (basis points scale)
  const spread = parseFloat(signal.spread_pct || 0)
  if (spread > 5.0) classes.push('high-spread')

  const decay = parseFloat(signal.decay_pct || 0)
  if (decay > 25.0) classes.push('high-decay')

  const voteDiff = parseFloat(signal.vote_diff || 0)
  if (voteDiff >= 7) classes.push('strong-conviction')

  return classes.join(' ')
}

// Format number helper
const fmtNum = (v, digits, blankIfNonPositive = false) => {
  const x = parseFloat(v) || 0
  if (blankIfNonPositive && x <= 0) return ''
  return x.toFixed(digits)
}

// Map signal row to merged columns
const signalToMergedRow = (r) => ({
  Time: toStr(r.time).slice(-8),
  Side: toStr(r.side),
  Strike: toStr(r.strike),
  StrPCR: fmtNum(r.strike_pcr, 2),
  Level: getPcrLevelLabel(parseFloat(r.strike_pcr || 0)),
  Entry: toStr(r.entry),
  SL: toStr(r.sl),
  T1: toStr(r.t1),
  T2: toStr(r.t2),
  Conf: toStr(r.confidence),
  Status: toStr(r.status),
  Score: toStr(r.score),
  Action: toStr(r.action),
  Stable: toStr(r.stable),
  CooldownS: toStr(r.cooldown_sec),
  EntryReady: isYes(r.entry_ready) ? 'Y' : (r.entry_ready === false ? 'N' : toStr(r.entry_ready)),
  Selected: isYes(r.selected) ? 'Y' : (r.selected === false ? 'N' : toStr(r.selected)),
  Bid: fmtNum(r.bid, 2, true),
  Ask: fmtNum(r.ask, 2, true),
  'Spr%': fmtNum(r.spread_pct, 2),
  'IV%': fmtNum(r.iv, 2),
  Delta: fmtNum(r.delta, 3),
  Gamma: fmtNum(r.gamma, 5),
  ThetaD: fmtNum(r.theta_day, 3),
  'Decay%': fmtNum(r.decay_pct, 2),
  VoteCE: toStr(r.vote_ce),
  VotePE: toStr(r.vote_pe),
  VoteSide: toStr(r.vote_side),
  VoteDiff: toStr(r.vote_diff),
  LearnP: fmtNum(r.learn_prob, 2, true),
  LearnGate: toStr(r.learn_gate),
  VolDom: toStr(r.vol_dom),
  VolSwitch: isYes(r.vol_switch) ? 'Y' : (r.vol_switch === false ? 'N' : toStr(r.vol_switch)),
  Note: toStr(r.reason)
})

// Map trade row to display
const tradeToRow = (r) => {
  const netPnl = parseFloat(r.net_pnl || 0)
  const holdSec = parseInt(r.hold_sec || 0)
  return {
    ID: toStr(r.trade_id),
    Time: toStr(r.entry_time).slice(-8),
    Side: toStr(r.side),
    Strike: toStr(r.strike),
    Qty: toStr(r.qty),
    Entry: fmtNum(r.entry_price, 2),
    Exit: fmtNum(r.exit_price, 2),
    Reason: toStr(r.exit_reason),
    'P&L': netPnl !== 0 ? (netPnl > 0 ? '+' : '') + netPnl.toFixed(2) : '0.00',
    Hold: holdSec > 0 ? `${holdSec}s` : '-',
    Result: toStr(r.result)
  }
}

// Get trade row class
const getTradeRowClass = (trade) => {
  const result = toStr(trade.result).toLowerCase()
  const netPnl = parseFloat(trade.net_pnl || 0)
  if (result === 'win' || netPnl > 0) return 'trade-win'
  if (result === 'loss' || netPnl < 0) return 'trade-loss'
  return 'trade-breakeven'
}

// CSS styles - extracted outside component to prevent re-parsing
const STYLES = `
  .signal-view-container {
    padding: 18px;
    background: linear-gradient(180deg, #eef2ff 0%, #f5f7fb 60%);
    min-height: 100vh;
    font-family: "Avenir Next", "Segoe UI", sans-serif;
  }
  .shell {
    background: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 12px;
    padding: 16px;
    box-shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
  }
  h1 { margin: 0 0 8px; font-size: 24px; color: #111827; }
  h2 { margin: 16px 0 8px; font-size: 20px; border-bottom: 2px solid #d1d5db; padding-bottom: 8px; color: #111827; }
  h3 { margin: 12px 0 6px; font-size: 15px; color: #0f172a; }
  .market-context {
    background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);
    border: 1px solid #bae6fd;
    border-radius: 10px;
    padding: 12px 16px;
    margin-bottom: 12px;
  }
  .context-row { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 8px; }
  .context-row:last-child { margin-bottom: 0; }
  .ctx-item { display: flex; flex-direction: column; min-width: 100px; }
  .ctx-item-wide { min-width: 180px; }
  .ctx-label { font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 2px; }
  .ctx-value { font-size: 15px; font-weight: 600; color: #0f172a; }
  .ctx-value small { font-weight: 400; color: #64748b; font-size: 12px; }
  .pcr-bull-strong { color: #16a34a; }
  .pcr-bull { color: #22c55e; }
  .pcr-neutral { color: #64748b; }
  .pcr-bear { color: #f97316; }
  .pcr-bear-strong { color: #dc2626; }
  .session-active { color: #16a34a; font-weight: 700; }
  .session-pre { color: #f59e0b; }
  .session-closed { color: #94a3b8; }
  .basis-premium { color: #16a34a; }
  .basis-discount { color: #dc2626; }
  .expiry-badge { display: inline-block; background: #fef3c7; color: #92400e; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; margin-left: 6px; }
  .best-entry { color: #16a34a; font-weight: 700; background: #dcfce7; padding: 2px 8px; border-radius: 4px; }
  .no-entry { color: #94a3b8; }
  .approved-count { color: #16a34a; font-weight: 600; }
  .event-badge { display: inline-block; margin-left: 8px; padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; border: 1px solid transparent; }
  .event-badge-live { background: #dcfce7; color: #166534; border-color: #86efac; }
  .event-badge-idle { background: #f1f5f9; color: #475569; border-color: #cbd5e1; }
  .meta { margin: 0 0 10px; color: #6b7280; font-size: 13px; }
  .meta code { color: #0f766e; font-weight: 600; }
  .legend { font-size: 11px; }
  .legend-approved { background: #dcfce7; color: #16a34a; padding: 1px 6px; border-radius: 3px; }
  .legend-entry-ready { background: #dcfce7; border: 2px solid #16a34a; padding: 1px 6px; border-radius: 3px; }
  .legend-vol-switch { background: #fee2e2; color: #dc2626; padding: 1px 6px; border-radius: 3px; }
  .legend-high-spread { background: #ffedd5; color: #ea580c; padding: 1px 6px; border-radius: 3px; }
  .legend-high-decay { background: #fecaca; color: #b91c1c; padding: 1px 6px; border-radius: 3px; }
  .paper-summary { display: flex; flex-wrap: wrap; gap: 16px; background: linear-gradient(135deg, #fefce8 0%, #fef9c3 100%); border: 1px solid #fde047; border-radius: 10px; padding: 12px 16px; margin-bottom: 12px; }
  .paper-summary-empty { background: #f8fafc; border-color: #e2e8f0; }
  .summary-item { display: flex; flex-direction: column; min-width: 80px; }
  .summary-label { font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }
  .summary-value { font-size: 15px; font-weight: 600; color: #0f172a; }
  .summary-value small { font-weight: 400; color: #64748b; font-size: 12px; }
  .pnl-positive { color: #16a34a; }
  .pnl-negative { color: #dc2626; }
  tr.trade-win { background: #f0fdf4 !important; }
  tr.trade-loss { background: #fef2f2 !important; }
  tr.trade-breakeven { background: #f8fafc !important; }
  .table-wrap { overflow-x: auto; overflow-y: auto; max-height: 420px; border: 1px solid #d1d5db; border-radius: 10px; background: #fff; }
  table { border-collapse: collapse; width: max-content; min-width: 100%; font-size: 12px; }
  th, td { border: 1px solid #d1d5db; padding: 4px 8px; white-space: nowrap; text-align: left; }
  thead th { position: sticky; top: 0; background: #e5e7eb; z-index: 2; }
  tbody tr:nth-child(even) { background: #f9fafb; }
  tr.row-approved { background: #f0fdf4 !important; }
  tr.row-prefilter { opacity: 0.7; }
  tr.entry-ready { outline: 2px solid #16a34a; outline-offset: -2px; font-weight: 600; }
  tr.vol-switch { background: #fef2f2 !important; }
  tr.high-spread td { color: #ea580c; }
  tr.high-decay td { color: #b91c1c; }
  tr.strong-conviction { font-weight: 600; }
  .header-controls { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
  .header-controls select { appearance: none; background: #fff; border: 1px solid #d1d5db; border-radius: 8px; padding: 8px 32px 8px 12px; font-size: 14px; cursor: pointer; }
  .refresh-btn { padding: 8px; border-radius: 8px; border: none; cursor: pointer; }
  .refresh-btn.active { background: #dcfce7; color: #16a34a; }
  .refresh-btn.inactive { background: #f3f4f6; color: #6b7280; }
  .refresh-btn svg { width: 20px; height: 20px; }
  .refresh-btn.active svg { animation: spin 3s linear infinite; }
  @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
  section { margin-bottom: 24px; }
`

// Get only the latest batch of signals (same date+time as most recent)
// Also filter to only CE/PE signals with strikes (matches original behavior)
// Deduplicates by strike+side to avoid duplicate rows
const getLatestSignalBatch = (signals) => {
  if (!signals || signals.length === 0) return []

  // Filter to only CE/PE with strikes
  const validSignals = signals.filter(s => {
    const side = toStr(s.side).toUpperCase()
    const strike = toStr(s.strike)
    return (side === 'CE' || side === 'PE') && strike
  })

  if (validSignals.length === 0) return []

  // Find the latest timestamp
  const latest = validSignals[validSignals.length - 1]
  const latestDate = toStr(latest.date)
  const latestTime = toStr(latest.time)

  // Filter to matching timestamp
  const batchSignals = validSignals.filter(s =>
    toStr(s.date) === latestDate && toStr(s.time) === latestTime
  )

  // Deduplicate by strike+side (keep last occurrence)
  const seen = new Map()
  for (const sig of batchSignals) {
    const key = `${sig.strike}-${sig.side}`
    seen.set(key, sig)
  }
  return Array.from(seen.values())
}

export default function SignalView() {
  const [dates, setDates] = useState([])
  const [latestDate, setLatestDate] = useState(null)
  const [selectedDate, setSelectedDate] = useState(null)
  const [signals, setSignals] = useState({})
  const [trades, setTrades] = useState({})
  const [events, setEvents] = useState({})
  const [loading, setLoading] = useState(true)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [lastUpdate, setLastUpdate] = useState(null)
  const [indicesConfig, setIndicesConfig] = useState({ activeIndices: DEFAULT_INDICES, indices: {} })
  const [isDocumentVisible, setIsDocumentVisible] = useState(() => {
    if (typeof document === 'undefined') return true
    return document.visibilityState !== 'hidden'
  })

  // Fetch indices config on mount
  useEffect(() => {
    fetchIndicesConfig()
      .then(data => {
        if (data.activeIndices) {
          setIndicesConfig(data)
        }
      })
      .catch(err => console.error('Error fetching indices config:', err))
  }, [])

  // Fetch available dates
  useEffect(() => {
    fetchFyersN7Dates()
      .then(data => {
        setDates(data.dates || [])
        setLatestDate(data.latest || null)
        if (data.latest) {
          setSelectedDate(current => current || data.latest)
        }
      })
      .catch(err => console.error('Error fetching dates:', err))
  }, [])

  // Fetch all data
  const fetchData = useCallback(async () => {
    if (!selectedDate) return
    setLoading(true)
    try {
      const snapshot = await fetchFyersN7Snapshot(selectedDate, null, { latestOnly: true })

      setSignals(snapshot.signals || {})
      setTrades(snapshot.trades || {})
      setEvents(snapshot.events || {})
      setLastUpdate(new Date())
    } catch (err) {
      console.error('Error fetching signal data:', err)
    } finally {
      setLoading(false)
    }
  }, [selectedDate])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  useEffect(() => {
    if (typeof document === 'undefined') return undefined

    const handleVisibilityChange = () => {
      setIsDocumentVisible(document.visibilityState !== 'hidden')
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  }, [])

  // Auto-refresh every 15 seconds
  useEffect(() => {
    if (!autoRefresh || !isDocumentVisible || !selectedDate || selectedDate !== latestDate) return
    const interval = setInterval(fetchData, 15000)
    return () => clearInterval(interval)
  }, [autoRefresh, fetchData, isDocumentVisible, latestDate, selectedDate])

  const sessionStatus = getSessionStatus()
  const isLiveDateSelected = Boolean(selectedDate && latestDate && selectedDate === latestDate)
  const autoRefreshLabel = !autoRefresh
    ? 'OFF'
    : !isDocumentVisible
      ? 'PAUSED'
      : isLiveDateSelected
        ? '15s'
        : 'HISTORICAL'

  // Compute total paper trade summary (deduplicated)
  const computeTotalSummary = () => {
    let total = { total_trades: 0, wins: 0, losses: 0, total_pnl: 0, total_fees: 0, net_pnl: 0 }

    // Deduplicate by trade_id + index before counting
    const seen = new Map()
    Object.entries(trades).forEach(([idx, indexTrades]) => {
      indexTrades.forEach(t => {
        const key = `${idx}-${t.trade_id}`
        if (!seen.has(key)) {
          seen.set(key, t)
        }
      })
    })

    seen.forEach(t => {
      total.total_trades++
      const result = toStr(t.result).toLowerCase()
      if (result === 'win') total.wins++
      else if (result === 'loss') total.losses++
      total.total_pnl += parseFloat(t.gross_pnl || 0)
      total.total_fees += parseFloat(t.fees || 0)
      total.net_pnl += parseFloat(t.net_pnl || 0)
    })

    total.win_rate = total.total_trades > 0 ? (total.wins / total.total_trades * 100) : 0
    return total
  }

  const totalSummary = computeTotalSummary()

  // Get all trades sorted by time (newest first), deduplicated by trade_id+index
  const getAllTrades = () => {
    const all = []
    Object.entries(trades).forEach(([idx, indexTrades]) => {
      indexTrades.forEach(t => all.push({ ...t, _index: idx }))
    })

    // Deduplicate by trade_id + index (keep first occurrence for each unique trade)
    const seen = new Map()
    for (const trade of all) {
      const key = `${trade._index}-${trade.trade_id}`
      if (!seen.has(key)) {
        seen.set(key, trade)
      }
    }
    const deduplicated = Array.from(seen.values())

    return deduplicated.sort((a, b) => {
      const aTime = `${a.entry_date || ''} ${a.entry_time || ''}`
      const bTime = `${b.entry_date || ''} ${b.entry_time || ''}`
      return bTime.localeCompare(aTime)
    }).slice(0, 30)
  }

  return (
    <div className="signal-view-container">
      <style>{STYLES}</style>

      <div className="shell">
        <h1>Live Multi-Index Signal View</h1>

        {/* Header Controls */}
        <div className="header-controls">
          <div style={{ position: 'relative' }}>
            <select value={selectedDate || ''} onChange={e => setSelectedDate(e.target.value)}>
              {dates.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
            <Calendar style={{ position: 'absolute', right: 10, top: 10, width: 16, height: 16, color: '#9ca3af', pointerEvents: 'none' }} />
          </div>
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={`refresh-btn ${autoRefresh ? 'active' : 'inactive'}`}
            title={autoRefresh ? 'Auto-refresh ON (latest date only)' : 'Auto-refresh OFF'}
          >
            <RefreshCw />
          </button>
          <span className="meta" style={{ margin: 0 }}>
            {lastUpdate && `Last update: ${lastUpdate.toLocaleTimeString()}`}
          </span>
        </div>

        <p className="meta">
          Date: <code>{selectedDate || '-'}</code> |
          Source: <code>decision_journal.csv</code> |
          Auto-refresh: <code>{autoRefreshLabel}</code>
        </p>

        {loading && !Object.keys(signals).length ? (
          <p className="meta">Loading...</p>
        ) : (
          <>
            {/* Per-Index Sections */}
            {indicesConfig.activeIndices.filter(idx => {
              const signalPayload = signals[idx]
              return Array.isArray(signalPayload)
                ? signalPayload.length > 0
                : Number(signalPayload?.total_signals || 0) > 0
            }).map(idx => {
              const signalPayload = signals[idx] || {}
              const allSignals = Array.isArray(signalPayload) ? signalPayload : (signalPayload.rows || [])
              const indexSignals = Array.isArray(signalPayload) ? getLatestSignalBatch(allSignals) : allSignals
              const indexEvents = (events[idx] || []).slice(-20) // Limit to 20
              const latestSignal = Array.isArray(signalPayload)
                ? (allSignals[allSignals.length - 1] || {})
                : (signalPayload.latest_signal || {})

              // Extract market context from latest signal
              const spot = parseFloat(latestSignal.spot || 0)
              const vix = parseFloat(latestSignal.vix || 0)
              const netPcr = parseFloat(latestSignal.net_pcr || 0)
              const maxPain = parseInt(latestSignal.max_pain || 0)
              const maxPainDist = parseFloat(latestSignal.max_pain_dist || 0)
              const futBasis = parseFloat(latestSignal.fut_basis || 0)
              const futBasisPct = parseFloat(latestSignal.fut_basis_pct || 0)

              const pcrSentiment = getPcrSentiment(netPcr)
              const basisClass = futBasis > 0 ? 'basis-premium' : futBasis < 0 ? 'basis-discount' : ''
              const basisLabel = futBasis > 0 ? 'Premium' : futBasis < 0 ? 'Discount' : 'Flat'

              // Count approved and find best entry (from latest batch only)
              const approvedSignals = indexSignals.filter(s => toStr(s.status).toUpperCase() === 'APPROVED')
              const bestEntry = approvedSignals.find(s => isYes(s.entry_ready))
              const totalSignals = Array.isArray(signalPayload)
                ? allSignals.length
                : Number(signalPayload.total_signals || 0)

              const lastEventTime = indexEvents.length > 0 ?
                `${indexEvents[indexEvents.length-1].event_date || ''} ${indexEvents[indexEvents.length-1].event_time || ''}` : ''

              return (
                <section key={idx}>
                  <h2>
                    {idx}
                    <span className={lastEventTime ? 'event-badge event-badge-live' : 'event-badge event-badge-idle'}>
                      {lastEventTime ? `Last executed event: ${lastEventTime}` : 'No events yet'}
                    </span>
                  </h2>

                  {/* Market Context */}
                  <div className="market-context">
                    <div className="context-row">
                      <div className="ctx-item">
                        <span className="ctx-label">Spot</span>
                        <span className="ctx-value">{spot.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span>
                      </div>
                      <div className="ctx-item">
                        <span className="ctx-label">VIX</span>
                        <span className="ctx-value vix-value">{vix.toFixed(2)}</span>
                      </div>
                      <div className="ctx-item">
                        <span className="ctx-label">PCR</span>
                        <span className={`ctx-value ${pcrSentiment.className}`}>
                          {netPcr.toFixed(2)} <small>({pcrSentiment.text})</small>
                        </span>
                      </div>
                      <div className="ctx-item">
                        <span className="ctx-label">Max Pain</span>
                        <span className="ctx-value">
                          {maxPain.toLocaleString()} <small>({maxPainDist >= 0 ? '+' : ''}{maxPainDist.toFixed(0)})</small>
                        </span>
                      </div>
                      <div className="ctx-item">
                        <span className="ctx-label">Fut Basis</span>
                        <span className={`ctx-value ${basisClass}`}>
                          {futBasis >= 0 ? '+' : ''}{futBasis.toFixed(2)} <small>({futBasisPct >= 0 ? '+' : ''}{futBasisPct.toFixed(2)}% {basisLabel})</small>
                        </span>
                      </div>
                    </div>
                    <div className="context-row">
                      <div className="ctx-item">
                        <span className="ctx-label">Session</span>
                        <span className={`ctx-value ${sessionStatus.className}`}>{sessionStatus.text}</span>
                      </div>
                      <div className="ctx-item">
                        <span className="ctx-label">Expiry In</span>
                        <span className="ctx-value">{latestSignal.option_expiry || '-'}</span>
                      </div>
                      <div className="ctx-item">
                        <span className="ctx-label">Signals</span>
                        <span className="ctx-value">
                          {indexSignals.length} in batch ({totalSignals} total), <span className="approved-count">{approvedSignals.length} APPROVED</span>
                        </span>
                      </div>
                      <div className="ctx-item ctx-item-wide">
                        <span className="ctx-label">Best Current Signal</span>
                        {bestEntry ? (
                          <span className="best-entry">
                            {bestEntry.strike}{bestEntry.side} @{bestEntry.entry}
                          </span>
                        ) : (
                          <span className="no-entry">-</span>
                        )}
                      </div>
                    </div>
                  </div>

                  <p className="meta">
                    Latest batch: <code>{latestSignal.date || '-'} {latestSignal.time || '-'}</code>
                  </p>

                  <h3>Signal Table</h3>
                  <p className="meta">
                    <span className="legend">Legend:
                      <span className="legend-approved">APPROVED</span>{' '}
                      <span className="legend-entry-ready">Entry Ready</span>{' '}
                      <span className="legend-vol-switch">Vol Switch</span>{' '}
                      <span className="legend-high-spread">High Spread</span>{' '}
                      <span className="legend-high-decay">High Decay</span>
                    </span>
                  </p>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          {MERGED_COLS.map(col => <th key={col}>{col}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {indexSignals.length === 0 ? (
                          <tr><td colSpan={MERGED_COLS.length}>No signal rows available</td></tr>
                        ) : (
                          indexSignals.map((sig, i) => {
                            const mapped = signalToMergedRow(sig)
                            const rowClass = getRowClasses(sig)
                            return (
                              <tr key={i} className={rowClass}>
                                {MERGED_COLS.map(col => <td key={col}>{mapped[col]}</td>)}
                              </tr>
                            )
                          })
                        )}
                      </tbody>
                    </table>
                  </div>

                  <h3>Opportunity Events</h3>
                  <p className="meta">Recent executed events: {indexEvents.length}</p>
                  <p className="meta">Executed history can differ from the latest signal batch.</p>
                  <div className="table-wrap" style={{ maxHeight: 200 }}>
                    <table>
                      <thead>
                        <tr>
                          {EVENT_COLS.map(col => <th key={col}>{col}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {indexEvents.length === 0 ? (
                          <tr><td colSpan={EVENT_COLS.length}>No opportunity events yet</td></tr>
                        ) : (
                          indexEvents.map((evt, i) => (
                            <tr key={i}>
                              <td>{evt.event_time || ''}</td>
                              <td>{evt.event_type || ''}</td>
                              <td>{evt.side || ''}</td>
                              <td>{evt.strike || ''}</td>
                              <td>{evt.entry || ''}</td>
                              <td>{evt.exit || ''}</td>
                              <td>{evt.reason || ''}</td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>
                </section>
              )
            })}

            {/* Consolidated Paper Trading Section */}
            <section className="paper-section">
              <h2>Paper Trading (All Indices)</h2>

              {/* Summary */}
              {totalSummary.total_trades === 0 ? (
                <div className="paper-summary paper-summary-empty">
                  <span className="summary-label">Paper Trading</span>
                  <span className="summary-value">No trades yet</span>
                </div>
              ) : (
                <div className="paper-summary">
                  <div className="summary-item">
                    <span className="summary-label">Trades</span>
                    <span className="summary-value">{totalSummary.total_trades} <small>({totalSummary.wins}W / {totalSummary.losses}L)</small></span>
                  </div>
                  <div className="summary-item">
                    <span className="summary-label">Win Rate</span>
                    <span className="summary-value">{totalSummary.win_rate.toFixed(1)}%</span>
                  </div>
                  <div className="summary-item">
                    <span className="summary-label">Net P&L</span>
                    <span className={`summary-value ${totalSummary.net_pnl > 0 ? 'pnl-positive' : totalSummary.net_pnl < 0 ? 'pnl-negative' : ''}`}>
                      {totalSummary.net_pnl >= 0 ? '+' : ''}{totalSummary.net_pnl.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </span>
                  </div>
                  <div className="summary-item">
                    <span className="summary-label">Fees</span>
                    <span className="summary-value">{totalSummary.total_fees.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                  </div>
                </div>
              )}

              <p className="meta">Last 30 trades across all indices (newest first):</p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Index</th>
                      {PAPER_TRADE_COLS.map(col => <th key={col}>{col}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {getAllTrades().length === 0 ? (
                      <tr><td colSpan={PAPER_TRADE_COLS.length + 1}>No paper trades yet</td></tr>
                    ) : (
                      getAllTrades().map((trade, i) => {
                        const mapped = tradeToRow(trade)
                        const rowClass = getTradeRowClass(trade)
                        return (
                          <tr key={i} className={rowClass}>
                            <td>{trade._index}</td>
                            {PAPER_TRADE_COLS.map(col => <td key={col}>{mapped[col]}</td>)}
                          </tr>
                        )
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  )
}
