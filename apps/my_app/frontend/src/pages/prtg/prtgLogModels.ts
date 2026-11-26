/* ============================================================================
 * PRTG 日志与设备事件：类型 + 纯展示函数（P0，2026-11-26）
 * ----------------------------------------------------------------------------
 * 后端：backend/services/prtg/logs.py、incidents.py；接口 /api/prtg/logs、/api/prtg/incidents。
 * 前端只格式化接口字段，不重算任何计数 / 时长；取不到一律「—」。
 * i18n key 用显式 switch 返回（check:i18n 不允许动态拼 key）。
 * ========================================================================== */

import { NA, type TFn } from './prtgModels'

export type PrtgLogReason =
  | 'icmp_timeout' | 'snmp_no_response' | 'unusual_low' | 'unusual_high'
  | 'paused' | 'value' | 'other'

export const PRTG_LOG_REASONS: PrtgLogReason[] = [
  'icmp_timeout', 'snmp_no_response', 'unusual_low', 'unusual_high', 'paused', 'value', 'other',
]

export interface PrtgLogRow {
  id: number
  sensor_id?: number | null
  sensor_name?: string | null
  device_name?: string | null
  sensor_type?: string | null
  happened_at?: string | null
  status?: string | null
  status_raw?: number | null
  message?: string | null
  reason?: string | null
  value_num?: number | null
  value_unit?: string | null
}

/** PRTG 基线判定原文解析结果（links 接口的 unusual_reason） */
export interface PrtgUnusualReason {
  window?: string | null
  value?: number | null
  unit?: string | null
  value_mbps?: number | null
  channel?: string | null
  direction?: string | null
  period?: string | null
}

export interface PrtgIncidentSensor {
  sensor_id?: number | null
  sensor_name?: string | null
  sensor_type?: string | null
  down_at?: string | null
  up_at?: string | null
}

export interface PrtgIncidentEvidence {
  log_count?: number | null
  icmp_timeouts?: number | null
  has_ping_sensor?: boolean | null
  merge_window_seconds?: number | null
  flaps?: number | null
}

export interface PrtgIncident {
  id: number
  device_name: string
  device_id?: number | null
  started_at?: string | null
  ended_at?: string | null
  is_open: boolean
  kind?: string | null
  sensor_count?: number | null
  duration_seconds?: number | null
  sensors?: PrtgIncidentSensor[]
  evidence?: PrtgIncidentEvidence | null
}

export interface PrtgIncidentSummary {
  days: number
  total: number
  open: number
  by_kind?: Record<string, number>
}

export function reasonLabelKey(reason?: string | null): string {
  switch (reason) {
    case 'icmp_timeout': return 'prtg.logReason.icmpTimeout'
    case 'snmp_no_response': return 'prtg.logReason.snmpNoResponse'
    case 'unusual_low': return 'prtg.logReason.unusualLow'
    case 'unusual_high': return 'prtg.logReason.unusualHigh'
    case 'paused': return 'prtg.logReason.paused'
    case 'value': return 'prtg.logReason.value'
    default: return 'prtg.logReason.other'
  }
}

export function reasonColor(reason?: string | null): string | undefined {
  switch (reason) {
    case 'icmp_timeout': return 'red'
    case 'snmp_no_response': return 'volcano'
    case 'unusual_low': return 'gold'
    case 'unusual_high': return 'orange'
    case 'paused': return 'default'
    default: return undefined
  }
}

export function incidentKindLabelKey(kind?: string | null): string {
  switch (kind) {
    case 'device_unreachable': return 'prtg.incidents.kind.deviceUnreachable'
    case 'snmp_mgmt': return 'prtg.incidents.kind.snmpMgmt'
    case 'single_link': return 'prtg.incidents.kind.singleLink'
    default: return 'prtg.incidents.kind.other'
  }
}

export function incidentKindColor(kind?: string | null): string | undefined {
  switch (kind) {
    case 'device_unreachable': return 'red'
    case 'snmp_mgmt': return 'orange'
    case 'single_link': return 'gold'
    default: return undefined
  }
}

/** 秒 → "1h 05m" / "3m 20s" / "45s"；null 显示「—」（未结束事件没有时长，不是 0） */
export function fmtDuration(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return NA
  const s = Math.max(0, Math.round(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`
  if (m > 0) return `${m}m ${String(sec).padStart(2, '0')}s`
  return `${sec}s`
}

/** 状态列悬停：PRTG 原因原文 + 结构化解析（有就带上） */
export function explainStatusReason(
  status: string | null | undefined,
  raw: number | null | undefined,
  lastMessage: string | null | undefined,
  unusual: PrtgUnusualReason | null | undefined,
  t: TFn,
): string {
  const base = t('prtg.cellExplain.statusReason', {
    status: status || NA,
    raw: raw == null ? NA : String(raw),
    message: lastMessage || NA,
  })
  if (!unusual) return base
  return `${base}\n${t('prtg.cellExplain.unusualParsed', {
    window: unusual.window || NA,
    channel: unusual.channel || NA,
    value: unusual.value_mbps == null ? NA : unusual.value_mbps.toFixed(3),
    direction: unusual.direction === 'high' ? t('prtg.unusual.high') : t('prtg.unusual.low'),
    period: unusual.period || NA,
  })}`
}
