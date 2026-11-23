/* ============================================================================
 * location/geo.ts —— 位置页（?page=location）的纯几何计算
 * ============================================================================
 *
 * 只做计算、不发请求、不碰 DOM，便于单测与复用：
 *   · haversineKm           两点球面距离（km）
 *   · distanceToSegmentKm   点到线段（链路）的最短距离 —— 判断「最近的链路」
 *   · ringOf                「最近的一圈站点」：半径内取最近的 N 个；半径内太少时
 *                           自动扩到第 N 个（并把实际半径回传，地图上画的圈与
 *                           列表里的站点才能对得上）
 *   · circlePolygon         以点为中心的等距圆（多边形近似，供 Leaflet 画虚线圈；
 *                           LeafletMap 没有 Circle 封装，也不值得为此加一个）
 *   · parseLatLon           从自由文本里抠经纬度（问答助手答案 / 手输都走它）
 *
 * 口径约定（与后端、GIS 页保持一致）：
 *   · 坐标一律 [lat, lng]、十进制角度；lat ∈ [-90,90]，lng ∈ [-180,180]。
 *   · 距离用球面 haversine（R=6371.0088 km，IUGG 平均半径）。站点间距多在
 *     几十公里内，用平面近似也不会差太多，但「点到线段」在投影后计算更简单，
 *     所以两处分别用各自合适的公式，差异只在米级。
 * ========================================================================== */

export type LatLon = { lat: number; lon: number }

/** IUGG 平均地球半径（km）。与常见 haversine 实现同源，便于与外部工具对拍。 */
const EARTH_R_KM = 6371.0088

const toRad = (deg: number): number => (deg * Math.PI) / 180
const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v))

/** 两点球面距离（km）。 */
export function haversineKm(a: LatLon, b: LatLon): number {
  const dLat = toRad(b.lat - a.lat)
  const dLon = toRad(b.lon - a.lon)
  const la1 = toRad(a.lat)
  const la2 = toRad(b.lat)
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin(dLon / 2) ** 2
  return 2 * EARTH_R_KM * Math.asin(Math.min(1, Math.sqrt(h)))
}

/**
 * 点到线段的最短距离（km）。
 *
 * 做法：以 p 为原点做等距圆柱投影（经度乘 cos(lat)），把三点压到局部 km 平面，
 * 再用标准「点到线段」公式。蒙罗维亚到哈珀约 400km，该投影在利比里亚纬度
 * （4°~9°N）的误差 < 0.1%，对「哪条链路最近」的排序没有影响。
 */
export function distanceToSegmentKm(p: LatLon, a: LatLon, b: LatLon): number {
  const kx = Math.cos(toRad(p.lat)) * 111.32 // 每度经度的 km 数（该纬度）
  const ky = 110.574 // 每度纬度的 km 数（近似常量）
  const ax = (a.lon - p.lon) * kx
  const ay = (a.lat - p.lat) * ky
  const bx = (b.lon - p.lon) * kx
  const by = (b.lat - p.lat) * ky
  const dx = bx - ax
  const dy = by - ay
  const len2 = dx * dx + dy * dy
  if (len2 <= 1e-12) return Math.hypot(ax, ay)
  // 投影参数 t 夹到 [0,1]：垂足落在线段外时，最近点就是端点
  const t = clamp(-(ax * dx + ay * dy) / len2, 0, 1)
  return Math.hypot(ax + t * dx, ay + t * dy)
}

export type RingOptions = {
  /** 用户选择的半径（km） */
  radiusKm: number
  /** 环内最多显示多少个站点 */
  limit: number
  /** 半径内少于这个数时自动扩圈（默认 3：一两个站围不成「一圈」） */
  minCount?: number
}

export type RingResult<T> = {
  /** 环内站点（按距离升序） */
  items: { item: T; km: number }[]
  /** 实际画圈的半径（可能因自动扩圈而大于用户选的 radiusKm） */
  radiusKm: number
  /** 是否发生了自动扩圈 */
  widened: boolean
  /** 半径内（用户原半径）实际有几个 —— 用于「扩圈」提示的原文数字 */
  withinOriginal: number
}

/**
 * 取「周围最近的一圈」：先按用户半径筛，再按距离取前 limit 个。
 * 若半径内不足 minCount 个，则扩到第 minCount 个（上限 limit）的距离，
 * 保证地图上总有一圈可看的站点，并如实回传 widened。
 */
export function ringOf<T extends LatLon>(
  items: T[],
  center: LatLon,
  opts: RingOptions,
): RingResult<T> {
  const minCount = Math.max(1, opts.minCount ?? 3)
  const limit = Math.max(1, opts.limit)
  const withKm = items
    .map((item) => ({ item, km: haversineKm(center, item) }))
    .sort((x, y) => x.km - y.km)

  const within = withKm.filter((r) => r.km <= opts.radiusKm)
  const withinOriginal = within.length
  if (within.length >= minCount) {
    return {
      items: within.slice(0, limit),
      radiusKm: opts.radiusKm,
      widened: false,
      withinOriginal,
    }
  }

  // 半径内太少：扩到「第 minCount 个最近站点」的距离；但要保证至少有一个站，
  // 否则空地图没有意义（查无站点时由调用方显示空态）。
  const need = Math.min(limit, Math.max(minCount, within.length))
  const picked = withKm.slice(0, Math.max(need, 1))
  const radiusKm = picked.length ? picked[picked.length - 1].km : opts.radiusKm
  return {
    items: picked,
    radiusKm: Math.max(radiusKm, opts.radiusKm),
    widened: true,
    withinOriginal,
  }
}

/**
 * 以 center 为中心、radiusKm 为半径的等距圆，返回 [lat, lng][]。
 * steps 取 64：地图上肉眼看不出折线，又不至于像 256 那样白算。
 */
export function circlePolygon(center: LatLon, radiusKm: number, steps = 64): [number, number][] {
  const pts: [number, number][] = []
  const dLat = (radiusKm / EARTH_R_KM) * (180 / Math.PI)
  const dLon = dLat / Math.max(0.05, Math.cos(toRad(center.lat)))
  for (let i = 0; i < steps; i++) {
    const ang = (i / steps) * Math.PI * 2
    pts.push([center.lat + dLat * Math.cos(ang), center.lon + dLon * Math.sin(ang)])
  }
  return pts
}

/** 距离的展示文本：<1km 用米、<10km 一位小数、其余取整。 */
export function fmtKm(km: number): string {
  if (!Number.isFinite(km)) return '—'
  if (km < 1) return `${Math.round(km * 1000)} m`
  if (km < 10) return `${km.toFixed(1)} km`
  return `${Math.round(km)} km`
}

/** 坐标的展示文本（6 位小数，约 0.1m；与 GIS 站点坐标精度一致）。 */
export function fmtLatLon(p: LatLon): string {
  return `${p.lat.toFixed(6)}, ${p.lon.toFixed(6)}`
}

/**
 * 从自由文本里提取经纬度。
 *
 * 支持（问答助手答案里的常见写法都在内）：
 *   `坐标: 6.2936, -10.7810` / `6.2936, -10.7810` / `lat 6.2936 lng -10.7810`
 *   `(6.2936, -10.7810)` / `6.2936 N, 10.7810 W`（N/S/E/W 后缀会转成符号）
 *
 * 只认「一对数值」：先按标签取，取不到再退化为全文第一对合法数值。
 * 返回 null = 答案里没有可用的坐标（调用方必须如实提示，不能猜一个点）。
 */
export function parseLatLon(text: string): LatLon | null {
  const src = String(text || '')
  if (!src.trim()) return null

  const num = '([+-]?\\d{1,3}(?:\\.\\d+)?)'
  const hemi = '\\s*([NSEW])?'
  const labelled = new RegExp(
    `(?:lat(?:itude)?|纬度)\\s*[:：=]?\\s*${num}${hemi}[\\s,，;；]+` +
    `(?:lon(?:gitude)?|lng|经度)\\s*[:：=]?\\s*${num}${hemi}`,
    'i',
  )
  const pair = src.match(labelled)
  if (pair) {
    const lat = signed(Number(pair[1]), pair[2] ?? '')
    const lon = signed(Number(pair[3]), pair[4] ?? '')
    if (valid(lat, lon)) return { lat, lon }
  }

  // 退化：第一对「逗号/空白分隔」的合法数值
  const generic = new RegExp(
    `(?<![\\d.])${num}${hemi}\\s*[,，;；]\\s*${num}${hemi}(?![\\d.])`,
    'gi',
  )
  let m: RegExpExecArray | null
  while ((m = generic.exec(src))) {
    const lat = signed(Number(m[1]), m[2] ?? '')
    const lon = signed(Number(m[3]), m[4] ?? '')
    if (valid(lat, lon)) return { lat, lon }
  }

  // 再退化：`lat: 6.2 ... lng: -10.7`（顺序反过来也认）
  const anyNum = new RegExp(num, 'g')
  const nums: number[] = []
  let n: RegExpExecArray | null
  while ((n = anyNum.exec(src))) nums.push(Number(n[1]))
  for (let i = 0; i + 1 < nums.length; i++) {
    if (valid(nums[i], nums[i + 1])) return { lat: nums[i], lon: nums[i + 1] }
  }
  return null
}

function signed(v: number, hemi: string): number {
  const h = hemi.toUpperCase()
  if (h === 'S' || h === 'W') return -Math.abs(v)
  if (h === 'N' || h === 'E') return Math.abs(v)
  return v
}

function valid(lat: number, lon: number): boolean {
  return Number.isFinite(lat) && Number.isFinite(lon)
    && lat >= -90 && lat <= 90
    && lon >= -180 && lon <= 180
    // 0,0 是「大西洋几内亚湾外海」，在利比里亚业务里只可能是解析错/空值
    && !(lat === 0 && lon === 0)
}
