import React, { useState, useEffect, useCallback, useRef } from 'react';

const FRONTEND_PORT = import.meta.env.VITE_FRONTEND_PORT || '3001';
const SCALPING_API_PORT = import.meta.env.VITE_SCALPING_API_PORT || '8002';
const SCALPING_API_ORIGIN = import.meta.env.VITE_SCALPING_API_ORIGIN || '';
const SCALPING_WS_ORIGIN = import.meta.env.VITE_SCALPING_WS_ORIGIN || '';

function trimTrailingSlash(value) {
  return value.replace(/\/$/, '');
}

function resolveScalpingHttpOrigin() {
  if (SCALPING_API_ORIGIN) return trimTrailingSlash(SCALPING_API_ORIGIN);
  if (typeof window === 'undefined') return `http://localhost:${SCALPING_API_PORT}`;

  const { protocol, hostname, origin, port } = window.location;
  if (port === FRONTEND_PORT) {
    return `${protocol}//${hostname}:${SCALPING_API_PORT}`;
  }

  return origin;
}

function resolveScalpingWebSocketUrl() {
  if (SCALPING_WS_ORIGIN) return `${trimTrailingSlash(SCALPING_WS_ORIGIN)}/ws/scalping`;
  if (typeof window === 'undefined') return `ws://localhost:${SCALPING_API_PORT}/ws/scalping`;

  return `${resolveScalpingHttpOrigin().replace(/^http/, 'ws')}/ws/scalping`;
}

const SCALPING_API = `${resolveScalpingHttpOrigin()}/api/scalping`;
const WS_URL = resolveScalpingWebSocketUrl();

// Agent layer colors
const LAYER_COLORS = {
  safety: '#dc2626',    // red - KillSwitch runs first
  data: '#3b82f6',      // blue
  analysis: '#8b5cf6',  // purple
  quality: '#06b6d4',   // cyan - SignalQuality gate
  risk: '#f97316',      // orange
  execution: '#10b981', // green
  learning: '#f59e0b',  // amber
  meta: '#ef4444',      // red
};

// Status colors
const STATUS_COLORS = {
  idle: '#6b7280',
  running: '#10b981',
  blocked: '#ef4444',
  error: '#dc2626',
};

function formatCurrency(value) {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value);
}

function formatPnL(value) {
  const color = value >= 0 ? '#10b981' : '#ef4444';
  const prefix = value >= 0 ? '+' : '';
  return (
    <span style={{ color, fontWeight: 600 }}>
      {prefix}{formatCurrency(value)}
    </span>
  );
}

function getAverageExitPrice(trade) {
  if (!trade?.exit_time) {
    return null;
  }

  const apiAverageExit = Number(trade.average_exit_price);
  if (Number.isFinite(apiAverageExit) && apiAverageExit > 0) {
    return apiAverageExit;
  }

  const partialExits = Array.isArray(trade.partial_exits) ? trade.partial_exits : [];
  const totalQuantity = Number(trade.quantity || 0);
  const finalExitPrice = Number(trade.exit_price);
  let exitedQuantity = 0;
  let weightedNotional = 0;

  partialExits.forEach((exit) => {
    const quantity = Number(exit?.quantity || 0);
    const price = Number(exit?.price || 0);
    if (quantity <= 0 || !Number.isFinite(price) || price <= 0) {
      return;
    }
    weightedNotional += quantity * price;
    exitedQuantity += quantity;
  });

  if (Number.isFinite(finalExitPrice) && finalExitPrice > 0) {
    const finalQuantity = Math.max(totalQuantity - exitedQuantity, 0);
    weightedNotional += finalQuantity * finalExitPrice;
    exitedQuantity += finalQuantity;
  }

  return exitedQuantity > 0 ? weightedNotional / exitedQuantity : null;
}

function prettyAgentData(value) {
  if (!value || (typeof value === 'object' && Object.keys(value).length === 0)) {
    return 'No recent details';
  }

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function AgentDetailSections({ agent, onPinToggle, isPinned = false }) {
  return (
    <>
      <div className="agent-details-toolbar">
        <button
          type="button"
          className={`pin-button ${isPinned ? 'active' : ''}`}
          onClick={(event) => {
            event.stopPropagation();
            onPinToggle?.(agent);
          }}
        >
          {isPinned ? 'Unpin' : '📌 Pin'}
        </button>
      </div>
      <div className="agent-details-section">
        <strong>Status</strong>
        <pre>{prettyAgentData({
          status: agent.status,
          layer: agent.layer,
          last_run: agent.last_run,
          runs: agent.run_count,
        })}</pre>
      </div>
      <div className="agent-details-section">
        <strong>Last Output</strong>
        <pre>{prettyAgentData(agent.last_output)}</pre>
      </div>
      <div className="agent-details-section">
        <strong>Metrics</strong>
        <pre>{prettyAgentData(agent.metrics)}</pre>
      </div>
    </>
  );
}

function TradabilityHeatmap({ agents }) {
  if (!agents?.agents?.length) return null;

  const getAgent = (name) => agents.agents.find((agent) => agent.name === name);
  const valueOrZero = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);

  const stages = [
    { label: 'Structure', value: valueOrZero(getAgent('Structure')?.metrics?.breaks_detected) },
    { label: 'Momentum', value: valueOrZero(getAgent('Momentum')?.output?.strong_signals ?? getAgent('Momentum')?.metrics?.strong_signals) },
    { label: 'Strike', value: valueOrZero(getAgent('StrikeSelector')?.output?.total_selections) },
    { label: 'Quality', value: valueOrZero(getAgent('SignalQuality')?.output?.signals_passed) },
    { label: 'Liquidity', value: valueOrZero(getAgent('LiquidityMonitor')?.output?.tradeable_options) },
    { label: 'Execution', value: valueOrZero(getAgent('Entry')?.output?.orders_created) },
  ];

  const maxValue = Math.max(...stages.map((stage) => stage.value), 1);

  return (
    <div className="tradability-heatmap">
      <div className="heatmap-header">
        <h3>Tradability Heatmap</h3>
        <span className="heatmap-caption">Green means the pipeline has tradable flow.</span>
      </div>
      <div className="heatmap-grid">
        {stages.map((stage) => {
          const intensity = stage.value > 0 ? stage.value / maxValue : 0;
          const background = stage.value > 0
            ? `rgba(16, 185, 129, ${0.22 + intensity * 0.55})`
            : 'rgba(51, 65, 85, 0.65)';

          return (
            <div
              key={stage.label}
              className={`heatmap-cell ${stage.value > 0 ? 'active' : 'inactive'}`}
              style={{ background }}
            >
              <span>{stage.label}</span>
              <strong>{stage.value}</strong>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EngineModeControls({
  selectedMode,
  onModeChange,
  replay,
  selectedFile,
  onFileChange,
  onRunReplay,
  onRunPostmortem,
  onReplayControl,
  replaySeekPct,
  onSeekStart,
  onSeekEnd,
  onSeekChange,
  currentEngineMode,
}) {
  const isReplayActive = Boolean(replay?.active);
  const isPaused = Boolean(replay?.paused);
  const speed = replay?.speed || 1;
  const direction = replay?.direction || 1;
  return (
    <div className="replay-controls">
      <div className="replay-left">
        <div className="mode-switch">
          <button
            className={`mode-button ${selectedMode === 'LIVE_PAPER' ? 'active' : ''}`}
            onClick={() => onModeChange('LIVE_PAPER')}
            disabled={replay?.active}
          >
            Live Paper
          </button>
          <button
            className={`mode-button ${selectedMode === 'REPLAY' ? 'active' : ''}`}
            onClick={() => onModeChange('REPLAY')}
            disabled={replay?.active}
          >
            Historical Replay
          </button>
        </div>
        {selectedMode === 'REPLAY' && (
          <>
            <label className="file-picker">
              <span>Replay CSV</span>
              <input type="file" accept=".csv,text/csv" onChange={onFileChange} disabled={isReplayActive} />
            </label>
            <button
              className="replay-button"
              onClick={onRunReplay}
              disabled={!selectedFile || isReplayActive}
            >
              {isReplayActive ? (isPaused ? 'Replay Paused' : 'Replay Running...') : 'Run Replay Test'}
            </button>
            <button
              className="replay-button secondary"
              onClick={onRunPostmortem}
              disabled={!selectedFile || isReplayActive}
            >
              Run Postmortem
            </button>
            <div className="replay-transport">
              <button
                type="button"
                className={`transport-btn ${direction < 0 ? 'active' : ''}`}
                onClick={() => onReplayControl('fast_rewind')}
                disabled={!isReplayActive}
              >
                Rew
              </button>
              <button
                type="button"
                className="transport-btn"
                onClick={() => onReplayControl(isPaused ? 'play' : 'pause')}
                disabled={!isReplayActive}
              >
                {isPaused ? 'Play' : 'Pause'}
              </button>
              <button
                type="button"
                className={`transport-btn ${direction > 0 ? 'active' : ''}`}
                onClick={() => onReplayControl('fast_forward')}
                disabled={!isReplayActive}
              >
                Fwd
              </button>
              <button
                type="button"
                className="transport-btn"
                onClick={() => onReplayControl('stop')}
                disabled={!isReplayActive}
              >
                Stop
              </button>
              <div className="speed-group">
                {[1, 2, 4, 8].map((value) => (
                  <button
                    key={value}
                    type="button"
                    className={`speed-btn ${speed === value ? 'active' : ''}`}
                    onClick={() => onReplayControl('speed', { speed: value })}
                    disabled={!isReplayActive}
                  >
                    {value}x
                  </button>
                ))}
              </div>
            </div>
            <div className="replay-seek">
              <input
                type="range"
                min="0"
                max="100"
                step="0.1"
                value={replaySeekPct}
                onMouseDown={onSeekStart}
                onTouchStart={onSeekStart}
                onChange={(event) => onSeekChange(event.target.value)}
                onMouseUp={(event) => onSeekEnd(event.target.value)}
                onTouchEnd={(event) => onSeekEnd(event.target.value)}
                disabled={!isReplayActive}
              />
              <span>{replaySeekPct.toFixed(1)}%</span>
            </div>
          </>
        )}
      </div>
      <div className="replay-right">
        <div>Engine Mode: {currentEngineMode || 'IDLE'}</div>
        <div>Selected Mode: {selectedMode === 'REPLAY' ? 'Historical Replay' : 'Live Paper'}</div>
        {selectedMode === 'REPLAY' ? (
          <>
            <div>Dataset: {replay?.dataset || selectedFile?.name || 'None'}</div>
            <div>Progress: {(replay?.progress_pct || 0).toFixed(1)}%</div>
            <div>State: {isReplayActive ? (isPaused ? 'Paused' : 'Playing') : 'Stopped'}</div>
            <div>Speed: {speed}x</div>
            <div>Direction: {direction < 0 ? 'Rewind' : 'Forward'}</div>
          </>
        ) : (
          <div>File: Not required</div>
        )}
      </div>
    </div>
  );
}

const ReplayResults = React.forwardRef(function ReplayResults({ replay }, ref) {
  const result = replay?.result;
  if (!result || !Object.keys(result).length) return null;

  return (
    <div className="replay-results" ref={ref}>
      <h3>Replay Results</h3>
      <div className="replay-metrics-grid">
        <div><span>Dataset</span><strong>{result.dataset}</strong></div>
        <div><span>Cycles</span><strong>{result.total_cycles}</strong></div>
        <div><span>Signals</span><strong>{result.signals_detected}</strong></div>
        <div><span>After Quality</span><strong>{result.signals_after_quality}</strong></div>
        <div><span>After Liquidity</span><strong>{result.signals_after_liquidity}</strong></div>
        <div><span>Trades</span><strong>{result.trades_executed}</strong></div>
        <div><span>Win Rate</span><strong>{result.win_rate}%</strong></div>
        <div><span>PnL</span><strong>{formatCurrency(result.simulated_pnl || 0)}</strong></div>
      </div>
      <div className="replay-summary-banner">
        Signals blocked at stage: <strong>{result.signals_blocked_stage || 'None'}</strong>
      </div>
      <div className="replay-diagnostics-grid">
        <div className="diagnostic-card">
          <h4>Stage Totals</h4>
          <pre>{JSON.stringify(result.stage_totals || {}, null, 2)}</pre>
        </div>
        <div className="diagnostic-card">
          <h4>Rejections</h4>
          <pre>{JSON.stringify(result.rejection_breakdown || {}, null, 2)}</pre>
        </div>
        <div className="diagnostic-card">
          <h4>Signal Stats</h4>
          <pre>{JSON.stringify(result.signal_stats || {}, null, 2)}</pre>
        </div>
        <div className="diagnostic-card">
          <h4>Pipeline Heatmap</h4>
          <pre>{JSON.stringify(result.pipeline_heatmap || {}, null, 2)}</pre>
        </div>
        <div className="diagnostic-card">
          <h4>Strategy Tags</h4>
          <pre>{JSON.stringify(result.strategy_quality || {}, null, 2)}</pre>
        </div>
        <div className="diagnostic-card">
          <h4>Edge Discovery</h4>
          <pre>{JSON.stringify(result.edge_discovery || {}, null, 2)}</pre>
        </div>
      </div>
    </div>
  );
});

// Pipeline visualization component with data flow
function PipelineView({ pipeline, agents, dataflow }) {
  if (!pipeline?.stages) return null;

  // Check if any flow is active for a stage
  const hasActiveFlow = (stageId) => {
    if (!dataflow?.connections) return false;
    const stageAgents = {
      data: ['DataFeed', 'OptionChain', 'Futures', 'LatencyGuard'],
      analysis: ['Regime', 'Structure', 'Momentum', 'TrapDetector', 'StrikeSelector'],
      meta: ['Meta'],
      risk: ['Liquidity', 'RiskGuard'],
      execution: ['Entry', 'Position', 'Exit'],
      learning: ['QuantLearner', 'StrategyOptimizer'],
    };
    const agentNames = stageAgents[stageId] || [];
    return dataflow.connections.some(
      c => c.active && (agentNames.includes(c.from) || agentNames.includes(c.to))
    );
  };

  // Get last update time formatted
  const getLastUpdate = () => {
    if (pipeline.last_cycle) {
      try {
        return new Date(pipeline.last_cycle).toLocaleTimeString();
      } catch {
        return pipeline.last_cycle;
      }
    }
    return 'Never';
  };

  return (
    <div className="pipeline-container">
      <h3>Agent Pipeline</h3>
      <div className="pipeline-flow">
        {pipeline.stages.map((stage, idx) => {
          const isActive = hasActiveFlow(stage.id) || stage.status === 'running';
          return (
            <React.Fragment key={stage.id}>
              <div className={`pipeline-stage ${stage.status} ${isActive ? 'active-flow' : ''}`}>
                <div className="stage-header">
                  <span className="stage-name">{stage.name}</span>
                  {stage.periodic && <span className="periodic-badge">Periodic</span>}
                  {isActive && <span className="flow-indicator" />}
                </div>
                <div className="stage-agents">
                  {stage.agents.map(agentId => {
                    const agent = agents.find(a => a.agent_id === agentId);
                    return (
                      <div
                        key={agentId}
                        className={`agent-chip ${agent?.status || 'idle'}`}
                        style={{ borderColor: LAYER_COLORS[agent?.layer] }}
                      >
                        <span className="agent-id">#{agentId}</span>
                        <span className="agent-name">{agent?.name}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
              {idx < pipeline.stages.length - 1 && (
                <div className={`pipeline-arrow ${isActive ? 'glowing' : ''}`}>→</div>
              )}
            </React.Fragment>
          );
        })}
      </div>
      <div className="cycle-info">
        Cycle #{pipeline.cycle_count} | Last: {getLastUpdate()}
      </div>
    </div>
  );
}

// Capital summary component
function CapitalSummary({ capital }) {
  if (!capital) return null;

  const riskPct = (capital.risk_used_pct || 0).toFixed(1);

  return (
    <div className="capital-summary">
      <h3>Capital & P&L</h3>
      <div className="capital-grid">
        <div className="capital-item">
          <span className="label">Initial</span>
          <span className="value">{formatCurrency(capital.initial_capital)}</span>
        </div>
        <div className="capital-item">
          <span className="label">Available</span>
          <span className="value">{formatCurrency(capital.available_capital)}</span>
        </div>
        <div className="capital-item">
          <span className="label">Used</span>
          <span className="value">{formatCurrency(capital.used_capital)}</span>
        </div>
        <div className="capital-item highlight">
          <span className="label">Total P&L</span>
          <span className="value">{formatPnL(capital.total_pnl)}</span>
        </div>
        <div className="capital-item">
          <span className="label">Realized</span>
          <span className="value">{formatPnL(capital.realized_pnl)}</span>
        </div>
        <div className="capital-item">
          <span className="label">Unrealized</span>
          <span className="value">{formatPnL(capital.unrealized_pnl)}</span>
        </div>
      </div>
      <div className="risk-bar">
        <div className="risk-label">Daily Risk: {riskPct}%</div>
        <div className="risk-track">
          <div
            className="risk-fill"
            style={{
              width: `${Math.min(100, riskPct)}%`,
              backgroundColor: riskPct > 80 ? '#ef4444' : riskPct > 50 ? '#f59e0b' : '#10b981',
            }}
          />
        </div>
        <div className="risk-limit">Limit: {formatCurrency(capital.daily_loss_limit)}</div>
      </div>
    </div>
  );
}

// Positions table component
function PositionsTable({ positions, agents }) {
  if (!positions?.positions?.length) {
    return (
      <div className="positions-empty">
        <p>No open positions</p>
      </div>
    );
  }

  return (
    <div className="positions-table">
      <h3>Open Positions ({positions.count})</h3>
      {(() => {
        // Extract spot prices from DataFeed agent (agent_id 0 or name DataFeed)
        const dataFeed = agents?.agents?.find(a => a.bot_type === 'data_feed' || a.name === 'DataFeed');
        const spotPrices = {};
        if (dataFeed?.last_output) {
          Object.entries(dataFeed.last_output).forEach(([sym, data]) => {
            if (data?.ltp) {
              const idx = sym.includes('BANKNIFTY') ? 'BANKNIFTY' : sym.includes('SENSEX') ? 'SENSEX' : sym.includes('NIFTY') ? 'NIFTY50' : sym;
              spotPrices[idx] = data.ltp;
            }
          });
        }
        const uniqueIndices = [...new Set(positions.positions.map(p => p.index))];
        return uniqueIndices.length > 0 && (
          <div className="spot-prices" style={{ display: 'flex', gap: '24px', marginBottom: '8px', fontSize: '14px' }}>
            {uniqueIndices.map(idx => (
              <span key={idx} style={{ color: '#e2e8f0' }}>
                <strong>{idx}</strong>: <span style={{ color: '#60a5fa', fontWeight: 600 }}>{spotPrices[idx]?.toFixed(2) || '--'}</span>
              </span>
            ))}
          </div>
        );
      })()}
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Strike</th>
            <th>Type</th>
            <th>Qty</th>
            <th>Entry</th>
            <th>Cur. Price</th>
            <th>SL</th>
            <th>Target</th>
            <th>P&L</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {positions.positions.map(pos => {
            const ltp = pos.current_price || 0;
            const sl = pos.current_sl || 0;
            const target = pos.target_price || 0;
            const pnl = pos.unrealized_pnl || 0;
            return (
            <tr key={pos.trade_id}>
              <td className="symbol">{pos.index}</td>
              <td>{pos.strike}</td>
              <td className={pos.option_type === 'CE' ? 'ce' : 'pe'}>
                {pos.option_type}
              </td>
              <td>{pos.remaining_qty || pos.quantity}</td>
              <td>{pos.entry_price.toFixed(2)}</td>
              <td className={ltp > pos.entry_price ? 'profit' : ltp < pos.entry_price ? 'loss' : ''} style={{ fontWeight: 600 }}>
                {ltp > 0 ? ltp.toFixed(2) : '--'}
              </td>
              <td className="sl">{sl > 0 ? sl.toFixed(2) : '--'}</td>
              <td className="target">{target > 0 ? target.toFixed(2) : '--'}</td>
              <td>{formatPnL(pnl)}</td>
              <td>
                <span className={`status-badge ${pos.status}`}>
                  {pos.status}
                </span>
              </td>
            </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Trade history component
function TradeHistory({ trades }) {
  const [selectedTrade, setSelectedTrade] = useState(null);

  if (!trades?.trades?.length) {
    return (
      <div className="trades-empty">
        <p>No trades yet</p>
      </div>
    );
  }

  return (
    <div className="trade-history">
      <h3>Trade History ({trades.total})</h3>
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Symbol</th>
            <th>Strike</th>
            <th>Entry</th>
            <th>Avg Exit</th>
            <th>P&L</th>
            <th>SL Moves</th>
            <th>Partials</th>
          </tr>
        </thead>
        <tbody>
          {trades.trades.map(trade => (
            <tr
              key={trade.trade_id}
              onClick={() => setSelectedTrade(trade)}
              className="clickable"
            >
              <td>{new Date(trade.entry_time).toLocaleTimeString()}</td>
              <td>{trade.index} {trade.strike}{trade.option_type}</td>
              <td>{trade.strike}</td>
              <td>{trade.entry_price.toFixed(2)}</td>
              <td>{getAverageExitPrice(trade)?.toFixed(2) || '--'}</td>
              <td>{formatPnL(trade.realized_pnl)}</td>
              <td>{trade.sl_moves?.length || 0}</td>
              <td>{trade.partial_exits?.length || 0}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {selectedTrade && (
        <TradeDetail trade={selectedTrade} onClose={() => setSelectedTrade(null)} />
      )}
    </div>
  );
}

// Trade detail modal
function TradeDetail({ trade, onClose }) {
  const averageExitPrice = getAverageExitPrice(trade);

  return (
    <div className="trade-detail-overlay" onClick={onClose}>
      <div className="trade-detail-modal" onClick={e => e.stopPropagation()}>
        <h4>Trade Details: {trade.trade_id}</h4>

        <div className="detail-section">
          <h5>Entry</h5>
          <p>Symbol: {trade.index} {trade.strike}{trade.option_type}</p>
          <p>Time: {new Date(trade.entry_time).toLocaleString()}</p>
          <p>Price: {trade.entry_price.toFixed(2)}</p>
          <p>Quantity: {trade.quantity} ({trade.lots} lots)</p>
          <p>Initial SL: {trade.initial_sl.toFixed(2)}</p>
        </div>

        {trade.sl_moves?.length > 0 && (
          <div className="detail-section">
            <h5>SL Moves ({trade.sl_moves.length})</h5>
            <ul>
              {trade.sl_moves.map((move, i) => (
                <li key={i}>
                  {new Date(move.time).toLocaleTimeString()}: {move.old_sl.toFixed(2)} → {move.new_sl.toFixed(2)}
                  <br />
                  <small>{move.reason}</small>
                </li>
              ))}
            </ul>
          </div>
        )}

        {trade.partial_exits?.length > 0 && (
          <div className="detail-section">
            <h5>Partial Exits ({trade.partial_exits.length})</h5>
            <ul>
              {trade.partial_exits.map((exit, i) => (
                <li key={i}>
                  {new Date(exit.time).toLocaleTimeString()}: {exit.quantity} @ {exit.price.toFixed(2)}
                  = {formatPnL(exit.pnl)}
                </li>
              ))}
            </ul>
          </div>
        )}

        {trade.exit_time && (
          <div className="detail-section">
            <h5>Exit</h5>
            <p>Time: {new Date(trade.exit_time).toLocaleString()}</p>
            <p>Average Exit: {averageExitPrice?.toFixed(2) || '--'}</p>
            {trade.partial_exits?.length > 0 && (
              <p>Final Exit: {trade.exit_price.toFixed(2)}</p>
            )}
            <p>P&L: {formatPnL(trade.realized_pnl)} ({trade.pnl_pct.toFixed(2)}%)</p>
            {trade.partial_exits?.length > 0 && (
              <p>Total realized P&L includes all partial exits and the final exit.</p>
            )}
          </div>
        )}

        {trade.entry_signals && Object.keys(trade.entry_signals).length > 0 && (
          <div className="detail-section">
            <h5>Entry Signals</h5>
            <pre>{JSON.stringify(trade.entry_signals, null, 2)}</pre>
          </div>
        )}

        <button onClick={onClose}>Close</button>
      </div>
    </div>
  );
}

// Agent status grid
function AgentGrid({ agents }) {
  if (!agents?.agents?.length) return null;
  const [pinnedAgentId, setPinnedAgentId] = useState(null);
  const pinnedAgent = agents.agents.find((agent) => agent.agent_id === pinnedAgentId) || null;

  const handlePinToggle = (agent) => {
    setPinnedAgentId((current) => (current === agent.agent_id ? null : agent.agent_id));
  };

  return (
    <div className="agent-grid">
      <h3>Agent Status</h3>
      {pinnedAgent && (
        <div className="pinned-agent-panel">
          <div className="pinned-agent-header">
            <div>
              <strong>{pinnedAgent.name}</strong>
              <span>{pinnedAgent.layer}</span>
            </div>
            <button type="button" className="pin-button active" onClick={() => setPinnedAgentId(null)}>
              Close
            </button>
          </div>
          <AgentDetailSections
            agent={pinnedAgent}
            onPinToggle={handlePinToggle}
            isPinned
          />
        </div>
      )}
      <TradabilityHeatmap agents={agents} />
      <div className="agents-by-layer">
        {Object.entries(agents.by_layer || {}).map(([layer, layerAgents]) => (
          <div key={layer} className="layer-group">
            <h4 style={{ color: LAYER_COLORS[layer] }}>{layer.toUpperCase()}</h4>
            <div className="layer-agents">
              {layerAgents.map(agent => (
                <div
                  key={agent.agent_id}
                  className={`agent-card ${agent.status}`}
                  style={{ borderLeftColor: LAYER_COLORS[layer] }}
                >
                  <div className="agent-header">
                    <span className="agent-num">#{agent.agent_id}</span>
                    <span className="agent-name">{agent.name}</span>
                    <span
                      className="status-dot"
                      style={{ backgroundColor: STATUS_COLORS[agent.status] }}
                    />
                  </div>
                  <div className="agent-meta">
                    <span>Runs: {agent.run_count}</span>
                    {agent.last_run && (
                      <span>Last: {new Date(agent.last_run).toLocaleTimeString()}</span>
                    )}
                  </div>
                  <div className="agent-details">
                    <AgentDetailSections
                      agent={agent}
                      onPinToggle={handlePinToggle}
                      isPinned={pinnedAgentId === agent.agent_id}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Main dashboard component
export default function ScalpingDashboard() {
  const [status, setStatus] = useState(null);
  const [capital, setCapital] = useState(null);
  const [positions, setPositions] = useState(null);
  const [trades, setTrades] = useState(null);
  const [agents, setAgents] = useState(null);
  const [pipeline, setPipeline] = useState(null);
  const [dataflow, setDataflow] = useState(null);
  const [replay, setReplay] = useState({
    active: false,
    progress_pct: 0,
    dataset: null,
    result: null,
    paused: false,
    speed: 1,
    direction: 1,
  });
  const [debateMode, setDebateMode] = useState('debate');
  const [learningMode, setLearningMode] = useState('hybrid');
  const [learningMetrics, setLearningMetrics] = useState(null);
  const [learningLastUpdate, setLearningLastUpdate] = useState(null);
  const [replaySeekPct, setReplaySeekPct] = useState(0);
  const [isSeeking, setIsSeeking] = useState(false);
  const [postmortemActive, setPostmortemActive] = useState(false);
  const [selectedReplayFile, setSelectedReplayFile] = useState(null);
  const [selectedMode, setSelectedMode] = useState('LIVE_PAPER');
  const [connected, setConnected] = useState(false);
  const [apiReady, setApiReady] = useState(false);
  const [error, setError] = useState(null);
  const replayResultsRef = useRef(null);

  const fetchData = useCallback(async () => {
    const safeFetch = (url) => fetch(url).then(r => r.json()).catch(() => null);
    try {
      const [statusRes, capitalRes, positionsRes, tradesRes, agentsRes, pipelineRes, dataflowRes, replayRes, debateRes, learningRes] = await Promise.all([
        safeFetch(`${SCALPING_API}/status`),
        safeFetch(`${SCALPING_API}/capital`),
        safeFetch(`${SCALPING_API}/positions`),
        safeFetch(`${SCALPING_API}/trades?limit=20`),
        safeFetch(`${SCALPING_API}/agents`),
        safeFetch(`${SCALPING_API}/pipeline`),
        safeFetch(`${SCALPING_API}/dataflow`),
        safeFetch(`${SCALPING_API}/replay/status`),
        safeFetch(`${SCALPING_API}/debate`),
        safeFetch(`${SCALPING_API}/learning`),
      ]);

      // Update each piece of state independently — one failure doesn't block others
      if (statusRes) setStatus(statusRes);
      if (capitalRes) setCapital(capitalRes);
      if (positionsRes) setPositions(positionsRes);
      if (tradesRes) setTrades(tradesRes);
      if (agentsRes) setAgents(agentsRes);
      if (pipelineRes) setPipeline(pipelineRes);
      if (dataflowRes) setDataflow(dataflowRes);
      if (replayRes) {
        setReplay(replayRes);
        if (!isSeeking) {
          setReplaySeekPct(Math.min(100, Math.max(0, replayRes?.progress_pct || 0)));
        }
      }
      if (debateRes) setDebateMode(debateRes?.mode || 'debate');
      if (learningRes) {
        setLearningMode(learningRes?.mode || 'hybrid');
        setLearningMetrics(learningRes?.metrics || null);
        setLearningLastUpdate(learningRes?.last_update || null);
      }
      setApiReady(true);
      setError(null);
      return true;
    } catch (err) {
      setApiReady(false);
      setError('Failed to fetch scalping data. Is the API running on port 8002?');
      return false;
    }
  }, []);

  const runReplay = useCallback(async () => {
    if (!selectedReplayFile) return;
    setReplay(prev => ({ ...prev, active: true, progress_pct: 0, dataset: selectedReplayFile.name }));

    try {
      const csvBody = await selectedReplayFile.text();
      const response = await fetch(`${SCALPING_API}/replay/run`, {
        method: 'POST',
        headers: {
          'Content-Type': 'text/csv',
          'X-Replay-Filename': selectedReplayFile.name,
        },
        body: csvBody,
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.error || 'Replay failed');
      }
      setReplay(prev => ({
        ...prev,
        active: true,
        progress_pct: 0,
        dataset: result.dataset || prev.dataset,
        result: {},
      }));
      setSelectedReplayFile(null);
      fetchData();
    } catch (err) {
      setPostmortemActive(false);
      setReplay(prev => ({ ...prev, active: false }));
      setError(err.message || 'Replay failed');
    }
  }, [fetchData, selectedReplayFile]);

  const runPostmortem = useCallback(async () => {
    if (!selectedReplayFile) return;
    setPostmortemActive(true);
    await runReplay();
  }, [runReplay, selectedReplayFile]);

  const updateDebateMode = useCallback(async (mode) => {
    try {
      const response = await fetch(`${SCALPING_API}/debate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.error || 'Failed to update debate mode');
      }
      setDebateMode(mode);
      fetchData();
    } catch (err) {
      setError(err.message || 'Failed to update debate mode');
    }
  }, [fetchData]);

  const updateLearningMode = useCallback(async (mode) => {
    try {
      const response = await fetch(`${SCALPING_API}/learning`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.error || 'Failed to update learning mode');
      }
      setLearningMode(mode);
      fetchData();
    } catch (err) {
      setError(err.message || 'Failed to update learning mode');
    }
  }, [fetchData]);

  const resetLearningMode = useCallback(async () => {
    try {
      const response = await fetch(`${SCALPING_API}/learning`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'revert' }),
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.error || 'Failed to reset learning mode');
      }
      setLearningMode(result.mode || 'hybrid');
      fetchData();
    } catch (err) {
      setError(err.message || 'Failed to reset learning mode');
    }
  }, [fetchData]);

  const sendReplayControl = useCallback(async (action, payload = {}) => {
    try {
      const response = await fetch(`${SCALPING_API}/replay/control`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, ...payload }),
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.error || 'Replay control failed');
      }
      fetchData();
    } catch (err) {
      setError(err.message || 'Replay control failed');
    }
  }, [fetchData]);

  const handleSeekStart = useCallback(() => setIsSeeking(true), []);
  const handleSeekEnd = useCallback((value) => {
    const pct = Math.min(100, Math.max(0, Number(value)));
    setReplaySeekPct(pct);
    setIsSeeking(false);
    sendReplayControl('seek', { pct });
  }, [sendReplayControl]);

  useEffect(() => {
    fetchData();
    const pollIntervalMs = replay?.active || status?.mode === 'REPLAY' ? 250 : 1000;
    const interval = setInterval(fetchData, pollIntervalMs);
    return () => clearInterval(interval);
  }, [fetchData, replay?.active, status?.mode]);

  useEffect(() => {
    if (replay?.active || status?.mode === 'REPLAY') {
      setSelectedMode('REPLAY');
      return;
    }
    if (status?.mode) {
      setSelectedMode('LIVE_PAPER');
    }
  }, [replay?.active, status?.mode]);

  useEffect(() => {
    if (!postmortemActive) return;
    if (!replay?.active && replay?.result && Object.keys(replay.result).length) {
      setPostmortemActive(false);
      if (replayResultsRef.current) {
        replayResultsRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }
  }, [postmortemActive, replay?.active, replay?.result]);

  useEffect(() => {
    if (!apiReady) {
      setConnected(false);
      return undefined;
    }

    let ws;
    let reconnectTimeout;
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      ws = new WebSocket(WS_URL);

      ws.onopen = () => {
        if (disposed) {
          ws?.close();
          return;
        }
        setConnected(true);
        console.log('WebSocket connected');
      };

      ws.onmessage = (event) => {
        if (disposed) return;
        const msg = JSON.parse(event.data);
        if (msg.type === 'update') {
          fetchData();
        }
        if (msg.type === 'replay_progress') {
          setReplay(prev => ({
            ...prev,
            active: true,
            dataset: msg.data?.dataset || prev.dataset,
            progress_pct: msg.data?.progress_pct || 0,
          }));
        }
        if (msg.type === 'replay_complete') {
          setReplay({
            active: false,
            progress_pct: 100,
            dataset: msg.data?.dataset || null,
            result: msg.data,
          });
          fetchData();
        }
      };

      ws.onclose = () => {
        if (disposed) return;
        setConnected(false);
        reconnectTimeout = setTimeout(connect, 5000);
      };

      ws.onerror = () => {
        if (disposed) return;
        setConnected(false);
      };
    };

    connect();

    return () => {
      disposed = true;
      ws?.close();
      clearTimeout(reconnectTimeout);
    };
  }, [apiReady, fetchData]);

  return (
    <div className="scalping-dashboard">
      <style>{`
        .scalping-dashboard {
          padding: 20px;
          background: #0f172a;
          min-height: 100vh;
          color: #e2e8f0;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }

        .dashboard-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 24px;
          padding-bottom: 16px;
          border-bottom: 1px solid #334155;
        }

        .dashboard-header h1 {
          font-size: 24px;
          font-weight: 600;
          display: flex;
          align-items: center;
          gap: 12px;
        }

        .status-indicator {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px 16px;
          border-radius: 8px;
          background: #1e293b;
        }

        .status-indicator.running { background: #064e3b; }
        .status-indicator.stopped { background: #7f1d1d; }

        .connection-dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          background: #ef4444;
        }

        .connection-dot.connected { background: #10b981; }

        .error-banner {
          background: #7f1d1d;
          padding: 12px 16px;
          border-radius: 8px;
          margin-bottom: 16px;
        }

        .dashboard-grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 20px;
        }

        .replay-controls, .replay-results {
          background: #1e293b;
          border-radius: 12px;
          padding: 20px;
          margin-bottom: 20px;
        }

        .replay-controls {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 16px;
        }

        .replay-transport {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
        }

        .replay-seek {
          display: flex;
          align-items: center;
          gap: 10px;
          width: 100%;
          max-width: 320px;
        }

        .replay-seek input[type="range"] {
          flex: 1;
          accent-color: #38bdf8;
        }

        .replay-seek span {
          font-size: 12px;
          color: #cbd5e1;
          min-width: 52px;
          text-align: right;
        }

        .transport-btn,
        .speed-btn {
          padding: 6px 10px;
          border-radius: 8px;
          border: 1px solid #334155;
          background: #0f172a;
          color: #e2e8f0;
          font-size: 12px;
          cursor: pointer;
        }

        .transport-btn.active,
        .speed-btn.active {
          border-color: #38bdf8;
          color: #f8fafc;
          box-shadow: 0 0 0 1px rgba(56, 189, 248, 0.35);
        }

        .transport-btn:disabled,
        .speed-btn:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }

        .speed-group {
          display: flex;
          align-items: center;
          gap: 6px;
          margin-left: 6px;
        }

        .debate-controls,
        .learning-controls {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
          background: #1e293b;
          padding: 12px 16px;
          border-radius: 12px;
          border: 1px solid #334155;
          margin-bottom: 16px;
        }

        .debate-label {
          display: flex;
          flex-direction: column;
          gap: 2px;
          font-size: 12px;
          color: #94a3b8;
        }

        .debate-controls select,
        .learning-controls select {
          background: #0f172a;
          color: #e2e8f0;
          border: 1px solid #334155;
          border-radius: 8px;
          padding: 6px 10px;
          font-size: 12px;
        }

        .learning-actions {
          display: flex;
          align-items: center;
          gap: 10px;
        }

        .learning-reset {
          background: transparent;
          color: #e2e8f0;
          border: 1px solid #334155;
          border-radius: 8px;
          padding: 6px 10px;
          font-size: 12px;
          cursor: pointer;
        }

        .learning-reset:hover {
          border-color: #38bdf8;
          color: #f8fafc;
        }

        .learning-metrics {
          display: flex;
          justify-content: space-between;
          gap: 16px;
          background: #0f172a;
          border: 1px solid #1e293b;
          border-radius: 10px;
          padding: 8px 12px;
          font-size: 12px;
          color: #cbd5e1;
          margin: -6px 0 16px;
        }

        .replay-left {
          display: flex;
          align-items: center;
          gap: 12px;
          flex-wrap: wrap;
        }

        .mode-switch {
          display: flex;
          gap: 8px;
          padding: 4px;
          border-radius: 10px;
          background: #0f172a;
        }

        .mode-button {
          padding: 10px 14px;
          border-radius: 8px;
          border: 1px solid #334155;
          background: transparent;
          color: #cbd5e1;
          font-weight: 600;
          cursor: pointer;
        }

        .mode-button.active {
          background: #0ea5e9;
          border-color: #0ea5e9;
          color: #ffffff;
        }

        .mode-button:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .file-picker {
          display: flex;
          flex-direction: column;
          gap: 6px;
          font-size: 12px;
          color: #94a3b8;
        }

        .replay-button {
          padding: 10px 16px;
          border-radius: 8px;
          border: none;
          background: #0ea5e9;
          color: white;
          font-weight: 600;
          cursor: pointer;
        }

        .replay-button.secondary {
          background: transparent;
          border: 1px solid #334155;
          color: #e2e8f0;
        }

        .replay-button.secondary:hover:not(:disabled) {
          border-color: #38bdf8;
          color: #f8fafc;
        }

        .replay-button:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .replay-right {
          text-align: right;
          font-size: 13px;
          color: #cbd5e1;
        }

        .replay-metrics-grid, .replay-diagnostics-grid {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 12px;
        }

        .replay-metrics-grid > div, .diagnostic-card {
          background: #0f172a;
          border-radius: 10px;
          padding: 12px;
        }

        .replay-metrics-grid span {
          display: block;
          font-size: 12px;
          color: #94a3b8;
          margin-bottom: 4px;
        }

        .replay-summary-banner {
          margin: 16px 0;
          padding: 12px 14px;
          border-radius: 10px;
          background: #0f172a;
          color: #e2e8f0;
        }

        .diagnostic-card h4 {
          margin-bottom: 10px;
          color: #cbd5e1;
        }

        .diagnostic-card pre {
          white-space: pre-wrap;
          font-size: 12px;
          color: #94a3b8;
        }

        .full-width {
          grid-column: 1 / -1;
        }

        .capital-summary, .positions-table, .trade-history, .agent-grid, .pipeline-container {
          background: #1e293b;
          border-radius: 12px;
          padding: 20px;
        }

        .tradability-heatmap, .pinned-agent-panel {
          background: #0f172a;
          border: 1px solid #334155;
          border-radius: 12px;
          padding: 16px;
          margin-bottom: 16px;
        }

        .heatmap-header, .pinned-agent-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 12px;
          margin-bottom: 12px;
        }

        .heatmap-caption, .pinned-agent-header span {
          font-size: 12px;
          color: #94a3b8;
        }

        .heatmap-grid {
          display: grid;
          grid-template-columns: repeat(6, minmax(0, 1fr));
          gap: 10px;
        }

        .heatmap-cell {
          border-radius: 10px;
          padding: 12px;
          border: 1px solid rgba(148, 163, 184, 0.14);
          min-height: 84px;
          display: flex;
          flex-direction: column;
          justify-content: space-between;
        }

        .heatmap-cell span {
          font-size: 12px;
          color: #cbd5e1;
        }

        .heatmap-cell strong {
          font-size: 26px;
          color: #f8fafc;
          line-height: 1;
        }

        .heatmap-cell.active {
          box-shadow: inset 0 0 0 1px rgba(16, 185, 129, 0.35);
        }

        h3 {
          font-size: 16px;
          font-weight: 600;
          margin-bottom: 16px;
          color: #94a3b8;
        }

        .capital-grid {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 16px;
          margin-bottom: 16px;
        }

        .capital-item {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }

        .capital-item .label {
          font-size: 12px;
          color: #64748b;
        }

        .capital-item .value {
          font-size: 18px;
          font-weight: 600;
        }

        .capital-item.highlight {
          background: #334155;
          padding: 12px;
          border-radius: 8px;
        }

        .risk-bar {
          display: flex;
          align-items: center;
          gap: 12px;
        }

        .risk-track {
          flex: 1;
          height: 8px;
          background: #334155;
          border-radius: 4px;
          overflow: hidden;
        }

        .risk-fill {
          height: 100%;
          transition: width 0.3s;
        }

        .risk-label, .risk-limit {
          font-size: 12px;
          color: #64748b;
        }

        table {
          width: 100%;
          border-collapse: collapse;
        }

        th, td {
          padding: 10px 12px;
          text-align: left;
          border-bottom: 1px solid #334155;
        }

        th {
          font-size: 12px;
          color: #64748b;
          font-weight: 500;
        }

        td.ce { color: #10b981; }
        td.pe { color: #ef4444; }
        td.sl { color: #f59e0b; }

        .status-badge {
          padding: 4px 8px;
          border-radius: 4px;
          font-size: 11px;
          font-weight: 500;
        }

        .status-badge.open { background: #064e3b; color: #10b981; }
        .status-badge.partial { background: #78350f; color: #f59e0b; }
        .status-badge.closed { background: #334155; color: #94a3b8; }

        .clickable { cursor: pointer; }
        .clickable:hover { background: #334155; }

        .pipeline-flow {
          display: flex;
          align-items: center;
          gap: 8px;
          overflow-x: auto;
          padding: 16px 0;
        }

        .pipeline-stage {
          background: #334155;
          border-radius: 8px;
          padding: 12px;
          min-width: 140px;
        }

        .pipeline-stage.running {
          box-shadow: 0 0 0 2px #10b981;
        }

        .stage-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 8px;
          gap: 8px;
        }

        .stage-name {
          font-size: 12px;
          font-weight: 600;
        }

        .periodic-badge {
          font-size: 9px;
          background: #78350f;
          color: #f59e0b;
          padding: 2px 6px;
          border-radius: 4px;
        }

        .stage-agents {
          display: flex;
          flex-wrap: wrap;
          gap: 4px;
        }

        .agent-chip {
          font-size: 10px;
          padding: 4px 8px;
          background: #1e293b;
          border-radius: 4px;
          border-left: 2px solid;
        }

        .agent-chip.running { background: #064e3b; }

        .pipeline-arrow {
          color: #64748b;
          font-size: 20px;
          transition: all 0.3s ease;
        }

        .pipeline-arrow.glowing {
          color: #10b981;
          text-shadow: 0 0 10px #10b981, 0 0 20px #10b981, 0 0 30px #10b981;
          animation: glow-pulse 1s ease-in-out infinite;
        }

        @keyframes glow-pulse {
          0%, 100% {
            text-shadow: 0 0 10px #10b981, 0 0 20px #10b981;
            opacity: 1;
          }
          50% {
            text-shadow: 0 0 20px #10b981, 0 0 40px #10b981, 0 0 60px #10b981;
            opacity: 0.8;
          }
        }

        .pipeline-stage.active-flow {
          background: linear-gradient(135deg, #064e3b 0%, #334155 100%);
          box-shadow: 0 0 15px rgba(16, 185, 129, 0.4);
        }

        .flow-indicator {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          background: #10b981;
          animation: flow-blink 0.5s ease-in-out infinite;
        }

        @keyframes flow-blink {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.5; transform: scale(1.2); }
        }

        .cycle-info {
          font-size: 12px;
          color: #64748b;
          text-align: center;
          margin-top: 8px;
        }

        .agents-by-layer {
          display: grid;
          grid-template-columns: repeat(5, 1fr);
          gap: 16px;
        }

        .layer-group h4 {
          font-size: 11px;
          margin-bottom: 8px;
        }

        .layer-agents {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }

        .agent-card {
          background: #334155;
          border-radius: 8px;
          padding: 10px;
          border-left: 3px solid;
        }

        .agent-card.running { background: #064e3b; }
        .agent-card.blocked { background: #7f1d1d; }

        .agent-header {
          display: flex;
          align-items: center;
          gap: 6px;
          margin-bottom: 4px;
        }

        .agent-num {
          font-size: 10px;
          color: #64748b;
        }

        .agent-name {
          font-size: 12px;
          font-weight: 500;
          flex: 1;
        }

        .status-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
        }

        .agent-meta {
          font-size: 10px;
          color: #64748b;
          display: flex;
          gap: 8px;
        }

        .agent-details {
          display: none;
          margin-top: 10px;
          padding: 12px;
          border-radius: 10px;
          background: #0f172a;
          border: 1px solid #334155;
          max-height: 280px;
          overflow: auto;
          user-select: text;
        }

        .agent-card:hover .agent-details,
        .agent-card:focus-within .agent-details {
          display: block;
        }

        .agent-details-toolbar {
          display: flex;
          justify-content: flex-end;
          margin-bottom: 10px;
        }

        .pin-button {
          border: 1px solid #334155;
          background: #1e293b;
          color: #e2e8f0;
          border-radius: 8px;
          padding: 6px 10px;
          font-size: 12px;
          cursor: pointer;
        }

        .pin-button.active {
          background: #10b981;
          border-color: #10b981;
          color: #052e16;
          font-weight: 700;
        }

        .agent-details-section + .agent-details-section {
          margin-top: 10px;
          padding-top: 10px;
          border-top: 1px solid #334155;
        }

        .agent-details-section strong {
          display: block;
          margin-bottom: 6px;
          font-size: 11px;
          color: #e2e8f0;
        }

        .agent-details-section pre {
          margin: 0;
          white-space: pre-wrap;
          word-break: break-word;
          font-size: 11px;
          color: #94a3b8;
        }

        .trade-detail-overlay {
          position: fixed;
          inset: 0;
          background: rgba(0,0,0,0.7);
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 1000;
        }

        .trade-detail-modal {
          background: #1e293b;
          border-radius: 12px;
          padding: 24px;
          max-width: 500px;
          max-height: 80vh;
          overflow-y: auto;
        }

        .trade-detail-modal h4 {
          margin-bottom: 16px;
        }

        .detail-section {
          margin-bottom: 16px;
          padding-bottom: 16px;
          border-bottom: 1px solid #334155;
        }

        .detail-section h5 {
          font-size: 12px;
          color: #64748b;
          margin-bottom: 8px;
        }

        .detail-section p {
          font-size: 14px;
          margin: 4px 0;
        }

        .detail-section ul {
          list-style: none;
          padding: 0;
        }

        .detail-section li {
          font-size: 13px;
          padding: 6px 0;
          border-bottom: 1px solid #334155;
        }

        .detail-section pre {
          font-size: 11px;
          background: #0f172a;
          padding: 12px;
          border-radius: 6px;
          overflow-x: auto;
        }

        .trade-detail-modal button {
          width: 100%;
          padding: 12px;
          background: #3b82f6;
          border: none;
          border-radius: 8px;
          color: white;
          font-weight: 500;
          cursor: pointer;
        }

        .positions-empty, .trades-empty {
          text-align: center;
          padding: 40px;
          color: #64748b;
        }

        @media (max-width: 1200px) {
          .dashboard-grid {
            grid-template-columns: 1fr;
          }
          .heatmap-grid {
            grid-template-columns: repeat(3, minmax(0, 1fr));
          }
          .replay-controls, .replay-metrics-grid, .replay-diagnostics-grid {
            grid-template-columns: 1fr;
          }
          .agents-by-layer {
            grid-template-columns: repeat(3, 1fr);
          }
        }
      `}</style>

      <header className="dashboard-header">
        <h1>
          <span>Scalping Dashboard</span>
          <span style={{ fontSize: 14, color: '#64748b' }}>21-Agent System</span>
        </h1>
        <div className={`status-indicator ${status?.running ? 'running' : 'stopped'}`}>
          <div className={`connection-dot ${connected ? 'connected' : ''}`} />
          <span>{status?.mode || 'OFFLINE'}</span>
          <span style={{ color: '#64748b' }}>|</span>
          <span>Cycle #{status?.cycle_count || 0}</span>
        </div>
      </header>

      {error && (
        <div className="error-banner">{error}</div>
      )}

      <div className="debate-controls">
        <div className="debate-label">
          <strong>LLM Mode</strong>
          <span>Controls debate for all bots</span>
        </div>
        <select
          value={debateMode}
          onChange={(event) => updateDebateMode(event.target.value)}
        >
          <option value="debate">Debate (Claude + GPT)</option>
          <option value="single">Single GPT Suggestion</option>
          <option value="off">Off</option>
        </select>
      </div>

      <div className="learning-controls">
        <div className="debate-label">
          <strong>Learning Mode</strong>
          <span>Hybrid = intraday micro tweaks + daily review</span>
        </div>
        <div className="learning-actions">
          <select
            value={learningMode}
            onChange={(event) => updateLearningMode(event.target.value)}
          >
            <option value="hybrid">Hybrid (Recommended)</option>
            <option value="daily">Daily Only</option>
            <option value="immediate">Immediate</option>
            <option value="off">Off</option>
          </select>
          <button type="button" className="learning-reset" onClick={resetLearningMode}>
            Reset
          </button>
        </div>
      </div>

      {learningMetrics && (
        <div className="learning-metrics">
          <span>
            Recent trades: {learningMetrics.closed_trades || 0} |
            Win rate: {learningMetrics.win_rate_pct || 0}%
          </span>
          <span>
            Avg spread: {learningMetrics.average_spread_pct || 0}%
            {learningLastUpdate ? ` | Updated: ${new Date(learningLastUpdate).toLocaleTimeString()}` : ''}
          </span>
        </div>
      )}

      <EngineModeControls
        selectedMode={selectedMode}
        onModeChange={setSelectedMode}
        replay={replay}
        selectedFile={selectedReplayFile}
        onFileChange={(event) => setSelectedReplayFile(event.target.files?.[0] || null)}
        onRunReplay={runReplay}
        onRunPostmortem={runPostmortem}
        onReplayControl={sendReplayControl}
        replaySeekPct={replaySeekPct}
        onSeekStart={handleSeekStart}
        onSeekEnd={handleSeekEnd}
        onSeekChange={(value) => setReplaySeekPct(Number(value))}
        currentEngineMode={status?.mode}
      />

      <ReplayResults replay={replay} ref={replayResultsRef} />

      <div className="dashboard-grid">
        <div className="full-width">
          <PipelineView pipeline={pipeline} agents={agents?.agents || []} dataflow={dataflow} />
        </div>

        <CapitalSummary capital={capital} />
        <PositionsTable positions={positions} agents={agents} />

        <div className="full-width">
          <TradeHistory trades={trades} />
        </div>

        <div className="full-width">
      <AgentGrid agents={agents} />
        </div>
      </div>
    </div>
  );
}
