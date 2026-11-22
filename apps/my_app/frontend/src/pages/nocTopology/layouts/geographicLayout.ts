/* ============================================================================
 * NOC 拓扑中心 —— 地理投影（保真实比例）
 * ============================================================================
 * 需求里有一条硬约束：「利比里亚地图必须保持真实地理比例，不能为了填满宽屏
 * 而拉伸」。所以这里**不用** `x = (lon-min)/(max-min)*width` 那种两轴独立归一，
 * 而是：
 *   1. 经纬度先转 Web Mercator（同一单位：米），高纬度地区的经线收缩被正确表达；
 *   2. 用**同一个 scale** 同时作用于 x/y；
 *   3. 画布内居中，剩余空间留白（letterbox），不放大到填满。
 *
 * 这套投影只用于 **SVG 逻辑画布里的地理视图**；Leaflet 地图由 Leaflet 自己
 * 做投影，不存在拉伸问题。
 * ========================================================================== */
import type { NocNode } from '../types'

export interface Pt { x: number; y: number }

export interface GeoProjection {
  pos: Record<string, Pt>
  width: number
  height: number
  /** 米 → 画布像素的缩放比（测试用：验证两轴一致） */
  scale: number
  /** 没有任何节点有坐标时为 true */
  empty: boolean
  /** 没有坐标、被跳过的节点数 */
  skipped: number
}

const MERCATOR_R = 6378137

function mercator(lat: number, lon: number): Pt {
  const x = (lon * Math.PI / 180) * MERCATOR_R
  const y = Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI / 180) / 2)) * MERCATOR_R
  return { x, y }
}

export function projectGeographic(
  nodes: NocNode[],
  width = 1000,
  height = 640,
  padding = 28,
): GeoProjection {
  const pts: { key: string; p: Pt }[] = []
  let skipped = 0
  for (const n of nodes) {
    if (typeof n.latitude !== 'number' || !Number.isFinite(n.latitude)
      || typeof n.longitude !== 'number' || !Number.isFinite(n.longitude)) {
      skipped++
      continue
    }
    pts.push({ key: n.key, p: mercator(n.latitude, n.longitude) })
  }

  if (!pts.length) {
    return { pos: {}, width, height, scale: 0, empty: true, skipped }
  }

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const { p } of pts) {
    minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x)
    minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y)
  }

  const spanX = Math.max(maxX - minX, 1)
  const spanY = Math.max(maxY - minY, 1)
  const innerW = Math.max(1, width - 2 * padding)
  const innerH = Math.max(1, height - 2 * padding)
  // 同一个 scale 作用于两轴 → 形状不变；取较小者保证完整落在画布内
  const scale = Math.min(innerW / spanX, innerH / spanY)

  const drawnW = spanX * scale
  const drawnH = spanY * scale
  const offsetX = padding + (innerW - drawnW) / 2
  const offsetY = padding + (innerH - drawnH) / 2

  const pos: Record<string, Pt> = {}
  for (const { key, p } of pts) {
    pos[key] = {
      x: offsetX + (p.x - minX) * scale,
      // 屏幕 y 向下，Mercator y 向上 → 翻转
      y: offsetY + (maxY - p.y) * scale,
    }
  }

  return { pos, width, height, scale, empty: false, skipped }
}
