/* ============================================================================
 * RAN KPI 15 分钟看板 —— 图表与口径工具（?page=ran_kpi_15mins）
 * ----------------------------------------------------------------------------
 * 与 CnKpi5mins 的图表工具同构，两处差异：
 *   ① 百分比：后端 `pct=true` 的指标值是 0~1 的比例（UME datatypeshow=7），
 *      这里统一 ×100 再交给 ECharts，纵轴/气泡才是 99.85% 而不是 0.9985；
 *   ② 指标来自后端 widgets[]，不再有硬编码的组件 id → 文案表。
 * ========================================================================== */
import type { EChartsOption } from 'echarts'
import { axisLabel, axisLine, darkTooltip } from '../../theme'

export type Row15 = [string, string, number | string | null]

export interface Widget15 {
  id: string
  title: string
  metric_name: string
  unit: string
  order: number
  pct: boolean
  columns: string[]
  rows: Row15[]
  error: string
  empty: boolean
  empty_reason: string
}

export interface Overview15 {
  meta: {
    db: string
    table: string
    tech: string
    template: string
    days: { d0: string; d1: string; d7: string }
    count: number
    failed: number
    empty: number
    elapsed_ms: number
    cached: boolean
    latest: string
  }
  tech: string
  title: string
  widgets: Widget15[]
}

export function toNumber(v: unknown): number {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

export const DAY_ORDER = ['Day0', 'Day1', 'Day7'] as const

/** 配色：Day0 绿 / Day1 蓝 / Day7 青，与 CnKpi5mins 一致（canvas 只吃实值） */
const DAY_COLOR: Record<string, string> = {
  Day0: '#08bd83',
  Day1: '#2b8cff',
  Day7: '#00d9ff',
}
const DAY_COLOR_LIGHT: Record<string, string> = { Day7: '#0e7490' }

export function dayColor(name: string, isDark: boolean) {
  return (!isDark && DAY_COLOR_LIGHT[name]) || DAY_COLOR[name] || '#8b95a7'
}

function fadeColor(hex: string, alpha: number) {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`
}

/** 百分比指标：库里是 0~1 比例，图上 ×100 */
function scaleValue(v: number, pct: boolean): number {
  return pct ? v * 100 : v
}

/** 类目轴抽稀：一天 96 个 15 分钟点，不抽稀会糊成斜带 */
export function xAxisLabel(n: number) {
  return {
    rotate: 0,
    hideOverlap: true,
    interval: n > 24 ? Math.ceil(n / 24) - 1 : ('auto' as const),
  }
}

/** 三列 [HH:MM, Day0|Day1|Day7, 值] → 多序列折线 */
export function buildDayOption(w: Widget15, isDark: boolean): EChartsOption {
  const cats: string[] = []
  const seen = new Set<string>()
  const lookup = new Map<string, number | null>()
  const names: string[] = []
  for (const r of w.rows) {
    const x = String(r[0])
    const day = String(r[1])
    if (!day || day === 'null' || day === 'NULL' || day === 'undefined') continue
    if (!seen.has(x)) { seen.add(x); cats.push(x) }
    if (!names.includes(day)) names.push(day)
    const raw = r[2]
    const v = raw === null || raw === undefined || raw === '' ? null : scaleValue(toNumber(raw), w.pct)
    lookup.set(`${x}\u0000${day}`, v)
  }
  names.sort((a, b) => (a === 'Day0' ? -1 : b === 'Day0' ? 1 : a.localeCompare(b)))
  const dense = cats.length > 120
  const isPct = w.pct
  const fmtValue = (v: unknown) => {
    if (v === null || v === undefined || v === '') return '—'
    const n = Number(v)
    if (!Number.isFinite(n)) return String(v)
    return isPct ? `${n.toFixed(2)}%` : n.toLocaleString('en-US', { maximumFractionDigits: 2 })
  }
  return {
    tooltip: { ...darkTooltip(isDark), trigger: 'axis', valueFormatter: fmtValue },
    legend: {
      top: 2,
      type: 'scroll',
      icon: 'roundRect',
      show: names.length > 0,
      textStyle: { color: isDark ? '#aebbd4' : '#3f5468', fontSize: 10 },
      itemWidth: 14,
      itemHeight: 8,
      itemGap: 10,
      data: names,
    },
    grid: { left: 12, right: 16, top: 34, bottom: 24, containLabel: true },
    xAxis: {
      type: 'category',
      data: cats,
      boundaryGap: false,
      axisLabel: { ...axisLabel(isDark), ...xAxisLabel(cats.length) },
      axisLine: axisLine(isDark),
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      scale: true,
      splitLine: { show: false },
      axisLabel: {
        ...axisLabel(isDark),
        formatter: isPct ? (v: number) => `${Number(v).toFixed(2)}%` : undefined,
      },
    },
    series: names.map((n) => {
      const c = dayColor(n, isDark)
      return {
        name: n,
        type: 'line' as const,
        smooth: true,
        showSymbol: false,
        connectNulls: false,
        sampling: dense ? ('lttb' as const) : undefined,
        color: c,
        lineStyle: { width: 1.5 },
        areaStyle: {
          color: {
            type: 'linear' as const,
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: fadeColor(c, 0.25) },
              { offset: 1, color: fadeColor(c, 0.02) },
            ],
          },
        },
        data: cats.map((x) => lookup.get(`${x}\u0000${n}`) ?? null),
      }
    }),
  }
}

/** 每日序列 */
type DaySeries = { t: string; v: number | null }[]

function collectDay(rows: Row15[], day: string): DaySeries {
  const out: DaySeries = []
  for (const r of rows) {
    if (String(r[1]) !== day) continue
    out.push({
      t: String(r[0]),
      v: r[2] === null || r[2] === undefined || r[2] === '' ? null : toNumber(r[2]),
    })
  }
  return out
}

/**
 * 今日 vs 昨日（%）：Day0 全部点均值 vs Day1 截到 Day0 最后时刻的均值。
 * 昨天截到同一时刻，避免「不完整的今天 vs 完整的昨天」的截断偏差。
 */
export function compareDay0Day1(w: Widget15): { pct: number | null; last0: string } {
  const d0 = collectDay(w.rows, 'Day0')
  const d1 = collectDay(w.rows, 'Day1')
  if (d0.length === 0 || d1.length === 0) return { pct: null, last0: '' }
  const last0 = d0.reduce((m, x) => (x.t > m ? x.t : m), d0[0].t)
  const mean = (xs: DaySeries) => {
    const vals = xs.map((x) => x.v ?? 0)
    return vals.reduce((s, v) => s + v, 0) / (vals.length || 1)
  }
  const m0 = mean(d0)
  const m1 = mean(d1.filter((x) => x.t <= last0))
  if (!Number.isFinite(m1) || m1 === 0 || !Number.isFinite(m0)) return { pct: null, last0 }
  return { pct: ((m0 - m1) / m1) * 100, last0 }
}

export function fmtVsPct(pct: number): string {
  if (Math.abs(pct) >= 999.95) return `${pct > 0 ? '+' : '-'}>999%`
  return `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`
}

/**
 * 指标标题：去掉 UME 模板里的技术后缀（`[SDR_ITBBU]`、`_900241_2_gv4.bsc-MO`、
 * `_1722590438161`、`-Inspection`、`-new`），折叠重复单位，再补一次单位。
 * 原始全名仍保留在卡头 ⓘ 气泡里（metric_name），这里只影响卡片标题的可读性。
 */
export function displayTitle(name: string, unit: string): string {
  let out = name
    .replace(/\[[A-Za-z0-9_]+\]/g, '')
    .replace(/_\d{6,}[0-9A-Za-z._-]*-MO\b/gi, '')
    .replace(/_\d{10,}\b/g, '')
    .replace(/-Inspection\b/gi, '')
    .replace(/-new\b/gi, '')
    .replace(/\(([^()]+)\)\(\1\)/g, '($1)')
    .replace(/\s{2,}/g, ' ')
    .trim()
  if (unit && !out.toLowerCase().includes(unit.toLowerCase())) out += ` (${unit})`
  return out || name
}

/** 指标标题（带单位；卡头展示用） */
export function titleWithUnit(w: Widget15): string {
  return displayTitle(w.title, w.unit)
}
