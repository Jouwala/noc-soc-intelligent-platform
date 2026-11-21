/* ============================================================================
 * MW（微波 UME）适配器
 * ============================================================================
 * 输入是 `/api/mw-ume/topology` 或 `/api/mw-ume/realtime` 的原样响应。
 *
 * ⚠ 本文件最重要的一条规则（2026-11-19 实测确认）：
 *   后端对「没有 KPI 的物理链路」返回的是 `tx_util: 0.0 / rx_util: 0.0` +
 *   `kpi_backed: false`（见 `mw_ume_topology.py:677-696`）。如果直接搬运，
 *   中心会把 20 条未采集链路画成「0% 正常」，正好是需求里禁止的「把未采集
 *   当成零流量」。所以这里以 **`kpi_backed`** 为准：false → 两侧利用率一律 null。
 *
 * 另外：MW 的 healthy/minor/major/critical 是业务四级，原样保留在 `nativeStatus`，
 * 只把名字映射到统一状态，不重新分级、不与 8 档流量色阶混用。
 * ========================================================================== */
import type { MwUmeEdge, MwUmeTopologyResponse } from '../../../api'
import type { NocCheckpoint, NocEdge, NocHistoryPoint, NocNode, NocStatus, SourceSlice, SourceState } from '../types'

export interface MwAdapterInput {
  resp: MwUmeTopologyResponse | null
  state: SourceState
  error?: string
  /** true = 15 分钟实时接口 */
  realtime: boolean
}

function statusOf(raw: string | undefined): NocStatus {
  switch (raw) {
    case 'healthy': return 'ok'
    case 'minor': return 'minor'
    case 'major': return 'major'
    case 'critical': return 'critical'
    case 'unknown': return 'unknown'
    default: return 'unknown'
  }
}

function num(v: string | number | null | undefined): number | null {
  if (v == null || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

function checkpointsOf(e: MwUmeEdge): NocCheckpoint[] | undefined {
  if (!e.checkpoints?.length) return undefined
  return e.checkpoints.map((c) => ({
    port: c.cp || '—',
    peerPort: c.ncp || undefined,
    txThroughput: num(c.tx_t),
    rxThroughput: num(c.rx_t),
    txUtilization: num(c.tx_u),
    rxUtilization: num(c.rx_u),
  }))
}

function historyOf(e: MwUmeEdge): NocHistoryPoint[] | undefined {
  if (!e.history?.length) return undefined
  return e.history.map((h) => ({
    period: h.date,
    txUtilization: num(h.tx_util),
    rxUtilization: num(h.rx_util),
    maxUtilization: num(h.max_util),
  }))
}

export function adaptMw(input: MwAdapterInput): SourceSlice {
  const { resp, state, error, realtime } = input
  const meta = resp?.meta
  const kpis = resp?.kpis

  const nodes: NocNode[] = []
  const seen = new Set<string>()
  for (const n of resp?.nodes ?? []) {
    if (seen.has(n.id)) continue
    seen.add(n.id)
    nodes.push({
      key: `mw:${n.id}`,
      source: 'mw',
      nativeId: n.id,
      name: n.name || n.id,
      ip: n.ip || undefined,
      siteId: n.site_id || undefined,
      siteName: n.site || undefined,
      latitude: n.lat,
      longitude: n.lon,
      deviceType: 'microwave',
      layer: realtime ? '15min' : '1day',
      status: statusOf(n.status),
      degree: n.degree,
    })
  }

  const nameOf = new Map(nodes.map((n) => [n.nativeId, n.name]))

  const edges: NocEdge[] = (resp?.edges ?? []).map((e, i) => {
    const backed = e.kpi_backed === true
    return {
      key: `mw:${e.source}->${e.target}#${i}`,
      source: 'mw',
      sourceNode: `mw:${e.source}`,
      targetNode: `mw:${e.target}`,
      medium: 'microwave',
      sourceName: nameOf.get(e.source) ?? e.source,
      targetName: nameOf.get(e.target) ?? e.target,
      capacityMbps: e.capacity ?? null,
      // 关键：未采集链路后端给的是 0.0，必须按 kpi_backed 改回 null
      txUtilization: backed ? (e.tx_util ?? null) : null,
      rxUtilization: backed ? (e.rx_util ?? null) : null,
      status: statusOf(e.status),
      nativeStatus: e.status,
      trafficAvailable: backed,
      planned: false,
      schematic: false,
      checkpoints: checkpointsOf(e),
      history: historyOf(e),
    }
  })

  const coverage = kpis && kpis.physical_links > 0
    ? kpis.links / kpis.physical_links
    : undefined

  return {
    nodes,
    edges,
    meta: {
      source: 'mw',
      state,
      fetchedAt: meta?.generated_at,
      sampledAt: meta?.selected_date ?? undefined,
      granularity: meta?.granularity,
      stale: false,
      trafficLoaded: true,
      coverage,
      warnings: [],
      nodeCount: nodes.length,
      edgeCount: edges.length,
      error,
    },
  }
}
