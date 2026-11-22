/* ============================================================================
 * OLIB FO 适配器
 * ============================================================================
 * 输入不是接口响应，而是既有页面已经装配好的三组数据：
 *   · `buildGeoSites()`  —— 667 个 PDF 站点 + 真实 GPS（命中 541）
 *   · `buildGeoLinks()`  —— `CORE_LINKS` 骨干关系落到经纬度（示意直线）
 *   · `buildFiberLinks()`—— `olib-fiber-links.json` 的 592 条 PDF 数字化链路
 * 三个函数都在 `../networkTopologyV2/geoData.ts`，本适配器只做**形状翻译**，
 * 不重新做站名匹配、不重新计算坐标。
 *
 * ⚠ 两条硬限制（见 `docs/NOC_TOPOLOGY_DATA_CONTRACT.md` 第四节）：
 *   1. 所有 OLIB 边 `schematic = true` —— 连接关系示意，不是光缆路由；
 *   2. 没有实时流量 → `trafficAvailable = false`、tx/rx 恒 null，
 *      界面据此画中性灰，**绝不上利用率色阶**。
 * ========================================================================== */
import type { CoreLinkType } from '../../networkTopology/topologyData'
import type { FiberLink, GeoLink, GeoSite } from '../../networkTopologyV2/types'
import type { NocEdge, NocNode, SourceSlice, SourceState } from '../types'

export interface OlibAdapterInput {
  sites: GeoSite[]
  coreLinks: GeoLink[]
  fiberLinks: FiberLink[]
  /** 端点无 GPS 被跳过的骨干链路数（图例展示） */
  skippedCore: number
  /** 端点未定位/映射过期被跳过的 PDF 链路数 */
  skippedFiber: number
  /** 映射表与当前站点表不一致（整层判为不可信） */
  fiberStale: boolean
  /** `olib-site-geo.json` 的生成日期 */
  geoGeneratedAt?: string
  state: SourceState
  error?: string
}

function coreNodeType(s: GeoSite): 'core' | 'isp' | 'site' {
  if (s.core) return 'core'
  if (s.isp) return 'isp'
  return 'site'
}

export function adaptOlib(input: OlibAdapterInput): SourceSlice {
  const {
    sites, coreLinks, fiberLinks, skippedCore, skippedFiber, fiberStale,
    geoGeneratedAt, state, error,
  } = input

  /* 只把「参与了某条链路」的站点放进拓扑，外加核心站。
     667 个站点里有一百多个既无链路也无 GPS，全画进来只是孤点噪声。 */
  const used = new Set<number>()
  for (const l of coreLinks) { used.add(l.a.index); used.add(l.b.index) }
  for (const l of fiberLinks) { used.add(l.a.index); used.add(l.b.index) }
  for (const s of sites) if (s.core && s.located) used.add(s.index)

  const byIndex = new Map<number, GeoSite>()
  const nodes: NocNode[] = []
  for (const s of sites) {
    if (!used.has(s.index)) continue
    byIndex.set(s.index, s)
    nodes.push({
      key: `olib:${s.index}`,
      source: 'olib',
      nativeId: String(s.index),
      name: s.name,
      siteId: s.siteId,
      siteName: s.name,
      latitude: s.located ? s.lat : null,
      longitude: s.located ? s.lng : null,
      deviceType: coreNodeType(s),
      status: 'static',
      degree: 0,
    })
  }

  const edges: NocEdge[] = []
  const seen = new Set<string>()

  for (const [i, l] of coreLinks.entries()) {
    const a = byIndex.get(l.a.index)
    const b = byIndex.get(l.b.index)
    if (!a || !b) continue
    let key = `olib:core:${l.id}`
    if (seen.has(key)) key = `${key}#${i}`
    seen.add(key)
    edges.push({
      key,
      source: 'olib',
      sourceNode: `olib:${l.a.index}`,
      targetNode: `olib:${l.b.index}`,
      medium: 'schematic',
      sourceName: a.name,
      targetName: b.name,
      capacityMbps: null,
      txUtilization: null,
      rxUtilization: null,
      status: 'static',
      trafficAvailable: false,
      planned: false,
      schematic: true,
      ports: [l.type],
    })
  }

  for (const [i, l] of fiberLinks.entries()) {
    const a = byIndex.get(l.a.index)
    const b = byIndex.get(l.b.index)
    if (!a || !b) continue
    let key = `olib:fiber:${l.id}`
    if (seen.has(key)) key = `${key}#${i}`
    seen.add(key)
    edges.push({
      key,
      source: 'olib',
      sourceNode: `olib:${l.a.index}`,
      targetNode: `olib:${l.b.index}`,
      medium: 'fiber',
      sourceName: a.name,
      targetName: b.name,
      capacityMbps: null,
      txUtilization: null,
      rxUtilization: null,
      status: l.dash ? 'planned' : 'static',
      trafficAvailable: false,
      planned: l.dash,
      schematic: true,
      ports: [l.type],
    })
  }

  const warnings: string[] = []

  return {
    nodes,
    edges,
    meta: {
      source: 'olib',
      state,
      fetchedAt: geoGeneratedAt,
      granularity: 'static',
      stale: fiberStale,
      trafficLoaded: false,
      warnings,
      stats: {
        skippedLinks: skippedCore + skippedFiber,
        staleMapping: fiberStale ? 1 : 0,
      },
      nodeCount: nodes.length,
      edgeCount: edges.length,
      error,
    },
  }
}

/** 给图例用的链路类型文案（骨干链路 type 原样展示） */
export function coreLinkTypeLabel(type: CoreLinkType): string {
  switch (type) {
    case 'exist': return 'Existing'
    case 'dwdm': return 'DWDM'
    case 'rail': return 'Rail'
    default: return 'P25A'
  }
}
