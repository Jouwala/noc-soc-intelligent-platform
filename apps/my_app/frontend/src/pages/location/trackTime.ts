/* ============================================================================
 * location/trackTime —— 给 CCA 驻留小区补「最新出现时间」
 * ============================================================================
 * 背景（2026-11-23 用户问「Subscriber track 能添加时间信息吗」）：
 *   · CCA 的 usertrack/query_cell **本身不带时间戳**（实测字段只有
 *     cellname / cell / celllat / celllon / pci / rat 等，没有 start_time）。
 *   · 但 VMAX 诊断的 1100 / 1300 / 1301 行带 `start_time` 和 cell 标识，
 *     可以按小区匹配出「该窗口内最后一次出现在信令/用户面记录里的时间」。
 *   · 列表顺序按用户确认的 CCA 倒序：**第 1 条是最新**；时间列只是补充，
 *     匹配不到就显示 —，不代表「没去过」，只代表该窗口内没有对应记录。
 *
 * 本模块只做纯计算：输入诊断 sources，输出「小区键 → {最后时间, 命中条数}」。
 * 键同时索引 cellname / cell / cellid / cell_id 并做 trim+lower，
 * 因为上游不同表的列名与大小写并不统一。
 * ========================================================================== */
import type { SourceResult } from '../vmaxDiagnosisRules'

export type TrackTime = {
  /** 该小区在诊断记录里最后一次出现的时间（字符串，按同格式可直接比较） */
  lastSeen: string
  /** 命中条数（同一小区出现多少次记录） */
  hits: number
}

/** 诊断行里可能装时间的字段（不同源列名不同，按优先级取第一个非空）。 */
const TIME_KEYS = ['start_time', 'begin_time', 'starttime', 'time'] as const
/** 诊断行里可能装小区标识的字段（名称与 ID 都会入索引）。 */
const CELL_KEYS = ['cellname', 'cell_name', 'cell', 'cellid', 'cell_id'] as const

const norm = (v: unknown): string => String(v ?? '').trim().toLowerCase()

function rowTime(row: Record<string, unknown>): string {
  for (const k of TIME_KEYS) {
    const v = row[k]
    if (v != null && String(v).trim()) return String(v).trim()
  }
  return ''
}

/** 汇总所有源里出现的「小区 → 最后时间」。 */
export function buildTrackTimes(sources: SourceResult[]): Map<string, TrackTime> {
  const out = new Map<string, TrackTime>()
  for (const s of sources) {
    for (const row of s.rows || []) {
      const time = rowTime(row as Record<string, unknown>)
      if (!time) continue
      const keys = new Set<string>()
      for (const k of CELL_KEYS) {
        const key = norm((row as Record<string, unknown>)[k])
        if (key) keys.add(key)
      }
      for (const key of keys) {
        const prev = out.get(key)
        if (!prev) {
          out.set(key, { lastSeen: time, hits: 1 })
        } else {
          out.set(key, {
            lastSeen: time > prev.lastSeen ? time : prev.lastSeen,
            hits: prev.hits + 1,
          })
        }
      }
    }
  }
  return out
}

/** 按 CCA 小区的名称 / 小区号查时间（任一命中即可）。 */
export function timeOfCell(
  times: Map<string, TrackTime>,
  cell: { cellname?: unknown; cell?: unknown },
): TrackTime | undefined {
  const byName = times.get(norm(cell.cellname))
  if (byName) return byName
  return times.get(norm(cell.cell))
}

/** 展示用：同一天只显示 MM-DD HH:mm:ss；原串放在 title 里由调用方处理。 */
export function fmtSeen(t?: string): string {
  if (!t) return '—'
  const m = String(t).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)/)
  if (!m) return String(t)
  return `${m[2]}-${m[3]} ${m[4]}`
}
