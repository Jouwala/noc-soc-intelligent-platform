/* ============================================================================
 * location/lteCells.ts —— LTE 小区扇区图层：解包、频点样式、空间索引、绘制、点选（纯函数）
 * ============================================================================
 *
 * 数据：GET /api/ngi-coverage/lte-cells（app.ngi_lte_cell_param 全量，约 8 千小区，
 * 列式 JSON，见 backend/services/ngi_data/ngi_cell_api.py）。
 *
 * 画法（2026-11-25 用户要求）：
 *   · 扇面张角固定 **65°**（SECTOR_DEG），以方位角为中线，左右各 32.5°；
 *   · **不同频点不同颜色、不同半径**：频点从低到高排序，低频画得大、高频画得小
 *     （按比例 1.0 → 0.5），同站多载波叠在一起时小扇面压在大扇面上面，几层都看得见；
 *   · **半径按站间距自适应**（2026-11-25 用户反馈：固定米数在密集区叠成一片）：
 *     每个站点取「到最近邻站的距离 × SITE_R_RATIO」作为本站最外层扇面的地面半径，
 *     城区站密就画得小、郊区站稀就画得大。半径是地面米数，所以缩放时和站间距
 *     同比例变化 —— 任何层级下相邻两站的扇面都不会互相压住；
 *     像素再夹在 [minPx, MAX_PX] 之间：缩到全国视图也看得见，放大到街道级也不会撑满屏；
 *   · 方位角为空或室分小区（cover_type 含 indoor）画成小圆，不瞎猜方向。
 *
 * 为什么用 Canvas 瓦片而不是 <Polygon>：8 千个 SVG 扇面 + Tooltip 会让拖动明显发涩，
 * 且和「覆盖 RSRP」图层同一套机制（见 coverageGrid.ts 文件头）。
 *
 * 想把某个频点固定成指定颜色/半径：改 FREQ_OVERRIDES 即可，其余频点仍自动分配。
 * ========================================================================== */
import { latOfMercY, mercX, mercY, metersPerPixel } from './coverageGrid'

export const SECTOR_DEG = 65
const HALF_RAD = ((SECTOR_DEG / 2) * Math.PI) / 180
const INDEX_Z = 12
const INDEX_N = 1 << INDEX_Z
/** 全向/室分小区画成小圆：半径取该频点扇面半径的这个比例 */
const OMNI_RATIO = 0.32

/* 站间距自适应半径（见文件头） */
/** 本站最外层扇面半径 = 到最近邻站距离 × 该比例。0.45：两站扇面正对时中间仍留 10% 空隙 */
export const SITE_R_RATIO = 0.45
/** 地面半径上下限（米）：再密也不小于 50 m，孤站也不超过 1.5 km */
export const MIN_SITE_R_M = 50
export const MAX_SITE_R_M = 1500
/** 最近邻只在这个范围内找；找不到按此距离算（即孤站取上限） */
const NN_SEARCH_M = 6000
/** 两个「站」距离小于此值视为共站址（同铁塔不同 eNB），不算邻站 */
const COLOCATED_M = 30
/** 像素上限（最低频）；高频按 factor 同比例缩小 */
export const MAX_PX = 160

export type LteCell = {
  uid: string
  enbId: string
  enbName: string
  cellId: string
  name: string
  sector: number | null
  pci: number | null
  tac: string
  freq: number | null
  freqKey: string
  earfcn: string
  band: string
  bw: string
  az: number | null
  height: number | null
  mechTilt: number | null
  elecTilt: number | null
  lat: number
  lon: number
  coverType: string
  active: string
  vendor: string
  omni: boolean
  /** 站点键：优先 eNodeB ID，缺失时用坐标（4 位小数） */
  site: string
  /** 本站最外层扇面的地面半径（米），由 assignSiteRadii 按站间距算出 */
  siteR: number
}

export type CellPayload = {
  available: boolean
  reason?: string
  columns?: string[]
  rows?: unknown[][]
  count?: number
  snapshot_date?: string | null
  fetched_at?: string | null
}

export type FreqStyle = {
  key: string
  freq: number | null
  label: string
  band: string
  count: number
  color: string
  /** 相对本站半径的比例：最低频 1.0，最高频 0.5 */
  factor: number
}

/* -------------------------------------------------------------- 解包 */

const num = (v: unknown): number | null => {
  if (v == null || v === '') return null
  const n = typeof v === 'number' ? v : Number(String(v).replace(/,/g, ''))
  return Number.isFinite(n) ? n : null
}
const str = (v: unknown): string => (v == null ? '' : String(v).trim())

/** 频点分组键：保留 1 位小数，避免 1815 与 1815.0 被当成两个频点 */
export function freqKeyOf(freq: number | null): string {
  return freq == null ? 'unknown' : String(Math.round(freq * 10) / 10)
}

export function parseCellPayload(p: CellPayload): LteCell[] {
  const cols = p.columns || []
  const at = (name: string) => cols.indexOf(name)
  const ix = {
    uid: at('cell_uid'), enb: at('enodeb_id'), enbName: at('enodeb_name'), cid: at('cell_id'),
    name: at('cell_name'), sector: at('sector_id'), pci: at('pci'), tac: at('tac'),
    freq: at('dl_center_freq'), earfcn: at('dl_earfcn'), band: at('band_indicator'),
    bandclass: at('bandclass'), bw: at('dl_system_bw'), az: at('azimuth'), h: at('height_m'),
    mt: at('mech_downtilt'), et: at('elec_downtilt'), clat: at('cell_lat'), clon: at('cell_lon'),
    slat: at('site_lat'), slon: at('site_lon'), cover: at('cover_type'), active: at('active'),
    vendor: at('vendor'),
  }
  const g = (r: unknown[], i: number) => (i >= 0 ? r[i] : null)
  const out: LteCell[] = []
  for (const r of p.rows || []) {
    let lat = num(g(r, ix.clat))
    let lon = num(g(r, ix.clon))
    if (lat == null || lon == null || (lat === 0 && lon === 0)) {
      lat = num(g(r, ix.slat))
      lon = num(g(r, ix.slon))
    }
    if (lat == null || lon == null || Math.abs(lat) > 85 || Math.abs(lon) > 180 || (lat === 0 && lon === 0)) continue
    const freq = num(g(r, ix.freq))
    const azRaw = num(g(r, ix.az))
    const az = azRaw == null ? null : ((azRaw % 360) + 360) % 360
    const coverType = str(g(r, ix.cover))
    out.push({
      uid: str(g(r, ix.uid)),
      enbId: str(g(r, ix.enb)),
      enbName: str(g(r, ix.enbName)),
      cellId: str(g(r, ix.cid)),
      name: str(g(r, ix.name)),
      sector: num(g(r, ix.sector)),
      pci: num(g(r, ix.pci)),
      tac: str(g(r, ix.tac)),
      freq,
      freqKey: freqKeyOf(freq),
      earfcn: str(g(r, ix.earfcn)),
      band: str(g(r, ix.band)) || str(g(r, ix.bandclass)),
      bw: str(g(r, ix.bw)),
      az,
      height: num(g(r, ix.h)),
      mechTilt: num(g(r, ix.mt)),
      elecTilt: num(g(r, ix.et)),
      lat,
      lon,
      coverType,
      active: str(g(r, ix.active)),
      vendor: str(g(r, ix.vendor)),
      omni: az == null || /indoor|室内|室分/i.test(coverType),
      site: str(g(r, ix.enb)) ? `e:${str(g(r, ix.enb))}` : `p:${lat.toFixed(4)},${lon.toFixed(4)}`,
      siteR: MAX_SITE_R_M,
    })
  }
  assignSiteRadii(out)
  return out
}

/* -------------------------------------------------------------- 站间距 */

/**
 * 按站点算最近邻距离，写回每个小区的 siteR（就地修改）。
 * 站点位置取本站各小区坐标的均值；用 1 km 网格找最近邻，8 千小区 / 数千站点约几毫秒。
 *
 * `refs`：额外的「邻站」参考点，只参与找最近邻、自己不分配半径。
 * 5G NR 图层用它把 LTE 站点也算进站间距（NR 站点稀疏，只看 NR 自己的话
 * 半径会顶到上限、盖住周围 LTE 站）；与参考点相距 < 30 m 的视为共站址，不算邻站。
 */
export function assignSiteRadii(cells: LteCell[], refs: { lat: number; lon: number }[] = []): void {
  const acc = new Map<string, { lat: number; lon: number; ids: number[] }>()
  cells.forEach((c, i) => {
    let a = acc.get(c.site)
    if (!a) { a = { lat: 0, lon: 0, ids: [] }; acc.set(c.site, a) }
    a.lat += c.lat
    a.lon += c.lon
    a.ids.push(i)
  })
  const sites = [...acc.values()].map((a) => ({ lat: a.lat / a.ids.length, lon: a.lon / a.ids.length, ids: a.ids }))
  if (!sites.length) return
  const ownCount = sites.length
  for (const p of refs) sites.push({ lat: p.lat, lon: p.lon, ids: [] })
  const lat0 = sites.slice(0, ownCount).reduce((s, x) => s + x.lat, 0) / ownCount
  const kx = 111320 * Math.cos((lat0 * Math.PI) / 180)
  const ky = 110540
  const G = 1000
  const xs = sites.map((x) => x.lon * kx)
  const ys = sites.map((x) => x.lat * ky)
  const grid = new Map<string, number[]>()
  sites.forEach((_, j) => {
    const k = `${Math.floor(xs[j] / G)},${Math.floor(ys[j] / G)}`
    let b = grid.get(k)
    if (!b) { b = []; grid.set(k, b) }
    b.push(j)
  })
  const rings = Math.ceil(NN_SEARCH_M / G)
  sites.forEach((site, j) => {
    if (j >= ownCount) return // 参考点只当邻站，不分配半径
    const gx = Math.floor(xs[j] / G)
    const gy = Math.floor(ys[j] / G)
    let best = Infinity
    for (let r = 0; r <= rings; r++) {
      if ((r - 1) * G > best) break // 第 r 圈的格子离本站至少 (r-1)·G
      for (let dx = -r; dx <= r; dx++) {
        for (let dy = -r; dy <= r; dy++) {
          if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue
          const ids = grid.get(`${gx + dx},${gy + dy}`)
          if (!ids) continue
          for (const k of ids) {
            if (k === j) continue
            const d = Math.hypot(xs[k] - xs[j], ys[k] - ys[j])
            if (d >= COLOCATED_M && d < best) best = d
          }
        }
      }
    }
    const nn = Math.min(best, NN_SEARCH_M)
    const R = Math.min(MAX_SITE_R_M, Math.max(MIN_SITE_R_M, nn * SITE_R_RATIO))
    for (const i of site.ids) cells[i].siteR = R
  })
}

/** 每个站点一个点（本站各小区坐标的均值），给 assignSiteRadii 的 refs 用 */
export function sitePoints(cells: LteCell[]): { lat: number; lon: number }[] {
  const acc = new Map<string, { lat: number; lon: number; n: number }>()
  for (const c of cells) {
    let a = acc.get(c.site)
    if (!a) { a = { lat: 0, lon: 0, n: 0 }; acc.set(c.site, a) }
    a.lat += c.lat
    a.lon += c.lon
    a.n += 1
  }
  return [...acc.values()].map((a) => ({ lat: a.lat / a.n, lon: a.lon / a.n }))
}

/* -------------------------------------------------------------- 频点样式 */

/** 分类色：相邻两色反差大，深浅底图上都读得清 */
export const FREQ_PALETTE = [
  '#e6194b', '#3cb44b', '#4363d8', '#f58231', '#911eb4',
  '#42d4f4', '#f032e6', '#9a6324', '#469990', '#808000',
]
export const UNKNOWN_FREQ_COLOR = '#9ca3af'

/** 想固定某个频点的颜色/半径比例就在这里加，键是 freqKeyOf() 的结果（如 '1815.1'），
 *  例：{ '801': { color: '#e6194b', factor: 1 } } */
export const FREQ_OVERRIDES: Record<string, Partial<Pick<FreqStyle, 'color' | 'factor' | 'label'>>> = {}

/* 5G NR 图层（2026-11-25）：与 LTE 同一套画法，但
 *   · 用另一组颜色（偏青 / 橙 / 深紫等，和 LTE 的红绿蓝错开），描边是虚线 —— 两层同时打开时一眼分得清；
 *   · 整体半径乘 NR_SCALE：NR 多与 LTE 共站，扇面若和 LTE 最低频一样大会把整站 LTE 盖住；
 *     缩小一圈后 LTE 的外环仍露在外面（3.5 GHz 覆盖本来也比低频小）。
 * 要固定某个 NR 频点的颜色 / 比例，改 NR_FREQ_OVERRIDES（键同 freqKeyOf()）。 */
export const NR_PALETTE = [
  '#00a3bf', '#ff8b00', '#5243aa', '#00875a', '#de350b',
  '#0065ff', '#c0b6f2', '#ffc400', '#79e2f2', '#6b778c',
]
export const NR_FREQ_OVERRIDES: Record<string, Partial<Pick<FreqStyle, 'color' | 'factor' | 'label'>>> = {}
export const NR_SCALE = 0.8

export type CellKind = 'lte' | 'nr'

/** Band 显示：LTE 加「B」，NR 加「n」；已带前缀的原样 */
export function bandLabel(band: string, kind: CellKind): string {
  const b = (band || '').trim()
  if (!b) return ''
  if (/^[bn]\d/i.test(b)) return b
  return `${kind === 'nr' ? 'n' : 'B'}${b}`
}

export type FreqStyleOptions = {
  palette?: string[]
  overrides?: Record<string, Partial<Pick<FreqStyle, 'color' | 'factor' | 'label'>>>
  /** 整层半径比例（LTE 1，NR 见 NR_SCALE） */
  scale?: number
}

/** 频点低 → 高 对应的半径比例 */
const FACTOR_LOW = 1
const FACTOR_HIGH = 0.5

const lerp = (a: number, b: number, t: number) => a + (b - a) * t

export function buildFreqStyles(cells: LteCell[], opts: FreqStyleOptions = {}): FreqStyle[] {
  const palette = opts.palette?.length ? opts.palette : FREQ_PALETTE
  const overrides = opts.overrides || FREQ_OVERRIDES
  const scale = opts.scale ?? 1
  const groups = new Map<string, { freq: number | null; count: number; bands: Map<string, number> }>()
  for (const c of cells) {
    let g = groups.get(c.freqKey)
    if (!g) { g = { freq: c.freq, count: 0, bands: new Map() }; groups.set(c.freqKey, g) }
    g.count += 1
    if (c.band) g.bands.set(c.band, (g.bands.get(c.band) || 0) + 1)
  }
  const known = [...groups.entries()].filter(([, g]) => g.freq != null).sort((a, b) => (a[1].freq as number) - (b[1].freq as number))
  const n = known.length
  const styles: FreqStyle[] = known.map(([key, g], i) => {
    // 只有一个频点时没有「高低」之分，用满本站半径（factor = 1）
    const t = n <= 1 ? 0 : i / (n - 1)
    const band = [...g.bands.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || ''
    const ov = overrides[key] || {}
    return {
      key,
      freq: g.freq,
      label: ov.label || `${key} MHz`,
      band,
      count: g.count,
      color: ov.color || palette[i % palette.length],
      factor: ov.factor || Math.round(lerp(FACTOR_LOW, FACTOR_HIGH, t) * scale * 100) / 100,
    }
  })
  const unknown = groups.get('unknown')
  if (unknown) {
    styles.push({
      key: 'unknown', freq: null, label: '', band: '', count: unknown.count,
      color: UNKNOWN_FREQ_COLOR, factor: Math.round(FACTOR_HIGH * scale * 100) / 100,
    })
  }
  return styles
}

/** 像素下限（最低频；单位 CSS 像素），按层级查表，`z <= 层级` 取对应值。
 *  2026-11-25 用户反馈「全国地图下站点和扇区太小、很难看到，放大了可以」：
 *  此前缩小时下限一路压到 2.5 px，全国视图里扇面只剩一个点。现在缩小时反而给一个
 *  **看得见的固定尺寸**（7~9 px）：全国视图下每个站都是一朵清楚的小扇区，
 *  密集城区会挨在一起，但放大到 13 级以上就按站间距展开（那时地面半径已大于下限）。
 *  想整体再大 / 再小，改这张表即可（hitCell 用同一个函数，点选范围自动一致）。 */
export const MIN_PX_BY_ZOOM: [number, number][] = [[8, 7], [9, 8], [10, 9], [11, 9], [12, 8]]
const MIN_PX_DEFAULT = 6

export function minPxAt(z: number): number {
  for (const [zz, px] of MIN_PX_BY_ZOOM) if (z <= zz) return px
  return MIN_PX_DEFAULT
}

/** 下限按频点比例缩小时不能缩太多：最高频（factor 0.5）也保留 80%，否则全国视图下又看不见 */
const minFactor = (factor: number) => 0.6 + 0.4 * factor

/** 某小区（按其频点样式）在某层级下的扇面半径（CSS 像素） */
export function sectorRadiusPx(style: FreqStyle, cell: Pick<LteCell, 'lat' | 'siteR'>, z: number, tileSize = 256): number {
  const px = (cell.siteR * style.factor) / metersPerPixel(cell.lat, z, tileSize)
  return Math.min(MAX_PX * style.factor, Math.max(minPxAt(z) * minFactor(style.factor), px))
}

/* -------------------------------------------------------------- 索引 */

export type CellIndex = {
  cells: LteCell[]
  mx: Float64Array
  my: Float64Array
  buckets: Map<number, number[]>
  /** 站点键 → 本站小区下标（悬浮提示列同扇区载波用） */
  sites: Map<string, number[]>
  /** 全部小区里最大的 siteR（米），用来算瓦片 / 点选的外扩范围 */
  maxR: number
}

const clampIdx = (v: number) => (v < 0 ? 0 : v >= INDEX_N ? INDEX_N - 1 : v)

export function buildCellIndex(cells: LteCell[]): CellIndex {
  const mx = new Float64Array(cells.length)
  const my = new Float64Array(cells.length)
  const buckets = new Map<number, number[]>()
  const sites = new Map<string, number[]>()
  let maxR = 0
  cells.forEach((c, i) => {
    let s = sites.get(c.site)
    if (!s) { s = []; sites.set(c.site, s) }
    s.push(i)
    if (c.siteR > maxR) maxR = c.siteR
    mx[i] = mercX(c.lon)
    my[i] = mercY(c.lat)
    const k = clampIdx(Math.floor(mx[i] * INDEX_N)) * INDEX_N + clampIdx(Math.floor(my[i] * INDEX_N))
    let b = buckets.get(k)
    if (!b) { b = []; buckets.set(k, b) }
    b.push(i)
  })
  return { cells, mx, my, buckets, sites, maxR }
}

/** 某纬度、某层级下，任何扇面可能伸出的最大像素距离（外扩范围用） */
function padPx(index: CellIndex, lat: number, z: number, tileSize: number): number {
  return Math.max(minPxAt(z), Math.min(MAX_PX, index.maxR / metersPerPixel(lat, z, tileSize))) + 2
}

/** 与第 i 个小区同站、同扇区的全部载波（含自己），按频点升序。
 *  有 sector_id 按 sector_id 判；没有就按方位角相差 ≤3° 判；全向的归一组。 */
export function sameSectorCells(index: CellIndex, i: number): number[] {
  const c = index.cells[i]
  const ids = index.sites.get(c.site) || [i]
  return ids
    .filter((k) => {
      const o = index.cells[k]
      if (c.sector != null && o.sector != null) return o.sector === c.sector
      if (c.omni || o.omni) return c.omni && o.omni
      return angleDiff(o.az as number, c.az as number) <= 3
    })
    .sort((a, b) => (index.cells[a].freq ?? 1e9) - (index.cells[b].freq ?? 1e9))
}

function cellsNear(index: CellIndex, x0: number, x1: number, y0: number, y1: number): number[] {
  const out: number[] = []
  const bx0 = clampIdx(Math.floor(x0 * INDEX_N))
  const bx1 = clampIdx(Math.floor(x1 * INDEX_N))
  const by0 = clampIdx(Math.floor(y0 * INDEX_N))
  const by1 = clampIdx(Math.floor(y1 * INDEX_N))
  for (let bx = bx0; bx <= bx1; bx++) {
    for (let by = by0; by <= by1; by++) {
      const ids = index.buckets.get(bx * INDEX_N + by)
      if (!ids) continue
      for (const i of ids) {
        const x = index.mx[i]
        const y = index.my[i]
        if (x >= x0 && x <= x1 && y >= y0 && y <= y1) out.push(i)
      }
    }
  }
  return out
}

/* -------------------------------------------------------------- 绘制 */

export type SectorCtx = {
  fillStyle: string | CanvasGradient | CanvasPattern
  strokeStyle: string | CanvasGradient | CanvasPattern
  lineWidth: number
  globalAlpha: number
  beginPath: () => void
  moveTo: (x: number, y: number) => void
  arc: (x: number, y: number, r: number, a0: number, a1: number) => void
  closePath: () => void
  fill: () => void
  stroke: () => void
  /** 可选：NR 图层画虚线描边（测试用的假 ctx 可以不实现） */
  setLineDash?: (segments: number[]) => void
}

/** 方位角（正北 0°、顺时针）→ Canvas 角度（正东 0、顺时针，y 轴向下） */
export const canvasAngle = (azDeg: number) => ((azDeg - 90) * Math.PI) / 180

/**
 * 画一张瓦片上的扇面；返回画了几个。`visible` 为 null 表示全部频点都显示。
 * 先画半径大的（低频）、后画半径小的（高频），小扇面始终压在上面。
 */
export function drawCellsTile(
  ctx: SectorCtx, index: CellIndex, styles: Map<string, FreqStyle>, visible: Set<string> | null,
  z: number, tx: number, ty: number, tileSize: number, fillAlpha = 0.32, dashed = false,
): number {
  const scale = tileSize * 2 ** z
  const latC = latOfMercY((ty + 0.5) / 2 ** z)
  const pad = padPx(index, latC, z, tileSize)
  const ids = cellsNear(
    index,
    (tx * tileSize - pad) / scale, ((tx + 1) * tileSize + pad) / scale,
    (ty * tileSize - pad) / scale, ((ty + 1) * tileSize + pad) / scale,
  )
  const items: { i: number; r: number; s: FreqStyle }[] = []
  for (const i of ids) {
    const c = index.cells[i]
    if (visible && !visible.has(c.freqKey)) continue
    const s = styles.get(c.freqKey)
    if (!s) continue
    items.push({ i, r: sectorRadiusPx(s, c, z, tileSize), s })
  }
  items.sort((a, b) => b.r - a.r)
  const ox = tx * tileSize
  const oy = ty * tileSize
  // 缩小时扇面只有几个像素：描边加粗、填充加深，否则淡色小扇面在底图上读不出来
  const lowZoom = z < 12
  ctx.lineWidth = z >= 13 ? 1.1 : lowZoom ? 1 : 0.8
  const fillA = lowZoom ? Math.max(fillAlpha, 0.55) : fillAlpha
  if (dashed) ctx.setLineDash?.(z >= 13 ? [5, 3] : [3, 2])
  for (const { i, r, s } of items) {
    const c = index.cells[i]
    const x = index.mx[i] * scale - ox
    const y = index.my[i] * scale - oy
    ctx.beginPath()
    if (c.omni) {
      ctx.arc(x, y, Math.max(3, r * OMNI_RATIO), 0, Math.PI * 2)
    } else {
      const a = canvasAngle(c.az as number)
      ctx.moveTo(x, y)
      ctx.arc(x, y, r, a - HALF_RAD, a + HALF_RAD)
    }
    ctx.closePath()
    ctx.fillStyle = s.color
    ctx.globalAlpha = fillA
    ctx.fill()
    ctx.strokeStyle = s.color
    ctx.globalAlpha = 0.95
    ctx.stroke()
  }
  if (dashed) ctx.setLineDash?.([])
  ctx.globalAlpha = 1
  return items.length
}

/* -------------------------------------------------------------- 点选 */

/** 0~180 的夹角差 */
export function angleDiff(a: number, b: number): number {
  const d = Math.abs((((a - b) % 360) + 360) % 360)
  return d > 180 ? 360 - d : d
}

/**
 * 点 (lat, lon) 落在哪个扇面里；多个重叠时取半径最小（最上层、即用户看到的那个），
 * 再按离站点近者优先。没有返回 -1。
 */
export function hitCell(
  index: CellIndex, styles: Map<string, FreqStyle>, visible: Set<string> | null,
  lat: number, lon: number, z: number, tileSize = 256,
): number {
  const scale = tileSize * 2 ** z
  const px = mercX(lon) * scale
  const py = mercY(lat) * scale
  const pad = padPx(index, lat, z, tileSize)
  const ids = cellsNear(index, (px - pad) / scale, (px + pad) / scale, (py - pad) / scale, (py + pad) / scale)
  let best = -1
  let bestR = Infinity
  let bestD = Infinity
  for (const i of ids) {
    const c = index.cells[i]
    if (visible && !visible.has(c.freqKey)) continue
    const s = styles.get(c.freqKey)
    if (!s) continue
    const r = sectorRadiusPx(s, c, z, tileSize)
    const dx = px - index.mx[i] * scale
    const dy = py - index.my[i] * scale
    const d = Math.hypot(dx, dy)
    let inside: boolean
    let effR = r
    if (c.omni) {
      effR = Math.max(3, r * OMNI_RATIO)
      inside = d <= effR + 1
    } else {
      const bearing = ((Math.atan2(dx, -dy) * 180) / Math.PI + 360) % 360
      inside = d <= r + 1 && (d < 2 || angleDiff(bearing, c.az as number) <= SECTOR_DEG / 2 + 0.5)
    }
    if (!inside) continue
    if (effR < bestR - 0.01 || (Math.abs(effR - bestR) <= 0.01 && d < bestD)) {
      best = i
      bestR = effR
      bestD = d
    }
  }
  return best
}

/** 高亮用：扇面轮廓的经纬度折线（约 2.5° 一个点） */
export function sectorOutline(c: LteCell, radiusPx: number, z: number, tileSize = 256): [number, number][] {
  const scale = tileSize * 2 ** z
  const cx = mercX(c.lon) * scale
  const cy = mercY(c.lat) * scale
  const toLatLng = (x: number, y: number): [number, number] => {
    const lon = (x / scale) * 360 - 180
    return [latOfMercY(y / scale), lon]
  }
  const pts: [number, number][] = []
  if (c.omni) {
    const r = Math.max(3, radiusPx * OMNI_RATIO)
    for (let k = 0; k <= 48; k++) {
      const a = (k / 48) * Math.PI * 2
      pts.push(toLatLng(cx + r * Math.cos(a), cy + r * Math.sin(a)))
    }
    return pts
  }
  const a = canvasAngle(c.az as number)
  pts.push(toLatLng(cx, cy))
  const steps = 26
  for (let k = 0; k <= steps; k++) {
    const t = a - HALF_RAD + (2 * HALF_RAD * k) / steps
    pts.push(toLatLng(cx + radiusPx * Math.cos(t), cy + radiusPx * Math.sin(t)))
  }
  pts.push(toLatLng(cx, cy))
  return pts
}
