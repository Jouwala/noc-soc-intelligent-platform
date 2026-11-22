/* ============================================================================
 * NOC 拓扑中心 —— 逻辑画布布局
 * ============================================================================
 * 为什么不用 IPRAN 专页那套力导向：
 *   中心要把三个来源放在同一张画布上，加起来的连通分量有几百个
 *   （实测 MW 649 节点 / 373 链路 = 276 个分量，其中 223 个是「一条链路两端」
 *   的点对；IPRAN 100 节点 = 4 个分量）。O(n²) 斥力既慢又会让每次刷新的
 *   位置抖动，值班人员刚盯住的点会跑掉。
 *
 * 这里用**确定性的分层环布局**：
 *   1. 拆连通分量；
 *   2. 每个分量从度数最大的节点 BFS，按层画同心环（第 1 层从右侧开始，
 *      所以「两个点的链路」是横线，不是竖线）；
 *   3. 每个分量按**自身自然尺寸**分配格子（不放大、只在过大时缩小），
 *      再货架式打包。这样几百个点对会排成紧凑的网格，而不是被拉成
 *      一排排贯穿画布的长线。
 * 同一份输入永远得到同一张图；复杂度 O(n+e)。
 * ========================================================================== */
import type { NocEdge, NocNode } from '../types'

export interface Pt { x: number; y: number }

export interface GraphLayoutResult {
  pos: Record<string, Pt>
  width: number
  height: number
  /** 连通分量数（MW 多、IPRAN 少，All Networks 会明显更多） */
  components: number
}

const PAD = 16
const RING_GAP = 44
/** 单个分量允许的最大边长（超过就等比缩小，避免一个巨型分量独占画布） */
const MAX_EXTENT = 620
/** 单个分量的最小格子尺寸（孤点也要可点） */
const MIN_CELL_W = 52
const MIN_CELL_H = 36

interface Block {
  keys: string[]
  w: number
  h: number
  local: Record<string, Pt>
}

function buildAdjacency(nodes: NocNode[], edges: NocEdge[]) {
  const keySet = new Set(nodes.map((n) => n.key))
  const adj = new Map<string, Set<string>>()
  for (const n of nodes) adj.set(n.key, new Set())
  for (const e of edges) {
    if (!keySet.has(e.sourceNode) || !keySet.has(e.targetNode)) continue
    adj.get(e.sourceNode)!.add(e.targetNode)
    adj.get(e.targetNode)!.add(e.sourceNode)
  }
  return adj
}

/** 一个连通分量 → 以 (0,0) 为中心的自然坐标 + 宽高 */
function layoutComponent(
  keys: string[],
  adj: Map<string, Set<string>>,
): { local: Record<string, Pt>; w: number; h: number } {
  const keySet = new Set(keys)
  let root = keys[0]
  let best = -1
  for (const k of keys) {
    const d = adj.get(k)?.size ?? 0
    if (d > best || (d === best && k < root)) { best = d; root = k }
  }

  const level = new Map<string, number>([[root, 0]])
  const order: string[] = [root]
  const queue: string[] = [root]
  for (let qi = 0; qi < queue.length; qi++) {
    const cur = queue[qi]
    for (const nb of [...(adj.get(cur) ?? [])].sort()) {
      if (!keySet.has(nb) || level.has(nb)) continue
      level.set(nb, (level.get(cur) ?? 0) + 1)
      order.push(nb)
      queue.push(nb)
    }
  }
  for (const k of keys) if (!level.has(k)) level.set(k, 0)

  const byLevel = new Map<number, string[]>()
  for (const k of order) {
    const lv = level.get(k) ?? 0
    if (!byLevel.has(lv)) byLevel.set(lv, [])
    byLevel.get(lv)!.push(k)
  }

  const raw: Record<string, Pt> = { [root]: { x: 0, y: 0 } }
  const maxLevel = Math.max(0, ...byLevel.keys())
  for (let lv = 1; lv <= maxLevel; lv++) {
    const list = byLevel.get(lv) ?? []
    const r = lv * RING_GAP
    // 第 1 层从 0 rad（右侧）开始：两点链路画成横线；奇数层错开半格避免射线重合
    const offset = (lv % 2) * (Math.PI / Math.max(list.length, 1))
    list.forEach((k, i) => {
      const a = (2 * Math.PI * i) / Math.max(list.length, 1) + offset
      raw[k] = { x: Math.cos(a) * r, y: Math.sin(a) * r }
    })
  }

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const p of Object.values(raw)) {
    minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x)
    minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y)
  }
  const cx = (minX + maxX) / 2
  const cy = (minY + maxY) / 2
  const sx = maxX - minX
  const sy = maxY - minY
  const natural = Math.max(sx, sy)
  // 只在过大时缩小；**不放大**（放大一对两点链路会变成贯穿整格的长线）
  const scale = natural > MAX_EXTENT ? MAX_EXTENT / natural : 1

  const local: Record<string, Pt> = {}
  for (const [k, p] of Object.entries(raw)) {
    local[k] = { x: (p.x - cx) * scale, y: (p.y - cy) * scale }
  }
  return {
    local,
    w: Math.max(sx * scale, 20),
    h: Math.max(sy * scale, 20),
  }
}

export function layoutGraph(nodes: NocNode[], edges: NocEdge[]): GraphLayoutResult {
  if (!nodes.length) return { pos: {}, width: 960, height: 600, components: 0 }

  const adj = buildAdjacency(nodes, edges)
  const seen = new Set<string>()
  const groups: string[][] = []
  for (const n of [...nodes].sort((a, b) => a.key.localeCompare(b.key))) {
    if (seen.has(n.key)) continue
    const group: string[] = []
    const stack = [n.key]
    seen.add(n.key)
    while (stack.length) {
      const cur = stack.pop()!
      group.push(cur)
      for (const nb of adj.get(cur) ?? []) {
        if (!seen.has(nb)) { seen.add(nb); stack.push(nb) }
      }
    }
    groups.push(group)
  }

  const blocks: Block[] = groups.map((keys) => {
    const { local, w, h } = layoutComponent(keys, adj)
    return {
      keys,
      local,
      w: Math.max(MIN_CELL_W, w + 2 * PAD),
      h: Math.max(MIN_CELL_H, h + 2 * PAD),
    }
  })
  // 大分量先排，视觉重心落在左上（与阅读方向一致）
  blocks.sort((a, b) => b.keys.length - a.keys.length || a.keys[0].localeCompare(b.keys[0]))

  const totalArea = blocks.reduce((s, b) => s + b.w * b.h, 0)
  const targetRow = Math.max(1400, Math.sqrt(totalArea) * 1.35)

  const pos: Record<string, Pt> = {}
  let x = 0
  let y = 0
  let rowH = 0
  let maxW = 0
  for (const b of blocks) {
    if (x > 0 && x + b.w > targetRow) {
      x = 0
      y += rowH + 14
      rowH = 0
    }
    const cx = x + b.w / 2
    const cy = y + b.h / 2
    for (const k of b.keys) {
      const p = b.local[k] ?? { x: 0, y: 0 }
      pos[k] = { x: cx + p.x, y: cy + p.y }
    }
    x += b.w + 14
    rowH = Math.max(rowH, b.h)
    maxW = Math.max(maxW, x)
  }

  const height = y + rowH
  return {
    pos,
    width: Math.max(maxW, 640),
    height: Math.max(height, 480),
    components: blocks.length,
  }
}

/** 点的包围盒（画布 fit 用；也便于测试） */
export function boundsOf(pos: Record<string, Pt>): { minX: number; minY: number; maxX: number; maxY: number } {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const p of Object.values(pos)) {
    minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x)
    minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y)
  }
  if (!Number.isFinite(minX)) return { minX: 0, minY: 0, maxX: 0, maxY: 0 }
  return { minX, minY, maxX, maxY }
}
