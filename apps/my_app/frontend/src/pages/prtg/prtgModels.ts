/* ============================================================================
 * PRTG 核心链路健康度看板（?page=prtg_links，Issue #228）
 * ----------------------------------------------------------------------------
 * 本文件只放「类型 + 纯展示函数」，页面主体在 PrtgLinkHealth.tsx。
 *
 * 口径来源：backend/services/prtg/dashboard.py 的模块 docstring 与
 * docs/workspace/prtg-channel-dictionary.md。前端**只格式化接口字段**，
 * 不重新推导 KPI / 利用率 / 容量，避免气泡和表格里的数字对不上。
 *
 * 空值约定：接口返回 null 一律显示「—」；0 是真值，照常显示 0。
 * ========================================================================== */

import { formatDateTime } from '../../datetime'
import type { PrtgIncidentSummary, PrtgUnusualReason } from './prtgLogModels'

/** 时间范围（与后端 WINDOWS 的 key 一致） */
export type PrtgWindow = '5m' | '24h' | '7d' | '30d'
export const PRTG_WINDOWS: PrtgWindow[] = ['5m', '24h', '7d', '30d']

/** i18n 的 t() 签名 */
export type TFn = (key: string, params?: Record<string, string | number>) => string

/** 全站统一空值：破折号而不是 0 */
export const NA = '—'

export interface PrtgCollector {
  status?: string | null
  last_run_at?: string | null
  last_success_at?: string | null
  detail?: string | null
  error?: string | null
}

export interface PrtgKpi {
  total?: number | null
  up?: number | null
  unusual?: number | null
  down?: number | null
  unknown?: number | null
  avg_uptime_pct?: number | null
  anomaly_links?: number | null
  uptime_samples?: number | null
}

export interface PrtgTrendPoint {
  t: string
  total_mbps: number | null
}

export interface PrtgOverview {
  generated_at?: string | null
  window?: PrtgWindow
  window_label?: string | null
  collector?: PrtgCollector | null
  data_age_seconds?: number | null
  kpi?: PrtgKpi
  trend?: PrtgTrendPoint[]
  /** P0：设备事件 7 天汇总；合并从未成功时为 null */
  incidents_7d?: PrtgIncidentSummary | null
  /** P0：近 7 天日志最多的设备 */
  top_noisy_device?: { device_name: string; messages: number; share_pct: number; days: number } | null
}

export interface PrtgDataQuality {
  traffic_available?: boolean
  capacity_known?: boolean
  sample_age_seconds?: number | null
}

export interface PrtgLink {
  sensor_id: number
  sensor_name: string
  device_name?: string | null
  group_name?: string | null
  sensor_type?: string | null
  status?: string | null
  status_raw?: number | null
  site_id?: string | null
  region?: string | null
  match_status?: string | null
  in_mbps?: number | null
  out_mbps?: number | null
  total_mbps?: number | null
  unicast_in?: number | null
  unicast_out?: number | null
  errors_in?: number | null
  errors_out?: number | null
  discards_in?: number | null
  discards_out?: number | null
  uptime_pct?: number | null
  downtime_pct?: number | null
  last_down?: string | null
  capacity_mbps?: number | null
  capacity_source?: string | null
  utilization_pct?: number | null
  sample_time?: string | null
  data_quality?: PrtgDataQuality
  /** P0-2：PRTG 当前状态说明（Unusual 时即基线判定原因） */
  last_message?: string | null
  unusual_reason?: PrtgUnusualReason | null
  /** P0-1：日志派生指标；日志采集没跑过为 null */
  down_count_24h?: number | null
  icmp_timeouts_24h?: number | null
  mttr_minutes_7d?: number | null
}

export interface PrtgLinksResponse {
  generated_at?: string | null
  window?: PrtgWindow
  window_label?: string | null
  links?: PrtgLink[]
}

export interface PrtgMapping {
  site_id?: string | null
  region?: string | null
  match_status?: string | null
}

export interface PrtgEvent {
  event_type?: string | null
  from_status?: string | null
  to_status?: string | null
  message?: string | null
  happened_at?: string | null
}

export interface PrtgSeriesPoint {
  t: string
  value: number | null
}

/** 详情接口：基础信息 + 映射 + 事件 + 通道时序（series 的 value 是原始 byte/s） */
export interface PrtgLinkDetailData {
  /** 是否 SNMP Traffic 传感器；Ping 等非流量传感器的通道语义不同（#229 再补） */
  is_traffic?: boolean
  sensor_id: number
  sensor_name: string
  device_name?: string | null
  group_name?: string | null
  sensor_type?: string | null
  status?: string | null
  status_raw?: number | null
  interval_seconds?: number | null
  uptime_pct?: number | null
  downtime_pct?: number | null
  last_up?: string | null
  last_down?: string | null
  last_check?: string | null
  capacity_mbps?: number | null
  capacity_source?: string | null
  last_message?: string | null
  last_message_at?: string | null
  unusual_reason?: PrtgUnusualReason | null
  mappings?: PrtgMapping[]
  events?: PrtgEvent[]
  series?: Record<string, PrtgSeriesPoint[]>
  window?: string | null
  window_label?: string | null
}

/* ------------------------------ 数字格式化 ------------------------------ */

/** 数值：null/undefined/NaN →「—」，其余按 digits 位小数 */
export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return NA
  return v.toFixed(digits)
}

/** 整数：0 照常显示，只有取不到才「—」 */
export function fmtInt(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return NA
  return String(Math.round(v))
}

/** 时间：统一 UTC，取不到显示「—」而不是 '-' */
export function fmtTime(v: string | null | undefined): string {
  return formatDateTime(v, { fallback: NA })
}

/** 时间范围档位的 i18n key（显式 switch，避免动态拼 key 被 check:i18n 拦下） */
export function windowLabelKey(w: PrtgWindow): string {
  switch (w) {
    case '5m': return 'prtg.window.5m'
    case '24h': return 'prtg.window.24h'
    case '7d': return 'prtg.window.7d'
    case '30d': return 'prtg.window.30d'
    default: return 'prtg.window.24h'
  }
}

/* ------------------------------ 状态标签 ------------------------------ */

export function statusLabelKey(status?: string | null): string {
  switch ((status || '').trim().toLowerCase()) {
    case 'up': return 'prtg.status.up'
    case 'unusual': return 'prtg.status.unusual'
    case 'down': return 'prtg.status.down'
    default: return 'prtg.status.unknown'
  }
}

export function statusColor(status?: string | null): string | undefined {
  switch ((status || '').trim().toLowerCase()) {
    case 'up': return 'green'
    case 'unusual': return 'orange'
    case 'down': return 'red'
    default: return undefined
  }
}

export function matchLabelKey(match?: string | null): string {
  switch ((match || '').trim().toLowerCase()) {
    case 'verified': return 'prtg.match.verified'
    case 'auto': return 'prtg.match.auto'
    default: return 'prtg.match.unknown'
  }
}

/* --------------------------- 名称清洗（纯函数） ---------------------------
 * 传感器名常见形态：
 *   "(436232192) ======== OLIB-NX-3172-WAN-2-FG1500-LAN-2-P-39 =========="
 * 设备名常见形态：
 *   "OLIB-NX-3172-WAN-2.orangeliberia (NEXUS 3172) [Cisco Device]"
 * 只做展示清洗，原始值仍放 title / 悬停提示。
 * -------------------------------------------------------------------- */

/** 去掉开头 `(数字)`、去掉 `=` 连串、合并空白；取不到返回「—」 */
export function cleanSensorName(raw?: string | null): string {
  if (raw == null) return NA
  let s = String(raw).trim()
  if (!s) return NA
  s = s.replace(/^\(\d+\)\s*/, '')
  s = s.replace(/=+/g, ' ')
  s = s.replace(/\s+/g, ' ').trim()
  return s || NA
}

/**
 * 设备短名：去掉括号/方括号说明，FQDN 只留主机名第一段。
 * 例：`OLIB-NX-3172-WAN-2.orangeliberia (NEXUS 3172) [Cisco Device]` → `OLIB-NX-3172-WAN-2`
 */
export function shortDeviceName(raw?: string | null): string {
  if (raw == null) return NA
  let s = String(raw).trim()
  if (!s) return NA
  s = s.replace(/\s*\([^)]*\)/g, '')
  s = s.replace(/\s*\[[^\]]*\]/g, '')
  s = s.trim()
  const dot = s.indexOf('.')
  if (dot > 0) s = s.slice(0, dot)
  s = s.replace(/\s+/g, ' ').trim()
  return s || NA
}

/** 站点是否已匹配：site_id 非空且不是 Unknown */
export function isSiteMatched(link: PrtgLink): boolean {
  const site = (link.site_id || '').trim()
  return !!site && site.toLowerCase() !== 'unknown'
}

/** 容量是否已登记：优先 data_quality.capacity_known，否则看 capacity_mbps */
export function isCapacityKnown(link: PrtgLink): boolean {
  if (link.data_quality?.capacity_known === true) return true
  if (link.data_quality?.capacity_known === false) return false
  return link.capacity_mbps != null && Number.isFinite(link.capacity_mbps)
}

/** 组内默认排序：Down > Unusual > Unknown > Up，再 uptime 升序、errors 降序 */
export function statusSortRank(status?: string | null): number {
  switch ((status || '').trim().toLowerCase()) {
    case 'down': return 0
    case 'unusual': return 1
    case 'up': return 3
    default: return 2 // unknown / 其他
  }
}

export function compareLinksDefault(a: PrtgLink, b: PrtgLink): number {
  const sr = statusSortRank(a.status) - statusSortRank(b.status)
  if (sr !== 0) return sr
  const ua = a.uptime_pct
  const ub = b.uptime_pct
  if (ua == null && ub != null) return 1
  if (ua != null && ub == null) return -1
  if (ua != null && ub != null && ua !== ub) return ua - ub
  const ea = (a.errors_in ?? 0) + (a.errors_out ?? 0)
  const eb = (b.errors_in ?? 0) + (b.errors_out ?? 0)
  return eb - ea
}

/** 「需要关注」条目类型（严重度从高到低） */
export type AttentionKind = 'down' | 'unusual' | 'low_uptime' | 'down_24h' | 'errors'

export interface AttentionItem {
  kind: AttentionKind
  link: PrtgLink
}

/**
 * SLA 默认可用率目标（%）。
 * 出处：backend/services/prtg/sla.py::DEFAULT_TARGET = 99.5
 * 与 pages/prtg/PrtgSlaView.tsx 的请求参数 target 同源。
 * 「需要关注」里 uptime_pct 偏低阈值与此一致（严格小于）。
 */
export const PRTG_SLA_TARGET_PCT = 99.5

/** 可用率低于此值视为「偏低」（只做筛选，不重算数值） */
export const UPTIME_LOW_THRESHOLD = PRTG_SLA_TARGET_PCT

/** uptime_pct 是否低于 SLA 目标（null 不判偏低） */
export function isUptimeBelowTarget(uptimePct: number | null | undefined): boolean {
  return uptimePct != null && Number.isFinite(uptimePct) && uptimePct < UPTIME_LOW_THRESHOLD
}

function hasPositiveRate(a?: number | null, b?: number | null): boolean {
  return (a != null && a > 0) || (b != null && b > 0)
}

function attentionKindOf(link: PrtgLink): AttentionKind | null {
  const st = (link.status || '').trim().toLowerCase()
  if (st === 'down') return 'down'
  if (st === 'unusual') return 'unusual'
  if (isUptimeBelowTarget(link.uptime_pct)) return 'low_uptime'
  if ((link.down_count_24h ?? 0) > 0) return 'down_24h'
  if (hasPositiveRate(link.errors_in, link.errors_out) || hasPositiveRate(link.discards_in, link.discards_out)) {
    return 'errors'
  }
  return null
}

const ATTENTION_KIND_RANK: Record<AttentionKind, number> = {
  down: 0,
  unusual: 1,
  low_uptime: 2,
  down_24h: 3,
  errors: 4,
}

/** 按严重度选出最多 limit 条需要关注的链路（只选取，不重算字段） */
export function pickAttentionItems(links: PrtgLink[], limit = 8): AttentionItem[] {
  const items: AttentionItem[] = []
  for (const link of links) {
    const kind = attentionKindOf(link)
    if (kind) items.push({ kind, link })
  }
  items.sort((a, b) => {
    const kr = ATTENTION_KIND_RANK[a.kind] - ATTENTION_KIND_RANK[b.kind]
    if (kr !== 0) return kr
    return compareLinksDefault(a.link, b.link)
  })
  return items.slice(0, limit)
}

/** links 中 uptime_pct 最小值及其链路（并列取第一条；全 null 则 null） */
export function findWorstUptime(links: PrtgLink[]): { link: PrtgLink; uptime_pct: number } | null {
  let best: { link: PrtgLink; uptime_pct: number } | null = null
  for (const link of links) {
    const u = link.uptime_pct
    if (u == null || !Number.isFinite(u)) continue
    if (!best || u < best.uptime_pct) best = { link, uptime_pct: u }
  }
  return best
}

export function eventLabelKey(eventType?: string | null): string {
  switch ((eventType || '').trim().toLowerCase()) {
    case 'status_change': return 'prtg.event.statusChange'
    case 'down': return 'prtg.event.down'
    case 'up': return 'prtg.event.up'
    case 'unusual': return 'prtg.event.unusual'
    default: return 'prtg.event.other'
  }
}

/* --------------------------- 单元格悬浮说明 ---------------------------
 * 遵循 AGENTS.md 三.3c：气泡里只引用接口字段，不重算。
 * 每个函数对应一个数字列 / 时间列，文案里的占位值全部来自入参。
 * ------------------------------------------------------------------- */

function sampleAge(link: PrtgLink): string {
  const age = link.data_quality?.sample_age_seconds
  return age == null ? NA : String(age)
}

export function explainIn(link: PrtgLink, t: TFn): string {
  return t('prtg.cellExplain.in', {
    value: fmtNum(link.in_mbps),
    time: fmtTime(link.sample_time),
    age: sampleAge(link),
  })
}

export function explainOut(link: PrtgLink, t: TFn): string {
  return t('prtg.cellExplain.out', {
    value: fmtNum(link.out_mbps),
    time: fmtTime(link.sample_time),
    age: sampleAge(link),
  })
}

export function explainTotal(link: PrtgLink, t: TFn): string {
  return t('prtg.cellExplain.total', {
    value: fmtNum(link.total_mbps),
    time: fmtTime(link.sample_time),
    age: sampleAge(link),
  })
}

export function explainUnicast(link: PrtgLink, t: TFn): string {
  return t('prtg.cellExplain.unicast', {
    inValue: fmtNum(link.unicast_in),
    outValue: fmtNum(link.unicast_out),
    time: fmtTime(link.sample_time),
  })
}

export function explainErrors(link: PrtgLink, t: TFn): string {
  return t('prtg.cellExplain.errors', {
    inValue: fmtNum(link.errors_in),
    outValue: fmtNum(link.errors_out),
    time: fmtTime(link.sample_time),
  })
}

export function explainDiscards(link: PrtgLink, t: TFn): string {
  return t('prtg.cellExplain.discards', {
    inValue: fmtNum(link.discards_in),
    outValue: fmtNum(link.discards_out),
    time: fmtTime(link.sample_time),
  })
}

export function explainUptime(link: PrtgLink, t: TFn): string {
  return t('prtg.cellExplain.uptime', {
    uptime: fmtNum(link.uptime_pct, 3),
    downtime: fmtNum(link.downtime_pct, 3),
  })
}

export function explainLastDown(link: PrtgLink, t: TFn): string {
  return t('prtg.cellExplain.lastDown', { value: fmtTime(link.last_down) })
}

export function explainCapacity(link: PrtgLink, t: TFn): string {
  return t('prtg.cellExplain.capacity', {
    value: link.capacity_mbps == null ? NA : fmtNum(link.capacity_mbps),
    source: link.capacity_source || NA,
  })
}

export function explainUtilization(link: PrtgLink, t: TFn): string {
  return t('prtg.cellExplain.utilization', { value: fmtNum(link.utilization_pct) })
}

export function explainStatus(link: PrtgLink, t: TFn): string {
  return t('prtg.cellExplain.status', {
    status: link.status || NA,
    raw: fmtInt(link.status_raw),
  })
}

export function explainSite(link: PrtgLink, t: TFn): string {
  return t('prtg.cellExplain.site', {
    site: link.site_id || NA,
    region: link.region || NA,
    match: t(matchLabelKey(link.match_status)),
  })
}


/* ---------------- SLA 与容量（Issue #231） ---------------- */

export interface PrtgSlaLink {
  sensor_id: number
  sensor_name: string
  device_name?: string | null
  status?: string | null
  site_id?: string | null
  region?: string | null
  availability_pct?: number | null
  uptime_pct_all_time?: number | null
  downtime_hours_month?: number | null
  downtime_hours_covered?: number | null
  downtime_hours_unknown_coverage?: number | null
  unknown_coverage_days?: number | null
  coverage_hours_month?: number | null
  down_count?: number | null
  last_down?: string | null
  uptime_days?: number | null
  target_pct?: number | null
  below_target?: boolean
  basis?: string | null
}

export interface PrtgSlaReport {
  generated_at?: string
  month: string
  target_pct: number
  summary: {
    links: number
    below_target: number
    avg_availability_pct?: number | null
    total_downtime_hours_month?: number | null
    capacity_known: number
    capacity_total: number
    sla_days_recorded: number
  }
  method?: Record<string, string>
  links: PrtgSlaLink[]
  below_target: PrtgSlaLink[]
  region_ranking: Array<{ region: string; links: number; avg_availability_pct: number; below_target: number }>
}

export interface PrtgCapacityRow {
  sensor_id: number
  sensor_name?: string | null
  device_name?: string | null
  capacity_mbps?: number | null
  capacity_source?: string | null
  capacity_known: boolean
  peak_24h_in_mbps?: number | null
  peak_24h_out_mbps?: number | null
  peak_7d_in_mbps?: number | null
  peak_7d_out_mbps?: number | null
  p95_30d_in_mbps?: number | null
  p95_30d_out_mbps?: number | null
  utilization_pct?: number | null
  level?: string
}
