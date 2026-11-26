/* ============================================================================
 * LTE 小区扇区图层（vitest，纯函数）
 * ============================================================================
 * 守住几件「错了不报错、只是图画歪」的事：
 *   1. 方位角方向：0° 朝北、90° 朝东，Canvas 角度换算不能差 90°；
 *   2. 扇面张角 65°：方位角 ±32.5° 内命中，之外不命中；
 *   3. 半径按站间距自适应：相邻两站在任何层级下扇面都不相交；共站址不算邻站；
 *   4. 频点越低半径越大；重叠时点选取最上层（半径最小）的那个；
 *   5. 缺 cell 坐标退回站点坐标，两者都缺的小区不画；
 *   6. 被取消勾选的频点既不画也点不中。
 * ========================================================================== */
import { describe, expect, it } from 'vitest'

import { mercX, mercY, metersPerPixel } from '../coverageGrid'
import {
  MAX_PX, MAX_SITE_R_M, MIN_SITE_R_M, NR_PALETTE, NR_SCALE, SECTOR_DEG, SITE_R_RATIO, angleDiff,
  assignSiteRadii, bandLabel, buildCellIndex, buildFreqStyles, canvasAngle, drawCellsTile, freqKeyOf,
  hitCell, minPxAt, parseCellPayload, sameSectorCells, sectorRadiusPx, sitePoints, type CellPayload,
} from '../lteCells'

const COLS = ['cell_uid', 'enodeb_id', 'enodeb_name', 'cell_id', 'cell_name', 'sector_id', 'pci', 'tac',
  'dl_center_freq', 'dl_earfcn', 'band_indicator', 'bandclass', 'dl_system_bw', 'azimuth', 'height_m',
  'mech_downtilt', 'elec_downtilt', 'cell_lat', 'cell_lon', 'site_lat', 'site_lon', 'cover_type', 'active', 'vendor']

function row(o: Record<string, unknown>): unknown[] {
  return COLS.map((c) => (c in o ? o[c] : null))
}

const LAT = 6.3
const LON = -10.8
/** 同纬度向东 m 米后的经度（与 assignSiteRadii 用同一近似） */
const east = (m: number) => LON + m / (111320 * Math.cos((LAT * Math.PI) / 180))

function payload(rows: Record<string, unknown>[]): CellPayload {
  return { available: true, columns: COLS, rows: rows.map(row), count: rows.length }
}

/** 从 (LAT,LON) 出发，沿方位角 az 走 px 个像素（z 层级）后的经纬度 */
function offset(az: number, px: number, z: number): [number, number] {
  const scale = 256 * 2 ** z
  const x = mercX(LON) * scale + px * Math.sin((az * Math.PI) / 180)
  const y = mercY(LAT) * scale - px * Math.cos((az * Math.PI) / 180)
  const lon = (x / scale) * 360 - 180
  const lat = (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / scale))) * 180) / Math.PI
  return [lat, lon]
}

describe('parseCellPayload', () => {
  it('按列名取值，缺 cell 坐标时退回站点坐标，都缺则丢弃', () => {
    const cells = parseCellPayload(payload([
      { cell_uid: 'a', enodeb_id: '1', cell_lat: LAT, cell_lon: LON, azimuth: 30, dl_center_freq: 1815 },
      { cell_uid: 'b', enodeb_id: '2', site_lat: 6.31, site_lon: -10.81, azimuth: 400, dl_center_freq: '1815.0' },
      { cell_uid: 'c', azimuth: 0 },
      { cell_uid: 'd', cell_lat: 0, cell_lon: 0, site_lat: 6.2, site_lon: -10.7 },
    ]))
    expect(cells.map((c) => c.uid)).toEqual(['a', 'b', 'd'])
    expect(cells[1].lat).toBeCloseTo(6.31)
    expect(cells[1].az).toBe(40) // 400° 归一化
    expect(cells[0].freqKey).toBe(cells[1].freqKey) // 1815 与 '1815.0' 同一频点
    expect(cells[2].omni).toBe(true) // 无方位角 → 全向
    expect(cells[0].site).toBe('e:1')
    expect(cells[2].site.startsWith('p:')).toBe(true) // 没有 eNodeB ID 用坐标当站点键
  })

  it('室分小区画成圆', () => {
    const [c] = parseCellPayload(payload([{ cell_uid: 'x', cell_lat: LAT, cell_lon: LON, azimuth: 90, cover_type: 'Indoor' }]))
    expect(c.omni).toBe(true)
  })
})

describe('buildFreqStyles', () => {
  const cells = parseCellPayload(payload([
    { cell_uid: '1', cell_lat: LAT, cell_lon: LON, azimuth: 0, dl_center_freq: 2655, band_indicator: '7' },
    { cell_uid: '2', cell_lat: LAT, cell_lon: LON, azimuth: 0, dl_center_freq: 806, band_indicator: '20' },
    { cell_uid: '3', cell_lat: LAT, cell_lon: LON, azimuth: 0, dl_center_freq: 1815, band_indicator: '3' },
    { cell_uid: '4', cell_lat: LAT, cell_lon: LON, azimuth: 0, dl_center_freq: 1815, band_indicator: '3' },
    { cell_uid: '5', cell_lat: LAT, cell_lon: LON, azimuth: 0 },
  ]))
  const styles = buildFreqStyles(cells)

  it('频点从低到高排序，低频比例大、颜色互不相同，缺频点的排最后', () => {
    expect(styles.map((s) => s.key)).toEqual(['806', '1815', '2655', 'unknown'])
    expect(styles[0].factor).toBe(1)
    expect(styles[0].factor).toBeGreaterThan(styles[1].factor)
    expect(styles[1].factor).toBeGreaterThan(styles[2].factor)
    expect(styles[2].factor).toBe(0.5)
    expect(new Set(styles.slice(0, 3).map((s) => s.color)).size).toBe(3)
    expect(styles[1].count).toBe(2)
    expect(styles[1].band).toBe('3')
  })
})

describe('站间距自适应半径', () => {
  const cells = parseCellPayload(payload([
    { cell_uid: 'A', enodeb_id: 'A', cell_lat: LAT, cell_lon: LON, azimuth: 90, dl_center_freq: 806 },
    { cell_uid: 'B', enodeb_id: 'B', cell_lat: LAT, cell_lon: east(500), azimuth: 270, dl_center_freq: 806 },
    { cell_uid: 'D', enodeb_id: 'D', cell_lat: LAT, cell_lon: east(10), azimuth: 0, dl_center_freq: 806 }, // 与 A 共站址
    { cell_uid: 'C', enodeb_id: 'C', cell_lat: LAT, cell_lon: east(20000), azimuth: 0, dl_center_freq: 806 }, // 孤站
    { cell_uid: 'E', enodeb_id: 'E', cell_lat: LAT, cell_lon: east(30000), azimuth: 0, dl_center_freq: 806 },
    { cell_uid: 'F', enodeb_id: 'F', cell_lat: LAT, cell_lon: east(30050), azimuth: 0, dl_center_freq: 806 }, // 与 E 很近
  ]))
  const by = (uid: string) => cells.find((c) => c.uid === uid)!
  const style = buildFreqStyles(cells)[0]

  it('本站半径 = 最近邻距离 × 比例；共站址不算邻站；孤站取上限、极密取下限', () => {
    expect(by('A').siteR).toBeCloseTo(500 * SITE_R_RATIO, 1)
    expect(by('B').siteR).toBeCloseTo(490 * SITE_R_RATIO, 1) // B 最近的是 D（490 m）
    expect(by('D').siteR).toBeCloseTo(490 * SITE_R_RATIO, 1) // A 只有 10 m，视为同铁塔
    expect(by('C').siteR).toBe(MAX_SITE_R_M)
    expect(by('E').siteR).toBe(MIN_SITE_R_M) // 50 m × 0.45 = 22.5 → 下限 50
  })

  it('半径随缩放与站间距同比例变化，相邻两站扇面在各层级都不相交', () => {
    const a = by('A')
    const b = by('B')
    for (let z = 13; z <= 18; z++) {
      const gapPx = 500 / metersPerPixel(LAT, z)
      expect(sectorRadiusPx(style, a, z) + sectorRadiusPx(style, b, z)).toBeLessThan(gapPx)
    }
    const r15 = sectorRadiusPx(style, a, 15)
    expect(sectorRadiusPx(style, a, 16)).toBeCloseTo(r15 * 2, 1)
  })

  it('像素夹在 [minPx, MAX_PX] 之间，高频按比例缩小', () => {
    const c = by('C')
    expect(sectorRadiusPx(style, c, 19)).toBe(MAX_PX)
    expect(sectorRadiusPx(style, by('E'), 8)).toBe(minPxAt(8))
    const high = { ...style, factor: 0.5 }
    expect(sectorRadiusPx(high, c, 19)).toBe(MAX_PX * 0.5)
  })

  it('全国视图（z8~11）下扇面不小于看得见的尺寸，高频也保留 80%', () => {
    for (const z of [7, 8, 9, 10, 11]) expect(minPxAt(z)).toBeGreaterThanOrEqual(7)
    const high = { ...style, factor: 0.5 }
    expect(sectorRadiusPx(high, by('E'), 8)).toBeCloseTo(minPxAt(8) * 0.8, 5)
    // 放大后按站间距展开，不再被下限托住（z15 时 A 站约 47 px，远大于下限）
    expect(sectorRadiusPx(style, by('A'), 15)).toBeGreaterThan(minPxAt(15) * 3)
  })
})

describe('方位角与 65° 扇面', () => {
  it('Canvas 角度：0° 朝北 = -90°，90° 朝东 = 0', () => {
    expect(canvasAngle(0)).toBeCloseTo(-Math.PI / 2)
    expect(canvasAngle(90)).toBeCloseTo(0)
    expect(SECTOR_DEG).toBe(65)
  })

  it('angleDiff 跨 0° 正确', () => {
    expect(angleDiff(350, 10)).toBe(20)
    expect(angleDiff(10, 350)).toBe(20)
    expect(angleDiff(180, 0)).toBe(180)
  })

  it('±32.5° 内命中、之外不命中；超出半径不命中', () => {
    const z = 15
    const cells = parseCellPayload(payload([{ cell_uid: 'n', cell_lat: LAT, cell_lon: LON, azimuth: 90, dl_center_freq: 1815 }]))
    const styles = buildFreqStyles(cells)
    const map = new Map(styles.map((s) => [s.key, s]))
    const ix = buildCellIndex(cells)
    const r = sectorRadiusPx(styles[0], cells[0], z)
    expect(hitCell(ix, map, null, ...offset(90, r * 0.6, z), z)).toBe(0)
    expect(hitCell(ix, map, null, ...offset(90 + 30, r * 0.6, z), z)).toBe(0)
    expect(hitCell(ix, map, null, ...offset(90 + 36, r * 0.6, z), z)).toBe(-1)
    expect(hitCell(ix, map, null, ...offset(270, r * 0.6, z), z)).toBe(-1)
    expect(hitCell(ix, map, null, ...offset(90, r * 1.3, z), z)).toBe(-1)
  })

  it('同站两个频点重叠时，点选取半径小（画在上面）的那个；取消勾选后点到下面那个', () => {
    const z = 15
    const cells = parseCellPayload(payload([
      { cell_uid: 'low', cell_lat: LAT, cell_lon: LON, azimuth: 0, dl_center_freq: 806 },
      { cell_uid: 'high', cell_lat: LAT, cell_lon: LON, azimuth: 0, dl_center_freq: 2655 },
    ]))
    const styles = buildFreqStyles(cells)
    const map = new Map(styles.map((s) => [s.key, s]))
    const ix = buildCellIndex(cells)
    const rHigh = sectorRadiusPx(map.get(freqKeyOf(2655))!, cells[1], z)
    const rLow = sectorRadiusPx(map.get(freqKeyOf(806))!, cells[0], z)
    expect(rLow).toBeGreaterThan(rHigh)
    const near = offset(0, rHigh * 0.5, z)
    expect(cells[hitCell(ix, map, null, ...near, z)].uid).toBe('high')
    const between = offset(0, (rHigh + rLow) / 2, z)
    expect(cells[hitCell(ix, map, null, ...between, z)].uid).toBe('low')
    expect(cells[hitCell(ix, map, new Set([freqKeyOf(806)]), ...near, z)].uid).toBe('low')
  })
})

describe('sameSectorCells（悬浮提示）', () => {
  it('同站同扇区的载波按频点升序，不含其它扇区', () => {
    const cells = parseCellPayload(payload([
      { cell_uid: 's1-2655', enodeb_id: '9', sector_id: 1, cell_lat: LAT, cell_lon: LON, azimuth: 0, dl_center_freq: 2655 },
      { cell_uid: 's2-806', enodeb_id: '9', sector_id: 2, cell_lat: LAT, cell_lon: LON, azimuth: 120, dl_center_freq: 806 },
      { cell_uid: 's1-806', enodeb_id: '9', sector_id: 1, cell_lat: LAT, cell_lon: LON, azimuth: 0, dl_center_freq: 806 },
      { cell_uid: 'other', enodeb_id: '10', sector_id: 1, cell_lat: LAT, cell_lon: east(800), azimuth: 0, dl_center_freq: 806 },
    ]))
    const ix = buildCellIndex(cells)
    expect(sameSectorCells(ix, 0).map((i) => cells[i].uid)).toEqual(['s1-806', 's1-2655'])
  })

  it('没有扇区号时按方位角（±3°）分组', () => {
    const cells = parseCellPayload(payload([
      { cell_uid: 'a', enodeb_id: '9', cell_lat: LAT, cell_lon: LON, azimuth: 118, dl_center_freq: 1815 },
      { cell_uid: 'b', enodeb_id: '9', cell_lat: LAT, cell_lon: LON, azimuth: 120, dl_center_freq: 806 },
      { cell_uid: 'c', enodeb_id: '9', cell_lat: LAT, cell_lon: LON, azimuth: 240, dl_center_freq: 806 },
    ]))
    const ix = buildCellIndex(cells)
    expect(sameSectorCells(ix, 0).map((i) => cells[i].uid)).toEqual(['b', 'a'])
  })
})

describe('drawCellsTile', () => {
  function fakeCtx() {
    const order: string[] = []
    const ctx = {
      fillStyle: '' as string | CanvasGradient | CanvasPattern,
      strokeStyle: '' as string | CanvasGradient | CanvasPattern,
      lineWidth: 1,
      globalAlpha: 1,
      beginPath() {}, moveTo() {}, arc() {}, closePath() {}, stroke() {},
      fill() { order.push(String(this.fillStyle)) },
    }
    return { ctx, order }
  }

  it('先画低频大扇面、后画高频小扇面；隐藏的频点不画', () => {
    const z = 14
    const cells = parseCellPayload(payload([
      { cell_uid: 'high', cell_lat: LAT, cell_lon: LON, azimuth: 0, dl_center_freq: 2655 },
      { cell_uid: 'low', cell_lat: LAT, cell_lon: LON, azimuth: 0, dl_center_freq: 806 },
    ]))
    const styles = buildFreqStyles(cells)
    const map = new Map(styles.map((s) => [s.key, s]))
    const ix = buildCellIndex(cells)
    const tx = Math.floor(mercX(LON) * 2 ** z)
    const ty = Math.floor(mercY(LAT) * 2 ** z)
    const a = fakeCtx()
    expect(drawCellsTile(a.ctx, ix, map, null, z, tx, ty, 256)).toBe(2)
    expect(a.order).toEqual([map.get('806')!.color, map.get('2655')!.color])
    const b = fakeCtx()
    expect(drawCellsTile(b.ctx, ix, map, new Set(['2655']), z, tx, ty, 256)).toBe(1)
    expect(b.order).toEqual([map.get('2655')!.color])
  })

  it('NR 虚线描边：画之前开、画完复原；假 ctx 没有 setLineDash 也不报错', () => {
    const z = 14
    const cells = parseCellPayload(payload([{ cell_uid: 'n', cell_lat: LAT, cell_lon: LON, azimuth: 0, dl_center_freq: 3500 }]))
    const styles = buildFreqStyles(cells, { palette: NR_PALETTE, scale: NR_SCALE })
    const map = new Map(styles.map((s) => [s.key, s]))
    const ix = buildCellIndex(cells)
    const tx = Math.floor(mercX(LON) * 2 ** z)
    const ty = Math.floor(mercY(LAT) * 2 ** z)
    const dashes: number[][] = []
    const { ctx } = fakeCtx()
    const withDash = { ...ctx, setLineDash: (d: number[]) => { dashes.push(d) } }
    expect(drawCellsTile(withDash, ix, map, null, z, tx, ty, 256, 0.32, true)).toBe(1)
    expect(dashes.length).toBe(2)
    expect(dashes[0].length).toBeGreaterThan(0)
    expect(dashes[1]).toEqual([])
    expect(drawCellsTile(fakeCtx().ctx, ix, map, null, z, tx, ty, 256, 0.32, true)).toBe(1)
  })
})

describe('5G NR', () => {
  it('另一组颜色、整体缩小一圈；单频点也按 NR_SCALE', () => {
    const cells = parseCellPayload(payload([
      { cell_uid: 'a', enodeb_id: 'g1', cell_lat: LAT, cell_lon: LON, azimuth: 0, dl_center_freq: 3500 },
    ]))
    const [s] = buildFreqStyles(cells, { palette: NR_PALETTE, scale: NR_SCALE })
    expect(s.color).toBe(NR_PALETTE[0])
    expect(s.factor).toBe(NR_SCALE)
  })

  it('Band 前缀：LTE 加 B、NR 加 n，已带前缀的原样', () => {
    expect(bandLabel('3', 'lte')).toBe('B3')
    expect(bandLabel('78', 'nr')).toBe('n78')
    expect(bandLabel('n78', 'nr')).toBe('n78')
    expect(bandLabel('B20', 'lte')).toBe('B20')
    expect(bandLabel('', 'nr')).toBe('')
  })

  it('站间距把 LTE 站点也算进来：共站址的 LTE 不算邻站，附近的 LTE 站决定 NR 半径', () => {
    const nr = parseCellPayload(payload([
      { cell_uid: 'n1', enodeb_id: 'g1', cell_lat: LAT, cell_lon: LON, azimuth: 0, dl_center_freq: 3500 },
      { cell_uid: 'n2', enodeb_id: 'g2', cell_lat: LAT, cell_lon: east(5000), azimuth: 0, dl_center_freq: 3500 },
    ]))
    expect(nr[0].siteR).toBeCloseTo(Math.min(MAX_SITE_R_M, 5000 * SITE_R_RATIO), 1) // 只看 NR：5 km 外
    const lte = parseCellPayload(payload([
      { cell_uid: 'l0', enodeb_id: 'e0', cell_lat: LAT, cell_lon: east(8), azimuth: 0, dl_center_freq: 801 }, // 与 n1 共站址
      { cell_uid: 'l1', enodeb_id: 'e1', cell_lat: LAT, cell_lon: east(600), azimuth: 0, dl_center_freq: 801 },
    ]))
    assignSiteRadii(nr, sitePoints(lte))
    expect(nr[0].siteR).toBeCloseTo(600 * SITE_R_RATIO, 0)
    expect(lte[0].siteR).toBeGreaterThan(0) // 参考点本身不被改写（仍是 LTE 自己算的）
  })

  it('sameSectorCells：NR 没有扇区号，按方位角分组', () => {
    const cells = parseCellPayload(payload([
      { cell_uid: 'a', enodeb_id: 'g', cell_lat: LAT, cell_lon: LON, azimuth: 60, dl_center_freq: 3500 },
      { cell_uid: 'b', enodeb_id: 'g', cell_lat: LAT, cell_lon: LON, azimuth: 61, dl_center_freq: 2600 },
      { cell_uid: 'c', enodeb_id: 'g', cell_lat: LAT, cell_lon: LON, azimuth: 180, dl_center_freq: 3500 },
    ]))
    const ix = buildCellIndex(cells)
    expect(sameSectorCells(ix, 0).map((i) => cells[i].uid)).toEqual(['b', 'a'])
  })
})
