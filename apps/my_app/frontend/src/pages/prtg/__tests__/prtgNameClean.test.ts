/* ============================================================================
 * PRTG 名称清洗纯函数单测（链路健康改版 P1）
 * ============================================================================ */

import { describe, expect, it } from 'vitest'

import { cleanSensorName, shortDeviceName, NA } from '../prtgModels'

describe('cleanSensorName', () => {
  it('去掉开头 (数字)、等号连串并合并空格', () => {
    expect(cleanSensorName(
      '(436232192) ======== OLIB-NX-3172-WAN-2-FG1500-LAN-2-P-39 ==========',
    )).toBe('OLIB-NX-3172-WAN-2-FG1500-LAN-2-P-39')
  })

  it('已干净的名字保持不变', () => {
    expect(cleanSensorName('WAN-2 Port 39')).toBe('WAN-2 Port 39')
  })

  it('空 / null → —', () => {
    expect(cleanSensorName(null)).toBe(NA)
    expect(cleanSensorName('')).toBe(NA)
    expect(cleanSensorName('   ')).toBe(NA)
    expect(cleanSensorName('====')).toBe(NA)
  })
})

describe('shortDeviceName', () => {
  it('FQDN + 括号型号 + 方括号厂商 → 主机短名', () => {
    expect(shortDeviceName(
      'OLIB-NX-3172-WAN-2.orangeliberia (NEXUS 3172) [Cisco Device]',
    )).toBe('OLIB-NX-3172-WAN-2')
  })

  it('无域名时只剥括号说明', () => {
    expect(shortDeviceName('PTP_SW (Switch) [Cisco]')).toBe('PTP_SW')
  })

  it('已是短名时原样返回', () => {
    expect(shortDeviceName('AML TOKADEH1')).toBe('AML TOKADEH1')
  })

  it('空 / null → —', () => {
    expect(shortDeviceName(null)).toBe(NA)
    expect(shortDeviceName('')).toBe(NA)
  })
})
