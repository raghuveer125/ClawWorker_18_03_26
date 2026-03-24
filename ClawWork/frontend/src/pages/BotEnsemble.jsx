import { AnimatePresence, motion } from 'framer-motion'
import { Activity, AlertCircle, AlertTriangle, ArrowDownRight, ArrowUpRight, BarChart2, Bot, Brain, Clock, DollarSign, Pause, Play, Power, RefreshCw, RotateCcw, Shield, ShieldCheck, Square, Target, TrendingDown, TrendingUp, Zap } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { fetchAgentLearningRoi, fetchAgents, fetchAutoTraderStatus, fetchBotDetails, fetchBotsLeaderboard, fetchBotsStatus, fetchEnsembleStats, fetchFyersn7TradeHistory, fetchGateStatus, fetchIctSniperStatus, fetchRHPipelineStatus, fetchTradeHistory, pauseAutoTrader, pauseRHPipeline, resetAutoTraderDaily, resetRHPipelineDaily, resumeAutoTrader, resumeRHPipeline, startAutoTrader, startRHPipeline, stopAutoTrader, stopRHPipeline, toggleTradingMode } from '../api'
import HybridPipelineCard from '../components/HybridPipelineCard'

const formatPct = (v) => `${(v || 0).toFixed(1)}%`
const formatNum = (v) => (v || 0).toFixed(2)

// Bot icons by strategy type
const BOT_ICONS = {
  TrendFollower: TrendingUp,
  ReversalHunter: TrendingDown,
  MomentumScalper: Zap,
  OIAnalyst: BarChart2,
  VolatilityTrader: Activity,
  LLMTrader: Brain,  // TRUE AI reasoning bot
  VetoLayer: ShieldCheck,  // Capital protection
  RegimeHunter: Target,  // Regime shift detector
}

const BOT_COLORS = {
  TrendFollower: 'from-cyan-500 to-blue-500',
  ReversalHunter: 'from-purple-500 to-pink-500',
  MomentumScalper: 'from-amber-500 to-orange-500',
  OIAnalyst: 'from-emerald-500 to-teal-500',
  VolatilityTrader: 'from-rose-500 to-red-500',
  LLMTrader: 'from-violet-500 to-purple-600',  // Special color for AI bot
  VetoLayer: 'from-green-500 to-emerald-600',  // Protection layer color
  RegimeHunter: 'from-yellow-500 to-amber-600',  // Regime transition hunter
}

// Live badge component
const LiveBadge = () => (
  <div className="flex items-center space-x-1.5 bg-red-950/60 border border-red-700/60 rounded-full px-3 py-1">
    <span className="relative flex h-2 w-2">
      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
      <span className="relative inline-flex rounded-full h-2 w-2 bg-red-500" />
    </span>
    <span className="text-xs font-bold tracking-widest text-red-400 uppercase">Live</span>
  </div>
)

// Bot card component
const BotCard = ({ bot, onClick }) => {
  const Icon = BOT_ICONS[bot.name] || Bot
  const gradient = BOT_COLORS[bot.name] || 'from-gray-500 to-slate-500'
  const perf = bot.performance || {}

  return (
    <motion.div
      whileHover={{ scale: 1.02, y: -4 }}
      whileTap={{ scale: 0.98 }}
      onClick={() => onClick(bot.name)}
      className="cursor-pointer relative overflow-hidden rounded-xl bg-gradient-to-br from-slate-800/90 to-slate-900/90 border border-slate-700/50 p-4 hover:border-cyan-500/50 transition-all"
    >
      {/* Gradient accent */}
      <div className={`absolute inset-0 bg-gradient-to-br ${gradient} opacity-10`} />

      <div className="relative z-10">
        {/* Header */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center space-x-2">
            <div className={`p-2 rounded-lg bg-gradient-to-br ${gradient}`}>
              <Icon className="w-5 h-5 text-white" />
            </div>
            <div>
              <h3 className="font-bold text-white">{bot.name}</h3>
              <p className="text-xs text-slate-400 truncate max-w-[180px]">{bot.description}</p>
            </div>
          </div>
          <div className={`text-sm font-mono px-2 py-0.5 rounded ${perf.weight >= 1.5 ? 'bg-green-500/20 text-green-400' :
            perf.weight >= 1.0 ? 'bg-cyan-500/20 text-cyan-400' :
              perf.weight >= 0.5 ? 'bg-amber-500/20 text-amber-400' :
                'bg-red-500/20 text-red-400'
            }`}>
            {formatNum(perf.weight)}x
          </div>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="bg-slate-800/50 rounded-lg p-2">
            <div className="text-xs text-slate-500">Win Rate</div>
            <div className={`text-lg font-bold ${perf.win_rate >= 60 ? 'text-green-400' : perf.win_rate >= 40 ? 'text-amber-400' : 'text-red-400'}`}>
              {formatPct(perf.win_rate)}
            </div>
          </div>
          <div className="bg-slate-800/50 rounded-lg p-2">
            <div className="text-xs text-slate-500">Trades</div>
            <div className="text-lg font-bold text-cyan-400">{perf.total_trades || 0}</div>
          </div>
          <div className="bg-slate-800/50 rounded-lg p-2">
            <div className="text-xs text-slate-500">P&L</div>
            <div className={`text-lg font-bold ${perf.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {perf.total_pnl >= 0 ? '+' : ''}{formatNum(perf.total_pnl)}
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  )
}

// ICT Sniper Detailed Card
const ICTSniperCard = ({ botData, onClick }) => {
  if (!botData) return null

  const perf = botData.performance || {}
  const config = botData.configuration || {}
  const setup = botData.setup_state || {}
  const signals = botData.recent_signals || []
  const multiTFSignals = botData.multi_timeframe_signals || {}

  const setupColor = (active) => active ? 'text-green-400' : 'text-slate-500'
  const setupBg = (active) => active ? 'bg-green-500/20' : 'bg-slate-500/10'

  // Get confluence level from latest signal
  const latestSignal = signals && signals.length > 0 ? signals[0] : null
  const confluence = latestSignal?.metadata?.confluence || 0
  const confluenceColor = confluence >= 3 ? 'text-green-400' : confluence >= 2 ? 'text-amber-400' : 'text-slate-500'

  const timeframeStatuses = {
    '1m': latestSignal?.metadata?.signal_1m || false,
    '5m': latestSignal?.metadata?.signal_5m || false,
    '15m': latestSignal?.metadata?.signal_15m || false,
  }

  return (
    <motion.div
      whileHover={{ scale: 1.01, y: -2 }}
      onClick={onClick}
      className="cursor-pointer relative overflow-hidden rounded-xl bg-gradient-to-br from-cyan-900/30 to-slate-900/90 border border-cyan-500/30 p-5 hover:border-cyan-400/50 transition-all"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4 pb-3 border-b border-cyan-500/20">
        <div>
          <h3 className="font-bold text-lg text-cyan-300">🎯 ICT Sniper Multi-TF</h3>
          <p className="text-xs text-slate-400">Smart Entry: LQ Grab + MSS + FVG/IFVG + OB (1m/5m/15m)</p>
        </div>
        <div className={`text-sm font-mono px-3 py-1 rounded ${perf.weight >= 1.5 ? 'bg-green-500/20 text-green-400' : 'bg-cyan-500/20 text-cyan-400'}`}>
          {(perf.weight || 0).toFixed(1)}x Weight
        </div>
      </div>

      {/* Multi-Timeframe Signal Status */}
      <div className="mb-4 bg-slate-800/30 rounded-lg p-3">
        <div className="text-xs text-slate-400 font-semibold mb-2">Multi-TF Signal Status</div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <div className={`${timeframeStatuses['1m'] ? 'bg-green-500/20 border border-green-500/50' : 'bg-slate-700/30 border border-slate-600/30'} rounded px-2 py-2 text-center`}>
            <div className="font-mono font-bold">1m</div>
            <div className={timeframeStatuses['1m'] ? 'text-green-400 font-bold' : 'text-slate-500'}>
              {timeframeStatuses['1m'] ? '✓ SIGNAL' : '—'}
            </div>
          </div>
          <div className={`${timeframeStatuses['5m'] ? 'bg-amber-500/20 border border-amber-500/50' : 'bg-slate-700/30 border border-slate-600/30'} rounded px-2 py-2 text-center`}>
            <div className="font-mono font-bold">5m</div>
            <div className={timeframeStatuses['5m'] ? 'text-amber-400 font-bold' : 'text-slate-500'}>
              {timeframeStatuses['5m'] ? '✓ SIGNAL' : '—'}
            </div>
          </div>
          <div className={`${timeframeStatuses['15m'] ? 'bg-cyan-500/20 border border-cyan-500/50' : 'bg-slate-700/30 border border-slate-600/30'} rounded px-2 py-2 text-center`}>
            <div className="font-mono font-bold">15m</div>
            <div className={timeframeStatuses['15m'] ? 'text-cyan-400 font-bold' : 'text-slate-500'}>
              {timeframeStatuses['15m'] ? '✓ SIGNAL' : '—'}
            </div>
          </div>
        </div>
        {confluence > 0 && (
          <div className={`mt-2 text-xs text-center font-bold ${confluenceColor}`}>
            Confluence: {confluence}/3 Timeframes
          </div>
        )}
      </div>

      {/* Performance Grid */}
      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="bg-slate-800/50 rounded-lg p-2">
          <div className="text-xs text-slate-500">Win Rate</div>
          <div className={`text-xl font-bold ${perf.win_rate >= 60 ? 'text-green-400' : 'text-amber-400'}`}>
            {(perf.win_rate || 0).toFixed(0)}%
          </div>
        </div>
        <div className="bg-slate-800/50 rounded-lg p-2">
          <div className="text-xs text-slate-500">Trades / Signals</div>
          <div className="text-xl font-bold text-cyan-400">{perf.total_trades || 0} / {perf.total_signals || 0}</div>
        </div>
      </div>

      {/* Setup State - Real-Time Status */}
      <div className="mb-4 bg-slate-800/30 rounded-lg p-3">
        <div className="text-xs text-slate-400 font-semibold mb-2">Active Setups</div>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className={`${setupBg(setup.bullish_setup_active)} ${setupColor(setup.bullish_setup_active)} rounded px-2 py-1`}>
            🟢 Bull LQ Grab {setup.bullish_setup_active ? '✓' : ''}
          </div>
          <div className={`${setupBg(setup.bearish_setup_active)} ${setupColor(setup.bearish_setup_active)} rounded px-2 py-1`}>
            🔴 Bear LQ Grab {setup.bearish_setup_active ? '✓' : ''}
          </div>
          <div className={`${setupBg(setup.bullish_mss_confirmed)} ${setupColor(setup.bullish_mss_confirmed)} rounded px-2 py-1`}>
            📈 Bull MSS {setup.bullish_mss_confirmed ? '✓' : ''}
          </div>
          <div className={`${setupBg(setup.bearish_mss_confirmed)} ${setupColor(setup.bearish_mss_confirmed)} rounded px-2 py-1`}>
            📉 Bear MSS {setup.bearish_mss_confirmed ? '✓' : ''}
          </div>
          <div className={`${setupBg(setup.bullish_fvg_active)} ${setupColor(setup.bullish_fvg_active)} rounded px-2 py-1`}>
            ▫ Bull FVG {setup.bullish_fvg_active ? '✓' : ''}
          </div>
          <div className={`${setupBg(setup.bearish_fvg_active)} ${setupColor(setup.bearish_fvg_active)} rounded px-2 py-1`}>
            ▪ Bear FVG {setup.bearish_fvg_active ? '✓' : ''}
          </div>
          <div className={`${setupBg(setup.bullish_ifvg_active)} ${setupColor(setup.bullish_ifvg_active)} rounded px-2 py-1`}>
            ◫ Bull IFVG {setup.bullish_ifvg_active ? '✓' : ''}
          </div>
          <div className={`${setupBg(setup.bearish_ifvg_active)} ${setupColor(setup.bearish_ifvg_active)} rounded px-2 py-1`}>
            ◪ Bear IFVG {setup.bearish_ifvg_active ? '✓' : ''}
          </div>
          <div className={`${setupBg(setup.bullish_order_block_active)} ${setupColor(setup.bullish_order_block_active)} rounded px-2 py-1`}>
            📦 Bull OB {setup.bullish_order_block_active ? '✓' : ''}
          </div>
          <div className={`${setupBg(setup.bearish_order_block_active)} ${setupColor(setup.bearish_order_block_active)} rounded px-2 py-1`}>
            📦 Bear OB {setup.bearish_order_block_active ? '✓' : ''}
          </div>
        </div>
      </div>

      {/* Configuration */}
      <div className="text-xs text-slate-400">
        <div className="grid grid-cols-2 gap-2 mb-2">
          <div>Swing LB: {config.swing_lookback} bars</div>
          <div>RR Ratio: {config.rr_ratio}:1</div>
          <div>Vol Mult: {(config.vol_multiplier || 0).toFixed(1)}x</div>
          <div>Disp Mult: {(config.displacement_multiplier || 0).toFixed(1)}x</div>
        </div>
      </div>
    </motion.div>
  )
}

// Auto-Trader Control Panel
const AutoTraderPanel = ({ status, onStart, onStop, onPause, onResume, onReset, onToggleMode, loading, modeLoading }) => {
  const isRunning = status?.is_running
  const isPaused = status?.is_paused
  const mode = status?.mode || 'paper'
  const isLive = mode === 'live'
  const dailyPnl = status?.daily_pnl || 0
  const dailyTrades = status?.daily_trades || 0
  const maxTrades = status?.risk_config?.max_daily_trades || 10
  const canTrade = status?.can_trade?.[0] ?? false
  const canTradeReason = status?.can_trade?.[1] || ''
  const openPositions = status?.open_positions || 0
  const strategyId = status?.strategy_id || 'unknown'

  const [showLiveConfirm, setShowLiveConfirm] = useState(false)

  const handleToggleMode = () => {
    if (!isLive) {
      // Going to live - show confirmation
      setShowLiveConfirm(true)
    } else {
      // Going to paper - no confirmation needed
      onToggleMode('paper')
    }
  }

  const confirmGoLive = () => {
    setShowLiveConfirm(false)
    onToggleMode('live')
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className={`bg-gradient-to-br from-slate-800/90 to-slate-900/90 border ${isLive ? 'border-red-500/50' : 'border-cyan-500/30'} rounded-xl p-4 relative overflow-hidden`}
    >
      {/* Gradient accent */}
      <div className={`absolute inset-0 bg-gradient-to-br ${isLive ? 'from-red-500 to-orange-600' : 'from-cyan-500 to-blue-600'} opacity-10`} />

      {/* Live Mode Warning Banner */}
      {isLive && (
        <div className="absolute top-0 left-0 right-0 bg-red-500/20 border-b border-red-500/30 px-3 py-1 flex items-center justify-center space-x-2">
          <AlertTriangle className="w-4 h-4 text-red-400" />
          <span className="text-xs font-bold text-red-400">LIVE MODE - REAL MONEY AT RISK</span>
        </div>
      )}

      <div className={`relative z-10 ${isLive ? 'pt-6' : ''}`}>
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center space-x-3">
            <div className={`p-2 rounded-lg ${isRunning ? 'bg-gradient-to-br from-green-500 to-emerald-600' : 'bg-gradient-to-br from-slate-500 to-slate-600'}`}>
              <Bot className="w-6 h-6 text-white" />
            </div>
            <div>
              <h3 className="font-bold text-white flex items-center space-x-2">
                <span>Auto-Trader</span>
                <span className={`text-xs px-2 py-0.5 rounded-full ${isLive ? 'bg-red-500/20 text-red-400 animate-pulse' : 'bg-cyan-500/20 text-cyan-400'
                  }`}>
                  {mode.toUpperCase()}
                </span>
                {isRunning && !isPaused && (
                  <span className="flex items-center space-x-1 text-xs bg-green-500/20 text-green-400 px-2 py-0.5 rounded-full">
                    <span className="relative flex h-2 w-2">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
                      <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
                    </span>
                    <span>RUNNING</span>
                  </span>
                )}
                {isPaused && (
                  <span className="text-xs bg-amber-500/20 text-amber-400 px-2 py-0.5 rounded-full">PAUSED</span>
                )}
                {!isRunning && !isPaused && (
                  <span className="text-xs bg-slate-500/20 text-slate-400 px-2 py-0.5 rounded-full">STOPPED</span>
                )}
              </h3>
              <p className="text-xs text-slate-400">
                {canTrade ? 'Ready to trade' : canTradeReason}
              </p>
              <p className="text-[11px] text-slate-500">Strategy: {strategyId}</p>
            </div>
          </div>

          {/* Control Buttons */}
          <div className="flex items-center space-x-2">
            {!isRunning ? (
              <button
                onClick={onStart}
                disabled={loading}
                className="flex items-center space-x-2 px-4 py-2 bg-green-500/20 hover:bg-green-500/30 text-green-400 rounded-lg transition-colors disabled:opacity-50"
              >
                <Play className="w-4 h-4" />
                <span>Start</span>
              </button>
            ) : (
              <>
                {!isPaused ? (
                  <button
                    onClick={onPause}
                    disabled={loading}
                    className="flex items-center space-x-2 px-3 py-2 bg-amber-500/20 hover:bg-amber-500/30 text-amber-400 rounded-lg transition-colors disabled:opacity-50"
                  >
                    <Pause className="w-4 h-4" />
                    <span>Pause</span>
                  </button>
                ) : (
                  <button
                    onClick={onResume}
                    disabled={loading}
                    className="flex items-center space-x-2 px-3 py-2 bg-cyan-500/20 hover:bg-cyan-500/30 text-cyan-400 rounded-lg transition-colors disabled:opacity-50"
                  >
                    <Play className="w-4 h-4" />
                    <span>Resume</span>
                  </button>
                )}
                <button
                  onClick={onStop}
                  disabled={loading}
                  className="flex items-center space-x-2 px-3 py-2 bg-red-500/20 hover:bg-red-500/30 text-red-400 rounded-lg transition-colors disabled:opacity-50"
                >
                  <Square className="w-4 h-4" />
                  <span>Stop</span>
                </button>
              </>
            )}
            <button
              onClick={onReset}
              disabled={loading}
              className="flex items-center space-x-2 px-3 py-2 bg-slate-500/20 hover:bg-slate-500/30 text-slate-400 rounded-lg transition-colors disabled:opacity-50"
              title="Reset daily counters"
            >
              <RotateCcw className="w-4 h-4" />
            </button>

            {/* Go Live Toggle Button */}
            <button
              onClick={handleToggleMode}
              disabled={modeLoading || isRunning}
              className={`flex items-center space-x-2 px-4 py-2 rounded-lg transition-colors disabled:opacity-50 ${isLive
                ? 'bg-green-500/20 hover:bg-green-500/30 text-green-400 border border-green-500/30'
                : 'bg-red-500/20 hover:bg-red-500/30 text-red-400 border border-red-500/30'
                }`}
              title={isLive ? 'Switch to Paper Mode' : 'Switch to Live Mode'}
            >
              <Power className="w-4 h-4" />
              <span>{isLive ? 'Go Paper' : 'Go Live'}</span>
            </button>
          </div>
        </div>

        {/* Live Mode Confirmation Modal */}
        <AnimatePresence>
          {showLiveConfirm && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-50"
              onClick={() => setShowLiveConfirm(false)}
            >
              <motion.div
                initial={{ scale: 0.9, y: 20 }}
                animate={{ scale: 1, y: 0 }}
                exit={{ scale: 0.9, y: 20 }}
                className="bg-slate-800 rounded-xl border-2 border-red-500/50 p-6 max-w-md w-full mx-4"
                onClick={e => e.stopPropagation()}
              >
                <div className="flex items-center space-x-3 mb-4">
                  <div className="p-3 bg-red-500/20 rounded-full">
                    <AlertTriangle className="w-8 h-8 text-red-500" />
                  </div>
                  <div>
                    <h3 className="text-xl font-bold text-white">Go Live?</h3>
                    <p className="text-sm text-slate-400">This will use real money</p>
                  </div>
                </div>

                <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-4 mb-4">
                  <ul className="text-sm text-slate-300 space-y-2">
                    <li className="flex items-start space-x-2">
                      <DollarSign className="w-4 h-4 text-red-400 mt-0.5 flex-shrink-0" />
                      <span>Real orders will be placed via Fyers broker</span>
                    </li>
                    <li className="flex items-start space-x-2">
                      <AlertTriangle className="w-4 h-4 text-red-400 mt-0.5 flex-shrink-0" />
                      <span>Only NIFTY50 options will be traded (₹5,000 capital)</span>
                    </li>
                    <li className="flex items-start space-x-2">
                      <Shield className="w-4 h-4 text-amber-400 mt-0.5 flex-shrink-0" />
                      <span>Max daily loss: ₹500 | Max trades: 5/day</span>
                    </li>
                  </ul>
                </div>

                <div className="flex space-x-3">
                  <button
                    onClick={() => setShowLiveConfirm(false)}
                    className="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={confirmGoLive}
                    className="flex-1 px-4 py-2 bg-red-500 hover:bg-red-600 text-white rounded-lg transition-colors font-bold"
                  >
                    Yes, Go Live
                  </button>
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Stats Grid - Mode Specific */}
        <div className="grid grid-cols-6 gap-3">
          <div className={`rounded-lg p-3 text-center ${isLive ? 'bg-red-500/10 border border-red-500/20' : 'bg-slate-800/60'}`}>
            <div className="text-xs text-slate-500">Daily P&L ({mode.toUpperCase()})</div>
            <div className={`text-xl font-bold ${dailyPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {dailyPnl >= 0 ? '+' : ''}₹{dailyPnl.toFixed(0)}
            </div>
          </div>
          <div className="bg-slate-800/60 rounded-lg p-3 text-center">
            <div className="text-xs text-slate-500">Trades ({mode.toUpperCase()})</div>
            <div className="text-xl font-bold text-cyan-400">{dailyTrades}/{maxTrades}</div>
          </div>
          <div className="bg-slate-800/60 rounded-lg p-3 text-center">
            <div className="text-xs text-slate-500">Open Positions</div>
            <div className="text-xl font-bold text-white">{openPositions}</div>
          </div>
          <div className="bg-slate-800/60 rounded-lg p-3 text-center">
            <div className="text-xs text-slate-500">Win Rate</div>
            <div className="text-xl font-bold text-amber-400">{status?.learning?.win_rate?.toFixed(1) || 0}%</div>
          </div>
          <div className="bg-cyan-500/10 rounded-lg p-3 text-center border border-cyan-500/20">
            <div className="text-xs text-cyan-400">Paper Total</div>
            <div className={`text-xl font-bold ${(status?.learning?.total_pnl_paper || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {(status?.learning?.total_pnl_paper || 0) >= 0 ? '+' : ''}₹{status?.learning?.total_pnl_paper || 0}
            </div>
          </div>
          <div className="bg-red-500/10 rounded-lg p-3 text-center border border-red-500/20">
            <div className="text-xs text-red-400">Live Total</div>
            <div className={`text-xl font-bold ${(status?.learning?.total_pnl_live || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {(status?.learning?.total_pnl_live || 0) >= 0 ? '+' : ''}₹{status?.learning?.total_pnl_live || 0}
            </div>
          </div>
        </div>

        {/* Risk Config Summary */}
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          <span className="bg-slate-700/50 px-2 py-1 rounded text-slate-400">
            Min Conf: {status?.risk_config?.min_probability || 60}%
          </span>
          <span className="bg-slate-700/50 px-2 py-1 rounded text-slate-400">
            Max Loss: ₹{status?.risk_config?.max_daily_loss || 2000}
          </span>
          <span className="bg-slate-700/50 px-2 py-1 rounded text-slate-400">
            SL: {status?.risk_config?.stop_loss_pct || 1.5}%
          </span>
          <span className="bg-slate-700/50 px-2 py-1 rounded text-slate-400">
            Target: {status?.risk_config?.target_pct || 3}%
          </span>
        </div>
      </div>
    </motion.div>
  )
}

// Veto Layer Card (Capital Protection)
const VetoLayerCard = ({ veto }) => {
  if (!veto?.enabled) return null

  const stats = veto.stats || {}
  const approvalRate = parseFloat(stats.approval_rate) || 0
  const savedFromLoss = stats.saved_from_loss || 0

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="col-span-full bg-gradient-to-br from-slate-800/90 to-slate-900/90 border border-green-500/30 rounded-xl p-4 relative overflow-hidden"
    >
      {/* Gradient accent */}
      <div className="absolute inset-0 bg-gradient-to-br from-green-500 to-emerald-600 opacity-10" />

      <div className="relative z-10">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center space-x-3">
            <div className="p-2 rounded-lg bg-gradient-to-br from-green-500 to-emerald-600">
              <ShieldCheck className="w-6 h-6 text-white" />
            </div>
            <div>
              <h3 className="font-bold text-white flex items-center space-x-2">
                <span>LLM Veto Layer</span>
                <span className="text-xs bg-green-500/20 text-green-400 px-2 py-0.5 rounded-full">CAPITAL PROTECTION</span>
              </h3>
              <p className="text-xs text-slate-400">Reviews and filters all signals before execution</p>
            </div>
          </div>
          <div className="text-right">
            <div className="text-xs text-slate-400">Model</div>
            <div className="text-sm font-mono text-green-400">gpt-4o-mini</div>
          </div>
        </div>

        {/* Stats Grid */}
        <div className="grid grid-cols-5 gap-3">
          <div className="bg-slate-800/60 rounded-lg p-3 text-center">
            <div className="text-xs text-slate-500">Reviewed</div>
            <div className="text-xl font-bold text-white">{stats.total_reviewed || 0}</div>
          </div>
          <div className="bg-slate-800/60 rounded-lg p-3 text-center">
            <div className="text-xs text-slate-500">Approved</div>
            <div className="text-xl font-bold text-green-400">{stats.approved || 0}</div>
          </div>
          <div className="bg-slate-800/60 rounded-lg p-3 text-center">
            <div className="text-xs text-slate-500">Rejected</div>
            <div className="text-xl font-bold text-red-400">{stats.rejected || 0}</div>
          </div>
          <div className="bg-slate-800/60 rounded-lg p-3 text-center">
            <div className="text-xs text-slate-500">Approval Rate</div>
            <div className={`text-xl font-bold ${approvalRate >= 50 ? 'text-cyan-400' : 'text-amber-400'}`}>
              {stats.approval_rate || '0%'}
            </div>
          </div>
          <div className="bg-slate-800/60 rounded-lg p-3 text-center border border-green-500/30">
            <div className="text-xs text-green-400">Saved from Loss</div>
            <div className="text-xl font-bold text-green-400">{savedFromLoss}</div>
          </div>
        </div>

        {/* Info text */}
        <div className="mt-3 text-xs text-slate-500 flex items-center space-x-1">
          <Shield className="w-3 h-3" />
          <span>The veto layer uses LLM reasoning to reject risky signals and protect your capital</span>
        </div>
      </div>
    </motion.div>
  )
}

// Execution Quality & Go-Live Validation Panel
const ExecutionQualityPanel = ({ gateData }) => {
  if (!gateData || gateData.error) return null

  const currentGate = gateData.current_gate || 'paper_validation'
  const gates = gateData.gates || {}

  const gateNames = ['paper_validation', 'micro_live', 'scale_up', 'full_capital']
  const gateLabels = {
    paper_validation: 'Paper Validation',
    micro_live: 'Micro Live (20%)',
    scale_up: 'Scale Up (60%)',
    full_capital: 'Full Capital'
  }

  const getStatusColor = (status) => {
    if (status === 'passed') return 'text-green-400 bg-green-500/20'
    if (status === 'failed') return 'text-red-400 bg-red-500/20'
    if (status === 'in_progress') return 'text-amber-400 bg-amber-500/20'
    return 'text-slate-400 bg-slate-500/20'
  }

  const getStatusIcon = (status) => {
    if (status === 'passed') return '✓'
    if (status === 'failed') return '✗'
    if (status === 'in_progress') return '●'
    return '○'
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-gradient-to-br from-slate-800/90 to-slate-900/90 border border-cyan-500/30 rounded-xl p-4"
    >
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center space-x-2">
          <div className="p-2 rounded-lg bg-gradient-to-br from-cyan-500 to-blue-600">
            <Target className="w-5 h-5 text-white" />
          </div>
          <div>
            <h3 className="font-bold text-white">Go-Live Validation Gates</h3>
            <p className="text-xs text-slate-400">Track execution quality for staged rollout</p>
          </div>
        </div>
        <div className="text-xs text-slate-400">
          Current: <span className="text-cyan-400 font-semibold">{gateLabels[currentGate]}</span>
        </div>
      </div>

      {/* Gate Progress */}
      <div className="grid grid-cols-4 gap-2 mb-4">
        {gateNames.map((gateName, idx) => {
          const gate = gates[gateName] || {}
          const isActive = gateName === currentGate
          const status = gate.status || 'not_started'

          return (
            <div
              key={gateName}
              className={`p-3 rounded-lg border ${isActive ? 'border-cyan-500 bg-cyan-500/10' : 'border-slate-700 bg-slate-800/50'
                }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-slate-400">{gateLabels[gateName]}</span>
                <span className={`text-xs px-1.5 py-0.5 rounded ${getStatusColor(status)}`}>
                  {getStatusIcon(status)} {status}
                </span>
              </div>
              <div className="text-xs text-slate-500">
                {gate.days_completed || 0}/{gate.days_required || '?'} days
              </div>
              <div className="text-xs text-slate-500">
                {gate.trades_completed || 0}/{gate.trades_required || '?'} trades
              </div>
            </div>
          )
        })}
      </div>

      {/* Current Gate Metrics */}
      {gates[currentGate] && gates[currentGate].metrics && (
        <div className="grid grid-cols-6 gap-2">
          {Object.entries(gates[currentGate].metrics).map(([key, data]) => (
            <div key={key} className="bg-slate-800/60 rounded-lg p-2 text-center">
              <div className="text-xs text-slate-500 capitalize">{key.replace('_', ' ')}</div>
              <div className={`text-sm font-bold ${data.passed ? 'text-green-400' : 'text-red-400'}`}>
                {typeof data.value === 'number' ? data.value.toFixed(1) : data.value}
                {key.includes('pct') || key.includes('rate') ? '%' : key.includes('latency') ? 'ms' : ''}
              </div>
              <div className={`text-xs ${data.passed ? 'text-green-500' : 'text-red-500'}`}>
                {data.passed ? 'PASS' : 'FAIL'}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Failure Reasons */}
      {gates[currentGate]?.failure_reasons?.length > 0 && (
        <div className="mt-3 p-2 bg-red-500/10 border border-red-500/30 rounded-lg">
          <div className="flex items-center space-x-1 text-xs text-red-400">
            <AlertTriangle className="w-3 h-3" />
            <span className="font-semibold">Blocking Issues:</span>
          </div>
          <ul className="mt-1 text-xs text-red-300 list-disc list-inside">
            {gates[currentGate].failure_reasons.slice(0, 3).map((reason, idx) => (
              <li key={idx}>{reason}</li>
            ))}
          </ul>
        </div>
      )}
    </motion.div>
  )
}

// Leaderboard row
const LeaderboardRow = ({ bot, rank }) => {
  const Icon = BOT_ICONS[bot.name] || Bot
  const gradient = BOT_COLORS[bot.name] || 'from-gray-500 to-slate-500'

  return (
    <motion.tr
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: rank * 0.05 }}
      className="border-b border-slate-700/30 hover:bg-slate-800/30 transition-colors"
    >
      <td className="py-3 px-4">
        <span className={`text-lg font-bold ${rank === 1 ? 'text-amber-400' :
          rank === 2 ? 'text-slate-300' :
            rank === 3 ? 'text-amber-600' :
              'text-slate-500'
          }`}>
          #{rank}
        </span>
      </td>
      <td className="py-3 px-4">
        <div className="flex items-center space-x-2">
          <div className={`p-1.5 rounded-lg bg-gradient-to-br ${gradient}`}>
            <Icon className="w-4 h-4 text-white" />
          </div>
          <span className="font-medium text-white">{bot.name}</span>
        </div>
      </td>
      <td className="py-3 px-4 text-center">
        <span className={`font-mono ${bot.win_rate >= 60 ? 'text-green-400' : bot.win_rate >= 40 ? 'text-amber-400' : 'text-red-400'}`}>
          {formatPct(bot.win_rate)}
        </span>
      </td>
      <td className="py-3 px-4 text-center">
        <span className="font-mono text-slate-300">{formatNum(bot.profit_factor)}</span>
      </td>
      <td className="py-3 px-4 text-center">
        <span className="font-mono text-cyan-400">{bot.total_trades}</span>
      </td>
      <td className="py-3 px-4 text-center">
        <span className={`font-mono ${bot.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
          {bot.total_pnl >= 0 ? '+' : ''}{formatNum(bot.total_pnl)}
        </span>
      </td>
      <td className="py-3 px-4 text-center">
        <span className={`font-mono px-2 py-0.5 rounded ${bot.weight >= 1.5 ? 'bg-green-500/20 text-green-400' :
          bot.weight >= 1.0 ? 'bg-cyan-500/20 text-cyan-400' :
            'bg-amber-500/20 text-amber-400'
          }`}>
          {formatNum(bot.weight)}x
        </span>
      </td>
    </motion.tr>
  )
}

// Trade History Row
const TradeHistoryRow = ({ trade, idx }) => {
  const isPE = trade.option_type === 'PE'
  const isWin = trade.outcome === 'WIN'
  const isLive = trade.mode === 'live'
  const time = new Date(trade.entry_time || trade.timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })

  return (
    <motion.tr
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: idx * 0.03 }}
      className={`border-b border-slate-700/30 hover:bg-slate-800/30 transition-colors ${isLive ? 'bg-red-500/5' : ''}`}
    >
      <td className="py-2 px-3 text-center">
        <span className={`text-xs px-2 py-0.5 rounded-full font-bold ${isLive ? 'bg-red-500/20 text-red-400 border border-red-500/30' : 'bg-cyan-500/20 text-cyan-400'
          }`}>
          {isLive ? 'LIVE' : 'PAPER'}
        </span>
      </td>
      <td className="py-2 px-3 text-xs text-slate-400 font-mono">{time}</td>
      <td className="py-2 px-3">
        <div className="flex items-center space-x-2">
          {isPE ? (
            <ArrowDownRight className="w-4 h-4 text-red-400" />
          ) : (
            <ArrowUpRight className="w-4 h-4 text-green-400" />
          )}
          <span className="text-sm text-white font-medium">{trade.index}</span>
          <span className={`text-xs px-1.5 py-0.5 rounded ${isPE ? 'bg-red-500/20 text-red-400' : 'bg-green-500/20 text-green-400'}`}>
            {trade.option_type}
          </span>
        </div>
      </td>
      <td className="py-2 px-3 text-sm text-slate-300 font-mono">{trade.strike}</td>
      <td className="py-2 px-3 text-sm text-slate-300 font-mono">{trade.entry_price?.toFixed(2)}</td>
      <td className="py-2 px-3 text-sm text-slate-300 font-mono">{trade.exit_price?.toFixed(2)}</td>
      <td className="py-2 px-3 text-center">
        <span className={`text-sm font-mono ${isWin ? 'text-green-400' : 'text-red-400'}`}>
          {trade.pnl >= 0 ? '+' : ''}{trade.pnl?.toFixed(2)}
        </span>
      </td>
      <td className="py-2 px-3 text-center">
        <span className={`text-xs px-2 py-1 rounded-full ${isWin ? 'bg-green-500/20 text-green-400' :
          trade.outcome === 'LOSS' ? 'bg-red-500/20 text-red-400' :
            'bg-slate-500/20 text-slate-400'
          }`}>
          {trade.outcome}
        </span>
      </td>
      <td className="py-2 px-3 text-xs text-slate-400">{trade.exit_reason}</td>
      <td className="py-2 px-3">
        {trade.bot_signals?.contributing_bots?.length > 0 ? (
          <div className="flex -space-x-1">
            {trade.bot_signals.contributing_bots.slice(0, 3).map((bot, i) => {
              const Icon = BOT_ICONS[bot] || Bot
              return (
                <div key={i} className="w-5 h-5 rounded-full bg-slate-700 flex items-center justify-center" title={bot}>
                  <Icon className="w-3 h-3 text-slate-400" />
                </div>
              )
            })}
          </div>
        ) : (
          <span className="text-xs text-slate-500">{trade.engine || '—'}</span>
        )}
      </td>
    </motion.tr>
  )
}

const LearningRoiCard = ({ data, loading, error, agentSignature, agents, onAgentChange }) => {
  const highRoiTopics = data?.high_roi_topics || []

  return (
    <div className="mb-4 bg-slate-800/50 rounded-xl border border-slate-700/50 p-4">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 mb-4">
        <h3 className="text-sm font-bold text-white flex items-center space-x-2">
          <Brain className="w-4 h-4 text-violet-400" />
          <span>Learning ROI</span>
        </h3>

        <div className="flex items-center space-x-2">
          <span className="text-xs text-slate-400">Agent</span>
          <select
            value={agentSignature}
            onChange={(e) => onAgentChange(e.target.value)}
            className="bg-slate-900/80 border border-slate-700 text-slate-200 text-xs rounded-lg px-2 py-1.5 focus:outline-none focus:border-cyan-500"
          >
            {agents.map((agent) => (
              <option key={agent.signature} value={agent.signature}>
                {agent.signature}
              </option>
            ))}
          </select>
        </div>
      </div>

      {loading ? (
        <p className="text-xs text-slate-400">Loading learning ROI...</p>
      ) : error ? (
        <p className="text-xs text-amber-400">{error}</p>
      ) : !data ? (
        <p className="text-xs text-slate-400">No ROI data available yet.</p>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
            <div className="bg-slate-900/60 rounded-lg p-3">
              <p className="text-[11px] text-slate-500">Knowledge Items</p>
              <p className="text-lg font-bold text-cyan-400">{data.total_knowledge_items || 0}</p>
            </div>
            <div className="bg-slate-900/60 rounded-lg p-3">
              <p className="text-[11px] text-slate-500">Knowledge Uses</p>
              <p className="text-lg font-bold text-cyan-400">{data.total_knowledge_uses || 0}</p>
            </div>
            <div className="bg-slate-900/60 rounded-lg p-3">
              <p className="text-[11px] text-slate-500">Knowledge Earnings</p>
              <p className="text-lg font-bold text-green-400">${(data.total_earnings_from_knowledge || 0).toFixed(2)}</p>
            </div>
            <div className="bg-slate-900/60 rounded-lg p-3">
              <p className="text-[11px] text-slate-500">Avg / Use</p>
              <p className="text-lg font-bold text-emerald-400">${(data.avg_earnings_per_use || 0).toFixed(2)}</p>
            </div>
          </div>

          <div className="bg-slate-900/40 rounded-lg border border-slate-700/40 p-3">
            <p className="text-xs font-semibold text-slate-300 mb-2">Top High-ROI Topics</p>
            {highRoiTopics.length === 0 ? (
              <p className="text-xs text-slate-500">No high-ROI topics yet.</p>
            ) : (
              <div className="space-y-2">
                {highRoiTopics.slice(0, 3).map((topic) => (
                  <div key={topic.topic} className="flex items-center justify-between text-xs">
                    <span className="text-slate-200 truncate pr-3">{topic.topic}</span>
                    <span className="text-green-400 font-semibold">${(topic.total_earnings || 0).toFixed(2)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

// Ensemble stats card
const EnsembleStatsCard = ({ stats }) => {
  const winRate = stats.win_rate || 0
  const totalPnl = stats.total_pnl || 0
  return (
    <div className="bg-gradient-to-br from-slate-800/90 to-slate-900/90 border border-slate-700/50 rounded-xl p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold text-white flex items-center space-x-2">
          <Brain className="w-6 h-6 text-cyan-400" />
          <span>Ensemble Performance</span>
        </h2>
        <LiveBadge />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-slate-800/50 rounded-lg p-4">
          <div className="text-sm text-slate-400">Total Decisions</div>
          <div className="text-2xl font-bold text-white">{stats.total_decisions || 0}</div>
        </div>
        <div className="bg-slate-800/50 rounded-lg p-4">
          <div className="text-sm text-slate-400">Trades Taken</div>
          <div className="text-2xl font-bold text-cyan-400">{stats.trades_taken || 0}</div>
        </div>
        <div className="bg-slate-800/50 rounded-lg p-4">
          <div className="text-sm text-slate-400">Win Rate</div>
          <div className={`text-2xl font-bold ${winRate >= 60 ? 'text-green-400' : winRate >= 40 ? 'text-amber-400' : 'text-red-400'}`}>
            {formatPct(winRate)}
          </div>
        </div>
        <div className="bg-slate-800/50 rounded-lg p-4">
          <div className="text-sm text-slate-400">Total P&L</div>
          <div className={`text-2xl font-bold ${totalPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {totalPnl >= 0 ? '+' : ''}{formatNum(totalPnl)}
          </div>
        </div>
      </div>

      {/* Active bots summary */}
      <div className="mt-4 flex flex-wrap gap-2">
        {(stats.bots || []).map(bot => (
          <div key={bot.name} className="flex items-center space-x-1.5 bg-slate-700/30 rounded-full px-3 py-1">
            <span className={`w-2 h-2 rounded-full ${bot.weight >= 1.0 ? 'bg-green-400' : 'bg-amber-400'}`} />
            <span className="text-xs text-slate-300">{bot.name}</span>
            <span className="text-xs text-slate-500">({formatNum(bot.weight)}x)</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// Regime Hunter Independent Pipeline Card
const RegimeHunterPipelineCard = ({ data, onStart, onStop, onPause, onResume, onReset, actionLoading }) => {
  if (!data) return null

  const regimeColors = {
    LIQUIDITY_SWEEP_REVERSAL: 'text-cyan-400 bg-cyan-500/20',
    BREAKOUT_INITIATION: 'text-green-400 bg-green-500/20',
    TREND_EXHAUSTION: 'text-amber-400 bg-amber-500/20',
    TRAP: 'text-red-400 bg-red-500/20',
    SIDEWAYS: 'text-slate-400 bg-slate-500/20',
    UNKNOWN: 'text-slate-500 bg-slate-500/10',
  }

  const regime = data.current_regime || 'UNKNOWN'
  const regimeStyle = regimeColors[regime] || regimeColors.UNKNOWN
  const isRunning = data.is_running
  const isPaused = data.is_paused

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="relative overflow-hidden rounded-xl bg-gradient-to-br from-yellow-900/20 to-slate-900/90 border border-yellow-500/30 p-5"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4 pb-3 border-b border-yellow-500/20">
        <div className="flex items-center space-x-3">
          <div className="p-2 rounded-lg bg-gradient-to-br from-yellow-500 to-amber-600">
            <Target className="w-5 h-5 text-white" />
          </div>
          <div>
            <h3 className="font-bold text-lg text-yellow-300">Regime Hunter Pipeline</h3>
            <p className="text-xs text-slate-400">Independent execution • No consensus needed • Solo conviction</p>
          </div>
        </div>
        <div className="flex items-center space-x-2">
          <span className={`text-xs font-mono px-2 py-1 rounded ${isRunning && !isPaused ? 'bg-green-500/20 text-green-400' :
              isPaused ? 'bg-amber-500/20 text-amber-400' :
                'bg-red-500/20 text-red-400'
            }`}>
            {isRunning && !isPaused ? 'RUNNING' : isPaused ? 'PAUSED' : 'STOPPED'}
          </span>
          <span className="text-xs font-mono px-2 py-1 rounded bg-slate-500/20 text-slate-400">
            {(data.mode || 'paper').toUpperCase()}
          </span>
        </div>
      </div>

      {/* Regime + Stats */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-4">
        {/* Current Regime */}
        <div className="col-span-2 lg:col-span-1 bg-slate-800/50 rounded-lg p-3">
          <div className="text-xs text-slate-500 mb-1">Current Regime</div>
          <div className={`text-sm font-bold px-2 py-1 rounded inline-block ${regimeStyle}`}>
            {regime.replace(/_/g, ' ')}
          </div>
        </div>
        {/* Daily P&L */}
        <div className="bg-slate-800/50 rounded-lg p-3">
          <div className="text-xs text-slate-500">Daily P&L</div>
          <div className={`text-xl font-bold ${(data.daily_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {(data.daily_pnl || 0) >= 0 ? '+' : ''}{formatNum(data.daily_pnl)}
          </div>
        </div>
        {/* Trades */}
        <div className="bg-slate-800/50 rounded-lg p-3">
          <div className="text-xs text-slate-500">Trades Today</div>
          <div className="text-xl font-bold text-cyan-400">{data.daily_trades || 0}</div>
        </div>
        {/* Win Rate */}
        <div className="bg-slate-800/50 rounded-lg p-3">
          <div className="text-xs text-slate-500">Win Rate</div>
          <div className={`text-xl font-bold ${(data.win_rate || 0) >= 60 ? 'text-green-400' : (data.win_rate || 0) >= 40 ? 'text-amber-400' : 'text-red-400'}`}>
            {formatPct(data.win_rate)}
          </div>
        </div>
        {/* Open Positions */}
        <div className="bg-slate-800/50 rounded-lg p-3">
          <div className="text-xs text-slate-500">Open Positions</div>
          <div className="text-xl font-bold text-white">{(data.open_positions || []).length}</div>
        </div>
      </div>

      {/* Open Positions List */}
      {(data.open_positions || []).length > 0 && (
        <div className="mb-4 bg-slate-800/30 rounded-lg p-3">
          <div className="text-xs text-slate-400 font-semibold mb-2">Open Positions</div>
          {data.open_positions.map((pos, i) => (
            <div key={i} className="flex items-center justify-between text-xs py-1 border-b border-slate-700/30 last:border-0">
              <span className="text-white font-mono">{pos.symbol}</span>
              <span className={`px-2 py-0.5 rounded ${pos.regime === 'LIQUIDITY_SWEEP_REVERSAL' ? 'bg-cyan-500/20 text-cyan-400' : 'bg-green-500/20 text-green-400'}`}>
                {pos.regime}
              </span>
              <span className="text-slate-400">@ {formatNum(pos.entry_price)}</span>
              <span className="text-amber-400">SL {formatNum(pos.stop_loss)}</span>
              <span className="text-green-400">T {formatNum(pos.target)}</span>
            </div>
          ))}
        </div>
      )}

      {/* Controls */}
      <div className="flex items-center space-x-2">
        {!isRunning ? (
          <button onClick={onStart} disabled={actionLoading}
            className="flex items-center space-x-1.5 px-4 py-2 rounded-lg bg-green-600 hover:bg-green-500 text-white text-sm font-medium transition disabled:opacity-50">
            <Play className="w-4 h-4" /><span>Start</span>
          </button>
        ) : (
          <>
            {!isPaused ? (
              <button onClick={onPause} disabled={actionLoading}
                className="flex items-center space-x-1.5 px-3 py-2 rounded-lg bg-amber-600 hover:bg-amber-500 text-white text-sm font-medium transition disabled:opacity-50">
                <Pause className="w-4 h-4" /><span>Pause</span>
              </button>
            ) : (
              <button onClick={onResume} disabled={actionLoading}
                className="flex items-center space-x-1.5 px-3 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-medium transition disabled:opacity-50">
                <Play className="w-4 h-4" /><span>Resume</span>
              </button>
            )}
            <button onClick={onStop} disabled={actionLoading}
              className="flex items-center space-x-1.5 px-3 py-2 rounded-lg bg-red-600 hover:bg-red-500 text-white text-sm font-medium transition disabled:opacity-50">
              <Square className="w-4 h-4" /><span>Stop</span>
            </button>
          </>
        )}
        <button onClick={onReset} disabled={actionLoading}
          className="flex items-center space-x-1.5 px-3 py-2 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-300 text-sm font-medium transition disabled:opacity-50">
          <RotateCcw className="w-4 h-4" /><span>Reset Daily</span>
        </button>
      </div>
    </motion.div>
  )
}

// Main component
export default function BotEnsemble() {
  const [botsStatus, setBotsStatus] = useState([])
  const [leaderboard, setLeaderboard] = useState([])
  const [ensembleStats, setEnsembleStats] = useState({})
  const [tradeHistory, setTradeHistory] = useState([])
  const [atTradeHistory, setAtTradeHistory] = useState([])
  const [engineTab, setEngineTab] = useState('fyersn7')
  const [selectedBot, setSelectedBot] = useState(null)
  const [botDetails, setBotDetails] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [lastFetch, setLastFetch] = useState(Date.now())
  const [autoTraderStatus, setAutoTraderStatus] = useState(null)
  const [autoTraderLoading, setAutoTraderLoading] = useState(false)
  const [modeLoading, setModeLoading] = useState(false)
  const [gateData, setGateData] = useState(null)
  const [agentsList, setAgentsList] = useState([])
  const [isDocumentVisible, setIsDocumentVisible] = useState(() => {
    if (typeof document === 'undefined') return true
    return document.visibilityState !== 'hidden'
  })
  const [learningAgentSignature, setLearningAgentSignature] = useState('')
  const [learningRoiData, setLearningRoiData] = useState(null)
  const [learningRoiLoading, setLearningRoiLoading] = useState(false)
  const [learningRoiError, setLearningRoiError] = useState(null)
  const [ictSniperData, setIctSniperData] = useState(null)
  const [rhPipelineData, setRhPipelineData] = useState(null)
  const [rhPipelineLoading, setRhPipelineLoading] = useState(false)

  const fetchLearningRoiData = useCallback(async (signatureArg) => {
    const signatureToLoad = signatureArg || learningAgentSignature
    if (!signatureToLoad) return

    try {
      setLearningRoiLoading(true)
      setLearningRoiError(null)
      const roiRes = await fetchAgentLearningRoi(signatureToLoad)
      setLearningRoiData(roiRes)
    } catch (err) {
      console.error('Error fetching learning ROI:', err)
      setLearningRoiData(null)
      setLearningRoiError('Learning ROI data not available yet')
    } finally {
      setLearningRoiLoading(false)
    }
  }, [learningAgentSignature])

  const fetchFastData = useCallback(async () => {
    const [tradesRes, atTradesRes, autoTraderRes, rhPipelineRes] = await Promise.all([
      fetchFyersn7TradeHistory(new Date().toISOString().slice(0, 10)).catch((err) => {
        console.error('Error fetching trade history:', err)
        return null
      }),
      fetchTradeHistory().catch((err) => {
        console.error('Error fetching AutoTrader trade history:', err)
        return null
      }),
      fetchAutoTraderStatus().catch((err) => {
        console.error('Error fetching auto-trader status:', err)
        return null
      }),
      fetchRHPipelineStatus().catch((err) => {
        console.error('Error fetching RH pipeline status:', err)
        return null
      }),
    ])

    const didUpdate = Boolean(tradesRes || atTradesRes || autoTraderRes || rhPipelineRes)

    if (tradesRes) {
      setTradeHistory(tradesRes.trades || [])
    }
    if (atTradesRes) {
      setAtTradeHistory(atTradesRes.trades || [])
    }
    if (autoTraderRes) {
      setAutoTraderStatus(autoTraderRes.status || null)
    }
    if (rhPipelineRes) {
      setRhPipelineData(rhPipelineRes)
    }
    if (didUpdate) {
      setError(null)
      setLastFetch(Date.now())
    }

    return didUpdate
  }, [])

  const fetchSlowData = useCallback(async () => {
    const [statusRes, leaderRes, statsRes, gatesRes, agentsRes, ictRes] = await Promise.all([
      fetchBotsStatus().catch((err) => {
        console.error('Error fetching bot status:', err)
        return null
      }),
      fetchBotsLeaderboard().catch((err) => {
        console.error('Error fetching bot leaderboard:', err)
        return null
      }),
      fetchEnsembleStats().catch((err) => {
        console.error('Error fetching ensemble stats:', err)
        return null
      }),
      fetchGateStatus().catch((err) => {
        console.error('Error fetching gate status:', err)
        return null
      }),
      fetchAgents().catch((err) => {
        console.error('Error fetching agents:', err)
        return null
      }),
      fetchIctSniperStatus().catch((err) => {
        console.error('Error fetching ICT Sniper status:', err)
        return null
      }),
    ])

    const didUpdate = Boolean(statusRes || leaderRes || statsRes || gatesRes || agentsRes || ictRes)

    if (statusRes) {
      setBotsStatus(statusRes.bots || [])
    }
    if (leaderRes) {
      setLeaderboard(leaderRes.leaderboard || [])
    }
    if (statsRes) {
      setEnsembleStats(statsRes.stats || {})
    }
    if (gatesRes) {
      setGateData(gatesRes)
    }
    if (agentsRes) {
      const fetchedAgents = agentsRes.agents || []
      setAgentsList(fetchedAgents)
      if (fetchedAgents.length > 0) {
        setLearningAgentSignature((current) => current || fetchedAgents[0].signature)
      }
    }
    if (ictRes && !ictRes.error) {
      setIctSniperData(ictRes)
    }
    if (ictRes && ictRes.error) {
      setIctSniperData(null)
    }
    if (didUpdate) {
      setError(null)
      setLastFetch(Date.now())
    }

    return didUpdate
  }, [])

  const refreshAllData = useCallback(async () => {
    const [slowUpdated, fastUpdated] = await Promise.all([
      fetchSlowData(),
      fetchFastData(),
    ])

    if (!slowUpdated && !fastUpdated) {
      setError('Failed to fetch bot data. Is the API server running?')
    }

    setLoading(false)
  }, [fetchFastData, fetchSlowData])

  // Auto-trader control handlers
  const handleStartAutoTrader = async () => {
    setAutoTraderLoading(true)
    try {
      await startAutoTrader()
      await refreshAllData()
    } catch (err) {
      console.error('Error starting auto-trader:', err)
    } finally {
      setAutoTraderLoading(false)
    }
  }

  const handleStopAutoTrader = async () => {
    setAutoTraderLoading(true)
    try {
      await stopAutoTrader()
      await refreshAllData()
    } catch (err) {
      console.error('Error stopping auto-trader:', err)
    } finally {
      setAutoTraderLoading(false)
    }
  }

  const handlePauseAutoTrader = async () => {
    setAutoTraderLoading(true)
    try {
      await pauseAutoTrader()
      await refreshAllData()
    } catch (err) {
      console.error('Error pausing auto-trader:', err)
    } finally {
      setAutoTraderLoading(false)
    }
  }

  const handleResumeAutoTrader = async () => {
    setAutoTraderLoading(true)
    try {
      await resumeAutoTrader()
      await refreshAllData()
    } catch (err) {
      console.error('Error resuming auto-trader:', err)
    } finally {
      setAutoTraderLoading(false)
    }
  }

  const handleResetDaily = async () => {
    setAutoTraderLoading(true)
    try {
      await resetAutoTraderDaily()
      await refreshAllData()
    } catch (err) {
      console.error('Error resetting daily:', err)
    } finally {
      setAutoTraderLoading(false)
    }
  }

  const handleToggleMode = async (mode) => {
    setModeLoading(true)
    try {
      await toggleTradingMode(mode)
      await refreshAllData()
    } catch (err) {
      console.error('Error toggling trading mode:', err)
    } finally {
      setModeLoading(false)
    }
  }

  useEffect(() => {
    if (typeof document === 'undefined') return undefined

    const handleVisibilityChange = () => {
      setIsDocumentVisible(document.visibilityState !== 'hidden')
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  }, [])

  useEffect(() => {
    if (!isDocumentVisible) return
    refreshAllData()
  }, [isDocumentVisible, refreshAllData])

  useEffect(() => {
    if (!learningAgentSignature) return
    fetchLearningRoiData(learningAgentSignature)
  }, [fetchLearningRoiData, learningAgentSignature])

  useEffect(() => {
    if (!isDocumentVisible) return undefined

    const interval = setInterval(fetchFastData, 10000)
    return () => clearInterval(interval)
  }, [fetchFastData, isDocumentVisible])

  useEffect(() => {
    if (!isDocumentVisible) return undefined

    const interval = setInterval(fetchSlowData, 30000)
    return () => clearInterval(interval)
  }, [fetchSlowData, isDocumentVisible])

  const handleBotClick = async (botName) => {
    setSelectedBot(botName)
    try {
      const details = await fetchBotDetails(botName)
      setBotDetails(details)
    } catch (err) {
      console.error('Error fetching bot details:', err)
    }
  }

  const handleLearningAgentChange = (signature) => {
    setLearningAgentSignature(signature)
  }

  // Regime Hunter Pipeline handlers
  const handleRHAction = async (actionFn) => {
    setRhPipelineLoading(true)
    try {
      await actionFn()
      await refreshAllData()
    } catch (err) {
      console.error('RH Pipeline action error:', err)
    } finally {
      setRhPipelineLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center">
        <div className="text-center">
          <RefreshCw className="w-8 h-8 text-cyan-400 animate-spin mx-auto mb-4" />
          <p className="text-slate-400">Loading bot ensemble...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center">
        <div className="text-center">
          <AlertCircle className="w-8 h-8 text-amber-400 mx-auto mb-4" />
          <p className="text-slate-400">{error}</p>
          <button
            onClick={refreshAllData}
            className="mt-4 px-4 py-2 bg-cyan-500/20 text-cyan-400 rounded-lg hover:bg-cyan-500/30 transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  const displayedTrades =
    engineTab === 'fyersn7'    ? tradeHistory
    : engineTab === 'autotrader' ? atTradeHistory
    : [...tradeHistory, ...atTradeHistory].sort((a, b) =>
        (b.timestamp || '').localeCompare(a.timestamp || ''))

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold text-white flex items-center space-x-3">
            <div className="p-2 bg-gradient-to-br from-cyan-500 to-blue-500 rounded-xl">
              <Bot className="w-8 h-8 text-white" />
            </div>
            <span>Multi-Bot Ensemble</span>
          </h1>
          <p className="text-slate-400 mt-1">Self-learning trading bots with weighted consensus</p>
        </div>
        <div className="flex items-center space-x-4">
          <span className="text-xs text-slate-500 font-mono">
            Updated {Math.round((Date.now() - lastFetch) / 1000)}s ago
          </span>
          <button
            onClick={refreshAllData}
            className="p-2 bg-slate-800 rounded-lg hover:bg-slate-700 transition-colors"
          >
            <RefreshCw className="w-5 h-5 text-slate-400" />
          </button>
        </div>
      </div>

      {/* Auto-Trader Control Panel */}
      {autoTraderStatus && (
        <div className="mb-6">
          <AutoTraderPanel
            status={autoTraderStatus}
            onStart={handleStartAutoTrader}
            onStop={handleStopAutoTrader}
            onPause={handlePauseAutoTrader}
            onResume={handleResumeAutoTrader}
            onReset={handleResetDaily}
            onToggleMode={handleToggleMode}
            loading={autoTraderLoading}
            modeLoading={modeLoading}
          />
        </div>
      )}

      {/* Ensemble Stats */}
      <EnsembleStatsCard stats={ensembleStats} />

      {/* Veto Layer Card (Capital Protection) */}
      {ensembleStats.veto_layer?.enabled && (
        <div className="mt-6">
          <VetoLayerCard veto={ensembleStats.veto_layer} />
        </div>
      )}

      {/* Execution Quality & Go-Live Validation */}
      {gateData && !gateData.error && (
        <div className="mt-6">
          <ExecutionQualityPanel gateData={gateData} />
        </div>
      )}

      {/* Bot Cards Grid */}
      <div className="mt-6">
        <h2 className="text-lg font-bold text-white mb-4">Trading Bots</h2>
        <LearningRoiCard
          data={learningRoiData}
          loading={learningRoiLoading}
          error={learningRoiError}
          agentSignature={learningAgentSignature}
          agents={agentsList}
          onAgentChange={handleLearningAgentChange}
        />

        {/* ICT Sniper Strategy Card */}
        {ictSniperData && !ictSniperData.error && (
          <div className="mt-4 mb-4">
            <ICTSniperCard botData={ictSniperData} onClick={() => { }} />
          </div>
        )}

        {/* Regime Hunter Independent Pipeline Card */}
        {rhPipelineData && (
          <div className="mt-4 mb-4">
            <RegimeHunterPipelineCard
              data={rhPipelineData}
              onStart={() => handleRHAction(startRHPipeline)}
              onStop={() => handleRHAction(stopRHPipeline)}
              onPause={() => handleRHAction(pauseRHPipeline)}
              onResume={() => handleRHAction(resumeRHPipeline)}
              onReset={() => handleRHAction(resetRHPipelineDaily)}
              actionLoading={rhPipelineLoading}
            />
          </div>
        )}

        {/* Hybrid Regime Hunter Pipeline Card (Modular Architecture) */}
        <div className="mt-4 mb-4">
          <HybridPipelineCard
            marketData={rhPipelineData?.market_data}
            selectedIndex={rhPipelineData?.index || 'NIFTY50'}
          />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4">
          {botsStatus.filter(bot => !bot.is_veto_layer).map(bot => (
            <BotCard key={bot.name} bot={bot} onClick={handleBotClick} />
          ))}
        </div>
      </div>

      {/* Leaderboard Table */}
      <div className="mt-8">
        <h2 className="text-lg font-bold text-white mb-4 flex items-center space-x-2">
          <Target className="w-5 h-5 text-amber-400" />
          <span>Bot Leaderboard</span>
        </h2>
        <div className="bg-slate-800/50 rounded-xl border border-slate-700/50 overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="bg-slate-800/80 text-slate-400 text-xs uppercase tracking-wider">
                <th className="py-3 px-4 text-left">Rank</th>
                <th className="py-3 px-4 text-left">Bot</th>
                <th className="py-3 px-4 text-center">Win Rate</th>
                <th className="py-3 px-4 text-center">Profit Factor</th>
                <th className="py-3 px-4 text-center">Trades</th>
                <th className="py-3 px-4 text-center">P&L</th>
                <th className="py-3 px-4 text-center">Weight</th>
              </tr>
            </thead>
            <tbody>
              {leaderboard.map((bot, idx) => (
                <LeaderboardRow key={bot.name} bot={bot} rank={idx + 1} />
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Trade History Table */}
      <div className="mt-8">
        <h2 className="text-lg font-bold text-white mb-4 flex items-center space-x-2">
          <Clock className="w-5 h-5 text-cyan-400" />
          <span>Trade History</span>
          <span className="text-sm font-normal text-slate-400">({displayedTrades.length} trades)</span>
        </h2>
        {/* Engine tabs */}
        <div className="flex space-x-2 mb-3">
          {[
            { key: 'fyersn7',     label: 'FyersN7',     count: tradeHistory.length },
            { key: 'autotrader',  label: 'AutoTrader',  count: atTradeHistory.length },
            { key: 'all',         label: 'All',         count: tradeHistory.length + atTradeHistory.length },
          ].map(({ key, label, count }) => (
            <button
              key={key}
              onClick={() => setEngineTab(key)}
              className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
                engineTab === key
                  ? 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30'
                  : 'text-slate-400 border-slate-600/50 hover:text-white hover:border-slate-500'
              }`}
            >
              {label} ({count})
            </button>
          ))}
        </div>
        <div className="bg-slate-800/50 rounded-xl border border-slate-700/50 overflow-hidden">
          {displayedTrades.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="bg-slate-800/80 text-slate-400 text-xs uppercase tracking-wider">
                    <th className="py-2 px-3 text-center">Mode</th>
                    <th className="py-2 px-3 text-left">Time</th>
                    <th className="py-2 px-3 text-left">Index</th>
                    <th className="py-2 px-3 text-left">Strike</th>
                    <th className="py-2 px-3 text-left">Entry</th>
                    <th className="py-2 px-3 text-left">Exit</th>
                    <th className="py-2 px-3 text-center">P&L</th>
                    <th className="py-2 px-3 text-center">Outcome</th>
                    <th className="py-2 px-3 text-left">Reason</th>
                    <th className="py-2 px-3 text-left">Engine</th>
                  </tr>
                </thead>
                <tbody>
                  {displayedTrades.slice().reverse().map((trade, idx) => (
                    <TradeHistoryRow key={trade.id || idx} trade={trade} idx={idx} />
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="py-12 text-center text-slate-500">
              <Clock className="w-8 h-8 mx-auto mb-2 opacity-50" />
              <p>No closed trades recorded yet</p>
              <p className="text-xs mt-1">Closed paper trades will appear here after they exit</p>
            </div>
          )}
        </div>
      </div>

      {/* Bot Details Modal */}
      <AnimatePresence>
        {selectedBot && botDetails && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50"
            onClick={() => setSelectedBot(null)}
          >
            <motion.div
              initial={{ scale: 0.9, y: 20 }}
              animate={{ scale: 1, y: 0 }}
              exit={{ scale: 0.9, y: 20 }}
              className="bg-slate-800 rounded-xl border border-slate-700 p-6 max-w-2xl w-full mx-4 max-h-[80vh] overflow-y-auto"
              onClick={e => e.stopPropagation()}
            >
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-xl font-bold text-white">{selectedBot} Details</h3>
                <button
                  onClick={() => setSelectedBot(null)}
                  className="p-2 hover:bg-slate-700 rounded-lg transition-colors"
                >
                  <span className="text-slate-400">✕</span>
                </button>
              </div>

              {/* Bot info */}
              <div className="space-y-4">
                <div className="bg-slate-700/30 rounded-lg p-4">
                  <h4 className="text-sm text-slate-400 mb-2">Description</h4>
                  <p className="text-white">{botDetails.bot?.description}</p>
                </div>

                <div className="bg-slate-700/30 rounded-lg p-4">
                  <h4 className="text-sm text-slate-400 mb-2">Parameters</h4>
                  <div className="grid grid-cols-2 gap-2">
                    {Object.entries(botDetails.bot?.parameters || {}).map(([key, val]) => (
                      <div key={key} className="flex justify-between text-sm">
                        <span className="text-slate-400">{key}:</span>
                        <span className="text-white font-mono">{val}</span>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="bg-slate-700/30 rounded-lg p-4">
                  <h4 className="text-sm text-slate-400 mb-2">Recent Learnings</h4>
                  <div className="space-y-2 max-h-40 overflow-y-auto">
                    {(botDetails.learnings || []).slice(-5).map((learning, idx) => (
                      <div key={idx} className="text-sm border-l-2 border-cyan-500/50 pl-3">
                        <p className="text-slate-300">{learning.insight}</p>
                        <p className="text-xs text-slate-500">{learning.topic}</p>
                      </div>
                    ))}
                    {(!botDetails.learnings || botDetails.learnings.length === 0) && (
                      <p className="text-slate-500 text-sm">No learnings recorded yet</p>
                    )}
                  </div>
                </div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
