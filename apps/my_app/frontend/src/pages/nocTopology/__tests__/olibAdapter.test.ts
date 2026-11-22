/* ============================================================================
 * OLIB 适配器测试
 * ============================================================================
 * OLIB 是唯一「永远没有实时流量」的来源，测试重点不是数据映射而是**边界**：
 *   1. 所有边 `schematic=true` —— 连接关系示意，不是光缆路由；
 *   2. `trafficAvailable=false`、tx/rx 恒 null —— 不能给静态资产编造利用率；
 *   3. 计划链路（dash）→ planned；
 *   4. 只把参与链路的站点（+ 核心站）放进拓扑，孤立站点不制造噪声。
 * ========================================================================== */
import { describe, expect, it } from 'vitest'

import { adaptOlib } from '../adapters/olibAdapter'
import type { FiberLink, GeoLink, GeoSite } from '../../networkTopologyV2/types'

function site(index: number, name: string, over: Partial<GeoSite> = {}): GeoSite {
  return {
    name, x: index, y: index, is5g: false, isp: false, sector: 'S1', core: false,
    index, located: true, method: 'name-exact', verified: true,
    lat: 6.2 + index * 0.01, lng: -10.5 + index * 0.01, siteId: `sid-${index}`,
    ...over,
  }
}

function coreLink(a: GeoSite, b: GeoSite): GeoLink {
  return { id: `${a.index}-${b.index}-dwdm-0`, a, b, type: 'dwdm', color: '#7b2d8b', offset: 0 }
}

function fiberLink(a: GeoSite, b: GeoSite, over: Partial<FiberLink> = {}): FiberLink {
  return { id: `${a.index}-${b.index}-base`, a, b, group: 'base', type: '10G', dash: false, ...over }
}

describe('OLIB 适配器', () => {
  const a = site(0, 'Alpha', { core: true })
  const b = site(1, 'Bravo')
  const c = site(2, 'Charlie')
  const isolated = site(3, 'Isolated')

  it('只收参与链路的站点与核心站，孤立站点不进拓扑', () => {
    const slice = adaptOlib({
      sites: [a, b, c, isolated],
      coreLinks: [coreLink(a, b)],
      fiberLinks: [fiberLink(b, c)],
      skippedCore: 0,
      skippedFiber: 0,
      fiberStale: false,
      state: 'static',
    })
    expect(slice.nodes.map((n) => n.nativeId)).toEqual(['0', '1', '2'])
    expect(slice.nodes.find((n) => n.nativeId === '3')).toBeUndefined()
    expect(slice.nodes[0].key).toBe('olib:0')
    expect(slice.nodes[0].deviceType).toBe('core')
    expect(slice.nodes[0].siteId).toBe('sid-0')
  })

  it('所有 OLIB 边都是示意连接、没有流量、利用率为 null', () => {
    const slice = adaptOlib({
      sites: [a, b, c],
      coreLinks: [coreLink(a, b)],
      fiberLinks: [fiberLink(b, c)],
      skippedCore: 0,
      skippedFiber: 0,
      fiberStale: false,
      state: 'static',
    })
    for (const e of slice.edges) {
      expect(e.schematic).toBe(true)
      expect(e.trafficAvailable).toBe(false)
      expect(e.txUtilization).toBeNull()
      expect(e.rxUtilization).toBeNull()
      expect(e.source).toBe('olib')
    }
    expect(slice.edges.find((e) => e.key.startsWith('olib:core'))?.medium).toBe('schematic')
    expect(slice.edges.find((e) => e.key.startsWith('olib:fiber'))?.medium).toBe('fiber')
  })

  it('计划链路：dash=true → planned，其余 static', () => {
    const slice = adaptOlib({
      sites: [a, b, c],
      coreLinks: [],
      fiberLinks: [
        fiberLink(a, b, { id: 'p1', dash: true }),
        fiberLink(b, c, { id: 'p2', dash: false }),
      ],
      skippedCore: 0,
      skippedFiber: 0,
      fiberStale: false,
      state: 'static',
    })
    expect(slice.edges.map((e) => e.planned)).toEqual([true, false])
    expect(slice.edges.map((e) => e.status)).toEqual(['planned', 'static'])
  })

  it('未定位站点：坐标落 null，但仍在逻辑视图里', () => {
    const unlocated = site(4, 'NoGeo', { located: false, lat: undefined, lng: undefined })
    const slice = adaptOlib({
      sites: [a, unlocated],
      coreLinks: [coreLink(a, unlocated)],
      fiberLinks: [],
      skippedCore: 0,
      skippedFiber: 0,
      fiberStale: false,
      state: 'static',
    })
    const n = slice.nodes.find((x) => x.name === 'NoGeo')
    expect(n?.latitude).toBeNull()
    expect(n?.longitude).toBeNull()
  })

  it('跳过的链路数进入 stats，映射过期进入 stale', () => {
    const slice = adaptOlib({
      sites: [a, b],
      coreLinks: [],
      fiberLinks: [],
      skippedCore: 7,
      skippedFiber: 133,
      fiberStale: true,
      state: 'static',
      geoGeneratedAt: '2026-11-17',
    })
    expect(slice.meta.stats?.skippedLinks).toBe(140)
    expect(slice.meta.stats?.staleMapping).toBe(1)
    expect(slice.meta.stale).toBe(true)
    expect(slice.meta.fetchedAt).toBe('2026-11-17')
  })
})
