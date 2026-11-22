/* ============================================================================
 * IPRAN（UME 四层）适配器
 * ============================================================================
 * 输入是 `/api/ipran-topo/graph` 的原样响应（`../ipranTopo/types.ts` 的 TopoResp）。
 * 只做「改名 + 定级 + 缺值规整」，不重算利用率、不合并收发。
 *
 * 两个容易做错的地方：
 *   1. `edge.gb` 是 **Gbps**，模型里统一存 Mbps，所以 ×1000；
 *   2. 不带 `traffic=1` 时 `rxUtil/txUtil` 是 `undefined`，必须落到 `null`，
 *      由 `trafficAvailable=false` 让界面画 N/A 虚线 —— 不能写成 0。
 * ========================================================================== */
import type { LayerKey, TopoEdge, TopoNode, TopoResp } from '../../ipranTopo/types'
import type {
  NocCheckpoint, NocDeviceType, NocEdge, NocNode, NocStatus, SourceSlice, SourceState,
} from '../types'

export interface IpranAdapterInput {
  resp: TopoResp | null
  layer: LayerKey | string
  state: SourceState
  error?: string
  /** 本次是否请求了 `traffic=1` */
  trafficRequested: boolean
}

function deviceTypeOf(tier: number): NocDeviceType {
  switch (tier) {
    case 0: return 'isp'
    case 1: return 'core'
    case 2: return 'aggregation'
    default: return 'access'
  }
}

function statusOfNode(n: TopoNode): NocStatus {
  if (n.up === 0) return 'down'
  if (n.crit > 0) return 'critical'
  if (n.major > 0) return 'major'
  if (n.minor > 0 || n.warn > 0) return 'minor'
  return 'ok'
}

function statusOfEdge(e: TopoEdge): NocStatus {
  if (e.down > 0) return 'down'
  if (e.crit > 0) return 'critical'
  if (e.major > 0) return 'major'
  return 'ok'
}

function checkpointsOf(e: TopoEdge): NocCheckpoint[] | undefined {
  if (!e.utilPorts?.length) return undefined
  return e.utilPorts.map((p) => ({
    port: p.port,
    peerPort: p.me,
    txUtilization: p.tx,
    rxUtilization: p.rx,
  }))
}

export function adaptIpran(input: IpranAdapterInput): SourceSlice {
  const { resp, layer, state, error, trafficRequested } = input
  const meta = resp?.meta

  const nodes: NocNode[] = (resp?.nodes ?? []).map((n) => ({
    key: `ipran:${n.id}`,
    source: 'ipran',
    nativeId: n.id,
    name: n.name,
    ip: n.ip || undefined,
    siteName: n.site || undefined,
    // geo = 'miss' / 'ext' 时后端给 null，保持 null 让地理视图跳过
    latitude: n.lat,
    longitude: n.lon,
    deviceType: deviceTypeOf(n.tier),
    layer: layer,
    status: statusOfNode(n),
    alarms: { crit: n.crit, major: n.major, minor: n.minor, warn: n.warn },
    degree: n.deg,
  }))

  const byId = new Map(nodes.map((n) => [n.nativeId, n]))

  const edges: NocEdge[] = (resp?.edges ?? []).map((e, i) => {
    const a = byId.get(e.s)
    const b = byId.get(e.t)
    const trafficAvailable = e.txUtil != null || e.rxUtil != null
    return {
      key: `ipran:${e.s}->${e.t}#${i}`,
      source: 'ipran',
      sourceNode: `ipran:${e.s}`,
      targetNode: `ipran:${e.t}`,
      medium: 'fiber',
      sourceName: a?.name ?? e.s,
      targetName: b?.name ?? e.t,
      capacityMbps: e.gb == null ? null : e.gb * 1000,
      txUtilization: trafficAvailable ? (e.txUtil ?? null) : null,
      rxUtilization: trafficAvailable ? (e.rxUtil ?? null) : null,
      status: statusOfEdge(e),
      trafficAvailable,
      planned: false,
      schematic: false,
      ports: e.ports,
      checkpoints: checkpointsOf(e),
    }
  })

  const trafficMeta = meta?.traffic
  const coverage = trafficMeta && trafficMeta.totalEdges > 0
    ? trafficMeta.matchedEdges / trafficMeta.totalEdges
    : undefined

  return {
    nodes,
    edges,
    meta: {
      source: 'ipran',
      state,
      fetchedAt: meta?.fetchedAt,
      sampledAt: trafficMeta?.sampledAt ?? undefined,
      granularity: trafficRequested ? '15min' : undefined,
      stale: meta?.stale === true,
      trafficLoaded: trafficRequested,
      coverage,
      warnings: meta?.warnings ?? [],
      nodeCount: nodes.length,
      edgeCount: edges.length,
      error,
    },
  }
}
