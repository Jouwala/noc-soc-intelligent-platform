/* ============================================================================
 * CS / PS CN KPI 15 分钟看板 —— 图表与口径说明的公共实现
 * ----------------------------------------------------------------------------
 * 两个页面（?page=cs_cn_kpi_15mins / ?page=ps_cn_kpi_15mins）共用本文件：
 *   · 类型定义（与 backend/services/cn/cn_kpi_15mins_service.py 对齐）
 *   · ECharts option：**颜色 = 维度**（TONE/TC/RTP、Used/Authorized…），
 *                     **线型 = 日子**（D0 实线 / D1 虚线 / D7 点线）
 *   · 卡片标题 ⓘ 的「这一格怎么算」文案（来源表 / 粒度 / 行数 / SQL）
 *
 * 为什么不是「一天一种颜色」：参考页每张卡本身就有 2~4 条维度线（TONE/TC/RTP、
 * 2G/3G/4G/5G…），再叠三天会有 6~12 条线。用颜色区分维度、线型区分日子，
 * 图例只占一行、也不丢参考页的维度信息。ECharts 颜色必须用实值（canvas 解析
 * 不了 CSS 变量），深浅各一套（AGENTS.md 三.3）。
 * ========================================================================== */
import type { EChartsOption } from 'echarts'
import { axisLabel, axisLine, darkTooltip } from '../../theme'

export type Grain15 = '' | '15m' | '5m' | '1h'

export type Widget15 = {
  id: string
  title: string
  chart: string
  pivot: boolean
  pct: boolean
  unit: string
  /** 源数据粒度：15m=原生 15 分钟；5m=5 分钟聚到 15 分钟桶；1h=上游只有小时档 */
  grain: Grain15
  dims: string[]
  source_table: string
  sql: string
  columns: string[]
  rows: (string | number | null)[][]
  error: string
  empty: boolean
  empty_code: '' | 'no_table' | 'no_rows' | 'no_values'
}

export type Overview15 = {
  meta: {
    db: string
    host: string
    board: string
    count: number
    failed: number
    empty: number
    elapsed_ms: number
    cached: boolean
    window: { d0: string; d1: string; d7: string }
  }
  title: string
  widgets: Widget15[]
}

type TFn = (key: string, params?: Record<string, string | number>) => string

// 日子配色（实值）：一张卡只有一个指标，颜色直接表示 D0/D1/D7。
// 与 ?page=cn_kpi_5mins 同一套 Day0 绿 / Day1 蓝 / Day7 青，用户跨页看不会串色。
const DAY_COLOR_DARK: Record<string, string> = {
  D0: '#08bd83', D1: '#2b8cff', D7: '#00d9ff',
}
const DAY_COLOR_LIGHT: Record<string, string> = {
  D0: '#0e9f6e', D1: '#2563eb', D7: '#0e7490',
}

function dayColor(day: string, isDark: boolean) {
  return (isDark ? DAY_COLOR_DARK : DAY_COLOR_LIGHT)[day] || '#8b95a7'
}

const DAYS = ['D0', 'D1', 'D7'] as const
type Day = (typeof DAYS)[number]

/** 日子的线型：D0 实线、D1 虚线、D7 点线（粗细也递减，D0 最醒目） */
function dayStyle(day: string) {
  switch (day) {
    case 'D0':
      return { type: 'solid' as const, width: 2.2, opacity: 1 }
    case 'D1':
      return { type: 'dashed' as const, width: 1.5, opacity: 0.9 }
    default:
      return { type: 'dotted' as const, width: 1.5, opacity: 0.85 }
  }
}

function toNumber(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

/** 数值格式：百分比 2 位、计数 0 位、GB/Mbps/Erl 2 位，统一带单位。 */
function formatNum(n: number, w: Widget15, decimals?: number) {
  if (w.pct) return `${n.toFixed(decimals ?? 2)}%`
  const dp = decimals ?? (w.unit === 'count' || w.unit === 'subs' ? 0 : w.unit === 'GB' ? 3 : 2)
  const txt = n.toLocaleString('en-US', { maximumFractionDigits: dp })
  return w.unit ? `${txt} ${w.unit}` : txt
}

/** 轴标签抽稀：15 分钟 × 一天 96 点，不抽稀会糊成一条带。 */
function tickInterval(n: number) {
  if (n > 60) return Math.ceil(n / 24) - 1
  if (n > 24) return Math.ceil(n / 12) - 1
  return 'auto' as const
}

/** 序列显示名：单维度卡只显示 D0/D1/D7；多维度卡显示「维度 · 日」。 */
export function seriesName(w: Widget15, dim: string, day: string) {
  return w.dims.length > 1 ? `${dim} · ${day}` : day
}

/**
 * option 构造。rows = [HH:MM, 维度, 日, 值]：
 *   · 颜色按维度取（同名维度恒同色）；
 *   · 线型按日子取（D0 实线 / D1 虚线 / D7 点线）；
 *   · 横轴取所有行里出现过的时刻并集 —— 小时源组件自然只有 24 个整点，
 *     不做前向填充、也不把没有采样的小时档画成 96 个空点。
 */
export function buildOption15(w: Widget15, isDark: boolean): EChartsOption {
  const cats: string[] = []
  const seenCat = new Set<string>()
  const dims: string[] = []
  const days: string[] = []
  const lookup = new Map<string, number | null>()

  for (const r of w.rows) {
    const t = String(r[0] ?? '')
    const dim = String(r[1] ?? '')
    const day = String(r[2] ?? '')
    if (!t || !day) continue
    if (!seenCat.has(t)) { seenCat.add(t); cats.push(t) }
    if (dim && !dims.includes(dim)) dims.push(dim)
    if (!days.includes(day)) days.push(day)
    lookup.set(`${t}\u0000${dim}\u0000${day}`, toNumber(r[3]))
  }
  cats.sort()
  // 日序固定 D0 → D1 → D7；维度按后端 dims 顺序（图例与参考页一致）
  const dayOrder = [...DAYS].filter((d) => days.includes(d))
  const dimOrder = w.dims.filter((d) => dims.includes(d))
  for (const d of dims) if (!dimOrder.includes(d)) dimOrder.push(d)

  const dense = cats.length > 120
  const fmt = (v: unknown) => {
    const n = toNumber(v)
    return n === null ? '—' : formatNum(n, w)
  }

  // 每张卡只有一个指标 → 三条线 = D0/D1/D7（颜色+线型都区分日子）。
  const series = dimOrder.flatMap((dim) =>
    dayOrder.map((day) => {
      const st = dayStyle(day)
      const c = dayColor(day, isDark)
      return {
        name: seriesName(w, dim, day),
        type: 'line' as const,
        smooth: false,
        showSymbol: false,
        connectNulls: false,
        sampling: dense ? ('lttb' as const) : undefined,
        lineStyle: { width: st.width, type: st.type, opacity: st.opacity },
        itemStyle: { color: c, opacity: st.opacity },
        color: c,
        data: cats.map(
          (t) => lookup.get(`${t}\u0000${dim}\u0000${day}`) ?? null,
        ),
      }
    }),
  )

  return {
    tooltip: { ...darkTooltip(isDark), trigger: 'axis', valueFormatter: fmt },
    // 一张卡只有 D0/D1/D7 三条线，ECharts 图例正好三个色块；可点选隐藏。
    legend: {
      top: 0,
      type: 'scroll',
      icon: 'roundRect',
      show: dayOrder.length > 1,
      data: dayOrder.map((d) => seriesName(w, dimOrder[0] || '', d)),
      textStyle: { color: isDark ? '#aebbd4' : '#3f5468', fontSize: 11 },
      itemWidth: 14,
      itemHeight: 8,
      itemGap: 12,
    },
    grid: { left: 12, right: 14, top: 26, bottom: 20, containLabel: true },
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
      splitLine: { show: true, lineStyle: { type: 'dashed', opacity: 0.22 } },
      axisLabel: {
        ...axisLabel(isDark),
        formatter: w.pct
          ? (v: number) => `${Number(v).toFixed(Math.abs(Number(v)) < 10 ? 2 : 0)}%`
          : undefined,
      },
    },
    series,
  }
}

/** 粒度角标文案键（显式 switch：动态拼 key 会被 check:i18n 拦下）。 */
export function grainKey(grain: Grain15) {
  switch (grain) {
    case '15m': return 'cnkpi15.gran.15m'
    case '5m': return 'cnkpi15.gran.5m'
    case '1h': return 'cnkpi15.gran.1h'
    default: return ''
  }
}

/** 卡片标题 ⓘ：来源表 / 源粒度 / 返回行数 / 实际执行 SQL。 */
export function widgetExplainText(w: Widget15, t: TFn): string {
  if (w.error) return ''
  const gk = grainKey(w.grain)
  return [
    t('cnkpi15.explain.source', { table: w.source_table || '—' }),
    gk ? t(gk) : '',
    t('cnkpi15.explain.rows', { n: w.rows.length }),
    t('cnkpi15.explain.dims', { dims: w.dims.join(' / ') || '—' }),
    t('cnkpi15.explain.grouped', { dim: w.dims[0] || '—' }),
    w.sql ? t('cnkpi15.explain.sql', { sql: w.sql }) : '',
  ].filter(Boolean).join('\n')
}

/** 空态原因：把表名带上，用户能直接去查同步任务。 */
export function emptyReason15(w: Widget15, t: TFn): string {
  switch (w.empty_code) {
    case 'no_table':
      return t('cnkpi15.empty.noTable', { table: w.source_table })
    case 'no_rows':
      return t('cnkpi15.empty.noRows', { table: w.source_table })
    case 'no_values':
      return t('cnkpi15.empty.noValues', { dim: w.dims[0] || '—' })
    default:
      return t('cnkpi15.empty.noData')
  }
}
