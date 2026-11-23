/* ============================================================================
 * location/useLocationData.ts —— 位置页的底图数据（站点 / MW 链路 / 光纤链路）
 * ============================================================================
 *
 * 三个数据源互相独立、任一失败不阻断其余（位置页的核心是「地图能看」）：
 *
 *   1. 站点   GET /api/gis/sites            —— 与 ?page=gis 同一接口、同一时间窗
 *      （近 7 天，与 GIS 首屏一致）。字段含 lat/lng、实时/历史告警数、TT/WO、
 *      VIP、站点等级 —— 站点着色走 gismap/types.ts 的 siteLegendKey()，
 *      与 GIS 页同源，两页颜色不会漂。
 *
 *   2. MW     GET /api/gis/topology?with_util=1 —— 与 GIS 页的 KML 链路同一份
 *      （844 条，端点自带经纬度）；利用率叠加与 GIS 同一槽位。点击链路时再用
 *      /api/mw-ume/realtime 取明细，见 MwLinkDrawer。
 *
 *   3. 光纤   public/olib/olib-fiber-links.json + olib-site-geo.json
 *      —— OLIB 拓扑 PDF 第 1 页数字化的「站点对」边（592 条），静态资产，
 *      加载失败只关掉这一层，不影响站点与 MW。
 *
 * 只做「取数 + 形状校验」，不做距离计算（那是 geo.ts 的职责）。
 * ========================================================================== */
import { useCallback, useEffect, useState } from 'react'
import dayjs from 'dayjs'

import api from '../../api'
import type { Site, TopologyLink, UtilMeta } from '../gismap/types'

/** 光纤链路（两端都已定位的一条「站点对」边）。 */
export type FiberLink = {
  id: string
  aName: string
  bName: string
  aLat: number
  aLon: number
  bLat: number
  bLon: number
  /** PDF 图例分组：base / f40 / f10 / ipran / p26 / plan */
  group: string
  /** PDF 图例里的原始类型，如 40G / 10G 规划 / P26B / DWDM / 基础链路 */
  type: string
  /** 1 = 虚线（规划中） */
  planned: boolean
}

type FiberFile = {
  edges?: { a: number; b: number; c?: string; t?: string; d?: number }[]
  sites?: Record<string, string>
}

type SiteGeoFile = {
  records?: { i: number; name?: string; lat?: number; lng?: number; siteId?: string }[]
}

type SitesResponse = { sites?: Site[]; total?: number }

export type LocationData = {
  sites: Site[]
  mwLinks: TopologyLink[]
  fiberLinks: FiberLink[]
  utilMeta: UtilMeta | null
  /** 各层的加载状态（分开暴露，便于在界面上分别说明「哪一层没出来」） */
  loadingSites: boolean
  loadingMw: boolean
  loadingFiber: boolean
  sitesError: string
  mwError: string
  fiberError: string
  reload: () => void
}

/** 与 GIS 首屏一致的时间窗：近 7 天（含今天）。后端接受 YYYY-MM-DD。 */
function gisTimeWindow(): { time_start: string } {
  return { time_start: dayjs().subtract(7, 'day').format('YYYY-MM-DD') }
}

/** 光纤静态资产的地址：必须走 BASE_URL（线上前缀 /myapp/，见 vite.config.ts）。 */
const FIBER_URL = `${import.meta.env.BASE_URL}olib/olib-fiber-links.json`
const SITE_GEO_URL = `${import.meta.env.BASE_URL}olib/olib-site-geo.json`

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return (await res.json()) as T
}

/** 把「站点对 + 站点 GPS 映射表」拼成可画的 FiberLink[]；缺坐标的边直接丢弃。 */
export function joinFiberLinks(file: FiberFile, geo: SiteGeoFile): FiberLink[] {
  const byIndex = new Map<number, { lat: number; lng: number; name: string }>()
  for (const r of geo.records || []) {
    const lat = Number(r.lat)
    const lng = Number(r.lng)
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) continue
    if (!(lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180)) continue
    byIndex.set(Number(r.i), { lat, lng, name: String(r.name || '') })
  }
  const out: FiberLink[] = []
  let seq = 0
  for (const e of file.edges || []) {
    const a = byIndex.get(Number(e.a))
    const b = byIndex.get(Number(e.b))
    if (!a || !b) continue
    seq += 1
    out.push({
      id: `fo-${seq}-${e.a}-${e.b}`,
      aName: a.name,
      bName: b.name,
      aLat: a.lat,
      aLon: a.lng,
      bLat: b.lat,
      bLon: b.lng,
      group: String(e.c || 'base'),
      type: String(e.t || ''),
      planned: Number(e.d) === 1,
    })
  }
  return out
}

export function useLocationData(): LocationData {
  const [sites, setSites] = useState<Site[]>([])
  const [mwLinks, setMwLinks] = useState<TopologyLink[]>([])
  const [fiberLinks, setFiberLinks] = useState<FiberLink[]>([])
  const [utilMeta, setUtilMeta] = useState<UtilMeta | null>(null)
  const [loadingSites, setLoadingSites] = useState(true)
  const [loadingMw, setLoadingMw] = useState(true)
  const [loadingFiber, setLoadingFiber] = useState(true)
  const [sitesError, setSitesError] = useState('')
  const [mwError, setMwError] = useState('')
  const [fiberError, setFiberError] = useState('')
  const [tick, setTick] = useState(0)

  const reload = useCallback(() => setTick((n) => n + 1), [])

  useEffect(() => {
    let alive = true
    setLoadingSites(true)
    setSitesError('')
    api.get<SitesResponse>('/gis/sites', { params: gisTimeWindow() })
      .then(({ data }) => {
        if (!alive) return
        setSites(Array.isArray(data?.sites) ? data.sites : [])
      })
      .catch((err: unknown) => {
        if (!alive) return
        setSitesError(err instanceof Error ? err.message : String(err))
        setSites([])
      })
      .finally(() => { if (alive) setLoadingSites(false) })
    return () => { alive = false }
  }, [tick])

  useEffect(() => {
    let alive = true
    setLoadingMw(true)
    setMwError('')
    api.get<{ links?: TopologyLink[]; util_meta?: UtilMeta | null }>('/gis/topology', {
      params: { with_util: 1 },
    })
      .then(({ data }) => {
        if (!alive) return
        setMwLinks(Array.isArray(data?.links) ? data.links : [])
        setUtilMeta(data?.util_meta ?? null)
      })
      .catch((err: unknown) => {
        if (!alive) return
        setMwError(err instanceof Error ? err.message : String(err))
        setMwLinks([])
      })
      .finally(() => { if (alive) setLoadingMw(false) })
    return () => { alive = false }
  }, [tick])

  useEffect(() => {
    let alive = true
    setLoadingFiber(true)
    setFiberError('')
    Promise.all([fetchJson<FiberFile>(FIBER_URL), fetchJson<SiteGeoFile>(SITE_GEO_URL)])
      .then(([fiber, geo]) => {
        if (!alive) return
        setFiberLinks(joinFiberLinks(fiber, geo))
      })
      .catch((err: unknown) => {
        if (!alive) return
        setFiberError(err instanceof Error ? err.message : String(err))
        setFiberLinks([])
      })
      .finally(() => { if (alive) setLoadingFiber(false) })
    return () => { alive = false }
  }, [tick])

  return {
    sites, mwLinks, fiberLinks, utilMeta,
    loadingSites, loadingMw, loadingFiber,
    sitesError, mwError, fiberError,
    reload,
  }
}
