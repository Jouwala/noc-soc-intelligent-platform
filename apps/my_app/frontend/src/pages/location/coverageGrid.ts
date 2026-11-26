/* ============================================================================
 * location/coverageGrid.ts —— NGI 覆盖网格的解包、空间索引与瓦片绘制（纯函数）
 * ============================================================================
 *
 * 为什么不用 <Polygon> 逐个画（2026-11-24 评估结论，勿删）：
 *   app.ngi_coverage_grid 一轮约 17.8 万个 50 m 格点。LeafletMap.tsx 的写法是
 *   「一个要素 = 一个 React 组件 + 一个 SVG path」，17.8 万个会把主线程卡死；
 *   而且在位置页默认 z11 下，50 m 格点连 1 像素都不到，画多边形纯属浪费。
 *   所以：后端只下发「中心点 + 档位」的紧凑二进制（NGC1，见
 *   backend/services/ngi_data/ngi_coverage_api.py），前端建一次桶索引，
 *   再由 Canvas GridLayer 按 256 px 瓦片只画落在该瓦片里的格点。
 *
 * NGC1 布局（小端）：
 *   [0,4)        魔数 "NGC1"
 *   [4,8)        uint32 n
 *   [8,8+4n)     float32 中心经度
 *   [8+4n,8+8n)  float32 中心纬度
 *   [8+8n,8+9n)  uint8   档位 bin（1~7；0 = 空）
 * 改格式必须与后端 pack_grid 同步（两边各有单测守着）。
 * ========================================================================== */

export const GRID_MAGIC = 'NGC1'
/** 空间桶所在的 Web Mercator 层级：z12 一个桶 ≈ 9.8 km，足够把 17.8 万点打散 */
export const INDEX_Z = 12
const INDEX_N = 1 << INDEX_Z
const EARTH_CIRCUMFERENCE_M = 40075016.686

export type CoverageGrid = {
  count: number
  lon: Float32Array
  lat: Float32Array
  bin: Uint8Array
}

export type GridIndex = {
  grid: CoverageGrid
  /** 归一化 Web Mercator 坐标（0~1）。用 Float64：z18 时世界宽 6700 万 px，float32 不够 */
  mx: Float64Array
  my: Float64Array
  /** key = bx * INDEX_N + by → 该桶里的格点下标 */
  buckets: Map<number, Int32Array>
}

export type BinStat = {
  bin: number
  rows: number
  rsrp_min: number | null
  rsrp_max: number | null
  rsrp_avg: number | null
}

/* -------------------------------------------------------------- 解包 */

export function parseGridBlob(buf: ArrayBuffer): CoverageGrid {
  if (buf.byteLength < 8) throw new Error(`coverage grid blob too short (${buf.byteLength} B)`)
  const head = new Uint8Array(buf, 0, 4)
  const magic = String.fromCharCode(head[0], head[1], head[2], head[3])
  if (magic !== GRID_MAGIC) throw new Error(`coverage grid blob: bad magic ${JSON.stringify(magic)}`)
  const count = new DataView(buf).getUint32(4, true)
  const need = 8 + count * 9
  if (buf.byteLength < need) throw new Error(`coverage grid blob truncated: ${buf.byteLength} < ${need}`)
  return {
    count,
    lon: new Float32Array(buf, 8, count),
    lat: new Float32Array(buf, 8 + 4 * count, count),
    bin: new Uint8Array(buf, 8 + 8 * count, count),
  }
}

/* -------------------------------------------------------------- 投影 */

export function mercX(lon: number): number {
  return (lon + 180) / 360
}

export function mercY(lat: number): number {
  const s = Math.min(Math.max(Math.sin((lat * Math.PI) / 180), -0.9999), 0.9999)
  return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)
}

/** 归一化 y → 纬度（瓦片中心纬度用来算「1 像素多少米」） */
export function latOfMercY(y: number): number {
  return (Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180) / Math.PI
}

/** 某纬度、某层级下 1 个 CSS 像素对应的地面米数 */
export function metersPerPixel(lat: number, z: number, tileSize = 256): number {
  return (EARTH_CIRCUMFERENCE_M * Math.cos((lat * Math.PI) / 180)) / (tileSize * 2 ** z)
}

/* -------------------------------------------------------------- 索引 */

const clampIdx = (v: number) => (v < 0 ? 0 : v >= INDEX_N ? INDEX_N - 1 : v)

export function buildIndex(grid: CoverageGrid): GridIndex {
  const n = grid.count
  const mx = new Float64Array(n)
  const my = new Float64Array(n)
  const keys = new Int32Array(n)
  const counts = new Map<number, number>()
  for (let i = 0; i < n; i++) {
    const lon = grid.lon[i]
    const lat = grid.lat[i]
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) { keys[i] = -1; continue }
    const x = mercX(lon)
    const y = mercY(lat)
    mx[i] = x
    my[i] = y
    const key = clampIdx(Math.floor(x * INDEX_N)) * INDEX_N + clampIdx(Math.floor(y * INDEX_N))
    keys[i] = key
    counts.set(key, (counts.get(key) || 0) + 1)
  }
  const buckets = new Map<number, Int32Array>()
  const fill = new Map<number, number>()
  counts.forEach((c, k) => { buckets.set(k, new Int32Array(c)); fill.set(k, 0) })
  for (let i = 0; i < n; i++) {
    const k = keys[i]
    if (k < 0) continue
    const pos = fill.get(k)!
    buckets.get(k)![pos] = i
    fill.set(k, pos + 1)
  }
  return { grid, mx, my, buckets }
}

/** 中心点落在「瓦片 (z,tx,ty) 外扩 padPx」范围内的格点下标 */
export function collectTile(
  index: GridIndex, z: number, tx: number, ty: number, tileSize: number, padPx: number,
): number[] {
  const scale = tileSize * 2 ** z
  const x0 = (tx * tileSize - padPx) / scale
  const x1 = ((tx + 1) * tileSize + padPx) / scale
  const y0 = (ty * tileSize - padPx) / scale
  const y1 = ((ty + 1) * tileSize + padPx) / scale
  const bx0 = clampIdx(Math.floor(x0 * INDEX_N))
  const bx1 = clampIdx(Math.floor(x1 * INDEX_N))
  const by0 = clampIdx(Math.floor(y0 * INDEX_N))
  const by1 = clampIdx(Math.floor(y1 * INDEX_N))
  const out: number[] = []
  const { mx, my, buckets } = index
  for (let bx = bx0; bx <= bx1; bx++) {
    for (let by = by0; by <= by1; by++) {
      const ids = buckets.get(bx * INDEX_N + by)
      if (!ids) continue
      for (let j = 0; j < ids.length; j++) {
        const i = ids[j]
        const x = mx[i]
        const y = my[i]
        if (x >= x0 && x < x1 && y >= y0 && y < y1) out.push(i)
      }
    }
  }
  return out
}

/** 点击位置所在的格点（中心距离不超过半格 × 1.2）；没有返回 -1 */
export function nearestCell(index: GridIndex, lat: number, lon: number, cellM: number): number {
  const bx = clampIdx(Math.floor(mercX(lon) * INDEX_N))
  const by = clampIdx(Math.floor(mercY(lat) * INDEX_N))
  const kx = 111320 * Math.cos((lat * Math.PI) / 180)
  const ky = 110540
  const limit = (cellM / 2) * 1.2
  let best = -1
  let bestD = Infinity
  for (let dx = -1; dx <= 1; dx++) {
    for (let dy = -1; dy <= 1; dy++) {
      const ids = index.buckets.get(clampIdx(bx + dx) * INDEX_N + clampIdx(by + dy))
      if (!ids) continue
      for (let j = 0; j < ids.length; j++) {
        const i = ids[j]
        const ex = Math.abs((index.grid.lon[i] - lon) * kx)
        const ey = Math.abs((index.grid.lat[i] - lat) * ky)
        if (ex > limit || ey > limit) continue
        const d = ex * ex + ey * ey
        if (d < bestD) { bestD = d; best = i }
      }
    }
  }
  return best
}

/* -------------------------------------------------------------- 配色 */

/** 好 → 差（RdYlGn 7 档） */
export const RSRP_PALETTE = ['#1a9641', '#58b453', '#a6d96a', '#ffffbf', '#fdae61', '#f46d43', '#d7191c']
export const COVERAGE_NA_COLOR = '#9ca3af'

/**
 * 档位 → 颜色。**不假设 1 是好还是差**：NGI legend 的档位顺序没有文档，
 * 按库内每档的 RSRP 均值从高到低排，再把调色板均匀铺上去。
 */
export function binColors(bins: BinStat[]): Record<number, string> {
  const out: Record<number, string> = {}
  const ranked = bins
    .filter((b) => b.bin > 0 && b.rsrp_avg != null && Number.isFinite(b.rsrp_avg))
    .sort((a, b) => (b.rsrp_avg as number) - (a.rsrp_avg as number))
  const P = RSRP_PALETTE.length
  ranked.forEach((b, i) => {
    const k = ranked.length <= 1 ? 0 : Math.round((i * (P - 1)) / (ranked.length - 1))
    out[b.bin] = RSRP_PALETTE[k]
  })
  for (const b of bins) if (!(b.bin in out)) out[b.bin] = COVERAGE_NA_COLOR
  return out
}

/* -------------------------------------------------------------- 绘制 */

export type FillCtx = {
  fillStyle: string | CanvasGradient | CanvasPattern
  fillRect: (x: number, y: number, w: number, h: number) => void
}

/**
 * 在一张瓦片上画格点；返回画了多少个。ctx 的坐标系是 CSS 像素（调用方负责 dpr 缩放）。
 * 小于 1 px 的格点按 1 px 画（否则低层级下整片覆盖区会「消失」）。
 */
export function drawTile(
  ctx: FillCtx, index: GridIndex, colors: Record<number, string>,
  z: number, tx: number, ty: number, tileSize: number, cellM: number,
): number {
  const scale = tileSize * 2 ** z
  const latC = latOfMercY((ty + 0.5) / 2 ** z)
  const cellPx = cellM / metersPerPixel(latC, z, tileSize)
  // 大于 2 px 时多 0.4 px，避免相邻格点之间因抗锯齿出现发丝缝
  const size = cellPx < 1 ? 1 : cellPx > 2 ? cellPx + 0.4 : cellPx
  const half = size / 2
  const ids = collectTile(index, z, tx, ty, tileSize, half + 1)
  if (!ids.length) return 0
  // 按档位分组，减少 fillStyle 切换
  const groups = new Map<number, number[]>()
  for (const i of ids) {
    const b = index.grid.bin[i]
    let g = groups.get(b)
    if (!g) { g = []; groups.set(b, g) }
    g.push(i)
  }
  const ox = tx * tileSize
  const oy = ty * tileSize
  groups.forEach((g, b) => {
    ctx.fillStyle = colors[b] || COVERAGE_NA_COLOR
    for (const i of g) {
      ctx.fillRect(index.mx[i] * scale - ox - half, index.my[i] * scale - oy - half, size, size)
    }
  })
  return ids.length
}

/** 覆盖范围外接框 → Leaflet fitBounds 用的 [[lat,lon],[lat,lon]] */
export function bboxToBounds(b: (number | null)[] | null | undefined): [[number, number], [number, number]] | null {
  if (!b || b.length < 4 || b.some((v) => v == null || !Number.isFinite(v as number))) return null
  const [minLon, minLat, maxLon, maxLat] = b as number[]
  return [[minLat, minLon], [maxLat, maxLon]]
}
