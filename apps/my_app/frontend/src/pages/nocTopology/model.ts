/* ============================================================================
 * NOC 拓扑中心 —— 纯函数模型层
 * ============================================================================
 * 这里不碰 React、不碰 DOM，只做「筛选 / 统计 / 邻接 / 风险排序」，
 * 因此可以直接被 vitest 覆盖（见 `__tests__/topologyModel.test.ts`）。
 *
 * 关键约定：
 *   · 度数在当前筛选结果上重算，不用上游 `deg`（上游 deg 是折叠前口径）；
 *   · 「有源链路」不计 OLIB 静态/规划链路，否则总览会出现大量「活动链路」；
 *   · 利用率取 max(tx, rx)，但任一侧为 null 时不参与取大（不把 null 当 0）。
 * ========================================================================== */
import type { NocEdge, NocFilter, NocModel, NocNode, NocStatus, SourceKey } from './types'
import { statusRank } from './registry'

export interface NocSummary {
  nodes: number
  links: number
  /** 有真实运行状态的活动链路（不含 static / planned / unknown / down） */
  activeLinks: number
  /** critical + down */
  criticalLinks: number
  downLinks: number
  /** 有流量数据的链路数 */
  trafficLinks: number
  /** 支持流量的链路里，有数据的占比 0..1；没有可统计链路时为 null */
  trafficCoverage: number | null
  /** 有流量能力、但本次没取流量数据的来源（如非流量视图下的 IPRAN） */
  trafficPending: SourceKey[]
  bySource: Record<SourceKey, { nodes: number; links: number }>
}

export function sourceOf(key: string): SourceKey | null {
  const i = key.indexOf(':')
  if (i <= 0) return null
  const s = key.slice(0, i)
  return s === 'ipran' || s === 'mw' || s === 'olib' ? s : null
}

/** 某条链路的最大方向利用率；两侧都没数据时为 null（不是 0） */
export function edgeUtilization(edge: NocEdge): number | null {
  const { txUtilization: tx, rxUtilization: rx } = edge
  const vals = [tx, rx].filter((v): v is number => v != null && Number.isFinite(v))
  if (!vals.length) return null
  return Math.max(...vals)
}

/** 链路是否「有真实运行状态」——静态资产与规划链路不算 */
export function isActiveEdge(edge: NocEdge): boolean {
  return edge.status !== 'static' && edge.status !== 'planned'
    && edge.status !== 'unknown' && edge.status !== 'down'
}

export function countDegree(edges: NocEdge[]): Map<string, number> {
  const deg = new Map<string, number>()
  for (const e of edges) {
    deg.set(e.sourceNode, (deg.get(e.sourceNode) ?? 0) + 1)
    deg.set(e.targetNode, (deg.get(e.targetNode) ?? 0) + 1)
  }
  return deg
}

/** 用当前边集重算节点度数，返回新数组（不改入参） */
export function withDegree(nodes: NocNode[], edges: NocEdge[]): NocNode[] {
  const deg = countDegree(edges)
  return nodes.map((n) => ({ ...n, degree: deg.get(n.key) ?? 0 }))
}

function matchesQuery(node: NocNode, q: string): boolean {
  if (!q) return true
  const needle = q.trim().toLowerCase()
  if (!needle) return true
  return node.name.toLowerCase().includes(needle)
    || (node.ip ?? '').toLowerCase().includes(needle)
    || (node.siteName ?? '').toLowerCase().includes(needle)
    || node.nativeId.toLowerCase().includes(needle)
}

/**
 * 筛选。
 * 搜索时保留命中节点的**一跳邻居**与它们之间的边 —— 只留命中点会让画布变成
 * 一堆互不相连的孤点，看不出「它在哪条链路上」。
 */
export function filterModel(model: NocModel, filter: NocFilter): NocModel {
  let nodes = model.nodes
  let edges = model.edges

  if (filter.source !== 'all') {
    nodes = nodes.filter((n) => n.source === filter.source)
    edges = edges.filter((e) => e.source === filter.source)
  }

  const q = filter.query.trim()
  if (q) {
    const hit = new Set(nodes.filter((n) => matchesQuery(n, q)).map((n) => n.key))
    const keep = new Set(hit)
    for (const e of edges) {
      if (hit.has(e.sourceNode)) keep.add(e.targetNode)
      if (hit.has(e.targetNode)) keep.add(e.sourceNode)
    }
    nodes = nodes.filter((n) => keep.has(n.key))
    edges = edges.filter((e) => keep.has(e.sourceNode) && keep.has(e.targetNode))
  }

  if (filter.trafficOnly) {
    edges = edges.filter((e) => e.trafficAvailable)
    const keep = new Set<string>()
    for (const e of edges) { keep.add(e.sourceNode); keep.add(e.targetNode) }
    nodes = nodes.filter((n) => keep.has(n.key))
  }

  if (filter.problemsOnly) {
    edges = edges.filter((e) => statusRank(e.status) >= statusRank('minor'))
    const keep = new Set<string>()
    for (const e of edges) { keep.add(e.sourceNode); keep.add(e.targetNode) }
    nodes = nodes.filter((n) => keep.has(n.key))
  }

  return { nodes: withDegree(nodes, edges), edges, sources: model.sources }
}

export function summarize(model: NocModel): NocSummary {
  const bySource: NocSummary['bySource'] = {
    ipran: { nodes: 0, links: 0 },
    mw: { nodes: 0, links: 0 },
    olib: { nodes: 0, links: 0 },
  }
  for (const n of model.nodes) bySource[n.source].nodes++
  for (const e of model.edges) bySource[e.source].links++

  let activeLinks = 0
  let criticalLinks = 0
  let downLinks = 0
  let trafficLinks = 0
  let trafficCapable = 0

  /* 只有「本次真的取了流量」的来源才进分母：
     IPRAN 不带 traffic=1 时 tx/rx 是 undefined，那是**没取**，不是**没覆盖**；
     OLIB 根本没有流量能力，永远不进分母。 */
  const loaded = new Set(
    model.sources.filter((s) => s.trafficLoaded).map((s) => s.source),
  )
  const trafficPending: SourceKey[] = (['ipran', 'mw'] as SourceKey[])
    .filter((s) => model.sources.some((m) => m.source === s) && !loaded.has(s))

  for (const e of model.edges) {
    if (isActiveEdge(e)) activeLinks++
    if (e.status === 'critical') criticalLinks++
    else if (e.status === 'down') { criticalLinks++; downLinks++ }
    if (e.trafficAvailable && loaded.has(e.source)) trafficLinks++
    if (loaded.has(e.source)) trafficCapable++
  }

  return {
    nodes: model.nodes.length,
    links: model.edges.length,
    activeLinks,
    criticalLinks,
    downLinks,
    trafficLinks,
    trafficCoverage: trafficCapable ? trafficLinks / trafficCapable : null,
    trafficPending,
    bySource,
  }
}

/** 风险链路排行：有流量的按利用率降序；其次按状态严重度。 */
export function topRiskEdges(model: NocModel, limit = 10): NocEdge[] {
  const scored = model.edges.map((e) => ({
    e,
    util: edgeUtilization(e) ?? -1,
    rank: statusRank(e.status),
  }))
  scored.sort((a, b) => {
    if (b.rank !== a.rank) return b.rank - a.rank
    return b.util - a.util
  })
  return scored.slice(0, Math.max(1, limit)).map((x) => x.e)
}

export function nodeMap(model: NocModel): Map<string, NocNode> {
  return new Map(model.nodes.map((n) => [n.key, n]))
}

/** 与该节点相连的边（画布聚焦、详情抽屉用） */
export function incidentEdges(model: NocModel, nodeKey: string): NocEdge[] {
  return model.edges.filter((e) => e.sourceNode === nodeKey || e.targetNode === nodeKey)
}

/**
 * 聚焦集合：节点本身 + 一跳邻居。
 * 返回空集合表示「不聚焦」，调用方据此不做变暗。
 */
export function focusSet(model: NocModel, nodeKey: string | null): Set<string> {
  if (!nodeKey) return new Set()
  const keep = new Set<string>([nodeKey])
  for (const e of model.edges) {
    if (e.sourceNode === nodeKey) keep.add(e.targetNode)
    if (e.targetNode === nodeKey) keep.add(e.sourceNode)
  }
  return keep
}

/** 地理坐标是否可用 */
export function hasGeo(node: NocNode): boolean {
  return typeof node.latitude === 'number' && Number.isFinite(node.latitude)
    && typeof node.longitude === 'number' && Number.isFinite(node.longitude)
}

export function statusOfNode(node: NocNode): NocStatus {
  return node.status
}

/** 某个来源的元信息（缺省返回 null，调用方显示「未加载」） */
export function sourceMeta(model: NocModel, source: SourceKey) {
  return model.sources.find((s) => s.source === source) ?? null
}
