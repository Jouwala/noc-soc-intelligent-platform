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
