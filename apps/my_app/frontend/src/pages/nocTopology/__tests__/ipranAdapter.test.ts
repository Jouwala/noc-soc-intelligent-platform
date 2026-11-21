/* ============================================================================
 * IPRAN 适配器测试
 * ============================================================================
 * 守住三件事：
 *   1. `gb`（Gbps）→ `capacityMbps` 的 ×1000 换算；
 *   2. 没带 `traffic=1` 时 `rxUtil/txUtil` 缺省 → null，绝不落成 0；
 *   3. 告警/在线状态 → 统一状态的映射（含 down 优先）。
 * ========================================================================== */
import { describe, expect, it } from 'vitest'

import { adaptIpran } from '../adapters/ipranAdapter'
import type { TopoNode, TopoResp } from '../../ipranTopo/types'

function node(over: Partial<TopoNode>): TopoNode {
  return {
    id: 'n1', name: 'NE1', site: 'Site1', tier: 2, model: 'M6000', ip: '10.0.0.1',
    up: 1, crit: 0, major: 0, minor: 0, warn: 0, sev: 'ok',
    lat: 6.3, lon: -10.7, geo: 'exact', geoName: 'Site1', deg: 1,
    ...over,
  }
}

function resp(over: Partial<TopoResp>): TopoResp {
  return {
    nodes: [],
    edges: [],
    meta: {
      layer: 'ipran', ne: 0, ext: 0, rawLinks: 0, foldedLinks: 0,
      tierCounts: {}, geo: {}, geoMissSites: [], geoFuzzyPairs: [], critNe: 0,
      warnings: [], fetchedAt: '2026-11-19T17:53:57+00:00',
    },
    ...over,
  }
}

describe('IPRAN 适配器', () => {
  it('节点 key 带来源前缀，tier 映射为设备类别', () => {
    const slice = adaptIpran({
      resp: resp({
        nodes: [
          node({ id: 'a', tier: 0 }),
          node({ id: 'b', tier: 1 }),
          node({ id: 'c', tier: 2 }),
          node({ id: 'd', tier: 3 }),
        ],
      }),
      layer: 'ipran',
      state: 'connected',
      trafficRequested: false,
    })
    expect(slice.nodes.map((n) => n.key)).toEqual(['ipran:a', 'ipran:b', 'ipran:c', 'ipran:d'])
    expect(slice.nodes.map((n) => n.deviceType)).toEqual(['isp', 'core', 'aggregation', 'access'])
    expect(slice.nodes[0].source).toBe('ipran')
  })

  it('状态映射：down 优先于告警，其次 crit / major / minor', () => {
    const slice = adaptIpran({
      resp: resp({
        nodes: [
          node({ id: 'a', up: 0, crit: 9 }),
          node({ id: 'b', crit: 1 }),
          node({ id: 'c', major: 1 }),
          node({ id: 'd', minor: 1 }),
          node({ id: 'e' }),
        ],
      }),
      layer: 'ipran',
      state: 'connected',
      trafficRequested: false,
    })
    expect(slice.nodes.map((n) => n.status)).toEqual(['down', 'critical', 'major', 'minor', 'ok'])
  })

  it('gb(Gbps) → Mbps，缺 gb 保持 null', () => {
    const slice = adaptIpran({
      resp: resp({
        nodes: [node({ id: 'a' }), node({ id: 'b' })],
        edges: [
          { s: 'a', t: 'b', n: 1, gb: 25, down: 0, crit: 0, major: 0, ports: [] },
          { s: 'a', t: 'b', n: 1, gb: null, down: 0, crit: 0, major: 0, ports: [] },
        ],
      }),
      layer: 'ipran',
      state: 'connected',
      trafficRequested: false,
    })
    expect(slice.edges[0].capacityMbps).toBe(25000)
    expect(slice.edges[1].capacityMbps).toBeNull()
  })

  it('没有 traffic=1：链路无流量，利用率为 null 而不是 0', () => {
    const slice = adaptIpran({
      resp: resp({
        nodes: [node({ id: 'a' }), node({ id: 'b' })],
        edges: [{ s: 'a', t: 'b', n: 1, gb: 10, down: 0, crit: 0, major: 0, ports: [] }],
      }),
      layer: 'ipran',
      state: 'connected',
      trafficRequested: false,
    })
    expect(slice.edges[0].trafficAvailable).toBe(false)
    expect(slice.edges[0].txUtilization).toBeNull()
    expect(slice.edges[0].rxUtilization).toBeNull()
  })

  it('traffic=1：TX/RX 分别搬运，不平均；端口明细映射到 checkpoints', () => {
    const slice = adaptIpran({
      resp: resp({
        nodes: [node({ id: 'a' }), node({ id: 'b' })],
        edges: [{
          s: 'a', t: 'b', n: 2, gb: 10, down: 0, crit: 0, major: 0, ports: ['p1'],
          txUtil: 72.5, rxUtil: 11.2,
          utilPorts: [{ port: 'p1', me: 'a', side: 'src', gb: 10, rx: 11.2, tx: 72.5 }],
        }],
        meta: {
          layer: 'ipran', ne: 2, ext: 0, rawLinks: 1, foldedLinks: 1,
          tierCounts: {}, geo: {}, geoMissSites: [], geoFuzzyPairs: [], critNe: 0,
          warnings: [], fetchedAt: '2026-11-19T17:53:57+00:00',
          traffic: {
            sampledAt: '2026-11-20 00:30:00', tables: ['t'], rows: 1,
            totalEdges: 1, matchedEdges: 1, totalPorts: 1, matchedPorts: 1, noBandwidthPorts: 0,
          },
        },
      }),
      layer: 'ipran',
      state: 'partial',
      trafficRequested: true,
    })
    const e = slice.edges[0]
    expect(e.trafficAvailable).toBe(true)
    expect(e.txUtilization).toBe(72.5)
    expect(e.rxUtilization).toBe(11.2)
    expect(e.checkpoints?.[0].port).toBe('p1')
    expect(slice.meta.coverage).toBe(1)
    expect(slice.meta.sampledAt).toBe('2026-11-20 00:30:00')
  })

  it('链路状态：down>0 优先，其次 crit / major', () => {
    const slice = adaptIpran({
      resp: resp({
        nodes: [node({ id: 'a' }), node({ id: 'b' })],
        edges: [
          { s: 'a', t: 'b', n: 2, gb: 10, down: 1, crit: 0, major: 0, ports: [] },
          { s: 'a', t: 'b', n: 1, gb: 10, down: 0, crit: 1, major: 0, ports: [] },
          { s: 'a', t: 'b', n: 1, gb: 10, down: 0, crit: 0, major: 1, ports: [] },
        ],
      }),
      layer: 'ipran',
      state: 'connected',
      trafficRequested: false,
    })
    expect(slice.edges.map((e) => e.status)).toEqual(['down', 'critical', 'major'])
  })
})
