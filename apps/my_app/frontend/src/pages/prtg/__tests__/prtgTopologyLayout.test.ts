/* ============================================================================
 * PRTG 拓扑布局 / 并行边合并 / 周期映射 单测（P2）
 * ========================================================================== */

import { describe, expect, it } from 'vitest'

import {
  buildPeerLabelBySensorId, deviceClusterKey, isSecurityName, layoutColumnOf,
  layoutTopoNodes, mapWindowToTopoPeriod, mergeParallelEdges, trafficWidth,
  type TopoLink, type TopoNode,
} from '../prtgTopologyModels'

function node(partial: Partial<TopoNode> & Pick<TopoNode, 'id' | 'name' | 'type'>): TopoNode {
  return { x: 0, y: 0, pinned: false, ...partial }
}

function link(partial: Partial<TopoLink> & Pick<TopoLink, 'id' | 'source_node' | 'target_node'>): TopoLink {
  return { status: 'Up', ...partial }
}

describe('mapWindowToTopoPeriod', () => {
  it('5m → live, 24h/7d 原样, 30d 退回 7d', () => {
    expect(mapWindowToTopoPeriod('5m')).toEqual({ period: 'live', fallbackFrom30d: false })
    expect(mapWindowToTopoPeriod('24h')).toEqual({ period: '24h', fallbackFrom30d: false })
    expect(mapWindowToTopoPeriod('7d')).toEqual({ period: '7d', fallbackFrom30d: false })
    expect(mapWindowToTopoPeriod('30d')).toEqual({ period: '7d', fallbackFrom30d: true })
  })
})

describe('layoutColumnOf / isSecurityName / deviceClusterKey', () => {
  it('识别 FG/FW 安全设备列', () => {
    expect(isSecurityName('OLIB-NX-3172-WAN-2-FG1500-LAN-2-P-39')).toBe(true)
    expect(isSecurityName('PTP_SW')).toBe(false)
    expect(layoutColumnOf(node({ id: 1, name: 'peer-A', type: 'peer' }))).toBe('peer')
    expect(layoutColumnOf(node({ id: 2, name: 'xxx-FG-WAN', type: 'unknown' }))).toBe('security')
    expect(layoutColumnOf(node({ id: 3, name: 'OLIB-NX', type: 'device' }))).toBe('device')
    expect(layoutColumnOf(node({ id: 4, name: 'port-channel2', type: 'unknown' }))).toBe('other')
  })

  it('核心设备簇短名', () => {
    expect(deviceClusterKey('OLIB-NX-3172-WAN-2.orangeliberia (NEXUS 3172) [Cisco Device]'))
      .toBe('OLIB-NX-3172-WAN-2')
    expect(deviceClusterKey('PTP_SW')).toBe('PTP_SW')
    expect(deviceClusterKey('AML TOKADEH1')).toBe('AML TOKADEH1')
  })
})

describe('mergeParallelEdges', () => {
  it('同对节点多条边合并，最差状态优先', () => {
    const merged = mergeParallelEdges([
      link({ id: 1, source_node: 10, target_node: 20, status: 'Up', sensor_id: 1 }),
      link({ id: 2, source_node: 20, target_node: 10, status: 'Down', sensor_id: 2 }),
      link({ id: 3, source_node: 10, target_node: 30, status: 'Unusual', sensor_id: 3 }),
    ])
    expect(merged).toHaveLength(2)
    const pair = merged.find(m => m.count === 2)!
    expect(pair.count).toBe(2)
    expect(pair.worst.status).toBe('Down')
    expect(pair.links.map(l => l.sensor_id).sort()).toEqual([1, 2])
  })

  it('不发明边：空入参 → 空结果', () => {
    expect(mergeParallelEdges([])).toEqual([])
  })
})

describe('layoutTopoNodes', () => {
  it('簇很多时各带仍有正高度，同列节点不挤到同一 y（#319 N4）', () => {
    const nodes: TopoNode[] = []
    const links: TopoLink[] = []
    for (let i = 0; i < 8; i++) {
      const id = i + 1
      nodes.push(node({ id, name: `DEV-CLUSTER-${i}`, type: 'device' }))
      nodes.push(node({ id: 100 + i, name: `peer-${i}`, type: 'peer' }))
      links.push(link({ id, source_node: id, target_node: 100 + i }))
    }
    const laid = layoutTopoNodes(nodes, links)
    const peers = laid.filter(n => (n.type || '') === 'peer')
    const ys = peers.map(n => Math.round(n.ly))
    expect(new Set(ys).size).toBeGreaterThan(4)
    for (const p of peers) {
      expect(p.ly).toBeGreaterThanOrEqual(40)
      expect(p.ly).toBeLessThanOrEqual(620 - 40)
    }
  })

  it('pinned 保留接口坐标；未 pinned 按列左右分层且同列不压叠', () => {
    const nodes: TopoNode[] = [
      node({ id: 1, name: 'OLIB-NX-3172-WAN-2.orangeliberia', type: 'device' }),
      node({ id: 2, name: 'CSQUARED peer', type: 'peer' }),
      node({ id: 3, name: 'FG-EDGE', type: 'unknown' }),
      node({
        id: 4, name: 'Pinned Peer', type: 'peer',
        x: 50, y: 100, pinned: true,
      }),
      node({ id: 5, name: 'port-channel2', type: 'unknown' }),
      node({ id: 6, name: 'port-channel11', type: 'unknown' }),
    ]
    const links: TopoLink[] = [
      link({ id: 1, source_node: 1, target_node: 2 }),
      link({ id: 2, source_node: 1, target_node: 3 }),
      link({ id: 3, source_node: 1, target_node: 5 }),
      link({ id: 4, source_node: 1, target_node: 6 }),
    ]
    const laid = layoutTopoNodes(nodes, links)
    const byId = new Map(laid.map(n => [n.id, n]))
    expect(byId.get(4)!.lx).toBe(50)
    expect(byId.get(4)!.ly).toBe(100)
    // peer 在左、device 偏右、other 更右
    expect(byId.get(2)!.lx).toBeLessThan(byId.get(1)!.lx)
    expect(byId.get(1)!.lx).toBeLessThan(byId.get(5)!.lx)
    expect(byId.get(3)!.lx).toBeLessThan(byId.get(1)!.lx)
    // 同列（other）两条接口节点纵向分开
    expect(Math.round(byId.get(5)!.ly)).not.toBe(Math.round(byId.get(6)!.ly))
  })
})

describe('trafficWidth', () => {
  it('按绝对流量分 4 档', () => {
    expect(trafficWidth(10, 10)).toBe(2)
    expect(trafficWidth(40, 20)).toBe(3)
    expect(trafficWidth(150, 100)).toBe(4)
    expect(trafficWidth(400, 200)).toBe(5)
  })
})

describe('buildPeerLabelBySensorId', () => {
  it('device→peer 取对端短名；device→unknown 取清洗后接口名', () => {
    const nodes: TopoNode[] = [
      node({ id: 1, name: 'OLIB-NX-3172-WAN-2.orangeliberia (NEXUS)', type: 'device' }),
      node({ id: 2, name: 'CSQUARED.peer.net', type: 'peer' }),
      node({
        id: 3,
        name: '(436232192) ======== OLIB-NX-3172-WAN-2-FG1500-LAN-2-P-39 ==========',
        type: 'unknown',
      }),
    ]
    const links: TopoLink[] = [
      link({ id: 1, source_node: 1, target_node: 2, sensor_id: 100 }),
      link({ id: 2, source_node: 1, target_node: 3, sensor_id: 101 }),
    ]
    const map = buildPeerLabelBySensorId(nodes, links)
    expect(map.get(100)).toBe('CSQUARED')
    expect(map.get(101)).toBe('OLIB-NX-3172-WAN-2-FG1500-LAN-2-P-39')
  })
})
