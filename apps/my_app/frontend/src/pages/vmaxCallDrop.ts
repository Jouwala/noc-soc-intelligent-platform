/* ============================================================================
 * vmaxCallDrop.ts —— 数据通话被电路域来电打断
 * ============================================================================
 * 语音卡的「回落耗时」量的是 Extended Service Request 到 UE context release
 * 的过程耗时（通常几毫秒到 2 秒）。投诉里说的是另一件事：人已经在用数据
 * 打电话（WhatsApp），电路域来电把 4G 拆掉，电话挂了才回来，数据通话也断了。
 *
 * 这里把 1100 配成一轮一轮的离开/回来：
 *   CS Fallback Triggered 的释放  →  之后 3 分钟内的第一次 TAU = 回到 4G
 * 同一秒里的 CS Service Notification 标成被叫。TAU 后 3 秒内的承载修改
 * 原因单独留出来——回来时数据承载是重建的，不是原样恢复。
 *
 * 规则层只产出 { key, params }，不写会显示给人看的句子。
 * ========================================================================== */

import { tx, type I18nText, type SourceResult, type XdrRow } from './vmaxDiagnosisRules'

/** 离开 4G 到回来，超过这个间隔就不再配成同一轮（避免配到很后面的周期性 TAU） */
const AWAY_MAX_MS = 3 * 60 * 1000
/** 释放前多少毫秒内的 ESR / CS Service Notification 算同一轮 */
const SETUP_MS = 5 * 1000
/** TAU 前后多少毫秒内的承载修改算「回来时重建」 */
const BEARER_MS = 3 * 1000
/** 两次 Inter-RAT Redirection 间隔小于这个值，并成一簇 */
const REDIRECT_GAP_MS = 90 * 1000
const REDIRECT_CAP = 8

export type CallDropKind = 'mt' | 'mo'

export type CallDropCycle = {
  leaveAt: string
  leaveCell: string
  returnAt: string
  returnCell: string
  awaySec: number | null
  kind: CallDropKind
  bearerCause: string
}

export type CallDropReport = {
  /** 1100 没取到时为 false，页面直接说信令缺失，不写成「没有回落」 */
  signalOk: boolean
  truncated: boolean
  cycles: CallDropCycle[]
  /** 有回落，或有 Inter-RAT Redirection */
  hasActivity: boolean
  headline: I18nText
  notes: I18nText[]
}

export type CallDropContext = {
  cells?: { rat?: number | string; cellname?: string }[] | null
  causes?: { rule_name?: string; object_name?: string; poortime?: string; description?: string }[] | null
}

type Timed = { ms: number; row: XdrRow }

const str = (v: unknown): string => (v == null ? '' : String(v).trim())
const up = (v: unknown): string => str(v).toUpperCase()

function stamp(v: unknown): string {
  return str(v).replace('T', ' ').slice(0, 19)
}

/** 用字段里的挂钟时间做差，不经过本地时区，避免浏览器时区把间隔算偏 */
function parseMs(v: unknown): number | null {
  const m = stamp(v).match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/)
  if (!m) return null
  return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6])
}

function cellOf(row: XdrRow): string {
  const name = str(row.cellname || row.cell || row.cell_name)
  return name && name !== '-' ? name : ''
}

function procOf(row: XdrRow): string {
  return up(row.procedure_type)
}

function causeOf(row: XdrRow): string {
  return up(row.request_cause)
}

function isCsfbRelease(row: XdrRow): boolean {
  return procOf(row).includes('CONTEXT RELEASE') && causeOf(row).includes('CS FALLBACK')
}

function isInterrat(row: XdrRow): boolean {
  return procOf(row).includes('CONTEXT RELEASE') && causeOf(row).includes('INTERRAT')
}

function isTau(row: XdrRow): boolean {
  const proc = procOf(row)
  return proc === 'TAU' || proc.includes('TRACKING AREA')
}

function isBearer(row: XdrRow): boolean {
  const proc = procOf(row)
  return proc.includes('BEARER') || proc.includes('E-RAB')
}

function usableCause(row: XdrRow): string {
  const cause = str(row.request_cause)
  const token = cause.toUpperCase()
  if (!cause || token === 'UNKNOWN' || token === '255' || token === '-') return ''
  return cause
}

function rowsOf(sources: SourceResult[], key: string): XdrRow[] {
  return sources.find((s) => s.key === key)?.rows || []
}

function timed(rows: XdrRow[], timeKey: 'start_time' | 'btime'): Timed[] {
  const out: Timed[] = []
  for (const row of rows) {
    const ms = parseMs(row[timeKey] || row.start_time || row.btime)
    if (ms == null) continue
    out.push({ ms, row })
  }
  out.sort((a, b) => a.ms - b.ms)
  return out
}

function topApps(rows: XdrRow[]): { named: boolean; label: string } {
  const counts = new Map<string, number>()
  let named = false
  for (const row of rows) {
    const blob = `${up(row.app_content_name)} ${up(row.subservice)} ${up(row.protocol)}`
    if (blob.includes('WHATSAPP') || blob.includes('STUN')) named = true
    const name = str(row.app_content_name || row.subservice || row.service)
    if (!name || name === '-') continue
    counts.set(name, (counts.get(name) || 0) + 1)
  }
  const label = [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([name, n]) => `${name} (${n})`)
    .join(', ')
  return { named, label: label || '—' }
}

function ratLine(rows: XdrRow[]): { text: string; has5g: boolean } {
  const counts = new Map<string, number>()
  for (const row of rows) {
    const rat = str(row.rat_name) || '—'
    counts.set(rat, (counts.get(rat) || 0) + 1)
  }
  const text = [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([rat, n]) => `${rat} × ${n}`)
    .join(', ')
  const has5g = [...counts.keys()].some((rat) => /5G/i.test(rat) || /\bNR\b/.test(rat))
  return { text: text || '—', has5g }
}

function radioNote(rows: XdrRow[]): I18nText | null {
  const rsrp: number[] = []
  const rsrq: number[] = []
  for (const row of rows) {
    const p = Number(row.serving_rsrp)
    const q = Number(row.serving_rsrq)
    if (Number.isFinite(p) && p <= -40 && p >= -140) rsrp.push(p)
    if (Number.isFinite(q) && q <= -3 && q >= -40) rsrq.push(q)
  }
  if (!rsrp.length && !rsrq.length) return null
  const worstRsrq = rsrq.length ? Math.min(...rsrq) : null
  const minR = rsrp.length ? Math.min(...rsrp) : null
  const maxR = rsrp.length ? Math.max(...rsrp) : null
  return tx('vmaxq.vd.calldrop.radio', {
    n: rows.length,
    rsrp: minR == null || maxR == null ? '—' : `${maxR} ~ ${minR}`,
    rsrq: worstRsrq == null ? '—' : String(worstRsrq),
  })
}

function otherRatNote(cells: CallDropContext['cells']): I18nText | null {
  if (!cells?.length) return null
  const seen = new Set<string>()
  const parts: string[] = []
  for (const cell of cells) {
    const rat = str(cell.rat)
    if (!rat || rat === '6') continue
    const name = str(cell.cellname) || '—'
    const token = `${rat} ${name}`
    if (seen.has(token)) continue
    seen.add(token)
    parts.push(token)
  }
  if (!parts.length) return null
  return tx('vmaxq.vd.calldrop.otherRat', { cells: parts.join(', ') })
}

function ottNotes(causes: CallDropContext['causes']): I18nText[] {
  if (!causes?.length) return []
  const out: I18nText[] = []
  for (const item of causes) {
    const rule = str(item.rule_name)
    if (!/OTT/i.test(rule) || !/VOICE|VIDEO/i.test(rule)) continue
    const detail = str(item.description).replace(/\s+/g, ' ').slice(0, 180)
    out.push(tx('vmaxq.vd.calldrop.ottCell', {
      cell: str(item.object_name) || '—',
      when: str(item.poortime) || '—',
      detail: detail || rule,
    }))
    if (out.length >= 4) break
  }
  return out
}

function pairCycles(rows: Timed[]): CallDropCycle[] {
  const releases = rows.filter((item) => isCsfbRelease(item.row))
  const kept: Timed[] = []
  for (const item of releases) {
    const prev = kept[kept.length - 1]
    if (prev && item.ms - prev.ms < 2000) continue
    kept.push(item)
  }
  return kept.map((leave) => {
    const setup = rows.filter((item) => item.ms <= leave.ms && leave.ms - item.ms <= SETUP_MS)
    const kind: CallDropKind = setup.some((item) => procOf(item.row).includes('CS SERVICE')) ? 'mt' : 'mo'
    const back = rows.find((item) => item.ms > leave.ms && item.ms - leave.ms <= AWAY_MAX_MS && isTau(item.row))
    const bearerCause = back
      ? [...new Set(rows
        .filter((item) => Math.abs(item.ms - back.ms) <= BEARER_MS && isBearer(item.row))
        .map((item) => usableCause(item.row))
        .filter(Boolean))]
        .join(' / ')
      : ''
    return {
      leaveAt: stamp(leave.row.start_time),
      leaveCell: cellOf(leave.row),
      returnAt: back ? stamp(back.row.start_time) : '',
      returnCell: back ? cellOf(back.row) : '',
      awaySec: back ? Math.round((back.ms - leave.ms) / 1000) : null,
      kind,
      bearerCause,
    }
  })
}

function redirectNotes(rows: Timed[]): I18nText[] {
  const events = rows.filter((item) => isInterrat(item.row))
  const clusters: Timed[][] = []
  for (const item of events) {
    const last = clusters[clusters.length - 1]
    const prev = last?.[last.length - 1]
    if (prev && item.ms - prev.ms <= REDIRECT_GAP_MS) last.push(item)
    else clusters.push([item])
  }
  const notes = clusters.slice(0, REDIRECT_CAP).map((group) => {
    const cells = [...new Set(group.map((item) => cellOf(item.row)).filter(Boolean))]
    return tx('vmaxq.vd.calldrop.redirect', {
      start: stamp(group[0].row.start_time).slice(11),
      end: stamp(group[group.length - 1].row.start_time).slice(11),
      n: group.length,
      cells: cells.join(', ') || '—',
    })
  })
  if (clusters.length > REDIRECT_CAP) {
    notes.push(tx('vmaxq.vd.calldrop.redirectMore', { n: clusters.length - REDIRECT_CAP }))
  }
  return notes
}

export function analyzeCallDrop(
  sources: SourceResult[],
  context: CallDropContext = {},
): CallDropReport {
  const signal = sources.find((s) => s.key === '1100')
  if (!signal || signal.ok === false) {
    return {
      signalOk: false,
      truncated: false,
      cycles: [],
      hasActivity: false,
      headline: tx('vmaxq.vd.calldrop.noSignal'),
      notes: signal?.error ? [tx('vmaxq.vd.calldrop.noSignalWhy', { why: signal.error })] : [],
    }
  }

  const flow = rowsOf(sources, '1300')
  const sig = timed(rowsOf(sources, '1100'), 'start_time')
  const cycles = pairCycles(sig)
  const redirects = redirectNotes(sig)
  const apps = topApps(flow)
  const rats = ratLine(flow)
  const qci1 = flow.some((row) => str(row.qci) === '1')
  const notes: I18nText[] = []

  if (!qci1) notes.push(tx('vmaxq.vd.calldrop.noQci'))
  notes.push(tx(rats.has5g ? 'vmaxq.vd.calldrop.rat5g' : 'vmaxq.vd.calldrop.rat', { rats: rats.text }))
  notes.push(tx(apps.named ? 'vmaxq.vd.calldrop.dpiNamed' : 'vmaxq.vd.calldrop.dpi', { apps: apps.label }))
  const others = otherRatNote(context.cells)
  if (others) notes.push(others)
  notes.push(...redirects)
  const radio = radioNote(rowsOf(sources, '32'))
  if (radio) notes.push(radio)
  notes.push(...ottNotes(context.causes))
  if (signal.truncated) notes.push(tx('vmaxq.vd.calldrop.truncated'))

  const aways = cycles.map((c) => c.awaySec).filter((n): n is number => n != null)
  const causes = [...new Set(cycles.map((c) => c.bearerCause).filter(Boolean))]
  let headline: I18nText
  if (!cycles.length) headline = tx('vmaxq.vd.calldrop.none')
  else if (!aways.length) {
    headline = tx('vmaxq.vd.calldrop.headlineNoReturn', { n: cycles.length })
  } else {
    headline = tx('vmaxq.vd.calldrop.headline', {
      n: cycles.length,
      min: Math.min(...aways),
      max: Math.max(...aways),
      cause: causes.join(' / ') || '—',
    })
  }

  return {
    signalOk: true,
    truncated: !!signal.truncated,
    cycles,
    hasActivity: cycles.length > 0 || redirects.length > 0,
    headline,
    notes,
  }
}
