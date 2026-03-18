import { motion } from 'framer-motion'
import { Activity, AlertTriangle, BarChart2, CheckCircle, ChevronDown, ChevronUp, Clock, Layers, RefreshCw, Settings, TrendingUp, Zap } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { analyzeHybridPipeline, fetchExpirySchedule, fetchHybridPipelineStatus, fetchLiveMarketData, setHybridModuleWeight, toggleHybridModule } from '../api'
import { useMarketWebSocket } from '../hooks/useMarketWebSocket'

// Note: Market data and expiry schedule fetched from FYERS API via shared_project_engine
// No sample/hardcoded data - shows empty if live data unavailable

const formatPct = (v) => `${(v || 0).toFixed(1)}%`

// Fallback indices (used if API fails)
const FALLBACK_INDICES = ['NIFTY50', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX']

// Color maps for different states
const REGIME_COLORS = {
  TRENDING_BULLISH: 'bg-green-500/20 text-green-400 border-green-500/30',
  TRENDING_BEARISH: 'bg-red-500/20 text-red-400 border-red-500/30',
  RANGING_BULLISH: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
  RANGING_BEARISH: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  RANGING_NEUTRAL: 'bg-slate-500/20 text-slate-400 border-slate-500/30',
  HIGH_VOLATILITY: 'bg-red-600/20 text-red-300 border-red-500/30',
  BREAKOUT_UP: 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30',
  BREAKOUT_DOWN: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
  COMPRESSED: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  UNKNOWN: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
}

const VOLATILITY_COLORS = {
  EXTREME: 'text-red-400',
  HIGH: 'text-orange-400',
  NORMAL: 'text-green-400',
  LOW: 'text-cyan-400',
  COMPRESSED: 'text-amber-400',
}

const SENTIMENT_COLORS = {
  STRONG_BULLISH: 'text-green-400',
  BULLISH: 'text-emerald-400',
  NEUTRAL: 'text-slate-400',
  BEARISH: 'text-orange-400',
  STRONG_BEARISH: 'text-red-400',
}

const TREND_COLORS = {
  STRONG_UP: 'text-green-400',
  UP: 'text-emerald-400',
  SIDEWAYS: 'text-slate-400',
  DOWN: 'text-orange-400',
  STRONG_DOWN: 'text-red-400',
}

// Module card component
const ModuleCard = ({ title, icon: Icon, gradient, data, enabled, weight, onToggle, onWeightChange }) => {
  const [showSettings, setShowSettings] = useState(false)

  return (
    <div className={`relative rounded-xl bg-gradient-to-br ${gradient} border border-white/10 p-4 ${!enabled ? 'opacity-50' : ''}`}>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center space-x-2">
          <div className="p-1.5 rounded-lg bg-white/10">
            <Icon className="w-4 h-4 text-white" />
          </div>
          <span className="font-bold text-white text-sm">{title}</span>
        </div>
        <div className="flex items-center space-x-1">
          <span className="text-xs text-white/60">{weight.toFixed(1)}x</span>
          <button
            onClick={() => setShowSettings(!showSettings)}
            className="p-1 rounded hover:bg-white/10 transition-colors"
          >
            <Settings className="w-3.5 h-3.5 text-white/60" />
          </button>
        </div>
      </div>

      {/* Settings dropdown */}
      {showSettings && (
        <div className="absolute top-full left-0 right-0 mt-1 p-3 bg-slate-800 rounded-lg border border-slate-600 z-10 shadow-xl">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-slate-400">Enabled</span>
            <button
              onClick={() => onToggle(!enabled)}
              className={`w-10 h-5 rounded-full transition-colors ${enabled ? 'bg-green-500' : 'bg-slate-600'}`}
            >
              <div className={`w-4 h-4 rounded-full bg-white transform transition-transform ${enabled ? 'translate-x-5' : 'translate-x-0.5'}`} />
            </button>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-400">Weight</span>
            <div className="flex items-center space-x-1">
              <button
                onClick={() => onWeightChange(Math.max(0.1, weight - 0.1))}
                className="p-1 rounded bg-slate-700 hover:bg-slate-600"
              >
                <ChevronDown className="w-3 h-3 text-white" />
              </button>
              <span className="text-xs text-white w-8 text-center">{weight.toFixed(1)}</span>
              <button
                onClick={() => onWeightChange(Math.min(3.0, weight + 0.1))}
                className="p-1 rounded bg-slate-700 hover:bg-slate-600"
              >
                <ChevronUp className="w-3 h-3 text-white" />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Data */}
      {data && (
        <div className="space-y-2">
          {Object.entries(data).map(([key, value]) => (
            <div key={key} className="flex items-center justify-between">
              <span className="text-xs text-white/50 capitalize">{key.replace(/_/g, ' ')}</span>
              <span className={`text-sm font-mono ${value.color || 'text-white'}`}>
                {value.display}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Confidence bar */}
      {data?.confidence && (
        <div className="mt-3">
          <div className="h-1.5 bg-black/30 rounded-full overflow-hidden">
            <div
              className="h-full bg-white/60 rounded-full transition-all"
              style={{ width: `${data.confidence.raw}%` }}
            />
          </div>
        </div>
      )}
    </div>
  )
}

// Index Summary Row component
const IndexSummaryRow = ({ index, analysis, isSelected, onClick, isAnalyzing, isExpiry }) => {
  const hasEntry = analysis?.entry_side && analysis.entry_side !== 'NONE'
  const regimeClass = analysis ? REGIME_COLORS[analysis.regime] || REGIME_COLORS.UNKNOWN : ''

  return (
    <tr
      onClick={onClick}
      className={`cursor-pointer transition-colors ${isSelected ? 'bg-indigo-500/20' : 'hover:bg-slate-700/50'}`}
    >
      <td className="px-3 py-2">
        <div className="flex items-center space-x-2">
          <span className={`font-bold ${isSelected ? 'text-indigo-300' : 'text-slate-300'}`}>{index}</span>
          {isExpiry && (
            <span className="flex items-center space-x-0.5 px-1.5 py-0.5 bg-amber-500/20 text-amber-400 text-[10px] font-bold rounded border border-amber-500/30">
              <Clock className="w-2.5 h-2.5" />
              <span>EXP</span>
            </span>
          )}
          {isSelected && <div className="w-1.5 h-1.5 rounded-full bg-indigo-400" />}
        </div>
      </td>
      <td className="px-3 py-2">
        {isAnalyzing ? (
          <RefreshCw className="w-3 h-3 text-slate-400 animate-spin" />
        ) : analysis ? (
          <span className={`text-xs px-2 py-0.5 rounded border ${regimeClass}`}>
            {analysis.regime?.replace(/_/g, ' ')}
          </span>
        ) : (
          <span className="text-xs text-slate-500">-</span>
        )}
      </td>
      <td className="px-3 py-2 text-center">
        {analysis?.modules?.sentiment?.institutional_signal ? (
          <span className={`text-xs font-bold ${
            analysis.modules.sentiment.institutional_signal === 'SUP+' ? 'text-green-400' :
            analysis.modules.sentiment.institutional_signal === 'RES+' ? 'text-red-400' :
            analysis.modules.sentiment.institutional_signal === 'SC' ? 'text-cyan-400' :
            analysis.modules.sentiment.institutional_signal === 'LU' ? 'text-orange-400' : 'text-slate-400'
          }`}>
            {analysis.modules.sentiment.institutional_signal}
          </span>
        ) : (
          <span className="text-xs text-slate-500">-</span>
        )}
      </td>
      <td className="px-3 py-2 text-center">
        {hasEntry ? (
          <span className={`flex items-center justify-center space-x-1 ${
            analysis.entry_side === 'CE' ? 'text-green-400' : 'text-red-400'
          }`}>
            <CheckCircle className="w-3.5 h-3.5" />
            <span className="text-xs font-bold">{analysis.entry_side}</span>
          </span>
        ) : (
          <span className="text-xs text-slate-500">-</span>
        )}
      </td>
      <td className="px-3 py-2 text-center">
        <span className="text-xs text-slate-400">{analysis?.confidence ? formatPct(analysis.confidence) : '-'}</span>
      </td>
    </tr>
  )
}

// Main component
export default function HybridPipelineCard({ marketData }) {
  const [status, setStatus] = useState(null)
  const [analysisMap, setAnalysisMap] = useState({}) // Map of index -> analysis
  const [selectedIndex, setSelectedIndex] = useState('NIFTY50')
  const [loading, setLoading] = useState(true)
  const [analyzingIndices, setAnalyzingIndices] = useState(new Set())
  const [expanded, setExpanded] = useState(true)
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [isDocumentVisible, setIsDocumentVisible] = useState(() => {
    if (typeof document === 'undefined') return true
    return document.visibilityState !== 'hidden'
  })

  // Expiry schedule from shared_project_engine
  const [expirySchedule, setExpirySchedule] = useState({})
  const [indices, setIndices] = useState(FALLBACK_INDICES)
  const [todaysExpiry, setTodaysExpiry] = useState([])

  // Live market data from FYERS API
  const [liveData, setLiveData] = useState({})
  const [marketOpen, setMarketOpen] = useState(false)
  const [lastUpdate, setLastUpdate] = useState(null)
  const { lastMessage: lastMarketMessage, connectionStatus: marketStreamStatus } = useMarketWebSocket(autoRefresh)

  // Check if index is expiring today
  const isExpiryDay = useCallback((index) => {
    return expirySchedule[index]?.is_expiry_today || false
  }, [expirySchedule])

  // Get expiry day name for index
  const getExpiryDayName = useCallback((index) => {
    return expirySchedule[index]?.weekday_short || '?'
  }, [expirySchedule])

  // Fetch expiry schedule from shared_project_engine
  const loadExpirySchedule = useCallback(async () => {
    try {
      const data = await fetchExpirySchedule()
      if (data.expirySchedule) {
        setExpirySchedule(data.expirySchedule)
      }
      if (data.indices) {
        setIndices(data.indices)
      }
      if (data.todaysExpiry) {
        setTodaysExpiry(data.todaysExpiry)
      }
    } catch (err) {
      console.error('Error fetching expiry schedule:', err)
      // Keep fallback values
    }
  }, [])

  // Fetch live market data from FYERS API
  const loadLiveMarketData = useCallback(async () => {
    try {
      const data = await fetchLiveMarketData()
      if (data.indices && Object.keys(data.indices).length > 0) {
        setLiveData(data.indices)
        setMarketOpen(data.market_open || false)
        setLastUpdate(new Date().toLocaleTimeString())
      }
    } catch (err) {
      console.error('Error fetching live market data:', err)
      // Keep existing data
    }
  }, [])

  // Fetch status
  const fetchStatus = useCallback(async () => {
    try {
      const data = await fetchHybridPipelineStatus()
      setStatus(data)
    } catch (err) {
      console.error('Error fetching hybrid status:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  // Analyze a single index (with auto expiry mode detection)
  const analyzeIndex = useCallback(async (index) => {
    const liveCandidate = liveData[index]
    const fallbackCandidate = marketData?.[index]
    const liveLtp = Number(liveCandidate?.ltp)
    const fallbackLtp = Number(fallbackCandidate?.ltp)
    const hasLiveLtp = Number.isFinite(liveLtp) && liveLtp > 0
    const hasFallbackLtp = Number.isFinite(fallbackLtp) && fallbackLtp > 0
    const dataToAnalyze = hasLiveLtp
      ? liveCandidate
      : (hasFallbackLtp ? fallbackCandidate : (liveCandidate || fallbackCandidate))

    // Skip if no live data available
    if (!dataToAnalyze || !Number.isFinite(Number(dataToAnalyze.ltp)) || Number(dataToAnalyze.ltp) <= 0) {
      console.log(`No live data for ${index}, skipping analysis`)
      return
    }

    setAnalyzingIndices(prev => new Set([...prev, index]))
    try {
      // Add expiry flag if today is expiry day for this index
      const isExpiry = isExpiryDay(index)
      const enrichedData = {
        ...dataToAnalyze,
        is_expiry: isExpiry,
      }

      const result = await analyzeHybridPipeline(index, enrichedData)

      // Mark result with expiry info
      result.is_expiry = isExpiry

      setAnalysisMap(prev => ({ ...prev, [index]: result }))
    } catch (err) {
      console.error(`Error analyzing ${index}:`, err)
    } finally {
      setAnalyzingIndices(prev => {
        const next = new Set(prev)
        next.delete(index)
        return next
      })
    }
  }, [liveData, marketData, isExpiryDay])

  // Analyze all indices
  const analyzeAllIndices = useCallback(async () => {
    await Promise.all(indices.map(index => analyzeIndex(index)))
  }, [analyzeIndex, indices])

  // Load expiry schedule, status, and live data on mount
  useEffect(() => {
    loadExpirySchedule()
    fetchStatus()
    loadLiveMarketData()
  }, [fetchStatus, loadExpirySchedule, loadLiveMarketData])

  useEffect(() => {
    if (typeof document === 'undefined') return undefined

    const handleVisibilityChange = () => {
      setIsDocumentVisible(document.visibilityState !== 'hidden')
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  }, [])

  useEffect(() => {
    if (!lastMarketMessage) return

    if (lastMarketMessage.type === 'market_live_update' && lastMarketMessage.indices) {
      setLiveData(lastMarketMessage.indices)
      setMarketOpen(lastMarketMessage.market_open || false)
      setLastUpdate(new Date(lastMarketMessage.timestamp || Date.now()).toLocaleTimeString())
      return
    }

    if (lastMarketMessage.type === 'market_live_error') {
      console.error('Error receiving live market stream:', lastMarketMessage.error)
    }
  }, [lastMarketMessage])

  // Poll only while auto-refresh is enabled and the market stream is unavailable.
  useEffect(() => {
    if (!autoRefresh || !isDocumentVisible || marketStreamStatus === 'connected') return

    const interval = setInterval(() => {
      loadLiveMarketData()
    }, 15000) // 15 seconds

    return () => clearInterval(interval)
  }, [autoRefresh, isDocumentVisible, loadLiveMarketData, marketStreamStatus])

  // Auto-analyze when live data or market data changes
  useEffect(() => {
    if (autoRefresh && (Object.keys(liveData).length > 0 || marketData)) {
      analyzeAllIndices()
    }
  }, [autoRefresh, liveData, marketData, analyzeAllIndices])

  // Module control handlers
  const handleToggleModule = async (module, enabled) => {
    try {
      await toggleHybridModule(module, enabled)
      await fetchStatus()
    } catch (err) {
      console.error('Error toggling module:', err)
    }
  }

  const handleSetWeight = async (module, weight) => {
    try {
      await setHybridModuleWeight(module, weight)
      await fetchStatus()
    } catch (err) {
      console.error('Error setting weight:', err)
    }
  }

  if (loading) {
    return (
      <div className="rounded-xl bg-gradient-to-br from-indigo-900/20 to-slate-900/90 border border-indigo-500/30 p-5">
        <div className="flex items-center justify-center py-8">
          <RefreshCw className="w-6 h-6 text-indigo-400 animate-spin" />
        </div>
      </div>
    )
  }

  if (!status?.available) {
    return (
      <div className="rounded-xl bg-gradient-to-br from-slate-800/50 to-slate-900/90 border border-slate-600/30 p-5">
        <div className="flex items-center space-x-3 text-slate-400">
          <AlertTriangle className="w-5 h-5" />
          <span>Hybrid Pipeline not available</span>
        </div>
      </div>
    )
  }

  const modules = status.modules || {}
  const analysis = analysisMap[selectedIndex]
  const regimeClass = analysis ? REGIME_COLORS[analysis.regime] || REGIME_COLORS.UNKNOWN : ''
  const indicesWithEntry = indices.filter(idx => analysisMap[idx]?.entry_side && analysisMap[idx].entry_side !== 'NONE')
  const selectedIsExpiry = isExpiryDay(selectedIndex)

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="relative overflow-hidden rounded-xl bg-gradient-to-br from-indigo-900/20 to-slate-900/90 border border-indigo-500/30"
    >
      {/* Header */}
      <div className="flex items-center justify-between p-5 pb-0">
        <div className="flex items-center space-x-3">
          <div className="p-2 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600">
            <Layers className="w-5 h-5 text-white" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h3 className="font-bold text-lg text-indigo-300">Hybrid Regime Pipeline</h3>
              {indicesWithEntry.length > 0 && (
                <span className="px-2 py-0.5 bg-green-500/20 text-green-400 text-xs font-bold rounded border border-green-500/30">
                  {indicesWithEntry.length} ENTRY
                </span>
              )}
              {todaysExpiry.length > 0 && (
                <span className="flex items-center space-x-1 px-2 py-0.5 bg-amber-500/20 text-amber-400 text-xs font-bold rounded border border-amber-500/30">
                  <Clock className="w-3 h-3" />
                  <span>{todaysExpiry.join(', ')} EXP</span>
                </span>
              )}
            </div>
            <div className="flex items-center space-x-2 text-xs">
              <span className="text-slate-400">Multi-index analysis</span>
              {Object.keys(liveData).length > 0 ? (
                <span className={`flex items-center space-x-1 ${
                  autoRefresh && marketStreamStatus === 'connected' ? 'text-cyan-400' : 'text-green-400'
                }`}>
                  <Activity className="w-3 h-3" />
                  <span>
                    {autoRefresh && marketStreamStatus === 'connected' ? 'STREAM' : 'LIVE'}
                    {lastUpdate ? ` (${lastUpdate})` : ''}
                  </span>
                </span>
              ) : (
                <span className="text-red-400">NO LIVE DATA</span>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center space-x-2">
          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={`p-2 rounded-lg transition-colors ${autoRefresh ? 'bg-green-500/20 text-green-400' : 'bg-slate-700 text-slate-400'}`}
            title={autoRefresh ? 'Auto-refresh ON' : 'Auto-refresh OFF'}
          >
            <RefreshCw className={`w-4 h-4 ${autoRefresh ? 'animate-spin' : ''}`} style={{ animationDuration: '3s' }} />
          </button>
          <button
            onClick={() => setExpanded(!expanded)}
            className="p-2 rounded-lg bg-slate-700 text-slate-400 hover:bg-slate-600"
          >
            {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {expanded && (
        <div className="p-5 space-y-4">
          {/* Summary Table - All Indices */}
          <div className="bg-slate-800/50 rounded-xl border border-slate-700/50 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-900/50 text-slate-400">
                  <th className="px-3 py-2 text-left font-medium">INDEX</th>
                  <th className="px-3 py-2 text-left font-medium">REGIME</th>
                  <th className="px-3 py-2 text-center font-medium">SIGNAL</th>
                  <th className="px-3 py-2 text-center font-medium">ENTRY</th>
                  <th className="px-3 py-2 text-center font-medium">CONF</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/50">
                {indices.map(index => (
                  <IndexSummaryRow
                    key={index}
                    index={index}
                    analysis={analysisMap[index]}
                    isSelected={selectedIndex === index}
                    isAnalyzing={analyzingIndices.has(index)}
                    isExpiry={isExpiryDay(index)}
                    onClick={() => setSelectedIndex(index)}
                  />
                ))}
              </tbody>
            </table>
          </div>

          {/* Selected Index Details */}
          {analysis && (
            <>
              {/* Index Header */}
              <div className="flex items-center space-x-2 pt-2">
                <span className="text-lg font-bold text-indigo-300">{selectedIndex}</span>
                {selectedIsExpiry && (
                  <span className="flex items-center space-x-1 px-2 py-0.5 bg-amber-500/20 text-amber-400 text-xs font-bold rounded border border-amber-500/30">
                    <Clock className="w-3 h-3" />
                    <span>EXPIRY TODAY</span>
                  </span>
                )}
                <span className="text-xs text-slate-500">Details</span>
              </div>

              {/* Module Cards */}
              <div className="grid grid-cols-3 gap-3">
                {/* Volatility Module */}
                <ModuleCard
                  title="VOLATILITY"
                  icon={Activity}
                  gradient="from-rose-600/30 to-red-900/30"
                  enabled={modules.volatility?.enabled}
                  weight={modules.volatility?.weight || 1.0}
                  onToggle={(enabled) => handleToggleModule('volatility', enabled)}
                  onWeightChange={(weight) => handleSetWeight('volatility', weight)}
                  data={analysis?.modules?.volatility ? {
                    level: {
                      display: analysis.modules.volatility.level,
                      color: VOLATILITY_COLORS[analysis.modules.volatility.level],
                    },
                    vix: { display: analysis.modules.volatility.vix?.toFixed(1) },
                    range: { display: `${analysis.modules.volatility.range_pct?.toFixed(2)}%` },
                    risk: { display: `${analysis.modules.volatility.risk_multiplier?.toFixed(2)}x` },
                    confidence: {
                      display: formatPct(analysis.modules.volatility.confidence),
                      raw: analysis.modules.volatility.confidence,
                    },
                  } : null}
                />

                {/* Sentiment Module */}
                <ModuleCard
                  title="SENTIMENT"
                  icon={BarChart2}
                  gradient="from-emerald-600/30 to-teal-900/30"
                  enabled={modules.sentiment?.enabled}
                  weight={modules.sentiment?.weight || 1.0}
                  onToggle={(enabled) => handleToggleModule('sentiment', enabled)}
                  onWeightChange={(weight) => handleSetWeight('sentiment', weight)}
                  data={analysis?.modules?.sentiment ? {
                    bias: {
                      display: analysis.modules.sentiment.bias?.replace(/_/g, ' '),
                      color: SENTIMENT_COLORS[analysis.modules.sentiment.bias],
                    },
                    pcr: { display: analysis.modules.sentiment.pcr?.toFixed(3) },
                    pattern: { display: analysis.modules.sentiment.oi_pattern?.replace(/_/g, ' ') },
                    signal: { display: analysis.modules.sentiment.institutional_signal || '-' },
                    confidence: {
                      display: formatPct(analysis.modules.sentiment.confidence),
                      raw: analysis.modules.sentiment.confidence,
                    },
                  } : null}
                />

                {/* Trend Module */}
                <ModuleCard
                  title="TREND"
                  icon={TrendingUp}
                  gradient="from-cyan-600/30 to-blue-900/30"
                  enabled={modules.trend?.enabled}
                  weight={modules.trend?.weight || 1.0}
                  onToggle={(enabled) => handleToggleModule('trend', enabled)}
                  onWeightChange={(weight) => handleSetWeight('trend', weight)}
                  data={analysis?.modules?.trend ? {
                    direction: {
                      display: analysis.modules.trend.direction?.replace(/_/g, ' '),
                      color: TREND_COLORS[analysis.modules.trend.direction],
                    },
                    strength: { display: `${analysis.modules.trend.strength?.toFixed(0)}` },
                    phase: { display: analysis.modules.trend.phase?.replace(/_/g, ' ') },
                    momentum: { display: analysis.modules.trend.momentum?.toFixed(1) },
                    support: {
                      display: analysis.modules.trend.support?.toFixed(2),
                      color: 'text-green-400',
                    },
                    resistance: {
                      display: analysis.modules.trend.resistance?.toFixed(2),
                      color: 'text-red-400',
                    },
                    confidence: {
                      display: formatPct(analysis.modules.trend.confidence),
                      raw: analysis.modules.trend.confidence,
                    },
                  } : null}
                />
              </div>

              {/* Analysis Result */}
              <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/50">
                <div className="grid grid-cols-4 gap-4 mb-3">
                  <div>
                    <div className="text-xs text-slate-500 mb-1">REGIME</div>
                    <div className={`text-sm font-bold px-2 py-1 rounded inline-block border ${regimeClass}`}>
                      {analysis.regime?.replace(/_/g, ' ')}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-500 mb-1">ACTION</div>
                    <div className={`text-lg font-bold ${analysis.action?.includes('CE') ? 'text-green-400' : analysis.action?.includes('PE') ? 'text-red-400' : 'text-slate-400'}`}>
                      {analysis.action?.replace(/_/g, ' ')}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-500 mb-1">CONFIDENCE</div>
                    <div className="text-lg font-bold text-indigo-400">{formatPct(analysis.confidence)}</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-500 mb-1">CONSENSUS</div>
                    <div className="text-lg font-bold text-cyan-400">
                      {analysis.consensus?.agreeing}/{analysis.consensus?.total}
                      <span className="text-sm text-slate-400 ml-1">({formatPct(analysis.consensus?.level * 100)})</span>
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-5 gap-4 text-center border-t border-slate-700 pt-3">
                  <div>
                    <div className="text-xs text-slate-500">Entry Side</div>
                    <div className={`text-sm font-bold ${analysis.entry_side === 'CE' ? 'text-green-400' : analysis.entry_side === 'PE' ? 'text-red-400' : 'text-slate-400'}`}>
                      {analysis.entry_side || 'NONE'}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-500">Support</div>
                    <div className="text-sm font-bold text-green-400">
                      {analysis.modules?.trend?.support?.toFixed(2) || '-'}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-500">Resistance</div>
                    <div className="text-sm font-bold text-red-400">
                      {analysis.modules?.trend?.resistance?.toFixed(2) || '-'}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-500">Risk Multiplier</div>
                    <div className="text-sm font-bold text-amber-400">{analysis.risk_multiplier?.toFixed(2)}x</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-500">SL / Target</div>
                    <div className="text-sm font-bold text-slate-300">
                      {analysis.stop_distance_pct}% / {analysis.target_distance_pct}%
                    </div>
                  </div>
                </div>

                {/* Warnings */}
                {analysis.warnings?.length > 0 && (
                  <div className="mt-3 p-2 bg-amber-500/10 rounded-lg border border-amber-500/30">
                    {analysis.warnings.map((w, i) => (
                      <div key={i} className="flex items-center space-x-2 text-xs text-amber-400">
                        <AlertTriangle className="w-3 h-3" />
                        <span>{w}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}

          {/* Controls */}
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <button
                onClick={analyzeAllIndices}
                disabled={analyzingIndices.size > 0}
                className="flex items-center space-x-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 rounded-lg text-white text-sm font-medium transition-colors disabled:opacity-50"
              >
                {analyzingIndices.size > 0 ? (
                  <RefreshCw className="w-4 h-4 animate-spin" />
                ) : (
                  <Zap className="w-4 h-4" />
                )}
                <span>Analyze All ({indices.length})</span>
              </button>
              <button
                onClick={() => analyzeIndex(selectedIndex)}
                disabled={analyzingIndices.has(selectedIndex)}
                className="flex items-center space-x-2 px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-slate-300 text-sm transition-colors disabled:opacity-50"
              >
                <span>Analyze {selectedIndex}</span>
              </button>
              <button
                onClick={loadLiveMarketData}
                className="flex items-center space-x-2 px-3 py-2 bg-emerald-600/20 hover:bg-emerald-600/30 rounded-lg text-emerald-400 text-sm transition-colors border border-emerald-500/30"
                title="Refresh live market data from FYERS"
              >
                <RefreshCw className="w-4 h-4" />
                <span>Refresh Live</span>
              </button>
            </div>

            {/* Expiry Schedule Info */}
            <div className="flex items-center space-x-3 text-xs text-slate-500">
              <span>Expiry:</span>
              {indices.map(idx => (
                <span key={idx} className={isExpiryDay(idx) ? 'text-amber-400 font-bold' : ''}>
                  {idx.substring(0, 3)}-{getExpiryDayName(idx)}
                </span>
              ))}
            </div>
          </div>

          {/* Stats */}
          {status.stats && (
            <div className="grid grid-cols-3 gap-3 text-center pt-3 border-t border-slate-700">
              <div>
                <div className="text-xs text-slate-500">Total Decisions</div>
                <div className="text-lg font-bold text-slate-300">{status.stats.total_decisions || 0}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Trades Suggested</div>
                <div className="text-lg font-bold text-indigo-400">{status.stats.trades_suggested || 0}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Top Regime</div>
                <div className="text-sm font-bold text-slate-300">
                  {Object.entries(status.stats.by_regime || {}).sort((a, b) => b[1] - a[1])[0]?.[0]?.replace(/_/g, ' ') || '-'}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </motion.div>
  )
}
