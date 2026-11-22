/* ============================================================================
 * MW 适配器测试
 * ============================================================================
 * 这个适配器存在的**唯一理由**就是下面第二条：
 *   后端对「没有 KPI 的物理链路」返回 `tx_util: 0.0` + `kpi_backed: false`，
 *   直接搬运会把未采集链路画成「0% 正常」。必须按 `kpi_backed` 判空。
 * 另外守住：原生四级名不丢、TX/RX 不平均、checkpoint 字符串数字要转 number。
 * ========================================================================== */
import { describe, expect, it } from 'vitest'

import { adaptMw } from '../adapters/mwAdapter'
import type { MwUmeEdge, MwUmeNode, MwUmeTopologyResponse } from '../../../api'

function node(over: Partial<MwUmeNode>): MwUmeNode {
  return {
    id: '10.0.0.1', name: 'Site A', site: 'Site A', site_id: '4017', ne: '',
    ip: '10.0.0.1', lat: 6.2, lon: -10.3, degree: 1, status: 'healthy', max_util: 10,
    ...over,
  }
}

function edge(over: Partial<MwUmeEdge>): MwUmeEdge {
  return {
    source: '10.0.0.1', target: '10.0.0.2',
    capacity: 800, tx_throughput: 300, rx_throughput: 100,
    tx_util: 37.5, rx_util: 12.5, status: 'healthy', max_util: 37.5,
    checkpoints: [], history: [], kpi_backed: true,
    ...over,
  }
}

function resp(nodes: MwUmeNode[], edges: MwUmeEdge[], unknown = 0): MwUmeTopologyResponse {
  return {
    meta: {
      generated_at: '2026-11-19 17:54:05',
      granularity: '1day',
      thresholds: { minor: 60, major: 70, critical: 80 },
      selected_date: '2026-11-18',
      available_dates: ['2026-11-18'],
      counts: {},
    },
    kpis: {
      nodes: nodes.length, links: edges.filter((e) => e.kpi_backed).length,
      physical_links: edges.length, physical_nodes: nodes.length,
      healthy: 0, minor: 0, major: 0, critical: 0, unknown,
      max_utilization: 0, max_util_link: null,
    },
    nodes, edges, top_risks: [], trends: [],
  }
}

describe('MW 适配器', () => {
  it('kpi_backed=false 的链路：利用率必须是 null，不能是后端的 0.0', () => {
    const slice = adaptMw({
      resp: resp(
        [node({}), node({ id: '10.0.0.2', ip: '10.0.0.2' })],
        [edge({ kpi_backed: false, tx_util: 0, rx_util: 0, status: 'unknown' })],
        1,
      ),
      state: 'partial',
      realtime: false,
    })
    const e = slice.edges[0]
    expect(e.trafficAvailable).toBe(false)
    expect(e.txUtilization).toBeNull()
    expect(e.rxUtilization).toBeNull()
    expect(e.status).toBe('unknown')
  })

  it('kpi_backed=true 且值为 0 时，0 是真实值（保留 0，不当缺失）', () => {
    const slice = adaptMw({
      resp: resp(
        [node({}), node({ id: '10.0.0.2', ip: '10.0.0.2' })],
        [edge({ kpi_backed: true, tx_util: 0, rx_util: 0 })],
      ),
      state: 'connected',
      realtime: false,
    })
    expect(slice.edges[0].trafficAvailable).toBe(true)
    expect(slice.edges[0].txUtilization).toBe(0)
    expect(slice.edges[0].rxUtilization).toBe(0)
  })

  it('原生四级名保留：healthy→ok / minor / major / critical / unknown', () => {
    const slice = adaptMw({
      resp: resp(
        [node({}), node({ id: '10.0.0.2', ip: '10.0.0.2' })],
        [
          edge({ status: 'healthy' }),
          edge({ status: 'minor' }),
          edge({ status: 'major' }),
          edge({ status: 'critical' }),
          edge({ status: 'unknown', kpi_backed: false }),
        ],
      ),
      state: 'partial',
      realtime: false,
    })
    expect(slice.edges.map((e) => e.status)).toEqual(['ok', 'minor', 'major', 'critical', 'unknown'])
    expect(slice.edges.map((e) => e.nativeStatus)).toEqual(['healthy', 'minor', 'major', 'critical', 'unknown'])
  })

  it('TX / RX 分别搬运，不平均；带宽原值即 Mbps', () => {
    const slice = adaptMw({
      resp: resp(
        [node({}), node({ id: '10.0.0.2', ip: '10.0.0.2' })],
        [edge({ tx_util: 99.9, rx_util: 3.2, capacity: 790.92 })],
      ),
      state: 'connected',
      realtime: false,
    })
    expect(slice.edges[0].txUtilization).toBe(99.9)
    expect(slice.edges[0].rxUtilization).toBe(3.2)
    expect(slice.edges[0].capacityMbps).toBe(790.92)
  })

  it('checkpoint / history 的字符串数字转 number，空值转 null', () => {
    const slice = adaptMw({
      resp: resp(
        [node({}), node({ id: '10.0.0.2', ip: '10.0.0.2' })],
        [edge({
          checkpoints: [{ cp: 'p1', ncp: 'p2', tx_t: '368.6', tx_u: '99.93', rx_t: '', rx_u: null }],
          history: [{ date: '2026-11-18', tx_util: 99.93, rx_util: 33.62, max_util: 99.93 }],
        })],
      ),
      state: 'connected',
      realtime: false,
    })
    const c = slice.edges[0].checkpoints?.[0]
    expect(c?.txThroughput).toBe(368.6)
    expect(c?.txUtilization).toBe(99.93)
    expect(c?.rxThroughput).toBeNull()
    expect(c?.rxUtilization).toBeNull()
    expect(slice.edges[0].history?.[0].maxUtilization).toBe(99.93)
  })

  it('覆盖率 = 有 KPI 链路 / 物理链路', () => {
    const slice = adaptMw({
      resp: resp(
        [node({}), node({ id: '10.0.0.2', ip: '10.0.0.2' })],
        [edge({}), edge({ kpi_backed: false, status: 'unknown' }), edge({}), edge({})],
        1,
      ),
      state: 'partial',
      realtime: false,
    })
    expect(slice.meta.coverage).toBe(0.75)
    expect(slice.meta.sampledAt).toBe('2026-11-18')
    expect(slice.meta.granularity).toBe('1day')
  })
})
