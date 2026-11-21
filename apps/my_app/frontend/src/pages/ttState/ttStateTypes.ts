/* ==================== TT 州维度统计：类型与纯函数 ====================
 * 页面 `?page=tt_state` 的数据契约（与 backend/services/tt_state_matrix.py 对齐）。
 * 只放类型与无副作用的小工具，渲染在 TtStateMatrixTable / TtStateDrillModal。
 * ================================================================ */

export type Gran = 'day' | 'week' | 'month'
export type Preset = 'last7' | 'last30' | 'last90' | 'last180' | 'all' | 'custom'
export type DrillFlag = '' | 'alarm_uncleared' | 'wo_unclosed' | 'tt_unclosed' | 'alarm_cleared'

export type TFn = (key: string, params?: Record<string, string | number>) => string

/** 一个州在窗口内的合计（也是「合计」行 / KPI 卡的形状） */
export interface StatTotals {
  state: string
  total: number
  alarm_uncleared: number
  wo_unclosed: number
  tt_unclosed: number
  alarm_cleared: number
}

/** 一个「州 × 时间桶」的格子 */
export interface CellStat extends Omit<StatTotals, 'state'> {
  state: string
  period: string
}

export interface OverviewMeta {
  start: string
  end: string
  gran: Gran
  all_time: boolean
  generated_at: string
  row_count: number
  skipped_rows: number
  wo_missing: number
  warnings: string[]
}

export interface Overview {
  meta: OverviewMeta
  periods: string[]
  states: StatTotals[]
  cells: CellStat[]
  summary: StatTotals
  error?: string
}

export interface DrillRow {
  state: string
  tt_no: string
  wo_no: string
  site_name: string
  alarm_occur_time: string
  alarm_clear_time: string
  tt_status: string
  wo_status: string
  alarm_uncleared: boolean
  wo_closed: boolean
  tt_unclosed: boolean
}

export interface DrillMeta {
  start?: string
  end?: string
  gran?: Gran
  all_time?: boolean
  state?: string
  period?: string
  flag?: DrillFlag
  count: number
  returned: number
  truncated: boolean
  limit: number
  warnings?: string[]
}

export interface DrillResult {
  meta: DrillMeta
  rows: DrillRow[]
  error?: string
}

/** 未标注州的哨兵值（后端 region_level1 为空的归到这里） */
export const UNKNOWN_STATE = '__unknown__'
/** 下钻「不限州」的哨兵值（KPI 卡下钻用） */
export const ALL_STATES = '*'

/** 空统计对象：格子缺失时按 0 补齐，不把「没有单」显示成「取数失败」 */
export const ZERO: Omit<StatTotals, 'state'> = {
  total: 0, alarm_uncleared: 0, wo_unclosed: 0, tt_unclosed: 0, alarm_cleared: 0,
}

export function stateLabel(state: string, t: TFn): string {
  return state === UNKNOWN_STATE ? t('ttState.unknownState') : state
}

/** 把若干州的统计相加（KPI 卡在州筛选后必须跟着变，不能继续显示后端全量 summary） */
export function sumStats(list: StatTotals[]): Omit<StatTotals, 'state'> {
  const out = { ...ZERO }
  for (const s of list) {
    out.total += s.total
    out.alarm_uncleared += s.alarm_uncleared
    out.wo_unclosed += s.wo_unclosed
    out.tt_unclosed += s.tt_unclosed
    out.alarm_cleared += s.alarm_cleared
  }
  return out
}

export function cellKey(state: string, period: string): string {
  return `${state}\u0001${period}`
}

/** 单元格三格的短标签：A / B / C，图例与悬停说明共用这一份顺序 */
export function pct(part: number, whole: number): string {
  if (!whole) return '—'
  return ((part / whole) * 100).toFixed(1)
}

/** 表头主/副行：日与周把年份放副行，月直接 2026-11（避免 13 列里年份重复 13 次） */
export function periodHead(period: string, gran: Gran): { main: string; sub: string } {
  if (gran === 'month') return { main: period, sub: '' }
  if (gran === 'week') return { main: period.slice(5), sub: period.slice(0, 4) }
  return { main: period.slice(5), sub: period.slice(0, 4) }
}

/** 时间窗的展示文案：全部时间 / 实际区间（后端会回填真实 start/end） */
export function windowLabel(meta: OverviewMeta | null | undefined, t: TFn): string {
  if (!meta) return '—'
  if (meta.all_time) return t('ttState.preset.all')
  return `${meta.start} ~ ${meta.end}`
}
