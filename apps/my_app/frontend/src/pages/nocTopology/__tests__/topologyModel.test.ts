/* ============================================================================
 * 统一模型 + 布局测试
 * ============================================================================
 * 这几个纯函数决定「用户在画布上看到什么」，必须可在没有浏览器的情况下验证：
 *   · 筛选：来源 / 搜索 / 只看有流量 / 只看问题；
 *   · 统计：活动链路与流量覆盖率的分母不含 OLIB；
 *   · 风险排序：断链排在「90% 但 healthy」前面；
 *   · 地理投影：两轴同一个 scale（地图不会被拉伸）；
 *   · 逻辑布局：确定性、所有节点都有坐标。
 * ========================================================================== */
import { describe, expect, it } from 'vitest'

import {
  edgeUtilization, filterModel, focusSet, hasGeo, summarize, topRiskEdges, withDegree,
} from '../model'
import { layoutGraph } from '../layouts/graphLayout'
import { projectGeographic } from '../layouts/geographicLayout'
import type { NocEdge, NocModel, NocNode } from '../types'

function node(key: string, over: Partial<NocNode> = {}): NocNode {
  return {
    key, source: key.split(':')[0] as NocNode['source'], nativeId: key,
    name: key, deviceType: 'access', status: 'ok', degree: 0, ...over,
  }
}

function edge(key: string, a: string, b: string, over: Partial<NocEdge> = {}): NocEdge {
  return {
    key, source: key.split(':')[0] as NocEdge['source'],
    sourceNode: a, targetNode: b, sourceName: a, targetName: b,
    medium: 'fiber', status: 'ok', trafficAvailable: false, planned: false, schematic: false,
    ...over,
  }
}

function model(): NocModel {
  return {
    nodes: [
      node('ipran:a', { name: 'Core A' }),
      node('ipran:b', { name: 'Agg B' }),
      node('mw:c', { name: 'MW C' }),
      node('mw:e', { name: 'MW E' }),
      node('olib:d', { name: 'Site D', status: 'static' }),
      node('olib:e', { name: 'Site E', status: 'static' }),
    ],
    edges: [
      edge('ipran:e1', 'ipran:a', 'ipran:b', { status: 'critical', trafficAvailable: true, txUtilization: 92, rxUtilization: 10 }),
      edge('mw:e2', 'mw:c', 'mw:e', { status: 'ok', trafficAvailable: true, txUtilization: 20, rxUtilization: 5 }),
      edge('olib:e3', 'olib:d', 'olib:e', { status: 'static', medium: 'schematic', schematic: true }),
    ],
    sources: [
      { source: 'ipran', state: 'connected', stale: false, trafficLoaded: true, warnings: [], nodeCount: 2, edgeCount: 1 },
      { source: 'mw', state: 'connected', stale: false, trafficLoaded: true, warnings: [], nodeCount: 2, edgeCount: 1 },
      { source: 'olib', state: 'static', stale: false, trafficLoaded: false, warnings: [], nodeCount: 2, edgeCount: 1 },
    ],
  }
}

describe('统一模型', () => {
  it('按来源筛选：节点与边同时收窄', () => {
    const m = filterModel(model(), { source: 'mw', query: '', trafficOnly: false, problemsOnly: false })
    expect(m.nodes.map((n) => n.key).sort()).toEqual(['mw:c', 'mw:e'])
    expect(m.edges.map((e) => e.key)).toEqual(['mw:e2'])
  })

  it('搜索保留命中节点的一跳邻居（不制造孤点）', () => {
    const m = filterModel(model(), { source: 'all', query: 'core a', trafficOnly: false, problemsOnly: false })
    const keys = m.nodes.map((n) => n.key).sort()
    expect(keys).toContain('ipran:a')
    expect(keys).toContain('ipran:b')
    expect(keys).not.toContain('mw:c')
  })

  it('只看有流量：只剩 trafficAvailable 的边与两端节点', () => {
    const m = filterModel(model(), { source: 'all', query: '', trafficOnly: true, problemsOnly: false })
    expect(m.edges.map((e) => e.key).sort()).toEqual(['ipran:e1', 'mw:e2'])
    expect(m.nodes.every((n) => n.source !== 'olib')).toBe(true)
  })

  it('只看问题：只剩 minor 及以上状态的边', () => {
    const m = filterModel(model(), { source: 'all', query: '', trafficOnly: false, problemsOnly: true })
    expect(m.edges.map((e) => e.key)).toEqual(['ipran:e1'])
  })

  it('度数在筛选结果上重算', () => {
    const m = withDegree(model().nodes, model().edges)
    expect(m.find((n) => n.key === 'ipran:a')?.degree).toBe(1)
    expect(m.find((n) => n.key === 'ipran:b')?.degree).toBe(1)
    expect(m.find((n) => n.key === 'mw:c')?.degree).toBe(1)
  })

  it('汇总：活动链路排除 static/planned/unknown/down；覆盖率分母不含 OLIB', () => {
    const s = summarize(model())
    expect(s.nodes).toBe(6)
    expect(s.links).toBe(3)
    // critical 算活动链路；static 不算
    expect(s.activeLinks).toBe(2)
    expect(s.criticalLinks).toBe(1)
    // 有流量的 2 条 ÷ 有流量能力的 2 条（OLIB 不进分母）= 100%
    expect(s.trafficCoverage).toBe(1)
    expect(s.bySource.olib.links).toBe(1)
    expect(s.trafficPending).toEqual([])
  })

  it('没取流量的来源不进覆盖率分母（没取 ≠ 没覆盖）', () => {
    const m = model()
    m.sources = m.sources.map((s) => (s.source === 'ipran' ? { ...s, trafficLoaded: false } : s))
    const s = summarize(m)
    // 只剩 MW：1 条有数据 ÷ 1 条已取 = 100%；仍有流量数据但没取的 IPRAN 进 pending
    expect(s.trafficCoverage).toBe(1)
    expect(s.trafficPending).toEqual(['ipran'])
  })

  it('风险排序：断链优先于高负载 healthy', () => {
    const m: NocModel = {
      nodes: [],
      edges: [
        edge('ipran:hi', 'a', 'b', { status: 'ok', trafficAvailable: true, txUtilization: 95 }),
        edge('ipran:down', 'a', 'b', { status: 'down' }),
      ],
      sources: [],
    }
    expect(topRiskEdges(m, 2)[0].key).toBe('ipran:down')
  })

  it('利用率取两方向最大，但单侧 null 不被当成 0', () => {
    expect(edgeUtilization(edge('x:1', 'a', 'b', { trafficAvailable: true, txUtilization: 12, rxUtilization: null }))).toBe(12)
    expect(edgeUtilization(edge('x:2', 'a', 'b', { trafficAvailable: false, txUtilization: null, rxUtilization: null }))).toBeNull()
    expect(edgeUtilization(edge('x:3', 'a', 'b', { trafficAvailable: true, txUtilization: 0, rxUtilization: null }))).toBe(0)
  })

  it('聚焦集合 = 自身 + 一跳邻居', () => {
    expect([...focusSet(model(), 'ipran:a')].sort()).toEqual(['ipran:a', 'ipran:b'])
    expect(focusSet(model(), null).size).toBe(0)
  })

  it('地理坐标有效性', () => {
    expect(hasGeo(node('x:1', { latitude: 6.3, longitude: -10.7 }))).toBe(true)
    expect(hasGeo(node('x:2', { latitude: null, longitude: null }))).toBe(false)
    expect(hasGeo(node('x:3'))).toBe(false)
  })
})

describe('逻辑布局', () => {
  it('确定性：同一输入两次得到相同坐标', () => {
    const m = model()
    const a = layoutGraph(m.nodes, m.edges)
    const b = layoutGraph(m.nodes, m.edges)
    expect(a.pos).toEqual(b.pos)
    expect(a.width).toBeGreaterThan(0)
    expect(a.height).toBeGreaterThan(0)
  })

  it('所有节点都有坐标，孤立点也算一个分量', () => {
    const nodes = [node('ipran:a'), node('ipran:b'), node('ipran:c')]
    const edges = [edge('ipran:e', 'ipran:a', 'ipran:b')]
    const l = layoutGraph(nodes, edges)
    for (const n of nodes) expect(l.pos[n.key]).toBeDefined()
    expect(l.components).toBe(2)
  })

  it('空输入不崩', () => {
    const l = layoutGraph([], [])
    expect(l.components).toBe(0)
    expect(l.pos).toEqual({})
  })
})

describe('地理投影（保真实比例）', () => {
  it('同一度数的经度差与纬度差在低纬处得到相近的像素距离（不按屏幕拉伸）', () => {
    const nodes = [
      node('x:o', { latitude: 0, longitude: 0 }),
      node('x:lon', { latitude: 0, longitude: 1 }),
      node('x:lat', { latitude: 1, longitude: 0 }),
    ]
    const p = projectGeographic(nodes, 1000, 640, 20)
    const o = p.pos['x:o']
    const dx = Math.abs(p.pos['x:lon'].x - o.x)
    const dy = Math.abs(p.pos['x:lat'].y - o.y)
    // Mercator 在赤道附近两轴等距，允许 1% 误差
    expect(Math.abs(dx - dy) / Math.max(dx, dy)).toBeLessThan(0.01)
  })

  it('没有坐标的节点被跳过，返回 skipped 计数', () => {
    const p = projectGeographic([node('x:1'), node('x:2', { latitude: 6.3, longitude: -10.7 })], 800, 600, 20)
    expect(p.skipped).toBe(1)
    expect(p.empty).toBe(false)
    expect(Object.keys(p.pos)).toEqual(['x:2'])
  })

  it('全部无坐标：empty=true，不产生 NaN 坐标', () => {
    const p = projectGeographic([node('x:1')])
    expect(p.empty).toBe(true)
    expect(p.pos).toEqual({})
    expect(p.scale).toBe(0)
  })

  it('所有点都落在画布内（居中留白，不溢出）', () => {
    const nodes = [
      node('x:1', { latitude: 4.3, longitude: -11.4 }),
      node('x:2', { latitude: 8.5, longitude: -7.3 }),
    ]
    const p = projectGeographic(nodes, 900, 500, 30)
    for (const pt of Object.values(p.pos)) {
      expect(pt.x).toBeGreaterThanOrEqual(30 - 1e-6)
      expect(pt.x).toBeLessThanOrEqual(900 - 30 + 1e-6)
      expect(pt.y).toBeGreaterThanOrEqual(30 - 1e-6)
      expect(pt.y).toBeLessThanOrEqual(500 - 30 + 1e-6)
    }
  })
})
