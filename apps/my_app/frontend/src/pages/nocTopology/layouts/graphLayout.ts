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
 *
 * IPRAN 例外 —— **分层布局**（ISP → 核心 → 汇聚 → 接入，自上而下）：
 *   IPRAN 设备自带 tier，同心环会把核心/汇聚/接入混在一起、连线横穿整团。
 *   当一个分量全部是 IPRAN 设备、≥ HIER_MIN_NODES 个、且至少跨两层时：
 *   1. 每层一行，层内顺序用重心法（barycenter）上下来回扫 SWEEPS 次，减少交叉；
 *   2. 一层太宽（> HIER_MAX_ROW_W）就拆成若干子行**交错排列**（第 i 个点落在
 *      第 i % sub 个子行），保持左右顺序不变，上下层的对位关系不被打乱；
 *   3. 不做 MAX_EXTENT 缩小 —— 节点半径是固定的，缩小只会把同层节点挤成一团。
 *   其他来源（MW 全是微波、OLIB 是示意站点）不分层，仍走同心环。
 *
 * 流量视图 —— **按负载排序**（opts.sortByLoad）：
 *   MW 有两百多个「一条链路两端」的点对，原来按大小 + 编号排，满载链路被淹没在中间。
 *   流量视图改为按分量内最高利用率**所在色阶档位**从高到低排，满载的落在左上角。
 *   用档位而不是原始百分比排序，是为了让自动刷新时只有「跨档」的链路才换位置，
 *   避免数值小幅波动就把整片网格打乱（值班人员刚盯住的点会跑掉）。
 *
 * 同一份输入永远得到同一张图；复杂度 O(n+e)。
 * ========================================================================== */
import { TRAFFIC_BINS } from '../../ipranTopo/trafficScale'
import type { NocDeviceType, NocEdge, NocNode } from '../types'

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

/* ---------------- 分层布局参数（仅 IPRAN） ---------------- */
/** 层号：越小越靠上。没列出的设备类型 → 该分量不分层 */
const RANK: Partial<Record<NocDeviceType, number>> = {
  isp: 0,
  core: 1,
  firewall: 1,
  aggregation: 2,
  switch: 2,
  access: 3,
}
/** 少于这么多点的小分量不分层（两三个点的分层图没有意义） */
const HIER_MIN_NODES = 6
/** 同一子行里相邻两个节点的水平间距 */
const NODE_GAP = 42
/** 同一层拆成多个子行时，子行之间的竖向间距 */
const SUB_GAP = 30
/** 层与层之间的竖向间距（含最后一个子行到下一层） */
const RANK_GAP = 96
/** 一层的最大宽度：超出就拆子行 */
const HIER_MAX_ROW_W = 1000
const HIER_MAX_SUBROWS = 6
/** 重心法上下扫的次数（偶数次 = 最后一次是自下而上） */
const SWEEPS = 6

interface Block {
  keys: string[]
  w: number
  h: number
  local: Record<string, Pt>
  /** 分量内最高利用率所在的色阶档位；无流量 = -1（仅 sortByLoad 时计算） */
  load: number
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

/** 这个分量能不能分层：全是 IPRAN、点数够、至少跨两层。能 → 返回每个点的层号 */
function hierarchyRanks(keys: string[], byKey: Map<string, NocNode>): Map<string, number> | null {
  if (keys.length < HIER_MIN_NODES) return null
  const ranks = new Map<string, number>()
  for (const k of keys) {
    const n = byKey.get(k)
    if (!n || n.source !== 'ipran') return null
    const r = RANK[n.deviceType]
    if (r == null) return null
    ranks.set(k, r)
  }
  if (new Set(ranks.values()).size < 2) return null
  return ranks
}

/** 分层布局：一个分量 → 以 (0,0) 为中心的坐标 + 宽高（与 layoutComponent 同一接口） */
function layoutHierarchy(
  keys: string[],
  adj: Map<string, Set<string>>,
  ranks: Map<string, number>,
): { local: Record<string, Pt>; w: number; h: number } {
  // 1. 按层分行；层号压紧（没有 ISP 时不会在顶上留一条空行）
  const levels = [...new Set(ranks.values())].sort((a, b) => a - b)
  const rows: string[][] = levels.map((lv) => keys.filter((k) => ranks.get(k) === lv).sort())
  const rowOf = new Map<string, number>()
  rows.forEach((row, ri) => row.forEach((k) => rowOf.set(k, ri)))

  // 2. 重心法排层内顺序。位置用 0..1 归一化，不同宽度的层之间才能比较
  const at = new Map<string, number>()
  const normalize = (row: string[]) => {
    row.forEach((k, i) => at.set(k, row.length === 1 ? 0.5 : i / (row.length - 1)))
  }
  rows.forEach(normalize)

  const reorder = (ri: number, side: (r: number) => boolean) => {
    const row = rows[ri]
    const bary = new Map<string, number>()
    for (const k of row) {
      // 优先看扫描方向那一侧的邻居；没有就看任意别的层；再没有就看同层邻居（接入环）；都没有保持原位
      const tiers: Array<(r: number) => boolean> = [side, (r) => r !== ri, (r) => r === ri]
      let value = at.get(k) ?? 0.5
      for (const accept of tiers) {
        let sum = 0
        let cnt = 0
        for (const nb of adj.get(k) ?? []) {
          const r = rowOf.get(nb)
          if (r == null || nb === k || !accept(r)) continue
          sum += at.get(nb) ?? 0.5
          cnt++
        }
        if (cnt > 0) { value = sum / cnt; break }
      }
      bary.set(k, value)
    }
    row.sort((a, b) => (bary.get(a)! - bary.get(b)!) || a.localeCompare(b))
    normalize(row)
  }

  for (let it = 0; it < SWEEPS; it++) {
    if (it % 2 === 0) {
      for (let ri = 1; ri < rows.length; ri++) reorder(ri, (r) => r < ri)
    } else {
      for (let ri = rows.length - 2; ri >= 0; ri--) reorder(ri, (r) => r > ri)
    }
  }

  // 3. 摆放：每层水平居中；太宽就拆子行交错排列，左右顺序不变
  const raw: Record<string, Pt> = {}
  let y = 0
  for (const row of rows) {
    const n = row.length
    const sub = Math.min(
      HIER_MAX_SUBROWS,
      Math.max(1, Math.ceil(((n - 1) * NODE_GAP) / HIER_MAX_ROW_W)),
    )
    const dx = NODE_GAP / sub
    const w = (n - 1) * dx
    row.forEach((k, i) => {
      raw[k] = { x: i * dx - w / 2, y: y + (i % sub) * SUB_GAP }
    })
    y += (Math.min(sub, n) - 1) * SUB_GAP + RANK_GAP
  }

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const p of Object.values(raw)) {
    minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x)
    minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y)
  }
  const cx = (minX + maxX) / 2
  const cy = (minY + maxY) / 2
  const local: Record<string, Pt> = {}
  for (const [k, p] of Object.entries(raw)) local[k] = { x: p.x - cx, y: p.y - cy }
  return {
    local,
    w: Math.max(maxX - minX, 20),
    h: Math.max(maxY - minY, 20),
  }
}

/** 链路两个方向里较大的利用率；没有流量 = null（单侧 null 不当 0） */
function peakUtil(e: NocEdge): number | null {
  if (!e.trafficAvailable) return null
  const vals = [e.txUtilization, e.rxUtilization]
    .filter((v): v is number => v != null && Number.isFinite(v))
  return vals.length ? Math.max(...vals) : null
}

/** 利用率 → 8 档色阶的下标（与画线颜色同一套分档） */
export function loadBucket(util: number): number {
  const i = TRAFFIC_BINS.findIndex((b) => util < b.max)
  return i < 0 ? TRAFFIC_BINS.length - 1 : i
}

export interface LayoutOptions {
  /** 流量视图：分量按最高负载档位从高到低排列 */
  sortByLoad?: boolean
}

export function layoutGraph(
  nodes: NocNode[],
  edges: NocEdge[],
  opts: LayoutOptions = {},
): GraphLayoutResult {
  if (!nodes.length) return { pos: {}, width: 960, height: 600, components: 0 }

  const adj = buildAdjacency(nodes, edges)
  const byKey = new Map(nodes.map((n) => [n.key, n]))
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
    const ranks = hierarchyRanks(keys, byKey)
    const { local, w, h } = ranks
      ? layoutHierarchy(keys, adj, ranks)
      : layoutComponent(keys, adj)
    return {
      keys,
      local,
      w: Math.max(MIN_CELL_W, w + 2 * PAD),
      h: Math.max(MIN_CELL_H, h + 2 * PAD),
      load: -1,
    }
  })

  if (opts.sortByLoad) {
    const blockOf = new Map<string, number>()
    blocks.forEach((b, i) => b.keys.forEach((k) => blockOf.set(k, i)))
    for (const e of edges) {
      const u = peakUtil(e)
      if (u == null) continue
      const i = blockOf.get(e.sourceNode)
      if (i == null || blockOf.get(e.targetNode) !== i) continue
      blocks[i].load = Math.max(blocks[i].load, loadBucket(u))
    }
  }

  // 流量视图：负载档位高的先排；其余（或同档）大分量先排，视觉重心落在左上（与阅读方向一致）
  blocks.sort((a, b) => (
    (b.load - a.load)
    || (b.keys.length - a.keys.length)
    || a.keys[0].localeCompare(b.keys[0])
  ))

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
