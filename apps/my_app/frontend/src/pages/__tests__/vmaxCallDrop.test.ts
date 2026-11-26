/**
 * 电路域打断数据通话：用 2026-11-26 这单投诉的信令形状锁住配对。
 * 离开时长必须是「释放 → 下一次 TAU」，不是信令过程自己的几毫秒。
 */
import { describe, expect, it } from 'vitest'

import { analyzeCallDrop } from '../vmaxCallDrop'
import type { SourceResult, XdrRow } from '../vmaxDiagnosisRules'

const src = (key: string, rows: XdrRow[], extra: Partial<SourceResult> = {}): SourceResult => ({
  key, label: key, ok: true, rows, truncated: false, ...extra,
})

const sig = (
  time: string, proc: string, cause: string, cell: string,
): XdrRow => ({
  start_time: time,
  procedure_type: proc,
  request_cause: cause,
  procedure_status: 'Success',
  cellname: cell,
  interface: 'S1-MME',
  rat_name: '(4G)EUTRAN',
})

function fixture(): SourceResult[] {
  return [
    src('1300', [
      { start_time: '2026-11-26 07:45:03', rat_name: '(4G)EUTRAN', qci: '9', app_content_name: 'UDP', subservice: 'UDP', service: 'Public Traffic' },
      { start_time: '2026-11-26 08:40:20', rat_name: '(4G)EUTRAN', qci: '9', app_content_name: 'UDP', subservice: 'UDP', service: 'Public Traffic' },
    ]),
    src('1100', [
      sig('2026-11-26 07:33:41', 'UE context release', 'Interrat Redirection', 'Qcell Orange-L0'),
      sig('2026-11-26 07:33:58', 'UE context release', 'Interrat Redirection', 'Qcell Orange-S0'),
      sig('2026-11-26 07:34:00', 'UE context release', 'Interrat Redirection', 'Qcell Orange-L0'),
      sig('2026-11-26 07:48:26', 'CS Service Notification', 'Unknown', 'Qcell Orange-L0'),
      sig('2026-11-26 07:48:26', 'Extended Service Request', '255', 'Qcell Orange-L0'),
      sig('2026-11-26 07:48:26', 'UE context release', 'CS Fallback Triggered', 'Qcell Orange-L0'),
      sig('2026-11-26 07:49:01', 'TAU', '255', 'Qcell Orange-L0'),
      sig('2026-11-26 07:49:02', 'EPS bearer context modification', 'Protocol Error Unspecified', 'Qcell Orange-L0'),
      sig('2026-11-26 08:44:26', 'Extended Service Request', '255', 'Orange-L1'),
      sig('2026-11-26 08:44:26', 'UE context release', 'CS Fallback Triggered', 'Orange-L1'),
      sig('2026-11-26 08:44:54', 'TAU', '255', 'Orange-L1'),
      sig('2026-11-26 08:44:55', 'E-RAB Modify', 'Unknown', 'Orange-L1'),
      sig('2026-11-26 08:44:55', 'EPS bearer context modification', 'Protocol Error Unspecified', 'Orange-L1'),
      sig('2026-11-26 09:18:18', 'CS Service Notification', 'Unknown', 'Qcell Orange-S2'),
      sig('2026-11-26 09:18:18', 'Extended Service Request', '255', 'Qcell Orange-S2'),
      sig('2026-11-26 09:18:18', 'UE context release', 'CS Fallback Triggered', 'Qcell Orange-S2'),
      sig('2026-11-26 09:19:06', 'TAU', '255', 'Qcell Orange-L2'),
      sig('2026-11-26 09:19:06', 'EPS bearer context modification', 'Protocol Error Unspecified', 'Qcell Orange-L2'),
    ]),
    src('32', [
      { btime: '2026-11-26 09:01:34', serving_rsrp: -97, serving_rsrq: -20, ulsinr: 255, cellname: 'Orange-L1' },
    ]),
    src('1301', []),
  ]
}

describe('analyzeCallDrop', () => {
  it('把三次 CS Fallback 配成离开与回来，时长是释放到 TAU', () => {
    const report = analyzeCallDrop(fixture(), {
      cells: [
        { rat: 6, cellname: 'Orange-L1' },
        { rat: 1, cellname: 'Orange-S7' },
      ],
      causes: [{
        rule_name: 'Average Delay of The Maximum Uplink UDP Packet Interval(OTT Voice Call) in the cell is large.',
        object_name: 'Orange-S1',
        poortime: '2026-11-26 08:45',
        description: 'Poor Quality Period:2026-11-26 08:45, interval 18590ms',
      }],
    })

    expect(report.signalOk).toBe(true)
    expect(report.cycles.map((c) => c.awaySec)).toEqual([35, 28, 48])
    expect(report.cycles.map((c) => c.kind)).toEqual(['mt', 'mo', 'mt'])
    expect(report.cycles[0]).toMatchObject({
      leaveAt: '2026-11-26 07:48:26',
      leaveCell: 'Qcell Orange-L0',
      returnAt: '2026-11-26 07:49:01',
      returnCell: 'Qcell Orange-L0',
      bearerCause: 'Protocol Error Unspecified',
    })
    expect(report.cycles[2].returnCell).toBe('Qcell Orange-L2')
    expect(report.headline).toMatchObject({
      key: 'vmaxq.vd.calldrop.headline',
      params: { n: 3, min: 28, max: 48, cause: 'Protocol Error Unspecified' },
    })
    const keys = report.notes.map((n) => n.key)
    expect(keys).toContain('vmaxq.vd.calldrop.noQci')
    expect(keys).toContain('vmaxq.vd.calldrop.rat')
    expect(keys).toContain('vmaxq.vd.calldrop.dpi')
    expect(keys).toContain('vmaxq.vd.calldrop.otherRat')
    expect(keys).toContain('vmaxq.vd.calldrop.redirect')
    expect(keys).toContain('vmaxq.vd.calldrop.radio')
    expect(keys).toContain('vmaxq.vd.calldrop.ottCell')
    const redirect = report.notes.find((n) => n.key === 'vmaxq.vd.calldrop.redirect')
    expect(redirect?.params).toMatchObject({ n: 3 })
  })

  it('没有 CS Fallback 时明确写没有，而不是空白', () => {
    const report = analyzeCallDrop([
      src('1100', [sig('2026-11-26 08:00:00', 'Service Request', '255', 'Orange-L1')]),
      src('1300', []), src('32', []), src('1301', []),
    ])
    expect(report.cycles).toHaveLength(0)
    expect(report.hasActivity).toBe(false)
    expect(report.headline.key).toBe('vmaxq.vd.calldrop.none')
  })

  it('信令源失败时不把空结果说成没有回落', () => {
    const report = analyzeCallDrop([
      src('1100', [], { ok: false, error: 'upstream down' }),
      src('1300', []), src('32', []), src('1301', []),
    ])
    expect(report.signalOk).toBe(false)
    expect(report.headline.key).toBe('vmaxq.vd.calldrop.noSignal')
  })
})
