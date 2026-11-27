/* ============================================================================
 * 「需要关注」可用率偏低阈值 = SLA 默认目标 PRTG_SLA_TARGET_PCT（99.5）
 * 出处：backend/services/prtg/sla.py::DEFAULT_TARGET
 *
 * 样例（真实链路口径）：
 *   AML TOKADEH 88.07 → 偏低（要标）
 *   PTP_SW      99.877 → 不低于 99.5，不标
 *   NX          99.997 → 不标
 * 说明：指令示例曾按 99.9 兜底期望标出 99.877；仓库实际 SLA 默认是 99.5，
 * 故前端与 SLA 对齐后 99.877 不进入「偏低」。
 * ========================================================================== */

import { describe, expect, it } from 'vitest'

import {
  PRTG_SLA_TARGET_PCT, UPTIME_LOW_THRESHOLD, isUptimeBelowTarget, pickAttentionItems,
  type PrtgLink,
} from '../prtgModels'

describe('UPTIME_LOW_THRESHOLD 与 SLA 目标一致', () => {
  it('常量 = 99.5（sla.py DEFAULT_TARGET）', () => {
    expect(PRTG_SLA_TARGET_PCT).toBe(99.5)
    expect(UPTIME_LOW_THRESHOLD).toBe(99.5)
  })
})

describe('isUptimeBelowTarget', () => {
  it('AML TOKADEH 88.07 要标出来', () => {
    expect(isUptimeBelowTarget(88.07)).toBe(true)
  })

  it('PTP_SW 99.877 不低于 SLA 99.5，不标', () => {
    expect(isUptimeBelowTarget(99.877)).toBe(false)
  })

  it('NX 99.997 不标', () => {
    expect(isUptimeBelowTarget(99.997)).toBe(false)
  })

  it('边界：等于目标不标；null 不标', () => {
    expect(isUptimeBelowTarget(99.5)).toBe(false)
    expect(isUptimeBelowTarget(99.499)).toBe(true)
    expect(isUptimeBelowTarget(null)).toBe(false)
    expect(isUptimeBelowTarget(undefined)).toBe(false)
  })
})

describe('pickAttentionItems 对 uptime 偏低', () => {
  function link(partial: Partial<PrtgLink> & Pick<PrtgLink, 'sensor_id'>): PrtgLink {
    return {
      sensor_name: `s-${partial.sensor_id}`,
      status: 'Up',
      ...partial,
    }
  }

  it('只把低于 99.5 的 Up 链路选进关注列表', () => {
    const items = pickAttentionItems([
      link({ sensor_id: 1, sensor_name: 'AML TOKADEH', uptime_pct: 88.07 }),
      link({ sensor_id: 2, sensor_name: 'PTP_SW', uptime_pct: 99.877 }),
      link({ sensor_id: 3, sensor_name: 'NX', uptime_pct: 99.997 }),
    ], 8)
    expect(items.map(i => i.link.sensor_id)).toEqual([1])
    expect(items[0].kind).toBe('low_uptime')
  })
})
