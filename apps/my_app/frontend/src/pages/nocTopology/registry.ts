/* ============================================================================
 * NOC 拓扑中心 —— 来源 / 状态 / 设备类型的展示注册表
 * ============================================================================
 * 把「某个枚举值该用什么颜色、什么图标、哪条 i18n 文案」收在一处，
 * 组件里不再各写一遍 switch。
 *
 * 颜色一律走 `WH` 令牌（CSS 变量），深/浅色主题自动跟随；
 * 唯一例外是流量 8 档色阶 —— 那是 Weathermap 的数据语义色，
 * 由 `../ipranTopo/trafficScale.ts` 提供，本文件只转出。
 * ========================================================================== */
import { WH } from '../../theme'
import { TRAFFIC_BINS, TRAFFIC_NA_COLOR } from '../ipranTopo/trafficScale'
import type { NocDeviceType, NocMedium, NocStatus, NocView, SourceKey, SourceState } from './types'

export const SOURCE_LABEL_KEY: Record<SourceKey, string> = {
  ipran: 'nocTopo.source.ipran',
  mw: 'nocTopo.source.mw',
  olib: 'nocTopo.source.olib',
}

export const SOURCE_DESC_KEY: Record<SourceKey, string> = {
  ipran: 'nocTopo.source.ipranDesc',
  mw: 'nocTopo.source.mwDesc',
  olib: 'nocTopo.source.olibDesc',
}

/** 来源主色：用于图例、来源卡圆点、地理图设备描边 */
export function sourceColor(source: SourceKey): string {
  switch (source) {
    case 'ipran': return WH.blue
    case 'mw': return WH.warn
    default: return WH.teal
  }
}

export const VIEW_LABEL_KEY: Record<NocView, string> = {
  overview: 'nocTopo.view.overview',
  logical: 'nocTopo.view.logical',
  traffic: 'nocTopo.view.traffic',
  geo: 'nocTopo.view.geo',
}

export const VIEW_HINT_KEY: Record<NocView, string> = {
  overview: 'nocTopo.view.overviewHint',
  logical: 'nocTopo.view.logicalHint',
  traffic: 'nocTopo.view.trafficHint',
  geo: 'nocTopo.view.geoHint',
}

export function statusColor(status: NocStatus): string {
  switch (status) {
    case 'ok': return WH.ok
    case 'minor': return WH.warn
    case 'major': return WH.sev.Major
    case 'critical': return WH.sev.Critical
    case 'down': return WH.danger
    case 'planned': return WH.textDim
    case 'static': return WH.textMuted
    default: return WH.textDim
  }
}

export const STATUS_LABEL_KEY: Record<NocStatus, string> = {
  ok: 'nocTopo.status.ok',
  minor: 'nocTopo.status.minor',
  major: 'nocTopo.status.major',
  critical: 'nocTopo.status.critical',
  down: 'nocTopo.status.down',
  unknown: 'nocTopo.status.unknown',
  static: 'nocTopo.status.static',
  planned: 'nocTopo.status.planned',
}

/** 严重度排序：越大越严重（风险榜、问题筛选用） */
export function statusRank(status: NocStatus): number {
  switch (status) {
    case 'down': return 6
    case 'critical': return 5
    case 'major': return 4
    case 'minor': return 3
    case 'unknown': return 2
    case 'ok': return 1
    default: return 0
  }
}

export const DEVICE_LABEL_KEY: Record<NocDeviceType, string> = {
  core: 'nocTopo.device.core',
  aggregation: 'nocTopo.device.aggregation',
  switch: 'nocTopo.device.switch',
  access: 'nocTopo.device.access',
  firewall: 'nocTopo.device.firewall',
  isp: 'nocTopo.device.isp',
  microwave: 'nocTopo.device.microwave',
  site: 'nocTopo.device.site',
}

export const MEDIUM_LABEL_KEY: Record<NocMedium, string> = {
  fiber: 'nocTopo.medium.fiber',
  microwave: 'nocTopo.medium.microwave',
  schematic: 'nocTopo.medium.schematic',
}

export const STATE_LABEL_KEY: Record<SourceState, string> = {
  loading: 'nocTopo.state.loading',
  connected: 'nocTopo.state.connected',
  partial: 'nocTopo.state.partial',
  stale: 'nocTopo.state.stale',
  unavailable: 'nocTopo.state.unavailable',
  static: 'nocTopo.state.static',
}

export function stateColor(state: SourceState): string {
  switch (state) {
    case 'connected': return WH.ok
    case 'partial': return WH.warn
    case 'stale': return WH.sev.Major
    case 'unavailable': return WH.danger
    case 'loading': return WH.textDim
    default: return WH.textMuted
  }
}

/** 8 档流量色阶（与 IPRAN 专页同一套，禁止另造配色） */
export { TRAFFIC_BINS, TRAFFIC_NA_COLOR }

/** 设备图标半径（逻辑画布） */
export const DEVICE_RADIUS: Record<NocDeviceType, number> = {
  core: 9,
  aggregation: 7,
  switch: 6,
  access: 5,
  firewall: 7,
  isp: 8,
  microwave: 6,
  site: 5,
}

/** 链路线宽：按介质区分，流量视图再叠加带宽权重 */
export function mediumWidth(medium: NocMedium): number {
  switch (medium) {
    case 'microwave': return 1.6
    case 'schematic': return 1.1
    default: return 1.3
  }
}
