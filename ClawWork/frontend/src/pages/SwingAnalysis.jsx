import {
  Activity,
  ArrowDownCircle,
  ArrowUpCircle,
  BarChart2,
  CheckCircle,
  Circle,
  Crosshair,
  Download,
  Eraser,
  MousePointer2,
  Move,
  Pencil,
  RefreshCw,
  Repeat,
  Square,
  Target,
  Trash2,
  TrendingDown,
  TrendingUp,
  Upload,
  XCircle,
  ZoomIn,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Plot from 'react-plotly.js'
import { fetchFyersN7LiveSignals } from '../api'

/**
 * EXPERT SWING DETECTION with PLOTLY Scientific Charts
 *
 * Features:
 * - Mouse drag to pan
 * - Scroll wheel to zoom
 * - Box/lasso select
 * - Crosshair hover
 * - Time axis with labels
 * - Professional annotations
 */

// Expert-tuned weights for each signal
const SIGNAL_WEIGHTS = {
  pcrExtreme: 25,
  vixSpike: 20,
  volumeSpike: 20,
  oiChange: 18,
  maxPainDist: 15,
  futBasis: 12,
  deltaExtreme: 10,
  gammaHigh: 8,
  voteContrarian: 12,
  confidenceHigh: 10,
  stableSignal: 8,
  entryReady: 8,
  flowMatch: 6,
  ivHigh: 5,
  spreadWide: 5,
  volOiRatio: 8,
  outcomeWin: 30,
}

const CHART_TOOL_OPTIONS = [
  { id: 'cursor', label: 'Cursor', icon: MousePointer2 },
  { id: 'pan', label: 'Pan', icon: Move },
  { id: 'zoom', label: 'Zoom', icon: ZoomIn },
  { id: 'drawline', label: 'Line', icon: Pencil },
  { id: 'drawopenpath', label: 'Path', icon: Crosshair },
  { id: 'drawrect', label: 'Rect', icon: Square },
  { id: 'drawcircle', label: 'Circle', icon: Circle },
  { id: 'eraseshape', label: 'Erase', icon: Eraser },
]

const SWING_WORKSPACE_STORAGE_KEY = 'swing_analysis_workspace_v1'
const INDEX_STRIKE_STEP_DEFAULTS = {
  NIFTY50: 50,
  BANKNIFTY: 100,
  FINNIFTY: 50,
  SENSEX: 100,
  MIDCPNIFTY: 25,
}
const INDEX_EXPIRY_WEEKDAY_DEFAULTS = {
  NIFTY50: 3,
  BANKNIFTY: 2,
  FINNIFTY: 1,
  SENSEX: 4,
  MIDCPNIFTY: 0,
}
const MONTHLY_OPTION_EXPIRY_OVERRIDES = {
  '2026-03': {
    SENSEX: '2026-03-12',
    BANKNIFTY: '2026-03-30',
    NIFTY50: '2026-03-10',
    FINNIFTY: '2026-03-10',
    MIDCPNIFTY: '2026-03-09',
  },
  '2026-04': {
    SENSEX: '2026-04-09',
    BANKNIFTY: '2026-04-29',
    NIFTY50: '2026-04-07',
    FINNIFTY: '2026-04-07',
    MIDCPNIFTY: '2026-04-06',
  },
}
const MONTH_TEXT_TO_NUMBER = {
  JAN: 1,
  FEB: 2,
  MAR: 3,
  APR: 4,
  MAY: 5,
  JUN: 6,
  JUL: 7,
  AUG: 8,
  SEP: 9,
  OCT: 10,
  NOV: 11,
  DEC: 12,
}
const MONTH_NUMBER_TO_TEXT = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
const normalizeIndexKey = (value) => String(value || '').trim().toUpperCase().replace(/[^A-Z0-9]/g, '')
const normalizeOptionSideValue = (value) => {
  const side = String(value || '').trim().toUpperCase()
  if (side === 'CE' || side === 'CALL') return 'CE'
  if (side === 'PE' || side === 'PUT') return 'PE'
  return ''
}
const STRIKE_LADDER_DEFS = [
  { id: 'CE_ATM', side: 'CE', offset: 0, label: 'CE ATM' },
  { id: 'CE_1_OTM', side: 'CE', offset: 1, label: 'CE 1 OTM' },
  { id: 'CE_2_OTM', side: 'CE', offset: 2, label: 'CE 2 OTM' },
  { id: 'CE_3_OTM', side: 'CE', offset: 3, label: 'CE 3 OTM' },
  { id: 'CE_4_OTM', side: 'CE', offset: 4, label: 'CE 4 OTM' },
  { id: 'PE_ATM', side: 'PE', offset: 0, label: 'PE ATM' },
  { id: 'PE_1_OTM', side: 'PE', offset: 1, label: 'PE 1 OTM' },
  { id: 'PE_2_OTM', side: 'PE', offset: 2, label: 'PE 2 OTM' },
  { id: 'PE_3_OTM', side: 'PE', offset: 3, label: 'PE 3 OTM' },
  { id: 'PE_4_OTM', side: 'PE', offset: 4, label: 'PE 4 OTM' },
]
const STRIKE_LADDER_STYLE = {
  CE_ATM: { color: '#22c55e', opacity: 0.45 },
  CE_1_OTM: { color: '#16a34a', opacity: 0.40 },
  CE_2_OTM: { color: '#15803d', opacity: 0.35 },
  CE_3_OTM: { color: '#166534', opacity: 0.3 },
  CE_4_OTM: { color: '#14532d', opacity: 0.25 },
  PE_ATM: { color: '#ef4444', opacity: 0.45 },
  PE_1_OTM: { color: '#dc2626', opacity: 0.40 },
  PE_2_OTM: { color: '#b91c1c', opacity: 0.35 },
  PE_3_OTM: { color: '#991b1b', opacity: 0.3 },
  PE_4_OTM: { color: '#7f1d1d', opacity: 0.25 },
}
const DEFAULT_STRIKE_LEG_IDS = ['CE_ATM', 'PE_ATM', 'CE_1_OTM', 'PE_1_OTM']
const VIEW_PRESET_OPTIONS = [
  { id: 'price-only', label: 'Price Only' },
  { id: 'signals', label: 'Signals' },
  { id: 'index-vs-strikes', label: 'Index vs Strikes' },
]
const GRADE_CONFLUENCE_WEIGHTS = {
  'A+': 4.0,
  A: 3.2,
  'B+': 2.3,
  B: 1.5,
  'C+': 0.9,
  C: 0.4,
}
const DECISION_3D_SETTINGS = {
  smoothingAlpha: 0.35,
  enterComposite: 24,
  exitComposite: 12,
  reverseComposite: 34,
  enterConfidence: 58,
  reverseConfidence: 64,
  persistenceBars: 2,
}
const OPTION_DELTA_FALLBACK_BY_OFFSET = {
  0: 0.5,
  1: 0.38,
  2: 0.28,
  3: 0.2,
  4: 0.14,
}

const toTimestampValue = (value) => {
  if (!value) return null
  const normalized = String(value).trim().replace(' ', 'T')
  const ts = Date.parse(normalized)
  return Number.isFinite(ts) ? ts : null
}

const detectMarketRegime = (rows = []) => {
  if (!Array.isArray(rows) || rows.length < 8) {
    return { key: 'range', label: 'RANGING', changePct: 0, volatilityPct: 0 }
  }

  const closes = rows
    .map((row) => (Number.isFinite(Number(row?.close)) ? Number(row.close) : Number(row?.spot)))
    .filter((value) => Number.isFinite(value) && value > 0)
  if (closes.length < 8) return { key: 'range', label: 'RANGING', changePct: 0, volatilityPct: 0 }

  const lookback = Math.min(30, closes.length - 1)
  const start = closes[closes.length - 1 - lookback]
  const end = closes[closes.length - 1]
  const changePct = start > 0 ? ((end - start) / start) * 100 : 0

  const returns = []
  for (let i = closes.length - lookback; i < closes.length; i++) {
    const prev = closes[i - 1]
    const curr = closes[i]
    if (prev > 0 && curr > 0) {
      returns.push(((curr - prev) / prev) * 100)
    }
  }

  const avgRet = returns.length
    ? returns.reduce((sum, value) => sum + value, 0) / returns.length
    : 0
  const variance = returns.length
    ? returns.reduce((sum, value) => sum + ((value - avgRet) ** 2), 0) / returns.length
    : 0
  const volatilityPct = Math.sqrt(Math.max(variance, 0))

  if (changePct >= 0.35) return { key: 'bull', label: 'BULL TREND', changePct, volatilityPct }
  if (changePct <= -0.35) return { key: 'bear', label: 'BEAR TREND', changePct, volatilityPct }
  return { key: 'range', label: 'RANGING', changePct, volatilityPct }
}

const computeConfluenceSignals = (swingHighs, swingLows, regimeKey = 'range') => {
  const all = [...(swingHighs || []), ...(swingLows || [])]
    .sort((a, b) => (Number(a?.index) || 0) - (Number(b?.index) || 0))

  return all.map((swing) => {
    const gradeWeight = GRADE_CONFLUENCE_WEIGHTS[swing?.grade] || 0
    let score = gradeWeight * 16

    const signalText = (swing?.signals || []).join(' ')
    if (signalText.includes('FVG+')) score += 14
    else if (signalText.includes('FVG')) score += 9
    if (signalText.includes('LQ Sweep')) score += 10

    if (swing?.details?.entryReady) score += 8
    if (swing?.details?.flowMatch) score += 6
    if (swing?.details?.stable) score += 5
    if (Number.isFinite(Number(swing?.details?.confidence)) && Number(swing.details.confidence) >= 85) score += 5

    const alignsBull = swing?.type === 'low'
    const alignsBear = swing?.type === 'high'
    if (regimeKey === 'bull') score += alignsBull ? 8 : -5
    if (regimeKey === 'bear') score += alignsBear ? 8 : -5
    if (regimeKey === 'range') score += 2

    const clamped = Math.max(0, Math.min(100, score))
    return {
      ...swing,
      confluenceScore: clamped,
      direction: swing?.type === 'low' ? 'BUY' : 'SELL',
    }
  })
}

const mean = (values = []) => {
  const finite = values.filter((value) => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)))
  if (!finite.length) return null
  return finite.reduce((sum, value) => sum + Number(value), 0) / finite.length
}

const stdDev = (values = []) => {
  const avg = mean(values)
  if (!Number.isFinite(avg)) return null
  const finite = values
    .filter((value) => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)))
    .map(Number)
  if (!finite.length) return null
  const variance = finite.reduce((sum, value) => sum + ((value - avg) ** 2), 0) / finite.length
  return Math.sqrt(Math.max(0, variance))
}

const clamp = (value, min, max) => Math.min(max, Math.max(min, value))

const formatStrikeValue = (strike) => {
  const numeric = Number(strike)
  if (!Number.isFinite(numeric)) return '-'
  return Number.isInteger(numeric) ? numeric.toFixed(0) : numeric.toFixed(2)
}

const parseDateOnly = (value) => {
  const trimmed = String(value || '').trim()
  const match = trimmed.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!match) return null
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const parsed = new Date(year, month - 1, day)
  if (
    Number.isNaN(parsed.getTime())
    || parsed.getFullYear() !== year
    || parsed.getMonth() !== (month - 1)
    || parsed.getDate() !== day
  ) {
    return null
  }
  parsed.setHours(0, 0, 0, 0)
  return parsed
}

const formatDateOnly = (value) => {
  if (!(value instanceof Date) || Number.isNaN(value.getTime())) return ''
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`
}

const formatExpiryCode = (value) => {
  if (!(value instanceof Date) || Number.isNaN(value.getTime())) return ''
  const month = MONTH_NUMBER_TO_TEXT[value.getMonth()] || ''
  const year = String(value.getFullYear()).slice(-2)
  return `${String(value.getDate()).padStart(2, '0')}${month}${year}`
}

const getWeekStart = (value) => {
  if (!(value instanceof Date) || Number.isNaN(value.getTime())) return null
  const out = new Date(value)
  out.setHours(0, 0, 0, 0)
  const mondayOffset = (out.getDay() + 6) % 7
  out.setDate(out.getDate() - mondayOffset)
  return out
}

const isSameExpiryWeek = (left, right) => {
  const leftWeek = getWeekStart(left)
  const rightWeek = getWeekStart(right)
  if (!leftWeek || !rightWeek) return false
  return leftWeek.getTime() === rightWeek.getTime()
}

const nextWeekdayOnOrAfter = (startDate, weekday) => {
  if (!(startDate instanceof Date) || Number.isNaN(startDate.getTime()) || !Number.isInteger(weekday)) return null
  const out = new Date(startDate)
  out.setHours(0, 0, 0, 0)
  const currentWeekday = (out.getDay() + 6) % 7
  const delta = (weekday - currentWeekday + 7) % 7
  out.setDate(out.getDate() + delta)
  return out
}

const inferIndexNameFromValue = (value) => {
  const normalized = normalizeIndexKey(value)
  if (!normalized) return ''
  if (normalized.includes('BANKNIFTY')) return 'BANKNIFTY'
  if (normalized.includes('FINNIFTY')) return 'FINNIFTY'
  if (normalized.includes('MIDCPNIFTY')) return 'MIDCPNIFTY'
  if (normalized.includes('SENSEX')) return 'SENSEX'
  if (normalized.includes('NIFTY')) return 'NIFTY50'
  return normalized
}

const getMonthlyExpiryOverride = (indexName, year, month) => {
  const monthKey = `${year}-${String(month).padStart(2, '0')}`
  return parseDateOnly(MONTHLY_OPTION_EXPIRY_OVERRIDES?.[monthKey]?.[indexName])
}

const parseLooseExpiryValue = (value, fallbackIndexName = '') => {
  const trimmed = String(value || '').trim().toUpperCase()
  if (!trimmed) return null

  const directDate = parseDateOnly(trimmed)
  if (directDate) return directDate

  const compactDate = trimmed.match(/^(?<dd>\d{2})(?<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(?<yy>\d{2})$/)
  if (compactDate) {
    const dd = Number(compactDate.groups.dd)
    const mm = MONTH_TEXT_TO_NUMBER[compactDate.groups.mon]
    const year = 2000 + Number(compactDate.groups.yy)
    return parseDateOnly(`${year}-${String(mm).padStart(2, '0')}-${String(dd).padStart(2, '0')}`)
  }

  return null
}

const parseExpiryDateFromOptionSymbol = (symbol, fallbackIndexName = '') => {
  const normalizedSymbol = String(symbol || '').trim().toUpperCase()
  if (!normalizedSymbol) return null
  const raw = normalizedSymbol.split(':').pop() || normalizedSymbol

  const weeklyYMDD = raw.match(/^[A-Z]+(?<yy>\d{2})(?<m>[1-9OND])(?<dd>\d{2})\d+(CE|PE)$/)
  if (weeklyYMDD) {
    const year = 2000 + Number(weeklyYMDD.groups.yy)
    const month = { O: 10, N: 11, D: 12 }[weeklyYMDD.groups.m] || Number(weeklyYMDD.groups.m)
    const day = Number(weeklyYMDD.groups.dd)
    return parseDateOnly(`${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`)
  }

  const weeklyYMMDD = raw.match(/^[A-Z]+(?<yy>\d{2})(?<mm>\d{2})(?<dd>\d{2})\d+(CE|PE)$/)
  if (weeklyYMMDD) {
    const year = 2000 + Number(weeklyYMMDD.groups.yy)
    const month = Number(weeklyYMMDD.groups.mm)
    const day = Number(weeklyYMMDD.groups.dd)
    return parseDateOnly(`${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`)
  }

  const weeklyDDMonYY = raw.match(/^[A-Z]+(?<dd>\d{2})(?<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(?<yy>\d{2})\d+(CE|PE)$/)
  if (weeklyDDMonYY) {
    const year = 2000 + Number(weeklyDDMonYY.groups.yy)
    const month = MONTH_TEXT_TO_NUMBER[weeklyDDMonYY.groups.mon]
    const day = Number(weeklyDDMonYY.groups.dd)
    return parseDateOnly(`${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`)
  }

  const monthlyYMon = raw.match(/^[A-Z]+(?<yy>\d{2})(?<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d+(CE|PE)$/)
  if (monthlyYMon) {
    const year = 2000 + Number(monthlyYMon.groups.yy)
    const month = MONTH_TEXT_TO_NUMBER[monthlyYMon.groups.mon]
    const inferredIndex = inferIndexNameFromValue(raw) || fallbackIndexName
    if (inferredIndex) {
      const override = getMonthlyExpiryOverride(inferredIndex, year, month)
      if (override) return override
    }
    const monthEnd = new Date(year, month, 0)
    monthEnd.setHours(0, 0, 0, 0)
    return monthEnd
  }

  return null
}

const computeSessionOptionExpiryDate = (indexName, sessionDate) => {
  const normalizedIndex = inferIndexNameFromValue(indexName)
  const baseDate = parseDateOnly(sessionDate)
  const expiryWeekday = INDEX_EXPIRY_WEEKDAY_DEFAULTS[normalizedIndex]
  if (!baseDate || !Number.isInteger(expiryWeekday)) return null

  let cursor = new Date(baseDate)
  for (let attempt = 0; attempt < 8; attempt++) {
    const candidate = nextWeekdayOnOrAfter(cursor, expiryWeekday)
    if (!candidate) return null

    const monthlyOverride = getMonthlyExpiryOverride(normalizedIndex, candidate.getFullYear(), candidate.getMonth() + 1)
    if (monthlyOverride && isSameExpiryWeek(candidate, monthlyOverride) && monthlyOverride.getTime() <= candidate.getTime()) {
      if (baseDate.getTime() <= monthlyOverride.getTime()) return monthlyOverride
      cursor = new Date(candidate)
      cursor.setDate(cursor.getDate() + 1)
      continue
    }

    return candidate
  }

  return nextWeekdayOnOrAfter(baseDate, expiryWeekday)
}

const resolveOptionExpiryDate = ({ explicitExpiry, explicitExpiryCode, optionSymbol, indexName, sessionDate }) => (
  parseLooseExpiryValue(explicitExpiry, indexName)
  || parseLooseExpiryValue(explicitExpiryCode, indexName)
  || parseExpiryDateFromOptionSymbol(optionSymbol, indexName)
  || computeSessionOptionExpiryDate(indexName, sessionDate)
)

const buildOptionContractLabel = ({ indexName, expiryCode, strike, side, fallbackLabel = '' }) => {
  const normalizedIndex = inferIndexNameFromValue(indexName)
  const normalizedSide = normalizeOptionSideValue(side)
  const strikeText = Number.isFinite(Number(strike)) ? formatStrikeValue(strike) : ''
  if (normalizedIndex && expiryCode && strikeText && normalizedSide) {
    return `${normalizedIndex} ${expiryCode} ${strikeText} ${normalizedSide}`
  }
  if (strikeText && normalizedSide) {
    return `${normalizedSide} ${strikeText}`
  }
  return fallbackLabel || ''
}

const getTradeDisplayLabel = (trade) => String(trade?.contractLabel || trade?.instrumentLabel || '').trim()

const getOptionSideFromLabel = (value) => {
  const text = String(value || '').trim().toUpperCase()
  if (!text) return ''
  if (/(^|[^A-Z])CE($|[^A-Z])/.test(text)) return 'CE'
  if (/(^|[^A-Z])PE($|[^A-Z])/.test(text)) return 'PE'
  return ''
}

const getOptionSideTextClass = (value, fallbackSide = '') => {
  const side = normalizeOptionSideValue(fallbackSide) || getOptionSideFromLabel(value)
  if (side === 'CE') return 'text-emerald-300'
  if (side === 'PE') return 'text-rose-300'
  return 'text-slate-300'
}

const enrichOptionRow = (row = {}) => {
  const symbol = String(row?.symbol || '').trim()
  const contractSymbol = String(row?.contract_symbol || row?.contractSymbol || row?.option_symbol || row?.optionSymbol || '').trim()
  const indexName = inferIndexNameFromValue(symbol || contractSymbol || row?.fut_symbol || '')
  const side = normalizeOptionSideValue(row?.side)
  const strike = Number(row?.strike)
  const optionExpiryDate = resolveOptionExpiryDate({
    explicitExpiry: row?.option_expiry || row?.optionExpiry || row?.expiry || row?.expiry_date,
    explicitExpiryCode: row?.option_expiry_code || row?.optionExpiryCode,
    optionSymbol: contractSymbol,
    indexName,
    sessionDate: row?.date,
  })
  const optionExpiry = optionExpiryDate ? formatDateOnly(optionExpiryDate) : ''
  const optionExpiryCode = optionExpiryDate ? formatExpiryCode(optionExpiryDate) : ''
  const contractLabel = buildOptionContractLabel({
    indexName,
    expiryCode: optionExpiryCode,
    strike,
    side,
    fallbackLabel: side && Number.isFinite(strike) ? `${side} ${formatStrikeValue(strike)}` : '',
  })

  return {
    ...row,
    indexName,
    side,
    strike: Number.isFinite(strike) ? strike : null,
    contractSymbol,
    optionExpiry,
    optionExpiryCode,
    contractLabel,
  }
}

const buildTradePlanContractLabel = (tradePlan = {}) => {
  const decision = String(tradePlan?.decision || '').trim().toUpperCase()
  const instrumentLabel = String(tradePlan?.contractLabel || tradePlan?.instrumentLabel || '').trim()
  const bucketShortLabel = String(tradePlan?.bucketShortLabel || '').trim()
  const parts = [decision, instrumentLabel].filter(Boolean)
  const title = parts.join(' ').trim()
  if (bucketShortLabel) {
    return title ? `${title} (${bucketShortLabel})` : bucketShortLabel
  }
  return title || 'Trade Plan'
}

const resolveSnapshotLeg = (snapshot, definition, strikeStep) => {
  if (!snapshot || !definition || !Number.isFinite(Number(snapshot.spot)) || !Number.isFinite(Number(strikeStep)) || strikeStep <= 0) {
    return null
  }
  const spot = Number(snapshot.spot)
  const atm = Math.round(spot / strikeStep) * strikeStep
  const strike = definition.side === 'CE'
    ? atm + (definition.offset * strikeStep)
    : atm - (definition.offset * strikeStep)
  const row = snapshot.legs?.[definition.side]?.get(strike) || null
  if (!row) return null
  const mid = getOptionMidPrice(row)
  return {
    row,
    strike,
    atm,
    mid: Number.isFinite(Number(mid)) ? Number(mid) : null,
    definitionId: definition.id,
    definitionLabel: definition.label,
    instrumentLabel: `${definition.side} ${formatStrikeValue(strike)}`,
    contractLabel: row?.contractLabel || buildOptionContractLabel({
      indexName: row?.indexName || snapshot?.indexName || row?.symbol || '',
      expiryCode: row?.optionExpiryCode || '',
      strike,
      side: definition.side,
      fallbackLabel: `${definition.side} ${formatStrikeValue(strike)}`,
    }),
    contractSymbol: row?.contractSymbol || '',
    optionExpiry: row?.optionExpiry || '',
    optionExpiryCode: row?.optionExpiryCode || '',
    bucketLabel: `${definition.label} -> ${row?.contractLabel || `${definition.side} ${formatStrikeValue(strike)}`}`,
  }
}

const getLegSpreadScore = (row) => {
  const bid = Number(row?.bid)
  const ask = Number(row?.ask)
  if (!Number.isFinite(bid) || !Number.isFinite(ask) || ask <= bid || (ask + bid) <= 0) return null
  const spreadPct = ((ask - bid) / ((ask + bid) / 2)) * 100
  return 100 - clamp(spreadPct * 120, 0, 100)
}

const getLegVolumeScore = (row) => {
  const volume = Number(row?.volume)
  if (!Number.isFinite(volume) || volume <= 0) return null
  return clamp(Math.log10(volume + 1) * 18, 0, 100)
}

const getLegOpenInterestScore = (row) => {
  const oi = Number(row?.oi)
  if (!Number.isFinite(oi) || oi <= 0) return null
  return clamp(Math.log10(oi + 1) * 16, 0, 100)
}

const getLegDeltaScore = (row) => {
  const deltaAbs = Math.abs(Number(row?.delta))
  if (!Number.isFinite(deltaAbs)) return null
  return clamp(deltaAbs * 100, 0, 100)
}

const buildLegDiagnostic = (currSnapshot, prevSnapshot, definition, strikeStep) => {
  const currLeg = resolveSnapshotLeg(currSnapshot, definition, strikeStep)
  const prevLeg = resolveSnapshotLeg(prevSnapshot, definition, strikeStep)
  const currRow = currLeg?.row || null
  const prevRow = prevLeg?.row || null
  const currMid = Number(currLeg?.mid)
  const prevMid = Number(prevLeg?.mid)
  const changePct = Number.isFinite(currMid) && Number.isFinite(prevMid) && prevMid > 0
    ? ((currMid - prevMid) / prevMid) * 100
    : null

  const spreadScore = getLegSpreadScore(currRow)
  const volumeScore = getLegVolumeScore(currRow)
  const oiScore = getLegOpenInterestScore(currRow)
  const deltaScore = getLegDeltaScore(currRow)
  const liquidityScore = mean([spreadScore, volumeScore, oiScore, deltaScore])
  const distanceScore = clamp(100 - (definition.offset * 15), 35, 100)
  const momentumScore = Number.isFinite(changePct) ? clamp(changePct * 6, 0, 100) : null
  const candidateScore = mean([
    momentumScore,
    liquidityScore,
    deltaScore,
    distanceScore,
  ])

  return {
    id: definition.id,
    label: definition.label,
    side: definition.side,
    offset: definition.offset,
    strike: Number.isFinite(Number(currLeg?.strike)) ? Number(currLeg.strike) : null,
    instrumentLabel: currLeg?.instrumentLabel || definition.label,
    contractLabel: currLeg?.contractLabel || currLeg?.instrumentLabel || definition.label,
    contractSymbol: currLeg?.contractSymbol || '',
    optionExpiry: currLeg?.optionExpiry || '',
    optionExpiryCode: currLeg?.optionExpiryCode || '',
    bucketLabel: currLeg?.bucketLabel || definition.label,
    mid: Number.isFinite(currMid) ? currMid : null,
    prevMid: Number.isFinite(prevMid) ? prevMid : null,
    changePct,
    spreadScore,
    volumeScore,
    oiScore,
    delta: Number.isFinite(Number(currRow?.delta)) ? Number(currRow.delta) : null,
    deltaScore,
    liquidityScore,
    candidateScore,
  }
}

const pickTradeCandidate = (legDiagnostics, side) => (
  [...legDiagnostics]
    .filter((diagnostic) => diagnostic.side === side && Number.isFinite(Number(diagnostic.mid)))
    .sort((left, right) => {
      const scoreDelta = (Number(right.candidateScore) || -1) - (Number(left.candidateScore) || -1)
      if (scoreDelta !== 0) return scoreDelta
      const offsetDelta = (Number(left.offset) || 99) - (Number(right.offset) || 99)
      if (offsetDelta !== 0) return offsetDelta
      return (Number(right.changePct) || -999) - (Number(left.changePct) || -999)
    })[0] || null
)

const estimateOptionDeltaAbs = (trade) => {
  const absoluteDelta = Math.abs(Number(trade?.delta))
  if (Number.isFinite(absoluteDelta) && absoluteDelta > 0.05) return absoluteDelta
  const fallback = OPTION_DELTA_FALLBACK_BY_OFFSET[Number(trade?.offset)]
  return Number.isFinite(fallback) ? fallback : 0.25
}

const extractInstrumentMidSeries = (snapshots = [], trade) => {
  if (!Array.isArray(snapshots) || !trade?.side || !Number.isFinite(Number(trade?.strike))) return []
  return snapshots
    .map((snapshot) => {
      const row = snapshot?.legs?.[trade.side]?.get(Number(trade.strike))
      const mid = getOptionMidPrice(row)
      return Number.isFinite(Number(mid)) ? Number(mid) : null
    })
    .filter((value) => Number.isFinite(Number(value)))
}

const findRecentSwingBelow = (swings = [], spot) => (
  [...(swings || [])]
    .reverse()
    .find((swing) => Number.isFinite(Number(swing?.spot)) && Number(swing.spot) < spot) || null
)

const findRecentSwingAbove = (swings = [], spot) => (
  [...(swings || [])]
    .reverse()
    .find((swing) => Number.isFinite(Number(swing?.spot)) && Number(swing.spot) > spot) || null
)

const computeStructuredTicketLevels = ({
  trade,
  decision,
  spot,
  swingHighs = [],
  swingLows = [],
  snapshots = [],
}) => {
  const entry = Number(trade?.mid)
  const referenceSpot = Number(spot)
  if (!Number.isFinite(entry) || entry <= 0 || !Number.isFinite(referenceSpot) || !decision) return null

  const bullish = decision === 'LONG'
  const stopSwing = bullish
    ? findRecentSwingBelow(swingLows, referenceSpot)
    : findRecentSwingAbove(swingHighs, referenceSpot)
  const targetSwing = bullish
    ? findRecentSwingAbove(swingHighs, referenceSpot)
    : findRecentSwingBelow(swingLows, referenceSpot)

  const structureRiskSpot = stopSwing ? Math.abs(referenceSpot - Number(stopSwing.spot)) : null
  const fallbackRiskSpot = Math.max(referenceSpot * 0.0008, 8)
  const spotRisk = Math.max(Number(structureRiskSpot) || 0, fallbackRiskSpot)

  const structureTargetSpot = targetSwing ? Math.abs(Number(targetSwing.spot) - referenceSpot) : null
  const spotTargetDistance = Math.max(Number(structureTargetSpot) || 0, spotRisk * 1.8)

  const deltaAbs = estimateOptionDeltaAbs(trade)
  const mids = extractInstrumentMidSeries(snapshots, trade)
  const recentDiffs = []
  for (let i = 1; i < mids.length; i++) {
    recentDiffs.push(Math.abs(mids[i] - mids[i - 1]))
  }
  const recentMoveMean = mean(recentDiffs.slice(-5))
  const optionMoveFloor = Math.max(entry * 0.06, Number(recentMoveMean) * 1.25 || 0, 1)
  const optionRisk = Math.max(spotRisk * deltaAbs, optionMoveFloor)
  const optionTargetDistance = Math.max(spotTargetDistance * deltaAbs, optionRisk * 1.8)

  const stop = Math.max(0.05, entry - optionRisk)
  const target = entry + optionTargetDistance
  const stopSpot = stopSwing
    ? Number(stopSwing.spot)
    : (bullish ? referenceSpot - spotRisk : referenceSpot + spotRisk)
  const targetSpot = targetSwing
    ? Number(targetSwing.spot)
    : (bullish ? referenceSpot + spotTargetDistance : referenceSpot - spotTargetDistance)

  return {
    stop,
    target,
    stopSpot,
    targetSpot,
    deltaAbs,
    optionRisk,
    optionTargetDistance,
    fromStructure: Boolean(stopSwing || targetSwing),
  }
}

const build3DDecisionSeries = (snapshots, strikeStep, selectedDefs = [], settings = {}) => {
  if (!Array.isArray(snapshots) || snapshots.length < 8 || !Number.isFinite(Number(strikeStep)) || strikeStep <= 0 || !selectedDefs.length) {
    return []
  }

  const decisionSettings = {
    ...DECISION_3D_SETTINGS,
    ...settings,
    persistenceBars: Math.max(1, Number(settings?.persistenceBars) || DECISION_3D_SETTINGS.persistenceBars),
  }

  const rawStates = []
  for (let i = 5; i < snapshots.length; i++) {
    const curr = snapshots[i]
    const prev = snapshots[i - 3]
    const spotNow = Number(curr?.spot)
    const spotPrev = Number(prev?.spot)
    if (!Number.isFinite(spotNow) || !Number.isFinite(spotPrev) || spotPrev <= 0) continue

    const structurePct = ((spotNow - spotPrev) / spotPrev) * 100
    const x = clamp(structurePct * 14, -100, 100)

    const legDiagnostics = selectedDefs.map((definition) => buildLegDiagnostic(curr, prev, definition, strikeStep))
    const ceRets = legDiagnostics
      .filter((diagnostic) => diagnostic.side === 'CE' && Number.isFinite(Number(diagnostic.changePct)))
      .map((diagnostic) => Number(diagnostic.changePct))
    const peRets = legDiagnostics
      .filter((diagnostic) => diagnostic.side === 'PE' && Number.isFinite(Number(diagnostic.changePct)))
      .map((diagnostic) => Number(diagnostic.changePct))
    const spreadScores = legDiagnostics.map((diagnostic) => diagnostic.spreadScore)
    const volumeScores = legDiagnostics.map((diagnostic) => diagnostic.volumeScore)

    const ceMean = mean(ceRets)
    const peMean = mean(peRets)
    const flowDelta = Number.isFinite(ceMean) && Number.isFinite(peMean)
      ? ceMean - peMean
      : Number.isFinite(ceMean)
        ? ceMean
        : Number.isFinite(peMean)
          ? -peMean
          : 0
    const y = clamp(flowDelta * 110, -100, 100)

    const spotReturns = []
    for (let j = i - 4; j <= i; j++) {
      const nowSpot = Number(snapshots[j]?.spot)
      const prevSpot = Number(snapshots[j - 1]?.spot)
      if (Number.isFinite(nowSpot) && Number.isFinite(prevSpot) && prevSpot > 0) {
        spotReturns.push(((nowSpot - prevSpot) / prevSpot) * 100)
      }
    }
    const vol = stdDev(spotReturns)
    const avgSpreadScore = mean(spreadScores)
    const avgVolumeScore = mean(volumeScores)
    const qualityBase = mean([
      Number.isFinite(avgSpreadScore) ? avgSpreadScore : null,
      Number.isFinite(avgVolumeScore) ? avgVolumeScore : null,
      60,
    ])
    const volPenalty = Number.isFinite(vol) ? clamp(vol * 680, 0, 40) : 0
    const z = clamp((Number.isFinite(qualityBase) ? qualityBase : 50) - volPenalty, -100, 100)

    const compositeRaw = clamp((0.42 * x) + (0.38 * y) + (0.20 * z), -100, 100)
    const magnitudeRaw = clamp((Math.sqrt((x ** 2) + (y ** 2) + (z ** 2)) / Math.sqrt(3)), 0, 100)

    rawStates.push({
      key: curr.key,
      time: curr.time || curr.key,
      date: curr.date || '',
      minuteKey: curr.date && curr.time ? `${curr.date} ${roundToMinute(curr.time)}` : curr.key,
      spot: spotNow,
      xRaw: x,
      yRaw: y,
      zRaw: z,
      compositeRaw,
      magnitudeRaw,
      preferredCall: pickTradeCandidate(legDiagnostics, 'CE'),
      preferredPut: pickTradeCandidate(legDiagnostics, 'PE'),
      legDiagnostics,
    })
  }

  if (!rawStates.length) return []

  let smoothX = rawStates[0].xRaw
  let smoothY = rawStates[0].yRaw
  let smoothZ = rawStates[0].zRaw
  let smoothComposite = rawStates[0].compositeRaw
  let activeDecision = 'HOLD'
  let pendingDecision = 'HOLD'
  let pendingCount = 0
  let activeSinceIndex = -1

    return rawStates.map((state, index) => {
    if (index > 0) {
      const alpha = decisionSettings.smoothingAlpha
      smoothX = (alpha * state.xRaw) + ((1 - alpha) * smoothX)
      smoothY = (alpha * state.yRaw) + ((1 - alpha) * smoothY)
      smoothZ = (alpha * state.zRaw) + ((1 - alpha) * smoothZ)
      smoothComposite = (alpha * state.compositeRaw) + ((1 - alpha) * smoothComposite)
    }

    const x = clamp(smoothX, -100, 100)
    const y = clamp(smoothY, -100, 100)
    const z = clamp(smoothZ, -100, 100)
    const composite = clamp(smoothComposite, -100, 100)
    const magnitude = clamp((Math.sqrt((x ** 2) + (y ** 2) + (z ** 2)) / Math.sqrt(3)), 0, 100)
    const confidence = clamp((Math.abs(composite) * 0.65) + (magnitude * 0.35), 0, 100)

    let biasDecision = 'HOLD'
    if (
      confidence >= decisionSettings.enterConfidence
      && composite >= decisionSettings.enterComposite
      && y >= 12
      && x > -8
    ) {
      biasDecision = 'LONG'
    } else if (
      confidence >= decisionSettings.enterConfidence
      && composite <= -decisionSettings.enterComposite
      && y <= -12
      && x < 8
    ) {
      biasDecision = 'SHORT'
    } else if (activeDecision === 'LONG' && composite >= decisionSettings.exitComposite && y >= -6) {
      biasDecision = 'LONG'
    } else if (activeDecision === 'SHORT' && composite <= -decisionSettings.exitComposite && y <= 6) {
      biasDecision = 'SHORT'
    }

    const reversing = activeDecision !== 'HOLD' && biasDecision !== 'HOLD' && biasDecision !== activeDecision
    if (
      reversing
      && (Math.abs(composite) < decisionSettings.reverseComposite || confidence < decisionSettings.reverseConfidence)
    ) {
      biasDecision = activeDecision
    }

    if (biasDecision === activeDecision) {
      pendingDecision = 'HOLD'
      pendingCount = 0
    } else {
      if (pendingDecision === biasDecision) pendingCount += 1
      else {
        pendingDecision = biasDecision
        pendingCount = 1
      }

      const persistenceNeeded = biasDecision === 'HOLD'
        ? 2
        : decisionSettings.persistenceBars

      if (pendingCount >= persistenceNeeded) {
        activeDecision = biasDecision
        activeSinceIndex = activeDecision === 'HOLD' ? -1 : index
        pendingDecision = 'HOLD'
        pendingCount = 0
      }
    }

    const suggestedTrade = activeDecision === 'LONG'
      ? state.preferredCall
      : activeDecision === 'SHORT'
        ? state.preferredPut
        : null
    const stabilityBars = activeDecision === 'HOLD' || activeSinceIndex < 0
      ? 0
      : (index - activeSinceIndex) + 1

    return {
      key: state.key,
      time: state.time,
      date: state.date,
      minuteKey: state.minuteKey,
      spot: state.spot,
      x,
      y,
      z,
      composite,
      magnitude,
      confidence,
      biasDecision,
      decision: activeDecision,
      stabilityBars,
      suggestedTrade,
      preferredCall: state.preferredCall,
      preferredPut: state.preferredPut,
      settings: decisionSettings,
      xRaw: state.xRaw,
      yRaw: state.yRaw,
      zRaw: state.zRaw,
      compositeRaw: state.compositeRaw,
    }
  })
}

const parseOptionalNumber = (value) => {
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

const formatOptionalNumber = (value, digits = 2) => (
  Number.isFinite(value) ? Number(value).toFixed(digits) : '-'
)

const parseCsvLine = (line) => {
  const values = []
  let current = ''
  let inQuotes = false
  for (const char of line) {
    if (char === '"') inQuotes = !inQuotes
    else if (char === ',' && !inQuotes) { values.push(current.trim()); current = '' }
    else current += char
  }
  values.push(current.trim())
  return values
}

const normalizeOptionSide = normalizeOptionSideValue

const normalizeIndexName = normalizeIndexKey

const getOptionMidPrice = (row) => {
  const bid = Number(row?.bid)
  const ask = Number(row?.ask)
  const entry = Number(row?.entry)
  if (Number.isFinite(bid) && Number.isFinite(ask) && bid > 0 && ask > 0) return (bid + ask) / 2
  if (Number.isFinite(entry) && entry > 0) return entry
  if (Number.isFinite(ask) && ask > 0) return ask
  if (Number.isFinite(bid) && bid > 0) return bid
  return null
}

const resolveStrikeLadderLeg = (row, strikeStep) => {
  const side = normalizeOptionSide(row?.side)
  const spot = Number(row?.spot)
  const strike = Number(row?.strike)
  if (!side || !Number.isFinite(spot) || !Number.isFinite(strike) || !Number.isFinite(strikeStep) || strikeStep <= 0) return null

  const atm = Math.round(spot / strikeStep) * strikeStep
  const rawOffset = side === 'CE' ? (strike - atm) / strikeStep : (atm - strike) / strikeStep
  const offset = Math.round(rawOffset)
  if (!Number.isFinite(offset) || Math.abs(rawOffset - offset) > 0.001 || offset < 0 || offset > 4) return null

  return STRIKE_LADDER_DEFS.find((definition) => definition.side === side && definition.offset === offset) || null
}

const calculateCorrelation = (xs, ys) => {
  if (!Array.isArray(xs) || !Array.isArray(ys) || xs.length !== ys.length || xs.length < 2) return null
  const n = xs.length
  const meanX = xs.reduce((sum, v) => sum + v, 0) / n
  const meanY = ys.reduce((sum, v) => sum + v, 0) / n

  let num = 0
  let denX = 0
  let denY = 0
  for (let i = 0; i < n; i++) {
    const dx = xs[i] - meanX
    const dy = ys[i] - meanY
    num += dx * dy
    denX += dx * dx
    denY += dy * dy
  }

  const den = Math.sqrt(denX * denY)
  if (!Number.isFinite(den) || den === 0) return null
  return num / den
}

const getPointClose = (point) => (
  Number.isFinite(point?.close)
    ? point.close
    : Number.isFinite(point?.spot)
      ? point.spot
      : 0
)

const getPointHigh = (point) => (
  Number.isFinite(point?.high)
    ? point.high
    : getPointClose(point)
)

const getPointLow = (point) => (
  Number.isFinite(point?.low)
    ? point.low
    : getPointClose(point)
)

// Configuration for swing confirmation to prevent jumping markers
const SWING_CONFIRMATION_BARS = 4  // Minimum bars after pivot to confirm a swing

// Find swing points with expert analysis
// Swing points are LOCKED once confirmed - they don't change as new candles arrive
const findSwingPointsExpert = (data, minMove = 0.002) => {
  if (data.length < 5) return { swingHighs: [], swingLows: [], pendingSwing: null }

  const swingHighs = []
  const swingLows = []
  const closes = data.map(getPointClose)
  const highs = data.map(getPointHigh)
  const lows = data.map(getPointLow)
  const avgPriceRaw = closes.reduce((a, b) => a + b, 0) / closes.length
  const avgPrice = Number.isFinite(avgPriceRaw) && avgPriceRaw > 0 ? avgPriceRaw : 1
  const stats = calculateStats(data)
  const dataLength = data.length

  let direction = null
  let lastPivotIdx = 0
  let lastPivotPrice = closes[0]

  for (let i = 1; i < data.length; i++) {
    const currentClose = closes[i]
    const currentHigh = highs[i]
    const currentLow = lows[i]

    if (direction === null) {
      if (currentClose === lastPivotPrice) continue
      direction = currentClose > lastPivotPrice ? 'up' : 'down'
      if (direction === 'up' && currentHigh > lastPivotPrice) {
        lastPivotPrice = currentHigh
        lastPivotIdx = i
      }
      if (direction === 'down' && currentLow < lastPivotPrice) {
        lastPivotPrice = currentLow
        lastPivotIdx = i
      }
    } else if (direction === 'up') {
      if (currentHigh >= lastPivotPrice) {
        lastPivotPrice = currentHigh
        lastPivotIdx = i
      }
      const pullback = (currentClose - lastPivotPrice) / avgPrice
      if (currentClose < lastPivotPrice && Math.abs(pullback) >= minMove) {
        // Confirmation check: only add if enough bars have passed since the pivot
        const barsSincePivot = i - lastPivotIdx
        if (barsSincePivot >= SWING_CONFIRMATION_BARS) {
          const analysis = analyzeSwingPoint(data[lastPivotIdx], 'high', stats)
          swingHighs.push({ ...data[lastPivotIdx], spot: lastPivotPrice, index: lastPivotIdx, type: 'high', confirmed: true, ...analysis })
        }
        direction = 'down'
        lastPivotPrice = currentLow
        lastPivotIdx = i
      }
    } else {
      if (currentLow <= lastPivotPrice) {
        lastPivotPrice = currentLow
        lastPivotIdx = i
      }
      const rebound = (currentClose - lastPivotPrice) / avgPrice
      if (currentClose > lastPivotPrice && Math.abs(rebound) >= minMove) {
        // Confirmation check: only add if enough bars have passed since the pivot
        const barsSincePivot = i - lastPivotIdx
        if (barsSincePivot >= SWING_CONFIRMATION_BARS) {
          const analysis = analyzeSwingPoint(data[lastPivotIdx], 'low', stats)
          swingLows.push({ ...data[lastPivotIdx], spot: lastPivotPrice, index: lastPivotIdx, type: 'low', confirmed: true, ...analysis })
        }
        direction = 'up'
        lastPivotPrice = currentHigh
        lastPivotIdx = i
      }
    }
  }

  // Track the pending (unconfirmed) swing separately - this one CAN move
  // but it's clearly marked as pending and won't be used for entry decisions
  let pendingSwing = null
  if (lastPivotIdx > 0 && direction) {
    const barsSincePivot = dataLength - 1 - lastPivotIdx
    const swingType = direction === 'up' ? 'high' : 'low'
    const analysis = analyzeSwingPoint(data[lastPivotIdx], swingType, stats)

    // Check if the swing can be confirmed
    if (barsSincePivot >= SWING_CONFIRMATION_BARS) {
      // Enough bars have passed - this swing is confirmed
      const arr = direction === 'up' ? swingHighs : swingLows
      arr.push({
        ...data[lastPivotIdx],
        spot: lastPivotPrice,
        index: lastPivotIdx,
        type: swingType,
        confirmed: true,
        ...analysis,
      })
    } else {
      // Not enough bars - mark as pending (this one may still move)
      pendingSwing = {
        ...data[lastPivotIdx],
        spot: lastPivotPrice,
        index: lastPivotIdx,
        type: swingType,
        confirmed: false,
        barsUntilConfirm: SWING_CONFIRMATION_BARS - barsSincePivot,
        ...analysis,
      }
    }
  }

  const maxIdx = highs.indexOf(Math.max(...highs))
  const minIdx = lows.indexOf(Math.min(...lows))

  // Only mark absolute high/low if they're confirmed (not too recent)
  const maxIsConfirmable = (dataLength - 1 - maxIdx) >= SWING_CONFIRMATION_BARS
  const minIsConfirmable = (dataLength - 1 - minIdx) >= SWING_CONFIRMATION_BARS

  const existingHigh = swingHighs.find(h => h.index === maxIdx)
  if (existingHigh) {
    existingHigh.isAbsoluteHigh = true
    existingHigh.spot = highs[maxIdx]
  } else if (maxIsConfirmable) {
    const analysis = analyzeSwingPoint(data[maxIdx], 'high', stats)
    swingHighs.push({ ...data[maxIdx], spot: highs[maxIdx], index: maxIdx, type: 'high', isAbsoluteHigh: true, confirmed: true, ...analysis })
  }

  const existingLow = swingLows.find(l => l.index === minIdx)
  if (existingLow) {
    existingLow.isAbsoluteLow = true
    existingLow.spot = lows[minIdx]
  } else if (minIsConfirmable) {
    const analysis = analyzeSwingPoint(data[minIdx], 'low', stats)
    swingLows.push({ ...data[minIdx], spot: lows[minIdx], index: minIdx, type: 'low', isAbsoluteLow: true, confirmed: true, ...analysis })
  }

  swingHighs.sort((a, b) => a.index - b.index)
  swingLows.sort((a, b) => a.index - b.index)

  return { swingHighs, swingLows, pendingSwing }
}

const calculateStats = (data) => {
  const get = (arr) => arr.filter(v => v !== null && !isNaN(v))
  const avg = (arr) => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0

  const volumes = get(data.map(d => d.volume))
  const vixValues = get(data.map(d => d.vix))
  const oiChanges = get(data.map(d => Math.abs(d.oich || 0)))

  return {
    avgVolume: avg(volumes),
    avgVix: avg(vixValues),
    avgOiChange: avg(oiChanges),
    maxVolume: Math.max(...volumes, 1),
    maxVix: Math.max(...vixValues, 1),
  }
}

const gradeFromScore = (score) => {
  if (score >= 80) return 'A+'
  if (score >= 60) return 'A'
  if (score >= 45) return 'B+'
  if (score >= 35) return 'B'
  if (score >= 25) return 'C+'
  return 'C'
}

const analyzeSwingPoint = (point, type, stats) => {
  let score = 0
  const signals = []
  const details = {}

  if (point.net_pcr !== null && point.net_pcr !== undefined) {
    details.pcr = point.net_pcr
    if (type === 'low' && point.net_pcr > 1.3) {
      score += SIGNAL_WEIGHTS.pcrExtreme
      signals.push(`PCR ${point.net_pcr.toFixed(2)} (fear)`)
    } else if (type === 'high' && point.net_pcr < 0.75) {
      score += SIGNAL_WEIGHTS.pcrExtreme
      signals.push(`PCR ${point.net_pcr.toFixed(2)} (greed)`)
    }
  }

  if (point.vix && stats.avgVix > 0) {
    details.vix = point.vix
    const vixRatio = point.vix / stats.avgVix
    if (type === 'low' && vixRatio > 1.15) {
      score += SIGNAL_WEIGHTS.vixSpike
      signals.push(`VIX ${point.vix.toFixed(1)} spike`)
    } else if (type === 'high' && vixRatio < 0.9) {
      score += SIGNAL_WEIGHTS.vixSpike * 0.8
      signals.push(`VIX ${point.vix.toFixed(1)} calm`)
    }
  }

  if (point.volume && stats.avgVolume > 0) {
    details.volume = point.volume
    const volRatio = point.volume / stats.avgVolume
    if (volRatio > 2.0) {
      score += SIGNAL_WEIGHTS.volumeSpike
      signals.push(`Vol ${volRatio.toFixed(1)}x`)
    } else if (volRatio > 1.5) {
      score += SIGNAL_WEIGHTS.volumeSpike * 0.6
      signals.push(`Vol ${volRatio.toFixed(1)}x`)
    }
  }

  if (point.oich !== null && point.oich !== undefined) {
    details.oiChange = point.oich
    const absOiChange = Math.abs(point.oich)
    if (absOiChange > 3000) {
      score += SIGNAL_WEIGHTS.oiChange
      signals.push(`OI ${point.oich > 0 ? '+' : ''}${(point.oich / 1000).toFixed(1)}K`)
    } else if (absOiChange > 1500) {
      score += SIGNAL_WEIGHTS.oiChange * 0.6
      signals.push(`OI ${point.oich > 0 ? '+' : ''}${(point.oich / 1000).toFixed(1)}K`)
    }
  }

  if (point.max_pain_dist !== null && point.max_pain_dist !== undefined) {
    details.maxPainDist = point.max_pain_dist
    const dist = Math.abs(point.max_pain_dist)
    if (dist > 500) {
      score += SIGNAL_WEIGHTS.maxPainDist
      signals.push(`MP ${dist.toFixed(0)}`)
    }
  }

  if (point.fut_basis_pct !== null && point.fut_basis_pct !== undefined) {
    details.futBasis = point.fut_basis_pct
    if (type === 'low' && point.fut_basis_pct < -0.1) {
      score += SIGNAL_WEIGHTS.futBasis
      signals.push(`Fut ${point.fut_basis_pct.toFixed(2)}%`)
    } else if (type === 'high' && point.fut_basis_pct > 0.4) {
      score += SIGNAL_WEIGHTS.futBasis
      signals.push(`Fut +${point.fut_basis_pct.toFixed(2)}%`)
    }
  }

  if (point.delta !== null && point.delta !== undefined) {
    details.delta = point.delta
    if (type === 'low' && point.delta < -0.4) {
      score += SIGNAL_WEIGHTS.deltaExtreme
      signals.push(`Δ ${point.delta.toFixed(2)}`)
    } else if (type === 'high' && point.delta > 0.4) {
      score += SIGNAL_WEIGHTS.deltaExtreme
      signals.push(`Δ +${point.delta.toFixed(2)}`)
    }
  }

  if (point.vote_side) {
    details.voteSide = point.vote_side
    if ((type === 'low' && point.vote_side === 'PE') || (type === 'high' && point.vote_side === 'CE')) {
      score += SIGNAL_WEIGHTS.voteContrarian
      signals.push(`Vote ${point.vote_side}`)
    }
  }

  if (point.confidence && point.confidence >= 85) {
    details.confidence = point.confidence
    score += SIGNAL_WEIGHTS.confidenceHigh
    signals.push(`Conf ${point.confidence}%`)
  }

  if (point.stable === 'Y') { details.stable = true; score += SIGNAL_WEIGHTS.stableSignal }
  if (point.entry_ready === 'Y') { details.entryReady = true; score += SIGNAL_WEIGHTS.entryReady }
  if (point.flow_match === 'Y') { details.flowMatch = true; score += SIGNAL_WEIGHTS.flowMatch }
  if (point.iv && point.iv > 30) { details.iv = point.iv; score += SIGNAL_WEIGHTS.ivHigh }
  if (point.vol_oi_ratio && point.vol_oi_ratio > 0.4) { details.volOiRatio = point.vol_oi_ratio; score += SIGNAL_WEIGHTS.volOiRatio }

  if (point.outcome) {
    details.outcome = point.outcome
    if (point.outcome.toUpperCase() === 'WIN') score += SIGNAL_WEIGHTS.outcomeWin
  }

  const grade = gradeFromScore(score)

  return { score, signals, details, grade }
}

const detectPatterns = (swingHighs, swingLows) => {
  const patterns = []
  const allSwings = [...swingHighs, ...swingLows].sort((a, b) => a.index - b.index)
  if (allSwings.length < 3) return patterns

  for (let i = 0; i < allSwings.length - 2; i++) {
    const [s1, s2, s3] = [allSwings[i], allSwings[i + 1], allSwings[i + 2]]

    if (s1.type === 'low' && s2.type === 'high' && s3.type === 'low') {
      const pct = ((s3.spot - s1.spot) / s1.spot * 100).toFixed(2)
      patterns.push({
        type: s3.spot > s1.spot ? 'Higher Low' : 'Lower Low',
        description: `${s1.spot.toFixed(0)} → ${s3.spot.toFixed(0)} (${pct}%)`,
        bullish: s3.spot > s1.spot,
        avgScore: ((s1.score || 0) + (s3.score || 0)) / 2,
      })
    }
    if (s1.type === 'high' && s2.type === 'low' && s3.type === 'high') {
      const pct = ((s3.spot - s1.spot) / s1.spot * 100).toFixed(2)
      patterns.push({
        type: s3.spot > s1.spot ? 'Higher High' : 'Lower High',
        description: `${s1.spot.toFixed(0)} → ${s3.spot.toFixed(0)} (${pct}%)`,
        bullish: s3.spot > s1.spot,
        avgScore: ((s1.score || 0) + (s3.score || 0)) / 2,
      })
    }
  }

  if (allSwings.length >= 4) {
    const first = allSwings.slice(0, 2).reduce((a, b) => a + b.spot, 0) / 2
    const last = allSwings.slice(-2).reduce((a, b) => a + b.spot, 0) / 2
    const pct = ((last - first) / first * 100).toFixed(2)
    patterns.unshift({
      type: 'Day Trend',
      description: last > first ? `Bullish +${pct}%` : `Bearish ${pct}%`,
      bullish: last > first,
      isOverall: true,
    })
  }

  return patterns
}

const parseCSV = (text) => {
  const lines = text.trim().split('\n')
  const headers = lines[0].split(',').map(h => h.trim().toLowerCase())

  const colMap = {}
  const colNames = [
    'date', 'time', 'symbol', 'spot', 'vix', 'total_ce_oi', 'total_pe_oi', 'net_pcr',
    'max_pain', 'max_pain_dist', 'fut_ltp', 'fut_basis', 'fut_basis_pct',
    'volume', 'oi', 'oich', 'vol_oi_ratio', 'iv', 'delta', 'gamma', 'theta_day',
    'vote_ce', 'vote_pe', 'vote_side', 'vote_diff', 'confidence', 'score',
    'stable', 'entry_ready', 'spread_pct', 'flow_match',
    'fvg_side', 'fvg_active', 'fvg_gap', 'fvg_distance', 'fvg_distance_atr', 'fvg_plus',
    'learn_prob', 'outcome', 'reason'
  ]
  colNames.forEach(n => { colMap[n] = headers.indexOf(n) })

  if (colMap.spot === -1) throw new Error('CSV must have "spot" column')

  const data = []
  const seen = new Set()

  for (let i = 1; i < lines.length; i++) {
    const values = parseCsvLine(lines[i])

    const get = (col) => col >= 0 && values[col] ? values[col] : null
    const num = (col) => { const v = get(col); return v ? parseFloat(v) : null }

    const spot = num(colMap.spot)
    if (!spot || isNaN(spot)) continue

    const date = get(colMap.date) || ''
    const time = get(colMap.time) || ''
    const key = `${date}-${time}`
    if (seen.has(key)) continue
    seen.add(key)

    data.push({
      date, time, datetime: `${date} ${time}`, spot,
      symbol: get(colMap.symbol),
      vix: num(colMap.vix),
      net_pcr: num(colMap.net_pcr),
      max_pain: num(colMap.max_pain),
      max_pain_dist: num(colMap.max_pain_dist),
      fut_basis_pct: num(colMap.fut_basis_pct),
      volume: num(colMap.volume),
      oi: num(colMap.oi),
      oich: num(colMap.oich),
      vol_oi_ratio: num(colMap.vol_oi_ratio),
      iv: num(colMap.iv),
      delta: num(colMap.delta),
      gamma: num(colMap.gamma),
      vote_side: get(colMap.vote_side),
      vote_diff: num(colMap.vote_diff),
      confidence: num(colMap.confidence),
      stable: get(colMap.stable),
      entry_ready: get(colMap.entry_ready),
      spread_pct: num(colMap.spread_pct),
      flow_match: get(colMap.flow_match),
      fvg_side: get(colMap.fvg_side),
      fvg_active: get(colMap.fvg_active),
      fvg_gap: num(colMap.fvg_gap),
      fvg_distance: num(colMap.fvg_distance),
      fvg_distance_atr: num(colMap.fvg_distance_atr),
      fvg_plus: get(colMap.fvg_plus),
      outcome: get(colMap.outcome),
    })
  }
  return data
}

const parseOptionRowsCSV = (text) => {
  const lines = text.trim().split('\n')
  const headers = lines[0].split(',').map(h => h.trim().toLowerCase())

  const colMap = {}
  const colNames = [
    'date', 'time', 'symbol', 'spot', 'side', 'strike', 'entry',
    'bid', 'ask', 'volume', 'oi', 'oich', 'iv', 'delta', 'gamma', 'theta_day',
    'fut_symbol', 'contract_symbol', 'option_symbol', 'option_expiry', 'option_expiry_code', 'expiry', 'expiry_date',
  ]
  colNames.forEach((name) => { colMap[name] = headers.indexOf(name) })

  if (colMap.spot === -1 || colMap.side === -1 || colMap.strike === -1) return []

  const getValue = (values, col) => (col >= 0 && values[col] ? values[col] : null)
  const getNumber = (values, col) => {
    const raw = getValue(values, col)
    const n = Number(raw)
    return Number.isFinite(n) ? n : null
  }

  const rows = []
  for (let i = 1; i < lines.length; i++) {
    const values = parseCsvLine(lines[i])
    const spot = getNumber(values, colMap.spot)
    const side = normalizeOptionSide(getValue(values, colMap.side))
    const strike = getNumber(values, colMap.strike)

    if (!Number.isFinite(spot) || !side || !Number.isFinite(strike)) continue

    const date = getValue(values, colMap.date) || ''
    const time = getValue(values, colMap.time) || ''
    rows.push(enrichOptionRow({
      date,
      time,
      datetime: `${date} ${time}`.trim(),
      symbol: getValue(values, colMap.symbol) || '',
      spot,
      side,
      strike,
      entry: getNumber(values, colMap.entry),
      bid: getNumber(values, colMap.bid),
      ask: getNumber(values, colMap.ask),
      volume: getNumber(values, colMap.volume),
      oi: getNumber(values, colMap.oi),
      oich: getNumber(values, colMap.oich),
      iv: getNumber(values, colMap.iv),
      delta: getNumber(values, colMap.delta),
      gamma: getNumber(values, colMap.gamma),
      theta_day: getNumber(values, colMap.theta_day),
      fut_symbol: getValue(values, colMap.fut_symbol) || '',
      contract_symbol: getValue(values, colMap.contract_symbol) || getValue(values, colMap.option_symbol) || '',
      option_expiry: getValue(values, colMap.option_expiry) || getValue(values, colMap.expiry) || getValue(values, colMap.expiry_date) || '',
      option_expiry_code: getValue(values, colMap.option_expiry_code) || '',
    }))
  }

  return rows
}

// Parse OHLCV CSV (candlestick data)
const parseOHLCV = (text) => {
  const lines = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim().split('\n')
  const headers = lines[0].split(',').map(h => h.trim().toLowerCase().replace(/[^a-z0-9_]/g, ''))

  const colMap = {}
  const colNames = ['timestamp_epoch', 'timestamp_ist', 'date', 'time', 'open', 'high', 'low', 'close', 'volume']
  colNames.forEach(n => { colMap[n] = headers.indexOf(n) })

  // Check for required columns
  if (colMap.open === -1 || colMap.high === -1 || colMap.low === -1 || colMap.close === -1) {
    throw new Error(`OHLCV CSV must have open, high, low, close columns. Found: [${headers.join(', ')}]`)
  }

  const data = []
  for (let i = 1; i < lines.length; i++) {
    const values = lines[i].split(',').map(v => v.trim())
    const get = (col) => col >= 0 && values[col] ? values[col] : null
    const num = (col) => { const v = get(col); return v ? parseFloat(v) : null }

    const open = num(colMap.open)
    const high = num(colMap.high)
    const low = num(colMap.low)
    const close = num(colMap.close)

    if (![open, high, low, close].every(Number.isFinite)) continue

    data.push({
      time: get(colMap.time) || '',
      date: get(colMap.date) || '',
      datetime: `${get(colMap.date) || ''} ${get(colMap.time) || ''}`.trim(),
      open, high, low, close,
      volume: num(colMap.volume),
    })
  }
  return data
}

const normalizeLiveSignalRows = (rows) => {
  const data = []
  const seen = new Set()

  rows.forEach((row) => {
    const spot = Number(row.spot)
    if (!Number.isFinite(spot) || spot <= 0) return

    const date = row.date || ''
    const time = row.time || ''
    const key = `${date}-${time}`
    if (seen.has(key)) return
    seen.add(key)

    data.push({
      date,
      time,
      datetime: `${date} ${time}`.trim(),
      spot,
      symbol: row.symbol || '',
      vix: Number.isFinite(Number(row.vix)) ? Number(row.vix) : null,
      net_pcr: Number.isFinite(Number(row.net_pcr)) ? Number(row.net_pcr) : null,
      max_pain: Number.isFinite(Number(row.max_pain)) ? Number(row.max_pain) : null,
      max_pain_dist: Number.isFinite(Number(row.max_pain_dist)) ? Number(row.max_pain_dist) : null,
      fut_basis_pct: Number.isFinite(Number(row.fut_basis_pct)) ? Number(row.fut_basis_pct) : null,
      volume: Number.isFinite(Number(row.volume)) ? Number(row.volume) : null,
      oi: Number.isFinite(Number(row.oi)) ? Number(row.oi) : null,
      oich: Number.isFinite(Number(row.oich)) ? Number(row.oich) : null,
      vol_oi_ratio: Number.isFinite(Number(row.vol_oi_ratio)) ? Number(row.vol_oi_ratio) : null,
      iv: Number.isFinite(Number(row.iv)) ? Number(row.iv) : null,
      delta: Number.isFinite(Number(row.delta)) ? Number(row.delta) : null,
      gamma: Number.isFinite(Number(row.gamma)) ? Number(row.gamma) : null,
      vote_side: row.vote_side || '',
      vote_diff: Number.isFinite(Number(row.vote_diff)) ? Number(row.vote_diff) : null,
      confidence: Number.isFinite(Number(row.confidence)) ? Number(row.confidence) : null,
      stable: row.stable || '',
      entry_ready: row.entry_ready ? 'Y' : 'N',
      spread_pct: Number.isFinite(Number(row.spread_pct)) ? Number(row.spread_pct) : null,
      flow_match: row.flow_match || '',
      fvg_side: row.fvg_side || '',
      fvg_active: row.fvg_active || '',
      fvg_gap: Number.isFinite(Number(row.fvg_gap)) ? Number(row.fvg_gap) : null,
      fvg_distance: Number.isFinite(Number(row.fvg_distance)) ? Number(row.fvg_distance) : null,
      fvg_distance_atr: Number.isFinite(Number(row.fvg_distance_atr)) ? Number(row.fvg_distance_atr) : null,
      fvg_plus: row.fvg_plus === true || row.fvg_plus === 'Y' ? 'Y' : 'N',
      outcome: row.outcome || '',
    })
  })

  return data
}

const normalizeLiveOptionRows = (rows) => {
  const data = []

  rows.forEach((row) => {
    const spot = Number(row.spot)
    const side = normalizeOptionSide(row.side)
    const strike = Number(row.strike)
    if (!Number.isFinite(spot) || !side || !Number.isFinite(strike)) return

    const date = row.date || ''
    const time = row.time || ''
    data.push(enrichOptionRow({
      date,
      time,
      datetime: `${date} ${time}`.trim(),
      symbol: row.symbol || '',
      spot,
      side,
      strike,
      entry: Number.isFinite(Number(row.entry)) ? Number(row.entry) : null,
      bid: Number.isFinite(Number(row.bid)) ? Number(row.bid) : null,
      ask: Number.isFinite(Number(row.ask)) ? Number(row.ask) : null,
      volume: Number.isFinite(Number(row.volume)) ? Number(row.volume) : null,
      oi: Number.isFinite(Number(row.oi)) ? Number(row.oi) : null,
      oich: Number.isFinite(Number(row.oich)) ? Number(row.oich) : null,
      iv: Number.isFinite(Number(row.iv)) ? Number(row.iv) : null,
      delta: Number.isFinite(Number(row.delta)) ? Number(row.delta) : null,
      gamma: Number.isFinite(Number(row.gamma)) ? Number(row.gamma) : null,
      theta_day: Number.isFinite(Number(row.theta_day)) ? Number(row.theta_day) : null,
      fut_symbol: row.fut_symbol || '',
      contract_symbol: row.contract_symbol || row.option_symbol || '',
      option_expiry: row.option_expiry || row.expiry || row.expiry_date || '',
      option_expiry_code: row.option_expiry_code || '',
    }))
  })

  return data
}

const deriveOhlcvFromSignals = (rows) => {
  const buckets = new Map()

  rows.forEach((row) => {
    if (!Number.isFinite(row.spot)) return

    const { date, minute } = getDateAndMinute(row)
    const key = toMinuteKey(row)
    if (!key || !minute) return

    const existing = buckets.get(key)
    if (!existing) {
      const datetime = date ? `${date} ${minute}` : minute
      buckets.set(key, {
        date,
        time: minute,
        datetime,
        open: row.spot,
        high: row.spot,
        low: row.spot,
        close: row.spot,
        volume: Number.isFinite(row.volume) ? row.volume : null,
      })
      return
    }

    existing.high = Math.max(existing.high, row.spot)
    existing.low = Math.min(existing.low, row.spot)
    existing.close = row.spot
    if (Number.isFinite(row.volume)) {
      existing.volume = (existing.volume || 0) + row.volume
    }
  })

  return Array.from(buckets.values()).sort((a, b) => a.datetime.localeCompare(b.datetime))
}

const buildOptionLadderCandles = (rows, strikeStep) => {
  if (!Array.isArray(rows) || rows.length === 0 || !Number.isFinite(strikeStep) || strikeStep <= 0) {
    return {}
  }

  const byLegAndMinute = new Map()

  rows.forEach((row) => {
    const leg = resolveStrikeLadderLeg(row, strikeStep)
    if (!leg) return

    const price = getOptionMidPrice(row)
    if (!Number.isFinite(price) || price <= 0) return

    const { date, minute } = getDateAndMinute(row)
    const minuteKey = (date && minute) ? `${date} ${minute}` : minute
    if (!minuteKey) return

    const legMap = byLegAndMinute.get(leg.id) || new Map()
    const existing = legMap.get(minuteKey)
    const rowStrike = Number(row.strike)
    if (!existing) {
      legMap.set(minuteKey, {
        datetime: minuteKey,
        open: price,
        high: price,
        low: price,
        close: price,
        strike: Number.isFinite(rowStrike) ? rowStrike : null,
        side: leg.side,
      })
    } else {
      existing.high = Math.max(existing.high, price)
      existing.low = Math.min(existing.low, price)
      existing.close = price
      if (Number.isFinite(rowStrike)) existing.strike = rowStrike
    }
    byLegAndMinute.set(leg.id, legMap)
  })

  const result = {}
  STRIKE_LADDER_DEFS.forEach((definition) => {
    const map = byLegAndMinute.get(definition.id)
    if (!map) {
      result[definition.id] = []
      return
    }
    result[definition.id] = Array.from(map.values()).sort((a, b) => a.datetime.localeCompare(b.datetime))
  })
  return result
}

const buildHeikinAshiCandles = (candles) => {
  if (!candles.length) return []

  let previousHaOpen = (candles[0].open + candles[0].close) / 2
  let previousHaClose = (candles[0].open + candles[0].high + candles[0].low + candles[0].close) / 4

  return candles.map((candle, index) => {
    const haClose = (candle.open + candle.high + candle.low + candle.close) / 4
    const haOpen = index === 0
      ? previousHaOpen
      : (previousHaOpen + previousHaClose) / 2
    const haHigh = Math.max(candle.high, haOpen, haClose)
    const haLow = Math.min(candle.low, haOpen, haClose)

    previousHaOpen = haOpen
    previousHaClose = haClose

    return {
      ...candle,
      open: haOpen,
      high: haHigh,
      low: haLow,
      close: haClose,
    }
  })
}

// Round time to nearest minute for matching signal times to candle times
const roundToMinute = (timeStr) => {
  if (!timeStr) return timeStr
  const parts = timeStr.split(':')
  if (parts.length >= 2) return `${parts[0]}:${parts[1]}:00`
  return timeStr
}

const getDateAndMinute = (point) => {
  const rawDate = (point?.date || '').trim()
  const rawTime = (point?.time || '').trim()
  const rawDatetime = (point?.datetime || '').trim()

  let date = rawDate
  let time = rawTime

  if (rawDatetime && (!date || !time)) {
    const match = rawDatetime.match(/(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)/)
    if (!date && match?.[1]) date = match[1]
    if (!time && match?.[2]) time = match[2]
  }

  return {
    date,
    minute: roundToMinute(time),
  }
}

const toMinuteKey = (point) => {
  const { date, minute } = getDateAndMinute(point)
  if (date && minute) return `${date} ${minute}`
  return minute || ''
}

const toPlotTimestamp = (point) => {
  const datetime = String(point?.datetime || '').trim()
  if (datetime) return datetime

  const date = String(point?.date || '').trim()
  const time = String(point?.time || '').trim()
  if (date && time) return `${date} ${time}`

  return time || datetime
}

const aggregateOptionSnapshotsByMinute = (snapshots = []) => {
  if (!Array.isArray(snapshots) || snapshots.length === 0) return []

  const buckets = new Map()
  snapshots.forEach((snapshot) => {
    const minuteKey = toMinuteKey(snapshot) || snapshot.key
    if (!minuteKey) return

    const { date, minute } = getDateAndMinute(snapshot)
    let bucket = buckets.get(minuteKey)
    if (!bucket) {
      bucket = {
        key: minuteKey,
        date: date || '',
        time: minute || snapshot.time || '',
        spot: null,
        legs: { CE: new Map(), PE: new Map() },
        sourceCount: 0,
      }
      buckets.set(minuteKey, bucket)
    }

    bucket.sourceCount += 1
    if (Number.isFinite(Number(snapshot.spot))) bucket.spot = Number(snapshot.spot)

    ;['CE', 'PE'].forEach((side) => {
      snapshot.legs?.[side]?.forEach((row, strike) => {
        bucket.legs[side].set(strike, row)
      })
    })
  })

  return Array.from(buckets.values()).sort((left, right) => left.key.localeCompare(right.key))
}

const buildSwingDataFromCandles = (candles, signalRows = []) => {
  if (!candles.length) return []

  const signalByMinute = new Map()
  signalRows.forEach((row) => {
    const key = toMinuteKey(row)
    if (key) signalByMinute.set(key, row)
  })

  return candles
    .filter((candle) => [candle.open, candle.high, candle.low, candle.close].every(Number.isFinite))
    .map((candle) => {
      const key = toMinuteKey(candle)
      const signal = key ? signalByMinute.get(key) : null
      return {
        ...(signal || {}),
        date: candle.date || signal?.date || '',
        time: candle.time || signal?.time || '',
        datetime: candle.datetime || `${candle.date || signal?.date || ''} ${candle.time || signal?.time || ''}`.trim(),
        spot: candle.close,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      }
    })
}

const getCandleColor = (candle) => {
  if (!candle || !Number.isFinite(candle.open) || !Number.isFinite(candle.close)) return 'unknown'
  if (candle.close > candle.open) return 'green'
  if (candle.close < candle.open) return 'red'
  return 'doji'
}

const applyEntryCandleConfirmation = (swingHighs, swingLows, candles) => {
  if (!candles.length) {
    return {
      highsAnnotated: swingHighs,
      lowsAnnotated: swingLows,
      confirmedHighs: swingHighs,
      confirmedLows: swingLows,
      stats: {
        enabled: false,
        total: swingHighs.length + swingLows.length,
        eligible: 0,
        confirmed: 0,
        confirmedPct: 0,
      },
    }
  }

  const candleIndexByMinute = new Map()
  candles.forEach((candle, idx) => {
    const key = toMinuteKey(candle)
    if (key) candleIndexByMinute.set(key, idx)
  })

  const annotate = (swing, expectedColor) => {
    const swingKey = toMinuteKey(swing)
    let idx = swingKey ? candleIndexByMinute.get(swingKey) : undefined
    if (!Number.isInteger(idx) && Number.isInteger(swing.index) && swing.index >= 0 && swing.index < candles.length) {
      idx = swing.index
    }

    if (!Number.isInteger(idx) || idx < 0 || idx >= candles.length - 1) {
      return {
        ...swing,
        entryConfirmed: false,
        confirmationEligible: false,
        confirmationReason: 'No next candle',
      }
    }

    const nextCandle = candles[idx + 1]
    const nextColor = getCandleColor(nextCandle)
    const entryConfirmed = nextColor === expectedColor

    return {
      ...swing,
      entryConfirmed,
      confirmationEligible: true,
      confirmationReason: `Next candle ${nextColor}`,
      confirmationCandleTime: nextCandle.time || nextCandle.datetime || '',
    }
  }

  const highsAnnotated = swingHighs.map((swing) => annotate(swing, 'red'))
  const lowsAnnotated = swingLows.map((swing) => annotate(swing, 'green'))
  const confirmedHighs = highsAnnotated.filter((swing) => swing.entryConfirmed)
  const confirmedLows = lowsAnnotated.filter((swing) => swing.entryConfirmed)

  const eligible = highsAnnotated.filter((swing) => swing.confirmationEligible).length
    + lowsAnnotated.filter((swing) => swing.confirmationEligible).length
  const confirmed = confirmedHighs.length + confirmedLows.length
  const confirmedPct = eligible > 0 ? (confirmed / eligible) * 100 : 0

  return {
    highsAnnotated,
    lowsAnnotated,
    confirmedHighs,
    confirmedLows,
    stats: {
      enabled: true,
      total: highsAnnotated.length + lowsAnnotated.length,
      eligible,
      confirmed,
      confirmedPct,
    },
  }
}

const normalizeFvgSide = (value) => {
  const side = String(value || '').trim().toUpperCase()
  if (side === 'BULLISH') return 'BULL'
  if (side === 'BEARISH') return 'BEAR'
  if (side === 'BULL' || side === 'BEAR') return side
  return ''
}

const isFvgAligned = (swingType, fvgSide) => (
  (swingType === 'low' && fvgSide === 'BULL')
  || (swingType === 'high' && fvgSide === 'BEAR')
)

const applyFvgBoost = (swingHighs, swingLows, mode = 'off') => {
  if (mode === 'off') {
    return {
      swingHighs,
      swingLows,
      stats: { mode: 'off', eligible: 0, boosted: 0 },
    }
  }

  let eligible = 0
  let boosted = 0

  const applyBoost = (swing) => {
    const fvgSide = normalizeFvgSide(swing.fvg_side)
    const active = String(swing.fvg_active || '').toUpperCase() === 'Y'
    const plus = String(swing.fvg_plus || '').toUpperCase() === 'Y'
    const distanceAtr = Number(swing.fvg_distance_atr)
    const nearGap = Number.isFinite(distanceAtr) ? distanceAtr <= 0.35 : false

    if (!active || !isFvgAligned(swing.type, fvgSide)) return swing
    eligible += 1

    let bonus = 0
    let tag = ''
    if (mode === 'fvg') {
      bonus = 8
      tag = 'FVG'
    } else if (mode === 'fvg-plus' && plus && nearGap) {
      bonus = 14
      tag = 'FVG+'
    }

    if (bonus <= 0) return swing

    boosted += 1
    const baseScore = Number.isFinite(Number(swing.score)) ? Number(swing.score) : 0
    const nextScore = baseScore + bonus
    return {
      ...swing,
      score: nextScore,
      grade: gradeFromScore(nextScore),
      signals: [...(swing.signals || []), `${tag} +${bonus}`],
    }
  }

  return {
    swingHighs: swingHighs.map(applyBoost),
    swingLows: swingLows.map(applyBoost),
    stats: {
      mode,
      eligible,
      boosted,
    },
  }
}

const LIQUIDITY_SWEEP_LOOKBACK = 20
const LIQUIDITY_SWEEP_STRICT_MIN_BREACH_PCT = 0.02

const isStrictLiquiditySweepEvent = (event) => (
  Number.isFinite(Number(event?.breachPct))
  && Number(event.breachPct) >= LIQUIDITY_SWEEP_STRICT_MIN_BREACH_PCT
)

const detectLiquiditySweepEvents = (candles, lookback = LIQUIDITY_SWEEP_LOOKBACK) => {
  if (!Array.isArray(candles) || candles.length <= lookback) return []

  const events = []
  for (let i = lookback; i < candles.length; i++) {
    const candle = candles[i]
    if (![candle?.open, candle?.high, candle?.low, candle?.close].every(Number.isFinite)) continue

    let priorHigh = Number.NEGATIVE_INFINITY
    let priorLow = Number.POSITIVE_INFINITY
    for (let j = i - lookback; j < i; j++) {
      const prev = candles[j]
      if (Number.isFinite(prev?.high)) priorHigh = Math.max(priorHigh, prev.high)
      if (Number.isFinite(prev?.low)) priorLow = Math.min(priorLow, prev.low)
    }
    if (!Number.isFinite(priorHigh) || !Number.isFinite(priorLow)) continue

    const key = toMinuteKey(candle)
    const time = candle.datetime || `${candle.date || ''} ${candle.time || ''}`.trim()

    if (candle.high > priorHigh && candle.close < priorHigh) {
      const breach = candle.high - priorHigh
      const breachPct = priorHigh > 0 ? (breach / priorHigh) * 100 : 0
      events.push({
        type: 'high',
        index: i,
        key,
        time,
        level: priorHigh,
        breach,
        breachPct,
      })
    }

    if (candle.low < priorLow && candle.close > priorLow) {
      const breach = priorLow - candle.low
      const breachPct = priorLow > 0 ? (breach / priorLow) * 100 : 0
      events.push({
        type: 'low',
        index: i,
        key,
        time,
        level: priorLow,
        breach,
        breachPct,
      })
    }
  }

  return events
}

const applyLiquiditySweepBoost = (swingHighs, swingLows, sweepEvents = [], mode = 'off') => {
  if (mode === 'off') {
    return {
      swingHighs,
      swingLows,
      stats: { mode: 'off', detected: sweepEvents.length, qualified: 0, eligible: 0, boosted: 0 },
    }
  }

  const strict = mode === 'strict'
  const qualifiedEvents = strict
    ? sweepEvents.filter(isStrictLiquiditySweepEvent)
    : sweepEvents

  const eventsByKey = new Map()
  const eventsByIndex = new Map()
  qualifiedEvents.forEach((event) => {
    if (!event) return
    if (event.key) {
      const arr = eventsByKey.get(event.key) || []
      arr.push(event)
      eventsByKey.set(event.key, arr)
    }
    if (Number.isInteger(event.index)) {
      const arr = eventsByIndex.get(event.index) || []
      arr.push(event)
      eventsByIndex.set(event.index, arr)
    }
  })

  const bonus = 10
  let eligible = 0
  let boosted = 0

  const applyBoost = (swing) => {
    const key = toMinuteKey(swing)
    const byKey = key ? (eventsByKey.get(key) || []) : []
    const byIndex = Number.isInteger(swing.index) ? (eventsByIndex.get(swing.index) || []) : []
    const aligned = [...byKey, ...byIndex].find((event) => event.type === swing.type)
    if (!aligned) return swing

    eligible += 1
    boosted += 1
    const baseScore = Number.isFinite(Number(swing.score)) ? Number(swing.score) : 0
    const nextScore = baseScore + bonus
    return {
      ...swing,
      score: nextScore,
      grade: gradeFromScore(nextScore),
      liquiditySweep: aligned,
      signals: [...(swing.signals || []), `LQ Sweep +${bonus}`],
    }
  }

  return {
    swingHighs: swingHighs.map(applyBoost),
    swingLows: swingLows.map(applyBoost),
    stats: {
      mode,
      detected: sweepEvents.length,
      qualified: qualifiedEvents.length,
      eligible,
      boosted,
    },
  }
}

// Plotly Chart Component
const PlotlySwingChart = ({
  data,
  swingHighs,
  swingLows,
  pendingSwing = null,  // Unconfirmed swing that may still move
  showSignals,
  chartRevision = 'swing-analysis-chart',
  ohlcvData = [],
  candleType = 'standard',
  fvgMode = 'off',
  liquiditySweepMode = 'off',
  liquiditySweepEvents = [],
  showStrikeCandles = false,
  strikeRenderMode = 'lines',
  optionLadderCandles = {},
  chartTool = 'pan',
  marketRegimeKey = 'range',
  showStructureMarkers = true,
  showContextMarkers = true,
  showSwingLabels = true,
  showEntrySignals = true,
  entrySignals = [],
  manualTradeMarks = [],
  tradePlan = null,
  showTradePlanOverlay = true,
  workspaceShapes = [],
  onWorkspaceShapesChange = null,
  onChartClick = null,
  isFullscreen = false,
}) => {
  const hasCandles = ohlcvData.length > 0
  const candleLabel = candleType === 'heikin-ashi' ? 'Heikin Ashi' : 'Standard Candles'
  const highMarkerOpacity = marketRegimeKey === 'bear' ? 1 : marketRegimeKey === 'bull' ? 0.5 : 0.75
  const lowMarkerOpacity = marketRegimeKey === 'bull' ? 1 : marketRegimeKey === 'bear' ? 0.5 : 0.75
  // Always use a unified timestamp representation to keep marker alignment stable.
  const times = data.map(d => toPlotTimestamp(d))
  const prices = data.map(d => d.spot)

  // Calculate proper Y-axis range with padding (include OHLCV if present)
  let allPrices = [...prices]
  if (ohlcvData.length > 0) {
    allPrices = [...allPrices, ...ohlcvData.map(d => d.high), ...ohlcvData.map(d => d.low)]
  }
  const minPrice = Math.min(...allPrices)
  const maxPrice = Math.max(...allPrices)
  const priceRange = maxPrice - minPrice
  const safePriceRange = priceRange > 0 ? priceRange : Math.max(Math.abs(maxPrice) * 0.001, 1)
  const yMin = minPrice - safePriceRange * 0.1  // 10% padding below
  const yMax = maxPrice + safePriceRange * 0.15 // 15% padding above for labels

  // Baseline for area fill (at minimum)
  const baselineTrace = {
    x: times,
    y: Array(times.length).fill(yMin),
    type: 'scatter',
    mode: 'lines',
    showlegend: false,
    line: { color: 'transparent', width: 0 },
    hoverinfo: 'skip',
  }

  // Main price line - HIDE when candles are shown, show only swing markers on candles
  const priceTrace = hasCandles ? null : {
    x: times,
    y: prices,
    type: 'scatter',
    mode: 'lines',
    name: 'Signal Price',
    line: { color: '#38bdf8', width: 2 },
    fill: 'tonexty',
    fillcolor: 'rgba(56, 189, 248, 0.15)',
    hovertemplate: '<b>%{x}</b><br>Price: %{y:.2f}<extra></extra>',
  }

  // Build a lookup from minute-key -> candle for fast matching
  const candleByMinute = useMemo(() => {
    const map = {}
    if (hasCandles) {
      ohlcvData.forEach(c => {
        const key = toMinuteKey(c)
        if (key) map[key] = c
      })
    }
    return map
  }, [ohlcvData, hasCandles])

  // Map swing point to candle price; snap to candle high/low + offset for visibility
  const mapSwingToCandle = (swing) => {
    if (!hasCandles) return { price: swing.spot, matchedTime: toPlotTimestamp(swing) }
    const minuteKey = toMinuteKey(swing)
    const candle = candleByMinute[minuteKey]
    if (candle) {
      const price = swing.type === 'high' ? candle.high : candle.low
      return { price, matchedTime: candle.datetime || candle.time }
    }
    return { price: swing.spot, matchedTime: toPlotTimestamp(swing) }
  }

  const mapPointToCandle = (point, side = 'high') => {
    if (!hasCandles) {
      const fallback = Number.isFinite(Number(point?.spot)) ? Number(point.spot) : Number(point?.level)
      return { price: fallback, matchedTime: toPlotTimestamp(point) }
    }

    const minuteKey = toMinuteKey(point)
    const candle = minuteKey ? candleByMinute[minuteKey] : null
    if (candle) {
      const price = side === 'high' ? candle.high : candle.low
      return { price, matchedTime: candle.datetime || candle.time }
    }

    const fallback = Number.isFinite(Number(point?.spot)) ? Number(point.spot) : Number(point?.level)
    return { price: fallback, matchedTime: toPlotTimestamp(point) }
  }

  // Compute mapped positions for swing highs and lows
  const priceOffset = safePriceRange * 0.012  // offset to keep markers above/below candle wicks
  const mappedHighs = swingHighs.map(h => mapSwingToCandle(h))
  const mappedLows = swingLows.map(l => mapSwingToCandle(l))

  const fvgMarkerRows = useMemo(() => {
    if (!showContextMarkers || fvgMode === 'off') return []

    const seen = new Set()
    const rows = []
    data.forEach((row) => {
      const active = String(row?.fvg_active || '').toUpperCase() === 'Y'
      if (!active) return
      const side = normalizeFvgSide(row?.fvg_side)
      if (!side) return

      const plus = String(row?.fvg_plus || '').toUpperCase() === 'Y'
      if (fvgMode === 'fvg-plus' && !plus) return

      const key = toMinuteKey(row) || toPlotTimestamp(row)
      const markerKey = `${key}:${side}:${plus ? 'plus' : 'base'}`
      if (seen.has(markerKey)) return
      seen.add(markerKey)

      rows.push({ row, side, plus })
    })
    return rows
  }, [data, fvgMode, showContextMarkers])

  // Structure highs/lows are neutral context markers, not trade entries.
  const highsTrace = {
    x: mappedHighs.map(m => m.matchedTime),
    y: mappedHighs.map((m, i) => hasCandles ? m.price + priceOffset : swingHighs[i].spot),
    type: 'scatter',
    mode: 'markers+text',
    name: 'Structure High (SH)',
    marker: {
      symbol: 'triangle-down',
      size: swingHighs.map(h => h.isAbsoluteHigh ? 20 : h.grade === 'A+' ? 17 : 13),
      color: swingHighs.map(h => h.grade === 'A+' ? '#f59e0b' : h.grade === 'A' ? '#fbbf24' : '#fcd34d'),
      opacity: highMarkerOpacity,
      line: { color: '#fde68a', width: 1.6 },
    },
    text: showSwingLabels ? swingHighs.map(h => `SH ${h.grade}`) : swingHighs.map(() => ''),
    textposition: 'top center',
    textfont: { size: 10, color: '#fde68a', family: 'Arial Black' },
    hovertemplate: swingHighs.map(h =>
      `<b>Structure High (SH)</b><br>` +
      `Signal Time: ${h.time}<br>` +
      `Spot: ${h.spot.toFixed(2)}<br>` +
      `Grade: ${h.grade} (Score: ${h.score})<br>` +
      `${(h.signals || []).slice(0, 3).join('<br>')}<br>` +
      `${h.details?.outcome ? 'Outcome: ' + h.details.outcome : ''}` +
      `<extra></extra>`
    ),
  }

  const lowsTrace = {
    x: mappedLows.map(m => m.matchedTime),
    y: mappedLows.map((m, i) => hasCandles ? m.price - priceOffset : swingLows[i].spot),
    type: 'scatter',
    mode: 'markers+text',
    name: 'Structure Low (SL)',
    marker: {
      symbol: 'triangle-up',
      size: swingLows.map(l => l.isAbsoluteLow ? 20 : l.grade === 'A+' ? 17 : 13),
      color: swingLows.map(l => l.grade === 'A+' ? '#0ea5e9' : l.grade === 'A' ? '#38bdf8' : '#7dd3fc'),
      opacity: lowMarkerOpacity,
      line: { color: '#bae6fd', width: 1.6 },
    },
    text: showSwingLabels ? swingLows.map(l => `SL ${l.grade}`) : swingLows.map(() => ''),
    textposition: 'bottom center',
    textfont: { size: 10, color: '#bae6fd', family: 'Arial Black' },
    hovertemplate: swingLows.map(l =>
      `<b>Structure Low (SL)</b><br>` +
      `Signal Time: ${l.time}<br>` +
      `Spot: ${l.spot.toFixed(2)}<br>` +
      `Grade: ${l.grade} (Score: ${l.score})<br>` +
      `${(l.signals || []).slice(0, 3).join('<br>')}<br>` +
      `${l.details?.outcome ? 'Outcome: ' + l.details.outcome : ''}` +
      `<extra></extra>`
    ),
  }

  // Pending swing trace - shows unconfirmed swing with distinct styling
  // This swing may still move as new candles arrive
  const pendingSwingTrace = useMemo(() => {
    if (!pendingSwing || !showStructureMarkers) return null
    const mapped = mapSwingToCandle(pendingSwing)
    const isHigh = pendingSwing.type === 'high'
    const yVal = hasCandles
      ? (isHigh ? mapped.price + priceOffset : mapped.price - priceOffset)
      : pendingSwing.spot
    const barsLeft = pendingSwing.barsUntilConfirm || 0

    return {
      x: [mapped.matchedTime],
      y: [yVal],
      type: 'scatter',
      mode: 'markers+text',
      name: `Pending ${isHigh ? 'SH' : 'SL'} (${barsLeft} bars)`,
      marker: {
        symbol: isHigh ? 'triangle-down-open' : 'triangle-up-open',
        size: 15,
        color: isHigh ? '#fbbf24' : '#38bdf8',
        opacity: 0.5,
        line: {
          color: isHigh ? '#fde68a' : '#bae6fd',
          width: 2,
          dash: 'dash',
        },
      },
      text: [`? ${barsLeft}`],
      textposition: isHigh ? 'top center' : 'bottom center',
      textfont: { size: 9, color: '#94a3b8', family: 'Arial' },
      hovertemplate:
        `<b>PENDING ${isHigh ? 'Structure High' : 'Structure Low'}</b><br>` +
        `<i>May still move - ${barsLeft} bar(s) until confirmed</i><br>` +
        `Signal Time: ${pendingSwing.time}<br>` +
        `Spot: ${pendingSwing.spot?.toFixed(2) || 'N/A'}<br>` +
        `<extra></extra>`,
    }
  }, [pendingSwing, showStructureMarkers, hasCandles, priceOffset, mapSwingToCandle])

  // Zigzag line connecting swings (use mapped candle positions when available)
  const allSwings = [...swingHighs, ...swingLows].sort((a, b) => a.index - b.index)
  const allMapped = allSwings.map(s => mapSwingToCandle(s))
  const zigzagTrace = {
    x: allMapped.map(m => m.matchedTime),
    y: allMapped.map(m => m.price),
    type: 'scatter',
    mode: 'lines',
    name: 'Zigzag',
    line: { color: '#a855f7', width: 1.5, dash: 'dot' },
    hoverinfo: 'skip',
  }

  // Candlestick trace (OHLCV data) - rendered on top with full opacity
  const candlestickTrace = ohlcvData.length > 0 ? {
    x: ohlcvData.map(d => d.datetime || d.time),
    open: ohlcvData.map(d => d.open),
    high: ohlcvData.map(d => d.high),
    low: ohlcvData.map(d => d.low),
    close: ohlcvData.map(d => d.close),
    type: 'candlestick',
    name: candleLabel,
    increasing: { line: { color: '#22c55e', width: 1.5 }, fillcolor: 'rgba(34, 197, 94, 0.9)' },
    decreasing: { line: { color: '#ef4444', width: 1.5 }, fillcolor: 'rgba(239, 68, 68, 0.9)' },
    whiskerwidth: 0.8,
    opacity: 1,
    hoverinfo: 'x+text',
    text: ohlcvData.map(d => `O: ${d.open.toFixed(2)}<br>H: ${d.high.toFixed(2)}<br>L: ${d.low.toFixed(2)}<br>C: ${d.close.toFixed(2)}`),
  } : null

  const strikeTraces = useMemo(() => {
    if (!showStrikeCandles) return []

    return STRIKE_LADDER_DEFS.map((definition) => {
      const candles = Array.isArray(optionLadderCandles?.[definition.id]) ? optionLadderCandles[definition.id] : []
      if (!candles.length) return null

      const style = STRIKE_LADDER_STYLE[definition.id] || { color: '#94a3b8', opacity: 0.5 }
      const strikeHoverLabels = candles.map((candle) => {
        const strike = Number(candle?.strike)
        if (!Number.isFinite(strike)) return definition.label
        const strikeText = Number.isInteger(strike) ? strike.toFixed(0) : strike.toFixed(2)
        return `${definition.side} ${strikeText}`
      })
      const strikeHoverData = candles.map((candle, idx) => ([
        strikeHoverLabels[idx],
        Number.isFinite(Number(candle?.open)) ? Number(candle.open) : null,
        Number.isFinite(Number(candle?.high)) ? Number(candle.high) : null,
        Number.isFinite(Number(candle?.low)) ? Number(candle.low) : null,
      ]))
      if (strikeRenderMode === 'lines') {
        return {
          x: candles.map((candle) => candle.datetime),
          y: candles.map((candle) => candle.close),
          customdata: strikeHoverData,
          type: 'scatter',
          mode: 'lines',
          name: `${definition.label} Line`,
          yaxis: 'y2',
          opacity: style.opacity,
          line: { color: style.color, width: 1.25 },
          hovertemplate:
            `<b>%{customdata[0]}</b><br>` +
            `Time: %{x}<br>` +
            `O: %{customdata[1]:.2f}<br>` +
            `H: %{customdata[2]:.2f}<br>` +
            `L: %{customdata[3]:.2f}<br>` +
            `Close: %{y:.2f}<br>` +
            `<extra></extra>`,
        }
      }
      return {
        x: candles.map((candle) => candle.datetime),
        open: candles.map((candle) => candle.open),
        high: candles.map((candle) => candle.high),
        low: candles.map((candle) => candle.low),
        close: candles.map((candle) => candle.close),
        customdata: strikeHoverLabels.map((label) => [label]),
        type: 'candlestick',
        name: `${definition.label} Candle`,
        yaxis: 'y2',
        opacity: style.opacity,
        whiskerwidth: 0.25,
        increasing: { line: { color: style.color, width: 1 }, fillcolor: style.color },
        decreasing: { line: { color: style.color, width: 1 }, fillcolor: style.color },
        hovertemplate:
          `<b>%{customdata[0]}</b><br>` +
          `Time: %{x}<br>` +
          `O: %{open:.2f}<br>` +
          `H: %{high:.2f}<br>` +
          `L: %{low:.2f}<br>` +
          `C: %{close:.2f}<br>` +
          `<extra></extra>`,
      }
    }).filter(Boolean)
  }, [optionLadderCandles, showStrikeCandles, strikeRenderMode])

  const fvgMarkersTrace = fvgMarkerRows.length > 0 ? {
    x: fvgMarkerRows.map(({ row }) => mapPointToCandle(row, 'high').matchedTime),
    y: fvgMarkerRows.map(({ row, side }) => {
      const mapped = mapPointToCandle(row, side === 'BEAR' ? 'high' : 'low')
      if (!Number.isFinite(mapped.price)) return null
      return side === 'BEAR'
        ? mapped.price + (priceOffset * 1.9)
        : mapped.price - (priceOffset * 1.9)
    }),
    type: 'scatter',
    mode: 'markers',
    name: 'FVG Context',
    marker: {
      symbol: 'diamond-open',
      size: 9,
      color: fvgMarkerRows.map(({ plus }) => (plus ? '#06b6d4' : '#0891b2')),
      line: { width: 2 },
    },
    hovertemplate: fvgMarkerRows.map(({ row, side, plus }) =>
      `<b>${plus ? 'FVG+' : 'FVG'}</b><br>` +
      `Side: ${side}<br>` +
      `Time: ${toPlotTimestamp(row)}<br>` +
      `${Number.isFinite(Number(row?.spot)) ? `Spot: ${Number(row.spot).toFixed(2)}<br>` : ''}` +
      `<extra></extra>`
    ),
  } : null

  const visibleLiquiditySweepEvents = useMemo(
    () => {
      if (!showContextMarkers) return []
      return liquiditySweepMode === 'strict'
        ? liquiditySweepEvents.filter(isStrictLiquiditySweepEvent)
        : liquiditySweepEvents
    },
    [liquiditySweepEvents, liquiditySweepMode, showContextMarkers],
  )

  const sweepMarkerPoints = useMemo(() => {
    if (liquiditySweepMode === 'off' || visibleLiquiditySweepEvents.length === 0) return []

    return visibleLiquiditySweepEvents.map((event) => {
      let x = null
      let price = null
      const side = event.type === 'high' ? 'high' : 'low'

      if (hasCandles && Number.isInteger(event.index) && event.index >= 0 && event.index < ohlcvData.length) {
        const candle = ohlcvData[event.index]
        if (candle) {
          x = candle.datetime || candle.time
          price = side === 'high' ? candle.high : candle.low
        }
      }

      if (!x || !Number.isFinite(price)) {
        const mapped = mapPointToCandle(event, side)
        x = mapped.matchedTime
        price = mapped.price
      }

      if (!x || !Number.isFinite(price)) return null
      return {
        event,
        side,
        x,
        y: side === 'high' ? price + (priceOffset * 2.4) : price - (priceOffset * 2.4),
      }
    }).filter(Boolean)
  }, [hasCandles, liquiditySweepMode, mapPointToCandle, ohlcvData, priceOffset, visibleLiquiditySweepEvents])

  const highSweepPoints = sweepMarkerPoints.filter((point) => point.side === 'high')
  const lowSweepPoints = sweepMarkerPoints.filter((point) => point.side === 'low')

  const liquiditySweepHighTrace = highSweepPoints.length > 0 ? {
    x: highSweepPoints.map((point) => point.x),
    y: highSweepPoints.map((point) => point.y),
    type: 'scatter',
    mode: 'markers',
    name: 'Liquidity Sweep High',
    marker: {
      symbol: 'x',
      size: 10,
      color: '#f59e0b',
      line: { width: 2 },
    },
    hovertemplate: highSweepPoints.map(({ event }) =>
      `<b>High Sweep</b><br>` +
      `Time: ${event.time || ''}<br>` +
      `Level: ${Number.isFinite(Number(event.level)) ? Number(event.level).toFixed(2) : '-'}<br>` +
      `Breach: ${Number.isFinite(Number(event.breachPct)) ? Number(event.breachPct).toFixed(3) : '-'}%<br>` +
      `<extra></extra>`
    ),
  } : null

  const liquiditySweepLowTrace = lowSweepPoints.length > 0 ? {
    x: lowSweepPoints.map((point) => point.x),
    y: lowSweepPoints.map((point) => point.y),
    type: 'scatter',
    mode: 'markers',
    name: 'Liquidity Sweep Low',
    marker: {
      symbol: 'circle-open',
      size: 10,
      color: '#fbbf24',
      line: { width: 2 },
    },
    hovertemplate: lowSweepPoints.map(({ event }) =>
      `<b>Low Sweep</b><br>` +
      `Time: ${event.time || ''}<br>` +
      `Level: ${Number.isFinite(Number(event.level)) ? Number(event.level).toFixed(2) : '-'}<br>` +
      `Breach: ${Number.isFinite(Number(event.breachPct)) ? Number(event.breachPct).toFixed(3) : '-'}%<br>` +
      `<extra></extra>`
    ),
  } : null

  const liquiditySweepMarkerTraces = [liquiditySweepHighTrace, liquiditySweepLowTrace].filter(Boolean)

  const entrySignalPoints = useMemo(() => {
    if (!showEntrySignals || !Array.isArray(entrySignals) || entrySignals.length === 0) return []
    return entrySignals.map((signal) => {
      const side = signal?.direction === 'BUY' ? 'low' : 'high'
      const mapped = mapPointToCandle(signal, side)
      if (!mapped?.matchedTime || !Number.isFinite(mapped?.price)) return null
      return {
        ...signal,
        x: mapped.matchedTime,
        y: side === 'low' ? mapped.price - (priceOffset * 3.2) : mapped.price + (priceOffset * 3.2),
      }
    }).filter(Boolean)
  }, [entrySignals, mapPointToCandle, priceOffset, showEntrySignals])

  const buyEntrySignals = entrySignalPoints.filter((signal) => signal.direction === 'BUY')
  const sellEntrySignals = entrySignalPoints.filter((signal) => signal.direction === 'SELL')

  const buyEntryTrace = buyEntrySignals.length > 0 ? {
    x: buyEntrySignals.map((signal) => signal.x),
    y: buyEntrySignals.map((signal) => signal.y),
    type: 'scatter',
    mode: 'markers+text',
    name: 'Qualified BUY',
    marker: {
      symbol: 'triangle-up',
      size: 11,
      color: '#16a34a',
      line: { width: 1.8, color: '#bbf7d0' },
    },
    text: buyEntrySignals.map((signal) => `${Math.round(signal.confluenceScore || 0)}`),
    textposition: 'top center',
    textfont: { size: 9, color: '#bbf7d0', family: 'Arial Black' },
    hovertemplate: buyEntrySignals.map((signal) =>
      `<b>BUY Setup</b><br>` +
      `Time: ${signal.time || signal.datetime || ''}<br>` +
      `Confluence: ${Number.isFinite(Number(signal.confluenceScore)) ? Number(signal.confluenceScore).toFixed(1) : '-'}<br>` +
      `Grade: ${signal.grade || '-'}<br>` +
      `<extra></extra>`
    ),
  } : null

  const sellEntryTrace = sellEntrySignals.length > 0 ? {
    x: sellEntrySignals.map((signal) => signal.x),
    y: sellEntrySignals.map((signal) => signal.y),
    type: 'scatter',
    mode: 'markers+text',
    name: 'Qualified SELL',
    marker: {
      symbol: 'triangle-down',
      size: 11,
      color: '#dc2626',
      line: { width: 1.8, color: '#fecaca' },
    },
    text: sellEntrySignals.map((signal) => `${Math.round(signal.confluenceScore || 0)}`),
    textposition: 'bottom center',
    textfont: { size: 9, color: '#fecaca', family: 'Arial Black' },
    hovertemplate: sellEntrySignals.map((signal) =>
      `<b>SELL Setup</b><br>` +
      `Time: ${signal.time || signal.datetime || ''}<br>` +
      `Confluence: ${Number.isFinite(Number(signal.confluenceScore)) ? Number(signal.confluenceScore).toFixed(1) : '-'}<br>` +
      `Grade: ${signal.grade || '-'}<br>` +
      `<extra></extra>`
    ),
  } : null

  const buyMarks = manualTradeMarks.filter(mark => mark.side === 'BUY')
  const sellMarks = manualTradeMarks.filter(mark => mark.side === 'SELL')

  const buyMarksTrace = buyMarks.length > 0 ? {
    x: buyMarks.map(mark => mark.x),
    y: buyMarks.map(mark => mark.y),
    type: 'scatter',
    mode: 'markers+text',
    name: 'Manual BUY',
    marker: {
      symbol: 'triangle-up',
      size: 14,
      color: '#16a34a',
      line: { color: '#86efac', width: 2 },
    },
    text: buyMarks.map(() => 'BUY'),
    textposition: 'top center',
    textfont: { size: 11, color: '#bbf7d0', family: 'Arial Black' },
    hovertemplate: buyMarks.map(mark =>
      `<b>BUY</b><br>` +
      `Time: ${mark.x}<br>` +
      `Price: ${Number(mark.y).toFixed(2)}<br>` +
      `${mark.note ? `Note: ${mark.note}` : ''}` +
      `<extra></extra>`
    ),
  } : null

  const sellMarksTrace = sellMarks.length > 0 ? {
    x: sellMarks.map(mark => mark.x),
    y: sellMarks.map(mark => mark.y),
    type: 'scatter',
    mode: 'markers+text',
    name: 'Manual SELL',
    marker: {
      symbol: 'triangle-down',
      size: 14,
      color: '#dc2626',
      line: { color: '#fca5a5', width: 2 },
    },
    text: sellMarks.map(() => 'SELL'),
    textposition: 'bottom center',
    textfont: { size: 11, color: '#fecaca', family: 'Arial Black' },
    hovertemplate: sellMarks.map(mark =>
      `<b>SELL</b><br>` +
      `Time: ${mark.x}<br>` +
      `Price: ${Number(mark.y).toFixed(2)}<br>` +
      `${mark.note ? `Note: ${mark.note}` : ''}` +
      `<extra></extra>`
    ),
  } : null

  const tradePlanTraces = useMemo(() => {
    if (!showTradePlanOverlay) return []
    const entrySpot = Number(tradePlan?.entrySpot)
    const stopSpot = Number(tradePlan?.stopSpot)
    const targetSpot = Number(tradePlan?.targetSpot)
    const startTime = String(tradePlan?.entryTime || '').trim()
    const endTime = hasCandles
      ? String(ohlcvData[ohlcvData.length - 1]?.datetime || ohlcvData[ohlcvData.length - 1]?.time || '').trim()
      : String(times[times.length - 1] || '').trim()

    if (!startTime || !endTime) return []
    if (![entrySpot, stopSpot, targetSpot].every((value) => Number.isFinite(value))) return []

    const decision = String(tradePlan?.decision || '').toUpperCase()
    const decisionColor = decision === 'LONG' ? '#22c55e' : decision === 'SHORT' ? '#f43f5e' : '#38bdf8'
    const strike = Number(tradePlan?.strike)
    const contractLabel = buildTradePlanContractLabel(tradePlan)
    const contractDetailParts = []
    if (Number.isFinite(strike)) contractDetailParts.push(`Strike ${formatStrikeValue(strike)}`)
    if (tradePlan?.bucketLabel) contractDetailParts.push(String(tradePlan.bucketLabel))
    const contractDetail = contractDetailParts.join(' | ')
    const entryLabel = contractLabel ? `Entry ${contractLabel}` : 'Entry'

    return [
      {
        x: [startTime, startTime],
        y: [stopSpot, targetSpot],
        type: 'scatter',
        mode: 'lines',
        name: 'Trade Plan Anchor',
        showlegend: false,
        hoverinfo: 'skip',
        line: { color: decisionColor, width: 1.2, dash: 'dot' },
      },
      {
        x: [startTime, endTime],
        y: [entrySpot, entrySpot],
        type: 'scatter',
        mode: 'lines+text',
        name: 'Trade Plan Entry',
        showlegend: false,
        text: ['', `${entryLabel} ${entrySpot.toFixed(2)}`],
        textposition: 'top right',
        textfont: { size: 10, color: '#e2e8f0', family: 'Arial Black' },
        hovertemplate: `<b>Trade Plan Entry</b><br>Contract: ${contractLabel}${contractDetail ? `<br>${contractDetail}` : ''}<br>Time: %{x}<br>Spot: %{y:.2f}<extra></extra>`,
        line: { color: '#e2e8f0', width: 2 },
      },
      {
        x: [startTime, endTime],
        y: [stopSpot, stopSpot],
        type: 'scatter',
        mode: 'lines+text',
        name: 'Trade Plan Stop',
        showlegend: false,
        text: ['', `Stop ${stopSpot.toFixed(2)}`],
        textposition: 'bottom right',
        textfont: { size: 10, color: '#fecaca', family: 'Arial Black' },
        hovertemplate: `<b>Trade Plan Stop</b><br>Contract: ${contractLabel}<br>Time: %{x}<br>Spot: %{y:.2f}<extra></extra>`,
        line: { color: '#ef4444', width: 1.7, dash: 'dash' },
      },
      {
        x: [startTime, endTime],
        y: [targetSpot, targetSpot],
        type: 'scatter',
        mode: 'lines+text',
        name: 'Trade Plan Target',
        showlegend: false,
        text: ['', `Target ${targetSpot.toFixed(2)}`],
        textposition: 'top right',
        textfont: { size: 10, color: '#bbf7d0', family: 'Arial Black' },
        hovertemplate: `<b>Trade Plan Target</b><br>Contract: ${contractLabel}<br>Time: %{x}<br>Spot: %{y:.2f}<extra></extra>`,
        line: { color: '#22c55e', width: 1.7, dash: 'dash' },
      },
    ]
  }, [hasCandles, ohlcvData, showTradePlanOverlay, times, tradePlan])

  const dragMode = (chartTool === 'cursor' || chartTool === 'eraseshape') ? 'pan' : chartTool

  // Annotations for prices - placed well above/below candles with alternating offsets
  const annotations = [
    ...swingHighs.map((h, i) => {
      const mapped = mappedHighs[i]
      const yVal = hasCandles ? mapped.price + priceOffset : h.spot
      return {
        x: mapped.matchedTime,
        y: yVal,
        text: `<b>${h.spot.toFixed(2)}</b>`,
        showarrow: true,
        arrowhead: 2,
        arrowsize: 1,
        arrowwidth: 1,
        arrowcolor: '#b45309',
        ax: (i % 2 === 0) ? -20 : 20,
        ay: -50,
        font: { size: 10, color: '#fde68a', family: 'monospace' },
        bgcolor: '#78350f',
        bordercolor: '#f59e0b',
        borderwidth: 1,
        borderpad: 3,
      }
    }),
    ...swingLows.map((l, i) => {
      const mapped = mappedLows[i]
      const yVal = hasCandles ? mapped.price - priceOffset : l.spot
      return {
        x: mapped.matchedTime,
        y: yVal,
        text: `<b>${l.spot.toFixed(2)}</b>`,
        showarrow: true,
        arrowhead: 2,
        arrowsize: 1,
        arrowwidth: 1,
        arrowcolor: '#075985',
        ax: (i % 2 === 0) ? 20 : -20,
        ay: 50,
        font: { size: 10, color: '#bae6fd', family: 'monospace' },
        bgcolor: '#0c4a6e',
        bordercolor: '#0ea5e9',
        borderwidth: 1,
        borderpad: 3,
      }
    }),
  ]

  const hasStrikePanel = strikeRenderMode === 'candles' && strikeTraces.length > 0
  const layout = {
    uirevision: chartRevision,
    title: {
      text: hasCandles
        ? `Expert Swing Detection (${candleLabel}${hasStrikePanel ? ' + Option Panel' : ''})`
        : 'Expert Swing Detection (Signal Price)',
      font: { color: '#e2e8f0', size: 16 },
    },
    paper_bgcolor: '#0f172a',
    plot_bgcolor: '#1e293b',
    font: { color: '#94a3b8' },
    xaxis: {
      title: 'Time',
      gridcolor: '#334155',
      linecolor: '#475569',
      tickfont: { size: 10 },
      showspikes: true,
      spikemode: 'across',
      spikesnap: 'cursor',
      spikethickness: 1,
      spikecolor: '#64748b',
      rangeslider: { visible: true, bgcolor: '#1e293b' },
      rangeselector: {
        buttons: [
          { count: 30, label: '30m', step: 'minute', stepmode: 'backward' },
          { count: 1, label: '1h', step: 'hour', stepmode: 'backward' },
          { count: 2, label: '2h', step: 'hour', stepmode: 'backward' },
          { step: 'all', label: 'All' },
        ],
        bgcolor: '#334155',
        activecolor: '#0ea5e9',
        font: { color: '#e2e8f0' },
      },
    },
    yaxis: {
      title: 'Price',
      domain: hasStrikePanel ? [0.34, 1] : [0, 1],
      gridcolor: '#334155',
      linecolor: '#475569',
      tickfont: { size: 10 },
      tickformat: ',.2f',
      range: [yMin, yMax],
      autorange: false,
      showspikes: true,
      spikemode: 'across',
      spikesnap: 'cursor',
      spikethickness: 1,
      spikecolor: '#64748b',
    },
    yaxis2: {
      title: 'Option Price',
      domain: hasStrikePanel ? [0, 0.26] : undefined,
      overlaying: hasStrikePanel ? undefined : 'y',
      anchor: 'x',
      side: 'right',
      showgrid: hasStrikePanel,
      gridcolor: '#243041',
      zeroline: false,
      tickfont: { size: 10, color: '#cbd5e1' },
      titlefont: { color: '#cbd5e1', size: 11 },
    },
    hovermode: 'closest',
    showlegend: true,
    legend: {
      orientation: 'h',
      y: 1.12,
      x: 0.5,
      xanchor: 'center',
      bgcolor: 'rgba(30, 41, 59, 0.8)',
      font: { color: '#e2e8f0' },
    },
    annotations: showSignals && showStructureMarkers ? annotations : [],
    shapes: workspaceShapes,
    margin: { t: 80, b: hasStrikePanel ? 120 : 100, l: 80, r: 60 },
    dragmode: dragMode,
  }

  const config = {
    displayModeBar: true,
    displaylogo: false,
    modeBarButtonsToAdd: ['drawline', 'drawopenpath', 'drawclosedpath', 'drawrect', 'drawcircle', 'eraseshape', 'togglespikelines'],
    modeBarButtonsToRemove: ['lasso2d', 'select2d'],
    scrollZoom: true,
    responsive: true,
    editable: true,
  }

  return (
    <Plot
      data={[
        baselineTrace,
        priceTrace,
        showStructureMarkers ? zigzagTrace : null,
        candlestickTrace,
        ...strikeTraces,
        showContextMarkers ? fvgMarkersTrace : null,
        ...(showContextMarkers ? liquiditySweepMarkerTraces : []),
        buyEntryTrace,
        sellEntryTrace,
        ...tradePlanTraces,
        buyMarksTrace,
        sellMarksTrace,
        showStructureMarkers ? lowsTrace : null,
        showStructureMarkers ? highsTrace : null,
        showStructureMarkers ? pendingSwingTrace : null,
      ].filter(Boolean)}
      layout={layout}
      config={config}
      onUpdate={(figure) => {
        if (!onWorkspaceShapesChange) return
        const shapes = Array.isArray(figure?.layout?.shapes) ? figure.layout.shapes : []
        onWorkspaceShapesChange(shapes)
      }}
      onClick={onChartClick || undefined}
      style={{ width: '100%', height: isFullscreen ? '82vh' : '700px' }}
      useResizeHandler={true}
    />
  )
}

// Main Component
const SwingAnalysis = () => {
  const [mode, setMode] = useState('historical')
  const [historicalData, setHistoricalData] = useState([])
  const [historicalOptionRows, setHistoricalOptionRows] = useState([])
  const [fileName, setFileName] = useState('')
  const [historicalOhlcvData, setHistoricalOhlcvData] = useState([])
  const [ohlcvFileName, setOhlcvFileName] = useState('')
  const [liveData, setLiveData] = useState([])
  const [liveOptionRows, setLiveOptionRows] = useState([])
  const [liveOhlcvData, setLiveOhlcvData] = useState([])
  const [liveIndex, setLiveIndex] = useState('NIFTY50')
  const [liveIntervalSec, setLiveIntervalSec] = useState(15)
  const [liveDate, setLiveDate] = useState('')
  const [liveStatus, setLiveStatus] = useState('idle')
  const [lastLiveUpdate, setLastLiveUpdate] = useState(null)
  const [liveCandlesStatus, setLiveCandlesStatus] = useState('')
  const [isDocumentVisible, setIsDocumentVisible] = useState(() => {
    if (typeof document === 'undefined') return true
    return document.visibilityState !== 'hidden'
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [minMove, setMinMove] = useState(0.0015)
  const [showSignals, setShowSignals] = useState(true)
  const [showCandles, setShowCandles] = useState(true)
  const [showStrikeCandles, setShowStrikeCandles] = useState(true)
  const [strikeRenderMode, setStrikeRenderMode] = useState('lines')
  const [showAllOptions, setShowAllOptions] = useState(true)
  const [viewPreset, setViewPreset] = useState('signals')
  const [selectedStrikeLegIds, setSelectedStrikeLegIds] = useState(DEFAULT_STRIKE_LEG_IDS)
  const [entrySignalThreshold, setEntrySignalThreshold] = useState(62)
  const [enable3DGate, setEnable3DGate] = useState(true)
  const [decisionPersistenceBars, setDecisionPersistenceBars] = useState(DECISION_3D_SETTINGS.persistenceBars)
  const [showTradePlanOverlay, setShowTradePlanOverlay] = useState(true)
  const [showTradeTicket, setShowTradeTicket] = useState(true)
  const [candleType, setCandleType] = useState('heikin-ashi')
  const [fvgMode, setFvgMode] = useState('off')
  const [liquiditySweepMode, setLiquiditySweepMode] = useState('off')
  const [requireEntryConfirmation, setRequireEntryConfirmation] = useState(false)
  const [chartTool, setChartTool] = useState('pan')
  const [tradeTagMode, setTradeTagMode] = useState('none')
  const [tradeTagNote, setTradeTagNote] = useState('')
  const [manualTradeMarks, setManualTradeMarks] = useState([])
  const [workspaceShapes, setWorkspaceShapes] = useState([])
  const [ticketSide, setTicketSide] = useState('BUY')
  const [ticketOrderType, setTicketOrderType] = useState('LIMIT')
  const [ticketEntry, setTicketEntry] = useState('')
  const [ticketStop, setTicketStop] = useState('')
  const [ticketTarget, setTicketTarget] = useState('')
  const [ticketQty, setTicketQty] = useState(1)
  const [ticketRiskBudget, setTicketRiskBudget] = useState(1000)
  const [ticketTag, setTicketTag] = useState('')
  const [ticketInstrumentLabel, setTicketInstrumentLabel] = useState('')
  const [ticketInstrumentBucketLabel, setTicketInstrumentBucketLabel] = useState('')
  const [ticketInstrumentDecision, setTicketInstrumentDecision] = useState('')
  const [ticketStructureSummary, setTicketStructureSummary] = useState('')
  const [ticketChartPlan, setTicketChartPlan] = useState(null)
  const [ticketSnapshots, setTicketSnapshots] = useState([])
  const [selectedEventTime, setSelectedEventTime] = useState('')
  const [activeTab, setActiveTab] = useState('chart')
  const [isChartFullscreen, setIsChartFullscreen] = useState(false)
  const chartPanelRef = useRef(null)

  const data = mode === 'live' ? liveData : historicalData
  const optionRows = mode === 'live' ? liveOptionRows : historicalOptionRows
  const rawOhlcvData = mode === 'live' ? liveOhlcvData : historicalOhlcvData
  const ohlcvData = useMemo(
    () => (rawOhlcvData.length > 0 ? rawOhlcvData : deriveOhlcvFromSignals(data)),
    [data, rawOhlcvData],
  )
  const analysisOhlcvData = useMemo(
    () => (ohlcvData.length > 0 ? buildHeikinAshiCandles(ohlcvData) : []),
    [ohlcvData],
  )
  const displayedOhlcvData = useMemo(() => {
    if (!showCandles) return []
    return candleType === 'heikin-ashi'
      ? analysisOhlcvData
      : ohlcvData
  }, [analysisOhlcvData, candleType, ohlcvData, showCandles])
  const showTradeTicketPanel = showAllOptions && showTradeTicket && !isChartFullscreen
  const showStructureMarkers = viewPreset !== 'price-only'
  const showContextMarkers = viewPreset === 'signals'
  const showEntrySignals = viewPreset !== 'price-only'
  const showSwingLabels = viewPreset === 'signals'
  const showStrikePanel = showStrikeCandles && viewPreset !== 'price-only'

  const toggleChartFullscreen = useCallback(async () => {
    if (typeof document === 'undefined') return
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen()
        return
      }
      if (chartPanelRef.current?.requestFullscreen) {
        await chartPanelRef.current.requestFullscreen()
      }
    } catch (err) {
      setError(err?.message || 'Unable to toggle fullscreen')
    }
  }, [])

  useEffect(() => {
    if (typeof document === 'undefined') return undefined
    const onFullscreenChange = () => {
      setIsChartFullscreen(document.fullscreenElement === chartPanelRef.current)
    }
    document.addEventListener('fullscreenchange', onFullscreenChange)
    onFullscreenChange()
    return () => document.removeEventListener('fullscreenchange', onFullscreenChange)
  }, [])

  const liveFileName = useMemo(
    () => (mode === 'live' ? `${liveIndex} (${liveDate || 'latest'})` : ''),
    [liveDate, liveIndex, mode],
  )
  const chartRevision = useMemo(() => {
    const firstSignalTime = data.length > 0 ? toPlotTimestamp(data[0]) : ''
    const lastSignalTime = data.length > 0 ? toPlotTimestamp(data[data.length - 1]) : ''
    const firstCandleTime = rawOhlcvData.length > 0 ? toPlotTimestamp(rawOhlcvData[0]) : ''
    const lastCandleTime = rawOhlcvData.length > 0 ? toPlotTimestamp(rawOhlcvData[rawOhlcvData.length - 1]) : ''

    return [
      mode,
      mode === 'live' ? liveIndex : fileName,
      mode === 'live' ? liveDate : ohlcvFileName,
      data.length,
      rawOhlcvData.length,
      firstSignalTime,
      lastSignalTime,
      firstCandleTime,
      lastCandleTime,
    ].join('|')
  }, [data, fileName, liveDate, liveIndex, mode, ohlcvFileName, rawOhlcvData])

  const swingDetectionData = useMemo(() => {
    if (analysisOhlcvData.length === 0) return data
    return buildSwingDataFromCandles(analysisOhlcvData, data)
  }, [analysisOhlcvData, data])

  const { swingHighs: detectedHighs, swingLows: detectedLows, pendingSwing } = useMemo(
    () => findSwingPointsExpert(swingDetectionData, minMove),
    [swingDetectionData, minMove],
  )

  const confirmationResult = useMemo(
    () => applyEntryCandleConfirmation(detectedHighs, detectedLows, analysisOhlcvData),
    [analysisOhlcvData, detectedHighs, detectedLows],
  )

  const baseSwingHighs = useMemo(
    () => (requireEntryConfirmation ? confirmationResult.confirmedHighs : confirmationResult.highsAnnotated),
    [confirmationResult, requireEntryConfirmation],
  )
  const baseSwingLows = useMemo(
    () => (requireEntryConfirmation ? confirmationResult.confirmedLows : confirmationResult.lowsAnnotated),
    [confirmationResult, requireEntryConfirmation],
  )
  const fvgApplied = useMemo(
    () => applyFvgBoost(baseSwingHighs, baseSwingLows, fvgMode),
    [baseSwingHighs, baseSwingLows, fvgMode],
  )
  const liquiditySweepEvents = useMemo(
    () => detectLiquiditySweepEvents(analysisOhlcvData),
    [analysisOhlcvData],
  )
  const liquidityApplied = useMemo(
    () => applyLiquiditySweepBoost(fvgApplied.swingHighs, fvgApplied.swingLows, liquiditySweepEvents, liquiditySweepMode),
    [fvgApplied.swingHighs, fvgApplied.swingLows, liquiditySweepEvents, liquiditySweepMode],
  )
  const swingHighs = liquidityApplied.swingHighs
  const swingLows = liquidityApplied.swingLows
  const swingSourceLabel = analysisOhlcvData.length > 0 ? 'Heikin Ashi candles' : 'Signal spot rows'
  const confirmationStats = confirmationResult.stats
  const fvgStats = fvgApplied.stats
  const liquiditySweepStats = liquidityApplied.stats
  const marketRegime = useMemo(
    () => detectMarketRegime(analysisOhlcvData.length > 0 ? analysisOhlcvData : data),
    [analysisOhlcvData, data],
  )
  const confluenceSignals = useMemo(
    () => computeConfluenceSignals(swingHighs, swingLows, marketRegime.key),
    [marketRegime.key, swingHighs, swingLows],
  )
  const baseQualifiedConfluenceSignals = useMemo(
    () => confluenceSignals.filter((signal) => Number(signal?.confluenceScore) >= entrySignalThreshold),
    [confluenceSignals, entrySignalThreshold],
  )
  const patterns = useMemo(() => detectPatterns(swingHighs, swingLows), [swingHighs, swingLows])
  const currentSymbol = useMemo(
    () => data.find((row) => row?.symbol)?.symbol || (mode === 'live' ? liveIndex : 'N/A'),
    [data, liveIndex, mode],
  )
  const optionStrikeStep = useMemo(() => {
    const indexName = normalizeIndexName(mode === 'live' ? liveIndex : currentSymbol)
    const defaultStep = INDEX_STRIKE_STEP_DEFAULTS[indexName] || 100

    const uniqueStrikes = Array.from(
      new Set(
        optionRows
          .map((row) => Number(row?.strike))
          .filter((strike) => Number.isFinite(strike) && strike > 0),
      ),
    ).sort((a, b) => a - b)

    if (uniqueStrikes.length < 2) return defaultStep
    let minGap = Number.POSITIVE_INFINITY
    for (let i = 1; i < uniqueStrikes.length; i++) {
      const gap = uniqueStrikes[i] - uniqueStrikes[i - 1]
      if (gap > 0 && gap < minGap) minGap = gap
    }
    return Number.isFinite(minGap) ? minGap : defaultStep
  }, [currentSymbol, liveIndex, mode, optionRows])
  const optionLadderCandles = useMemo(
    () => buildOptionLadderCandles(optionRows, optionStrikeStep),
    [optionRows, optionStrikeStep],
  )
  const filteredOptionLadderCandles = useMemo(() => {
    const selected = new Set(selectedStrikeLegIds)
    const out = {}
    STRIKE_LADDER_DEFS.forEach((definition) => {
      out[definition.id] = selected.has(definition.id)
        ? (Array.isArray(optionLadderCandles?.[definition.id]) ? optionLadderCandles[definition.id] : [])
        : []
    })
    return out
  }, [optionLadderCandles, selectedStrikeLegIds])
  const selectedStrikeDefs = useMemo(
    () => STRIKE_LADDER_DEFS.filter((definition) => selectedStrikeLegIds.includes(definition.id)),
    [selectedStrikeLegIds],
  )
  const selectedStrikeLabels = useMemo(
    () => selectedStrikeDefs.map((definition) => definition.label).join(', '),
    [selectedStrikeDefs],
  )
  const optionSnapshots = useMemo(() => {
    if (!optionRows.length) return []

    const byTime = new Map()
    optionRows.forEach((row) => {
      const key = toPlotTimestamp(row)
      if (!key) return

      const side = normalizeOptionSide(row.side)
      const strike = Number(row.strike)
      if (!side || !Number.isFinite(strike)) return

      let snapshot = byTime.get(key)
      if (!snapshot) {
        snapshot = {
          key,
          date: row.date || '',
          time: row.time || '',
          spot: Number.isFinite(Number(row.spot)) ? Number(row.spot) : null,
          legs: { CE: new Map(), PE: new Map() },
        }
        byTime.set(key, snapshot)
      }

      if (Number.isFinite(Number(row.spot))) snapshot.spot = Number(row.spot)
      snapshot.legs[side].set(strike, row)
    })

    return Array.from(byTime.values()).sort((a, b) => a.key.localeCompare(b.key))
  }, [optionRows])
  const optionMinuteSnapshots = useMemo(
    () => aggregateOptionSnapshotsByMinute(optionSnapshots),
    [optionSnapshots],
  )
  const decisionInputSnapshots = useMemo(() => {
    if (optionMinuteSnapshots.length === 0) return []
    if (mode === 'live' && optionMinuteSnapshots.length > 1) {
      return optionMinuteSnapshots.slice(0, -1)
    }
    return optionMinuteSnapshots
  }, [mode, optionMinuteSnapshots])
  const decisionBarModeLabel = mode === 'live' ? 'Closed 1m bars' : '1m bars'
  const optionLadderComparison = useMemo(() => {
    if (optionSnapshots.length < 2 || !Number.isFinite(optionStrikeStep) || optionStrikeStep <= 0) {
      return {
        rows: [],
        totalSnapshots: optionSnapshots.length,
        spotStart: null,
        spotLatest: null,
        spotChangePct: null,
      }
    }

    const totalSnapshots = optionSnapshots.length
    const spotSeries = optionSnapshots.map((snapshot) => (
      Number.isFinite(Number(snapshot.spot)) ? Number(snapshot.spot) : null
    ))
    const spotFinite = spotSeries.filter((value) => Number.isFinite(value))
    const spotStart = spotFinite.length > 0 ? spotFinite[0] : null
    const spotLatest = spotFinite.length > 0 ? spotFinite[spotFinite.length - 1] : null
    const spotChangePct = Number.isFinite(spotStart) && spotStart !== 0 && Number.isFinite(spotLatest)
      ? ((spotLatest - spotStart) / spotStart) * 100
      : null

    const spotReturns = []
    for (let i = 1; i < spotSeries.length; i++) {
      const prev = spotSeries[i - 1]
      const next = spotSeries[i]
      if (Number.isFinite(prev) && prev !== 0 && Number.isFinite(next)) {
        spotReturns.push((next - prev) / prev)
      } else {
        spotReturns.push(null)
      }
    }

    const legDefs = STRIKE_LADDER_DEFS

    const rows = legDefs.map((definition) => {
      const mids = []
      const ivs = []
      const deltas = []
      const volumes = []
      const ois = []

      optionSnapshots.forEach((snapshot) => {
        const spot = Number(snapshot.spot)
        if (!Number.isFinite(spot)) {
          mids.push(null)
          return
        }

        const atm = Math.round(spot / optionStrikeStep) * optionStrikeStep
        const targetStrike = definition.side === 'CE'
          ? atm + (definition.offset * optionStrikeStep)
          : atm - (definition.offset * optionStrikeStep)
        const leg = snapshot.legs[definition.side].get(targetStrike)
        if (!leg) {
          mids.push(null)
          return
        }

        const mid = getOptionMidPrice(leg)
        mids.push(Number.isFinite(Number(mid)) ? Number(mid) : null)
        if (Number.isFinite(Number(leg.iv))) ivs.push(Number(leg.iv))
        if (Number.isFinite(Number(leg.delta))) deltas.push(Number(leg.delta))
        if (Number.isFinite(Number(leg.volume))) volumes.push(Number(leg.volume))
        if (Number.isFinite(Number(leg.oi))) ois.push(Number(leg.oi))
      })

      const finiteMids = mids.filter((value) => Number.isFinite(value))
      const first = finiteMids.length > 0 ? finiteMids[0] : null
      const latest = finiteMids.length > 0 ? finiteMids[finiteMids.length - 1] : null
      const changePct = Number.isFinite(first) && first !== 0 && Number.isFinite(latest)
        ? ((latest - first) / first) * 100
        : null

      const spotRetAligned = []
      const legRetAligned = []
      for (let i = 1; i < mids.length; i++) {
        const prev = mids[i - 1]
        const next = mids[i]
        const spotRet = spotReturns[i - 1]
        if (Number.isFinite(prev) && prev !== 0 && Number.isFinite(next) && Number.isFinite(spotRet)) {
          spotRetAligned.push(spotRet)
          legRetAligned.push((next - prev) / prev)
        }
      }

      return {
        id: definition.id,
        label: definition.label,
        side: definition.side,
        offset: definition.offset,
        coverage: `${finiteMids.length}/${totalSnapshots}`,
        latest,
        changePct,
        avgIv: ivs.length > 0 ? ivs.reduce((sum, value) => sum + value, 0) / ivs.length : null,
        avgDelta: deltas.length > 0 ? deltas.reduce((sum, value) => sum + value, 0) / deltas.length : null,
        avgVolume: volumes.length > 0 ? volumes.reduce((sum, value) => sum + value, 0) / volumes.length : null,
        avgOi: ois.length > 0 ? ois.reduce((sum, value) => sum + value, 0) / ois.length : null,
        corrToSpot: calculateCorrelation(spotRetAligned, legRetAligned),
        corrPoints: spotRetAligned.length,
      }
    })

    return {
      rows,
      totalSnapshots,
      spotStart,
      spotLatest,
      spotChangePct,
    }
  }, [optionSnapshots, optionStrikeStep])
  const decision3DSeries = useMemo(
    () => build3DDecisionSeries(decisionInputSnapshots, optionStrikeStep, selectedStrikeDefs, { persistenceBars: decisionPersistenceBars }),
    [decisionInputSnapshots, decisionPersistenceBars, optionStrikeStep, selectedStrikeDefs],
  )
  const latestDecision3D = useMemo(
    () => (decision3DSeries.length > 0 ? decision3DSeries[decision3DSeries.length - 1] : null),
    [decision3DSeries],
  )
  const decision3DRecent = useMemo(
    () => [...decision3DSeries].slice(-8).reverse(),
    [decision3DSeries],
  )
  const decision3DByMinuteKey = useMemo(() => {
    const map = new Map()
    decision3DSeries.forEach((state) => {
      if (state?.minuteKey) map.set(state.minuteKey, state)
    })
    return map
  }, [decision3DSeries])
  const latestSuggestedTrade = latestDecision3D?.suggestedTrade || null
  const qualifiedConfluenceSignals = useMemo(() => {
    const withDecisionContext = baseQualifiedConfluenceSignals.map((signal) => {
      const key = toMinuteKey(signal)
      const state = key ? decision3DByMinuteKey.get(key) : null
      if (!state) return signal
      return {
        ...signal,
        decision3D: state,
        suggestedTrade: state.suggestedTrade || null,
      }
    })

    if (!enable3DGate || decision3DByMinuteKey.size === 0) return withDecisionContext
    return withDecisionContext.filter((signal) => {
      const state = signal?.decision3D || null
      if (!state) return true
      if (!Number.isFinite(Number(state.confidence)) || Number(state.confidence) < 55) return false
      if (state.decision === 'HOLD') return false
      return (
        (state.decision === 'LONG' && signal.direction === 'BUY')
        || (state.decision === 'SHORT' && signal.direction === 'SELL')
      )
    })
  }, [baseQualifiedConfluenceSignals, decision3DByMinuteKey, enable3DGate])
  const latestConfluenceSignals = useMemo(
    () => [...qualifiedConfluenceSignals]
      .sort((a, b) => (Number(b?.index) || 0) - (Number(a?.index) || 0))
      .slice(0, 6),
    [qualifiedConfluenceSignals],
  )
  const eventStudy = useMemo(() => {
    if (!selectedEventTime || optionSnapshots.length < 3 || !Number.isFinite(optionStrikeStep) || optionStrikeStep <= 0) {
      return null
    }

    const resolveLegPrice = (snapshot, definition) => {
      if (!snapshot || !definition || !Number.isFinite(Number(snapshot.spot))) return null
      const spot = Number(snapshot.spot)
      const atm = Math.round(spot / optionStrikeStep) * optionStrikeStep
      const strike = definition.side === 'CE'
        ? atm + (definition.offset * optionStrikeStep)
        : atm - (definition.offset * optionStrikeStep)
      const row = snapshot.legs?.[definition.side]?.get(strike)
      return row ? getOptionMidPrice(row) : null
    }

    const targetTs = toTimestampValue(selectedEventTime)
    let centerIdx = -1
    if (Number.isFinite(targetTs)) {
      let bestDiff = Number.POSITIVE_INFINITY
      optionSnapshots.forEach((snapshot, idx) => {
        const ts = toTimestampValue(snapshot.key)
        if (!Number.isFinite(ts)) return
        const diff = Math.abs(ts - targetTs)
        if (diff < bestDiff) {
          bestDiff = diff
          centerIdx = idx
        }
      })
    }
    if (centerIdx < 0) {
      centerIdx = optionSnapshots.findIndex((snapshot) => snapshot.key === selectedEventTime)
    }
    if (centerIdx < 0) return null

    const beforeIdx = Math.max(0, centerIdx - 5)
    const afterIdx = Math.min(optionSnapshots.length - 1, centerIdx + 5)
    const before = optionSnapshots[beforeIdx]
    const center = optionSnapshots[centerIdx]
    const after = optionSnapshots[afterIdx]

    const spotBefore = Number(before?.spot)
    const spotCenter = Number(center?.spot)
    const spotAfter = Number(after?.spot)
    const spotIntoEventPct = Number.isFinite(spotBefore) && spotBefore > 0 && Number.isFinite(spotCenter)
      ? ((spotCenter - spotBefore) / spotBefore) * 100
      : null
    const spotAfterEventPct = Number.isFinite(spotCenter) && spotCenter > 0 && Number.isFinite(spotAfter)
      ? ((spotAfter - spotCenter) / spotCenter) * 100
      : null

    const rows = selectedStrikeDefs.map((definition) => {
      const beforePrice = resolveLegPrice(before, definition)
      const centerPrice = resolveLegPrice(center, definition)
      const afterPrice = resolveLegPrice(after, definition)
      const intoEventPct = Number.isFinite(beforePrice) && beforePrice > 0 && Number.isFinite(centerPrice)
        ? ((centerPrice - beforePrice) / beforePrice) * 100
        : null
      const afterEventPct = Number.isFinite(centerPrice) && centerPrice > 0 && Number.isFinite(afterPrice)
        ? ((afterPrice - centerPrice) / centerPrice) * 100
        : null
      return {
        id: definition.id,
        label: definition.label,
        side: definition.side,
        beforePrice,
        centerPrice,
        afterPrice,
        intoEventPct,
        afterEventPct,
      }
    })

    return {
      selectedTime: center.key,
      beforeTime: before?.key || '',
      afterTime: after?.key || '',
      spotBefore,
      spotCenter,
      spotAfter,
      spotIntoEventPct,
      spotAfterEventPct,
      rows,
    }
  }, [optionSnapshots, optionStrikeStep, selectedEventTime, selectedStrikeDefs])
  const latestSpot = useMemo(() => {
    if (!data.length) return null
    const last = data[data.length - 1]
    return Number.isFinite(Number(last?.spot)) ? Number(last.spot) : null
  }, [data])
  const ticketMetrics = useMemo(() => {
    const entry = parseOptionalNumber(ticketEntry)
    const stop = parseOptionalNumber(ticketStop)
    const target = parseOptionalNumber(ticketTarget)
    const qty = Math.max(1, Number(ticketQty) || 1)
    const budget = Math.max(0, Number(ticketRiskBudget) || 0)

    const riskPerUnit = Number.isFinite(entry) && Number.isFinite(stop)
      ? (ticketSide === 'BUY' ? entry - stop : stop - entry)
      : null
    const rewardPerUnit = Number.isFinite(entry) && Number.isFinite(target)
      ? (ticketSide === 'BUY' ? target - entry : entry - target)
      : null

    const validRisk = Number.isFinite(riskPerUnit) && riskPerUnit > 0 ? riskPerUnit : null
    const validReward = Number.isFinite(rewardPerUnit) && rewardPerUnit > 0 ? rewardPerUnit : null
    const rr = validRisk && validReward ? validReward / validRisk : null
    const capital = Number.isFinite(entry) ? entry * qty : null
    const maxLoss = validRisk ? validRisk * qty : null
    const maxProfit = validReward ? validReward * qty : null
    const riskPctOfBudget = maxLoss && budget > 0 ? (maxLoss / budget) * 100 : null
    const recommendedQty = validRisk && budget > 0 ? Math.floor(budget / validRisk) : null

    return {
      entry,
      stop,
      target,
      qty,
      budget,
      validRisk,
      validReward,
      rr,
      capital,
      maxLoss,
      maxProfit,
      riskPctOfBudget,
      recommendedQty,
      isValid: Boolean(validRisk && validReward),
    }
  }, [ticketEntry, ticketQty, ticketRiskBudget, ticketSide, ticketStop, ticketTarget])

  const gradeStats = useMemo(() => {
    const all = [...swingHighs, ...swingLows]
    return {
      'A+': all.filter(s => s.grade === 'A+').length,
      'A': all.filter(s => s.grade === 'A').length,
      'B+': all.filter(s => s.grade === 'B+').length,
      'B': all.filter(s => s.grade === 'B').length,
      'C+': all.filter(s => s.grade === 'C+').length,
      'C': all.filter(s => s.grade === 'C').length,
    }
  }, [swingHighs, swingLows])

  const topSwings = useMemo(() =>
    [...swingHighs, ...swingLows].sort((a, b) => b.score - a.score).slice(0, 10)
    , [swingHighs, swingLows])

  const handleUpload = useCallback((e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setLoading(true); setError(''); setFileName(file.name)
    const reader = new FileReader()
    reader.onload = (ev) => {
      try {
        const parsed = parseCSV(ev.target.result)
        const optionParsed = parseOptionRowsCSV(ev.target.result)
        if (!parsed.length) throw new Error('No data')
        setHistoricalData(parsed)
        setHistoricalOptionRows(optionParsed)
        setLoading(false)
      } catch (err) { setError(err.message); setLoading(false) }
    }
    reader.onerror = () => { setError('Read failed'); setLoading(false) }
    reader.readAsText(file)
  }, [])

  const handleOhlcvUpload = useCallback((e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setLoading(true); setError(''); setOhlcvFileName(file.name)
    const reader = new FileReader()
    reader.onload = (ev) => {
      try {
        const parsed = parseOHLCV(ev.target.result)
        if (!parsed.length) throw new Error('No OHLCV data')
        setHistoricalOhlcvData(parsed); setLoading(false)
      } catch (err) { setError(err.message); setLoading(false) }
    }
    reader.onerror = () => { setError('Read failed'); setLoading(false) }
    reader.readAsText(file)
  }, [])

  const fetchLiveNow = useCallback(async ({ background = false } = {}) => {
    if (!background) {
      setLoading(true)
      setError('')
    }
    try {
      const payload = await fetchFyersN7LiveSignals(liveIndex)
      const latestDate = payload?.date
      if (!latestDate) throw new Error('No live date found')
      setLiveDate(latestDate)

      const rows = payload?.rows || []
      const normalized = normalizeLiveSignalRows(rows)
      const normalizedOptionRows = normalizeLiveOptionRows(rows)
      setLiveData(normalized)
      setLiveOptionRows(normalizedOptionRows)

      const derivedCandles = deriveOhlcvFromSignals(normalized)
      setLiveOhlcvData(derivedCandles)
      setLiveCandlesStatus(
        derivedCandles.length > 0
          ? `Derived ${derivedCandles.length} candles from live spot ticks`
          : 'Live candle stream unavailable for current window',
      )

      setLiveStatus('running')
      setLastLiveUpdate(new Date())
    } catch (err) {
      setLiveStatus('error')
      setError(err?.message || 'Live fetch failed')
    } finally {
      if (!background) {
        setLoading(false)
      }
    }
  }, [liveIndex])

  useEffect(() => {
    if (typeof document === 'undefined') return undefined

    const handleVisibilityChange = () => {
      setIsDocumentVisible(document.visibilityState !== 'hidden')
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  }, [])

  useEffect(() => {
    if (mode !== 'live' || !isDocumentVisible) return
    fetchLiveNow({ background: false })
    const sec = Math.min(60, Math.max(1, Number(liveIntervalSec) || 15))
    const timer = setInterval(() => {
      fetchLiveNow({ background: true })
    }, sec * 1000)
    return () => clearInterval(timer)
  }, [mode, liveIntervalSec, fetchLiveNow, isDocumentVisible])

  useEffect(() => {
    if (analysisOhlcvData.length === 0) {
      setRequireEntryConfirmation(false)
    }
  }, [analysisOhlcvData.length])

  useEffect(() => {
    setSelectedEventTime('')
  }, [fileName, liveDate, liveIndex, mode])

  useEffect(() => {
    if (typeof window === 'undefined') return
    try {
      const raw = window.localStorage.getItem(SWING_WORKSPACE_STORAGE_KEY)
      if (!raw) return
      const saved = JSON.parse(raw)
      if (Array.isArray(saved.manualTradeMarks)) setManualTradeMarks(saved.manualTradeMarks)
      if (Array.isArray(saved.workspaceShapes)) setWorkspaceShapes(saved.workspaceShapes)
      if (typeof saved.chartTool === 'string') setChartTool(saved.chartTool)
      if (typeof saved.showSignals === 'boolean') setShowSignals(saved.showSignals)
      if (typeof saved.showCandles === 'boolean') setShowCandles(saved.showCandles)
      if (typeof saved.showStrikeCandles === 'boolean') setShowStrikeCandles(saved.showStrikeCandles)
      if (typeof saved.showAllOptions === 'boolean') setShowAllOptions(saved.showAllOptions)
      if (typeof saved.strikeRenderMode === 'string' && ['lines', 'candles'].includes(saved.strikeRenderMode)) {
        setStrikeRenderMode(saved.strikeRenderMode)
      }
      if (typeof saved.viewPreset === 'string' && VIEW_PRESET_OPTIONS.some((option) => option.id === saved.viewPreset)) {
        setViewPreset(saved.viewPreset)
      }
      if (Array.isArray(saved.selectedStrikeLegIds)) {
        const normalized = saved.selectedStrikeLegIds.filter((id) => STRIKE_LADDER_DEFS.some((definition) => definition.id === id))
        if (normalized.length > 0) setSelectedStrikeLegIds(normalized)
      }
      if (Number.isFinite(Number(saved.entrySignalThreshold))) {
        const normalizedThreshold = Math.min(95, Math.max(40, Number(saved.entrySignalThreshold)))
        setEntrySignalThreshold(normalizedThreshold)
      }
      if (typeof saved.enable3DGate === 'boolean') setEnable3DGate(saved.enable3DGate)
      if (Number.isFinite(Number(saved.decisionPersistenceBars))) {
        setDecisionPersistenceBars(Math.min(6, Math.max(1, Number(saved.decisionPersistenceBars))))
      }
      if (typeof saved.showTradePlanOverlay === 'boolean') setShowTradePlanOverlay(saved.showTradePlanOverlay)
      if (typeof saved.showTradeTicket === 'boolean') setShowTradeTicket(saved.showTradeTicket)
      if (typeof saved.candleType === 'string') setCandleType(saved.candleType)
      if (typeof saved.fvgMode === 'string') setFvgMode(saved.fvgMode)
      if (typeof saved.liquiditySweepMode === 'string') {
        const normalizedMode = saved.liquiditySweepMode === 'on'
          ? 'basic'
          : (['off', 'basic', 'strict'].includes(saved.liquiditySweepMode) ? saved.liquiditySweepMode : 'off')
        setLiquiditySweepMode(normalizedMode)
      }
      if (typeof saved.requireEntryConfirmation === 'boolean') {
        setRequireEntryConfirmation(saved.requireEntryConfirmation)
      }
      if (saved.ticket && typeof saved.ticket === 'object') {
        const ticket = saved.ticket
        if (typeof ticket.side === 'string') setTicketSide(ticket.side)
        if (typeof ticket.orderType === 'string') setTicketOrderType(ticket.orderType)
        if (typeof ticket.entry === 'string') setTicketEntry(ticket.entry)
        if (typeof ticket.stop === 'string') setTicketStop(ticket.stop)
        if (typeof ticket.target === 'string') setTicketTarget(ticket.target)
        if (Number.isFinite(Number(ticket.qty))) setTicketQty(Math.max(1, Number(ticket.qty)))
        if (Number.isFinite(Number(ticket.riskBudget))) setTicketRiskBudget(Math.max(0, Number(ticket.riskBudget)))
        if (typeof ticket.tag === 'string') setTicketTag(ticket.tag)
        if (typeof ticket.instrumentLabel === 'string') setTicketInstrumentLabel(ticket.instrumentLabel)
        if (typeof ticket.instrumentBucketLabel === 'string') setTicketInstrumentBucketLabel(ticket.instrumentBucketLabel)
        if (typeof ticket.instrumentDecision === 'string') setTicketInstrumentDecision(ticket.instrumentDecision)
        if (typeof ticket.structureSummary === 'string') setTicketStructureSummary(ticket.structureSummary)
        if (ticket.chartPlan && typeof ticket.chartPlan === 'object') setTicketChartPlan(ticket.chartPlan)
      }
      if (Array.isArray(saved.ticketSnapshots)) setTicketSnapshots(saved.ticketSnapshots)
    } catch (err) {
      // Ignore malformed saved workspace settings.
    }
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const payload = {
      manualTradeMarks,
      workspaceShapes,
      chartTool,
      showSignals,
      showCandles,
      showStrikeCandles,
      showAllOptions,
      strikeRenderMode,
      viewPreset,
      selectedStrikeLegIds,
      entrySignalThreshold,
      enable3DGate,
      decisionPersistenceBars,
      showTradePlanOverlay,
      showTradeTicket,
      candleType,
      fvgMode,
      liquiditySweepMode,
      requireEntryConfirmation,
      ticket: {
        side: ticketSide,
        orderType: ticketOrderType,
        entry: ticketEntry,
        stop: ticketStop,
        target: ticketTarget,
        qty: ticketQty,
        riskBudget: ticketRiskBudget,
        tag: ticketTag,
        instrumentLabel: ticketInstrumentLabel,
        instrumentBucketLabel: ticketInstrumentBucketLabel,
        instrumentDecision: ticketInstrumentDecision,
        structureSummary: ticketStructureSummary,
        chartPlan: ticketChartPlan,
      },
      ticketSnapshots,
    }
    window.localStorage.setItem(SWING_WORKSPACE_STORAGE_KEY, JSON.stringify(payload))
  }, [
    candleType,
    chartTool,
    fvgMode,
    liquiditySweepMode,
    manualTradeMarks,
    requireEntryConfirmation,
    showCandles,
    showStrikeCandles,
    showAllOptions,
    strikeRenderMode,
    viewPreset,
    selectedStrikeLegIds,
    entrySignalThreshold,
    enable3DGate,
    decisionPersistenceBars,
    showTradePlanOverlay,
    showTradeTicket,
    showSignals,
    ticketEntry,
    ticketInstrumentBucketLabel,
    ticketInstrumentDecision,
    ticketInstrumentLabel,
    ticketStructureSummary,
    ticketChartPlan,
    ticketOrderType,
    ticketQty,
    ticketRiskBudget,
    ticketSide,
    ticketSnapshots,
    ticketStop,
    ticketTag,
    ticketTarget,
    workspaceShapes,
  ])

  const handleWorkspaceShapesChange = useCallback((shapes) => {
    const next = Array.isArray(shapes) ? shapes : []
    setWorkspaceShapes((prev) => {
      if (JSON.stringify(prev) === JSON.stringify(next)) return prev
      return next
    })
  }, [])

  const toggleStrikeLegSelection = useCallback((legId) => {
    setSelectedStrikeLegIds((prev) => {
      if (prev.includes(legId)) {
        if (prev.length === 1) return prev
        return prev.filter((id) => id !== legId)
      }
      return [...prev, legId]
    })
  }, [])

  const handleChartClick = useCallback((event) => {
    const point = event?.points?.[0]
    if (!point) return

    const x = point.x
    if (x) setSelectedEventTime(String(x))

    if (tradeTagMode === 'none') return

    let y = Number(point.y)
    if (!Number.isFinite(y) && Number.isFinite(Number(point.close))) {
      y = Number(point.close)
    }
    if (
      !Number.isFinite(y)
      && Number.isInteger(point.pointNumber)
      && Array.isArray(point?.data?.close)
      && Number.isFinite(Number(point.data.close[point.pointNumber]))
    ) {
      y = Number(point.data.close[point.pointNumber])
    }

    if (!x || !Number.isFinite(y)) return

    const side = tradeTagMode === 'buy' ? 'BUY' : 'SELL'
    const note = tradeTagNote.trim()
    setManualTradeMarks((prev) => [
      ...prev,
      {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        side,
        x: String(x),
        y,
        note,
      },
    ])
    setTradeTagMode('none')
  }, [tradeTagMode, tradeTagNote])

  const clearManualTradeMarks = useCallback(() => {
    setManualTradeMarks([])
  }, [])

  const clearWorkspaceDrawings = useCallback(() => {
    setWorkspaceShapes([])
  }, [])

  const clearTicketInstrument = useCallback(() => {
    setTicketInstrumentLabel('')
    setTicketInstrumentBucketLabel('')
    setTicketInstrumentDecision('')
    setTicketStructureSummary('')
    setTicketChartPlan(null)
  }, [])

  const loadTicketFromLastMark = useCallback(() => {
    if (!manualTradeMarks.length) return
    const last = manualTradeMarks[manualTradeMarks.length - 1]
    setTicketSide(last.side === 'SELL' ? 'SELL' : 'BUY')
    setTicketEntry(Number(last.y).toFixed(2))
    if (last.note) setTicketTag(last.note)
    clearTicketInstrument()
  }, [clearTicketInstrument, manualTradeMarks])

  const loadTicketFromSpot = useCallback(() => {
    if (!Number.isFinite(latestSpot)) return
    setTicketEntry(Number(latestSpot).toFixed(2))
    clearTicketInstrument()
  }, [clearTicketInstrument, latestSpot])

  const loadTicketFromSuggestedTrade = useCallback(() => {
    if (!latestSuggestedTrade || !latestDecision3D || !Number.isFinite(Number(latestSuggestedTrade.mid))) return
    const levels = computeStructuredTicketLevels({
      trade: latestSuggestedTrade,
      decision: latestDecision3D.decision,
      spot: latestDecision3D.spot,
      swingHighs,
      swingLows,
      snapshots: decisionInputSnapshots,
    })
    setTicketSide('BUY')
    setTicketOrderType('LIMIT')
    setTicketEntry(Number(latestSuggestedTrade.mid).toFixed(2))
    if (levels) {
      setTicketStop(Number(levels.stop).toFixed(2))
      setTicketTarget(Number(levels.target).toFixed(2))
      setTicketStructureSummary(
        `Spot SL ${formatStrikeValue(levels.stopSpot)} | Spot T ${formatStrikeValue(levels.targetSpot)} | Delta ${formatOptionalNumber(levels.deltaAbs, 2)}`
      )
      setTicketChartPlan({
        decision: latestDecision3D.decision,
        instrumentLabel: latestSuggestedTrade.instrumentLabel || '',
        contractLabel: latestSuggestedTrade.contractLabel || latestSuggestedTrade.instrumentLabel || '',
        bucketLabel: latestSuggestedTrade.bucketLabel || '',
        bucketShortLabel: latestSuggestedTrade.label || '',
        side: latestSuggestedTrade.side || '',
        strike: Number.isFinite(Number(latestSuggestedTrade.strike)) ? Number(latestSuggestedTrade.strike) : null,
        optionExpiry: latestSuggestedTrade.optionExpiry || '',
        optionExpiryCode: latestSuggestedTrade.optionExpiryCode || '',
        entryTime: latestDecision3D.minuteKey || latestDecision3D.key || latestDecision3D.time || '',
        entrySpot: Number(latestDecision3D.spot),
        stopSpot: Number(levels.stopSpot),
        targetSpot: Number(levels.targetSpot),
      })
      setShowTradePlanOverlay(true)
    } else {
      setTicketStop('')
      setTicketTarget('')
      setTicketStructureSummary('')
      setTicketChartPlan(null)
    }
    setTicketInstrumentLabel(latestSuggestedTrade.contractLabel || latestSuggestedTrade.instrumentLabel || '')
    setTicketInstrumentBucketLabel(latestSuggestedTrade.bucketLabel || '')
    setTicketInstrumentDecision(latestDecision3D.decision || '')
    if (!ticketTag.trim()) {
      setTicketTag(`3D ${latestDecision3D.decision} ${latestSuggestedTrade.contractLabel || latestSuggestedTrade.instrumentLabel}`)
    }
  }, [decisionInputSnapshots, latestDecision3D, latestSuggestedTrade, swingHighs, swingLows, ticketTag])

  const clearTradeTicket = useCallback(() => {
    setTicketSide('BUY')
    setTicketOrderType('LIMIT')
    setTicketEntry('')
    setTicketStop('')
    setTicketTarget('')
    setTicketQty(1)
    setTicketRiskBudget(1000)
    setTicketTag('')
    clearTicketInstrument()
  }, [clearTicketInstrument])

  const saveTradeTicketSnapshot = useCallback(() => {
    if (!ticketMetrics.isValid || !Number.isFinite(ticketMetrics.entry) || !Number.isFinite(ticketMetrics.stop) || !Number.isFinite(ticketMetrics.target)) return
    setTicketSnapshots((prev) => {
      const next = [
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          ts: new Date().toISOString(),
          symbol: currentSymbol,
          side: ticketSide,
          orderType: ticketOrderType,
          entry: ticketMetrics.entry,
          stop: ticketMetrics.stop,
          target: ticketMetrics.target,
          qty: ticketMetrics.qty,
          riskBudget: ticketMetrics.budget,
          instrumentLabel: ticketInstrumentLabel,
          instrumentBucketLabel: ticketInstrumentBucketLabel,
          instrumentDecision: ticketInstrumentDecision,
          structureSummary: ticketStructureSummary,
          chartPlan: ticketChartPlan,
          rr: ticketMetrics.rr,
          maxLoss: ticketMetrics.maxLoss,
          maxProfit: ticketMetrics.maxProfit,
          tag: ticketTag.trim(),
        },
        ...prev,
      ]
      return next.slice(0, 10)
    })
  }, [currentSymbol, ticketChartPlan, ticketInstrumentBucketLabel, ticketInstrumentDecision, ticketInstrumentLabel, ticketMetrics, ticketOrderType, ticketSide, ticketStructureSummary, ticketTag])

  const loadTicketSnapshot = useCallback((snapshot) => {
    if (!snapshot) return
    setTicketSide(snapshot.side === 'SELL' ? 'SELL' : 'BUY')
    setTicketOrderType(snapshot.orderType || 'LIMIT')
    setTicketEntry(Number(snapshot.entry).toFixed(2))
    setTicketStop(Number(snapshot.stop).toFixed(2))
    setTicketTarget(Number(snapshot.target).toFixed(2))
    setTicketQty(Math.max(1, Number(snapshot.qty) || 1))
    setTicketRiskBudget(Math.max(0, Number(snapshot.riskBudget) || 0))
    setTicketTag(snapshot.tag || '')
    setTicketInstrumentLabel(snapshot.instrumentLabel || '')
    setTicketInstrumentBucketLabel(snapshot.instrumentBucketLabel || '')
    setTicketInstrumentDecision(snapshot.instrumentDecision || '')
    setTicketStructureSummary(snapshot.structureSummary || '')
    setTicketChartPlan(snapshot.chartPlan && typeof snapshot.chartPlan === 'object' ? snapshot.chartPlan : null)
  }, [])

  const resetWorkspace = useCallback(() => {
    setChartTool('pan')
    setTradeTagMode('none')
    setTradeTagNote('')
    setManualTradeMarks([])
    setWorkspaceShapes([])
    setViewPreset('signals')
    setSelectedStrikeLegIds(DEFAULT_STRIKE_LEG_IDS)
    setEntrySignalThreshold(62)
    setDecisionPersistenceBars(DECISION_3D_SETTINGS.persistenceBars)
    setShowStrikeCandles(true)
    setStrikeRenderMode('lines')
    setShowAllOptions(true)
    setEnable3DGate(true)
    setShowTradePlanOverlay(true)
    setShowTradeTicket(true)
    setSelectedEventTime('')
    setTicketSnapshots([])
    clearTradeTicket()
    if (typeof window !== 'undefined') {
      window.localStorage.removeItem(SWING_WORKSPACE_STORAGE_KEY)
    }
  }, [clearTradeTicket])

  const exportData = () => {
    const all = [...swingHighs, ...swingLows].sort((a, b) => a.index - b.index)
    const csv = 'time,spot,type,grade,score,signals,pcr,vix,volume,delta,outcome\n' +
      all.map(p => `${p.time},${p.spot},${p.type},${p.grade},${p.score},"${(p.signals || []).join('; ')}",${p.details?.pcr || ''},${p.details?.vix || ''},${p.details?.volume || ''},${p.details?.delta || ''},${p.details?.outcome || ''}`).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob)
    const exportBase = (mode === 'live' ? `live_${liveIndex}_${liveDate || 'latest'}` : fileName || 'historical')
      .replace('.csv', '')
    a.download = `expert_swings_${exportBase}.csv`; a.click()
  }

  return (
    <div className="p-6 bg-slate-950 min-h-screen text-white">
      <div className="max-w-7xl mx-auto">
        <div className="mb-6">
          <h1 className="text-2xl font-bold flex items-center gap-3">
            <Target className="w-8 h-8 text-cyan-400" />
            Expert Swing Analysis (Plotly)
          </h1>
          <p className="text-slate-400 mt-1">Scientific-grade charts with mouse pan/zoom, crosshair, range selector</p>
        </div>

        {/* Mode Switch */}
        <div className="bg-slate-800/50 rounded-xl p-4 mb-4 border border-slate-700 flex items-center gap-3">
          <button
            onClick={() => setMode('historical')}
            className={`px-4 py-2 rounded-lg text-sm font-medium ${mode === 'historical' ? 'bg-cyan-600 text-white' : 'bg-slate-700 text-slate-300'}`}
          >
            Historical Mode
          </button>
          <button
            onClick={() => setMode('live')}
            className={`px-4 py-2 rounded-lg text-sm font-medium ${mode === 'live' ? 'bg-emerald-600 text-white' : 'bg-slate-700 text-slate-300'}`}
          >
            Live Mode
          </button>
          <span className="text-xs text-slate-400">
            {mode === 'live' ? 'Auto feed enabled' : 'Upload old data files'}
          </span>
        </div>

        {/* Data Source Controls */}
        <div className="bg-slate-800/50 rounded-xl p-5 mb-6 border border-slate-700">
          {mode === 'historical' ? (
            <div className="flex flex-wrap items-center gap-4">
              <label className="flex items-center gap-2 px-5 py-2.5 bg-cyan-600 hover:bg-cyan-500 rounded-lg cursor-pointer font-medium">
                <Upload className="w-5 h-5" />Signal CSV
                <input type="file" accept=".csv" onChange={handleUpload} className="hidden" />
              </label>
              {fileName && <span className="text-slate-300">{fileName} | <span className="text-cyan-400">{data.length} pts</span></span>}

              <div className="border-l border-slate-600 h-8 mx-2" />

              <label className="flex items-center gap-2 px-5 py-2.5 bg-amber-600 hover:bg-amber-500 rounded-lg cursor-pointer font-medium">
                <BarChart2 className="w-5 h-5" />OHLCV CSV
                <input type="file" accept=".csv" onChange={handleOhlcvUpload} className="hidden" />
              </label>
              {ohlcvFileName && <span className="text-slate-300">{ohlcvFileName} | <span className="text-amber-400">{ohlcvData.length} candles</span></span>}

              {loading && <RefreshCw className="w-5 h-5 animate-spin text-cyan-400" />}
              {data.length > 0 && (
                <button onClick={exportData} className="ml-auto flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-sm">
                  <Download className="w-4 h-4" />Export
                </button>
              )}
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-4">
              <div className="flex items-center gap-2">
                <span className="text-slate-300 text-sm">Index</span>
                <select
                  value={liveIndex}
                  onChange={(e) => setLiveIndex(e.target.value)}
                  className="bg-slate-900 border border-slate-600 rounded px-3 py-2 text-sm"
                >
                  <option value="NIFTY50">NIFTY50</option>
                  <option value="BANKNIFTY">BANKNIFTY</option>
                  <option value="SENSEX">SENSEX</option>
                </select>
              </div>

              <div className="flex items-center gap-2">
                <span className="text-slate-300 text-sm">Interval</span>
                <input
                  type="number"
                  min={1}
                  max={60}
                  value={liveIntervalSec}
                  onChange={(e) => setLiveIntervalSec(Math.min(60, Math.max(1, Number(e.target.value) || 1)))}
                  className="w-20 bg-slate-900 border border-slate-600 rounded px-2 py-2 text-sm"
                />
                <span className="text-slate-400 text-sm">sec</span>
              </div>

              <button onClick={() => fetchLiveNow({ background: false })} className="px-4 py-2 bg-sky-700 hover:bg-sky-600 rounded-lg text-sm">
                Refresh Now
              </button>

              {loading && <RefreshCw className="w-5 h-5 animate-spin text-emerald-400" />}

              {data.length > 0 && (
                <button onClick={exportData} className="ml-auto flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-sm">
                  <Download className="w-4 h-4" />Export
                </button>
              )}

              <div className="basis-full text-xs text-slate-400 mt-1">
                Source: {liveFileName} | Rows: <span className="text-cyan-400">{data.length}</span> | Candles:{' '}
                <span className="text-amber-400">{ohlcvData.length}</span> | Interval: {liveIntervalSec}s | Status:{' '}
                <span className={liveStatus === 'error' ? 'text-red-400' : 'text-emerald-400'}>{liveStatus}</span>
                {lastLiveUpdate && <> | Last update: {lastLiveUpdate.toLocaleTimeString()}</>}
              </div>
              <div className="basis-full text-xs text-slate-500">{liveCandlesStatus}</div>
            </div>
          )}
          {error && <div className="mt-3 p-3 bg-red-900/50 border border-red-700 rounded text-red-300">{error}</div>}
        </div>

        {data.length > 0 && showAllOptions && optionLadderComparison.rows.length > 0 && (
          <div className="bg-slate-800/50 rounded-xl p-4 mb-6 border border-slate-700">
            <div className="flex flex-wrap items-center gap-3">
              <h3 className="text-sm font-semibold text-slate-100">Index vs Option Ladder (ATM + 1..4 OTM)</h3>
              <span className="text-[11px] px-2 py-1 rounded bg-slate-900 border border-slate-700 text-slate-300">
                Strike Step {optionStrikeStep}
              </span>
              <span className="text-[11px] px-2 py-1 rounded bg-slate-900 border border-slate-700 text-slate-300">
                Selected Legs {selectedStrikeLegIds.length}
              </span>
            </div>
            <div className="mt-2 text-xs text-slate-400">
              Snapshots: <span className="text-cyan-300">{optionLadderComparison.totalSnapshots}</span>
              {' '}| Spot: <span className="text-slate-200">{formatOptionalNumber(optionLadderComparison.spotStart, 2)}</span>
              {' '}→ <span className="text-slate-200">{formatOptionalNumber(optionLadderComparison.spotLatest, 2)}</span>
              {' '}| Spot Change: <span className={optionLadderComparison.spotChangePct >= 0 ? 'text-emerald-300' : 'text-rose-300'}>
                {Number.isFinite(optionLadderComparison.spotChangePct)
                  ? `${formatOptionalNumber(optionLadderComparison.spotChangePct, 2)}%`
                  : '-'}
              </span>
              {' '}| Option Price Basis: Mid (Bid/Ask), fallback Entry
            </div>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-slate-900/70">
                  <tr className="text-slate-400 border-b border-slate-700">
                    <th className="p-2 text-left">Leg</th>
                    <th className="p-2 text-center">Coverage</th>
                    <th className="p-2 text-right">Latest</th>
                    <th className="p-2 text-right">Change %</th>
                    <th className="p-2 text-right">Avg IV</th>
                    <th className="p-2 text-right">Avg Δ</th>
                    <th className="p-2 text-right">Avg Vol</th>
                    <th className="p-2 text-right">Avg OI</th>
                    <th className="p-2 text-right">Corr(ΔSpot, ΔLeg)</th>
                  </tr>
                </thead>
                <tbody>
                  {optionLadderComparison.rows.map((row) => {
                    const selected = selectedStrikeLegIds.includes(row.id)
                    return (
                    <tr key={row.label} className={`border-b border-slate-700/50 hover:bg-slate-700/30 ${selected ? 'bg-slate-700/40' : ''}`}>
                      <td className={`p-2 font-medium ${row.side === 'CE' ? 'text-emerald-300' : 'text-rose-300'}`}>{row.label}</td>
                      <td className="p-2 text-center text-slate-300">{row.coverage}</td>
                      <td className="p-2 text-right text-slate-200">{formatOptionalNumber(row.latest, 2)}</td>
                      <td className={`p-2 text-right ${row.changePct >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>
                        {Number.isFinite(row.changePct) ? `${formatOptionalNumber(row.changePct, 2)}%` : '-'}
                      </td>
                      <td className="p-2 text-right text-slate-300">{formatOptionalNumber(row.avgIv, 2)}</td>
                      <td className="p-2 text-right text-slate-300">{formatOptionalNumber(row.avgDelta, 3)}</td>
                      <td className="p-2 text-right text-slate-300">{formatOptionalNumber(row.avgVolume, 0)}</td>
                      <td className="p-2 text-right text-slate-300">{formatOptionalNumber(row.avgOi, 0)}</td>
                      <td className="p-2 text-right text-cyan-300">
                        {formatOptionalNumber(row.corrToSpot, 3)}
                        {row.corrPoints > 0 && <span className="text-slate-500"> ({row.corrPoints})</span>}
                      </td>
                    </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {showAllOptions && decision3DSeries.length > 0 && latestDecision3D && (
          <div className="bg-slate-800/50 rounded-xl p-4 mb-6 border border-slate-700">
            <div className="flex flex-wrap items-center gap-3">
              <h3 className="text-sm font-semibold text-slate-100">3D Decision Engine (X/Y/Z)</h3>
              <span className="text-[11px] px-2 py-1 rounded bg-slate-900 border border-slate-700 text-slate-300">
                X=Structure | Y=Options Flow | Z=Quality
              </span>
            </div>
            <div className="mt-2 text-xs text-slate-400">
              This is a directional filter over the selected dynamic ATM/OTM legs, not a direct order signal.
              Action changes only after persistence; the suggested leg resolves the current exact strike for the active bucket.
            </div>
            <div className="mt-2 text-xs text-slate-500">
              Selected legs: <span className="text-slate-300">{selectedStrikeLabels || 'None'}</span>
              {' '}| Decision bars: <span className="text-slate-300">{decisionBarModeLabel}</span>
              {' '}| Inputs: <span className="text-slate-300">{decisionInputSnapshots.length}</span>
              {' '}/ Raw snapshots: <span className="text-slate-300">{optionSnapshots.length}</span>
            </div>
            <div className="mt-3 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-8 gap-2 text-xs">
              <div className="rounded border border-slate-700 bg-slate-900/60 p-2">
                <div className="text-slate-500">Action</div>
                <div className={latestDecision3D.decision === 'LONG' ? 'text-emerald-300 font-semibold' : latestDecision3D.decision === 'SHORT' ? 'text-rose-300 font-semibold' : 'text-amber-300 font-semibold'}>
                  {latestDecision3D.decision}
                </div>
              </div>
              <div className="rounded border border-slate-700 bg-slate-900/60 p-2">
                <div className="text-slate-500">Bias</div>
                <div className={latestDecision3D.biasDecision === 'LONG' ? 'text-emerald-300 font-semibold' : latestDecision3D.biasDecision === 'SHORT' ? 'text-rose-300 font-semibold' : 'text-amber-300 font-semibold'}>
                  {latestDecision3D.biasDecision}
                </div>
              </div>
              <div className="rounded border border-slate-700 bg-slate-900/60 p-2">
                <div className="text-slate-500">Confidence</div>
                <div className="text-cyan-300 font-semibold">{formatOptionalNumber(latestDecision3D.confidence, 1)}%</div>
              </div>
              <div className="rounded border border-slate-700 bg-slate-900/60 p-2">
                <div className="text-slate-500">Suggested Leg</div>
                {latestDecision3D.suggestedTrade ? (
	                  <>
	                    <div className={`font-semibold ${getOptionSideTextClass(getTradeDisplayLabel(latestDecision3D.suggestedTrade), latestDecision3D.suggestedTrade.side)}`}>
	                      {getTradeDisplayLabel(latestDecision3D.suggestedTrade) || latestDecision3D.suggestedTrade.instrumentLabel}
	                    </div>
	                    <div className="text-[11px] text-slate-500">
	                      {latestDecision3D.suggestedTrade.label} @ {formatOptionalNumber(latestDecision3D.suggestedTrade.mid, 2)}
	                    </div>
                  </>
                ) : (
                  <div className="text-amber-300 font-semibold">Wait / No trade</div>
                )}
              </div>
              <div className="rounded border border-slate-700 bg-slate-900/60 p-2">
                <div className="text-slate-500">Persistence</div>
                <div className="text-slate-200 font-semibold">
                  {latestDecision3D.stabilityBars > 0 ? `${latestDecision3D.stabilityBars} bars` : '0 bars'}
                </div>
              </div>
              <div className="rounded border border-slate-700 bg-slate-900/60 p-2">
                <div className="text-slate-500">X (Structure)</div>
                <div className={`${latestDecision3D.x >= 0 ? 'text-emerald-300' : 'text-rose-300'} font-semibold`}>{formatOptionalNumber(latestDecision3D.x, 1)}</div>
              </div>
              <div className="rounded border border-slate-700 bg-slate-900/60 p-2">
                <div className="text-slate-500">Y (Flow)</div>
                <div className={`${latestDecision3D.y >= 0 ? 'text-emerald-300' : 'text-rose-300'} font-semibold`}>{formatOptionalNumber(latestDecision3D.y, 1)}</div>
              </div>
              <div className="rounded border border-slate-700 bg-slate-900/60 p-2">
                <div className="text-slate-500">Z (Quality)</div>
                <div className={`${latestDecision3D.z >= 0 ? 'text-emerald-300' : 'text-rose-300'} font-semibold`}>{formatOptionalNumber(latestDecision3D.z, 1)}</div>
              </div>
              <div className="rounded border border-slate-700 bg-slate-900/60 p-2">
                <div className="text-slate-500">Composite</div>
                <div className={`${latestDecision3D.composite >= 0 ? 'text-emerald-300' : 'text-rose-300'} font-semibold`}>{formatOptionalNumber(latestDecision3D.composite, 1)}</div>
              </div>
            </div>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-slate-900/70">
                  <tr className="text-slate-400 border-b border-slate-700">
                    <th className="p-2 text-left">Time</th>
                    <th className="p-2 text-right">Bias</th>
                    <th className="p-2 text-right">Action</th>
                    <th className="p-2 text-left">Suggested Leg</th>
                    <th className="p-2 text-right">Price</th>
                    <th className="p-2 text-right">Bars</th>
                    <th className="p-2 text-right">X</th>
                    <th className="p-2 text-right">Y</th>
                    <th className="p-2 text-right">Z</th>
                    <th className="p-2 text-right">Composite</th>
                    <th className="p-2 text-right">Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {decision3DRecent.map((state) => (
                    <tr key={state.key} className="border-b border-slate-700/50">
                      <td className="p-2 text-slate-300">{state.time || state.key}</td>
                      <td className={`p-2 text-right ${state.biasDecision === 'LONG' ? 'text-emerald-300' : state.biasDecision === 'SHORT' ? 'text-rose-300' : 'text-amber-300'}`}>
                        {state.biasDecision}
                      </td>
                      <td className={`p-2 text-right ${state.decision === 'LONG' ? 'text-emerald-300' : state.decision === 'SHORT' ? 'text-rose-300' : 'text-amber-300'}`}>
                        {state.decision}
                      </td>
	                      <td className={`p-2 ${getOptionSideTextClass(getTradeDisplayLabel(state.suggestedTrade), state.suggestedTrade?.side)}`}>
	                        {getTradeDisplayLabel(state.suggestedTrade) || '-'}
	                      </td>
                      <td className="p-2 text-right text-slate-300">{formatOptionalNumber(state.suggestedTrade?.mid, 2)}</td>
                      <td className="p-2 text-right text-slate-300">{state.stabilityBars || 0}</td>
                      <td className={`p-2 text-right ${state.x >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>{formatOptionalNumber(state.x, 1)}</td>
                      <td className={`p-2 text-right ${state.y >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>{formatOptionalNumber(state.y, 1)}</td>
                      <td className={`p-2 text-right ${state.z >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>{formatOptionalNumber(state.z, 1)}</td>
                      <td className={`p-2 text-right ${state.composite >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>{formatOptionalNumber(state.composite, 1)}</td>
                      <td className="p-2 text-right text-cyan-300">{formatOptionalNumber(state.confidence, 1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {data.length > 0 && showAllOptions && qualifiedConfluenceSignals.length > 0 && (
          <div className="bg-slate-800/50 rounded-xl p-4 mb-6 border border-slate-700">
            <div className="flex flex-wrap items-center gap-3">
              <h3 className="text-sm font-semibold text-slate-100">Qualified Entry Setups</h3>
              <span className="text-[11px] px-2 py-1 rounded bg-slate-900 border border-slate-700 text-fuchsia-200">
                Threshold {Math.round(entrySignalThreshold)}
              </span>
            </div>
            <div className="mt-2 text-xs text-slate-400">
              Latest qualified setups are plotted as BUY/SELL arrows with confluence scores and, when available, the 3D engine's current strike suggestion.
            </div>
            <div className="mt-3 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
              {latestConfluenceSignals.map((signal, idx) => (
                <div key={`${signal.time || signal.datetime || idx}-${signal.type}`} className="rounded border border-slate-700 bg-slate-900/60 p-2 text-xs">
                  <div className="flex items-center justify-between">
                    <span className={signal.direction === 'BUY' ? 'text-emerald-300 font-semibold' : 'text-rose-300 font-semibold'}>
                      {signal.direction}
                    </span>
                    <span className="text-fuchsia-300 font-semibold">{formatOptionalNumber(signal.confluenceScore, 1)}</span>
                  </div>
                  <div className="mt-1 text-slate-300">{signal.time || signal.datetime || '-'}</div>
                  <div className="mt-1 text-slate-500">
                    Grade {signal.grade || '-'} | Spot {formatOptionalNumber(signal.spot, 2)}
                  </div>
	                  {signal.suggestedTrade && (
	                    <div className="mt-1 text-slate-400">
	                      Suggested: <span className={getOptionSideTextClass(getTradeDisplayLabel(signal.suggestedTrade), signal.suggestedTrade.side)}>
	                        {getTradeDisplayLabel(signal.suggestedTrade) || signal.suggestedTrade.instrumentLabel}
	                      </span>
	                      {' '}@ {formatOptionalNumber(signal.suggestedTrade.mid, 2)}
	                    </div>
                  )}
                  {signal.decision3D && (
                    <div className="mt-1 text-slate-500">
                      3D: <span className={signal.decision3D.decision === 'LONG' ? 'text-emerald-300' : signal.decision3D.decision === 'SHORT' ? 'text-rose-300' : 'text-amber-300'}>
                        {signal.decision3D.decision}
                      </span>
                      {' '}| Conf {formatOptionalNumber(signal.decision3D.confidence, 1)}%
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {showAllOptions && eventStudy && (
          <div className="bg-slate-800/50 rounded-xl p-4 mb-6 border border-slate-700">
            <div className="flex flex-wrap items-center gap-3">
              <h3 className="text-sm font-semibold text-slate-100">Event Study (Click Chart)</h3>
              <span className="text-[11px] px-2 py-1 rounded bg-slate-900 border border-slate-700 text-slate-300">
                {eventStudy.beforeTime} {'->'} {eventStudy.selectedTime} {'->'} {eventStudy.afterTime}
              </span>
            </div>
            <div className="mt-2 text-xs text-slate-400">
              Spot change into event:{' '}
              <span className={Number(eventStudy.spotIntoEventPct) >= 0 ? 'text-emerald-300' : 'text-rose-300'}>
                {formatOptionalNumber(eventStudy.spotIntoEventPct, 2)}%
              </span>
              {' '}| Spot change after event:{' '}
              <span className={Number(eventStudy.spotAfterEventPct) >= 0 ? 'text-emerald-300' : 'text-rose-300'}>
                {formatOptionalNumber(eventStudy.spotAfterEventPct, 2)}%
              </span>
            </div>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-slate-900/70">
                  <tr className="text-slate-400 border-b border-slate-700">
                    <th className="p-2 text-left">Leg</th>
                    <th className="p-2 text-right">Before</th>
                    <th className="p-2 text-right">At Event</th>
                    <th className="p-2 text-right">After</th>
                    <th className="p-2 text-right">Into Event %</th>
                    <th className="p-2 text-right">After Event %</th>
                  </tr>
                </thead>
                <tbody>
                  {eventStudy.rows.map((row) => (
                    <tr key={row.id} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                      <td className={`p-2 font-medium ${row.side === 'CE' ? 'text-emerald-300' : 'text-rose-300'}`}>{row.label}</td>
                      <td className="p-2 text-right text-slate-300">{formatOptionalNumber(row.beforePrice, 2)}</td>
                      <td className="p-2 text-right text-slate-200">{formatOptionalNumber(row.centerPrice, 2)}</td>
                      <td className="p-2 text-right text-slate-300">{formatOptionalNumber(row.afterPrice, 2)}</td>
                      <td className={`p-2 text-right ${Number(row.intoEventPct) >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>
                        {Number.isFinite(Number(row.intoEventPct)) ? `${formatOptionalNumber(row.intoEventPct, 2)}%` : '-'}
                      </td>
                      <td className={`p-2 text-right ${Number(row.afterEventPct) >= 0 ? 'text-emerald-300' : 'text-rose-300'}`}>
                        {Number.isFinite(Number(row.afterEventPct)) ? `${formatOptionalNumber(row.afterEventPct, 2)}%` : '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        {showAllOptions && !eventStudy && optionRows.length > 0 && (
          <div className="bg-slate-800/40 rounded-xl p-3 mb-6 border border-slate-700 text-xs text-slate-400">
            Click any candle/marker on the chart to generate strike-level event study around that timestamp.
          </div>
        )}

        {/* Tabs */}
        {data.length > 0 && showAllOptions && (
          <div className="flex gap-2 mb-4">
            {[
              { id: 'chart', icon: BarChart2, label: 'Chart' },
              { id: 'top', icon: Target, label: 'Top 10' },
              { id: 'patterns', icon: Repeat, label: 'Patterns' },
              { id: 'all', icon: Activity, label: `All (${swingHighs.length + swingLows.length})` },
            ].map(t => (
              <button key={t.id} onClick={() => setActiveTab(t.id)}
                className={`px-4 py-2 rounded-lg flex items-center gap-2 ${activeTab === t.id ? 'bg-cyan-600' : 'bg-slate-800 text-slate-400 hover:bg-slate-700'}`}>
                <t.icon className="w-4 h-4" />{t.label}
              </button>
            ))}
          </div>
        )}

        {/* Chart */}
        {data.length > 0 && activeTab === 'chart' && (
          <div
            ref={chartPanelRef}
            className={`bg-slate-800/50 rounded-xl border border-slate-700 ${isChartFullscreen ? 'p-3 h-screen max-h-screen overflow-y-auto' : 'p-4 mb-6'}`}
          >
	            <div className="mb-4 rounded-lg border border-slate-700 bg-slate-900/60 p-3">
	              <div className="flex flex-wrap items-center gap-2">
	                {mode === 'live' && (
	                  <div className="flex items-center gap-2 rounded-md border border-slate-700 bg-slate-900/70 px-2 py-1">
	                    <span className="text-[11px] uppercase tracking-wide text-slate-400">Index</span>
	                    <select
	                      value={liveIndex}
	                      onChange={(e) => setLiveIndex(e.target.value)}
	                      className="bg-slate-950 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200"
	                    >
	                      <option value="NIFTY50">NIFTY50</option>
	                      <option value="BANKNIFTY">BANKNIFTY</option>
	                      <option value="SENSEX">SENSEX</option>
	                    </select>
	                    <button
	                      onClick={() => fetchLiveNow({ background: false })}
	                      className="px-2 py-1 rounded border border-slate-700 bg-slate-800 text-[11px] text-slate-200 hover:bg-slate-700"
	                    >
	                      Refresh
	                    </button>
	                  </div>
	                )}
	                {showAllOptions && (
	                  <>
	                    <span className="text-xs uppercase tracking-wide text-slate-400">Chart Tools</span>
                    {CHART_TOOL_OPTIONS.map((tool) => (
                      <button
                        key={tool.id}
                        onClick={() => setChartTool(tool.id)}
                        className={`px-2.5 py-1.5 rounded-md text-xs flex items-center gap-1.5 border ${chartTool === tool.id ? 'bg-cyan-600/90 border-cyan-400 text-white' : 'bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700'}`}
                      >
                        <tool.icon className="w-3.5 h-3.5" />
                        {tool.label}
                      </button>
	                    ))}
	                  </>
	                )}
	                <div className="ml-auto flex items-center gap-2">
                  <button
                    onClick={() => setShowAllOptions((prev) => !prev)}
                    className={`px-2.5 py-1.5 rounded-md text-xs border ${showAllOptions ? 'bg-violet-700/80 border-violet-500 text-violet-100' : 'bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700'}`}
                  >
                    {showAllOptions ? 'Hide Options' : 'Show Options'}
                  </button>
                  {showAllOptions && (
                    <button
                      onClick={() => setShowTradeTicket((prev) => !prev)}
                      className={`px-2.5 py-1.5 rounded-md text-xs border ${showTradeTicket ? 'bg-cyan-700/80 border-cyan-500 text-cyan-100' : 'bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700'}`}
                    >
                      Trade Ticket {showTradeTicket ? 'ON' : 'OFF'}
                    </button>
                  )}
                  <button
                    onClick={toggleChartFullscreen}
                    className={`px-2.5 py-1.5 rounded-md text-xs border ${isChartFullscreen ? 'bg-amber-700/80 border-amber-500 text-amber-100' : 'bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700'}`}
                  >
                    {isChartFullscreen ? 'Exit Fullscreen' : 'Fullscreen Graph'}
                  </button>
                  {showAllOptions && <span className="text-xs text-slate-400">Active: <span className="text-cyan-300">{chartTool}</span></span>}
                </div>
              </div>

              {showAllOptions && (
                <>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <span className="text-xs uppercase tracking-wide text-slate-400">Manual Trade Tags</span>
                    <button
                      onClick={() => setTradeTagMode((prev) => (prev === 'buy' ? 'none' : 'buy'))}
                      className={`px-2.5 py-1.5 rounded-md text-xs flex items-center gap-1.5 border ${tradeTagMode === 'buy' ? 'bg-emerald-600 border-emerald-400 text-white' : 'bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700'}`}
                    >
                      <ArrowUpCircle className="w-3.5 h-3.5" />
                      Buy
                    </button>
                    <button
                      onClick={() => setTradeTagMode((prev) => (prev === 'sell' ? 'none' : 'sell'))}
                      className={`px-2.5 py-1.5 rounded-md text-xs flex items-center gap-1.5 border ${tradeTagMode === 'sell' ? 'bg-rose-600 border-rose-400 text-white' : 'bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700'}`}
                    >
                      <ArrowDownCircle className="w-3.5 h-3.5" />
                      Sell
                    </button>
                    <button
                      onClick={() => setTradeTagMode('none')}
                      className="px-2.5 py-1.5 rounded-md text-xs border bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={clearManualTradeMarks}
                      className="px-2.5 py-1.5 rounded-md text-xs flex items-center gap-1.5 border bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      Clear Tags
                    </button>
                    <button
                      onClick={clearWorkspaceDrawings}
                      className="px-2.5 py-1.5 rounded-md text-xs flex items-center gap-1.5 border bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700"
                    >
                      <Eraser className="w-3.5 h-3.5" />
                      Clear Drawings
                    </button>
                    <button
                      onClick={resetWorkspace}
                      className="px-2.5 py-1.5 rounded-md text-xs flex items-center gap-1.5 border bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      Reset Workspace
                    </button>
                    <input
                      value={tradeTagNote}
                      onChange={(e) => setTradeTagNote(e.target.value)}
                      placeholder="Optional note for next tag"
                      className="min-w-[220px] flex-1 bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5 text-xs text-slate-200"
                    />
                    <span className="text-xs text-slate-400">Tags: <span className="text-cyan-300">{manualTradeMarks.length}</span></span>
                    <span className="text-xs text-slate-400">Drawings: <span className="text-cyan-300">{workspaceShapes.length}</span></span>
                  </div>

                  {tradeTagMode !== 'none' && (
                    <div className="mt-2 text-xs text-amber-300">
                      Placement armed for <span className="font-semibold">{tradeTagMode.toUpperCase()}</span>. Click a point on chart to place marker.
                    </div>
                  )}
                  <div className="mt-2 text-[11px] text-slate-500">
                    Workspace auto-saves locally (tools, markers, drawings).
                  </div>
                </>
              )}
            </div>

            {showAllOptions && (
              <div className="mb-4 rounded-lg border border-slate-700 bg-slate-900/60 p-3 flex flex-wrap items-center gap-4">
                <div>
                  <label className="text-slate-400 text-sm">Sensitivity: <span className="text-cyan-400">{(minMove * 100).toFixed(2)}%</span></label>
                  <input type="range" min="5" max="50" value={minMove * 10000} onChange={(e) => setMinMove(parseInt(e.target.value, 10) / 10000)} className="w-32 ml-2" />
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-slate-400 text-sm">View</span>
                  <div className="flex rounded-lg overflow-hidden border border-slate-600">
                    {VIEW_PRESET_OPTIONS.map((option) => (
                      <button
                        key={option.id}
                        onClick={() => setViewPreset(option.id)}
                        className={`px-3 py-1.5 text-sm ${viewPreset === option.id ? 'bg-violet-600 text-white' : 'bg-slate-800 text-slate-300'}`}
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                </div>
                <button onClick={() => setShowSignals(!showSignals)} className={`px-3 py-1.5 rounded text-sm ${showSignals ? 'bg-cyan-600' : 'bg-slate-700'}`}>
                  Annotations {showSignals ? 'ON' : 'OFF'}
                </button>
                {ohlcvData.length > 0 && (
                  <button onClick={() => setShowCandles(!showCandles)} className={`px-3 py-1.5 rounded text-sm ${showCandles ? 'bg-amber-600' : 'bg-slate-700'}`}>
                    Candles {showCandles ? 'ON' : 'OFF'}
                  </button>
                )}
                {optionRows.length > 0 && (
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => setShowStrikeCandles(!showStrikeCandles)}
                      className={`px-3 py-1.5 rounded text-sm ${showStrikeCandles ? 'bg-emerald-600' : 'bg-slate-700'}`}
                    >
                      Strike Plot {showStrikeCandles ? 'ON' : 'OFF'}
                    </button>
                    {showStrikeCandles && (
                      <div className="flex rounded-lg overflow-hidden border border-slate-600">
                        <button
                          onClick={() => setStrikeRenderMode('lines')}
                          className={`px-3 py-1.5 text-sm ${strikeRenderMode === 'lines' ? 'bg-emerald-700 text-white' : 'bg-slate-800 text-slate-300'}`}
                        >
                          Lines
                        </button>
                        <button
                          onClick={() => setStrikeRenderMode('candles')}
                          className={`px-3 py-1.5 text-sm ${strikeRenderMode === 'candles' ? 'bg-emerald-700 text-white' : 'bg-slate-800 text-slate-300'}`}
                        >
                          Candles
                        </button>
                      </div>
                    )}
                  </div>
                )}
                {ohlcvData.length > 0 && showCandles && (
                  <div className="flex items-center gap-2">
                    <span className="text-slate-400 text-sm">Candle Type</span>
                    <div className="flex rounded-lg overflow-hidden border border-slate-600">
                      <button
                        onClick={() => setCandleType('standard')}
                        className={`px-3 py-1.5 text-sm ${candleType === 'standard' ? 'bg-amber-600 text-white' : 'bg-slate-800 text-slate-300'}`}
                      >
                        Standard
                      </button>
                      <button
                        onClick={() => setCandleType('heikin-ashi')}
                        className={`px-3 py-1.5 text-sm ${candleType === 'heikin-ashi' ? 'bg-amber-600 text-white' : 'bg-slate-800 text-slate-300'}`}
                      >
                        Heikin Ashi
                      </button>
                    </div>
                  </div>
                )}
                <div className="flex items-center gap-2">
                  <span className="text-slate-400 text-sm">FVG</span>
                  <div className="flex rounded-lg overflow-hidden border border-slate-600">
                    <button
                      onClick={() => setFvgMode('off')}
                      className={`px-3 py-1.5 text-sm ${fvgMode === 'off' ? 'bg-slate-600 text-white' : 'bg-slate-800 text-slate-300'}`}
                    >
                      Off
                    </button>
                    <button
                      onClick={() => setFvgMode('fvg')}
                      className={`px-3 py-1.5 text-sm ${fvgMode === 'fvg' ? 'bg-cyan-600 text-white' : 'bg-slate-800 text-slate-300'}`}
                    >
                      FVG
                    </button>
                    <button
                      onClick={() => setFvgMode('fvg-plus')}
                      className={`px-3 py-1.5 text-sm ${fvgMode === 'fvg-plus' ? 'bg-cyan-600 text-white' : 'bg-slate-800 text-slate-300'}`}
                    >
                      FVG+
                    </button>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-slate-400 text-sm">Liquidity Sweep</span>
                  <div className="flex rounded-lg overflow-hidden border border-slate-600">
                    <button
                      onClick={() => setLiquiditySweepMode('off')}
                      className={`px-3 py-1.5 text-sm ${liquiditySweepMode === 'off' ? 'bg-slate-600 text-white' : 'bg-slate-800 text-slate-300'}`}
                    >
                      Off
                    </button>
                    <button
                      onClick={() => setLiquiditySweepMode('basic')}
                      className={`px-3 py-1.5 text-sm ${liquiditySweepMode === 'basic' ? 'bg-amber-600 text-white' : 'bg-slate-800 text-slate-300'}`}
                    >
                      Basic
                    </button>
                    <button
                      onClick={() => setLiquiditySweepMode('strict')}
                      className={`px-3 py-1.5 text-sm ${liquiditySweepMode === 'strict' ? 'bg-amber-700 text-white' : 'bg-slate-800 text-slate-300'}`}
                    >
                      Strict
                    </button>
                  </div>
                </div>
                {analysisOhlcvData.length > 1 && (
                  <button
                    onClick={() => setRequireEntryConfirmation(!requireEntryConfirmation)}
                    className={`px-3 py-1.5 rounded text-sm ${requireEntryConfirmation ? 'bg-emerald-600' : 'bg-slate-700'}`}
                  >
                    Entry Confirm {requireEntryConfirmation ? 'ON' : 'OFF'}
                  </button>
                )}
                <div>
                  <label className="text-slate-400 text-sm">Confluence Alert: <span className="text-cyan-400">{Math.round(entrySignalThreshold)}</span></label>
                  <input
                    type="range"
                    min="40"
                    max="95"
                    value={entrySignalThreshold}
                    onChange={(e) => setEntrySignalThreshold(Number(e.target.value))}
                    className="w-32 ml-2"
                  />
                </div>
                <button
                  onClick={() => setEnable3DGate((prev) => !prev)}
                  className={`px-3 py-1.5 rounded text-sm ${enable3DGate ? 'bg-fuchsia-600' : 'bg-slate-700'}`}
                >
                  3D Gate {enable3DGate ? 'ON' : 'OFF'}
                </button>
                <div>
                  <label className="text-slate-400 text-sm">3D Hold Bars: <span className="text-fuchsia-300">{decisionPersistenceBars}</span></label>
                  <input
                    type="range"
                    min="1"
                    max="6"
                    value={decisionPersistenceBars}
                    onChange={(e) => setDecisionPersistenceBars(Math.min(6, Math.max(1, Number(e.target.value) || 1)))}
                    className="w-28 ml-2"
                  />
                </div>
                <div className="flex items-center gap-1 ml-auto text-xs">
                  {['A+', 'A', 'B+', 'B', 'C+', 'C'].map((grade) => (
                    <span key={grade} className={`px-2 py-1 rounded ${grade.startsWith('A') ? 'bg-green-900/50 text-green-400' : grade.startsWith('B') ? 'bg-yellow-900/50 text-yellow-400' : 'bg-slate-700 text-slate-400'}`}>
                      {grade}: {gradeStats[grade]}
                    </span>
                  ))}
                </div>
                {optionRows.length > 0 && (
                  <div className="basis-full flex flex-wrap items-center gap-2">
                    <span className="text-xs text-slate-400 uppercase tracking-wide">Strike Legs</span>
                    {STRIKE_LADDER_DEFS.map((definition) => {
                      const selected = selectedStrikeLegIds.includes(definition.id)
                      const style = STRIKE_LADDER_STYLE[definition.id] || { color: '#94a3b8' }
                      return (
                        <button
                          key={definition.id}
                          onClick={() => toggleStrikeLegSelection(definition.id)}
                          className={`px-2.5 py-1 rounded-md text-xs border ${selected ? 'text-white border-slate-400' : 'text-slate-300 border-slate-700 bg-slate-900'}`}
                          style={selected ? { backgroundColor: style.color } : undefined}
                        >
                          {definition.label}
                        </button>
                      )
                    })}
                  </div>
                )}
                <div className="basis-full text-xs text-slate-400">
                  Swing Source: <span className="text-cyan-300">{swingSourceLabel}</span>
                  {' '}| Regime:{' '}
                  <span className={marketRegime.key === 'bull' ? 'text-emerald-300' : marketRegime.key === 'bear' ? 'text-rose-300' : 'text-amber-300'}>
                    {marketRegime.label}
                  </span>
                  {' '}({formatOptionalNumber(marketRegime.changePct, 2)}%)
                  {analysisOhlcvData.length > 1 && (
                    <>
                      {' '}| Next-candle confirmation (SL/SH):{' '}
                      <span className={requireEntryConfirmation ? 'text-emerald-300' : 'text-amber-300'}>
                        {requireEntryConfirmation ? 'ENFORCED' : 'OPTIONAL'}
                      </span>
                      {' '}| Confirmed {confirmationStats.confirmed}/{confirmationStats.eligible}
                      {confirmationStats.eligible > 0 && ` (${confirmationStats.confirmedPct.toFixed(1)}%)`}
                    </>
                  )}
                  {' '}| FVG Mode: <span className={fvgMode === 'off' ? 'text-slate-300' : 'text-cyan-300'}>{fvgMode === 'off' ? 'OFF' : fvgMode === 'fvg-plus' ? 'FVG+' : 'FVG'}</span>
                  {fvgMode !== 'off' && (
                    <> | Boosted {fvgStats.boosted}/{fvgStats.eligible}</>
                  )}
                  {' '}| Liquidity Sweep:{' '}
                  <span className={liquiditySweepMode === 'off' ? 'text-slate-300' : 'text-amber-300'}>
                    {liquiditySweepMode === 'off' ? 'OFF' : liquiditySweepMode === 'strict' ? 'STRICT' : 'BASIC'}
                  </span>
                  {liquiditySweepMode !== 'off' && (
                    <> | Events {liquiditySweepStats.detected} | Qualified {liquiditySweepStats.qualified} | Boosted {liquiditySweepStats.boosted}/{liquiditySweepStats.eligible}</>
                  )}
                  {' '}| Confluence Alerts ≥{Math.round(entrySignalThreshold)}:{' '}
                  <span className="text-fuchsia-300">{qualifiedConfluenceSignals.length}</span>
                  {' '}| 3D Gate:{' '}
                  <span className={enable3DGate ? 'text-emerald-300' : 'text-slate-300'}>
                    {enable3DGate ? 'ON' : 'OFF'}
                  </span>
                  {' '}| 3D Bars: <span className="text-slate-300">{decisionBarModeLabel}</span>
                  {' '}| Min Hold: <span className="text-fuchsia-300">{decisionPersistenceBars}</span>
                  {latestDecision3D && (
                    <>
                      {' '}| 3D Action:{' '}
                      <span className={latestDecision3D.decision === 'LONG' ? 'text-emerald-300' : latestDecision3D.decision === 'SHORT' ? 'text-rose-300' : 'text-amber-300'}>
                        {latestDecision3D.decision}
                      </span>
                      {' '}({formatOptionalNumber(latestDecision3D.confidence, 1)}%)
                      {' '}| Bias:{' '}
                      <span className={latestDecision3D.biasDecision === 'LONG' ? 'text-emerald-300' : latestDecision3D.biasDecision === 'SHORT' ? 'text-rose-300' : 'text-amber-300'}>
                        {latestDecision3D.biasDecision}
                      </span>
                      {' '}| Hold:{' '}
                      <span className="text-slate-300">{latestDecision3D.stabilityBars || 0} bars</span>
	                      {latestDecision3D.suggestedTrade && (
	                        <>
	                          {' '}| Suggested:{' '}
	                          <span className={getOptionSideTextClass(getTradeDisplayLabel(latestDecision3D.suggestedTrade), latestDecision3D.suggestedTrade.side)}>
	                            {getTradeDisplayLabel(latestDecision3D.suggestedTrade) || latestDecision3D.suggestedTrade.instrumentLabel}
	                          </span>
	                          {' '}@ {formatOptionalNumber(latestDecision3D.suggestedTrade.mid, 2)}
	                        </>
                      )}
                    </>
                  )}
                  {optionRows.length > 0 && (
                    <>
                      {' '}| Strike Plot:{' '}
                      <span className={showStrikePanel ? 'text-emerald-300' : 'text-slate-300'}>
                        {showStrikePanel
                          ? (strikeRenderMode === 'lines' ? 'LINES' : 'CANDLES')
                          : (showStrikeCandles ? `PAUSED (${strikeRenderMode.toUpperCase()}, Price Only view)` : 'OFF')}
                      </span>
                    </>
                  )}
                  {' '}| Structure markers are not trade entries.
                </div>
              </div>
            )}

		            <div className={`grid grid-cols-1 ${showTradeTicketPanel ? 'xl:grid-cols-[minmax(0,1fr)_340px]' : ''} gap-4 items-start`}>
	              <div className="min-w-0">
	                <PlotlySwingChart
	                  data={data}
	                  swingHighs={swingHighs}
	                  swingLows={swingLows}
	                  pendingSwing={pendingSwing}
	                  showSignals={showSignals}
	                  chartRevision={chartRevision}
	                  ohlcvData={displayedOhlcvData}
	                  candleType={candleType}
	                  fvgMode={fvgMode}
	                  liquiditySweepMode={liquiditySweepMode}
	                  liquiditySweepEvents={liquiditySweepEvents}
	                  showStrikeCandles={showStrikePanel}
	                  strikeRenderMode={strikeRenderMode}
	                  optionLadderCandles={filteredOptionLadderCandles}
	                  marketRegimeKey={marketRegime.key}
	                  showStructureMarkers={showStructureMarkers}
	                  showContextMarkers={showContextMarkers}
	                  showSwingLabels={showSwingLabels}
	                  showEntrySignals={showEntrySignals}
	                  entrySignals={qualifiedConfluenceSignals}
	                  chartTool={chartTool}
	                  manualTradeMarks={manualTradeMarks}
	                  tradePlan={ticketChartPlan}
	                  showTradePlanOverlay={showTradePlanOverlay}
	                  workspaceShapes={workspaceShapes}
	                  onWorkspaceShapesChange={handleWorkspaceShapesChange}
	                  onChartClick={handleChartClick}
	                  isFullscreen={isChartFullscreen}
	                />
	                <div className="mt-3 text-xs text-slate-500 text-center">
	                  Drag/Zoom/Draw enabled | Use top chart tools + Plotly toolbar | Double-click chart to reset
	                </div>
	              </div>

	              {showTradeTicketPanel && (
	              <div className="rounded-xl border border-slate-700 bg-slate-900/70 p-4 xl:sticky xl:top-4">
	                <div className="flex items-center justify-between mb-3">
	                  <div className="text-sm font-semibold text-slate-100">Trade Ticket</div>
	                  <span className="text-[11px] px-2 py-1 rounded bg-slate-800 text-slate-300">
	                    UI Only
	                  </span>
	                </div>

	                <div className="grid grid-cols-2 gap-2 text-xs mb-3">
	                  <div className="rounded border border-slate-700 bg-slate-950/70 p-2">
	                    <div className="text-slate-500">Symbol</div>
	                    <div className="text-slate-200 font-medium">{currentSymbol}</div>
	                  </div>
	                  <div className="rounded border border-slate-700 bg-slate-950/70 p-2">
	                    <div className="text-slate-500">Last Spot</div>
	                    <div className="text-cyan-300 font-medium">{formatOptionalNumber(latestSpot, 2)}</div>
	                  </div>
	                </div>

	                <div className="mb-3 rounded border border-slate-700 bg-slate-950/70 p-2 text-xs">
	                  <div className="flex items-center justify-between">
	                    <div className="text-slate-500">Latest 3D Suggestion</div>
	                    <span className={`font-semibold ${latestDecision3D?.decision === 'LONG' ? 'text-emerald-300' : latestDecision3D?.decision === 'SHORT' ? 'text-rose-300' : 'text-amber-300'}`}>
	                      {latestDecision3D?.decision || 'HOLD'}
	                    </span>
	                  </div>
		                  {latestSuggestedTrade ? (
		                    <div className="mt-1">
		                      <div className={`${getOptionSideTextClass(getTradeDisplayLabel(latestSuggestedTrade), latestSuggestedTrade.side)} font-medium`}>
		                        {getTradeDisplayLabel(latestSuggestedTrade) || latestSuggestedTrade.instrumentLabel}
		                      </div>
		                      <div className="mt-1 text-slate-400">
		                        Price {formatOptionalNumber(latestSuggestedTrade.mid, 2)} | Confidence {formatOptionalNumber(latestDecision3D?.confidence, 1)}% | Hold {latestDecision3D?.stabilityBars || 0} bars
		                      </div>
	                    </div>
	                  ) : (
	                    <div className="mt-1 text-slate-500">
	                      No active option leg suggestion. Wait for a persistent LONG or SHORT action.
	                    </div>
	                  )}
	                </div>

	                <div className="mb-3 rounded border border-slate-700 bg-slate-950/70 p-2 text-xs">
	                  <div className="text-slate-500">Locked Ticket Instrument</div>
	                  {ticketInstrumentLabel ? (
	                    <>
		                      <div className={`mt-1 font-medium ${getOptionSideTextClass(ticketInstrumentLabel)}`}>
		                        {ticketInstrumentLabel}
		                      </div>
	                      <div className="mt-1 text-slate-500">
	                        {ticketInstrumentBucketLabel || 'Exact strike locked from suggestion'}
	                        {ticketInstrumentDecision ? ` | Source ${ticketInstrumentDecision}` : ''}
	                      </div>
		                      {ticketChartPlan && (
		                        <div className="mt-2 flex items-center gap-2">
		                          <div className={`text-[11px] ${showTradePlanOverlay ? 'text-cyan-300' : 'text-slate-500'}`}>
		                            {showTradePlanOverlay ? 'Chart overlay active' : 'Chart overlay hidden'}
		                          </div>
		                          <button
		                            onClick={() => setShowTradePlanOverlay((prev) => !prev)}
		                            className={`px-2 py-1 rounded text-[10px] border ${showTradePlanOverlay ? 'border-cyan-500/50 text-cyan-200 bg-cyan-500/10' : 'border-slate-700 text-slate-300 bg-slate-800'}`}
		                          >
		                            {showTradePlanOverlay ? 'Hide Overlay' : 'Show Overlay'}
		                          </button>
		                        </div>
		                      )}
	                      {ticketStructureSummary && (
	                        <div className="mt-1 text-slate-500">
	                          {ticketStructureSummary}
	                        </div>
	                      )}
	                    </>
	                  ) : (
	                    <div className="mt-1 text-slate-500">
	                      No exact option leg locked in ticket.
	                    </div>
	                  )}
	                </div>

	                <div className="mb-3">
	                  <label className="text-[11px] text-slate-400">Side</label>
	                  <div className="mt-1 grid grid-cols-2 gap-2">
	                    <button
	                      onClick={() => setTicketSide('BUY')}
	                      className={`px-3 py-2 rounded text-xs font-semibold ${ticketSide === 'BUY' ? 'bg-emerald-600 text-white' : 'bg-slate-800 text-slate-300 border border-slate-700'}`}
	                    >
	                      BUY
	                    </button>
	                    <button
	                      onClick={() => setTicketSide('SELL')}
	                      className={`px-3 py-2 rounded text-xs font-semibold ${ticketSide === 'SELL' ? 'bg-rose-600 text-white' : 'bg-slate-800 text-slate-300 border border-slate-700'}`}
	                    >
	                      SELL
	                    </button>
	                  </div>
	                </div>

	                <div className="mb-3">
	                  <label className="text-[11px] text-slate-400">Order Type</label>
	                  <select
	                    value={ticketOrderType}
	                    onChange={(e) => setTicketOrderType(e.target.value)}
	                    className="mt-1 w-full bg-slate-950 border border-slate-700 rounded px-2 py-2 text-xs text-slate-200"
	                  >
	                    <option value="LIMIT">LIMIT</option>
	                    <option value="MARKET">MARKET</option>
	                    <option value="STOP_LIMIT">STOP_LIMIT</option>
	                  </select>
	                </div>

	                <div className="grid grid-cols-2 gap-2 mb-3">
	                  <div>
	                    <label className="text-[11px] text-slate-400">Entry</label>
	                    <input
	                      value={ticketEntry}
	                      onChange={(e) => setTicketEntry(e.target.value)}
	                      className="mt-1 w-full bg-slate-950 border border-slate-700 rounded px-2 py-2 text-xs text-slate-200"
	                      placeholder="0.00"
	                    />
	                  </div>
	                  <div>
	                    <label className="text-[11px] text-slate-400">Stop Loss</label>
	                    <input
	                      value={ticketStop}
	                      onChange={(e) => setTicketStop(e.target.value)}
	                      className="mt-1 w-full bg-slate-950 border border-slate-700 rounded px-2 py-2 text-xs text-slate-200"
	                      placeholder="0.00"
	                    />
	                  </div>
	                  <div>
	                    <label className="text-[11px] text-slate-400">Target</label>
	                    <input
	                      value={ticketTarget}
	                      onChange={(e) => setTicketTarget(e.target.value)}
	                      className="mt-1 w-full bg-slate-950 border border-slate-700 rounded px-2 py-2 text-xs text-slate-200"
	                      placeholder="0.00"
	                    />
	                  </div>
	                  <div>
	                    <label className="text-[11px] text-slate-400">Qty</label>
	                    <input
	                      type="number"
	                      min={1}
	                      value={ticketQty}
	                      onChange={(e) => setTicketQty(Math.max(1, Number(e.target.value) || 1))}
	                      className="mt-1 w-full bg-slate-950 border border-slate-700 rounded px-2 py-2 text-xs text-slate-200"
	                    />
	                  </div>
	                </div>

	                <div className="grid grid-cols-2 gap-2 mb-3">
	                  <div>
	                    <label className="text-[11px] text-slate-400">Risk Budget</label>
	                    <input
	                      type="number"
	                      min={0}
	                      value={ticketRiskBudget}
	                      onChange={(e) => setTicketRiskBudget(Math.max(0, Number(e.target.value) || 0))}
	                      className="mt-1 w-full bg-slate-950 border border-slate-700 rounded px-2 py-2 text-xs text-slate-200"
	                    />
	                  </div>
	                  <div>
	                    <label className="text-[11px] text-slate-400">Tag</label>
	                    <input
	                      value={ticketTag}
	                      onChange={(e) => setTicketTag(e.target.value)}
	                      className="mt-1 w-full bg-slate-950 border border-slate-700 rounded px-2 py-2 text-xs text-slate-200"
	                      placeholder="Setup name"
	                    />
	                  </div>
	                </div>

	                <div className="flex flex-wrap gap-2 mb-3">
	                  <button
	                    onClick={loadTicketFromSuggestedTrade}
	                    className={`px-2.5 py-1.5 rounded text-[11px] ${latestSuggestedTrade ? 'bg-fuchsia-600 text-white hover:bg-fuchsia-500' : 'bg-slate-700 text-slate-400 cursor-not-allowed'}`}
	                    disabled={!latestSuggestedTrade}
	                  >
	                    Use 3D Suggestion + Levels
	                  </button>
	                  <button
	                    onClick={loadTicketFromLastMark}
	                    className="px-2.5 py-1.5 rounded text-[11px] bg-slate-800 border border-slate-700 text-slate-300 hover:bg-slate-700"
	                  >
	                    Use Last Tag
	                  </button>
	                  <button
	                    onClick={loadTicketFromSpot}
	                    className="px-2.5 py-1.5 rounded text-[11px] bg-slate-800 border border-slate-700 text-slate-300 hover:bg-slate-700"
	                  >
	                    Use Last Spot
	                  </button>
	                  <button
	                    onClick={saveTradeTicketSnapshot}
	                    className={`px-2.5 py-1.5 rounded text-[11px] ${ticketMetrics.isValid ? 'bg-cyan-600 text-white' : 'bg-slate-700 text-slate-400 cursor-not-allowed'}`}
	                    disabled={!ticketMetrics.isValid}
	                  >
	                    Save Snapshot
	                  </button>
	                  <button
	                    onClick={clearTradeTicket}
	                    className="px-2.5 py-1.5 rounded text-[11px] bg-slate-800 border border-slate-700 text-slate-300 hover:bg-slate-700"
	                  >
	                    Clear Ticket
	                  </button>
	                </div>

	                <div className="grid grid-cols-2 gap-2 text-[11px] mb-3">
	                  <div className="rounded border border-slate-700 bg-slate-950/70 p-2">
	                    <div className="text-slate-500">R:R</div>
	                    <div className={`${ticketMetrics.rr >= 2 ? 'text-emerald-300' : ticketMetrics.rr >= 1 ? 'text-amber-300' : 'text-rose-300'} font-semibold`}>
	                      {formatOptionalNumber(ticketMetrics.rr, 2)}
	                    </div>
	                  </div>
	                  <div className="rounded border border-slate-700 bg-slate-950/70 p-2">
	                    <div className="text-slate-500">Capital</div>
	                    <div className="text-slate-200 font-semibold">{formatOptionalNumber(ticketMetrics.capital, 2)}</div>
	                  </div>
	                  <div className="rounded border border-slate-700 bg-slate-950/70 p-2">
	                    <div className="text-slate-500">Max Loss</div>
	                    <div className="text-rose-300 font-semibold">{formatOptionalNumber(ticketMetrics.maxLoss, 2)}</div>
	                  </div>
	                  <div className="rounded border border-slate-700 bg-slate-950/70 p-2">
	                    <div className="text-slate-500">Max Profit</div>
	                    <div className="text-emerald-300 font-semibold">{formatOptionalNumber(ticketMetrics.maxProfit, 2)}</div>
	                  </div>
	                  <div className="rounded border border-slate-700 bg-slate-950/70 p-2">
	                    <div className="text-slate-500">Risk % Budget</div>
	                    <div className="text-slate-200 font-semibold">{formatOptionalNumber(ticketMetrics.riskPctOfBudget, 2)}%</div>
	                  </div>
	                  <div className="rounded border border-slate-700 bg-slate-950/70 p-2">
	                    <div className="text-slate-500">Qty by Budget</div>
	                    <div className="text-cyan-300 font-semibold">{formatOptionalNumber(ticketMetrics.recommendedQty, 0)}</div>
	                  </div>
	                </div>

	                <div className="border-t border-slate-700 pt-3">
	                  <div className="flex items-center justify-between mb-2">
	                    <span className="text-xs text-slate-400">Saved Snapshots</span>
	                    <span className="text-[11px] text-slate-500">{ticketSnapshots.length}</span>
	                  </div>
	                  <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
	                    {ticketSnapshots.length === 0 && (
	                      <div className="text-[11px] text-slate-500">No snapshots yet.</div>
	                    )}
	                    {ticketSnapshots.map((snap) => (
	                      <button
	                        key={snap.id}
	                        onClick={() => loadTicketSnapshot(snap)}
	                        className="w-full text-left rounded border border-slate-700 bg-slate-950/70 p-2 hover:bg-slate-800"
	                      >
	                        <div className="flex justify-between text-[11px]">
	                          <span className={snap.side === 'BUY' ? 'text-emerald-300' : 'text-rose-300'}>{snap.side}</span>
	                          <span className="text-slate-500">{new Date(snap.ts).toLocaleTimeString()}</span>
	                        </div>
	                        <div className="text-[11px] text-slate-300 mt-1">
	                          E {Number(snap.entry).toFixed(2)} | SL {Number(snap.stop).toFixed(2)} | T {Number(snap.target).toFixed(2)}
	                        </div>
		                        {snap.instrumentLabel && (
		                          <div className="text-[11px] mt-1">
		                            <span className={getOptionSideTextClass(snap.instrumentLabel)}>
		                              {snap.instrumentLabel}
		                            </span>
		                            {snap.instrumentDecision ? <span className="text-slate-500">{` | ${snap.instrumentDecision}`}</span> : null}
		                          </div>
	                        )}
	                        {snap.structureSummary && (
	                          <div className="text-[11px] text-slate-500 mt-1">
	                            {snap.structureSummary}
	                          </div>
	                        )}
	                        <div className="text-[11px] text-slate-500 mt-1">
	                          RR {formatOptionalNumber(snap.rr, 2)} {snap.tag ? `| ${snap.tag}` : ''}
	                        </div>
	                      </button>
	                    ))}
	                  </div>
	                </div>
	              </div>
	              )}
	            </div>
          </div>
        )}

        {/* Top 10 */}
        {data.length > 0 && activeTab === 'top' && (
          <div className="bg-slate-800/50 rounded-xl p-6 border border-slate-700">
            <h3 className="text-lg font-semibold mb-4 flex items-center gap-2"><Target className="w-5 h-5 text-yellow-400" />Top 10 Highest-Confidence Swing Points</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-slate-800">
                  <tr className="text-slate-400 border-b border-slate-700">
                    <th className="p-3 text-left">#</th><th className="p-3">Type</th><th className="p-3">Time</th>
                    <th className="p-3 text-right">Price</th><th className="p-3">Grade</th><th className="p-3">Score</th>
                    <th className="p-3 text-left">Key Signals</th><th className="p-3">Outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {topSwings.map((p, i) => (
                    <tr key={i} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                      <td className="p-3 text-slate-500">{i + 1}</td>
                      <td className="p-3"><span className={`px-2 py-1 rounded text-xs font-bold ${p.type === 'high' ? 'bg-red-900/50 text-red-400' : 'bg-green-900/50 text-green-400'}`}>{p.type.toUpperCase()}</span></td>
                      <td className="p-3 font-mono">{p.time}</td>
                      <td className="p-3 text-right font-mono font-medium">{p.spot.toFixed(2)}</td>
                      <td className="p-3"><span className={`px-2 py-1 rounded text-xs font-bold ${p.grade.startsWith('A') ? 'bg-green-800 text-green-200' : 'bg-yellow-800 text-yellow-200'}`}>{p.grade}</span></td>
                      <td className="p-3 text-center font-bold text-cyan-400">{p.score}</td>
                      <td className="p-3 text-slate-400 text-xs">{(p.signals || []).slice(0, 4).join(' | ')}</td>
                      <td className="p-3">
                        {p.details?.outcome === 'WIN' && <span className="px-2 py-1 bg-green-800 text-green-200 rounded text-xs flex items-center gap-1"><CheckCircle className="w-3 h-3" />WIN</span>}
                        {p.details?.outcome === 'LOSS' && <span className="px-2 py-1 bg-red-800 text-red-200 rounded text-xs flex items-center gap-1"><XCircle className="w-3 h-3" />LOSS</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Patterns */}
        {data.length > 0 && activeTab === 'patterns' && (
          <div className="bg-slate-800/50 rounded-xl p-6 border border-slate-700">
            <h3 className="text-lg font-semibold mb-4">Market Structure Patterns</h3>
            <div className="space-y-2">
              {patterns.map((p, i) => (
                <div key={i} className={`p-4 rounded-lg border ${p.isOverall ? 'border-cyan-700 bg-cyan-900/20' : p.bullish ? 'border-green-800 bg-green-900/20' : 'border-red-800 bg-red-900/20'}`}>
                  <span className={`px-2 py-1 rounded text-xs font-bold mr-3 ${p.isOverall ? 'bg-cyan-800 text-cyan-200' : p.bullish ? 'bg-green-800 text-green-200' : 'bg-red-800 text-red-200'}`}>{p.type}</span>
                  <span className="text-slate-300">{p.description}</span>
                  {p.avgScore > 0 && <span className="text-slate-500 text-xs ml-3">Avg Score: {p.avgScore.toFixed(0)}</span>}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* All Swings */}
        {data.length > 0 && activeTab === 'all' && (
          <div className="bg-slate-800/50 rounded-xl p-6 border border-slate-700">
            <h3 className="text-lg font-semibold mb-4">All Detected Swing Points</h3>
            <div className="overflow-x-auto max-h-[600px]">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-slate-800">
                  <tr className="text-slate-400 border-b border-slate-700">
                    <th className="p-2">#</th><th className="p-2">Type</th><th className="p-2">Time</th><th className="p-2 text-right">Price</th>
                    <th className="p-2">Grade</th><th className="p-2">Score</th><th className="p-2">PCR</th><th className="p-2">VIX</th>
                    <th className="p-2">Vol</th><th className="p-2">OI</th><th className="p-2">Delta</th><th className="p-2">Vote</th><th className="p-2">Out</th>
                  </tr>
                </thead>
                <tbody>
                  {[...swingHighs, ...swingLows].sort((a, b) => a.index - b.index).map((p, i) => (
                    <tr key={i} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                      <td className="p-2 text-slate-500">{i + 1}</td>
                      <td className="p-2"><span className={`px-1.5 py-0.5 rounded ${p.type === 'high' ? 'bg-red-900/50 text-red-400' : 'bg-green-900/50 text-green-400'}`}>{p.type.toUpperCase()}</span></td>
                      <td className="p-2 font-mono">{p.time}</td>
                      <td className="p-2 text-right font-mono">{p.spot.toFixed(2)}</td>
                      <td className="p-2"><span className={`px-1.5 py-0.5 rounded text-xs font-bold ${p.grade.startsWith('A') ? 'bg-green-900 text-green-300' : p.grade.startsWith('B') ? 'bg-yellow-900 text-yellow-300' : 'bg-slate-700'}`}>{p.grade}</span></td>
                      <td className="p-2 text-center text-cyan-400">{p.score}</td>
                      <td className="p-2 text-slate-400">{p.details?.pcr?.toFixed(2) || '-'}</td>
                      <td className="p-2 text-slate-400">{p.details?.vix?.toFixed(1) || '-'}</td>
                      <td className="p-2 text-slate-400">{p.details?.volume ? (p.details.volume / 1000).toFixed(0) + 'K' : '-'}</td>
                      <td className="p-2 text-slate-400">{p.details?.oiChange ? (p.details.oiChange / 1000).toFixed(1) + 'K' : '-'}</td>
                      <td className="p-2 text-slate-400">{p.details?.delta?.toFixed(2) || '-'}</td>
                      <td className="p-2">{p.details?.voteSide || '-'}</td>
                      <td className="p-2">{p.details?.outcome === 'WIN' ? <CheckCircle className="w-4 h-4 text-green-400" /> : p.details?.outcome === 'LOSS' ? <XCircle className="w-4 h-4 text-red-400" /> : '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Stats */}
        {data.length > 0 && (
          <div className="grid grid-cols-3 md:grid-cols-6 gap-3 mt-6">
            <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
              <div className="text-slate-400 text-sm">Data Points</div>
              <div className="text-xl font-bold">{data.length}</div>
            </div>
            <div className="bg-slate-800/50 rounded-xl p-4 border border-red-900/50">
              <div className="text-red-400 text-sm flex items-center gap-1"><TrendingUp className="w-3 h-3" />Highs</div>
              <div className="text-xl font-bold text-red-400">{swingHighs.length}</div>
            </div>
            <div className="bg-slate-800/50 rounded-xl p-4 border border-green-900/50">
              <div className="text-green-400 text-sm flex items-center gap-1"><TrendingDown className="w-3 h-3" />Lows</div>
              <div className="text-xl font-bold text-green-400">{swingLows.length}</div>
            </div>
            <div className="bg-slate-800/50 rounded-xl p-4 border border-green-900/50">
              <div className="text-green-400 text-sm">A/A+ Grade</div>
              <div className="text-xl font-bold text-green-400">{gradeStats['A+'] + gradeStats['A']}</div>
            </div>
            <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
              <div className="text-slate-400 text-sm">Day High</div>
              <div className="text-lg font-bold text-red-400">{Math.max(...data.map(d => d.spot)).toFixed(2)}</div>
            </div>
            <div className="bg-slate-800/50 rounded-xl p-4 border border-slate-700">
              <div className="text-slate-400 text-sm">Day Low</div>
              <div className="text-lg font-bold text-green-400">{Math.min(...data.map(d => d.spot)).toFixed(2)}</div>
            </div>
          </div>
        )}

        {/* Empty */}
        {!data.length && !loading && (
          <div className="bg-slate-800/30 rounded-xl p-12 text-center border border-dashed border-slate-600">
            <Target className="w-16 h-16 mx-auto text-slate-600 mb-4" />
            <h3 className="text-xl text-slate-400 mb-2">Scientific Swing Analysis</h3>
            <p className="text-slate-500">
              {mode === 'live'
                ? 'Waiting for live feed. Check API server and selected index.'
                : 'Upload decision_journal.csv for Plotly-powered analysis'}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

export default SwingAnalysis
