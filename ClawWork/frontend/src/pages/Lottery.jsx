import { useCallback, useEffect, useState } from 'react'
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowUpRight,
  CheckCircle, Clock, RefreshCw, Shield,
  Ticket, TrendingUp, XCircle, Zap
} from 'lucide-react'
import {
  fetchLotteryStatus, fetchLotteryRawData, fetchLotteryFormulaAudit,
  fetchLotteryQuality, fetchLotterySignals, fetchLotteryTrades,
  fetchLotteryCapital, fetchLotteryCandidates, fetchLotteryRejections,
} from '../api'

const formatINR = (v) => `\u20B9${(v || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const toLocalTime = (isoStr) => {
  if (!isoStr) return '-'
  try { return new Date(isoStr).toLocaleTimeString('en-IN', { hour12: false }) }
  catch { return isoStr?.slice(11, 19) || '-' }
}
const toLocalDateTime = (isoStr) => {
  if (!isoStr) return '-'
  try {
    const d = new Date(isoStr)
    return `${d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })} ${d.toLocaleTimeString('en-IN', { hour12: false })}`
  } catch { return isoStr?.slice(0, 19) || '-' }
}

// ── Light Theme Status Pill ──────────────────────────────────────────────────
const StatusPill = ({ status }) => {
  const colors = {
    PASS: 'bg-green-100 text-green-800 border-green-300',
    WARN: 'bg-amber-100 text-amber-800 border-amber-300',
    FAIL: 'bg-red-100 text-red-800 border-red-300',
    IDLE: 'bg-gray-100 text-gray-600 border-gray-300',
    ZONE_ACTIVE_CE: 'bg-emerald-100 text-emerald-800 border-emerald-300',
    ZONE_ACTIVE_PE: 'bg-rose-100 text-rose-800 border-rose-300',
    CANDIDATE_FOUND: 'bg-amber-100 text-amber-800 border-amber-300',
    IN_TRADE: 'bg-blue-100 text-blue-800 border-blue-300',
    COOLDOWN: 'bg-purple-100 text-purple-800 border-purple-300',
    VALID: 'bg-green-100 text-green-800 border-green-300',
    INVALID: 'bg-gray-100 text-gray-500 border-gray-300',
    OPEN: 'bg-blue-100 text-blue-800 border-blue-300',
    CLOSED: 'bg-gray-100 text-gray-600 border-gray-300',
    CE: 'bg-emerald-100 text-emerald-800 border-emerald-300',
    PE: 'bg-rose-100 text-rose-800 border-rose-300',
  }
  const cls = colors[status] || 'bg-gray-100 text-gray-500 border-gray-300'
  return <span className={`px-2 py-0.5 rounded text-xs font-semibold border ${cls}`}>{status}</span>
}

const TABS = ['Status', 'Raw Data', 'Formula Audit', 'Quality', 'Signals', 'Trades', 'Capital']

function Lottery() {
  const [symbol, setSymbol] = useState('NIFTY')
  const [tab, setTab] = useState('Status')
  const [status, setStatus] = useState(null)
  const [rawData, setRawData] = useState(null)
  const [audit, setAudit] = useState(null)
  const [quality, setQuality] = useState(null)
  const [signals, setSignals] = useState(null)
  const [trades, setTrades] = useState(null)
  const [capital, setCapital] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setStatus(null); setRawData(null); setAudit(null); setQuality(null)
    setSignals(null); setTrades(null); setCapital(null)
  }, [symbol])

  const fetchTab = useCallback(async () => {
    setLoading(true)
    try {
      if (tab === 'Status') setStatus(await fetchLotteryStatus(symbol))
      else if (tab === 'Raw Data') setRawData(await fetchLotteryRawData(symbol))
      else if (tab === 'Formula Audit') setAudit(await fetchLotteryFormulaAudit(symbol))
      else if (tab === 'Quality') setQuality(await fetchLotteryQuality(symbol))
      else if (tab === 'Signals') setSignals(await fetchLotterySignals(symbol))
      else if (tab === 'Trades') setTrades(await fetchLotteryTrades(symbol))
      else if (tab === 'Capital') setCapital(await fetchLotteryCapital(symbol))
    } catch {}
    setLoading(false)
  }, [tab, symbol])

  useEffect(() => { fetchTab() }, [fetchTab])
  useEffect(() => {
    const interval = setInterval(fetchTab, 2000)
    return () => clearInterval(interval)
  }, [fetchTab])

  return (
    <div className="p-4 md:p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="p-2.5 rounded-lg bg-gradient-to-br from-amber-500 to-orange-600 shadow-md">
            <Ticket className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-gray-900">Lottery Strike Picker</h1>
            <p className="text-gray-500 text-xs">Far-OTM premium band strategy</p>
          </div>
          <div className="flex items-center space-x-1.5 bg-red-50 border border-red-200 rounded-full px-2.5 py-0.5">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-red-500" />
            </span>
            <span className="text-[10px] font-bold tracking-widest text-red-600 uppercase">Live</span>
          </div>
        </div>
        <div className="flex items-center space-x-2">
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}
            className="bg-white border border-gray-300 text-gray-800 rounded-lg px-3 py-1.5 text-sm shadow-sm focus:ring-2 focus:ring-amber-400 focus:border-amber-400">
            <option value="NIFTY">NIFTY</option>
            <option value="BANKNIFTY">BANKNIFTY</option>
            <option value="FINNIFTY">FINNIFTY</option>
            <option value="SENSEX">SENSEX</option>
          </select>
          <button onClick={fetchTab} className="p-2 rounded-lg bg-white border border-gray-300 hover:bg-gray-50 text-gray-500 shadow-sm">
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Status Cards */}
      {status?.data && <StatusCards data={status.data} />}

      {/* Tabs */}
      <div className="flex space-x-1 bg-gray-100 rounded-lg p-1">
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
              tab === t ? 'bg-white text-amber-700 shadow-sm font-semibold' : 'text-gray-500 hover:text-gray-800 hover:bg-white/50'
            }`}>
            {t}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="bg-white border border-gray-200 rounded-xl shadow-sm">
        <div className="p-4">
          {tab === 'Status' && <StatusTab data={status?.data} />}
          {tab === 'Raw Data' && <RawDataTab data={rawData?.data} />}
          {tab === 'Formula Audit' && <FormulaAuditTab data={audit?.data} />}
          {tab === 'Quality' && <QualityTab data={quality?.data} />}
          {tab === 'Signals' && <SignalsTab data={signals?.data} />}
          {tab === 'Trades' && <TradesTab data={trades?.data} />}
          {tab === 'Capital' && <CapitalTab data={capital?.data} />}
        </div>
      </div>
    </div>
  )
}

// ── Status Cards ──────────────────────────────────────────────────────────────

function StatusCards({ data }) {
  const cards = [
    { label: 'State', value: data?.state || 'N/A', icon: Activity, bg: 'bg-amber-50', border: 'border-amber-200', iconColor: 'text-amber-600' },
    { label: 'Spot', value: data?.spot ? formatINR(data.spot) : 'N/A', icon: TrendingUp, bg: 'bg-blue-50', border: 'border-blue-200', iconColor: 'text-blue-600' },
    { label: 'Side Bias', value: data?.side_bias || 'None', icon: data?.side_bias === 'PE' ? ArrowDownRight : ArrowUpRight, bg: data?.side_bias === 'PE' ? 'bg-rose-50' : 'bg-emerald-50', border: data?.side_bias === 'PE' ? 'border-rose-200' : 'border-emerald-200', iconColor: data?.side_bias === 'PE' ? 'text-rose-600' : 'text-emerald-600' },
    { label: 'Quality', value: data?.quality || 'N/A', icon: Shield, bg: data?.quality === 'PASS' ? 'bg-green-50' : 'bg-amber-50', border: data?.quality === 'PASS' ? 'border-green-200' : 'border-amber-200', iconColor: data?.quality === 'PASS' ? 'text-green-600' : 'text-amber-600' },
    { label: 'Cycle', value: data?.cycle_count || 0, icon: RefreshCw, bg: 'bg-purple-50', border: 'border-purple-200', iconColor: 'text-purple-600' },
    { label: 'Latency', value: `${(data?.last_cycle_latency_ms || 0).toFixed(1)}ms`, icon: Clock, bg: 'bg-gray-50', border: 'border-gray-200', iconColor: 'text-gray-600' },
  ]
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      {cards.map(c => (
        <div key={c.label} className={`${c.bg} border ${c.border} rounded-xl p-3`}>
          <div className="flex items-center space-x-2 mb-1">
            <c.icon className={`w-4 h-4 ${c.iconColor}`} />
            <span className="text-[11px] text-gray-500 font-medium">{c.label}</span>
          </div>
          <p className="text-base font-bold text-gray-900 truncate">{c.value}</p>
        </div>
      ))}
    </div>
  )
}

// ── Status Tab ────────────────────────────────────────────────────────────────

function StatusTab({ data }) {
  if (!data) return (
    <div className="text-center py-10">
      <Activity className="w-8 h-8 text-gray-300 mx-auto mb-3" />
      <p className="text-gray-500 font-medium">Lottery pipeline not running</p>
      <p className="text-gray-400 text-sm mt-1">Start the pipeline or restart the API server</p>
    </div>
  )
  return (
    <div className="space-y-3">
      <h3 className="text-base font-semibold text-gray-800">Pipeline Status</h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="space-y-1">
          {Object.entries(data).map(([k, v]) => (
            <div key={k} className="flex justify-between border-b border-gray-100 py-1.5">
              <span className="text-sm text-gray-500">{k}</span>
              <span className="text-sm text-gray-900 font-mono">
                {typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v ?? '-')}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Raw Data Tab ──────────────────────────────────────────────────────────────

function RawDataTab({ data }) {
  if (!data || !data.rows?.length) return <p className="text-gray-400 py-6 text-center">No chain data available</p>
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-gray-800">Option Chain — {data.symbol}</h3>
        <span className="text-xs text-gray-500 bg-gray-100 rounded-full px-3 py-1">Spot: {formatINR(data.spot_ltp)} | {data.row_count} strikes</span>
      </div>
      <div className="overflow-x-auto max-h-[500px] overflow-y-auto border border-gray-200 rounded-lg">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="px-3 py-2 text-right text-gray-600 font-semibold">Strike</th>
              <th className="px-3 py-2 text-right text-blue-700 font-semibold">CE LTP</th>
              <th className="px-3 py-2 text-right text-blue-600 font-medium">CE Vol</th>
              <th className="px-3 py-2 text-right text-blue-600 font-medium">CE OI</th>
              <th className="px-3 py-2 text-right text-rose-700 font-semibold">PE LTP</th>
              <th className="px-3 py-2 text-right text-rose-600 font-medium">PE Vol</th>
              <th className="px-3 py-2 text-right text-rose-600 font-medium">PE OI</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r, i) => {
              const isATM = data.spot_ltp && Math.abs(r.strike - data.spot_ltp) <= 50
              return (
                <tr key={i} className={`border-b border-gray-100 ${isATM ? 'bg-amber-50' : 'hover:bg-gray-50'}`}>
                  <td className={`px-3 py-1.5 text-right font-mono ${isATM ? 'text-amber-700 font-bold' : 'text-gray-800 font-medium'}`}>{r.strike}</td>
                  <td className="px-3 py-1.5 text-right text-blue-800 font-mono">{r.CE_LTP?.toFixed(2) || '-'}</td>
                  <td className="px-3 py-1.5 text-right text-gray-500">{r.CE_volume?.toLocaleString() || '-'}</td>
                  <td className="px-3 py-1.5 text-right text-gray-500">{r.CE_OI?.toLocaleString() || '-'}</td>
                  <td className="px-3 py-1.5 text-right text-rose-800 font-mono">{r.PE_LTP?.toFixed(2) || '-'}</td>
                  <td className="px-3 py-1.5 text-right text-gray-500">{r.PE_volume?.toLocaleString() || '-'}</td>
                  <td className="px-3 py-1.5 text-right text-gray-500">{r.PE_OI?.toLocaleString() || '-'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Formula Audit Tab ─────────────────────────────────────────────────────────

function FormulaAuditTab({ data }) {
  const [filter, setFilter] = useState('band')
  if (!data || !data.rows?.length) return <p className="text-gray-400 py-6 text-center">No calculations available</p>
  let rows = data.rows
  if (filter === 'band') rows = rows.filter(r => r.CE_band_eligible || r.PE_band_eligible)
  if (filter === 'scored') rows = rows.filter(r => r.CE_score || r.PE_score)

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-gray-800">Formula Audit</h3>
        <div className="flex space-x-1 bg-gray-100 rounded-lg p-0.5">
          {['all', 'band', 'scored'].map(f => (
            <button key={f} onClick={() => setFilter(f)}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-all ${filter === f ? 'bg-white text-amber-700 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}>
              {f}
            </button>
          ))}
        </div>
      </div>
      <div className="overflow-x-auto max-h-[500px] overflow-y-auto border border-gray-200 rounded-lg">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="px-2 py-2 text-right text-gray-600 font-semibold">Strike</th>
              <th className="px-2 py-2 text-right text-gray-500">d(K)</th>
              <th className="px-2 py-2 text-right text-blue-700">CE Ext</th>
              <th className="px-2 py-2 text-right text-rose-700">PE Ext</th>
              <th className="px-2 py-2 text-right text-gray-500">Liq Skew</th>
              <th className="px-2 py-2 text-right text-blue-600">CE Slope</th>
              <th className="px-2 py-2 text-right text-rose-600">PE Slope</th>
              <th className="px-2 py-2 text-center text-blue-600">CE Band</th>
              <th className="px-2 py-2 text-center text-rose-600">PE Band</th>
              <th className="px-2 py-2 text-right text-blue-800 font-semibold">CE Score</th>
              <th className="px-2 py-2 text-right text-rose-800 font-semibold">PE Score</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                <td className="px-2 py-1.5 text-right text-gray-800 font-mono font-medium">{r.strike}</td>
                <td className="px-2 py-1.5 text-right text-gray-500 font-mono">{r.distance?.toFixed(0) || '-'}</td>
                <td className="px-2 py-1.5 text-right text-blue-700 font-mono">{r.CE_extrinsic?.toFixed(2) || '-'}</td>
                <td className="px-2 py-1.5 text-right text-rose-700 font-mono">{r.PE_extrinsic?.toFixed(2) || '-'}</td>
                <td className="px-2 py-1.5 text-right text-gray-500 font-mono">{r.liquidity_skew?.toFixed(2) || '-'}</td>
                <td className="px-2 py-1.5 text-right text-blue-600 font-mono">{r.CE_slope?.toFixed(4) || '-'}</td>
                <td className="px-2 py-1.5 text-right text-rose-600 font-mono">{r.PE_slope?.toFixed(4) || '-'}</td>
                <td className="px-2 py-1.5 text-center">{r.CE_band_eligible ? <CheckCircle className="w-3.5 h-3.5 text-green-500 inline" /> : <span className="text-gray-300">-</span>}</td>
                <td className="px-2 py-1.5 text-center">{r.PE_band_eligible ? <CheckCircle className="w-3.5 h-3.5 text-green-500 inline" /> : <span className="text-gray-300">-</span>}</td>
                <td className="px-2 py-1.5 text-right text-blue-800 font-mono font-bold">{r.CE_score?.toFixed(2) || '-'}</td>
                <td className="px-2 py-1.5 text-right text-rose-800 font-mono font-bold">{r.PE_score?.toFixed(2) || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Quality Tab ───────────────────────────────────────────────────────────────

function QualityTab({ data }) {
  if (!data) return <p className="text-gray-400 py-6 text-center">No quality data available</p>
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold text-gray-800">Data Quality Checks</h3>
        <div className="flex items-center space-x-2">
          <span className="text-sm text-gray-500">Score: <strong>{data.quality_score}</strong></span>
          <StatusPill status={data.overall_status} />
        </div>
      </div>
      <div className="space-y-2">
        {data.checks?.map((c, i) => (
          <div key={i} className={`flex items-center justify-between p-3 rounded-lg border ${
            c.status === 'PASS' ? 'bg-green-50 border-green-200' :
            c.status === 'WARN' ? 'bg-amber-50 border-amber-200' :
            c.status === 'FAIL' ? 'bg-red-50 border-red-200' :
            'bg-gray-50 border-gray-200'
          }`}>
            <div className="flex items-center space-x-3">
              {c.status === 'PASS' ? <CheckCircle className="w-4 h-4 text-green-600" /> :
               c.status === 'WARN' ? <AlertTriangle className="w-4 h-4 text-amber-600" /> :
               <XCircle className="w-4 h-4 text-red-600" />}
              <div>
                <p className="text-sm font-medium text-gray-800">{c.check_name}</p>
                <p className="text-xs text-gray-500">{c.observed}</p>
              </div>
            </div>
            <StatusPill status={c.status} />
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Signals Tab ───────────────────────────────────────────────────────────────

function SignalsTab({ data }) {
  if (!data || !data.signals?.length) return <p className="text-gray-400 py-6 text-center">No signals yet</p>
  return (
    <div className="space-y-3">
      <h3 className="text-base font-semibold text-gray-800">Signal History ({data.count})</h3>
      <div className="overflow-x-auto max-h-[500px] overflow-y-auto border border-gray-200 rounded-lg">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="px-3 py-2 text-left text-gray-600">Time</th>
              <th className="px-3 py-2 text-center text-gray-600">Validity</th>
              <th className="px-3 py-2 text-center text-gray-600">State</th>
              <th className="px-3 py-2 text-center text-gray-600">Zone</th>
              <th className="px-3 py-2 text-right text-gray-600">Strike</th>
              <th className="px-3 py-2 text-right text-gray-600">Premium</th>
              <th className="px-3 py-2 text-right text-gray-600">Spot</th>
              <th className="px-3 py-2 text-left text-gray-600">Rejection</th>
            </tr>
          </thead>
          <tbody>
            {data.signals.map((s, i) => (
              <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                <td className="px-3 py-1.5 text-gray-600 font-mono">{toLocalTime(s.timestamp)}</td>
                <td className="px-3 py-1.5 text-center"><StatusPill status={s.validity} /></td>
                <td className="px-3 py-1.5 text-center"><StatusPill status={s.machine_state} /></td>
                <td className="px-3 py-1.5 text-center text-gray-700 text-[11px]">{s.zone}</td>
                <td className="px-3 py-1.5 text-right text-gray-900 font-mono font-medium">{s.selected_strike || '-'}</td>
                <td className="px-3 py-1.5 text-right text-amber-700 font-mono">{s.selected_premium || '-'}</td>
                <td className="px-3 py-1.5 text-right text-gray-600 font-mono">{s.spot_ltp?.toFixed(1) || '-'}</td>
                <td className="px-3 py-1.5 text-left text-red-600 text-[10px] font-medium">{s.rejection_reason || ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Trades Tab ────────────────────────────────────────────────────────────────

function TradesTab({ data }) {
  if (!data) return <p className="text-gray-400 py-6 text-center">No trade data available</p>
  return (
    <div className="space-y-4">
      {data.active_trade && (
        <div className="bg-blue-50 border border-blue-200 rounded-xl p-4">
          <div className="flex items-center space-x-2 mb-3">
            <Zap className="w-4 h-4 text-blue-600" />
            <h4 className="text-sm font-bold text-blue-700">ACTIVE TRADE</h4>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            <div><span className="text-gray-500">Strike:</span> <span className="text-gray-900 font-mono font-bold">{data.active_trade.strike}</span></div>
            <div><span className="text-gray-500">Side:</span> <span className="text-gray-900 font-medium">{data.active_trade.side} {data.active_trade.option_type}</span></div>
            <div><span className="text-gray-500">Entry:</span> <span className="text-gray-900 font-mono">{formatINR(data.active_trade.entry_price)}</span></div>
            <div><span className="text-gray-500">SL:</span> <span className="text-red-700 font-mono font-medium">{formatINR(data.active_trade.sl)}</span></div>
          </div>
        </div>
      )}
      <h3 className="text-base font-semibold text-gray-800">Trade History ({data.count})</h3>
      {data.trades?.length > 0 ? (
        <div className="overflow-x-auto max-h-[400px] overflow-y-auto border border-gray-200 rounded-lg">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-3 py-2 text-left text-gray-600">Entry</th>
                <th className="px-3 py-2 text-center text-gray-600">Side</th>
                <th className="px-3 py-2 text-right text-gray-600">Strike</th>
                <th className="px-3 py-2 text-right text-gray-600">Entry</th>
                <th className="px-3 py-2 text-right text-gray-600">Exit</th>
                <th className="px-3 py-2 text-right text-gray-600">PnL</th>
                <th className="px-3 py-2 text-center text-gray-600">Status</th>
                <th className="px-3 py-2 text-left text-gray-600">Reason</th>
              </tr>
            </thead>
            <tbody>
              {data.trades.map((t, i) => (
                <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                  <td className="px-3 py-1.5 text-gray-600 font-mono">{toLocalDateTime(t.timestamp_entry)}</td>
                  <td className="px-3 py-1.5 text-center"><StatusPill status={t.side} /></td>
                  <td className="px-3 py-1.5 text-right text-gray-900 font-mono font-medium">{t.strike}</td>
                  <td className="px-3 py-1.5 text-right text-gray-800 font-mono">{formatINR(t.entry_price)}</td>
                  <td className="px-3 py-1.5 text-right text-gray-800 font-mono">{t.exit_price ? formatINR(t.exit_price) : '-'}</td>
                  <td className={`px-3 py-1.5 text-right font-mono font-bold ${t.pnl > 0 ? 'text-green-700' : t.pnl < 0 ? 'text-red-700' : 'text-gray-400'}`}>
                    {t.pnl != null ? formatINR(t.pnl) : '-'}
                  </td>
                  <td className="px-3 py-1.5 text-center"><StatusPill status={t.status} /></td>
                  <td className="px-3 py-1.5 text-left text-gray-500 text-[10px]">{t.reason_exit || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="text-gray-400 text-center py-4">No trades yet</p>}
    </div>
  )
}

// ── Capital Tab ───────────────────────────────────────────────────────────────

function CapitalTab({ data }) {
  if (!data || !data.ledger?.length) return <p className="text-gray-400 py-6 text-center">No capital data available</p>
  return (
    <div className="space-y-3">
      <h3 className="text-base font-semibold text-gray-800">Capital Ledger ({data.count})</h3>
      <div className="overflow-x-auto max-h-[500px] overflow-y-auto border border-gray-200 rounded-lg">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="px-3 py-2 text-left text-gray-600">Time</th>
              <th className="px-3 py-2 text-left text-gray-600">Event</th>
              <th className="px-3 py-2 text-right text-gray-600">Amount</th>
              <th className="px-3 py-2 text-right text-gray-600">Capital</th>
              <th className="px-3 py-2 text-right text-gray-600">PnL</th>
              <th className="px-3 py-2 text-right text-gray-600">Drawdown</th>
            </tr>
          </thead>
          <tbody>
            {data.ledger.map((e, i) => (
              <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                <td className="px-3 py-1.5 text-gray-600 font-mono">{toLocalTime(e.timestamp)}</td>
                <td className="px-3 py-1.5 text-gray-800 font-medium">{e.event}</td>
                <td className={`px-3 py-1.5 text-right font-mono font-medium ${e.amount > 0 ? 'text-green-700' : e.amount < 0 ? 'text-red-700' : 'text-gray-400'}`}>
                  {formatINR(e.amount)}
                </td>
                <td className="px-3 py-1.5 text-right text-gray-900 font-mono font-medium">{formatINR(e.running_capital)}</td>
                <td className="px-3 py-1.5 text-right text-gray-600 font-mono">{formatINR(e.realized_pnl)}</td>
                <td className="px-3 py-1.5 text-right text-orange-700 font-mono">{formatINR(e.drawdown)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default Lottery
