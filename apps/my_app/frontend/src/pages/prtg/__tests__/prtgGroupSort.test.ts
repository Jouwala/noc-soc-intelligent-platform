/* ============================================================================
 * #319 B1：分组表组内受控排序
 * ========================================================================== */

import { describe, expect, it } from 'vitest'

import type { PrtgLink } from '../prtgModels'
import {
  buildGroupedDataSource, compareNullableNumber, linkRowsUnderGroups,
  sortLinksWithinGroup,
} from '../prtgGroupSort'

function link(partial: Partial<PrtgLink> & Pick<PrtgLink, 'sensor_id' | 'sensor_name'>): PrtgLink {
  return {
    status: 'Up',
    device_name: 'dev',
    ...partial,
  }
}

describe('compareNullableNumber', () => {
  it('null 升序/降序都排在最后', () => {
    expect(compareNullableNumber(null, 1, 'ascend')).toBeGreaterThan(0)
    expect(compareNullableNumber(1, null, 'ascend')).toBeLessThan(0)
    expect(compareNullableNumber(null, 1, 'descend')).toBeGreaterThan(0)
    expect(compareNullableNumber(1, null, 'descend')).toBeLessThan(0)
    expect(compareNullableNumber(null, null, 'ascend')).toBe(0)
  })

  it('数值升序/降序', () => {
    expect(compareNullableNumber(10, 20, 'ascend')).toBeLessThan(0)
    expect(compareNullableNumber(10, 20, 'descend')).toBeGreaterThan(0)
  })
})

describe('sortLinksWithinGroup / buildGroupedDataSource', () => {
  it('复现数据：In 升序后 b1 仍在 G_B 下，组内 a1→a2', () => {
    // [G_A, a1(in=10), a2(in=20), G_B, b1(in=5)]
    const groups = [
      {
        key: 'G_A',
        raw: 'G_A',
        links: [
          link({ sensor_id: 1, sensor_name: 'a1', device_name: 'G_A', in_mbps: 10 }),
          link({ sensor_id: 2, sensor_name: 'a2', device_name: 'G_A', in_mbps: 20 }),
        ],
      },
      {
        key: 'G_B',
        raw: 'G_B',
        links: [
          link({ sensor_id: 3, sensor_name: 'b1', device_name: 'G_B', in_mbps: 5 }),
        ],
      },
    ]
    const rows = buildGroupedDataSource(
      groups,
      {},
      { key: 'inOut', order: 'ascend' },
      s => s,
    )
    const under = linkRowsUnderGroups(rows)
    expect(under.map(g => g.deviceKey)).toEqual(['G_A', 'G_B'])
    expect(under[0].sensorIds).toEqual([1, 2]) // a1(10) then a2(20)
    expect(under[1].sensorIds).toEqual([3]) // b1 stays under G_B
    // 组头计数与组内行一致
    const gA = rows.find(r => r.rowType === 'group' && r.deviceKey === 'G_A')
    expect(gA && gA.rowType === 'group' && gA.linkCount).toBe(2)
    expect(gA && gA.rowType === 'group' && gA.up).toBe(2)
  })

  it('In 升序时 null 排在组内最后', () => {
    const sorted = sortLinksWithinGroup(
      [
        link({ sensor_id: 1, sensor_name: 'n', in_mbps: null }),
        link({ sensor_id: 2, sensor_name: 'lo', in_mbps: 5 }),
        link({ sensor_id: 3, sensor_name: 'hi', in_mbps: 50 }),
      ],
      { key: 'inOut', order: 'ascend' },
    )
    expect(sorted.map(l => l.sensor_id)).toEqual([2, 3, 1])
  })

  it('In 降序时 null 仍排在组内最后', () => {
    const sorted = sortLinksWithinGroup(
      [
        link({ sensor_id: 1, sensor_name: 'n', in_mbps: null }),
        link({ sensor_id: 2, sensor_name: 'lo', in_mbps: 5 }),
        link({ sensor_id: 3, sensor_name: 'hi', in_mbps: 50 }),
      ],
      { key: 'inOut', order: 'descend' },
    )
    expect(sorted.map(l => l.sensor_id)).toEqual([3, 2, 1])
  })

  it('组顺序不随列排序改变', () => {
    const groups = [
      { key: 'A', raw: 'A', links: [link({ sensor_id: 1, sensor_name: 'a', in_mbps: 100 })] },
      { key: 'B', raw: 'B', links: [link({ sensor_id: 2, sensor_name: 'b', in_mbps: 1 })] },
    ]
    const rows = buildGroupedDataSource(
      groups, {}, { key: 'inOut', order: 'ascend' }, s => s,
    )
    expect(rows.filter(r => r.rowType === 'group').map(r => r.deviceKey)).toEqual(['A', 'B'])
  })
})
