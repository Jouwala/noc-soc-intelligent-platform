/* ============================================================================
 * CS / PS CN KPI 分组看板 —— 图表与口径说明的公共实现
 * ----------------------------------------------------------------------------
 * 两个页面（?page=cs_cn_kpi / ?page=ps_cn_kpi）共用本文件的：
 *   · 类型定义（与 backend/services/cn/cn_kpi_split_service.py 对齐）
 *   · ECharts option 构造（pivot / 多列两种数据形态）
 *   · 卡片标题旁 ⓘ 的「这个数怎么算」文案（数据表 / 粒度 / 行数 / SQL）
 *
 * 约定：ECharts 颜色必须用实值（canvas 解析不了 CSS 变量），深浅各一套；
 * 主题判断走 useTheme() 的 isDark（AGENTS.md 三.3）。
 * ========================================================================== */
import type { EChartsOption } from 'echarts'
import { axisLabel, axisLine, darkTooltip } from '../../theme'

export type SplitWidget = {
  id: string
  title: string
  chart: string
  pivot: boolean
  pct: boolean
  unit: string
  source_table: string
  fixed_range: boolean
  sql: string
  columns: string[]
  rows: (string | number | null)[][]
  error: string
  empty: boolean
  empty_code: '' | 'no_table' | 'not_collected' | 'no_rows'
  truncated: boolean
  grain: '' | 'minute' | 'hour' | 'day' | 'month'
}

export type SplitSection = { key: 'ems' | 'ume'; widgets: SplitWidget[] }

export type SplitOverview = {
  meta: {
    db: string
    host: string
    board: string
    start: string | null
    end: string | null
    count: number
    failed: number
    empty: number
    truncated: number
    elapsed_ms: number
    cached: boolean
  }
  title: string
  sections: SplitSection[]
}

type TFn = (key: string, params?: Record<string, string | number>) => string

// 网元 / 序列配色（实值；与 CnHourlyKpi 同一套，两个页面并排看不会串色）
const SERIES_COLOR_DARK = ['#08bd83', '#2b8cff', '#00d9ff', '#f6cf00', '#ff5c6c', '#9254de', '#fa8c16']
const SERIES_COLOR_LIGHT = ['#0e9f6e', '#2563eb', '#0e7490', '#d97706', '#dc2626', '#7c3aed', '#ea580c']

function seriesColor(name: string, names: string[], isDark: boolean): string {
  const palette = isDark ? SERIES_COLOR_DARK : SERIES_COLOR_LIGHT
  const idx = names.indexOf(name)
  return palette[idx % palette.length] || '#8b95a7'
}

function toNumber(v: unknown): number {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

/** 类目标签：15 分钟 / 小时 / 日 / 月四种形态各给一个短标签。 */
export function catLabel(raw: string, grain: string, t: TFn): string {
  const v = String(raw ?? '')
  if (grain === 'month') return v.slice(0, 7)
  if (grain === 'day' || /^\d{4}-\d{2}-\d{2}$/.test(v)) return v.slice(5)
  // 'YYYY-MM-DD HH:MM[:SS]'
  const m = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})/.exec(v)
  if (m) {
    const mmdd = m[1].slice(5)
    // 15 分钟档保留分钟；小时档统一显示「N时」，避免 :00 的点与 :15/:30/:45 混排
    if (grain === 'minute') return `${mmdd} ${m[2]}:${m[3]}`
    return `${mmdd} ${m[2]}${t('cnkpiSplit.hourSuffix')}`
  }
  return v
}

/** 轴标签抽稀：15 分钟 × 31 天 ≈ 3000 个点，不抽稀会糊成一条带。 */
function tickInterval(n: number) {
  if (n > 720) return Math.ceil(n / 20) - 1
  if (n > 168) return Math.ceil(n / 24) - 1
  return 'auto' as const
}

function formatNum(n: number, w: SplitWidget): string {
  if (w.pct) return `${n.toFixed(2)}%`
  const txt = n.toLocaleString('en-US', { maximumFractionDigits: w.unit === 'count' ? 0 : 2 })
  return w.unit ? `${txt} ${w.unit}` : txt
}

/**
 * option 构造，支持两种数据形态：
 *   · pivot=true  → rows = [t, series, v]，按 series 拆线（网元 / 资源名 / RG 组）
 *   · pivot=false → rows = [t, 序列1, 序列2, …]，列头即序列名
 */
export function buildSplitOption(w: SplitWidget, isDark: boolean, t: TFn): EChartsOption {
  const cats: string[] = []
  const names: string[] = []
  const lookup = new Map<string, number | null>()
  const seen = new Set<string>()

  const pushCat = (raw: string) => {
    const c = catLabel(raw, w.grain, t)
    if (!seen.has(c)) {
      seen.add(c)
      cats.push(c)
    }
    return c
  }
  const val = (v: unknown) => (v === null || v === undefined || v === '' ? null : toNumber(v))

  if (w.pivot) {
    for (const r of w.rows) {
      const cat = pushCat(String(r[0] ?? ''))
      const s = String(r[1] ?? '')
      if (!s || s === 'null' || s === 'undefined') continue
      if (!names.includes(s)) names.push(s)
      lookup.set(`${cat}\u0000${s}`, val(r[2]))
    }
  } else {
    names.push(...w.columns.slice(1))
    for (const r of w.rows) {
      const cat = pushCat(String(r[0] ?? ''))
      for (let i = 1; i < w.columns.length; i++) {
        lookup.set(`${cat}\u0000${w.columns[i]}`, val(r[i]))
      }
    }
  }

  const dense = cats.length > 120
  const fmt = (v: unknown) => {
    if (v === null || v === undefined || v === '') return '—'
    const n = Number(v)
    return Number.isFinite(n) ? formatNum(n, w) : String(v)
  }

  return {
    tooltip: { ...darkTooltip(isDark), trigger: 'axis', valueFormatter: fmt },
    legend: {
      top: 2,
      type: 'scroll',
      icon: 'roundRect',
      show: names.length > 1,
      data: names,
      textStyle: { color: isDark ? '#aebbd4' : '#3f5468', fontSize: 10 },
      itemWidth: 14,
      itemHeight: 8,
      itemGap: 10,
    },
    grid: { left: 12, right: 16, top: names.length > 1 ? 34 : 16, bottom: 22, containLabel: true },
    xAxis: {
      type: 'category',
      data: cats,
      boundaryGap: false,
      axisLabel: { ...axisLabel(isDark), hideOverlap: true, interval: tickInterval(cats.length) },
      axisLine: axisLine(isDark),
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      scale: true,
      splitLine: { show: true, lineStyle: { type: 'dashed', opacity: 0.25 } },
      axisLabel: {
        ...axisLabel(isDark),
        formatter: w.pct ? (v: number) => `${Number(v).toFixed(0)}%` : undefined,
      },
    },
    series: names.map((n) => {
      const c = n === 'ALL'
        ? (isDark ? '#00d9ff' : '#0e7490')
        : seriesColor(n, names, isDark)
      return {
        name: n,
        type: 'line',
        smooth: false,
        showSymbol: false,
        connectNulls: false,
        sampling: dense ? 'lttb' : undefined,
        lineStyle: { width: names.length > 6 ? 1.2 : 1.6 },
        color: c,
        data: cats.map((x) => lookup.get(`${x}\u0000${n}`) ?? null),
      }
    }),
  }
}

/** 卡片标题 ⓘ 的文案：这一张卡的数据表 / 粒度 / 行数 / 实际执行 SQL。 */
export function widgetExplainText(w: SplitWidget, t: TFn): string {
  if (w.error) return ''
  let gran = ''
  switch (w.grain) {
    case 'minute': gran = t('cnkpiSplit.gran.minute'); break
    case 'hour': gran = t('cnkpiSplit.gran.hour'); break
    case 'day': gran = t('cnkpiSplit.gran.day'); break
    case 'month': gran = t('cnkpiSplit.gran.month'); break
    default: gran = ''
  }
  return [
    t('cnkpiSplit.explain.source', { table: w.source_table || '—' }),
    gran ? t('cnkpiSplit.explain.gran', { gran }) : '',
    t('cnkpiSplit.explain.rows', { n: w.rows.length }),
    w.fixed_range ? t('cnkpiSplit.explain.fixedRange') : '',
    w.truncated ? t('cnkpiSplit.truncatedTag') : '',
    w.sql ? t('cnkpiSplit.explain.sql', { sql: w.sql }) : '',
  ].filter(Boolean).join('\n')
}

/** 空态原因：一律把表名带上，用户能直接去查同步任务。 */
export function emptyReason(w: SplitWidget, t: TFn): string {
  switch (w.empty_code) {
    case 'no_table':
      return t('cnkpiSplit.empty.noTable', { table: w.source_table })
    case 'not_collected':
      return t('cnkpiSplit.empty.notCollected', { table: w.source_table })
    case 'no_rows':
      return t('cnkpiSplit.empty.noRows', { table: w.source_table })
    default:
      return t('cnkpiSplit.empty.noData')
  }
}

