import { useState, useEffect, useRef, useCallback } from 'react'
import { DollarSign, TrendingUp, Activity, AlertCircle, Briefcase, Brain, Wallet, Bell, BellOff, X } from 'lucide-react'
import { fetchAgentDashboardSupplemental, fetchAgentDetail, fetchAgentEconomic, fetchAgentTasks } from '../api'
import { AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { motion, AnimatePresence } from 'framer-motion'
import { useDisplayName } from '../DisplayNamesContext'

const formatINR = (value, digits = 2) =>
  `₹${Number(value || 0).toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`

// Bias change detection helper
const shouldNotifyBiasChange = (prevBias, newBias) => {
  if (!prevBias || !newBias) return false
  if (prevBias === newBias) return false
  if (newBias === 'NEUTRAL') return false // No notification when changing TO neutral
  // Notify: Neutral→Bullish, Neutral→Bearish, Bullish→Bearish, Bearish→Bullish
  return true
}

// Stock signal movement detection helper
// Normalize signals to simplified categories for movement tracking
const normalizeSignal = (signal) => {
  if (signal === 'BUY_CANDIDATE') return 'BUY'
  if (signal === 'SELL_CANDIDATE') return 'SELL'
  if (signal === 'WATCH') return 'NEUTRAL'
  return signal // OVERBOUGHT, OVERSOLD, AVOID stay as is
}

// Check if this is a significant movement we want to track
const isSignificantMovement = (fromSignal, toSignal) => {
  const from = normalizeSignal(fromSignal)
  const to = normalizeSignal(toSignal)
  if (from === to) return null

  // Track: BUY↔SELL, NEUTRAL→BUY, NEUTRAL→SELL
  if (from === 'BUY' && to === 'SELL') return 'BUY→SELL'
  if (from === 'SELL' && to === 'BUY') return 'SELL→BUY'
  if (from === 'NEUTRAL' && to === 'BUY') return 'NEUTRAL→BUY'
  if (from === 'NEUTRAL' && to === 'SELL') return 'NEUTRAL→SELL'
  if (from === 'BUY' && to === 'NEUTRAL') return 'BUY→NEUTRAL'
  if (from === 'SELL' && to === 'NEUTRAL') return 'SELL→NEUTRAL'

  return null
}

const MOVEMENT_ALERT_THRESHOLD = 3 // Alert when 3+ stocks move in same direction
const DEFAULT_SCREENER_BASKETS = ['SENSEX', 'NIFTY50', 'BANKNIFTY']

const Dashboard = ({ agents, selectedAgent }) => {
  const dn = useDisplayName()
  const [agentDetails, setAgentDetails] = useState(null)
  const [economicData, setEconomicData] = useState(null)
  const [tasksData, setTasksData] = useState(null)
  const [fyersScreener, setFyersScreener] = useState(null)
  const [institutionalShadow, setInstitutionalShadow] = useState(null)
  const [marketSession, setMarketSession] = useState(null)
  const [loading, setLoading] = useState(true)
  const [resultsView, setResultsView] = useState('few')
  const [basketFilter, setBasketFilter] = useState('ALL')
  const [signalFilter, setSignalFilter] = useState('ALL')
  const [signalSort, setSignalSort] = useState('default')
  const [isDocumentVisible, setIsDocumentVisible] = useState(() => {
    if (typeof document === 'undefined') return true
    return document.visibilityState !== 'hidden'
  })

  // Bias change notification state
  const [biasAlerts, setBiasAlerts] = useState([])
  const [notificationsEnabled, setNotificationsEnabled] = useState(() => {
    return localStorage.getItem('biasNotificationsEnabled') !== 'false'
  })
  const [notificationPermission, setNotificationPermission] = useState('default')
  const prevBiasesRef = useRef({})
  const audioRef = useRef(null)

  // Stock signal movement alerts state
  const [signalMovementAlerts, setSignalMovementAlerts] = useState([])
  const prevStockSignalsRef = useRef({})

  // Request notification permission on mount
  useEffect(() => {
    if ('Notification' in window) {
      setNotificationPermission(Notification.permission)
      if (Notification.permission === 'default') {
        Notification.requestPermission().then(perm => setNotificationPermission(perm))
      }
    }
  }, [])

  // Toggle notifications
  const toggleNotifications = useCallback(() => {
    const newValue = !notificationsEnabled
    setNotificationsEnabled(newValue)
    localStorage.setItem('biasNotificationsEnabled', String(newValue))
    if (newValue && 'Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission().then(perm => setNotificationPermission(perm))
    }
  }, [notificationsEnabled])

  // Dismiss a single alert
  const dismissAlert = useCallback((alertId) => {
    setBiasAlerts(prev => prev.filter(a => a.id !== alertId))
  }, [])

  // Dismiss all alerts
  const dismissAllAlerts = useCallback(() => {
    setBiasAlerts([])
  }, [])

  // Dismiss a single signal movement alert
  const dismissSignalMovementAlert = useCallback((alertId) => {
    setSignalMovementAlerts(prev => prev.filter(a => a.id !== alertId))
  }, [])

  // Dismiss all signal movement alerts
  const dismissAllSignalMovementAlerts = useCallback(() => {
    setSignalMovementAlerts([])
  }, [])

  const applyDashboardSupplemental = useCallback((payload) => {
    setFyersScreener(payload?.fyers_screener || null)
    setInstitutionalShadow(payload?.institutional_shadow || null)
    setMarketSession(payload?.market_session || null)
  }, [])

  const refreshDashboardSupplemental = useCallback(async (signature) => {
    if (!signature) return

    try {
      const payload = await fetchAgentDashboardSupplemental(signature)
      applyDashboardSupplemental(payload)
    } catch (error) {
      console.error('Error refreshing dashboard supplemental data:', error)
    }
  }, [applyDashboardSupplemental])

  // Detect bias changes and trigger notifications
  useEffect(() => {
    if (!fyersScreener?.available || !notificationsEnabled) return

    const indexRecs = fyersScreener.data?.index_recommendations || []
    const newAlerts = []

    for (const rec of indexRecs) {
      const indexName = rec.index
      const newBias = rec.signal // BULLISH, BEARISH, NEUTRAL
      const prevBias = prevBiasesRef.current[indexName]

      if (shouldNotifyBiasChange(prevBias, newBias)) {
        const alertId = `${indexName}-${Date.now()}`
        const alertMsg = `${indexName}: ${prevBias} → ${newBias}`

        newAlerts.push({
          id: alertId,
          index: indexName,
          from: prevBias,
          to: newBias,
          message: alertMsg,
          timestamp: new Date().toLocaleTimeString(),
        })

        // Browser notification
        if ('Notification' in window && Notification.permission === 'granted') {
          new Notification(`Bias Change: ${indexName}`, {
            body: `${prevBias} → ${newBias}`,
            icon: newBias === 'BULLISH' ? '🟢' : '🔴',
            tag: alertId,
          })
        }

        // Play alert sound
        if (audioRef.current) {
          audioRef.current.currentTime = 0
          audioRef.current.play().catch(() => { })
        }
      }

      // Update previous bias
      prevBiasesRef.current[indexName] = newBias
    }

    if (newAlerts.length > 0) {
      setBiasAlerts(prev => [...newAlerts, ...prev].slice(0, 10)) // Keep last 10 alerts
    }
  }, [fyersScreener, notificationsEnabled])

  // Detect stock signal movements and trigger notifications
  useEffect(() => {
    if (!fyersScreener?.available || !notificationsEnabled) return

    const results = fyersScreener.data?.results || []
    const watchlistBaskets = fyersScreener.data?.watchlist_baskets || {}
    const movements = {} // { 'BUY→SELL': [symbols], 'SELL→BUY': [symbols], ... }

    // Helper to find which baskets a symbol belongs to
    const getSymbolBaskets = (symbol) => {
      const baskets = []
      for (const [basketName, basketSymbols] of Object.entries(watchlistBaskets)) {
        if (basketSymbols.includes(symbol)) {
          baskets.push(basketName)
        }
      }
      return baskets.length > 0 ? baskets : ['OTHER']
    }

    for (const row of results) {
      const symbol = row.symbol
      const newSignal = row.signal
      const prevSignal = prevStockSignalsRef.current[symbol]

      if (prevSignal) {
        const movementType = isSignificantMovement(prevSignal, newSignal)
        if (movementType) {
          if (!movements[movementType]) movements[movementType] = []
          movements[movementType].push({
            symbol,
            baskets: getSymbolBaskets(symbol),
          })
        }
      }

      // Update previous signal
      prevStockSignalsRef.current[symbol] = newSignal
    }

    // Create alerts for movements with 3+ stocks
    const newMovementAlerts = []
    for (const [movementType, symbolData] of Object.entries(movements)) {
      if (symbolData.length >= MOVEMENT_ALERT_THRESHOLD) {
        const alertId = `movement-${movementType}-${Date.now()}`
        const isBullish = movementType.includes('→BUY')
        const isBearish = movementType.includes('→SELL')

        // Group symbols by basket
        const symbolsByBasket = {}
        for (const { symbol, baskets } of symbolData) {
          for (const basket of baskets) {
            if (!symbolsByBasket[basket]) symbolsByBasket[basket] = []
            symbolsByBasket[basket].push(symbol)
          }
        }

        newMovementAlerts.push({
          id: alertId,
          type: movementType,
          count: symbolData.length,
          symbols: symbolData.map(d => d.symbol),
          symbolsByBasket,
          isBullish,
          isBearish,
          timestamp: new Date().toLocaleTimeString(),
        })

        // Browser notification
        if ('Notification' in window && Notification.permission === 'granted') {
          new Notification(`Signal Movement: ${movementType}`, {
            body: `${symbolData.length} stocks moved ${movementType}`,
            icon: isBullish ? '📈' : isBearish ? '📉' : '📊',
            tag: alertId,
          })
        }

        // Play alert sound
        if (audioRef.current) {
          audioRef.current.currentTime = 0
          audioRef.current.play().catch(() => { })
        }
      }
    }

    if (newMovementAlerts.length > 0) {
      setSignalMovementAlerts(prev => [...newMovementAlerts, ...prev].slice(0, 10))
    }
  }, [fyersScreener, notificationsEnabled])

  useEffect(() => {
    if (typeof document === 'undefined') return undefined

    const handleVisibilityChange = () => {
      setIsDocumentVisible(document.visibilityState !== 'hidden')
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  }, [])

  useEffect(() => {
    let cancelled = false

    if (!selectedAgent) {
      setAgentDetails(null)
      setEconomicData(null)
      setTasksData(null)
      setFyersScreener(null)
      setInstitutionalShadow(null)
      setLoading(false)
      return () => { cancelled = true }
    }

    const loadSelectedAgent = async () => {
      try {
        setLoading(true)
        setAgentDetails(null)
        setEconomicData(null)
        setTasksData(null)
        setInstitutionalShadow(null)

        const [details, economic, tasks, supplemental] = await Promise.allSettled([
          fetchAgentDetail(selectedAgent),
          fetchAgentEconomic(selectedAgent),
          fetchAgentTasks(selectedAgent),
          fetchAgentDashboardSupplemental(selectedAgent),
        ])

        if (cancelled) return

        setAgentDetails(details.status === 'fulfilled' ? details.value : null)
        setEconomicData(economic.status === 'fulfilled' ? economic.value : null)
        setTasksData(tasks.status === 'fulfilled' ? tasks.value : null)
        applyDashboardSupplemental(supplemental.status === 'fulfilled' ? supplemental.value : null)
      } catch (error) {
        if (!cancelled) {
          console.error('Error loading selected agent:', error)
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    loadSelectedAgent()

    return () => {
      cancelled = true
    }
  }, [applyDashboardSupplemental, selectedAgent])

  useEffect(() => {
    if (!selectedAgent || !isDocumentVisible) return

    const id = setInterval(() => {
      refreshDashboardSupplemental(selectedAgent)
    }, 15000)

    return () => clearInterval(id)
  }, [isDocumentVisible, refreshDashboardSupplemental, selectedAgent])

  if (!selectedAgent) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <AlertCircle className="w-16 h-16 text-gray-400 mx-auto mb-4" />
          <h2 className="text-2xl font-bold text-gray-600">No Agent Selected</h2>
          <p className="text-gray-500 mt-2">Select an agent from the sidebar to view details</p>
        </div>
      </div>
    )
  }

  if (loading || !agentDetails) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary-600"></div>
      </div>
    )
  }

  const { current_status, balance_history, decisions } = agentDetails

  const getStatusColor = (status) => {
    switch (status) {
      case 'thriving':
        return 'text-green-600 bg-green-50 border-green-200'
      case 'stable':
        return 'text-blue-600 bg-blue-50 border-blue-200'
      case 'struggling':
        return 'text-yellow-600 bg-yellow-50 border-yellow-200'
      case 'bankrupt':
        return 'text-red-600 bg-red-50 border-red-200'
      default:
        return 'text-gray-600 bg-gray-50 border-gray-200'
    }
  }

  const getStatusEmoji = (status) => {
    switch (status) {
      case 'thriving':
        return '💪'
      case 'stable':
        return '👍'
      case 'struggling':
        return '⚠️'
      case 'bankrupt':
        return '💀'
      default:
        return '❓'
    }
  }

  const getActivityIcon = (activity) => {
    switch (activity) {
      case 'work':
        return <Briefcase className="w-5 h-5" />
      case 'learn':
        return <Brain className="w-5 h-5" />
      default:
        return <Activity className="w-5 h-5" />
    }
  }

  // Prepare chart data
  const balanceChartData = balance_history?.filter(item => item.date !== 'initialization').map(item => ({
    date: item.date,
    balance: item.balance,
    tokenCost: item.daily_token_cost || 0,
    workIncome: item.work_income_delta || 0,
  })) || []

  const QUALITY_CLIFF = 0.6

  // Domain earnings breakdown per occupation:
  //   earned  (green) — payment from tasks with score >= QUALITY_CLIFF
  //   failed  (red)   — task_value_usd of tasks that were completed but scored < QUALITY_CLIFF
  //                     (agent burned tokens, got almost nothing — a real loss)
  //   untapped (blue) — task_value_usd of tasks never completed
  const domainChartData = (() => {
    const tasks = tasksData?.tasks || []
    const byDomain = {}
    for (const t of tasks) {
      const domain = t.occupation || t.sector || 'Unknown'
      if (!byDomain[domain]) byDomain[domain] = { earned: 0, failed: 0, untapped: 0, totalTasks: 0 }
      byDomain[domain].totalTasks += 1
      const score = t.evaluation_score
      if (t.completed) {
        if (score === null || score === undefined || score >= QUALITY_CLIFF) {
          byDomain[domain].earned += (t.payment || 0)
        } else {
          // Worked but failed quality gate — show full task value as "loss"
          byDomain[domain].failed += (t.task_value_usd || 0)
        }
      } else {
        byDomain[domain].untapped += (t.task_value_usd || 0)
      }
    }
    return Object.entries(byDomain)
      .map(([domain, v]) => ({
        domain,
        earned: parseFloat(v.earned.toFixed(2)),
        failed: parseFloat(v.failed.toFixed(2)),
        untapped: parseFloat(v.untapped.toFixed(2)),
        totalTasks: v.totalTasks,
      }))
      .sort((a, b) => b.earned - a.earned)
  })()

  const screenerResults = fyersScreener?.data?.results || []
  const watchlistBaskets = fyersScreener?.data?.watchlist_baskets || {}
  const rawBasketSummaries = fyersScreener?.data?.basket_summaries || []
  const basketSummaryMap = rawBasketSummaries.reduce((acc, row) => {
    if (row?.basket) acc[row.basket] = row
    return acc
  }, {})
  const basketOptions = [
    ...DEFAULT_SCREENER_BASKETS.filter((basket) => watchlistBaskets[basket] || basketSummaryMap[basket]),
    ...Object.keys(watchlistBaskets).filter((basket) => !DEFAULT_SCREENER_BASKETS.includes(basket)),
    ...rawBasketSummaries
      .map((row) => row?.basket)
      .filter((basket) => basket && !DEFAULT_SCREENER_BASKETS.includes(basket) && !watchlistBaskets[basket]),
  ]
  const basketSummaryRows = basketOptions.map((basket) => (
    basketSummaryMap[basket] || {
      basket,
      total: (watchlistBaskets[basket] || []).length,
      buy_candidates: 0,
      sell_candidates: 0,
      watch: 0,
      overbought: 0,
      oversold: 0,
      missing_quotes: 0,
    }
  ))
  const basketFilteredResults = basketFilter === 'ALL'
    ? screenerResults
    : screenerResults.filter((row) => (watchlistBaskets[basketFilter] || []).includes(row.symbol))

  const signalCounts = basketFilteredResults.reduce((acc, row) => {
    if (!row?.signal) return acc
    acc[row.signal] = (acc[row.signal] || 0) + 1
    return acc
  }, {})
  const availableSignals = Object.keys(signalCounts)
  const effectiveSignalFilter = signalFilter !== 'ALL' && !availableSignals.includes(signalFilter)
    ? 'ALL'
    : signalFilter
  const filteredResults = effectiveSignalFilter === 'ALL'
    ? basketFilteredResults
    : basketFilteredResults.filter((row) => row.signal === effectiveSignalFilter)
  const sortedResults = [...filteredResults]

  const basketCounts = basketOptions.reduce((acc, basket) => {
    acc[basket] = (watchlistBaskets[basket] || []).length
    return acc
  }, { ALL: screenerResults.length })

  if (signalSort === 'signal') {
    const signalPriority = { BUY_CANDIDATE: 0, SELL_CANDIDATE: 1, WATCH: 2, OVERBOUGHT: 3, OVERSOLD: 4, AVOID: 5 }
    sortedResults.sort((a, b) => {
      const priorityA = signalPriority[a.signal] ?? 99
      const priorityB = signalPriority[b.signal] ?? 99
      if (priorityA !== priorityB) return priorityA - priorityB
      return (a.symbol || '').localeCompare(b.symbol || '')
    })
  }

  const visibleCount = resultsView === 'few'
    ? 8
    : resultsView === 'more'
      ? 20
      : sortedResults.length
  const visibleResults = sortedResults.slice(0, visibleCount)

  return (
    <div className="p-4 md:p-6 lg:p-8 space-y-4 md:space-y-6 max-w-full overflow-x-hidden">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3"
      >
        <div className="min-w-0 flex-1">
          <h1 className="text-xl md:text-3xl font-bold text-gray-900 truncate">{dn(selectedAgent)}</h1>
          <p className="text-xs md:text-base text-gray-500 mt-1">Dashboard - Live Monitoring</p>
        </div>
        <div className={`px-3 md:px-6 py-2 md:py-3 rounded-lg md:rounded-xl border-2 text-sm md:text-base font-semibold uppercase tracking-wide ${getStatusColor(current_status.survival_status)}`}>
          {getStatusEmoji(current_status.survival_status)} {current_status.survival_status}
        </div>
      </motion.div>

      {/* Metrics Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 md:gap-4 lg:gap-6">
        <MetricCard
          title="Starter Asset"
          value={formatINR(balance_history?.[0]?.balance || 0)}
          icon={<Wallet className="w-6 h-6" />}
          color="gray"
        />
        <MetricCard
          title="Balance"
          value={formatINR(current_status.balance || 0)}
          icon={<DollarSign className="w-6 h-6" />}
          color="blue"
          trend={balance_history?.length > 1 ?
            ((balance_history[balance_history.length - 1].balance - balance_history[0].balance) / balance_history[0].balance * 100).toFixed(1) :
            '0'
          }
        />
        <MetricCard
          title="Net Worth"
          value={formatINR(current_status.net_worth || 0)}
          icon={<TrendingUp className="w-6 h-6" />}
          color="green"
        />
        <MetricCard
          title="Total Token Cost"
          value={formatINR(current_status.total_token_cost || 0)}
          icon={<Activity className="w-6 h-6" />}
          color="red"
        />
        <MetricCard
          title="Work Income"
          value={formatINR(current_status.total_work_income || 0)}
          icon={<Briefcase className="w-6 h-6" />}
          color="purple"
        />
        <MetricCard
          title="Avg Quality Score"
          value={current_status.avg_evaluation_score !== null && current_status.avg_evaluation_score !== undefined
            ? `${(current_status.avg_evaluation_score * 100).toFixed(1)}%`
            : 'N/A'}
          icon={<Activity className="w-6 h-6" />}
          color="orange"
          subtitle={current_status.num_evaluations > 0 ? `${current_status.num_evaluations} tasks` : ''}
        />
      </div>

      {/* Current Activity */}
      {current_status.current_activity && (
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          className="bg-gradient-to-r from-primary-500 to-purple-600 rounded-xl md:rounded-2xl p-4 md:p-6 text-white shadow-lg"
        >
          <div className="flex items-center space-x-3 md:space-x-4">
            <div className="w-10 h-10 md:w-12 md:h-12 bg-white/20 rounded-lg md:rounded-xl flex items-center justify-center animate-pulse-slow flex-shrink-0">
              {getActivityIcon(current_status.current_activity)}
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-xs md:text-sm font-medium opacity-90">Currently Active</p>
              <p className="text-lg md:text-2xl font-bold capitalize truncate">{current_status.current_activity}</p>
            </div>
            <div className="text-right flex-shrink-0">
              <p className="text-xs md:text-sm opacity-90">Date</p>
              <p className="text-sm md:text-base font-semibold">{current_status.current_date}</p>
            </div>
          </div>
        </motion.div>
      )}

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 md:gap-6">
        {/* Balance History Chart */}
        <motion.div
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.2 }}
          className="bg-white rounded-xl md:rounded-2xl p-4 md:p-6 shadow-sm border border-gray-200 min-w-0"
        >
          <h3 className="text-base md:text-lg font-semibold text-gray-900 mb-4">Balance History</h3>
          <ResponsiveContainer width="100%" height={250}>
            <AreaChart data={balanceChartData}>
              <defs>
                <linearGradient id="colorBalance" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#0ea5e9" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#0ea5e9" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 11 }}
                interval={Math.max(0, Math.floor(balanceChartData.length / 8) - 1)}
                angle={-45}
                textAnchor="end"
                height={60}
                tickFormatter={(d) => { const p = d.split('-'); return p.length === 3 ? `${p[1]}/${p[2]}` : d }}
              />
              <YAxis tick={{ fontSize: 12 }} tickFormatter={(v) => formatINR(v, 0)} />
              <Tooltip
                contentStyle={{
                  backgroundColor: 'white',
                  border: '1px solid #e5e7eb',
                  borderRadius: '8px',
                  boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)',
                }}
                labelFormatter={(d) => `Date: ${d}`}
                formatter={(value) => [formatINR(value), 'Balance']}
              />
              <Area
                type="monotone"
                dataKey="balance"
                stroke="#0ea5e9"
                strokeWidth={2}
                fill="url(#colorBalance)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </motion.div>

        {/* Domain Earnings Distribution */}
        <motion.div
          initial={{ opacity: 0, x: 20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.3 }}
          className="bg-white rounded-xl md:rounded-2xl p-4 md:p-6 shadow-sm border border-gray-200 min-w-0"
        >
          <h3 className="text-base md:text-lg font-semibold text-gray-900 mb-1">Domain Earnings</h3>
          <p className="text-[10px] md:text-xs text-gray-400 mb-4 flex flex-wrap gap-2">
            <span><span className="inline-block w-2 h-2 rounded-sm bg-green-500 mr-1" />Earned</span>
            <span><span className="inline-block w-2 h-2 rounded-sm bg-red-400 mr-1" />Failed</span>
            <span><span className="inline-block w-2 h-2 rounded-sm bg-slate-300 mr-1" />Untapped</span>
          </p>
          {domainChartData.length === 0 ? (
            <div className="flex items-center justify-center h-[300px] text-gray-400 text-sm">No task data yet</div>
          ) : (
            <ResponsiveContainer width="100%" height={Math.max(300, domainChartData.length * 38)}>
              <BarChart
                data={domainChartData}
                layout="vertical"
                margin={{ left: 8, right: 48, top: 4, bottom: 4 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" horizontal={false} />
                <XAxis
                  type="number"
                  tick={{ fontSize: 11 }}
                  tickFormatter={v => formatINR(v, 0)}
                />
                <YAxis
                  type="category"
                  dataKey="domain"
                  tick={{ fontSize: 11 }}
                  width={160}
                  tickFormatter={s => s.length > 24 ? s.slice(0, 22) + '…' : s}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: 'white',
                    border: '1px solid #e5e7eb',
                    borderRadius: '8px',
                    boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)',
                    fontSize: 12,
                  }}
                  formatter={(value, name) => {
                    const labels = { earned: 'Earned', failed: 'Failed & wasted', untapped: 'Untapped potential' }
                    return [formatINR(value), labels[name] || name]
                  }}
                  labelFormatter={(label, payload) => {
                    const d = payload?.[0]?.payload
                    return d ? `${label} (${d.totalTasks} task${d.totalTasks !== 1 ? 's' : ''})` : label
                  }}
                />
                <Legend formatter={n => ({ earned: 'Earned', failed: 'Failed & wasted', untapped: 'Untapped potential' }[n] || n)} />
                <Bar dataKey="earned" stackId="a" fill="#22c55e" radius={[0, 0, 0, 0]} />
                <Bar dataKey="failed" stackId="a" fill="#f87171" radius={[0, 0, 0, 0]} />
                <Bar dataKey="untapped" stackId="a" fill="#94a3b8" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </motion.div>
      </div>

      {/* Market Session Status */}
      {marketSession && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.32 }}
          className={`rounded-xl md:rounded-2xl p-3 md:p-4 shadow-sm border min-w-0 overflow-hidden mb-4 ${marketSession.can_trade
              ? marketSession.session === 'PRIME_TIME'
                ? 'bg-green-50 border-green-200'
                : 'bg-yellow-50 border-yellow-200'
              : 'bg-red-50 border-red-200'
            }`}
        >
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
            <div className="flex items-center gap-3">
              <div className={`w-3 h-3 rounded-full ${marketSession.can_trade
                  ? marketSession.session === 'PRIME_TIME' ? 'bg-green-500 animate-pulse' : 'bg-yellow-500'
                  : 'bg-red-500'
                }`} />
              <div>
                <span className={`font-semibold ${marketSession.can_trade
                    ? marketSession.session === 'PRIME_TIME' ? 'text-green-800' : 'text-yellow-800'
                    : 'text-red-800'
                  }`}>
                  {marketSession.session?.replace(/_/g, ' ')}
                </span>
                <span className="text-gray-500 text-sm ml-2">
                  {marketSession.day_type?.replace(/_/g, ' ')}
                </span>
              </div>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`text-xs px-2 py-1 rounded-full font-medium ${marketSession.can_trade ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                }`}>
                {marketSession.can_trade ? '✓ Trading Allowed' : '✗ No Trading'}
              </span>
              {marketSession.day_rules?.theta_warning && (
                <span className="text-xs px-2 py-1 rounded-full bg-orange-100 text-orange-700 font-medium">
                  ⚠ Theta Warning
                </span>
              )}
            </div>
          </div>
          {marketSession.warning && (
            <p className={`text-xs mt-2 ${marketSession.can_trade ? 'text-yellow-700' : 'text-red-700'
              }`}>
              {marketSession.warning}
            </p>
          )}
          <p className="text-xs text-gray-500 mt-1">
            {marketSession.recommended_action}
          </p>
        </motion.div>
      )}

      {/* FYERS Screener */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.35 }}
        className="bg-white rounded-xl md:rounded-2xl p-3 md:p-6 shadow-sm border border-gray-200 min-w-0 overflow-hidden"
      >
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between mb-4 gap-2">
          <h3 className="text-base md:text-lg font-semibold text-gray-900">FYERS Screener</h3>
          <div className="flex items-center gap-2 flex-wrap">
            {fyersScreener?.available && (fyersScreener.data?.missing_quote_symbols || []).length > 0 && (
              <span className="text-[10px] md:text-[11px] px-2 py-1 rounded-full bg-amber-100 text-amber-700 font-medium">
                {(fyersScreener.data?.missing_quote_symbols || []).length} missing
              </span>
            )}
            {fyersScreener?.available && (
              <span className="text-[10px] md:text-xs text-gray-500 truncate max-w-[150px]">{fyersScreener.file}</span>
            )}
          </div>
        </div>

        {!fyersScreener?.available ? (
          <div className="text-sm text-gray-500">
            <div>{fyersScreener?.message || <>No screener run found yet. Run <span className="font-mono">./scripts/fyers_screener.sh</span> and refresh.</>}</div>
            {fyersScreener?.hint && (
              <div className="mt-2 font-mono text-xs text-amber-700 break-all">{fyersScreener.hint}</div>
            )}
          </div>
        ) : (
          <>
            <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-7 gap-2 md:gap-3 mb-4">
              <div className="rounded-lg bg-gray-50 p-2 md:p-3 border border-gray-100">
                <p className="text-[10px] md:text-xs text-gray-500">Total</p>
                <p className="text-base md:text-lg font-semibold text-gray-900">{fyersScreener.data?.summary?.total ?? 0}</p>
              </div>
              <div className="rounded-lg bg-green-50 p-2 md:p-3 border border-green-100">
                <p className="text-[10px] md:text-xs text-green-700">Buy</p>
                <p className="text-base md:text-lg font-semibold text-green-700">{fyersScreener.data?.summary?.buy_candidates ?? 0}</p>
              </div>
              <div className="rounded-lg bg-pink-50 p-2 md:p-3 border border-pink-100">
                <p className="text-[10px] md:text-xs text-pink-700">Sell</p>
                <p className="text-base md:text-lg font-semibold text-pink-700">{fyersScreener.data?.summary?.sell_candidates ?? 0}</p>
              </div>
              <div className="rounded-lg bg-blue-50 p-2 md:p-3 border border-blue-100">
                <p className="text-[10px] md:text-xs text-blue-700">Watch</p>
                <p className="text-base md:text-lg font-semibold text-blue-700">{fyersScreener.data?.summary?.watch ?? 0}</p>
              </div>
              <div className="rounded-lg bg-orange-50 p-2 md:p-3 border border-orange-100">
                <p className="text-[10px] md:text-xs text-orange-700">OB</p>
                <p className="text-base md:text-lg font-semibold text-orange-700">{fyersScreener.data?.summary?.overbought ?? 0}</p>
              </div>
              <div className="rounded-lg bg-purple-50 p-2 md:p-3 border border-purple-100">
                <p className="text-[10px] md:text-xs text-purple-700">OS</p>
                <p className="text-base md:text-lg font-semibold text-purple-700">{fyersScreener.data?.summary?.oversold ?? 0}</p>
              </div>
              <div className="rounded-lg bg-red-50 p-2 md:p-3 border border-red-100">
                <p className="text-[10px] md:text-xs text-red-700">Avoid</p>
                <p className="text-base md:text-lg font-semibold text-red-700">{fyersScreener.data?.summary?.avoid ?? 0}</p>
              </div>
            </div>

            {/* Stock Signal Movement Alerts */}
            <AnimatePresence>
              {signalMovementAlerts.length > 0 && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  className="mb-4"
                >
                  <div className="flex items-center justify-between mb-2">
                    <h4 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
                      <Activity className="w-4 h-4" />
                      Signal Movement Alerts
                    </h4>
                    <button
                      type="button"
                      onClick={dismissAllSignalMovementAlerts}
                      className="text-xs text-gray-500 hover:text-gray-700"
                    >
                      Clear all
                    </button>
                  </div>
                  <div className="space-y-2">
                    {signalMovementAlerts.map((alert) => (
                      <motion.div
                        key={alert.id}
                        initial={{ opacity: 0, x: -20 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: 20 }}
                        className={`rounded-lg border p-3 ${alert.isBullish
                            ? 'bg-green-50 border-green-200'
                            : alert.isBearish
                              ? 'bg-red-50 border-red-200'
                              : 'bg-blue-50 border-blue-200'
                          }`}
                      >
                        <div className="flex items-start justify-between">
                          <div className="flex items-center gap-2">
                            <span className="text-xl">
                              {alert.isBullish ? '📈' : alert.isBearish ? '📉' : '📊'}
                            </span>
                            <div>
                              <p className={`text-sm font-bold ${alert.isBullish
                                  ? 'text-green-800'
                                  : alert.isBearish
                                    ? 'text-red-800'
                                    : 'text-blue-800'
                                }`}>
                                {alert.type}: {alert.count} stocks
                              </p>
                              <p className="text-xs text-gray-500 mt-0.5">{alert.timestamp}</p>
                            </div>
                          </div>
                          <button
                            type="button"
                            onClick={() => dismissSignalMovementAlert(alert.id)}
                            className="p-1 hover:bg-white/50 rounded"
                          >
                            <X className="w-4 h-4 text-gray-400" />
                          </button>
                        </div>
                        <div className="mt-2 space-y-1.5">
                          {alert.symbolsByBasket && Object.entries(alert.symbolsByBasket).map(([basket, symbols]) => (
                            <div key={basket} className="flex flex-wrap items-center gap-1">
                              <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold uppercase tracking-wide ${basket === 'SENSEX' ? 'bg-indigo-100 text-indigo-700' :
                                  basket === 'NIFTY50' ? 'bg-emerald-100 text-emerald-700' :
                                    basket === 'BANKNIFTY' ? 'bg-amber-100 text-amber-700' :
                                      'bg-gray-100 text-gray-600'
                                }`}>
                                {basket}:
                              </span>
                              {symbols.slice(0, 8).map((symbol) => (
                                <span
                                  key={`${basket}-${symbol}`}
                                  className={`text-[11px] px-1.5 py-0.5 rounded font-medium ${alert.isBullish
                                      ? 'bg-green-100 text-green-700'
                                      : alert.isBearish
                                        ? 'bg-red-100 text-red-700'
                                        : 'bg-blue-100 text-blue-700'
                                    }`}
                                >
                                  {symbol.replace('NSE:', '').replace('-EQ', '')}
                                </span>
                              ))}
                              {symbols.length > 8 && (
                                <span className="text-[11px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 font-medium">
                                  +{symbols.length - 8}
                                </span>
                              )}
                            </div>
                          ))}
                          {(!alert.symbolsByBasket || Object.keys(alert.symbolsByBasket).length === 0) && (
                            <div className="flex flex-wrap gap-1">
                              {alert.symbols.slice(0, 10).map((symbol) => (
                                <span
                                  key={symbol}
                                  className={`text-[11px] px-1.5 py-0.5 rounded font-medium ${alert.isBullish
                                      ? 'bg-green-100 text-green-700'
                                      : alert.isBearish
                                        ? 'bg-red-100 text-red-700'
                                        : 'bg-blue-100 text-blue-700'
                                    }`}
                                >
                                  {symbol.replace('NSE:', '').replace('-EQ', '')}
                                </span>
                              ))}
                            </div>
                          )}
                          {alert.symbols.length > 10 && !alert.symbolsByBasket && (
                            <span className="text-[11px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 font-medium">
                              +{alert.symbols.length - 10} more
                            </span>
                          )}
                        </div>
                      </motion.div>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {basketSummaryRows.length > 0 && (
              <div className="mb-3 overflow-x-auto">
                <table className="min-w-full text-[13px] table-fixed">
                  <colgroup>
                    <col className="w-[18%]" />
                    <col className="w-[10%]" />
                    <col className="w-[12%]" />
                    <col className="w-[12%]" />
                    <col className="w-[10%]" />
                    <col className="w-[10%]" />
                    <col className="w-[10%]" />
                    <col className="w-[18%]" />
                  </colgroup>
                  <thead>
                    <tr className="border-b border-gray-100">
                      <th className="text-left py-1.5 pr-2 font-semibold text-gray-800 bg-gray-100">Basket</th>
                      <th className="text-center py-1.5 px-1 font-semibold text-gray-800 bg-gray-100">Total</th>
                      <th className="text-center py-1.5 px-1 font-semibold text-green-800 bg-green-100 whitespace-nowrap">Buy</th>
                      <th className="text-center py-1.5 px-1 font-semibold text-pink-800 bg-pink-100 whitespace-nowrap">Sell</th>
                      <th className="text-center py-1.5 px-1 font-semibold text-blue-800 bg-blue-100">Watch</th>
                      <th className="text-center py-1.5 px-1 font-semibold text-orange-800 bg-orange-100 whitespace-nowrap">OB</th>
                      <th className="text-center py-1.5 px-1 font-semibold text-purple-800 bg-purple-100 whitespace-nowrap">OS</th>
                      <th className="text-center py-1.5 px-1 font-semibold text-amber-800 bg-amber-100 whitespace-nowrap">Missing</th>
                    </tr>
                  </thead>
                  <tbody>
                    {basketSummaryRows.map((row, idx) => (
                      <tr key={`${row.basket}-${idx}`} className="border-b border-gray-50">
                        <td className="py-1.5 pr-2 text-gray-900 font-semibold tracking-wide">{row.basket}</td>
                        <td className="py-1.5 px-2 text-center bg-gray-50 text-gray-800 font-semibold">{row.total ?? 0}</td>
                        <td className="py-1.5 px-2 text-center bg-green-50 text-green-700 font-semibold">{row.buy_candidates ?? 0}</td>
                        <td className="py-1.5 px-2 text-center bg-pink-50 text-pink-700 font-semibold">{row.sell_candidates ?? 0}</td>
                        <td className="py-1.5 px-2 text-center bg-blue-50 text-blue-700 font-semibold">{row.watch ?? 0}</td>
                        <td className="py-1.5 px-2 text-center bg-orange-50 text-orange-700 font-semibold">{row.overbought ?? 0}</td>
                        <td className="py-1.5 px-2 text-center bg-purple-50 text-purple-700 font-semibold">{row.oversold ?? 0}</td>
                        <td className="py-1.5 px-2 text-center bg-amber-50 text-amber-700 font-semibold">{row.missing_quotes ?? 0}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {((fyersScreener.data?.warnings || []).length > 0 || (fyersScreener.data?.missing_quote_symbols || []).length > 0) && (
              <div className="text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded-lg p-2 mb-4">
                {(fyersScreener.data?.warnings || []).map((warning, idx) => (
                  <p key={`fyers-warning-${idx}`}>{warning}</p>
                ))}
                {(fyersScreener.data?.missing_quote_symbols || []).length > 0 && (
                  <p>
                    Missing symbols: {(fyersScreener.data?.missing_quote_symbols || []).join(', ')}
                  </p>
                )}
              </div>
            )}

            <div className="mb-4">
              <div className="flex items-center justify-between mb-2">
                <h4 className="text-sm font-semibold text-gray-900">Index + Strike Recommender</h4>
                <div className="flex items-center gap-2">
                  {biasAlerts.length > 0 && (
                    <button
                      type="button"
                      onClick={dismissAllAlerts}
                      className="text-xs text-gray-500 hover:text-gray-700"
                    >
                      Clear alerts
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={toggleNotifications}
                    className={`flex items-center gap-1 px-2 py-1 rounded text-xs font-medium border transition-colors ${notificationsEnabled
                        ? 'bg-green-50 border-green-200 text-green-700 hover:bg-green-100'
                        : 'bg-gray-50 border-gray-200 text-gray-500 hover:bg-gray-100'
                      }`}
                    title={notificationsEnabled ? 'Bias alerts ON' : 'Bias alerts OFF'}
                  >
                    {notificationsEnabled ? <Bell className="w-3.5 h-3.5" /> : <BellOff className="w-3.5 h-3.5" />}
                    {notificationsEnabled ? 'Alerts ON' : 'Alerts OFF'}
                  </button>
                </div>
              </div>

              {/* Bias Change Alerts */}
              <AnimatePresence>
                {biasAlerts.length > 0 && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    exit={{ opacity: 0, height: 0 }}
                    className="mb-3 space-y-2"
                  >
                    {biasAlerts.map((alert) => (
                      <motion.div
                        key={alert.id}
                        initial={{ opacity: 0, x: -20 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: 20 }}
                        className={`flex items-center justify-between px-3 py-2 rounded-lg border ${alert.to === 'BULLISH'
                            ? 'bg-green-50 border-green-200'
                            : 'bg-red-50 border-red-200'
                          }`}
                      >
                        <div className="flex items-center gap-2">
                          <span className="text-lg">{alert.to === 'BULLISH' ? '🟢' : '🔴'}</span>
                          <div>
                            <p className={`text-sm font-semibold ${alert.to === 'BULLISH' ? 'text-green-800' : 'text-red-800'
                              }`}>
                              {alert.index}: {alert.from} → {alert.to}
                            </p>
                            <p className="text-xs text-gray-500">{alert.timestamp}</p>
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => dismissAlert(alert.id)}
                          className="p-1 hover:bg-white/50 rounded"
                        >
                          <X className="w-4 h-4 text-gray-400" />
                        </button>
                      </motion.div>
                    ))}
                  </motion.div>
                )}
              </AnimatePresence>

              {/* Hidden audio element for alert sound */}
              <audio ref={audioRef} preload="auto">
                <source src="data:audio/wav;base64,UklGRnoGAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQoGAACBhYqFbF1fdJivrJBhNjVgodDbq2EcBj+a2teleQgAQZvYxaV2DgE7kc7Yl38qGUyRwMmmjVU7U4Gsw7GYXTg5V4qru6+OVzU2X4ymtrqkdDswQHmdrbS3o3E5K0V8mbGzt6F9RCxAe5ixsremiFQ2OnqZrrW0pIdPNTx4mK61tKWIUjU8eJiutbSliFI1PHiYrrW0pYhSNTx4mK61tKWIUjU8eJiutbSliFI1PHiYrrW0pYhSNTx4mK61tKWIUjU8eJiutbSliFI1PHiY" type="audio/wav" />
              </audio>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
                <div className="rounded-lg bg-gray-50 p-3 border border-gray-100">
                  <p className="text-xs text-gray-500">Tracked</p>
                  <p className="text-base font-semibold text-gray-900">{fyersScreener.data?.index_summary?.tracked ?? 0}</p>
                </div>
                <div className="rounded-lg bg-green-50 p-3 border border-green-100">
                  <p className="text-xs text-green-700">Bullish</p>
                  <p className="text-base font-semibold text-green-700">{fyersScreener.data?.index_summary?.bullish ?? 0}</p>
                </div>
                <div className="rounded-lg bg-red-50 p-3 border border-red-100">
                  <p className="text-xs text-red-700">Bearish</p>
                  <p className="text-base font-semibold text-red-700">{fyersScreener.data?.index_summary?.bearish ?? 0}</p>
                </div>
                <div className="rounded-lg bg-blue-50 p-3 border border-blue-100">
                  <p className="text-xs text-blue-700">Neutral</p>
                  <p className="text-base font-semibold text-blue-700">{fyersScreener.data?.index_summary?.neutral ?? 0}</p>
                </div>
              </div>

              {fyersScreener.data?.index_error && (
                <div className="text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded-lg p-2 mb-2">
                  Index recommendation unavailable: {fyersScreener.data?.index_error}
                </div>
              )}

              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-100 text-gray-500">
                      <th className="text-left py-2 pr-3 font-medium">Index</th>
                      <th className="text-left py-2 pr-3 font-medium">Bias</th>
                      <th className="text-left py-2 pr-3 font-medium">Side</th>
                      <th className="text-right py-2 pr-3 font-medium">LTP</th>
                      <th className="text-right py-2 pr-3 font-medium">Change %</th>
                      <th className="text-right py-2 pr-3 font-medium">Preferred Strike</th>
                      <th className="text-right py-2 pr-3 font-medium">Confidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(fyersScreener.data?.index_recommendations || []).map((row, idx) => (
                      <tr key={`${row.index}-${idx}`} className="border-b border-gray-50">
                        <td className="py-2 pr-3 text-gray-900 font-medium">{row.index}</td>
                        <td className="py-2 pr-3">
                          <span className={`px-2 py-1 rounded text-xs font-semibold ${row.signal === 'BULLISH'
                              ? 'bg-green-100 text-green-700'
                              : row.signal === 'BEARISH'
                                ? 'bg-red-100 text-red-700'
                                : 'bg-blue-100 text-blue-700'
                            }`}>
                            {row.signal}
                          </span>
                        </td>
                        <td className="py-2 pr-3 text-gray-700">{row.option_side}</td>
                        <td className="py-2 pr-3 text-right text-gray-700">
                          {typeof row.ltp === 'number' ? row.ltp.toFixed(2) : 'NA'}
                        </td>
                        <td className="py-2 pr-3 text-right text-gray-700">
                          {typeof row.change_pct === 'number' ? `${row.change_pct.toFixed(2)}%` : 'NA'}
                        </td>
                        <td className="py-2 pr-3 text-right text-gray-700">
                          {row.preferred_strike || 'WAIT'}
                        </td>
                        <td className="py-2 pr-3 text-right text-gray-700">{row.confidence ?? 0}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="sticky top-0 z-10 mb-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-gray-100 bg-white/95 px-2 py-2 backdrop-blur">
              <div className="flex items-center gap-2 text-xs text-gray-600">
                <span className="font-medium">View:</span>
                <button
                  type="button"
                  onClick={() => setResultsView('few')}
                  className={`px-2.5 py-1 rounded border ${resultsView === 'few' ? 'bg-gray-100 border-gray-300 text-gray-900' : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'}`}
                >
                  Few
                </button>
                <button
                  type="button"
                  onClick={() => setResultsView('more')}
                  className={`px-2.5 py-1 rounded border ${resultsView === 'more' ? 'bg-gray-100 border-gray-300 text-gray-900' : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'}`}
                >
                  Little More
                </button>
                <button
                  type="button"
                  onClick={() => setResultsView('full')}
                  className={`px-2.5 py-1 rounded border ${resultsView === 'full' ? 'bg-gray-100 border-gray-300 text-gray-900' : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'}`}
                >
                  Full
                </button>
              </div>

              <div className="flex items-center gap-2 text-xs text-gray-600">
                <label className="font-medium" htmlFor="basket-filter">Basket:</label>
                <select
                  id="basket-filter"
                  value={basketFilter}
                  onChange={(e) => setBasketFilter(e.target.value)}
                  className="px-2 py-1 rounded border border-gray-200 bg-white text-gray-700"
                >
                  <option value="ALL">All ({basketCounts.ALL || 0})</option>
                  {basketOptions.map((basket) => (
                    <option key={basket} value={basket}>{basket} ({basketCounts[basket] || 0})</option>
                  ))}
                </select>

                <label className="font-medium" htmlFor="signal-filter">Filter Signal:</label>
                <select
                  id="signal-filter"
                  value={effectiveSignalFilter}
                  onChange={(e) => setSignalFilter(e.target.value)}
                  className="px-2 py-1 rounded border border-gray-200 bg-white text-gray-700"
                >
                  <option value="ALL">All ({basketFilteredResults.length})</option>
                  {availableSignals.map((signal) => (
                    <option key={signal} value={signal}>{signal} ({signalCounts[signal] || 0})</option>
                  ))}
                </select>

                <label className="font-medium" htmlFor="signal-sort">Sort:</label>
                <select
                  id="signal-sort"
                  value={signalSort}
                  onChange={(e) => setSignalSort(e.target.value)}
                  className="px-2 py-1 rounded border border-gray-200 bg-white text-gray-700"
                >
                  <option value="default">Default</option>
                  <option value="signal">By Signal</option>
                </select>

                <span className="text-gray-500">Showing {visibleResults.length} of {sortedResults.length}</span>
              </div>

              <div className="w-full flex flex-wrap items-center gap-1 text-xs">
                <button
                  type="button"
                  onClick={() => setBasketFilter('ALL')}
                  className={`px-2 py-1 rounded border ${basketFilter === 'ALL' ? 'bg-gray-100 border-gray-300 text-gray-900' : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'}`}
                >
                  All Baskets ({basketCounts.ALL || 0})
                </button>
                {basketOptions.map((basket) => (
                  <button
                    type="button"
                    key={`basket-chip-${basket}`}
                    onClick={() => setBasketFilter(basket)}
                    className={`px-2 py-1 rounded border ${basketFilter === basket ? 'bg-gray-100 border-gray-300 text-gray-900' : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'}`}
                  >
                    {basket} ({basketCounts[basket] || 0})
                  </button>
                ))}

                <button
                  type="button"
                  onClick={() => setSignalFilter('ALL')}
                  className={`px-2 py-1 rounded border ${effectiveSignalFilter === 'ALL' ? 'bg-gray-100 border-gray-300 text-gray-900' : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'}`}
                >
                  All Signals ({basketFilteredResults.length})
                </button>
                {availableSignals.map((signal) => {
                  const chipClasses = {
                    BUY_CANDIDATE: 'border-green-200 text-green-700 bg-green-50',
                    SELL_CANDIDATE: 'border-pink-200 text-pink-700 bg-pink-50',
                    WATCH: 'border-blue-200 text-blue-700 bg-blue-50',
                    OVERBOUGHT: 'border-orange-200 text-orange-700 bg-orange-50',
                    OVERSOLD: 'border-purple-200 text-purple-700 bg-purple-50',
                    AVOID: 'border-red-200 text-red-700 bg-red-50',
                  }
                  const chipClass = chipClasses[signal] || 'border-gray-200 text-gray-700 bg-gray-50'
                  const activeSignalClass = effectiveSignalFilter === signal ? 'ring-1 ring-gray-300' : ''

                  return (
                    <button
                      type="button"
                      key={`signal-chip-${signal}`}
                      onClick={() => setSignalFilter(signal)}
                      className={`px-2 py-1 rounded border ${chipClass} ${activeSignalClass}`}
                    >
                      {signal} ({signalCounts[signal] || 0})
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="max-h-[420px] overflow-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="sticky top-0 z-[1] border-b border-gray-100 text-gray-500 bg-white">
                    <th className="text-left py-2 pr-3 font-medium">Symbol</th>
                    <th className="text-left py-2 pr-3 font-medium">Signal</th>
                    <th className="text-right py-2 pr-3 font-medium">LTP</th>
                    <th className="text-right py-2 pr-3 font-medium">Change %</th>
                    <th className="text-left py-2 font-medium">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleResults.map((row, idx) => (
                    <tr key={`${row.symbol}-${idx}`} className="border-b border-gray-50">
                      <td className="py-2 pr-3 text-gray-900 font-medium">{row.symbol}</td>
                      <td className="py-2 pr-3">
                        <span className={`px-2 py-1 rounded text-xs font-semibold ${row.signal === 'BUY_CANDIDATE'
                            ? 'bg-green-100 text-green-700'
                            : row.signal === 'SELL_CANDIDATE'
                              ? 'bg-pink-100 text-pink-700'
                              : row.signal === 'OVERBOUGHT'
                                ? 'bg-orange-100 text-orange-700'
                                : row.signal === 'OVERSOLD'
                                  ? 'bg-purple-100 text-purple-700'
                                  : row.signal === 'AVOID'
                                    ? 'bg-red-100 text-red-700'
                                    : 'bg-blue-100 text-blue-700'
                          }`}>
                          {row.signal}
                        </span>
                      </td>
                      <td className="py-2 pr-3 text-right text-gray-700">
                        {typeof row.last_price === 'number' ? row.last_price.toFixed(2) : 'NA'}
                      </td>
                      <td className="py-2 pr-3 text-right text-gray-700">
                        {typeof row.change_pct === 'number' ? `${row.change_pct.toFixed(2)}%` : 'NA'}
                      </td>
                      <td className="py-2 text-gray-600">{row.reason}</td>
                    </tr>
                  ))}
                  {visibleResults.length === 0 && (
                    <tr>
                      <td colSpan={5} className="py-3 text-center text-gray-500">No stocks match selected signal filter.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </motion.div>

      {/* Institutional Shadow */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.38 }}
        className="bg-white rounded-2xl p-6 shadow-sm border border-gray-200"
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-gray-900">Institutional Shadow (Latest)</h3>
          {institutionalShadow?.available && (
            <span className="text-xs text-gray-500">{institutionalShadow.date || institutionalShadow.timestamp}</span>
          )}
        </div>

        {!institutionalShadow?.available ? (
          <div className="text-sm text-gray-500">
            No institutional shadow audit found yet for this agent.
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
              <div className="rounded-lg bg-gray-50 p-3 border border-gray-100">
                <p className="text-xs text-gray-500">Status</p>
                <p className="text-base font-semibold text-gray-900">{institutionalShadow.institutional_shadow?.status || 'NA'}</p>
              </div>
              <div className="rounded-lg bg-gray-50 p-3 border border-gray-100">
                <p className="text-xs text-gray-500">Records</p>
                <p className="text-base font-semibold text-gray-900">{institutionalShadow.institutional_shadow?.record_count ?? 0}</p>
              </div>
              <div className="rounded-lg bg-green-50 p-3 border border-green-100">
                <p className="text-xs text-green-700">Agree</p>
                <p className="text-base font-semibold text-green-700">{institutionalShadow.institutional_shadow?.agree_count ?? 0}</p>
              </div>
              <div className="rounded-lg bg-red-50 p-3 border border-red-100">
                <p className="text-xs text-red-700">Disagree</p>
                <p className="text-base font-semibold text-red-700">{institutionalShadow.institutional_shadow?.disagree_count ?? 0}</p>
              </div>
              <div className="rounded-lg bg-blue-50 p-3 border border-blue-100">
                <p className="text-xs text-blue-700">Screener OK</p>
                <p className="text-base font-semibold text-blue-700">{institutionalShadow.success ? 'YES' : 'NO'}</p>
              </div>
            </div>
          </>
        )}
      </motion.div>

      {/* Recent Decisions */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.4 }}
        className="bg-white rounded-2xl p-6 shadow-sm border border-gray-200"
      >
        <h3 className="text-lg font-semibold text-gray-900 mb-4">Recent Decisions</h3>
        <p className="text-xs text-gray-500 mb-3">Badge “recovered” means the entry was restored from activity logs fallback.</p>
        <div className="space-y-3">
          {decisions?.slice(-5).reverse().map((decision, index) => (
            <div
              key={index}
              className="flex items-center space-x-4 p-4 bg-gray-50 rounded-xl hover:bg-gray-100 transition-colors"
            >
              <div className="w-10 h-10 bg-primary-100 rounded-lg flex items-center justify-center">
                {getActivityIcon(decision.activity)}
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <p className="font-medium text-gray-900 capitalize">{decision.activity}</p>
                  {decision.source === 'activity_logs' && (
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-50 text-amber-700 border border-amber-100">
                      recovered
                    </span>
                  )}
                </div>
                <p className="text-sm text-gray-500">{decision.reasoning}</p>
              </div>
              <div className="text-right">
                <p className="text-sm font-medium text-gray-900">{decision.date}</p>
              </div>
            </div>
          ))}
        </div>
      </motion.div>
    </div>
  )
}

const MetricCard = ({ title, value, icon, color, trend, subtitle }) => {
  const colorClasses = {
    blue: 'bg-blue-50 text-blue-600',
    green: 'bg-green-50 text-green-600',
    red: 'bg-red-50 text-red-600',
    purple: 'bg-purple-50 text-purple-600',
    orange: 'bg-orange-50 text-orange-600',
    gray: 'bg-gray-100 text-gray-500',
  }

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{ opacity: 1, scale: 1 }}
      className="bg-white rounded-xl md:rounded-2xl p-3 md:p-6 shadow-sm border border-gray-200 hover:shadow-md transition-shadow"
    >
      <div className="flex items-center justify-between mb-2 md:mb-3">
        <div className={`w-8 h-8 md:w-12 md:h-12 rounded-lg md:rounded-xl flex items-center justify-center ${colorClasses[color]}`}>
          <span className="scale-75 md:scale-100">{icon}</span>
        </div>
        {trend && (
          <span className={`text-xs md:text-sm font-medium ${parseFloat(trend) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
            {parseFloat(trend) >= 0 ? '+' : ''}{trend}%
          </span>
        )}
      </div>
      <p className="text-[10px] md:text-sm text-gray-500 mb-0.5 md:mb-1 truncate">{title}</p>
      <p className="text-base md:text-2xl font-bold text-gray-900 truncate">{value}</p>
      {subtitle && (
        <p className="text-[10px] md:text-xs text-gray-400 mt-0.5 md:mt-1">{subtitle}</p>
      )}
    </motion.div>
  )
}

export default Dashboard
