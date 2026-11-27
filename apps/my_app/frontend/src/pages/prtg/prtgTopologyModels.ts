/* ============================================================================
 * PRTG 链路健康页 · 拓扑槽位（P2）
 * ----------------------------------------------------------------------------
 * 数据：GET /api/network-topology/traffic?period=live|1h|24h|7d
 * 本文件只放类型 + 纯函数（布局分层、并行边合并、周期映射、着色口径）。
 * 边只来自接口 links[]，绝不猜测设备间连线。
 * ========================================================================== */

import type { PrtgLink, PrtgWindow } from './prtgModels'
import { shortDeviceName, statusSortRank } from './prtgModels'

export type TopoPeriod = 'live' | '1h' | '24h' | '7d'

export type TopoNodeType = 'device' | 'peer' | 'customer' | 'unknown'

export interface TopoDataQuality {
  capacity_known?: boolean | null
  traffic_available?: boolean | null
  sample_age_seconds?: number | null
  mapping_confidence?: string | null
}

export interface TopoNode {
  id: number
  key?: string | null
  name: string
  type: TopoNodeType | string
  x: number
  y: number
  pinned?: boolean | null
  status?: string | null
}

export interface TopoLink {
  id: number
  key?: string | null
  sensor_id?: number | null
  sensor_objid?: number | null
  source_node: number
  target_node: number
  source?: string | null
  target?: string | null
  status?: string | null
  in_mbps?: number | null
  out_mbps?: number | null
  capacity_ab_mbps?: number | null
  capacity_ba_mbps?: number | null
  utilization_pct?: number | null
  mapping_confidence?: string | null
  link_type?: string | null
  bend_offset?: number | null
  data_quality?: TopoDataQuality | null
}

export interface TopoTrafficPayload {
  generated_at?: string | null
  period?: string | null
  data_age_seconds?: number | null
  nodes?: TopoNode[]
  links?: TopoLink[]
  kpi?: Record<string, number | null> | null
}

/** 画布约定（与后端 / 门户一致） */
export const TOPO_CANVAS = { w: 1000, h: 620 } as const

/** 页面时间窗 → 拓扑 period；30d 后端不支持，退回 7d */
export function mapWindowToTopoPeriod(windowKey: PrtgWindow): {
  period: TopoPeriod
  fallbackFrom30d: boolean
} {
  switch (windowKey) {
    case '5m': return { period: 'live', fallbackFrom30d: false }
    case '24h': return { period: '24h', fallbackFrom30d: false }
    case '7d': return { period: '7d', fallbackFrom30d: false }
    case '30d': return { period: '7d', fallbackFrom30d: true }
    default: return { period: '24h', fallbackFrom30d: false }
  }
}

export function sensorIdOf(link: TopoLink): number | null {
  const id = link.sensor_id ?? link.sensor_objid
  return id == null || !Number.isFinite(id) ? null : Number(id)
}

export function isSecurityName(name?: string | null): boolean {
  const s = (name || '').toUpperCase()
  return /\bFG\b/.test(s) || /\bFW\b/.test(s) || s.includes('-FG-') || s.includes('-FW-')
    || s.includes('FG1500') || s.includes('FIREWALL')
}

/** 布局列：peer → 安全设备 → 核心 device → customer/unknown */
export type LayoutColumn = 'peer' | 'security' | 'device' | 'other'

export function layoutColumnOf(node: TopoNode): LayoutColumn {
  const t = (node.type || '').toLowerCase()
  if (t === 'peer') return 'peer'
  if (isSecurityName(node.name)) return 'security'
  if (t === 'device') return 'device'
  return 'other'
}

const COLUMN_X: Record<LayoutColumn, number> = {
  peer: 120,
  security: 340,
  device: 580,
  other: 860,
}

/** 核心设备簇：按短名匹配（不含猜测连线，只影响纵向分区） */
export function deviceClusterKey(name?: string | null): string {
  const short = shortDeviceName(name)
  const u = short.toUpperCase()
  if (u.includes('OLIB-NX-3172') || u.includes('OLIB-NX-3172-WAN-2')) return 'OLIB-NX-3172-WAN-2'
  if (u.includes('PTP_SW') || u === 'PTP_SW') return 'PTP_SW'
  if (u.includes('AML') && u.includes('TOKADEH')) return 'AML TOKADEH1'
  return short === '—' ? '__other__' : short
}

function pairKey(a: number, b: number): string {
  return a < b ? `${a}:${b}` : `${b}:${a}`
}

export interface MergedTopoEdge {
  key: string
  source_node: number
  target_node: number
  links: TopoLink[]
  /** 最差状态的那条（Down > Unusual > Unknown > Up） */
  worst: TopoLink
  count: number
}

/** 同一对节点的多条边合并；只消费接口 links，不补边 */
export function mergeParallelEdges(links: TopoLink[]): MergedTopoEdge[] {
  const map = new Map<string, TopoLink[]>()
  for (const link of links) {
    if (link.source_node == null || link.target_node == null) continue
    const k = pairKey(link.source_node, link.target_node)
    const arr = map.get(k)
    if (arr) arr.push(link)
    else map.set(k, [link])
  }
  const out: MergedTopoEdge[] = []
  for (const [key, group] of map) {
    const sorted = [...group].sort(
      (a, b) => statusSortRank(a.status) - statusSortRank(b.status),
    )
    const worst = sorted[0]
    out.push({
      key,
      source_node: worst.source_node,
      target_node: worst.target_node,
      links: sorted,
      worst,
      count: sorted.length,
    })
  }
  return out
}

export interface LaidOutNode extends TopoNode {
  lx: number
  ly: number
}

/**
 * 布局：pinned 用接口坐标；未 pinned 按列 + 设备簇纵向分区。
 * 只移动节点，不新增边。
 */
export function layoutTopoNodes(
  nodes: TopoNode[],
  links: TopoLink[],
): LaidOutNode[] {
  const byId = new Map(nodes.map(n => [n.id, n]))
  const clusterOf = new Map<number, string>()

  // 先给 device 节点定簇；再把与之相连的节点归入同一簇
  for (const n of nodes) {
    if ((n.type || '').toLowerCase() === 'device') {
      clusterOf.set(n.id, deviceClusterKey(n.name))
    }
  }
  for (const link of links) {
    const src = byId.get(link.source_node)
    const dst = byId.get(link.target_node)
    if (!src || !dst) continue
    const srcDev = (src.type || '').toLowerCase() === 'device'
    const dstDev = (dst.type || '').toLowerCase() === 'device'
    if (srcDev && !clusterOf.has(dst.id)) clusterOf.set(dst.id, deviceClusterKey(src.name))
    if (dstDev && !clusterOf.has(src.id)) clusterOf.set(src.id, deviceClusterKey(dst.name))
  }
  for (const n of nodes) {
    if (!clusterOf.has(n.id)) clusterOf.set(n.id, deviceClusterKey(n.name))
  }

  const clusterOrder = ['OLIB-NX-3172-WAN-2', 'PTP_SW', 'AML TOKADEH1']
  const present = new Set(clusterOf.values())
  const extraClusters = [...present].filter(c => !clusterOrder.includes(c)).sort()
  const orderedBands: string[] = [
    ...clusterOrder.filter(c => present.has(c)),
    ...extraClusters,
  ]
  if (orderedBands.length === 0) orderedBands.push('__other__')

  const bandH = TOPO_CANVAS.h / orderedBands.length
  const buckets = new Map<string, TopoNode[]>()
  for (const n of nodes) {
    if (n.pinned) continue
    const col = layoutColumnOf(n)
    const band = clusterOf.get(n.id) || '__other__'
    const k = `${band}||${col}`
    const arr = buckets.get(k)
    if (arr) arr.push(n)
    else buckets.set(k, [n])
  }
  for (const arr of buckets.values()) {
    arr.sort((a, b) => shortDeviceName(a.name).localeCompare(shortDeviceName(b.name)))
  }

  const pos = new Map<number, { x: number; y: number }>()
  for (const n of nodes) {
    if (n.pinned) {
      pos.set(n.id, {
        x: clamp(n.x, 40, TOPO_CANVAS.w - 40),
        y: clamp(n.y, 40, TOPO_CANVAS.h - 40),
      })
    }
  }

  for (let bi = 0; bi < orderedBands.length; bi++) {
    const band = orderedBands[bi]
    const y0 = bi * bandH + 36
    const y1 = (bi + 1) * bandH - 36
    for (const col of ['peer', 'security', 'device', 'other'] as LayoutColumn[]) {
      const list = buckets.get(`${band}||${col}`) || []
      if (!list.length) continue
      const x = COLUMN_X[col]
      const step = list.length === 1 ? 0 : (y1 - y0) / (list.length - 1)
      list.forEach((n, i) => {
        if (pos.has(n.id)) return
        const y = list.length === 1 ? (y0 + y1) / 2 : y0 + step * i
        pos.set(n.id, { x, y: clamp(y, 40, TOPO_CANVAS.h - 40) })
      })
    }
  }

  // 兜底：未落入任何桶的节点
  let orphanY = 80
  return nodes.map(n => {
    const p = pos.get(n.id) || { x: COLUMN_X[layoutColumnOf(n)], y: (orphanY += 40) }
    return { ...n, lx: p.x, ly: p.y }
  })
}

function clamp(v: number, lo: number, hi: number): number {
  if (!Number.isFinite(v)) return lo
  return Math.min(hi, Math.max(lo, v))
}

/** 流量绝对值 → 线宽档（无容量时用） */
export function trafficWidth(inMbps?: number | null, outMbps?: number | null): number {
  const total = (inMbps ?? 0) + (outMbps ?? 0)
  if (total >= 500) return 5
  if (total >= 200) return 4
  if (total >= 50) return 3
  return 2
}

/** §3 利用率分档色（有容量时用）；返回 null 表示无利用率 */
export const UTIL_BANDS: { limit: number; color: string }[] = [
  { limit: 1, color: '#7c3aed' },
  { limit: 10, color: '#8b5cf6' },
  { limit: 25, color: '#6366f1' },
  { limit: 40, color: '#3b82f6' },
  { limit: 55, color: '#0ea5e9' },
  { limit: 70, color: '#f59e0b' },
  { limit: 85, color: '#f97316' },
  { limit: 100, color: '#ef4444' },
]
export const OVER_CAPACITY_COLOR = '#991b1b'

export function utilBandColor(util: number): string {
  if (util > 100) return OVER_CAPACITY_COLOR
  return (UTIL_BANDS.find(b => util <= b.limit) ?? UTIL_BANDS[UTIL_BANDS.length - 1]).color
}

export function statusStroke(status?: string | null, isDark = false): string {
  switch ((status || '').trim().toLowerCase()) {
    case 'up': return isDark ? '#0bc78c' : '#15803d'
    case 'unusual': return isDark ? '#ff922b' : '#ea580c'
    case 'down': return isDark ? '#ff5c6c' : '#d92d20'
    default: return isDark ? '#6f8cb4' : '#94a3b8'
  }
}

export function isMappingVerified(link: TopoLink): boolean {
  const c = (link.data_quality?.mapping_confidence || link.mapping_confidence || '').toLowerCase()
  return c === 'verified'
}

export function hasCapacity(link: TopoLink): boolean {
  if (link.data_quality?.capacity_known === true) return true
  if (link.data_quality?.capacity_known === false) return false
  return (link.capacity_ab_mbps != null && link.capacity_ab_mbps > 0)
    || (link.capacity_ba_mbps != null && link.capacity_ba_mbps > 0)
}

export function paintEdge(link: TopoLink, isDark: boolean): {
  color: string
  width: number
  dashed: boolean
  overCapacity: boolean
} {
  const dashed = !isMappingVerified(link)
  if (hasCapacity(link) && link.utilization_pct != null && Number.isFinite(link.utilization_pct)) {
    const util = link.utilization_pct
    return {
      color: utilBandColor(util),
      width: 2 + (Math.min(Math.max(util, 0), 100) / 100) * 2.5,
      dashed,
      overCapacity: util > 100,
    }
  }
  return {
    color: statusStroke(link.status, isDark),
    width: trafficWidth(link.in_mbps, link.out_mbps),
    dashed,
    overCapacity: false,
  }
}

/** 用页面 links 补齐状态原因（按 sensor_id 关联） */
export function findPageLink(links: PrtgLink[], sensorId: number | null): PrtgLink | undefined {
  if (sensorId == null) return undefined
  return links.find(l => l.sensor_id === sensorId)
}
