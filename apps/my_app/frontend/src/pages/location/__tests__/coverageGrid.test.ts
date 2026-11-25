/* ============================================================================
 * 覆盖网格 NGC1 解包 / 空间索引 / 瓦片绘制（vitest，纯函数）
 * ============================================================================
 * 守住三件「错了不报错、只是图画歪」的事：
 *   1. 字节偏移与后端 pack_grid 一致（tests/test_ngi_coverage_api.py 守另一半）；
 *   2. 瓦片取点不漏边：格点中心在瓦片外、但方块压进瓦片的那一圈也要画；
 *   3. 配色不假设「档位 1 = 好」：按每档 RSRP 均值排序。
 * ========================================================================== */
import { describe, expect, it } from 'vitest'

import {
  COVERAGE_NA_COLOR, RSRP_PALETTE, binColors, bboxToBounds, buildIndex, collectTile, drawTile,
  mercX, mercY, nearestCell, parseGridBlob,
} from '../coverageGrid'

function pack(rows: [number, number, number][]): ArrayBuffer {
  const n = rows.length
  const buf = new ArrayBuffer(8 + 9 * n)
  const u8 = new Uint8Array(buf)
  u8.set([78, 71, 67, 49]) // "NGC1"
  new DataView(buf).setUint32(4, n, true)
  const lon = new Float32Array(buf, 8, n)
  const lat = new Float32Array(buf, 8 + 4 * n, n)
  const bin = new Uint8Array(buf, 8 + 8 * n, n)
  rows.forEach(([x, y, b], i) => { lon[i] = x; lat[i] = y; bin[i] = b })
  return buf
}

/** 某层级下经纬度所在瓦片号 */
function tileOf(lat: number, lon: number, z: number) {
  const s = 2 ** z
  return { x: Math.floor(mercX(lon) * s), y: Math.floor(mercY(lat) * s) }
}

describe('parseGridBlob', () => {
  it('按 NGC1 偏移解出经纬度与档位', () => {
    const g = parseGridBlob(pack([[-10.8, 6.3, 1], [-10.79955, 6.30045, 7]]))
    expect(g.count).toBe(2)
    expect(g.lon[1]).toBeCloseTo(-10.79955, 5)
    expect(g.lat[0]).toBeCloseTo(6.3, 5)
    expect(Array.from(g.bin)).toEqual([1, 7])
  })

  it('魔数不对 / 截断时明确报错，而不是画一片乱点', () => {
    const bad = pack([[-10.8, 6.3, 1]])
    new Uint8Array(bad)[0] = 0
    expect(() => parseGridBlob(bad)).toThrow(/magic/)
    expect(() => parseGridBlob(pack([[-10.8, 6.3, 1]]).slice(0, 12))).toThrow(/truncated/)
    expect(() => parseGridBlob(new ArrayBuffer(3))).toThrow(/short/)
  })

  it('空网格合法', () => {
    expect(parseGridBlob(pack([])).count).toBe(0)
  })
})

describe('索引与取点', () => {
  // 以 Monrovia 附近为中心铺 20×20 个 50 m 格点
  const step = 50 / 111320
  const rows: [number, number, number][] = []
  for (let i = 0; i < 20; i++) for (let j = 0; j < 20; j++) rows.push([-10.8 + i * step, 6.3 + j * step, (i % 7) + 1])
  const index = buildIndex(parseGridBlob(pack(rows)))

  it('每个格点恰好进一个桶', () => {
    let total = 0
    index.buckets.forEach((ids) => { total += ids.length })
    expect(total).toBe(rows.length)
  })

  it('z14 下包含全部格点的瓦片能取到它们（含外扩边）', () => {
    const z = 14
    const seen = new Set<number>()
    const t0 = tileOf(6.3, -10.8, z)
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
      for (const i of collectTile(index, z, t0.x + dx, t0.y + dy, 256, 4)) seen.add(i)
    }
    expect(seen.size).toBe(rows.length)
  })

  it('点击格点中心附近命中该格，离得远返回 -1', () => {
    const k = 7 * 20 + 3
    const [lon, lat] = rows[k]
    expect(nearestCell(index, lat + step * 0.2, lon - step * 0.2, 50)).toBe(k)
    expect(nearestCell(index, 7.5, -9.0, 50)).toBe(-1)
  })

  it('drawTile 每个格点画一次、并按档位分组设置颜色', () => {
    const z = 16
    const t0 = tileOf(6.3 + 10 * step, -10.8 + 10 * step, z)
    const calls: { style: string; w: number }[] = []
    const ctx = {
      fillStyle: '' as string | CanvasGradient | CanvasPattern,
      fillRect(_x: number, _y: number, w: number) { calls.push({ style: String(this.fillStyle), w }) },
    }
    const colors = { 1: '#111111', 2: '#222222' }
    const n = drawTile(ctx, index, colors, z, t0.x, t0.y, 256, 50)
    expect(n).toBeGreaterThan(0)
    expect(calls.length).toBe(n)
    // z16、纬度 6° 时 50 m ≈ 21 px，加 0.4 px 防缝
    expect(calls[0].w).toBeGreaterThan(20)
    expect(calls.some((c) => c.style === COVERAGE_NA_COLOR)).toBe(true) // 档位 3~7 没给颜色
  })
})

describe('binColors', () => {
  it('RSRP 均值最高的档位拿最好的颜色，与档位编号无关', () => {
    const c = binColors([
      { bin: 1, rows: 10, rsrp_min: -130, rsrp_max: -120, rsrp_avg: -125 },
      { bin: 2, rows: 10, rsrp_min: -80, rsrp_max: -60, rsrp_avg: -70 },
      { bin: 3, rows: 10, rsrp_min: -100, rsrp_max: -90, rsrp_avg: -95 },
      { bin: 0, rows: 3, rsrp_min: null, rsrp_max: null, rsrp_avg: null },
    ])
    expect(c[2]).toBe(RSRP_PALETTE[0])
    expect(c[1]).toBe(RSRP_PALETTE[RSRP_PALETTE.length - 1])
    expect(c[0]).toBe(COVERAGE_NA_COLOR)
  })
})

describe('bboxToBounds', () => {
  it('[minLon,minLat,maxLon,maxLat] → [[lat,lon],[lat,lon]]，缺值返回 null', () => {
    expect(bboxToBounds([-10.9, 6.2, -10.7, 6.4])).toEqual([[6.2, -10.9], [6.4, -10.7]])
    expect(bboxToBounds([null, 6.2, -10.7, 6.4])).toBeNull()
    expect(bboxToBounds(undefined)).toBeNull()
  })
})
